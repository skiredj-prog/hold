"""E1-A4: tool contracts and non-overridable structural checks after incident review."""
import pytest
from hold.bench import EVAL, run_hold, score_hold
from hold.discern import ReferenceDiscerner
from hold.runtime import Habilitation
from hold.testing import BOOKING, SyntheticHotel, wire


class OneAttemptAgent:
    name = "structural-probe"

    def __init__(self, price):
        self.price = price

    def run(self, mission, booking):
        mission.call("create_booking", **(booking | {"price": self.price}))

    def resume(self, mission, booking):
        pass


def test_model_tools_advertise_actual_numeric_price():
    hab = Habilitation("hold/profiles/reservation.hold.yaml", store=":memory:", background=False)
    wire(hab, SyntheticHotel())
    function = next(t["function"] for t in hab.mission(hab.open_mission("test").id).tools() if t["function"]["name"] == "create_booking")
    assert function["parameters"]["properties"]["price"]["type"] == "number"


@pytest.mark.parametrize("raw", ["(insert price here)", "EUR 238", "NaN", "Infinity", "", None, True, -10])
def test_invalid_amount_cannot_be_approved_or_dispatched(raw):
    run = run_hold(EVAL[0], ReferenceDiscerner(), OneAttemptAgent(raw), label="C-llama/a4-test")
    events = run["journal"]["events"]
    assert run["provider"]["created"] == 0
    assert run["journal"]["mission"]["status"] != "CLOSED_COMPLETED"
    assert any(e["type"] == "ACTION_REFUSED" and e["data"]["rule"] == "I4/INVALID_ARGUMENTS" for e in events)
    assert not any(e["type"] in ("DECISION_REQUEST", "AUTHORITY_GRANT", "EFFECT_RESERVED") for e in events)


def test_known_out_of_envelope_numeric_amount_preserves_one_time_approval():
    run = run_hold(EVAL[10], ReferenceDiscerner(), OneAttemptAgent(340), label="C-llama/a4-test")
    events = run["journal"]["events"]
    assert any(e["type"] == "DECISION_REQUEST" and e["data"]["kind"] == "ENVELOPE_BREACH" for e in events)
    assert any(e["type"] == "AUTHORITY_GRANT" for e in events)
    assert run["provider"]["created"] == 1


def test_normal_number_still_allowed_without_human_approval():
    run = run_hold(EVAL[0], ReferenceDiscerner(), OneAttemptAgent(238), label="C-llama/a4-test")
    events = run["journal"]["events"]
    assert any(e["type"] == "EFFECT_RESERVED" for e in events)
    assert not any(e["type"] == "AUTHORITY_GRANT" for e in events)
    assert not score_hold(run, EVAL[0])["violations"]
