"""Fournisseur hôtelier SYNTHÉTIQUE pour les tests et la démonstration.

Il simule un vrai système externe : registre propre, non idempotent (chaque création crée
une réservation), capable de créer puis de perdre la réponse. Aucune réservation réelle.
"""

from __future__ import annotations

import fcntl
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .outcome import Outcome

CreateMode = Literal["respond", "lost_response", "reject"]
SupportMode = Literal["finds", "unhelpful", "unavailable"]


@dataclass
class SyntheticHotel:
    create_mode: CreateMode = "lost_response"
    visible_after: int | None = 2  # contrôles avant que la réservation soit visible ; None = jamais
    support_mode: SupportMode = "unhelpful"
    timeout_s: float = 0.05
    idempotent: bool = False  # True: the provider deduplicates on operation_id (a retry of the same operation creates nothing)
    bookings: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def create(self, operation_id: str, **attrs: Any) -> Outcome:
        with self._lock:
            if self.create_mode == "reject":
                self.requests.append({"kind": "CREATE", "operation_id": operation_id, "response": "REJECTED"})
                return Outcome.rejected("No room available (refused before creation)")
            same = next((b for b in self.bookings if b["operation_id"] == operation_id), None) if self.idempotent else None
            if same:
                self.requests.append({"kind": "CREATE", "operation_id": operation_id, "response": "DEDUPLICATED"})
                return Outcome.confirmed(same["receipt"], operation_id=operation_id)
            receipt = f"RCPT-{secrets.token_hex(3).upper()}"
            self.bookings.append({"operation_id": operation_id, "receipt": receipt, "attrs": attrs, "checks": 0})
            self.requests.append({"kind": "CREATE", "operation_id": operation_id, "response": "CREATED" if self.create_mode == "respond" else "LOST_RESPONSE"})
        if self.create_mode == "respond":
            return Outcome.confirmed(receipt, operation_id=operation_id)
        time.sleep(self.timeout_s)
        raise TimeoutError(f"no answer from the provider after {self.timeout_s:g} s")

    def status(self, operation_id: str) -> Outcome:
        with self._lock:
            b = next((x for x in self.bookings if x["operation_id"] == operation_id), None)
            if not b:
                self.requests.append({"kind": "STATUS", "operation_id": operation_id, "response": "NOT_FOUND"})
                return Outcome.not_found()
            b["checks"] += 1
            visible = self.visible_after is not None and b["checks"] >= self.visible_after
            self.requests.append({"kind": "STATUS", "operation_id": operation_id, "response": "CONFIRMED" if visible else "NOT_FOUND"})
            return Outcome.confirmed(b["receipt"], operation_id=operation_id) if visible else Outcome.not_found()

    def support(self, question: str, operation_id: str) -> Outcome:
        with self._lock:
            self.requests.append({"kind": "SUPPORT", "operation_id": operation_id, "response": self.support_mode})
            if self.support_mode == "unavailable":
                raise ConnectionError("support unreachable")
            b = next((x for x in self.bookings if x["operation_id"] == operation_id), None)
            if self.support_mode == "finds" and b:
                return Outcome.reply(f"Booking found: {b['receipt']}.", operation_id=operation_id, receipt=b["receipt"])
            return Outcome.reply("We are forwarding your request to the relevant team.")

    @property
    def created(self) -> int:
        return len(self.bookings)


class LedgerHotel:
    """A synthetic provider whose register is a file, shared by several processes (one line per
    booking, under an exclusive file lock). Used to count provider-side effects across processes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.touch(exist_ok=True)

    def _locked(self, fn):
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                rows = [json.loads(x) for x in f.read().splitlines() if x.strip()]
                return fn(f, rows)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def create(self, operation_id: str, **attrs: Any) -> Outcome:
        receipt = f"RCPT-{secrets.token_hex(3).upper()}"

        def write(f, rows):
            f.write(json.dumps({"operation_id": operation_id, "receipt": receipt, "attrs": attrs}) + "\n")
            f.flush()

        self._locked(write)
        return Outcome.confirmed(receipt, operation_id=operation_id)

    def status(self, operation_id: str) -> Outcome:
        rows = self._locked(lambda f, rows: rows)
        b = next((x for x in rows if x["operation_id"] == operation_id), None)
        return Outcome.confirmed(b["receipt"], operation_id=operation_id) if b else Outcome.not_found()

    def support(self, question: str, operation_id: str) -> Outcome:
        return Outcome.reply("We are forwarding your request to the relevant team.")

    @property
    def created(self) -> int:
        return self._locked(lambda f, rows: len(rows))


BOOKING = {
    "provider": "hotel-synthetique",
    "hotel_id": "LYON-EUREXPO-042",
    "hotel_name": "Fictional Eurexpo Lyon Hotel",
    "city": "Lyon",
    "traveler": "Fictional Traveler A.",
    "check_in": "2026-11-17",
    "check_out": "2026-11-19",
    "price": 238,
    "currency": "EUR",
}


def wire(hab, hotel: SyntheticHotel) -> None:
    """Branche le fournisseur synthétique sur les pouvoirs du profil « réservation »."""

    @hab.read("search_hotels")
    def search_hotels(city: str = "Lyon") -> Any:
        return Outcome.value([BOOKING | {"distance": "900 m from hall 4"}])

    @hab.read("venue_info")
    def venue_info() -> Any:
        return Outcome.value({"venue": "Fictional exhibition centre", "hours": "9:00–18:00"})

    idem = bool(getattr(hotel, "idempotent", False))

    @hab.effect("create_booking", idempotency="operation_id" if idem else "none",
                verified_by="tests/test_guarantees.py::test_provider_side_deduplication_is_labelled_by_what_was_verified" if idem else None)
    def create_booking(provider: str, hotel_id: str, traveler: str, check_in: str, check_out: str, price: float, currency: str, city: str, hotel_name: str = "", operation_id: str = "") -> Outcome:
        return hotel.create(operation_id, provider=provider, hotel_id=hotel_id, traveler=traveler, check_in=check_in, check_out=check_out, price=price, currency=currency, city=city)

    @hab.status("check_booking")
    def check_booking(operation_id: str) -> Outcome:
        return hotel.status(operation_id)

    @hab.contact("contact_support")
    def contact_support(question: str, operation_id: str = "") -> Outcome:
        return hotel.support(question, operation_id)

    @hab.effect("send_itinerary")
    def send_itinerary(traveler: str, booking_reference: str, operation_id: str = "") -> Outcome:
        return Outcome.confirmed(f"ITIN-{booking_reference}", operation_id=operation_id)
