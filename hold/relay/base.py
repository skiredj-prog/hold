"""Relais vers l'autorité responsable. Le relais transporte la Decision Request et rapporte
le choix ; il ne décide rien. La réponse repasse par Habilitation.answer(), la même porte
que la console."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..profile import Authority

TITLES = {
    "fr": {
        "EXTEND_CHECKS": "Autoriser 1 contrôle",
        "CLOSE_UNCONFIRMED": "Clore sans confirmer",
        "REVOKE": "Révoquer la mission",
        "APPROVE_ONCE": "Autoriser une fois",
        "REFUSE": "Refuser",
    },
    "en": {
        "EXTEND_CHECKS": "Allow 1 more check",
        "CLOSE_UNCONFIRMED": "Close unconfirmed",
        "REVOKE": "Revoke mission",
        "APPROVE_ONCE": "Approve once",
        "REFUSE": "Refuse",
    },
}


def new_question_id() -> str:
    return f"q_{secrets.token_hex(6)}"


@dataclass
class SendResult:
    ok: bool
    mode: str
    message_id: str | None = None
    error: str | None = None
    via: str = "interactive"


class Relay(Protocol):
    channel: str
    mode: str

    def available(self, authority: Authority) -> bool: ...

    def send_request(self, *, mission_id: str, mission_title: str, request: dict[str, Any], authority: Authority) -> SendResult: ...

    def send_text(self, *, mission_id: str, authority: Authority, text: str) -> SendResult: ...


@dataclass
class SimulatedRelay:
    """Relais en mémoire : rien ne part. Sert aux tests et aux démonstrations sans clés Meta."""

    channel: str = "whatsapp"
    mode: str = "simulated"
    outbox: list[dict[str, Any]] = field(default_factory=list)

    def available(self, authority: Authority) -> bool:
        return bool(authority.contact) or authority.channel == "console"

    def send_request(self, *, mission_id: str, mission_title: str, request: dict[str, Any], authority: Authority) -> SendResult:
        mid = f"sim.{secrets.token_hex(6)}"
        self.outbox.append({"type": "request", "mission": mission_id, "to": authority.contact, "request": request, "id": mid})
        return SendResult(True, self.mode, mid)

    def send_text(self, *, mission_id: str, authority: Authority, text: str) -> SendResult:
        mid = f"sim.{secrets.token_hex(6)}"
        self.outbox.append({"type": "text", "mission": mission_id, "to": authority.contact, "text": text, "id": mid})
        return SendResult(True, self.mode, mid)


@dataclass
class ConsoleRelay:
    """L'autorité répond depuis le terminal (`hold answer …`) ou depuis HOLD Studio."""

    channel: str = "console"
    mode: str = "console"

    def available(self, authority: Authority) -> bool:
        return True

    def send_request(self, *, mission_id: str, mission_title: str, request: dict[str, Any], authority: Authority) -> SendResult:
        opts = ", ".join(o["code"] for o in request["options"])
        print(f"\n[HOLD] Decision Request {request['question_id']} · {mission_title}\n       {request['question']}\n       Options : {opts}")
        return SendResult(True, self.mode, request["question_id"])

    def send_text(self, *, mission_id: str, authority: Authority, text: str) -> SendResult:
        print(f"[HOLD] {text}")
        return SendResult(True, self.mode, None)
