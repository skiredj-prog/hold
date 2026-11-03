"""The WhatsApp preflight explains each failure with its fix, and never prints a secret."""

import io
import json

import httpx

from hold.whatsapp_check import main

ENV = {"WHATSAPP_ACCESS_TOKEN": "EAAG-secret-token-value", "WHATSAPP_PHONE_NUMBER_ID": "123456789012345",
       "WHATSAPP_APP_SECRET": "app-secret-value", "WHATSAPP_VERIFY_TOKEN": "hold-verify"}


def run(argv, handler, env=ENV):
    out = io.StringIO()
    code = main(argv, env=env, client=httpx.Client(transport=httpx.MockTransport(handler)), out=out)
    return code, out.getvalue()


def graph_ok(req):
    if req.url.path.endswith("/messages"):
        return httpx.Response(200, json={"messages": [{"id": "wamid.HBgLMjEyNjEyMzQ1Njc4FQIAERgS"}]})
    return httpx.Response(200, json={"display_phone_number": "+1 555 0100", "verified_name": "Test Number", "quality_rating": "GREEN"})


def test_all_good_with_server_and_ping():
    def handler(req):
        if req.url.host == "graph.facebook.com":
            assert req.headers["authorization"] == "Bearer " + ENV["WHATSAPP_ACCESS_TOKEN"]
            assert "/v26.0/" in req.url.path
            return graph_ok(req)
        if req.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "whatsapp": {"send": True, "receive": True, "mode": "cloud"}})
        if req.method == "GET" and req.url.path == "/api/whatsapp/webhook":
            q = req.url.params
            ok = q["hub.mode"] == "subscribe" and q["hub.verify_token"] == ENV["WHATSAPP_VERIFY_TOKEN"]
            return httpx.Response(200, text=q["hub.challenge"]) if ok else httpx.Response(403)
        if req.method == "POST" and req.url.path == "/api/whatsapp/webhook":
            return httpx.Response(401)
        return httpx.Response(404)

    code, out = run(["--contact", "212612345678", "--url", "https://x.trycloudflare.com/", "--ping"], handler)
    assert code == 0, out
    assert "Ready." in out and "FAIL" not in out
    assert "+1 555 0100" in out and "ping accepted" in out and "Accepted is not delivered" in out
    for secret in ENV.values():
        assert secret not in out


def test_missing_env_and_bad_numbers():
    code, out = run(["--contact", "+212612345678"], graph_ok, env={})
    assert code == 1
    assert out.count("is not set in this shell") == 4
    assert "no '+' or '00'" in out and "+212•••••5678" in out
    _, out = run(["--contact", "2120612345678"], graph_ok)
    assert "drop the 0 after 212" in out


def test_graph_errors_come_with_their_fix():
    def expired(req):
        return httpx.Response(401, json={"error": {"message": "Error validating access token: Session has expired", "code": 190}})

    code, out = run(["--contact", "212612345678", "--ping"], expired)
    assert code == 1
    assert "190 Error validating access token" in out and "temporary token" in out
    assert "SKIP  ping" in out

    def outside_window(req):
        if req.url.path.endswith("/messages"):
            return httpx.Response(400, json={"error": {"message": "Re-engagement message", "code": 131047}})
        return graph_ok(req)

    code, out = run(["--contact", "212612345678", "--ping"], outside_window)
    assert code == 1 and "131047" in out and "send any WhatsApp message to the business number" in out


def test_server_misconfigurations_are_named():
    def handler(req):
        if req.url.host == "graph.facebook.com":
            return graph_ok(req)
        if req.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "whatsapp": {"send": False, "receive": False, "mode": "simulated"}})
        if req.method == "GET":
            return httpx.Response(404)
        return httpx.Response(503)

    code, out = run(["--url", "https://x.trycloudflare.com"], handler)
    assert code == 1
    assert "relay mode 'simulated'" in out and "--relay whatsapp" in out
    assert "the server cannot receive" in out
    assert "404: the server has no WHATSAPP_VERIFY_TOKEN" in out
    assert "503: the server has no WHATSAPP_APP_SECRET" in out


def test_studio_server_without_relay_is_enough_for_webhook_setup():
    def handler(req):
        if req.url.host == "graph.facebook.com":
            return graph_ok(req)
        if req.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "whatsapp": {"send": True, "receive": True, "mode": None}})
        if req.method == "GET":
            return httpx.Response(200, text=req.url.params["hub.challenge"])
        return httpx.Response(401)

    code, out = run(["--url", "https://x.trycloudflare.com"], handler)
    assert code == 0, out
    assert "without a relay (hold studio)" in out
