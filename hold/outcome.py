"""Ce que renvoient les outils de l'agent, et ce que HOLD renvoie à l'agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OutcomeStatus = Literal["CONFIRMED", "REJECTED", "NOT_FOUND", "UNKNOWN", "REPLY", "VALUE"]


@dataclass(frozen=True)
class Outcome:
    """Résultat d'un outil réel. Un délai dépassé ou une coupure se signalent par une exception
    (TimeoutError, ConnectionError, OSError) ou par Outcome.unknown() : HOLD les traite comme UNKNOWN."""

    status: OutcomeStatus
    receipt: str | None = None
    operation_id: str | None = None
    reason: str | None = None
    text: str | None = None
    data: Any = None

    @staticmethod
    def confirmed(receipt: str, *, operation_id: str | None = None, data: Any = None) -> "Outcome":
        return Outcome("CONFIRMED", receipt=receipt, operation_id=operation_id, data=data)

    @staticmethod
    def rejected(reason: str) -> "Outcome":
        """Refus explicite AVANT toute création : seul cas où un nouvel essai est admissible."""
        return Outcome("REJECTED", reason=reason)

    @staticmethod
    def not_found(reason: str = "not found") -> "Outcome":
        """« Introuvable » ne prouve pas l'échec : l'effet peut exister sans être visible."""
        return Outcome("NOT_FOUND", reason=reason)

    @staticmethod
    def unknown(reason: str) -> "Outcome":
        return Outcome("UNKNOWN", reason=reason)

    @staticmethod
    def reply(text: str, *, operation_id: str | None = None, receipt: str | None = None) -> "Outcome":
        """Réponse d'un contact (support). Ne vaut preuve que si elle est corrélée à l'opération."""
        return Outcome("REPLY", text=text, operation_id=operation_id, receipt=receipt)

    @staticmethod
    def value(data: Any) -> "Outcome":
        return Outcome("VALUE", data=data)


ResultStatus = Literal["OK", "REFUSED", "HELD", "ALREADY_CONFIRMED", "FAILED", "CLOSED", "REVOKED"]


@dataclass
class ToolResult:
    """Ce que l'agent reçoit en retour d'un appel habillé par HOLD."""

    status: ResultStatus
    message: str
    data: Any = None
    rule: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def for_model(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        if self.rule:
            out["rule"] = self.rule
        return out
