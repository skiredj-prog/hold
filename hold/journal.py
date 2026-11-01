"""Journal : enveloppe d'événement v1 et chaîne d'empreintes.

L'enveloppe est le second contrat portable (le profil est le premier) : tout outil qui sait
lire ce format peut vérifier et rejouer une mission, sans la bibliothèque.

    {v, mission, seq, at, type, actor, cause, profile, revision, core, data, prev, hash}

hash = sha256(JSON canonique de l'enveloppe sans le champ hash).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .canonical import canonical_json, sha256_hex

ENVELOPE_VERSION = "hold.event/v1"
CORE_VERSION = "hold-core/0.1.0"
GENESIS = "0" * 64

ACTORS = {"CORE", "AGENT", "DISCERNER", "AUTHORITY", "PROVIDER", "RELAY"}

# Champs obligatoires de `data` par type d'événement (validation à l'écriture et à la lecture).
# The HOLD cycle of the habilitated agent. Functions of the cycle, not states: they repeat, revisit
# and interleave, and an agent on hold keeps gathering, discerning and relating without acting.
PHASES = ["RESSENTIR", "CAPTER", "PERCEVOIR", "DISCERNER", "RELIER", "CHOISIR", "ENGAGER", "REEVALUER"]


def default_phase(type_: str, data: dict[str, Any]) -> str | None:
    """The phase an event belongs to, from what it records. Administrative events have none."""
    if type_ == "ACTION_ADMITTED" or type_ == "ACTION_RESULT":
        return {"read": "CAPTER", "contact": "RELIER", "effect": "ENGAGER"}.get(data.get("kind"), "CAPTER" if type_ == "ACTION_RESULT" else "ENGAGER")
    if type_ == "EFFECT_OUTCOME":
        return "RESSENTIR" if data.get("status") == "UNKNOWN" else "REEVALUER"
    if type_ == "PATH_EXECUTED":
        return "CAPTER" if data.get("path") == "VERIFY" else "RELIER"
    if type_ == "REQUEST_CLOSED":
        return "REEVALUER" if data.get("reason") == "CONDITIONS_CHANGED" else "RESSENTIR"
    if type_ == "PROFILE_ATTACHED":
        return "RESSENTIR" if data.get("supersedes") else None
    return {
        "ACTION_REFUSED": "ENGAGER", "EFFECT_RESERVED": "ENGAGER", "EVIDENCE": "CAPTER", "HOLD_ENTERED": "RELIER",
        "POSITION": "DISCERNER", "PROPOSAL": "PERCEVOIR", "DECISION": "CHOISIR", "WAIT_STARTED": "ENGAGER", "WAIT_ENDED": "RESSENTIR",
        "DECISION_REQUEST": "RELIER", "AUTHORITY_ANSWER": "RELIER", "AUTHORITY_GRANT": "RELIER", "RELAY_SENT": "RELIER",
        "RELAY_FAILED": "RELIER", "RELAY_REJECTED": "RELIER", "REVOKED": "RELIER",
        "RECONCILED": "REEVALUER", "RESUMED": "REEVALUER", "PRESERVED": "REEVALUER", "MISSION_CLOSED": "REEVALUER",
    }.get(type_)


EVENT_TYPES: dict[str, set[str]] = {
    "MISSION_OPENED": {"title"},
    "PROFILE_ATTACHED": {"fingerprint", "revision", "profile"},
    "RUNTIME_BOUND": {"core", "discerner", "tools"},
    "ACTION_ADMITTED": {"action", "kind"},
    "ACTION_REFUSED": {"action", "rule", "reason"},
    "ACTION_RESULT": {"action", "status"},
    "EFFECT_RESERVED": {"action", "effect_key", "operation_id", "attributes"},
    "EFFECT_OUTCOME": {"effect_key", "operation_id", "status"},
    "HOLD_ENTERED": {"effect_key", "operation_id", "scope"},
    "POSITION": {"round", "effect_key", "position"},
    "PROPOSAL": {"round", "attempt", "discerner", "position_seq", "proposal"},
    "DECISION": {"round", "attempt", "position_seq", "proposal", "read_digest", "now_digest", "final", "outcome", "verdict"},
    "WAIT_STARTED": {"effect_key", "seconds", "until", "condition"},
    "WAIT_ENDED": {"effect_key"},
    "PATH_EXECUTED": {"round", "path", "result"},
    "EVIDENCE": {"effect_key", "source", "kind"},
    "DECISION_REQUEST": {"question_id", "kind", "options"},
    "AUTHORITY_ANSWER": {"question_id", "answer", "channel"},
    "AUTHORITY_GRANT": {"grant"},
    "REQUEST_CLOSED": {"question_id", "reason", "status", "mission_status"},
    "RECONCILED": {"effect_key", "receipt"},
    "RESUMED": {"effect_key"},
    "PRESERVED": {"effect_key", "reason"},
    "REVOKED": {"by", "channel"},
    "RELAY_SENT": {"question_id", "channel", "mode"},
    "RELAY_FAILED": {"channel", "error"},
    "RELAY_REJECTED": {"reason"},
    "MISSION_CLOSED": {"outcome"},
}


@dataclass(frozen=True)
class Event:
    v: str
    mission: str
    seq: int
    at: str
    type: str
    actor: str
    cause: int | None
    profile: str
    revision: int
    core: str
    data: dict[str, Any] = field(default_factory=dict)
    phase: str | None = None  # function of the agent's cycle this event belongs to (not a state)
    status: str | None = None  # mission status after this event
    prev: str = GENESIS
    hash: str = ""

    def unsigned(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("hash")
        return d

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_hash(e: Event) -> str:
    return sha256_hex(canonical_json(e.unsigned()))


def validate_data(type_: str, data: dict[str, Any]) -> None:
    required = EVENT_TYPES.get(type_)
    if required is None:
        raise ValueError(f"Unknown event type: {type_}")
    missing = required - set(data)
    if missing:
        raise ValueError(f"{type_}: missing fields {sorted(missing)}")


def make_event(*, mission: str, seq: int, at: str, type_: str, actor: str, cause: int | None, profile: str, revision: int, data: dict, prev: str, phase: str | None = None, status: str | None = None) -> Event:
    if actor not in ACTORS:
        raise ValueError(f"Unknown actor: {actor}")
    if phase is not None and phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")
    validate_data(type_, data)
    e = Event(v=ENVELOPE_VERSION, mission=mission, seq=seq, at=at, type=type_, actor=actor, cause=cause, profile=profile, revision=revision, core=CORE_VERSION, data=data, phase=phase, status=status, prev=prev)
    return Event(**{**e.to_dict(), "hash": compute_hash(e)})


@dataclass(frozen=True)
class ChainReport:
    valid: bool
    count: int
    head: str
    broken_at: int | None = None
    reason: str | None = None


def verify_chain(events: list[Event] | list[dict]) -> ChainReport:
    prev = GENESIS
    for i, raw in enumerate(events):
        e = raw if isinstance(raw, Event) else Event(**raw)
        if e.v != ENVELOPE_VERSION:
            return ChainReport(False, len(events), prev, e.seq, f"unknown envelope version {e.v}")
        if e.seq != i + 1:
            return ChainReport(False, len(events), prev, e.seq, "non-contiguous sequence")
        if e.prev != prev:
            return ChainReport(False, len(events), prev, e.seq, "broken chain")
        if compute_hash(e) != e.hash:
            return ChainReport(False, len(events), prev, e.seq, "invalid hash")
        try:
            validate_data(e.type, e.data)
        except ValueError as err:
            return ChainReport(False, len(events), prev, e.seq, str(err))
        if e.phase is not None and e.phase not in PHASES:
            return ChainReport(False, len(events), prev, e.seq, f"unknown phase {e.phase}")
        prev = e.hash
    return ChainReport(True, len(events), prev)
