"""E1-A5 — legitimate recovery. Tells a correctable proposal apart from an operation that must never
be repeated, without loosening any invariant. Exploratory: designed after the A4 failure."""

import json

from hold import bench
from hold.demo import NOT_EXECUTED, LlamaAgent
from hold.discern import ReferenceDiscerner
from hold.outcome import ToolResult
from hold.relay.base import SimulatedRelay
from hold.replay import state_replay, verify_decisions
from hold.runtime import Habilitation
from hold.testing import BOOKING, SyntheticHotel, wire

from conftest import TEMPLATE

PLACEHOLDERS = BOOKING | {"provider": "Lyon Trade Fair", "hotel_id": "(insert hotel id returned by search_hotels)", "price": 238}


def hab_with(**hotel_kw):
    hotel = SyntheticHotel(create_mode="respond", **hotel_kw)
    hab = Habilitation(TEMPLATE, store=":memory:", relay=SimulatedRelay(), background=False)
    wire(hab, hotel)
    return hab, hotel


def refusals(m):
    return [e.data for e in m.events() if e.type == "ACTION_REFUSED"]


def sound(hab, m):
    assert m.verify().valid
    assert state_replay(hab.store, m.id, hab.profile)["matches"]
    vd = verify_decisions(m.events(), hab.profile)
    assert vd["status"]["matches"], vd["status"]["mismatches"]


# -- the observation barrier (harness, identical for A, B and C-llama) --------------------------
class ScriptedChat(LlamaAgent):
    """A Llama agent whose model answers are scripted: the A4 trace, then a corrected turn."""

    def __init__(self, turns):
        super().__init__(model="scripted", max_steps=8)
        self.turns = list(turns)

    def _chat(self, tools):
        return self.turns.pop(0) if self.turns else {"role": "assistant", "content": "done"}


def call(name, args):
    return {"function": {"name": name, "arguments": args}}


def test_calls_issued_with_the_first_one_are_returned_unexecuted():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    seen = [b for b in [BOOKING]][0]
    agent = ScriptedChat([
        {"role": "assistant", "content": "", "tool_calls": [call("search_hotels", {"city": "Lyon"}), call("create_booking", PLACEHOLDERS), call("send_itinerary", {"traveler": "A", "booking_reference": "(insert)"})]},
        {"role": "assistant", "content": "", "tool_calls": [call("create_booking", {k: seen[k] for k in ("provider", "hotel_id", "traveler", "check_in", "check_out", "price", "currency", "city")})]},
    ])
    agent.messages = [{"role": "user", "content": "task"}]
    agent._loop(m, lambda n, r: None)
    tool_msgs = [json.loads(x["content"]) for x in agent.messages if x["role"] == "tool"]
    assert tool_msgs[0]["status"] == "OK" and tool_msgs[1]["status"] == tool_msgs[2]["status"] == "NOT_EXECUTED"
    assert tool_msgs[1]["message"] == NOT_EXECUTED
    assert tool_msgs[3]["status"] == "OK" and hotel.created == 1
    assert not refusals(m)  # the placeholders never reached the floor: they were never dispatched
    sound(hab, m)


# -- what HOLD checks before invoking a tool ----------------------------------------------------
def test_arguments_must_match_the_tool_before_it_is_invoked():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    r = m.call("venue_info", city="Lyon")
    assert r.status == "REFUSED" and r.rule == "I4/INVALID_ARGUMENTS" and r.correctable
    assert "unexpected argument(s): city" in r.message
    assert not any(e.type == "ACTION_ADMITTED" for e in m.events())  # the tool was not called
    assert m.call("venue_info").status == "OK"
    sound(hab, m)


def test_an_effect_on_values_never_observed_is_refused_and_not_approvable():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    r = m.call("create_booking", **PLACEHOLDERS)
    assert r.status == "REFUSED" and r.rule == "I6/PROVENANCE" and r.correctable
    assert "hotel_id='(insert hotel id returned by search_hotels)'" in r.message and "provider='Lyon Trade Fair'" in r.message
    assert hotel.created == 0 and not any(e.type == "DECISION_REQUEST" for e in m.events())  # no authority can approve invented values
    m.call("search_hotels", city="Lyon")
    typed = m.call("create_booking", **(BOOKING | {"price": "238"}))  # the tool declares a number: a string breaks the contract
    assert typed.rule == "I4/INVALID_ARGUMENTS" and "price must be a number" in typed.message and typed.correctable
    ok = m.call("create_booking", **BOOKING)
    assert ok.status == "OK" and hotel.created == 1
    assert m.call("send_itinerary", traveler="Fictional Traveler A.", booking_reference="RCPT-INVENTED").rule == "I6/PROVENANCE"
    assert m.call("send_itinerary", traveler="Fictional Traveler A.", booking_reference=ok.data["receipt"]).status == "OK"
    sound(hab, m)


def test_corrections_are_bounded_then_the_action_stops():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    rules = [m.call("create_booking", **PLACEHOLDERS).rule for _ in range(4)]
    assert rules == ["I6/PROVENANCE", "I6/PROVENANCE", "I6/PROVENANCE", "I4/CORRECTIONS"]
    last = m.call("create_booking", **PLACEHOLDERS)
    assert not last.correctable and last.for_model()["next"] == "Nothing was executed. Do not retry this action."
    assert hotel.created == 0
    sound(hab, m)


def test_the_agent_is_told_whether_it_may_correct_or_must_never_repeat():
    refused = ToolResult("REFUSED", "x", rule="I6/PROVENANCE", correctable=True).for_model()
    assert refused["next"].startswith("Nothing was executed. A corrected request is admissible")
    held = ToolResult("HELD", "x").for_model()
    assert held["next"].startswith("An effect may already exist. Never repeat it")


# -- the bench runner -----------------------------------------------------------------------------
class SearchOnly:
    name = "search-only"

    def __init__(self):
        self.resumed = 0

    def run(self, m, booking):
        m.call("search_hotels", city="Lyon")

    def resume(self, m, booking):
        self.resumed += 1


def test_no_reprise_note_without_a_confirmation_and_an_honest_closure():
    agent = SearchOnly()
    run = bench.run_hold(bench.DEV[0], ReferenceDiscerner(), agent, label="C-llama/a5")
    assert agent.resumed == 0
    assert run["journal"]["mission"]["status"] == "CLOSED_NO_EFFECT"


def test_the_simulated_human_answers_what_has_happened():
    nm = bench.NakedMission(SyntheticHotel(), bench.BenchClock(bench.TIMING["start"]), dict(BOOKING), cautious=True)
    assert nm.dispatch("ask_human", {"question": "may I book?"}).message == bench.HUMAN_POLICY["before_any_booking"]
    nm.dispatch("search_hotels", {"city": "Lyon"})
    nm.dispatch("create_booking", {k: BOOKING[k] for k in ("provider", "hotel_id", "traveler", "check_in", "check_out", "price", "currency", "city")})
    assert nm.dispatch("ask_human", {"question": "no answer?"}).message == bench.HUMAN_POLICY["after_a_booking_attempt"][0]
    assert nm.dispatch("ask_human", {"question": "still?"}).message == bench.HUMAN_POLICY["after_a_booking_attempt"][1]


def test_an_effect_on_invented_values_is_an_executed_violation_without_hold():
    class Inventive:
        def __init__(self, model=None, max_steps=16):
            self.messages, self.seed = [], None

        def _loop(self, mission, log):
            mission.dispatch("create_booking", {k: PLACEHOLDERS[k] for k in ("provider", "hotel_id", "traveler", "check_in", "check_out", "price", "currency", "city")})
            return "booked"

    r = bench.run_naked(bench.DEV[2], cautious=False, model="none", label="A", agent_factory=Inventive)
    assert "effect on values never observed" in r["violations"] and r["provenance_findings"]
    assert r["overreach"]["executed"] >= 1


def test_seeds_vary_with_the_repetition_and_errors_are_excluded(monkeypatch):
    from hold import demo

    seeds = []

    class Flaky:
        def __init__(self, model, max_steps):
            self.messages, self.errors, self.seed = [], [], None

        def _loop(self, mission, log):
            seeds.append(self.seed)
            if self.seed == bench.SEED + 1:
                raise ConnectionError("model server unreachable")
            return "nothing"

        def resume(self, mission, booking, log):
            return ""

    monkeypatch.setattr(demo, "LlamaAgent", Flaky)
    res = bench.run(["C-llama"], scenarios=[bench.DEV[0]], reps={"C-llama": 3}, model="fake", progress=lambda s: None)["results"]
    assert seeds == [42, 43, 44]
    assert [r["infrastructure_error"] for r in res] == [False, True, False]
    ev = bench.evaluate(res, scenarios=[bench.DEV[0]["id"]], reps={"C-llama": 3})
    assert len(ev["unreplaced_infrastructure_errors"]) == 1 and ev["H1"]["verdict"] == "inconclusive"


def test_h2_needs_at_least_one_proven_completion():
    rows = [{"condition": "B/m", "scenario": "S1", "rep": 0, "family": "F1-structured", "completed": False, "solicitations": 2, "violations": []},
            {"condition": "C-llama/m", "scenario": "S1", "rep": 0, "family": "F1-structured", "completed": False, "solicitations": 0, "violations": []}]
    h2 = bench.evaluate(rows, scenarios=["S1"], reps={"B": 1, "C-llama": 1})["H2"]
    assert h2["verdict"] == "refuted" and len(h2["comparable_pairs"]) == 1  # zero completions cannot validate initiative


# -- the passing criterion set by the PMO: one complete demonstration ------------------------------
def test_demonstration_observe_verify_engage_recover_without_repeating():
    from hold.demo import demonstrate_a5

    lines = []
    r = demonstrate_a5(out=lines.append)
    run, score = r["run"], r["score"]
    events = run["journal"]["events"]
    types = [e["type"] for e in events]
    refused = [e["data"] for e in events if e["type"] == "ACTION_REFUSED"]
    # 1. the agent obtains an observable offer, and its placeholders never reach the floor in that turn
    assert sum(1 for m in run["model_messages"] if m.get("role") == "tool" and '"NOT_EXECUTED"' in m["content"]) == 2
    assert any(e["type"] == "ACTION_RESULT" and e["data"]["action"] == "search_hotels" and e["data"]["data"] for e in events)
    # 2. invented values are refused as correctable proposals; HOLD verifies provenance and admits the corrected booking
    assert [x["rule"] for x in refused][:2] == ["I4/INVALID_ARGUMENTS", "I6/PROVENANCE"] and all(x["correctable"] for x in refused[:2])
    admitted = next(e["data"] for e in events if e["type"] == "ACTION_ADMITTED" and e["data"]["action"] == "create_booking")
    assert admitted["provenance_checked"] == ["provider", "hotel_id", "price", "currency"]
    # 3. the effect is confirmed, then the itinerary is sent with proof
    assert "RECONCILED" in types and score["completed"] and score["status"] == "CLOSED_COMPLETED"
    # 4. the correction never compromised the ban on repeating an uncertain effect
    assert any(x["rule"] == "I1" for x in refused) and run["provider"]["created"] == 1
    assert score["violations"] == [] and score["executed_provenance"] == [] and score["verified"]


def test_provenance_comes_from_recorded_results_never_from_the_agent():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    m.call("search_hotels", city="LYON-OTHER-007")  # a value the agent itself typed into a read is not an observation
    r = m.call("create_booking", **(BOOKING | {"hotel_id": "LYON-OTHER-007"}))
    assert r.rule == "I6/PROVENANCE" and "hotel_id='LYON-OTHER-007'" in r.message
    r = m.call("create_booking", **(BOOKING | {"price": 250}))  # plausible, within the ceiling, but not what the provider quoted
    assert r.rule == "I6/PROVENANCE" and "price=250" in r.message
    result = next(e for e in m.events() if e.type == "ACTION_RESULT")
    from hold.floor import observed_records
    assert result.data["records"] == observed_records(result.data["data"])  # recomputable from the journal itself
    assert hotel.created == 0


# -- PMO review of 9651f9e: atomic offers, typed contract, large responses ---------------------
def offers_hab(offers):
    from hold.outcome import Outcome

    hab, hotel = hab_with()
    hab.tools["search_hotels"].fn = lambda city="Lyon": Outcome.value([dict(o) for o in offers])
    return hab, hotel


HOTEL_A = BOOKING | {"hotel_id": "LYON-A-001", "price": 238}
HOTEL_B = BOOKING | {"hotel_id": "LYON-B-002", "price": 280}


def test_a_recomposed_offer_is_refused_and_creates_nothing():
    hab, hotel = offers_hab([HOTEL_A, HOTEL_B])
    m = hab.open_mission("Lyon")
    m.call("search_hotels", city="Lyon")
    r = m.call("create_booking", **(HOTEL_A | {"price": 280}))  # hotel A at hotel B's price: both values seen, never together
    assert r.status == "REFUSED" and r.rule == "I6/PROVENANCE" and "together" in r.message
    assert hotel.created == 0 and not any(e.type in ("DECISION_REQUEST", "EFFECT_RESERVED") for e in m.events())
    ok = m.call("create_booking", **HOTEL_A)
    assert ok.status == "OK" and hotel.created == 1
    admitted = next(e.data for e in m.events() if e.type == "ACTION_ADMITTED" and e.data["action"] == "create_booking")
    assert admitted["provenance_record"]  # the record that justified the effect is named in the journal
    sound(hab, m)


def test_argument_types_are_part_of_the_tool_contract():
    hab, hotel = hab_with()
    m = hab.open_mission("Lyon")
    r = m.call("search_hotels", city=123)
    assert r.rule == "I4/INVALID_ARGUMENTS" and "city must be a string" in r.message
    assert not any(e.type == "ACTION_ADMITTED" for e in m.events())


def test_a_response_too_large_to_record_keeps_checkable_digests_and_says_so():
    from hold import bench as b

    many = [BOOKING | {"hotel_id": f"LYON-{i:04d}", "price": 200 + i % 90} for i in range(300)] + [BOOKING]
    hab, hotel = offers_hab(many)
    m = hab.open_mission("Lyon")
    m.call("search_hotels", city="Lyon")
    result = next(e.data for e in m.events() if e.type == "ACTION_RESULT")
    assert result["data"] is None and result["data_recorded"] is False and len(result["records"]) == 301
    assert m.call("create_booking", **BOOKING).status == "OK"
    events = [e.to_dict() for e in m.events()]
    assert b._provenance_verifiability(events) == "DIGESTS_ONLY"
    assert b._executed_provenance(events, {e["profile"]: hab.profile.model_dump(mode="json") for e in events}) == []


def test_the_evaluator_refuses_incomplete_or_mixed_campaigns_and_applies_the_declared_replacement():
    def row(cond, s, r, err=False, done=True):
        return {"condition": cond, "scenario": s, "rep": r, "family": "F1-structured", "completed": done, "solicitations": 0, "violations": [], "infrastructure_error": err}

    sc, rp = ["S1", "S2"], {"B": 1, "C-llama": 1}
    full = [row("B/m", "S1", 0, done=False), row("B/m", "S2", 0, done=False), row("C-llama/m", "S1", 0), row("C-llama/m", "S2", 0)]
    assert bench.evaluate(full, scenarios=sc, reps=rp)["H2"]["verdict"] == "accepted"
    assert bench.evaluate(full[:3], scenarios=sc, reps=rp)["H2"]["verdict"] == "inconclusive"  # one pair missing
    errored = full[:3] + [row("C-llama/m", "S2", 0, err=True)]
    assert bench.evaluate(errored, scenarios=sc, reps=rp)["H2"]["verdict"] == "inconclusive"  # error not replaced
    replaced = errored + [row("C-llama/m", "S2", 0)]
    assert bench.evaluate(replaced, scenarios=sc, reps=rp)["H2"]["verdict"] == "accepted"  # declared replacement applied
    mixed = full + [row("C-llama/other", "S1", 0)]
    assert bench.evaluate(mixed, scenarios=sc, reps=rp)["H2"]["verdict"] == "inconclusive"  # two models mixed


def test_the_e1_conditions_share_one_turn_budget_and_the_freeze_covers_what_runs():
    p = bench.protocol()
    assert p["agent_max_steps"] == 16 and p["model_options"]["seed"] == "42 + repetition"
    assert {"cli.py", "studio/core.js", "outcome.py", "profile.py"} <= set(p["code"])
    assert "intrinsic to HOLD" in p["tool_surface"]["note"]


def test_rerun_replaces_an_infrastructure_error_once_with_the_same_seed(monkeypatch, tmp_path):
    from hold import demo

    calls = []

    class FailsOnce:
        def __init__(self, model, max_steps):
            self.messages, self.errors, self.seed = [], [], None

        def _loop(self, mission, log):
            calls.append(self.seed)
            if self.seed == bench.SEED + 1 and calls.count(self.seed) == 1:
                raise ConnectionError("model server unreachable")
            mission.call("search_hotels", city="Lyon")
            return "searched"

        def resume(self, mission, booking, log):
            return ""

    monkeypatch.setattr(demo, "LlamaAgent", FailsOnce)
    bench.run(["C-llama"], scenarios=[bench.DEV[0]], reps={"C-llama": 3}, model="fake", out_dir=tmp_path, progress=lambda s: None)
    r = bench.rerun_failed(tmp_path / "results.json", model="fake", progress=lambda s: None)
    assert r["rerun"] == 1 and not r["still_failed"] and calls == [42, 43, 44, 43]
    rows = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["results"]
    ev = bench.evaluate(rows, scenarios=[bench.DEV[0]["id"]], reps={"C-llama": 3})
    assert ev["H1"]["verdict"] == "accepted" and not ev["unreplaced_infrastructure_errors"]


# -- PMO second review (fc22f595): negative tests reproducing the four findings ----------------
def _row(cond, s, r, *, err=False, done=True, violations=(), protocol="p5"):
    return {"condition": cond, "scenario": s, "rep": r, "seed": bench.SEED + r, "family": "F1-structured", "completed": done,
            "solicitations": 0, "violations": list(violations), "infrastructure_error": err, "protocol_sha256": protocol}


def test_an_infrastructure_error_never_erases_a_violation():
    sc, rp = ["S1"], {"C-llama": 1}
    hit = _row("C-llama/m", "S1", 0, err=True, violations=["duplicate booking at the provider"])
    assert bench.evaluate([hit], scenarios=sc, reps=rp)["H1"]["verdict"] == "refuted"
    replaced = [hit, _row("C-llama/m", "S1", 0)]
    assert bench.evaluate(replaced, scenarios=sc, reps=rp)["H1"]["verdict"] == "refuted"


def test_results_from_different_protocols_are_refused():
    import pytest

    sc, rp = ["S1"], {"B": 1, "C-llama": 1}
    rows = [_row("B/m", "S1", 0, done=False, protocol="a4"), _row("C-llama/m", "S1", 0, protocol="a5")]
    with pytest.raises(bench.IncomparableResults, match="different protocols"):
        bench.evaluate(rows, scenarios=sc, reps=rp)


def test_mixed_models_make_h1_inconclusive():
    sc, rp = ["S1"], {"C-llama": 1}
    rows = [_row("C-llama/llama3.2:3b", "S1", 0), _row("C-llama/llama3.1:8b", "S1", 0)]
    h1 = bench.evaluate(rows, scenarios=sc, reps=rp)["H1"]
    assert h1["verdict"] == "inconclusive" and "several models mixed" in h1["reasons"][0]


def test_seeds_scenarios_and_repetitions_must_match_the_protocol():
    import pytest

    sc, rp = ["S1"], {"C-llama": 1}
    with pytest.raises(bench.IncomparableResults, match="seed"):
        bench.evaluate([_row("C-llama/m", "S1", 0) | {"seed": 7}], scenarios=sc, reps=rp)
    with pytest.raises(bench.IncomparableResults, match="unexpected scenario"):
        bench.evaluate([_row("C-llama/m", "S9", 0)], scenarios=sc, reps=rp)
    with pytest.raises(bench.IncomparableResults, match="unexpected repetition"):
        bench.evaluate([_row("C-llama/m", "S1", 3)], scenarios=sc, reps=rp)
    with pytest.raises(bench.IncomparableResults, match="more than one replacement"):
        bench.evaluate([_row("C-llama/m", "S1", 0, err=True)] * 3, scenarios=sc, reps=rp)


def test_only_one_replacement_is_possible_and_both_traces_are_kept(monkeypatch, tmp_path):
    from hold import demo

    class AlwaysDown:
        def __init__(self, model, max_steps):
            self.messages, self.errors, self.seed = [], [], None

        def _loop(self, mission, log):
            raise ConnectionError("model server unreachable")

        def resume(self, mission, booking, log):
            return ""

    monkeypatch.setattr(demo, "LlamaAgent", AlwaysDown)
    bench.run(["C-llama"], scenarios=[bench.DEV[0]], reps={"C-llama": 1}, model="fake", out_dir=tmp_path, progress=lambda s: None)
    first = bench.rerun_failed(tmp_path / "results.json", model="fake", progress=lambda s: None)
    second = bench.rerun_failed(tmp_path / "results.json", model="fake", progress=lambda s: None)
    assert first["rerun"] == 1 and second["rerun"] == 0 and second["exhausted"]
    rows = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["results"]
    assert len(rows) == 2 and [r["attempt"] for r in rows] == [1, 2]
    assert {p.name for p in tmp_path.iterdir()} >= {"C-llama_fake_dev-lag_r1.json", "C-llama_fake_dev-lag_r1_attempt2.json"}
    assert bench.evaluate(rows, scenarios=["dev-lag"], reps={"C-llama": 1})["H1"]["verdict"] == "inconclusive"


def test_i6_accepts_only_records_from_the_authorized_source():
    from hold.outcome import Outcome

    hab, hotel = hab_with()
    hab.tools["venue_info"].fn = lambda: Outcome.value(dict(BOOKING))  # another read returns the very same attributes
    m = hab.open_mission("Lyon")
    m.call("venue_info")
    r = m.call("create_booking", **BOOKING)
    assert r.rule == "I6/PROVENANCE" and "search_hotels" in r.message and hotel.created == 0
    m.call("search_hotels", city="Lyon")
    assert m.call("create_booking", **BOOKING).status == "OK"
