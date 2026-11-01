import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hold.relay.base import SimulatedRelay  # noqa: E402
from hold.runtime import Habilitation  # noqa: E402
from hold.testing import BOOKING, SyntheticHotel, wire  # noqa: E402

TEMPLATE = ROOT / "hold" / "profiles" / "reservation.hold.yaml"


PROFILE = TEMPLATE  # A5: every integration test runs on the real template, with I6 active
PRICEY = dict(BOOKING, price=340)


def opened(hab, title="Lyon", offers=None, **kw):
    """Open a mission and let the agent observe the offer(s) it will book: under I6, an effect may only use
    values a tool result returned. `offers` replaces what search_hotels returns (default: the standard offer)."""
    from hold.outcome import Outcome

    if offers is not None:
        hab.tools["search_hotels"].fn = lambda city="Lyon": Outcome.value([dict(o) for o in offers])
    m = hab.open_mission(title, **kw)
    assert m.call("search_hotels", city="Lyon").status == "OK"
    return m


@pytest.fixture
def make():
    """Fabrique une habilitation branchée sur un hôtel synthétique."""

    def _make(*, relay=True, discerner=None, profile=PROFILE, **hotel_kw):
        hotel = SyntheticHotel(**hotel_kw)
        r = SimulatedRelay() if relay else None
        hab = Habilitation(profile, store=":memory:", relay=r, discerner=discerner, background=False)
        wire(hab, hotel)
        return hab, hotel, r

    return _make


@pytest.fixture
def booking():
    return dict(BOOKING)
