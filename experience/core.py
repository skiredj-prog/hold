"""One coherent HOLD mission, with accountable authority and an inspectable record.

Only the *demo experience* is new. All governance decisions are delegated to
HOLD's existing Habilitation/Mission/WhatsApp inbound implementations.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from hold.demo import LlamaAgent, ScriptedAgent, build
from hold.relay.whatsapp import Inbound, WhatsAppConfig, button_id, simulated_tap
from hold.server import create_app

from .envelope import capture

ROOT = Path(__file__).parent
LOOPBACK = {"127.0.0.1", "::1", "testclient"}
PUBLIC_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip")
NUMBER = re.compile(r"^[1-9][0-9]{7,14}$")


def local_only(request: Request) -> None:
    # Safety is primarily enforced by binding HOLD Studio to localhost and
    # exposing only the signed webhook through the distinct gateway.
    host = request.client.host if request.client else ""
    if host not in LOOPBACK or any(request.headers.get(x) for x in PUBLIC_HEADERS):
        raise HTTPException(403, "The experience control plane is local only")


class Experience:
    def __init__(self, *, relay: str = "simulated", agent: str = "scripted", contact: str | None = None,
                 db: str = "hold-experience.db", wait_seconds: int = 180):
        if relay not in {"simulated", "whatsapp"}:
            raise ValueError("relay must be simulated or whatsapp")
        if agent not in {"scripted", "llama"}:
            raise ValueError("agent must be scripted or llama")
        cfg = WhatsAppConfig.from_env()
        if relay == "whatsapp":
            if not (cfg.can_send and cfg.can_receive):
                raise ValueError("Live mode requires WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN")
            if not contact or not NUMBER.fullmatch(contact):
                raise ValueError("Live mode requires --contact in international digits-only format")
        if relay == "simulated" and not contact:
            contact = "212600000000"  # demo-only fictional identity; never messaged
        self.hab, self.hotel, self.booking = build("authority", store=db, relay=relay, contact=contact)
        self.relay = relay
        self.agent_name = agent
        self.wait_seconds = wait_seconds
        self.mission = None
        self.worker = None
        self.error = None
        self.report = None
        self._lock = threading.RLock()
        self.inbound = Inbound(self.hab.store, lambda mid: self.hab if self.mission and mid == self.mission.id else None)
        self.app = create_app(self.hab.store, [self.hab], whatsapp=cfg, studio_token=os.environ.get("HOLD_STUDIO_TOKEN") or "")
        self._routes()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.mission is not None:
                raise ValueError("One mission per process. Restart the experience for another run.")
            self.mission = self.hab.open_mission("Lyon hotel — uncertain booking and human authority",
                                                agent={"name": self.agent_name, "model": "llama3.2:3b" if self.agent_name == "llama" else "scripted reference"})
            self.worker = threading.Thread(target=self._drive, daemon=True, name="hold-experience-agent")
            self.worker.start()
            return {"mission_id": self.mission.id, "started": True}

    def _drive(self) -> None:
        try:
            m = self.mission
            a = LlamaAgent(max_steps=10) if self.agent_name == "llama" else ScriptedAgent()
            report = a.run(m, self.booking, lambda *_: None)
            if m.status in ("HELD", "AWAITING_AUTHORITY"):
                m.wait(timeout=self.wait_seconds)
                self.hab.join(5)
                if m.status == "OPEN":
                    report = a.resume(m, self.booking, lambda *_: None)
            if m.status == "OPEN":
                m.close()
            self.report = report
        except Exception as ex:  # don't falsely close a mission that encountered an error
            self.error = f"{type(ex).__name__}: {ex}"

    def tap(self, code: str, *, stranger: bool = False) -> dict[str, Any]:
        if self.relay != "simulated":
            raise ValueError("Simulated authority tap disabled in live mode")
        m = self.mission
        req = m.open_request() if m else None
        if not req:
            raise ValueError("No open Decision Request")
        permitted = {option["code"] for option in req["options"]}
        if code not in permitted:
            raise ValueError("Choice is not part of the pending request")
        sender = "15550001111" if stranger else self.hab.profile.authority.contact
        payload = simulated_tap(sender, button_id(m.id, req["question_id"], code), code)
        return self.inbound.handle(payload, mode="simulated")

    def snapshot(self) -> dict[str, Any]:
        m = self.mission
        if not m:
            return {"started": False, "relay": self.relay, "agent": self.agent_name,
                    "provider": "SYNTHETIC HOTEL", "control": "LOCAL ONLY", "error": self.error}
        events = m.events()
        request = m.open_request()
        relay = [dict(r) for r in m.store.read(
            "SELECT direction, kind, mode, status, peer, created_at FROM relay_messages WHERE mission=? ORDER BY id", (m.id,)
        )]
        return {
            "started": True, "mission_id": m.id, "status": m.status,
            "relay": self.relay, "agent": self.agent_name,
            "provider": "SYNTHETIC HOTEL", "control": "LOCAL ONLY",
            "profile": {"title": m.profile.title, "id": m.profile.id, "revision": m.profile.revision,
                        "max_amount": 300, "currency": "EUR", "authority": m.profile.authority.name,
                        "fingerprint": m.profile.fingerprint},
            "open_request": request,
            "events": [{"seq": e.seq, "type": e.type, "at": e.at,
                        "detail": self._event_detail(e)} for e in events],
            "effects": [{"action": e["action"], "status": e["status"], "receipt": e["receipt"],
                         "operation_id": e["operation_id"]} for e in m.effects()],
            "relay_messages": relay,
            "synthetic_bookings": self.hotel.created,
            "provider_requests": list(self.hotel.requests),
            "chain_valid": bool(m.verify().valid), "worker_active": bool(self.worker and self.worker.is_alive()),
            "error": self.error,
        }

    @staticmethod
    def _event_detail(e) -> str:
        d = e.data
        if e.type == "ACTION_REFUSED":
            return f"{d.get('action', '')}: {d.get('reason') or d.get('rule') or 'refused'}"
        if e.type in ("EFFECT_OUTCOME", "RECONCILED", "EVIDENCE"):
            return str(d.get("status") or d.get("kind") or d.get("receipt") or "")
        if e.type in ("DECISION_REQUEST", "AUTHORITY_ANSWER"):
            return str(d.get("question") or d.get("answer") or d.get("question_id") or "")[:170]
        if e.type == "DECISION":
            return str((d.get("verdict") or {}).get("applied") or d.get("outcome") or "")
        if e.type == "MISSION_CLOSED":
            return str(d.get("outcome") or "")
        return ""

    def _routes(self) -> None:
        app = self.app

        @app.get("/experience")
        def page(request: Request):
            local_only(request)
            return FileResponse(ROOT / "index.html", media_type="text/html")

        @app.get("/api/experience/state")
        def state(request: Request):
            local_only(request)
            return self.snapshot()

        @app.post("/api/experience/start")
        def start(request: Request):
            local_only(request)
            try:
                return self.start()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        @app.post("/api/experience/tap")
        async def tap(request: Request):
            local_only(request)
            try:
                data = await request.json()
                return self.tap(str(data.get("code", "")))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        @app.get("/api/experience/inspect")
        def inspect(request: Request):
            local_only(request)
            if not self.mission:
                raise HTTPException(409, "Start a mission before inspecting evidence")
            report, _ = capture(self.mission, self.hotel, channel=self.relay, agent=self.agent_name)
            return report

        @app.get("/api/experience/envelope")
        def envelope(request: Request):
            local_only(request)
            if not self.mission:
                raise HTTPException(409, "Start a mission before exporting evidence")
            _, content = capture(self.mission, self.hotel, channel=self.relay, agent=self.agent_name)
            return Response(content=content, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="HOLD-{self.mission.id}-evidence.zip"',
                "Cache-Control": "no-store"})


def main() -> None:
    parser = argparse.ArgumentParser(description="HOLD one-mission evidence experience (synthetic hotel)")
    parser.add_argument("--relay", choices=("simulated", "whatsapp"), default="simulated")
    parser.add_argument("--agent", choices=("scripted", "llama"), default="scripted")
    parser.add_argument("--contact", help="Authority's WhatsApp number, digits-only, required for live mode")
    parser.add_argument("--db", default="hold-experience.db")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--wait", type=int, default=180)
    args = parser.parse_args()
    x = Experience(relay=args.relay, agent=args.agent, contact=args.contact, db=args.db, wait_seconds=args.wait)
    print(f"HOLD experience: http://127.0.0.1:{args.port}/experience")
    print(f"Agent: {args.agent} | relay: {args.relay} | provider: SYNTHETIC | one mission per process")
    if args.relay == "whatsapp":
        print("Expose ONLY the gateway port 8788, never the private experience port")
    uvicorn.run(x.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
