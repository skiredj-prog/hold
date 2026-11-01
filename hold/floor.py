"""The floor: what cannot happen, whatever the agent proposes.

Invariants (not configurable):
  I1  No equivalent effect is created while one is in flight, unknown or confirmed.
  I2  No success and no resumption without evidence correlated with the operation.
  I3  No action depending on an unknown effect is executed.
  I4  No power beyond the profile: action, envelope, budget, expiry, revocation.
  I5  No decision taken on a stale position.

Reasons are recorded in English (stable journal language, replayed identically by HOLD Studio).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .canonical import digest
from .profile import Power, Profile

INVARIANTS = {
    "I1": "No equivalent effect is created while one is in flight, unknown or confirmed.",
    "I2": "No success and no resumption without evidence correlated with the operation.",
    "I3": "No action depending on an unknown effect is executed.",
    "I4": "No power beyond the profile: action, envelope, budget, expiry, revocation.",
    "I5": "No decision taken on a stale position.",
}


def norm(v: Any) -> str:
    s = unicodedata.normalize("NFKC", str(v)).strip().casefold()
    return " ".join(s.split())


def effect_key(mission_id: str, action: str, identity: list[str], attrs: dict[str, Any]) -> str:
    """Canonical identity of an effect: independent of the name the agent uses, of case and of spacing."""
    return digest({"mission": mission_id, "action": action, "identity": {k: norm(attrs.get(k, "")) for k in identity}})


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    rule: str
    reason: str
    violations: list[str] = field(default_factory=list)


@dataclass
class ActionContext:
    revoked: bool
    expired: bool
    usage: dict[str, int]
    confirmed_actions: set[str]  # actions dont au moins un effet est confirmé
    uncertain_actions: set[str]  # actions dont un effet est UNKNOWN ou DISPATCHING
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_V = {
    "en": {"required": "required attribute missing: {k}", "amount_unreadable": "unreadable amount in {k}",
           "currency": "currency {cur} outside the envelope ({exp})", "ceiling": "amount {amount} {cur} above the ceiling of {max} {exp}",
           "allowed": "{k} \"{v}\" is not an allowed value", "date_unreadable": "unreadable date in {k}",
           "window": "{k} {d} outside the window {start} → {end}"},
    "fr": {"required": "attribut requis absent : {k}", "amount_unreadable": "montant illisible dans {k}",
           "currency": "devise {cur} hors enveloppe ({exp})", "ceiling": "montant de {amount} {cur} au-delà du plafond de {max} {exp}",
           "allowed": "{k} « {v} » hors des valeurs permises", "date_unreadable": "date illisible dans {k}",
           "window": "{k} {d} hors de la fenêtre {start} → {end}"},
}


def envelope_violations(power: Power, attrs: dict[str, Any], locale: str = "en") -> list[str]:
    m = _V.get(locale, _V["en"])
    env = power.envelope
    if not env:
        return []
    out: list[str] = []
    for k in env.required:
        if attrs.get(k) in (None, ""):
            out.append(m["required"].format(k=k))
    if env.max_amount:
        raw = attrs.get(env.amount_field)
        cur = attrs.get(env.currency_field)
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            out.append(m["amount_unreadable"].format(k=env.amount_field))
        else:
            if cur is not None and norm(cur) != norm(env.max_amount.currency):
                out.append(m["currency"].format(cur=cur, exp=env.max_amount.currency))
            if amount > env.max_amount.amount:
                out.append(m["ceiling"].format(amount=f"{amount:g}", cur=cur or env.max_amount.currency, max=f"{env.max_amount.amount:g}", exp=env.max_amount.currency))
    for k, allowed in env.allowed.items():
        if k in attrs and norm(attrs[k]) not in {norm(a) for a in allowed}:
            out.append(m["allowed"].format(k=k, v=attrs[k]))
    for k, w in env.windows.items():
        if k not in attrs:
            continue
        try:
            d = date.fromisoformat(str(attrs[k])[:10])
        except ValueError:
            out.append(m["date_unreadable"].format(k=k))
            continue
        if not (w.start <= d <= w.end):
            out.append(m["window"].format(k=k, d=d.isoformat(), start=w.start.isoformat(), end=w.end.isoformat()))
    return out


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def grant_problem(grant: dict[str, Any] | None, *, action: str, attrs: dict[str, Any], profile: Profile, now: datetime) -> str | None:
    """Why a one-time approval does not apply here, or None if it does.

    Verified authority may override discretionary strategy (here: the envelope), never a hard
    invariant. The approval is bound to the exact action and attributes, to the profile revision it
    was given under, to an expiry and to a single use."""
    if not grant:
        return "no approval"
    if grant.get("consumed"):
        return "approval already used"
    if grant.get("action") != action or grant.get("attrs_digest") != digest({"action": action, "attrs": attrs}):
        return "approval given for different attributes"
    if grant.get("profile") != profile.fingerprint:
        return "approval given under another profile revision"
    if not grant.get("expires_at") or now >= parse_time(grant["expires_at"]):
        return "approval expired"
    return None


def admit_action(profile: Profile, action: str, attrs: dict[str, Any], ctx: ActionContext, *, grant: dict[str, Any] | None = None) -> Verdict:
    """Admission of a tool call by the agent. `grant`: one-time approval by the authority (envelope
    breach approved through a Decision Request). It lifts the envelope rule only: revocation, expiry,
    dependencies (I2, I3), budget and equivalence (I1, checked before admission) still apply."""
    if ctx.revoked:
        return Verdict(False, "I4/REVOKED", "The mandate was revoked by the authority: no further action.")
    if ctx.expired:
        return Verdict(False, "I4/EXPIRED", "The habilitation profile has expired.")
    power = profile.power(action)
    if not power:
        return Verdict(False, "I4/NO_POWER", f"No power \"{action}\" in the profile.")
    if power.kind == "status":
        return Verdict(False, "I4/STATUS_RESERVED", "Status checks are conducted by HOLD during a hesitation.")
    for dep in power.depends_on:
        if dep in ctx.uncertain_actions:
            return Verdict(False, "I3", f"Depends on \"{dep}\", whose outcome is unknown: action suspended.")
        if dep not in ctx.confirmed_actions:
            return Verdict(False, "I2", f"Depends on \"{dep}\", which is not confirmed by evidence.")
    if power.budget is not None and ctx.usage.get(f"power:{action}", 0) >= power.budget:
        return Verdict(False, "I4/BUDGET", f"Budget exhausted for \"{action}\" ({power.budget}).")
    if power.kind == "effect":
        v = envelope_violations(power, attrs)
        if v:
            problem = grant_problem(grant, action=action, attrs=attrs, profile=profile, now=ctx.now) if grant else "no approval"
            if problem:
                tail = f" (the authority's approval does not apply: {problem})" if grant else ""
                return Verdict(False, "I4/ENVELOPE", "Outside the profile's envelope: " + "; ".join(v) + tail, v)
            return Verdict(True, "ADMIT/APPROVED", "Outside the envelope, approved once by the authority for exactly these attributes.")
    return Verdict(True, "ADMIT", "Power present, budget available, envelope respected.")


# -- admission of a hesitation move ---------------------------------------------------------

def _seconds(v: Any) -> int | None:
    """A number of seconds from untrusted input: an actual number, truncated; anything else is None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e9:
        return None
    return int(v)


@dataclass(frozen=True)
class PathVerdict:
    outcome: str  # ADMITTED | MAP_REFUSED | MOVE_REFUSED | INCOHERENT | NO_PROPOSAL | STALE | REFERENCE_APPLIED
    proposed: str | None
    applied: str | None  # None: the model reads again (or the position is re-read)
    reference: str
    credit: str | None  # "discerner": its own move was admitted; "floor": the reference was applied
    rule: str
    reason: str
    map_true: bool | None
    coherent: bool | None
    justified: list[str]
    discrepancies: list[str]
    wait_seconds: int | None = None


def admit_proposal(proposal: dict[str, Any] | None, position: dict[str, Any], read_digest: str, now_digest: str, *, final: bool, on_refused: str) -> PathVerdict:
    """The gate, in order: position current (I5), a usable proposal, a true map (D1), an admissible
    move (I4), a move that follows from the declared map. If the attempt is the last one, a refusal
    applies the reference (or PRESERVE), credited to the floor, never to the model."""
    from .hesitation import admissible_moves, check_map, justified_moves

    ref = position["reference"]
    p = proposal or {}
    move = p.get("move")
    if read_digest != now_digest:
        return PathVerdict("STALE", move, None, ref, None, "I5", "The position changed during deliberation: re-read before acting.", None, None, [], [])
    mc = check_map(p.get("map"), position)
    justified = justified_moves(p.get("map"), position)
    coherent = move in justified if move else None
    if not move:
        outcome, rule, reason = "NO_PROPOSAL", "D1/NONE", "No usable proposal" + (f" ({p['error']})" if p.get("error") else "") + "."
    elif not mc["true"]:
        outcome, rule, reason = "MAP_REFUSED", "D1/MAP", "The declared reading is false: " + "; ".join(mc["discrepancies"]) + "."
    elif move not in admissible_moves(position):
        path = next((x for x in position["paths"] if x["name"] == move), None)
        outcome, rule, reason = "MOVE_REFUSED", "I4/PATH", f"Path {move} is closed: {path['reason'] if path else 'unknown path'}."
    elif not coherent:
        outcome, rule, reason = "INCOHERENT", "D1/COHERENCE", f"{move} does not follow from the declared reading, which justifies {', '.join(justified)}."
    else:
        wait = None
        if move == "WAIT":
            from .hesitation import pending_of

            left = next(x for x in position["paths"] if x["name"] == "WAIT").get("left") or 0
            asked = _seconds(p.get("wait_seconds")) or _seconds((pending_of(p.get("map")) or {}).get("wait_seconds")) or left
            wait = max(1, min(asked, left))
        return PathVerdict("ADMITTED", move, move, ref, "discerner", "ADMIT", f"{move} is open and follows from a true reading.", True, True, justified, [], wait)
    if not final:
        return PathVerdict(outcome, move, None, ref, None, rule, reason, mc["true"], coherent, justified, mc["discrepancies"])
    applied = ref if on_refused == "reference" else "PRESERVE"
    return PathVerdict("REFERENCE_APPLIED", move, applied, ref, "floor", rule, f"{reason} Retries exhausted: the floor applies {applied} (credited to the floor, not to the model).", mc["true"], coherent, justified, mc["discrepancies"])
