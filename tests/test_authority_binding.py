"""Verified authority may override discretionary strategy; it cannot silently override hard
invariants, missing evidence or limits on its own scope. A one-time directive is bound to the exact
action and attributes, to the profile revision, to an expiry and to a single use."""

from datetime import datetime, timedelta, timezone

import pytest

from hold.profile import revise
from hold.relay.base import SimulatedRelay
from hold.replay import decision_replay, journal_profiles, state_replay, verify_decisions
from hold.runtime import Habilitation, ProfileMismatch
from hold.testing import BOOKING, SyntheticHotel, wire

from conftest import PROFILE, PRICEY, opened


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def world(*, tools=True, **hotel_kw):
    clock = Clock()
    hotel = SyntheticHotel(**hotel_kw)
    hab = Habilitation(PROFILE, store=":memory:", relay=SimulatedRelay(), background=False, clock=clock)
    if tools:
        wire(hab, hotel)
    return hab, hotel, clock


def sound(hab, m):
    assert m.verify().valid
    sr = state_replay(hab.store, m.id, hab.profile)
    assert sr["matches"], sr["differences"]
    dr = decision_replay(m.events(), journal_profiles(hab.store, m.events()))
    assert dr["matches"], dr["mismatches"]
    vd = verify_decisions(m.events(), journal_profiles(hab.store, m.events()))
    assert vd["positions"]["matches"], vd["positions"]["mismatches"]
    assert vd["decisions"]["matches"], vd["decisions"]["mismatches"]
    assert vd["status"]["matches"], vd["status"]["mismatches"]


def types(m):
    return [e.type for e in m.events()]


def test_unanswered_question_expires_and_the_hesitation_continues(booking):
    hab, hotel, clock = world(visible_after=None)
    m = opened(hab)
    m.call("create_booking", **booking)
    first = m.open_request()
    assert first["expires_at"] == "2026-11-17T10:00:00.000Z" and first["profile"] == hab.profile.fingerprint
    clock.advance(61)
    r = hab.answer(m.id, first["question_id"], "EXTEND_CHECKS", channel="whatsapp")
    assert r == {"accepted": False, "reason": "QUESTION_EXPIRED"}
    closed = [e.data for e in m.events() if e.type == "REQUEST_CLOSED"]
    assert closed[0]["reason"] == "QUESTION_EXPIRED" and closed[0]["status"] == "EXPIRED"
    # a silent authority is not an answer: the hesitation resumed and asked once more (max_requests = 2)
    second = m.open_request()
    assert second and second["question_id"] != first["question_id"]
    clock.advance(61)
    m.sweep()
    assert m.open_request() is None and m.status == "HELD" and "PRESERVED" in types(m)
    assert hotel.created == 1
    sound(hab, m)


def test_one_time_approval_expires_unused(booking):
    agent, hotel, clock = world(create_mode="respond")
    server = Habilitation(PROFILE, store=agent.store, relay=agent.relay, background=False, clock=clock)  # no tools: records only
    m = opened(agent, offers=[PRICEY])
    assert m.call("create_booking", **booking | {"price": 340}).status == "HELD"
    q = m.open_request()["question_id"]
    assert server.answer(m.id, q, "APPROVE_ONCE", channel="whatsapp")["accepted"]
    grant = [e.data["grant"] for e in m.events() if e.type == "AUTHORITY_GRANT"][0]
    assert grant["expires_at"] == "2026-11-17T09:15:00.000Z" and grant["revision"] == 1
    clock.advance(16)
    assert m.resume_pending() is None  # an expired approval is not used
    r = m.call("create_booking", **booking | {"price": 340}, _grant=grant["id"])
    assert r.status in ("REFUSED", "HELD") and hotel.created == 0
    assert any("approval expired" in e.data.get("reason", "") for e in m.events() if e.type == "ACTION_REFUSED")
    sound(agent, m)


def test_approval_is_used_once_and_never_creates_a_duplicate(booking):
    agent, hotel, clock = world(create_mode="respond")
    server = Habilitation(PROFILE, store=agent.store, relay=agent.relay, background=False, clock=clock)
    m = opened(agent, offers=[PRICEY])
    m.call("create_booking", **booking | {"price": 340})
    server.answer(m.id, m.open_request()["question_id"], "APPROVE_ONCE", channel="whatsapp")
    assert m.resume_pending().status == "OK" and hotel.created == 1
    assert m.resume_pending() is None
    gid = [e.data["grant"]["id"] for e in m.events() if e.type == "AUTHORITY_GRANT"][0]
    assert m.call("create_booking", **booking | {"price": 340}, _grant=gid).status == "ALREADY_CONFIRMED"
    assert hotel.created == 1
    sound(agent, m)


def test_approval_cannot_outlive_a_revocation(booking):
    agent, hotel, clock = world(create_mode="respond")
    server = Habilitation(PROFILE, store=agent.store, relay=agent.relay, background=False, clock=clock)
    m = opened(agent, offers=[PRICEY])
    m.call("create_booking", **booking | {"price": 340})
    server.answer(m.id, m.open_request()["question_id"], "APPROVE_ONCE", channel="whatsapp")
    m.revoke(by="Authority")
    gid = [e.data["grant"]["id"] for e in m.events() if e.type == "AUTHORITY_GRANT"][0]
    assert m.call("create_booking", **booking | {"price": 340}, _grant=gid).status == "REVOKED"
    assert m.resume_pending() is None and hotel.created == 0
    sound(agent, m)


def test_changed_conditions_void_the_question(booking):
    hab, hotel, clock = world(create_mode="respond")
    m = opened(hab, offers=[PRICEY, BOOKING])
    assert m.call("create_booking", **booking | {"price": 340}).status == "HELD"
    q = m.open_request()["question_id"]
    # meanwhile the agent books the same room at a price inside the envelope
    assert m.call("create_booking", **booking).status == "OK" and hotel.created == 1
    r = hab.answer(m.id, q, "APPROVE_ONCE", channel="whatsapp")
    assert r == {"accepted": False, "reason": "CONDITIONS_CHANGED"}
    assert hotel.created == 1 and m.status == "OPEN"
    sound(hab, m)


def test_a_new_profile_revision_voids_questions_and_must_be_adopted_explicitly(booking):
    hab, hotel, clock = world(visible_after=None)
    m = opened(hab)
    m.call("create_booking", **booking)
    q = m.open_request()["question_id"]
    rev2 = revise(hab.profile, title=hab.profile.title + " (rev. 2)")
    hab2 = Habilitation(rev2, store=hab.store, relay=hab.relay, background=False, clock=clock)
    wire(hab2, hotel)
    with pytest.raises(ProfileMismatch):
        hab2.mission(m.id)
    m2 = hab2.mission(m.id, adopt=True)
    attached = [e.data for e in m2.events() if e.type == "PROFILE_ATTACHED"]
    assert attached[-1]["supersedes"] == hab.profile.fingerprint and attached[-1]["revision"] == 2
    assert hab2.answer(m.id, q, "EXTEND_CHECKS", channel="whatsapp") == {"accepted": False, "reason": "PROFILE_CHANGED"}
    assert m2.verify().valid
    dr = decision_replay(m2.events(), journal_profiles(hab.store, m2.events()))
    assert dr["matches"] and dr["checked"] >= 4
    assert len({e.profile for e in m2.events()}) == 2
    assert hotel.created == 1
