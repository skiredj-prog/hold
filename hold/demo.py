"""Démonstration : le piège de la double réservation.

Le même agent de voyage, deux fois :
  - sans HOLD (témoin) : délai dépassé → l'agent conclut à l'échec → recrée → deux réservations ;
  - avec HOLD : le profil « réservation » attaché, l'effet inconnu ouvre une hésitation,
    l'agent reprend seul après preuve, ou la question part sur WhatsApp.

L'agent est Llama 3.2 (poids ouverts Meta) servi par Ollama, en appels d'outils ;
un agent scripté reproduit le même plan sans modèle.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from .discern import LlamaDiscerner, ReferenceDiscerner
from .i18n import lang, t
from .narrate import line
from .outcome import ToolResult
from .relay.base import ConsoleRelay, SimulatedRelay
from .relay.whatsapp import WhatsAppConfig, WhatsAppRelay
from .runtime import Habilitation, Mission
from .testing import BOOKING, SyntheticHotel, wire

PROFILE = Path(__file__).parent / "profiles" / "reservation.hold.yaml"

SCENARIOS: dict[str, dict[str, Any]] = {
    "lost-response": {"label": {"en": "lost answer, visible at the 2nd check", "fr": "réponse perdue, puis visible au 2e contrôle"}, "hotel": {"visible_after": 2}},
    "support": {"label": {"en": "never visible on status; the provider's support finds it", "fr": "jamais visible au statut ; le support retrouve la réservation"}, "hotel": {"visible_after": None, "support_mode": "finds"}},
    "authority": {"label": {"en": "not found: the authority grants one check on WhatsApp, which finds the receipt", "fr": "introuvable : l'autorité autorise un contrôle sur WhatsApp, qui trouve le reçu"}, "hotel": {"visible_after": 3}},
    "never": {"label": {"en": "never visible: the authority closes it unconfirmed", "fr": "jamais visible : l'autorité clôt sans confirmer"}, "hotel": {"visible_after": None}},
    "envelope": {"label": {"en": "the only hotel available is above the ceiling: the authority decides", "fr": "seul hôtel disponible au-dessus du plafond : l'autorité tranche"}, "hotel": {"create_mode": "respond"}, "price": 340},
}


def label(scenario: str) -> str:
    return SCENARIOS[scenario]["label"][lang()]

TASK = (
    "Prepare the business trip to the Lyon trade fair for 'Fictional Traveler A.' from 2026-11-17 to 2026-11-19. "
    "Check the venue, search a hotel in Lyon, book it with create_booking using exactly the fields returned by search_hotels, "
    "then send the itinerary with send_itinerary using the booking receipt as booking_reference."
)

SYSTEM = (
    "You are a travel agent that acts through tools. Every tool call goes through a habilitation layer that may refuse it or put it on HOLD. "
    "Rules: call create_booking at most once for a stay. If a tool returns status HELD, do not retry it and do not book anything equivalent: "
    "continue only independent work, or stop and report. If a tool returns REFUSED, do not try to work around it. "
    "When done, answer with a two-sentence report: what is confirmed, what is not."
)


# -- témoin sans HOLD ------------------------------------------------------------------------

def run_baseline(hotel: SyntheticHotel, booking: dict[str, Any], out: Callable[[str], None] = print) -> int:
    out(t("d_baseline"))
    for attempt in (1, 2):
        try:
            r = hotel.create(f"naive_{attempt}", **booking)
            out(t("d_attempt_ok", n=attempt, status=r.status, receipt=r.receipt or ""))
            break
        except TimeoutError:
            out(t("d_attempt_timeout", n=attempt))
            hotel.create_mode = "respond"
    out(t("d_baseline_count", n=hotel.created))
    return hotel.created


# -- agents ----------------------------------------------------------------------------------

class ScriptedAgent:
    """Même plan que l'agent Llama, sans modèle (bras de contrôle et repli hors-ligne)."""

    name = "scripted"

    def run(self, mission: Mission, booking: dict[str, Any], log: Callable[[str, ToolResult], None]) -> str:
        r = mission.call("venue_info")
        log("venue_info", r)
        r = mission.call("search_hotels", city="Lyon")
        log("search_hotels", r)
        r = mission.call("create_booking", **booking)
        log("create_booking", r)
        if r.status in ("OK", "ALREADY_CONFIRMED"):
            r2 = mission.call("send_itinerary", traveler=booking["traveler"], booking_reference=r.data["receipt"])
            log("send_itinerary", r2)
            return t("d_done_report", receipt=r.data["receipt"])
        return t("d_pending_report", status=r.status)

    def resume(self, mission: Mission, booking: dict[str, Any], log: Callable[[str, ToolResult], None]) -> str:
        eff = next((e for e in mission.effects() if e["action"] == "create_booking" and e["status"] == "CONFIRMED"), None)
        if not eff:
            return t("d_still")
        r = mission.call("send_itinerary", traveler=booking["traveler"], booking_reference=eff["receipt"])
        log("send_itinerary", r)
        return t("d_done_report", receipt=eff["receipt"])


@dataclass
class LlamaAgent:
    """Agent à appels d'outils : Llama via Ollama /api/chat (tools)."""

    model: str = field(default_factory=lambda: os.environ.get("LLAMA_MODEL", "llama3.2:3b"))
    base_url: str = field(default_factory=lambda: os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:11434"))
    max_steps: int = 10
    name: str = "llama"

    def __post_init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def _chat(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        r = httpx.post(
            f"{self.base_url.rstrip('/')}/api/chat",
            timeout=300,
            json={"model": self.model, "stream": False, "messages": self.messages, "tools": tools, "options": {"temperature": 0, "seed": 42, "num_ctx": 8192}},
        )
        r.raise_for_status()
        return r.json()["message"]

    def _loop(self, mission: Mission, log: Callable[[str, ToolResult], None]) -> str:
        tools = mission.tools()
        for _ in range(self.max_steps):
            msg = self._chat(tools)
            self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip()
            for call in calls:
                fn = call.get("function") or {}
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                result = mission.dispatch(name, args)
                log(name, result)
                self.messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result.for_model(), ensure_ascii=False)})
        return "Step limit reached."

    def run(self, mission: Mission, booking: dict[str, Any], log: Callable[[str, ToolResult], None]) -> str:
        self.messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": TASK}]
        return self._loop(mission, log)

    def resume(self, mission: Mission, booking: dict[str, Any], log: Callable[[str, ToolResult], None]) -> str:
        eff = next((e for e in mission.effects() if e["action"] == "create_booking" and e["status"] == "CONFIRMED"), None)
        note = (
            f"HOLD update: create_booking is now confirmed after reconciliation (receipt {eff['receipt']}). Nothing was repeated. Continue the plan."
            if eff else "HOLD update: the booking is still unconfirmed. Do not retry. Report."
        )
        self.messages.append({"role": "user", "content": note})
        return self._loop(mission, log)


# -- exécution -------------------------------------------------------------------------------

def build(scenario: str, *, store: str = ":memory:", discerner: str = "reference", relay: str = "simulated", contact: str | None = None):
    conf = SCENARIOS[scenario]
    hotel = SyntheticHotel(timeout_s=float(os.environ.get("HOLD_DEMO_TIMEOUT", "1.5")), **conf["hotel"])
    if relay == "whatsapp":
        rel = WhatsAppRelay(WhatsAppConfig.from_env())
    elif relay == "console":
        rel = ConsoleRelay()
    else:
        rel = WhatsAppRelay(WhatsAppConfig.from_env(), simulate=True)
    profile: Any = PROFILE
    if contact:
        from .profile import load_profile

        p = load_profile(PROFILE)
        profile = p.model_copy(update={"authority": p.authority.model_copy(update={"contact": contact})})
    disc = LlamaDiscerner() if discerner == "llama" else ReferenceDiscerner()
    hab = Habilitation(profile, store=store, discerner=disc, relay=rel)
    wire(hab, hotel)
    booking = dict(BOOKING)
    if conf.get("price"):
        from .outcome import Outcome

        booking["price"] = conf["price"]
        hab.tools["search_hotels"].fn = lambda city="Lyon": Outcome.value([booking | {"distance": "1.2 km from hall 4", "note": "only hotel available"}])
    return hab, hotel, booking


def run(scenario: str = "authority", *, agent: str = "scripted", discerner: str = "reference", relay: str = "simulated",
        store: str = ":memory:", serve_port: int | None = None, wait_s: float = 600, contact: str | None = None,
        color: bool = True, out: Callable[[str], None] = print) -> dict[str, Any]:
    hab, hotel, booking = build(scenario, store=store, discerner=discerner, relay=relay, contact=contact)
    if serve_port:
        hab.serve(host="0.0.0.0" if relay == "whatsapp" else "127.0.0.1", port=serve_port)
        out(t("d_server", port=serve_port))
    m = hab.open_mission(t("d_title", label=label(scenario)))
    seen = {"seq": 0}

    def tail() -> None:
        for e in m.events():
            if e.seq > seen["seq"]:
                seen["seq"] = e.seq
                s = line(e, color)
                if s:
                    out(s)

    def log(name: str, r: ToolResult) -> None:
        tail()
        out(t("d_agent", name=name, status=r.status, msg=(" · " + r.message) if r.status != "OK" else ""))

    out(t("d_mission", id=m.id, scenario=scenario, agent=agent, discerner=discerner, relay=getattr(hab.relay, "mode", relay)))
    tail()
    a = LlamaAgent() if agent == "llama" else ScriptedAgent()
    report = a.run(m, booking, log)
    tail()
    if m.status in ("HELD", "AWAITING_AUTHORITY"):
        req = m.open_request()
        if req:
            out(t("d_waiting", authority=hab.profile.authority.name, q=req["question_id"], options=", ".join(o["code"] for o in req["options"])))
            if getattr(hab.relay, "mode", "") == "simulated" and not serve_port:
                code = "APPROVE_ONCE" if req["kind"] == "ENVELOPE_BREACH" else ("EXTEND_CHECKS" if scenario == "authority" else "CLOSE_UNCONFIRMED")
                out(t("d_simulated_tap", code=code))
                from .relay.whatsapp import Inbound, button_id, simulated_tap

                Inbound(hab.store, lambda mid: hab if mid == m.id else None).handle(
                    simulated_tap(hab.profile.authority.contact, button_id(m.id, req["question_id"], code), code), mode="simulated")
        m.wait(timeout=wait_s)
        hab.join(30)
        tail()
        if m.status == "OPEN":
            report = a.resume(m, booking, log)
            tail()
    if m.status == "OPEN":
        m.close()
        if m.status == "CLOSED_COMPLETED":
            m.notify(t("d_notice", report=report))
    tail()
    out(t("d_report", report=report))
    chain = m.verify()
    out(t("d_summary", bookings=hotel.created, chain=t("d_valid") if chain.valid else t("d_invalid"), events=chain.count, status=m.status))
    return {"mission": m.id, "status": m.status, "bookings": hotel.created, "chain_valid": chain.valid, "events": chain.count, "hab": hab, "report": report}


if __name__ == "__main__":  # pragma: no cover
    run(sys.argv[1] if len(sys.argv) > 1 else "authority")
