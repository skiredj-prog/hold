"""Serveur HOLD (facultatif) : webhook WhatsApp + API de HOLD Studio.

- Le webhook est public (signé par Meta).
- L'API du Studio (missions, journaux, réponses console) n'est servie qu'en local, ou avec
  le jeton HOLD_STUDIO_TOKEN : exposer le webhook par un tunnel n'expose pas les missions.
- Embarqué dans le processus de l'agent (`hab.serve()`), le serveur peut poursuivre une mission
  dès la réponse de l'autorité ; lancé seul (`hold studio`), il enregistre et l'agent reprendra.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import ValidationError

from .profile import Profile, json_schema, load_profile
from .relay.whatsapp import Inbound, WhatsAppConfig, simulated_tap, button_id, verify_signature
from .replay import decision_replay, export_journal, journal_profiles, state_replay
from .runtime import Habilitation
from .store import Store

STUDIO = Path(__file__).parent / "studio"
CATALOG = Path(__file__).parent / "profiles"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip")
MAX_WEBHOOK_BODY = 256 * 1024


def create_app(store: Store, habilitations: list[Habilitation] | None = None, *, whatsapp: WhatsAppConfig | None = None, studio_token: str | None = None) -> FastAPI:
    habs = {h.profile.fingerprint: h for h in (habilitations or [])}
    cfg = whatsapp or WhatsAppConfig.from_env()
    token = studio_token if studio_token is not None else os.environ.get("HOLD_STUDIO_TOKEN", "")
    relay = next((h.relay for h in habs.values() if h.relay is not None), None)

    def resolve(mission_id: str) -> Habilitation | None:
        row = store.mission(mission_id)
        if not row:
            return None
        if row["profile_fp"] in habs:
            return habs[row["profile_fp"]]
        raw = store.profile_json(row["profile_fp"])
        if not raw:
            return None
        h = Habilitation(load_profile(json.loads(raw)), store=store, relay=relay)  # sans outils : enregistre seulement
        habs[row["profile_fp"]] = h
        return h

    inbound = Inbound(store, resolve)
    app = FastAPI(title="HOLD", version="0.1.0", docs_url=None, redoc_url=None)

    def studio_guard(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if token and hmac.compare_digest(auth, f"Bearer {token}"):
            return
        host = request.client.host if request.client else ""
        if host in LOOPBACK and not any(h in request.headers for h in FORWARD_HEADERS):
            return
        raise HTTPException(403, "Studio API is local only (or use HOLD_STUDIO_TOKEN).")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "whatsapp": {"send": cfg.can_send, "receive": cfg.can_receive, "mode": getattr(relay, "mode", None)}}

    # -- webhook WhatsApp -----------------------------------------------------------------
    @app.get("/api/whatsapp/webhook")
    def wa_verify(request: Request) -> Response:
        q = request.query_params
        if not cfg.verify_token:
            return Response(status_code=404)
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), cfg.verify_token):
            return PlainTextResponse(q.get("hub.challenge", ""))
        return Response(status_code=403)

    @app.post("/api/whatsapp/webhook")
    async def wa_event(request: Request) -> Response:
        if not cfg.app_secret:
            return Response(status_code=503)
        try:
            declared = int(request.headers.get("content-length", "-1"))
        except ValueError:
            declared = -1
        if declared > MAX_WEBHOOK_BODY:
            return Response(status_code=413)
        raw = await request.body()
        if len(raw) > MAX_WEBHOOK_BODY:
            return Response(status_code=413)
        if not verify_signature(raw, request.headers.get("x-hub-signature-256"), cfg.app_secret):
            return Response(status_code=401)
        try:
            payload = json.loads(raw)
        except ValueError:
            return Response(status_code=400)
        return JSONResponse(inbound.handle(payload, mode="cloud"))

    # -- Studio : catalogue et profils (aucune donnée de mission) --------------------------------
    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        items = []
        for f in sorted(CATALOG.glob("*.hold.yaml")):
            p = load_profile(f)
            items.append({"file": f.name, "id": p.id, "title": p.title, "description": p.description, "profile": p.model_dump(mode="json", exclude_none=True), "fingerprint": p.fingerprint})
        return {"templates": items, "schema": json_schema()}

    @app.post("/api/profiles/validate")
    async def validate(request: Request) -> dict[str, Any]:
        data = await request.json()
        try:
            p = Profile.model_validate(data)
        except ValidationError as e:
            return {"valid": False, "errors": [{"loc": ".".join(str(x) for x in err["loc"]), "msg": err["msg"]} for err in e.errors()]}
        return {"valid": True, "fingerprint": p.fingerprint, "profile": p.model_dump(mode="json", exclude_none=True)}

    # -- Studio : missions (local seulement) ------------------------------------------------
    @app.get("/api/missions")
    def missions(request: Request) -> list[dict[str, Any]]:
        studio_guard(request)
        return [{k: r[k] for k in ("id", "title", "status", "profile_fp", "profile_rev", "created_at", "head_seq")} for r in store.missions()]

    @app.get("/api/missions/{mission_id}")
    def mission(mission_id: str, request: Request) -> dict[str, Any]:
        studio_guard(request)
        h = resolve(mission_id)
        if not h:
            raise HTTPException(404, "Unknown mission")
        m = h.mission(mission_id)
        journal = export_journal(store, mission_id)
        chain = m.verify()
        relay_rows = [dict(r) for r in store.read("SELECT direction, kind, mode, status, question_id, peer, detail, error, created_at FROM relay_messages WHERE mission=? ORDER BY id", (mission_id,))]
        return {
            "journal": journal,
            "verification": {
                "chain": chain.__dict__,
                "state": state_replay(store, mission_id, h.profile),
                "decisions": decision_replay(m.events(), journal_profiles(store, m.events())),
            },
            "open_request": m.open_request(),
            "effects": m.effects(),
            "relay": relay_rows,
        }

    @app.post("/api/missions/{mission_id}/answer")
    async def answer(mission_id: str, request: Request) -> dict[str, Any]:
        studio_guard(request)
        body = await request.json()
        h = resolve(mission_id)
        if not h:
            raise HTTPException(404, "Unknown mission")
        return h.mission(mission_id).answer(str(body.get("question_id", "")), str(body.get("code", "")), channel="console", evidence={})

    @app.post("/api/missions/{mission_id}/simulate-tap")
    async def simulate(mission_id: str, request: Request) -> dict[str, Any]:
        """Mode simulé : fabrique le webhook que Meta enverrait et le fait suivre le même traitement."""
        studio_guard(request)
        if relay is None or getattr(relay, "mode", "") != "simulated":
            raise HTTPException(409, "Only available with a simulated WhatsApp relay.")
        body = await request.json()
        h = resolve(mission_id)
        req = h.mission(mission_id).open_request() if h else None
        if not req:
            raise HTTPException(409, "No open Decision Request.")
        sender = h.profile.authority.contact if not body.get("stranger") else "15550000000"
        code = str(body.get("code", ""))
        title = next((o["title"] for o in req["options"] if o["code"] == code), code)
        return inbound.handle(simulated_tap(sender, button_id(mission_id, req["question_id"], code), title), mode="simulated")

    # -- Studio : interface -------------------------------------------------------------------
    @app.get("/")
    def index() -> Response:
        page = STUDIO / "index.html"
        if page.exists():
            return FileResponse(page, media_type="text/html; charset=utf-8")
        return HTMLResponse("<h1>HOLD</h1><p>Studio is not installed.</p>")

    app.state.inbound = inbound
    return app


def serve(store: Store, habilitations: list[Habilitation] | None = None, *, host: str = "127.0.0.1", port: int = 8787, background: bool = True):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(create_app(store, habilitations), host=host, port=port, log_level="warning"))
    if not background:
        server.run()
        return server
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    return server
