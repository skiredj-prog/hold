"""The gate checks the reading, the move, and that one follows from the other. Inside what the facts
justify, the choice belongs to the discerner and is credited to it; outside, its error is handed back,
then the floor applies the reference and takes the credit. Discernment is not conformity to one move."""

from datetime import datetime, timedelta, timezone

from hold.discern import Proposal, ReferenceDiscerner
from hold.hesitation import true_map
from hold.relay.base import SimulatedRelay
from hold.replay import decision_replay, state_replay
from hold.runtime import Habilitation
from hold.testing import SyntheticHotel, wire

from conftest import PROFILE

LAG_NOTE = "No booking found for this operation. New bookings can take up to 10 minutes to appear in status."


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def setup(discerner, *, relay=True, **hotel_kw):
    clock = Clock()
    hotel = SyntheticHotel(clock=clock, **hotel_kw)
    hab = Habilitation(PROFILE, store=":memory:", relay=SimulatedRelay() if relay else None, discerner=discerner, background=False, clock=clock)
    wire(hab, hotel)
    return hab, hotel, clock


def decisions(m):
    return [e.data for e in m.events() if e.type == "DECISION"]


def sound(hab, m):
    assert m.verify().valid
    assert state_replay(hab.store, m.id, hab.profile)["matches"]
    dr = decision_replay(m.events(), hab.profile)
    assert dr["matches"], dr["mismatches"]


class Fixed:
    """Reads the facts truly, then makes a fixed move."""

    def __init__(self, move):
        self.move, self.name = move, "fixed"

    def propose(self, position, context):
        return Proposal(self.name, self.move, true_map(position), f"fixed {self.move}", ["P:" + self.move])


class Reader:
    """Reads the facts truly and also reads the free text of the evidence: waits when an observation
    says the record may take time to appear; otherwise takes the first justified path."""

    name = "reader"

    def __init__(self, quote=None):
        self.quote = quote

    def propose(self, position, context):
        m = true_map(position)
        obs = position["observations"]
        for o in obs:
            waited_since = any(x["kind"] == "WAITED" and x["seq"] > o["seq"] for x in obs)
            if "take up to" in (o.get("text") or "") and "WAIT" in m["open_paths"] and not waited_since:
                m["pending"] = {"condition": "VISIBILITY_DELAY", "evidence": o["id"], "quote": self.quote or "can take up to 10 minutes to appear", "wait_seconds": 600}
                return Proposal(self.name, "WAIT", m, "The status service says new bookings can take 10 minutes to appear.", [o["id"], "P:WAIT"])
        return Proposal(self.name, position["reference"], m, "first justified path", ["P:" + position["reference"]])


def test_a_justified_move_other_than_the_reference_is_the_discerners_and_credited_to_it(booking):
    hab, hotel, _ = setup(Fixed("CONTACT_SUPPORT"), visible_after=None, support_mode="finds")
    m = hab.open_mission("Lyon")
    assert m.call("create_booking", **booking).status == "OK"
    d = decisions(m)[0]
    assert d["outcome"] == "ADMITTED" and d["verdict"]["credit"] == "discerner"
    assert d["verdict"]["applied"] == "CONTACT_SUPPORT" != d["verdict"]["reference"] == "VERIFY"
    assert d["verdict"]["justified"] == ["VERIFY", "CONTACT_SUPPORT"]
    assert hotel.created == 1
    sound(hab, m)


def test_a_true_reading_with_a_move_it_does_not_justify_is_incoherent(booking):
    hab, hotel, _ = setup(Fixed("ASK_AUTHORITY"), visible_after=2)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    first = decisions(m)[0]
    assert first["outcome"] == "INCOHERENT" and first["verdict"]["map_true"] and first["verdict"]["coherent"] is False
    assert decisions(m)[1]["outcome"] == "REFERENCE_APPLIED" and decisions(m)[1]["verdict"]["credit"] == "floor"
    assert hab.relay.outbox == []  # the authority was not disturbed by an unjustified request
    sound(hab, m)


def test_a_true_reading_with_a_closed_move_is_refused(booking):
    hab, hotel, _ = setup(Fixed("ASK_AUTHORITY"), relay=False, visible_after=2)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    assert decisions(m)[0]["outcome"] == "MOVE_REFUSED" and decisions(m)[0]["verdict"]["rule"] == "I4/PATH"
    sound(hab, m)


def test_reading_the_evidence_spares_the_authority_where_the_cascade_cannot(booking):
    """Same facts, same floor. The cascade cannot read free text: it burns its checks and the support,
    then disturbs the authority. A discerner that reads the note waits, and the booking appears."""
    # the cascade
    hab, hotel, clock = setup(ReferenceDiscerner(), visible_after_s=600, status_note=LAG_NOTE)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    assert [d["verdict"]["applied"] for d in decisions(m)] == ["VERIFY", "VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY"]
    assert m.status == "AWAITING_AUTHORITY" and len(hab.relay.outbox) == 1

    # the reader
    hab, hotel, clock = setup(Reader(), visible_after_s=600, status_note=LAG_NOTE)
    m = hab.open_mission("Lyon")
    assert m.call("create_booking", **booking).status == "HELD"
    applied = [d["verdict"]["applied"] for d in decisions(m)]
    assert applied == ["VERIFY", "WAIT"] and m.status == "HELD"
    wait = decisions(m)[1]["verdict"]
    assert wait["credit"] == "discerner" and wait["wait_seconds"] == 600 and wait["reference"] == "VERIFY"
    clock.advance(600)
    m.sweep()  # the wait is over: the position is read again, the booking has appeared
    assert m.status == "OPEN" and hotel.created == 1
    assert [d["verdict"]["applied"] for d in decisions(m)] == ["VERIFY", "WAIT", "VERIFY"]
    assert hab.relay.outbox == []
    phases = {e.phase for e in m.events() if e.status == "HELD"}
    assert {"CAPTER", "DISCERNER", "PERCEVOIR", "CHOISIR", "ENGAGER"} <= phases  # on hold, and still active
    sound(hab, m)


def test_a_wait_must_quote_the_evidence(booking):
    hab, hotel, clock = setup(Reader(quote="the hotel promised to call back tomorrow"), visible_after_s=600, status_note=LAG_NOTE)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    refused = [d for d in decisions(m) if d["outcome"] == "MAP_REFUSED"]
    assert refused and "does not contain the quoted passage" in refused[0]["verdict"]["reason"]
    assert not any(d["verdict"]["applied"] == "WAIT" for d in decisions(m))
    sound(hab, m)


def test_patience_is_bounded(booking):
    hab, hotel, clock = setup(Reader(), visible_after_s=None, visible_after=None, status_note=LAG_NOTE)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    for _ in range(4):
        clock.advance(900)
        m.sweep()
    waits = [d for d in decisions(m) if d["verdict"]["applied"] == "WAIT"]
    assert sum(d["verdict"]["wait_seconds"] for d in waits) <= hab.profile.hesitation.max_wait_seconds
    assert len(waits) <= hab.profile.hesitation.max_waits
    assert hotel.created == 1
    sound(hab, m)
