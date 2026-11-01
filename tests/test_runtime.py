import json
import threading

from hold.discern import Proposal
from hold.hesitation import true_map
from hold.replay import decision_replay, export_journal, policy_replay, state_replay
from hold.testing import BOOKING


def assert_sound(hab, m):
    report = m.verify()
    assert report.valid, report
    sr = state_replay(hab.store, m.id, hab.profile)
    assert sr["matches"], sr["differences"]
    dr = decision_replay(m.events(), hab.profile)
    assert dr["matches"], dr["mismatches"]


def types(m):
    return [e.type for e in m.events()]


def test_lost_response_is_reconciled_without_repeating(make, booking):
    hab, hotel, relay = make(visible_after=2)
    m = hab.open_mission("Lyon")
    assert m.call("venue_info").status == "OK"
    r = m.call("create_booking", **booking)
    assert r.status == "OK" and r.data["reconciled"]
    assert hotel.created == 1
    assert m.call("create_booking", **booking).status == "ALREADY_CONFIRMED"
    assert m.call("create_booking", **booking | {"traveler": "fictional traveler a."}).status == "ALREADY_CONFIRMED"
    assert hotel.created == 1
    assert m.call("send_itinerary", traveler="A", booking_reference=r.data["receipt"]).status == "OK"
    assert [e.data["verdict"]["applied"] for e in m.events() if e.type == "DECISION"] == ["VERIFY", "VERIFY"]
    assert m.close()["status"] == "CLOSED_COMPLETED"
    assert relay.outbox == []  # l'autorité n'a pas été dérangée
    assert_sound(hab, m)


def test_dependent_actions_wait_and_independent_work_continues(make, booking):
    hab, hotel, _ = make(visible_after=None, relay=False)
    m = hab.open_mission("Lyon")
    r = m.call("create_booking", **booking)
    assert r.status == "HELD" and m.status == "HELD"
    assert m.call("send_itinerary", traveler="A", booking_reference="X").rule == "I3"
    assert m.call("search_hotels", city="Lyon").status == "OK"
    assert m.call("create_booking", **booking).status == "HELD"
    assert hotel.created == 1
    # sans autorité joignable, la cohérence n'est pas rétablissable : préserver
    assert "PRESERVED" in types(m)
    assert m.close()["status"] == "CLOSED_UNCONFIRMED"
    assert_sound(hab, m)


def test_authority_is_asked_only_when_habilitation_cannot_restore(make, booking):
    hab, hotel, relay = make(visible_after=3)
    m = hab.open_mission("Lyon")
    r = m.call("create_booking", **booking)
    assert r.status == "HELD" and m.status == "AWAITING_AUTHORITY"
    applied = [e.data["verdict"]["applied"] for e in m.events() if e.type == "DECISION"]
    assert applied == ["VERIFY", "VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY"]
    req = m.open_request()
    assert [o["code"] for o in req["options"]] == ["EXTEND_CHECKS", "CLOSE_UNCONFIRMED", "REVOKE"]
    assert all(len(o["title"]) <= 20 for o in req["options"])
    assert relay.outbox[-1]["request"]["question_id"] == req["question_id"]
    assert hab.answer(m.id, "q_000000000000", "EXTEND_CHECKS", channel="whatsapp") == {"accepted": False, "reason": "QUESTION_NOT_OPEN"}
    assert hab.answer(m.id, req["question_id"], "APPROVE_ONCE", channel="whatsapp")["reason"] == "OPTION_NOT_OFFERED"
    assert hab.answer(m.id, req["question_id"], "EXTEND_CHECKS", channel="whatsapp")["accepted"]
    assert hab.answer(m.id, req["question_id"], "EXTEND_CHECKS", channel="whatsapp")["reason"] == "QUESTION_NOT_OPEN"
    # la directive de l'autorité passe avant l'échelle : le contrôle accordé est fait, il trouve le reçu
    assert m.status == "OPEN"
    last = [e for e in m.events() if e.type == "DECISION"][-1]
    pos = {e.seq: e.data["position"] for e in m.events() if e.type == "POSITION"}[last.data["position_seq"]]
    assert last.data["verdict"]["applied"] == "VERIFY" and pos["reference"] == "VERIFY" and pos["paths"][0]["directed"]
    assert hotel.created == 1
    assert_sound(hab, m)


def test_close_unconfirmed_and_revocation(make, booking):
    hab, hotel, _ = make(visible_after=None)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    q = m.open_request()["question_id"]
    hab.answer(m.id, q, "CLOSE_UNCONFIRMED", channel="whatsapp")
    assert m.status == "CLOSED_UNCONFIRMED"
    closed = [e for e in m.events() if e.type == "MISSION_CLOSED"][-1]
    assert closed.data["report"]["unconfirmed"][0]["action"] == "create_booking"
    assert m.call("venue_info").status == "CLOSED"
    assert_sound(hab, m)

    hab2, _, _ = make(visible_after=None)
    m2 = hab2.open_mission("Lyon")
    m2.call("create_booking", **booking)
    hab2.answer(m2.id, m2.open_request()["question_id"], "REVOKE", channel="whatsapp")
    assert m2.status == "REVOKED"
    assert m2.call("venue_info").status == "REVOKED"
    assert_sound(hab2, m2)


def test_proven_refusal_allows_one_new_attempt(make, booking):
    hab, hotel, _ = make(create_mode="reject")
    m = hab.open_mission("Lyon")
    assert m.call("create_booking", **booking).status == "FAILED"
    hotel.create_mode = "respond"
    r = m.call("create_booking", **booking)
    assert r.status == "OK" and hotel.created == 1
    eff = m.effects()[0]
    assert eff["attempts"] == 2 and eff["status"] == "CONFIRMED"
    assert_sound(hab, m)


def test_concurrent_calls_create_one_effect(make, booking):
    hab, hotel, _ = make(create_mode="respond")
    m = hab.open_mission("Lyon")
    results = []
    threads = [threading.Thread(target=lambda: results.append(m.call("create_booking", **booking).status)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hotel.created == 1
    assert results.count("OK") == 1 and set(results) <= {"OK", "ALREADY_CONFIRMED", "HELD", "REFUSED"}
    assert_sound(hab, m)


def test_envelope_breach_goes_to_authority_and_approval_is_exact(make, booking):
    hab, hotel, relay = make(create_mode="respond")
    m = hab.open_mission("Lyon")
    pricey = booking | {"price": 340}
    r = m.call("create_booking", **pricey)
    assert r.status == "HELD" and r.rule == "I4/ENVELOPE" and hotel.created == 0
    req = m.open_request()
    assert req["kind"] == "ENVELOPE_BREACH" and [o["code"] for o in req["options"]] == ["APPROVE_ONCE", "REFUSE", "REVOKE"]
    assert hab.answer(m.id, req["question_id"], "APPROVE_ONCE", channel="whatsapp")["accepted"]
    assert hotel.created == 1 and hotel.bookings[0]["attrs"]["price"] == 340
    # l'autorisation ne valait que pour ces attributs, une seule fois
    assert m.call("create_booking", **booking | {"price": 350, "hotel_id": "LYON-AUTRE"}).status in ("REFUSED", "HELD")
    assert hotel.created == 1
    assert_sound(hab, m)


def test_refused_proposals_never_deadlock_the_mission(make, booking):
    class Stubborn:
        name = "stubborn"

        def propose(self, position, context):
            self.feedback = context["feedback"]
            return Proposal("stubborn", "ASK_AUTHORITY", {"effect_state": "NOT_SENT", "open_paths": [], "reaching_tau": []}, "always ask")

    stubborn = Stubborn()
    hab, hotel, relay = make(visible_after=2, relay=False, discerner=stubborn)
    m = hab.open_mission("Lyon")
    r = m.call("create_booking", **booking)
    assert r.status == "OK" and hotel.created == 1
    decisions = [e.data for e in m.events() if e.type == "DECISION"]
    first_tries = [d for d in decisions if d["attempt"] == 0]
    finals = [d for d in decisions if d["final"]]
    assert all(d["outcome"] == "MAP_REFUSED" and d["verdict"]["applied"] is None for d in first_tries)
    assert all(d["outcome"] == "REFERENCE_APPLIED" and d["verdict"]["credit"] == "floor" and d["verdict"]["applied"] == "VERIFY" for d in finals)
    assert "effect_state is UNKNOWN, not NOT_SENT" in stubborn.feedback[0]["reason"]  # its error was handed back
    assert not any(d["verdict"]["credit"] == "discerner" for d in decisions)  # nothing is credited to it
    assert_sound(hab, m)


def test_stale_position_is_refused_and_reread(make, booking):
    hab, hotel, _ = make(visible_after=2)
    m = hab.open_mission("Lyon")
    calls = {"n": 0}

    class Racy:
        name = "racy"

        def propose(self, position, context):
            calls["n"] += 1
            if calls["n"] == 1:  # pendant la délibération, le monde change (contrôle externe)
                with hab.store.tx() as c:
                    c.execute("UPDATE effects SET not_found=not_found+1 WHERE effect_key=?", (position["effect_key"],))
            return Proposal("racy", position["reference"], true_map(position), "")

    hab.discerner = Racy()
    r = m.call("create_booking", **booking)
    first = [e.data for e in m.events() if e.type == "DECISION"][0]
    assert first["verdict"]["rule"] == "I5" and first["verdict"]["applied"] is None
    assert r.status in ("OK", "HELD")
    assert decision_replay(m.events(), hab.profile)["matches"]


def test_tampering_is_detected_and_policy_replay_is_counterfactual(make, booking):
    hab, hotel, _ = make(visible_after=3)
    m = hab.open_mission("Lyon")
    m.call("create_booking", **booking)
    alt = policy_replay(m.events(), tau_star=0.3, profile=hab.profile)
    assert [x["recorded"] for x in alt][:3] == ["VERIFY", "VERIFY", "CONTACT_SUPPORT"]
    assert alt[2]["replayed"] == "VERIFY" and alt[2]["changed"]  # avec τ*=0,3, un troisième contrôle aurait suffi
    exported = export_journal(hab.store, m.id)
    assert exported["format"] == "hold.journal/v1" and len(exported["profiles"]) == 1
    with hab.store.tx() as c:
        row = c.execute("SELECT seq, json FROM events WHERE mission=? AND type='EFFECT_OUTCOME'", (m.id,)).fetchone()
        ev = json.loads(row["json"])
        ev["data"]["status"] = "CONFIRMED"
        c.execute("UPDATE events SET json=? WHERE mission=? AND seq=?", (json.dumps(ev), m.id, row["seq"]))
    report = m.verify()
    assert not report.valid and report.reason == "invalid hash"
