from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from hold.canonical import canonical_json, digest
from hold.floor import ActionContext, admit_action, effect_key, envelope_violations
from hold.profile import load_profile, revise, sign, signature_valid

from conftest import PROFILE


@pytest.fixture
def profile():
    return load_profile(PROFILE)


def ctx(**kw):
    base = dict(revoked=False, expired=False, usage={}, confirmed_actions=set(), uncertain_actions=set())
    return ActionContext(**(base | kw))


def test_canonical_matches_javascript_conventions():
    assert canonical_json({"b": 300.0, "a": 0.5, "c": None, "d": "é"}) == '{"a":0.5,"b":300,"d":"é"}'
    assert digest({"x": 1}) == digest({"x": 1.0})


def test_profile_fingerprint_revision_signature(profile):
    assert len(profile.fingerprint) == 64
    assert load_profile(PROFILE).fingerprint == profile.fingerprint
    r = revise(profile, title="Autre titre")
    assert r.revision == 2 and r.supersedes == profile.fingerprint and r.fingerprint != profile.fingerprint
    s = sign(profile, "k", "Aziz")
    assert s.fingerprint == profile.fingerprint  # la signature ne change pas l'empreinte
    assert signature_valid(s, "k") and not signature_valid(s, "x")
    with pytest.raises(ValueError):
        load_profile(s.model_dump(mode="json"), secret="x")
    tampered = s.model_copy(update={"title": "modifié"})
    assert not signature_valid(tampered, "k")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["powers"][2].pop("identity"), "identité"),
        (lambda d: d["powers"][2]["reconciliation"].update(status_action="search_hotels"), "status"),
        (lambda d: d["hesitation"].update(ladder=["PRESERVE", "VERIFY"]), "PRESERVE"),
        (lambda d: d["authority"].pop("contact"), "numéro"),
        (lambda d: d["powers"][5].update(depends_on=["venue_info"]), "dépendance"),
        (lambda d: d.update(unknown_field=1), "Extra"),
    ],
)
def test_invalid_profiles_are_rejected(profile, mutate, message):
    data = profile.model_dump(mode="json")
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        load_profile(data)


def test_envelope_bounds_the_content_of_the_effect(profile, booking):
    power = profile.power("create_booking")
    assert envelope_violations(power, booking) == []
    v = envelope_violations(power, booking | {"price": 340, "city": "Paris", "check_in": "2026-12-01", "currency": "USD"})
    assert any("plafond" in x for x in v) and any("city" in x for x in v) and any("check_in" in x for x in v) and any("devise" in x for x in v)
    assert any("requis" in x for x in envelope_violations(power, {k: val for k, val in booking.items() if k != "traveler"}))


def test_admission_rules(profile, booking):
    assert admit_action(profile, "create_booking", booking, ctx()).admitted
    assert admit_action(profile, "create_booking", booking, ctx(revoked=True)).rule == "I4/REVOKED"
    assert admit_action(profile, "create_booking", booking, ctx(expired=True)).rule == "I4/EXPIRED"
    assert admit_action(profile, "pay_invoice", {}, ctx()).rule == "I4/NO_POWER"
    assert admit_action(profile, "check_booking", {}, ctx()).rule == "I4/STATUS_RESERVED"
    assert admit_action(profile, "create_booking", booking, ctx(usage={"power:create_booking": 1})).rule == "I4/BUDGET"
    assert admit_action(profile, "create_booking", booking | {"price": 999}, ctx()).rule == "I4/ENVELOPE"
    grant = digest({"action": "create_booking", "attrs": booking | {"price": 999}})
    assert admit_action(profile, "create_booking", booking | {"price": 999}, ctx(), grant_digest=grant).admitted
    it = {"traveler": "A", "booking_reference": "R"}
    assert admit_action(profile, "send_itinerary", it, ctx(uncertain_actions={"create_booking"})).rule == "I3"
    assert admit_action(profile, "send_itinerary", it, ctx()).rule == "I2"
    assert admit_action(profile, "send_itinerary", it, ctx(confirmed_actions={"create_booking"})).admitted


def test_effect_identity_ignores_case_spaces_and_extra_fields(booking):
    ident = ["provider", "hotel_id", "traveler", "check_in", "check_out"]
    a = effect_key("m1", "create_booking", ident, booking)
    b = effect_key("m1", "create_booking", ident, booking | {"hotel_id": "  lyon-eurexpo-042 ", "traveler": "VOYAGEUR  FICTIF A.", "hotel_name": "autre nom"})
    assert a == b
    assert a != effect_key("m2", "create_booking", ident, booking)


def test_profile_expiry(profile):
    assert not profile.expired(datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert profile.expired(datetime(2026, 12, 1, tzinfo=timezone.utc))
