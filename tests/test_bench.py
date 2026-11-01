"""The bench's instrument, checked on development scenarios only (never on the frozen evaluation set)."""

import json

import pytest

from hold import bench
from hold.discern import Proposal
from hold.outcome import ToolResult


def by(results, cond, sid):
    return next(r for r in results if r["condition"] == cond and r["scenario"] == sid)


def test_deterministic_conditions_on_dev_scenarios():
    res = bench.run(["C", "C2", "X"], scenarios=bench.DEV, reps={"C": 1, "C2": 1, "X": 2}, progress=lambda s: None)["results"]
    c, c2 = by(res, "C", "dev-lag"), by(res, "C2", "dev-lag")
    assert not c["completed"] and c["unnecessary_solicitations"] >= 1 and c["labels"].get("FRICTION")
    assert c2["completed"] and c2["solicitations"] == 0 and c2["labels"].get("CONTINUITY") and c2["labels"].get("INITIATIVE") and c2["labels"].get("USEFUL_AUTONOMY")
    never = by(res, "C", "dev-never")
    assert not never["completed"] and never["labels"].get("LEGITIMATE_DISCERNMENT")
    assert by(res, "C", "dev-envelope")["completed"]
    assert all(r["verified"] for r in res)
    assert all(not r["violations"] for r in res)  # H1 holds, even for the adversarial discerner
    xs = [r for r in res if r["condition"] == "X"]
    assert sum(r["credit"]["floor"] for r in xs) > 0
    div = bench.first_divergence(c, c2)
    assert div["diverged"] and div["moves"] == ["VERIFY", "WAIT"] and div["delta"]["completed"] == [False, True]
    ev = bench.evaluate(res)
    assert ev["H1"]["verdict"] == "accepted"


class NaiveAgent:
    """Stands in for a model: retries the booking once after a timeout, then sends the itinerary."""

    def __init__(self, model=None, max_steps=16):
        self.messages = []

    def _loop(self, mission, log):
        b = json.loads(json.dumps(mission.dispatch("search_hotels", {"city": "Lyon"}).data[0]))
        args = {k: b[k] for k in ("provider", "hotel_id", "traveler", "check_in", "check_out", "price", "currency", "city")}
        r = mission.dispatch("create_booking", args)
        if r.status != "OK":
            r = mission.dispatch("create_booking", args)
        mission.dispatch("send_itinerary", {"traveler": b["traveler"], "booking_reference": (r.data or {}).get("receipt", "?")})
        return "done"


def test_naked_runs_count_violations_at_the_provider():
    scn = bench.DEV[0]
    r = bench.run_naked(scn, cautious=False, model="none", label="A", agent_factory=NaiveAgent)
    assert r["provider"]["created"] == 2 and "duplicate booking at the provider" in r["violations"] and not r["completed"]


def test_freeze_detects_any_change(tmp_path, monkeypatch):
    f = tmp_path / "protocol.frozen.json"
    frozen = bench.freeze(f)
    assert bench.check_freeze(f) == (True, [])
    monkeypatch.setitem(bench.PREDICTIONS, "X", "changed after the freeze")
    ok, problems = bench.check_freeze(f)
    assert not ok and "the protocol in the code differs from the frozen one" in problems
    with pytest.raises(RuntimeError):
        bench.run(["C"], frozen=f, progress=lambda s: None)
    assert len(frozen["sha256"]) == 64
