import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hold.relay.whatsapp import (
    Inbound, WhatsAppConfig, WhatsAppRelay, button_id, parse_button_id, parse_webhook, question_message, simulated_tap, verify_signature,
)
from hold.runtime import Habilitation
from hold.server import create_app
from hold.testing import SyntheticHotel, wire

from conftest import PROFILE, opened

SECRET = "app-secret"
AUTH = "212600000000"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def world(booking):
    cfg = WhatsAppConfig(app_secret=SECRET, verify_token="verify-me")
    relay = WhatsAppRelay(cfg, simulate=True)
    hab = Habilitation(PROFILE, store=":memory:", relay=relay, background=False)
    hotel = SyntheticHotel(visible_after=3)
    wire(hab, hotel)
    app = create_app(hab.store, [hab], whatsapp=cfg, studio_token="")
    client = TestClient(app)
    m = opened(hab)
    m.call("create_booking", **booking)
    return hab, hotel, relay, client, m


def post(client, payload, secret=SECRET, headers=None):
    body = json.dumps(payload).encode()
    h = {"content-type": "application/json", "x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
    return client.post("/api/whatsapp/webhook", content=body, headers=h | (headers or {}))


def test_outgoing_decision_request_is_a_conformant_interactive_message(world):
    hab, hotel, relay, client, m = world
    msg = relay.outbox[0]
    assert msg["to"] == AUTH and msg["type"] == "interactive"
    buttons = msg["interactive"]["action"]["buttons"]
    assert len(buttons) == 3 and all(len(b["reply"]["title"]) <= 20 and len(b["reply"]["id"]) <= 256 for b in buttons)
    assert parse_button_id(buttons[0]["reply"]["id"])[0] == m.id
    assert len(msg["interactive"]["body"]["text"]) <= 1024


def test_webhook_verification_and_signature(world):
    hab, hotel, relay, client, m = world
    assert client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"}).text == "42"
    assert client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}).status_code == 403
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    assert client.post("/api/whatsapp/webhook", content=body).status_code == 401
    assert post(client, json.loads(body), secret="other").status_code == 401
    assert post(client, json.loads(body)).status_code == 200


def test_tap_binding_idempotence_and_resume(world):
    hab, hotel, relay, client, m = world
    q = m.open_request()["question_id"]
    stranger = simulated_tap("15550001111", button_id(m.id, q, "EXTEND_CHECKS"), "x")
    r = post(client, stranger).json()["results"][0]
    assert r == {"message_id": r["message_id"], "accepted": False, "reason": "SENDER_NOT_AUTHORITY"}
    tap = simulated_tap(AUTH, button_id(m.id, q, "EXTEND_CHECKS"), "Autoriser 1 contrôle")
    first = post(client, tap).json()["results"][0]
    again = post(client, tap).json()["results"][0]
    assert first["accepted"] and again["duplicate"]
    assert m.status == "OPEN" and hotel.created == 1  # contrôle accordé → reçu → reprise
    forged = simulated_tap(AUTH, button_id(m.id, "q_000000000000", "REVOKE"), "x")
    assert not post(client, forged).json()["results"][0]["accepted"]
    acks = [x for x in relay.outbox if x["type"] == "text"]
    assert any("réponse enregistrée" in a["text"]["body"] for a in acks) and any("plus ouverte" in a["text"]["body"] for a in acks)
    assert "RELAY_REJECTED" in [e.type for e in m.events()]
    assert m.verify().valid


def test_stop_message_revokes_active_missions(world):
    hab, hotel, relay, client, m = world
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [{"from": AUTH, "id": "wamid.T1", "type": "text", "text": {"body": "STOP"}}]}}]}]}
    r = post(client, payload).json()["results"][0]
    assert r["accepted"] and r["missions"] == [m.id]
    assert m.status == "REVOKED"
    other = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [{"from": "15550001111", "id": "wamid.T2", "type": "text", "text": {"body": "STOP"}}]}}]}]}
    assert not post(client, other).json()["results"][0]["accepted"]


def test_parse_webhook_never_raises():
    for p in [None, 1, "x", {}, {"object": "page"}, {"object": "whatsapp_business_account", "entry": None}, {"object": "whatsapp_business_account", "entry": [None, {}, {"changes": [None]}]}]:
        assert parse_webhook(p) == {"replies": [], "texts": [], "statuses": [], "others": []}
    assert verify_signature(b"x", None, SECRET) is False and verify_signature(b"x", "sha256=zz", SECRET) is False


def test_cloud_send_uses_graph_v26_and_falls_back_to_template(world):
    hab, hotel, relay, client, m = world
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers["authorization"], body))
        if body["type"] == "interactive":
            return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]})

    cfg = WhatsAppConfig(access_token="TOKEN", phone_number_id="PN1", template_name="hold_question")
    cloud = WhatsAppRelay(cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    req = m.open_request()
    r = cloud.send_request(mission_id=m.id, mission_title="Lyon", request=req, authority=hab.profile.authority)
    assert r.ok and r.via == "template" and r.mode == "cloud"
    assert calls[0][0] == "https://graph.facebook.com/v26.0/PN1/messages" and calls[0][1] == "Bearer TOKEN"
    payloads = [c["parameters"][0]["payload"] for c in calls[1][2]["template"]["components"] if c["type"] == "button"]
    assert [parse_button_id(p)[2] for p in payloads] == ["EXTEND_CHECKS", "CLOSE_UNCONFIRMED", "REVOKE"]


def test_studio_api_is_local_only_and_validates_profiles(world):
    hab, hotel, relay, client, m = world
    assert client.get("/api/missions").status_code == 200
    assert client.get("/api/missions", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    detail = client.get(f"/api/missions/{m.id}").json()
    assert detail["verification"]["chain"]["valid"] and detail["verification"]["state"]["matches"] and detail["verification"]["decisions"]["matches"]
    cat = client.get("/api/catalog").json()
    ids = [x["id"] for x in cat["templates"]]
    assert {"reservation-voyage", "remboursement-client", "achat-fournisseur"} <= set(ids) and "properties" in cat["schema"]
    good = next(x for x in cat["templates"] if x["id"] == "reservation-voyage")["profile"]
    assert client.post("/api/profiles/validate", json=good).json()["fingerprint"] == hab.profile.fingerprint
    bad = good | {"hesitation": good["hesitation"] | {"ladder": ["VERIFY"]}}
    assert not client.post("/api/profiles/validate", json=bad).json()["valid"]
    tok = TestClient(create_app(hab.store, [hab], whatsapp=WhatsAppConfig(), studio_token="t0k"))
    assert tok.get("/api/missions", headers={"x-forwarded-for": "1.2.3.4", "authorization": "Bearer t0k"}).status_code == 200


def test_webhook_rejects_oversized_payload_before_processing(world):
    _, _, _, client, _ = world
    body = b"x" * (256 * 1024 + 1)
    response = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={"content-type": "application/json", "content-length": str(len(body))},
    )
    assert response.status_code == 413
