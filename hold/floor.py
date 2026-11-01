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


def admit_action(profile: Profile, action: str, attrs: dict[str, Any], ctx: ActionContext, *, grant_digest: str | None = None) -> Verdict:
    """Admission of a tool call by the agent. `grant_digest`: one-off approval by the authority for
    exactly these attributes (envelope breach approved through a Decision Request)."""
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
        if v and not (grant_digest and grant_digest == digest({"action": action, "attrs": attrs})):
            return Verdict(False, "I4/ENVELOPE", "Outside the profile's envelope: " + "; ".join(v), v)
    return Verdict(True, "ADMIT", "Power present, budget available, envelope respected.")


# -- admission d'un chemin d'hésitation -------------------------------------------

@dataclass(frozen=True)
class PathVerdict:
    proposed: str | None
    admitted: bool
    applied: str | None  # None : position périmée, relire
    reference: str
    rule: str
    reason: str
    overruled: bool


def admit_path(proposal_path: str | None, position: dict[str, Any], read_digest: str, now_digest: str, *, on_refused: str) -> PathVerdict:
    ref = position["reference"]
    if read_digest != now_digest:
        return PathVerdict(proposal_path, False, None, ref, "I5", "The position changed during deliberation: re-read before acting.", False)
    paths = {p["name"]: p for p in position["paths"]}
    p = paths.get(proposal_path or "")
    if p is None:
        reason = "No usable proposal."
    elif not p["open"]:
        reason = f"Path {proposal_path} is closed: {p['reason']}."
    else:
        return PathVerdict(proposal_path, True, proposal_path, ref, "ADMIT", f"Path {proposal_path} is open: {p['reason']}.", False)
    applied = ref if on_refused == "reference" else "PRESERVE"
    return PathVerdict(proposal_path, False, applied, ref, "I4/PATH", f"{reason} The floor applies {applied}.", True)
