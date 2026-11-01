"""Le plancher : ce qui ne peut pas arriver, quel que soit l'agent.

Invariants (non configurables) :
  I1  Aucun effet équivalent n'est créé tant qu'un effet est en cours, inconnu ou confirmé.
  I2  Aucun succès ni aucune reprise sans preuve corrélée à l'opération.
  I3  Aucune action dépendante d'un effet inconnu n'est exécutée.
  I4  Aucun pouvoir au-delà du profil : action, enveloppe, budget, expiration, révocation.
  I5  Aucune décision prise sur une position périmée.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .canonical import digest
from .profile import Power, Profile

INVARIANTS = {
    "I1": "Aucun effet équivalent n'est créé tant qu'un effet est en cours, inconnu ou confirmé.",
    "I2": "Aucun succès ni aucune reprise sans preuve corrélée à l'opération.",
    "I3": "Aucune action dépendante d'un effet inconnu n'est exécutée.",
    "I4": "Aucun pouvoir au-delà du profil : action, enveloppe, budget, expiration, révocation.",
    "I5": "Aucune décision prise sur une position périmée.",
}


def norm(v: Any) -> str:
    s = unicodedata.normalize("NFKC", str(v)).strip().casefold()
    return " ".join(s.split())


def effect_key(mission_id: str, action: str, identity: list[str], attrs: dict[str, Any]) -> str:
    """Identité canonique d'un effet : indépendante du nom donné par l'agent, de la casse et des espaces."""
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


def envelope_violations(power: Power, attrs: dict[str, Any]) -> list[str]:
    env = power.envelope
    if not env:
        return []
    out: list[str] = []
    for k in env.required:
        if attrs.get(k) in (None, ""):
            out.append(f"attribut requis absent : {k}")
    if env.max_amount:
        raw = attrs.get(env.amount_field)
        cur = attrs.get(env.currency_field)
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            out.append(f"montant illisible dans {env.amount_field}")
        else:
            if cur is not None and norm(cur) != norm(env.max_amount.currency):
                out.append(f"devise {cur} hors enveloppe ({env.max_amount.currency})")
            if amount > env.max_amount.amount:
                out.append(f"montant {amount:g} {cur or ''} au-delà du plafond {env.max_amount.amount:g} {env.max_amount.currency}".replace("  ", " "))
    for k, allowed in env.allowed.items():
        if k in attrs and norm(attrs[k]) not in {norm(a) for a in allowed}:
            out.append(f"{k} « {attrs[k]} » hors des valeurs permises")
    for k, w in env.windows.items():
        if k not in attrs:
            continue
        try:
            d = date.fromisoformat(str(attrs[k])[:10])
        except ValueError:
            out.append(f"date illisible dans {k}")
            continue
        if not (w.start <= d <= w.end):
            out.append(f"{k} {d.isoformat()} hors de la fenêtre {w.start.isoformat()} → {w.end.isoformat()}")
    return out


def admit_action(profile: Profile, action: str, attrs: dict[str, Any], ctx: ActionContext, *, grant_digest: str | None = None) -> Verdict:
    """Admission d'un appel d'outil par l'agent. `grant_digest` : autorisation ponctuelle de l'autorité
    portant exactement sur ces attributs (dépassement d'enveloppe approuvé par Decision Request)."""
    if ctx.revoked:
        return Verdict(False, "I4/REVOKED", "Le mandat a été révoqué par l'autorité : plus aucune action.")
    if ctx.expired:
        return Verdict(False, "I4/EXPIRED", "Le profil d'habilitation a expiré.")
    power = profile.power(action)
    if not power:
        return Verdict(False, "I4/NO_POWER", f"Aucun pouvoir « {action} » dans le profil.")
    if power.kind == "status":
        return Verdict(False, "I4/STATUS_RESERVED", "Les contrôles de statut sont conduits par HOLD pendant une hésitation.")
    for dep in power.depends_on:
        if dep in ctx.uncertain_actions:
            return Verdict(False, "I3", f"Dépend de « {dep} », dont le résultat est inconnu : action suspendue.")
        if dep not in ctx.confirmed_actions:
            return Verdict(False, "I2", f"Dépend de « {dep} », qui n'est pas confirmé par une preuve.")
    if power.budget is not None and ctx.usage.get(f"power:{action}", 0) >= power.budget:
        return Verdict(False, "I4/BUDGET", f"Budget épuisé pour « {action} » ({power.budget}).")
    if power.kind == "effect":
        v = envelope_violations(power, attrs)
        if v and not (grant_digest and grant_digest == digest({"action": action, "attrs": attrs})):
            return Verdict(False, "I4/ENVELOPE", "Hors de l'enveloppe du profil : " + " ; ".join(v), v)
    return Verdict(True, "ADMIT", "Pouvoir présent, budget disponible, enveloppe respectée.")


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
        return PathVerdict(proposal_path, False, None, ref, "I5", "La position a changé pendant la délibération : relire avant d'agir.", False)
    paths = {p["name"]: p for p in position["paths"]}
    p = paths.get(proposal_path or "")
    if p is None:
        reason = "Aucune proposition exploitable."
    elif not p["open"]:
        reason = f"Chemin {proposal_path} fermé : {p['reason']}."
    else:
        return PathVerdict(proposal_path, True, proposal_path, ref, "ADMIT", f"Chemin {proposal_path} ouvert : {p['reason']}.", False)
    applied = ref if on_refused == "reference" else "PRESERVE"
    return PathVerdict(proposal_path, False, applied, ref, "I4/PATH", f"{reason} Le plancher applique {applied}.", True)
