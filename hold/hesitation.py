"""Hesitation: pass / fail / hold, then hold → discernment.

When an effect becomes UNKNOWN, coherence falls below τ*. The boundary position states the facts the
kernel owns: for each path, whether it is open now (and why), the chance σ that it restores
coherence, the authority it mobilizes, and what is left of it. The model reads these facts into a
declared map, chooses a move, and the floor checks three things separately:

1. the map is true (on every field the kernel can verify; a quoted piece of evidence must exist);
2. the move is admissible (the path is open now);
3. the move follows from the declared map (it belongs to the moves that map justifies).

Several moves can be justified by the same facts: discernment is not conformity to one reference.
The reference cascade (first open path reaching τ* in the ladder, least authority first) is the
fallback when the model's reading or move is refused after its retries, and is credited as such.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .profile import Profile

PATH_AUTHORITY = {"VERIFY": "agent", "CONTACT_SUPPORT": "mandate", "ASK_AUTHORITY": "authority", "WAIT": "none", "PRESERVE": "none"}
MOVES = ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "WAIT", "PRESERVE"]
PENDING_CONDITIONS = ["VISIBILITY_DELAY", "SUPPORT_REPLY"]
INSIDE_HABILITATION = ("VERIFY", "CONTACT_SUPPORT")
# An undetermined reading (not contradicted, not established) is enough only for bounded moves that
# gather information or keep the state. Contacting a third party or the authority needs a supported
# reading. Effects never pass on a reading at all: they go through the floor's execution rules.
UNDETERMINED_SCOPE = ("VERIFY", "WAIT", "PRESERVE")
FORBIDDEN_DURING_HOLD = ["RECREATE_EQUIVALENT_EFFECT", "DECLARE_SUCCESS_WITHOUT_EVIDENCE", "RUN_DEPENDENT_ACTIONS", "EXCEED_PROFILE"]
MIN_QUOTE = 8


@dataclass(frozen=True)
class PathState:
    name: str
    open: bool
    reason: str
    sigma: float
    authority: str
    reaches_tau: bool
    directed: bool = False  # explicitly granted by the authority: takes precedence over strategy
    left: int | None = None  # checks, contacts, requests or seconds of patience left (None: unbounded)


@dataclass(frozen=True)
class BoundaryPosition:
    effect_key: str
    action: str
    operation_id: str
    effect_state: str
    coherence: float
    tau_star: float
    threatened: bool
    paths: list[PathState]
    restorable_in_habilitation: bool
    reference: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=lambda: list(FORBIDDEN_DURING_HOLD))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def path(self, name: str) -> PathState | None:
        return next((p for p in self.paths if p.name == name), None)


def coherence_of(effect_state: str) -> float:
    # The truth of the effect is known (receipt or proven refusal): full coherence.
    return 1.0 if effect_state in ("CONFIRMED", "FAILED_PROVEN") else 0.0


def reference_path(paths: list[PathState], ladder: list[str]) -> str:
    directed = next((p for p in paths if p.open and p.directed), None)
    if directed:
        return directed.name
    by = {p.name: p for p in paths}
    for name in ladder:
        p = by.get(name)
        if p and p.open and p.reaches_tau:
            return name
    return "PRESERVE"


def boundary_position(
    profile: Profile,
    *,
    effect_key: str,
    action: str,
    operation_id: str,
    effect_state: str,
    not_found: int,
    usage: dict[str, int],
    authority_available: bool,
    open_request: bool,
    revoked: bool,
    observations: list[dict[str, Any]],
) -> BoundaryPosition:
    h = profile.hesitation
    tau = h.tau_star
    coherence = coherence_of(effect_state)
    power = profile.power(action)
    rec = power.reconciliation if power else None
    paths: list[PathState] = []

    for name in h.ladder:
        if name == "VERIFY":
            if not rec:
                paths.append(PathState(name, False, "no reconciliation declared for this effect", 0.0, "agent", False, left=0))
                continue
            granted = usage.get(f"extra_checks:{effect_key}", 0)
            used = usage.get(f"checks:{effect_key}", 0)
            left = rec.max_checks + granted - used
            sigma = round(rec.sigma * (rec.decay ** not_found), 4)
            ok = left > 0 and not revoked
            granted_now = usage.get(f"directed_checks:{effect_key}", 0) > 0
            scheduled = usage.get(f"scheduled_checks:{effect_key}", 0) > 0
            directed = ok and (granted_now or scheduled)
            if directed:
                reason = "check granted by the authority" if granted_now else "check scheduled at the end of the wait"
            else:
                reason = f"{left} check(s) left" if ok else ("mandate revoked" if revoked else "checks exhausted")
            paths.append(PathState(name, ok, reason, sigma, "agent", sigma >= tau, directed, max(left, 0)))
        elif name == "CONTACT_SUPPORT":
            sup = profile.support
            if not sup:
                paths.append(PathState(name, False, "no support contact power in the profile", 0.0, "mandate", False, left=0))
                continue
            used = usage.get(f"power:{sup.action}", 0)
            left = None if sup.budget is None else sup.budget - used
            sigma = sup.sigma if sup.sigma is not None else 0.5
            ok = (left is None or left > 0) and not revoked
            reason = ("support reachable" if left is None else f"{left} contact(s) left") if ok else ("mandate revoked" if revoked else "contact budget exhausted")
            paths.append(PathState(name, ok, reason, sigma, "mandate", sigma >= tau, left=None if left is None else max(left, 0)))
        elif name == "ASK_AUTHORITY":
            a = profile.authority
            used = usage.get("authority_requests", 0)
            left = a.max_requests - used
            ok = authority_available and not open_request and left > 0 and not revoked
            if revoked:
                reason = "mandate revoked"
            elif not authority_available:
                reason = "authority unreachable (no channel configured)"
            elif open_request:
                reason = "a request is already open"
            elif left <= 0:
                reason = "requests to the authority exhausted"
            else:
                reason = f"{a.name} reachable on {a.channel}"
            paths.append(PathState(name, ok, reason, a.sigma, "authority", a.sigma >= tau, left=max(left, 0)))
        elif name == "PRESERVE":
            if h.max_wait_seconds > 0:
                waited = usage.get(f"waited_s:{effect_key}", 0)
                waits = usage.get(f"waits:{effect_key}", 0)
                left_s = max(0, h.max_wait_seconds - waited)
                ok = left_s > 0 and waits < h.max_waits and not revoked
                if revoked:
                    reason = "mandate revoked"
                elif not ok:
                    reason = "patience exhausted"
                else:
                    reason = f"{left_s} s of patience left; only on a pending condition stated in the evidence"
                paths.append(PathState("WAIT", ok, reason, 0.0, "none", False, left=left_s))
            else:
                paths.append(PathState("WAIT", False, "waiting is not allowed by the profile", 0.0, "none", False, left=0))
            paths.append(PathState(name, True, "always possible: keep the state without acting", 0.0, "none", False))

    restorable = any(p.open and p.reaches_tau and p.authority in ("agent", "mandate") for p in paths)
    return BoundaryPosition(
        effect_key=effect_key,
        action=action,
        operation_id=operation_id,
        effect_state=effect_state,
        coherence=coherence,
        tau_star=tau,
        threatened=coherence < tau,
        paths=paths,
        restorable_in_habilitation=restorable,
        reference=reference_path(paths, h.ladder),
        observations=observations,
    )


# -- the map: what the model claims about the facts ---------------------------------------------

def true_map(position: dict[str, Any]) -> dict[str, Any]:
    """The kernel's reading of every fact it can verify (a pending condition stated in free text is
    not one of them: the kernel can only check that a quoted passage exists)."""
    paths = position["paths"]
    return {
        "effect_state": position["effect_state"],
        "open_paths": sorted(p["name"] for p in paths if p["open"] and p["name"] != "PRESERVE"),
        "reaching_tau": sorted(p["name"] for p in paths if p["open"] and p["reaches_tau"]),
        "pending": {"condition": "NONE"},
    }


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"\s+", " ", s).strip(" .\"'")


def _names(v: Any) -> list[str]:
    """A declared list of path names, whatever the model sent (untrusted input)."""
    return sorted({x for x in v if isinstance(x, str)}) if isinstance(v, list) else []


def pending_of(m: Any) -> dict[str, Any] | None:
    p = m.get("pending") if isinstance(m, dict) else None
    return p if isinstance(p, dict) and p.get("condition") not in (None, "", "NONE") else None


SUPPORTED, CONTRADICTED, UNDETERMINED = "SUPPORTED", "CONTRADICTED", "UNDETERMINED"


def check_map(declared: dict[str, Any] | None, position: dict[str, Any]) -> dict[str, Any]:
    """Qualifies each claim of the declared reading against the evidence available at decision time:
    SUPPORTED, CONTRADICTED, or UNDETERMINED (neither established nor refuted: a pending condition
    quoted verbatim from the evidence, which the kernel cannot interpret). The reading as a whole is
    CONTRADICTED if any claim is, UNDETERMINED if any claim is, SUPPORTED otherwise. An uncertain
    reading is not a false one: only a contradicted reading is refused."""
    if not isinstance(declared, dict):
        return {"reading": None, "claims": {}, "discrepancies": ["no reading declared"]}
    truth = true_map(position)
    out: list[str] = []
    claims: dict[str, str] = {}
    state = declared.get("effect_state")
    claims["effect_state"] = SUPPORTED if state == truth["effect_state"] else CONTRADICTED
    if state != truth["effect_state"]:
        out.append(f"effect_state is {truth['effect_state']}, not {state if isinstance(state, str) else 'nothing'}")
    for k, label in (("open_paths", "open paths"), ("reaching_tau", "paths reaching tau*")):
        got = _names(declared.get(k))
        claims[k] = SUPPORTED if got == truth[k] else CONTRADICTED
        if got != truth[k]:
            out.append(f"{label} are {', '.join(truth[k]) or 'none'}, not {', '.join(got) or 'none'}")
    grounded = None
    p = pending_of(declared)
    if p:
        obs = {o.get("id"): o for o in position.get("observations", [])}
        o = obs.get(p.get("evidence")) if isinstance(p.get("evidence"), str) else None
        quote = _norm(p.get("quote") if isinstance(p.get("quote"), str) else "")
        cited = p.get("evidence") if isinstance(p.get("evidence"), str) else "nothing"
        if p["condition"] not in PENDING_CONDITIONS:
            out.append(f"pending condition {p['condition'] if isinstance(p['condition'], str) else 'nothing'} is not a known condition")
            grounded = False
        elif not o:
            out.append(f"the cited evidence {cited} does not exist")
            grounded = False
        elif len(quote) < MIN_QUOTE or quote not in _norm(o.get("text") or ""):
            out.append(f"the cited evidence {cited} does not contain the quoted passage")
            grounded = False
        else:
            grounded = True
        claims["pending"] = UNDETERMINED if grounded else CONTRADICTED
    values = set(claims.values())
    reading = CONTRADICTED if CONTRADICTED in values else UNDETERMINED if UNDETERMINED in values else SUPPORTED
    return {"reading": reading, "claims": claims, "discrepancies": out}


def justified_moves(declared: dict[str, Any] | None, position: dict[str, Any]) -> list[str]:
    """The moves a map justifies. A directive of the authority comes first; then any path inside the
    habilitation that reaches τ*, and waiting if a grounded pending condition is declared; the
    authority only when nothing inside the habilitation can restore coherence; otherwise preserve."""
    directed = [p["name"] for p in position["paths"] if p["open"] and p.get("directed")]
    if directed:
        return directed[:1]
    m = declared if isinstance(declared, dict) else {}
    reach = set(_names(m.get("reaching_tau")))
    moves = [n for n in INSIDE_HABILITATION if n in reach]
    if pending_of(m) and "WAIT" in _names(m.get("open_paths")):
        moves.append("WAIT")
    if not moves and "ASK_AUTHORITY" in reach:
        moves.append("ASK_AUTHORITY")
    return moves or ["PRESERVE"]


def admissible_moves(position: dict[str, Any]) -> list[str]:
    return [p["name"] for p in position["paths"] if p["open"]]
