"""L'agent Llama et le discerneur Llama contre un faux serveur Ollama (le vrai modèle tourne chez l'utilisateur)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hold import demo


def fake_reply(req: dict) -> dict:
    if "format" in req:  # discerneur : imite un modèle discipliné (lit les champs ouverts/σ)
        facts = json.loads(req["messages"][1]["content"].split("\n", 1)[1])
        paths = facts["paths"]
        directed = [p["name"] for p in paths if p["open"] and p.get("directed")]
        reaching = [p["name"] for p in paths if p["open"] and p["name"] != "PRESERVE" and p["sigma"] >= facts["tau_star"]]
        choice = (directed or reaching or ["PRESERVE"])[0]
        content = json.dumps({"perceived_state": facts["effect_state"], "paths_reaching_tau": reaching, "choice": choice, "rationale": "open and sigma >= tau"})
        return {"message": {"role": "assistant", "content": content}}
    msgs = req["messages"]
    tools = [m for m in msgs if m["role"] == "tool"]
    last = json.loads(tools[-1]["content"]) if tools else None
    names = [m.get("tool_name") for m in tools]

    def call(name, args):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}

    if "venue_info" not in names:
        return call("venue_info", {})
    if "search_hotels" not in names:
        return call("search_hotels", {"city": "Lyon"})
    if "create_booking" not in names:
        hotel = json.loads(next(m["content"] for m in tools if m["tool_name"] == "search_hotels"))["data"][0]
        return call("create_booking", {k: hotel[k] for k in ("provider", "hotel_id", "traveler", "check_in", "check_out", "price", "currency", "city")})
    if msgs[-1]["role"] == "user" and "confirmed" in msgs[-1]["content"]:
        receipt = msgs[-1]["content"].split("receipt ")[1].split(")")[0]
        return call("send_itinerary", {"traveler": "Voyageur fictif A.", "booking_reference": receipt})
    if names[-1] == "create_booking" and last["status"] == "OK":
        return call("send_itinerary", {"traveler": "Voyageur fictif A.", "booking_reference": last["data"]["receipt"]})
    return {"message": {"role": "assistant", "content": f"Report: last status {last['status']}."}}


@pytest.fixture
def fake_ollama(monkeypatch):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self._send({"models": [{"name": "llama3.2:3b", "digest": "fake"}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["content-length"])))
            self._send(fake_reply(req))

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("LLAMA_BASE_URL", url)
    monkeypatch.setenv("HOLD_DEMO_TIMEOUT", "0.05")
    yield url
    srv.shutdown()


@pytest.mark.parametrize("scenario, final", [("lost-response", "CLOSED_COMPLETED"), ("authority", "CLOSED_COMPLETED"), ("never", "CLOSED_UNCONFIRMED"), ("envelope", "CLOSED_COMPLETED")])
def test_llama_agent_wears_the_profile(fake_ollama, scenario, final):
    r = demo.run(scenario, agent="llama", discerner="llama", color=False, out=lambda s: None)
    assert r["bookings"] == 1 and r["chain_valid"] and r["status"] == final
    events = r["hab"].mission(r["mission"]).events()
    proposals = [e.data for e in events if e.type == "PROPOSAL"]
    assert all(p["proposal"]["discerner"] == "llama" and p["judgment"]["choice"] for p in proposals)
