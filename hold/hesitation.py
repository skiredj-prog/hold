"""Hésitation : pass / fail / hold, puis hold → discernement.

Quand un effet devient UNKNOWN, la cohérence tombe sous τ*. La position de frontière dit,
pour chaque chemin de l'échelle, s'il est ouvert, quelle chance σ il a de rétablir la
cohérence, et quelle autorité il mobilise. La référence choisit le premier chemin ouvert
qui atteint τ*, dans l'ordre de l'échelle (le moins d'autorité d'abord) ; sinon PRESERVE.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .profile import Profile

PATH_AUTHORITY = {"VERIFY": "agent", "CONTACT_SUPPORT": "mandate", "ASK_AUTHORITY": "authority", "PRESERVE": "none"}
FORBIDDEN_DURING_HOLD = ["RECREATE_EQUIVALENT_EFFECT", "DECLARE_SUCCESS_WITHOUT_EVIDENCE", "RUN_DEPENDENT_ACTIONS", "EXCEED_PROFILE"]


@dataclass(frozen=True)
class PathState:
    name: str
    open: bool
    reason: str
    sigma: float
    authority: str
    reaches_tau: bool
    directed: bool = False  # accordé explicitement par l'autorité : passe avant l'échelle


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
    # La vérité de l'effet est connue (reçu ou refus prouvé) : cohérence pleine.
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
                paths.append(PathState(name, False, "aucune réconciliation déclarée pour cet effet", 0.0, "agent", False))
                continue
            granted = usage.get(f"extra_checks:{effect_key}", 0)
            used = usage.get(f"checks:{effect_key}", 0)
            left = rec.max_checks + granted - used
            sigma = round(rec.sigma * (rec.decay ** not_found), 4)
            ok = left > 0 and not revoked
            directed = ok and usage.get(f"directed_checks:{effect_key}", 0) > 0
            if directed:
                reason = "contrôle accordé par l'autorité"
            else:
                reason = f"{left} contrôle(s) restant(s)" if ok else ("mandat révoqué" if revoked else "contrôles épuisés")
            paths.append(PathState(name, ok, reason, sigma, "agent", sigma >= tau, directed))
        elif name == "CONTACT_SUPPORT":
            sup = profile.support
            if not sup:
                paths.append(PathState(name, False, "aucun pouvoir de contact « support » dans le profil", 0.0, "mandate", False))
                continue
            used = usage.get(f"power:{sup.action}", 0)
            left = None if sup.budget is None else sup.budget - used
            sigma = sup.sigma if sup.sigma is not None else 0.5
            ok = (left is None or left > 0) and not revoked
            reason = ("support joignable" if left is None else f"{left} contact(s) restant(s)") if ok else ("mandat révoqué" if revoked else "budget de contact épuisé")
            paths.append(PathState(name, ok, reason, sigma, "mandate", sigma >= tau))
        elif name == "ASK_AUTHORITY":
            a = profile.authority
            used = usage.get("authority_requests", 0)
            left = a.max_requests - used
            ok = authority_available and not open_request and left > 0 and not revoked
            if revoked:
                reason = "mandat révoqué"
            elif not authority_available:
                reason = "autorité injoignable (canal non configuré)"
            elif open_request:
                reason = "une demande est déjà ouverte"
            elif left <= 0:
                reason = "demandes à l'autorité épuisées"
            else:
                reason = f"{a.name} joignable par {a.channel}"
            paths.append(PathState(name, ok, reason, a.sigma, "authority", a.sigma >= tau))
        elif name == "PRESERVE":
            paths.append(PathState(name, True, "toujours possible : conserver l'état sans agir", 0.0, "none", False))

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


def judge(proposal: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    """Concordance de la lecture du discerneur avec la position calculée hors du modèle."""
    reaching = sorted(p["name"] for p in position["paths"] if p["open"] and p["reaches_tau"])
    proposed_reaching = sorted(set(proposal.get("paths_reaching_tau") or []))
    choice = proposal.get("path")
    closed = {p["name"] for p in position["paths"] if not p["open"]}
    return {
        "perception": proposal.get("perceived_state") == position["effect_state"],
        "reaching": proposed_reaching == reaching,
        "choice": choice == position["reference"],
        "chose_closed_path": choice in closed,
        "over_escalation": choice == "ASK_AUTHORITY" and position["restorable_in_habilitation"],
    }
