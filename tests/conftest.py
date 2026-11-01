import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hold.relay.base import SimulatedRelay  # noqa: E402
from hold.runtime import Habilitation  # noqa: E402
from hold.testing import BOOKING, SyntheticHotel, wire  # noqa: E402

PROFILE = ROOT / "hold" / "profiles" / "reservation.hold.yaml"


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
