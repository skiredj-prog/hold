"""What HOLD can claim about an external effect, stated as three separate guarantees.

1. At-most-once dispatch inside the HOLD boundary (threads, and processes sharing one store).
2. Provider-side duplicate prevention only where the provider honors the operation_id, labelled
   DECLARED or VERIFIED, never assumed.
3. An explicit UNKNOWN when the outcome cannot be established."""

import json
import subprocess
import sys
import textwrap
import time

from hold.runtime import GUARANTEES, Habilitation
from hold.testing import BOOKING, LedgerHotel, SyntheticHotel, wire

from conftest import PROFILE, ROOT

CHILD = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, {root!r})
    from hold.runtime import Habilitation
    from hold.testing import BOOKING, LedgerHotel, wire
    hab = Habilitation({profile!r}, store={db!r}, background=False)
    wire(hab, LedgerHotel({ledger!r}))
    m = hab.mission({mission!r})
    while time.time() < {start}:
        time.sleep(0.001)
    print(json.dumps(m.call("create_booking", **BOOKING).status))
""")


def test_two_processes_sharing_one_store_dispatch_once(tmp_path):
    db, ledger = str(tmp_path / "hold.db"), str(tmp_path / "provider.jsonl")
    hab = Habilitation(PROFILE, store=db, background=False)
    m = hab.open_mission("Lyon, two processes")
    start = time.time() + 1.5
    code = CHILD.format(root=str(ROOT), profile=str(PROFILE), db=db, ledger=ledger, mission=m.id, start=start)
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        results.append(json.loads(out.strip().splitlines()[-1]))
    assert LedgerHotel(ledger).created == 1  # counted at the provider, not by HOLD
    assert results.count("OK") == 1 and set(results) <= {"OK", "ALREADY_CONFIRMED"}
    assert m.verify().valid
    reserved = [e for e in m.events() if e.type == "EFFECT_RESERVED"]
    assert len(reserved) == 1


def test_separate_stores_are_outside_the_boundary(tmp_path):
    """Stated, not hidden: two runtimes with separate stores do not coordinate."""
    hotel = SyntheticHotel(create_mode="respond")
    a = Habilitation(PROFILE, store=str(tmp_path / "a.db"), background=False)
    b = Habilitation(PROFILE, store=str(tmp_path / "b.db"), background=False)
    wire(a, hotel)
    wire(b, hotel)
    a.open_mission("Lyon").call("create_booking", **BOOKING)
    b.open_mission("Lyon").call("create_booking", **BOOKING)
    assert hotel.created == 2
    assert "Not across separate stores" in GUARANTEES["dispatch"]


def test_provider_side_deduplication_is_labelled_by_what_was_verified(booking):
    plain = Habilitation(PROFILE, store=":memory:", background=False)
    wire(plain, SyntheticHotel(create_mode="respond"))
    m = plain.open_mission("Lyon")
    m.call("create_booking", **booking)
    g = m.effects()[0]["guarantees"]
    assert g == {"dispatch": "AT_MOST_ONCE_IN_BOUNDARY", "dispatches": 1, "provider": "NOT_DECLARED", "outcome": "CONFIRMED_CORRELATED"}

    hotel = SyntheticHotel(create_mode="respond", idempotent=True)
    first = hotel.create("op_1", **booking)
    again = hotel.create("op_1", **booking)  # a transport-level retry of the same operation
    assert hotel.created == 1 and first.receipt == again.receipt
    idem = Habilitation(PROFILE, store=":memory:", background=False)
    wire(idem, hotel)
    m2 = idem.open_mission("Lyon")
    m2.call("create_booking", **booking)
    assert m2.effects()[0]["guarantees"]["provider"] == "VERIFIED"
    bound = [e.data for e in m2.events() if e.type == "RUNTIME_BOUND"][0]
    tool = next(t for t in bound["tools"] if t["action"] == "create_booking")
    assert tool["idempotency"] == "operation_id" and tool["verified_by"].endswith("what_was_verified")


def test_unknown_stays_unknown(make, booking):
    hab, hotel, _ = make(visible_after=None, relay=False)
    m = hab.open_mission("Lyon")
    assert m.call("create_booking", **booking).status == "HELD"
    assert m.effects()[0]["guarantees"]["outcome"] == "UNKNOWN"
    assert m.close()["status"] == "CLOSED_UNCONFIRMED"


def test_runtime_binding_is_journaled_not_in_the_profile(make):
    hab, _, _ = make()
    m = hab.open_mission("Lyon", agent={"name": "scripted", "model": None})
    bound = [e.data for e in m.events() if e.type == "RUNTIME_BOUND"][0]
    assert bound["discerner"]["name"] == "reference" and bound["agent"]["name"] == "scripted"
    assert {t["action"] for t in bound["tools"]} >= {"create_booking", "check_booking"}
    assert "discerner" not in hab.profile.model_dump() and "tools" not in hab.profile.model_dump()
