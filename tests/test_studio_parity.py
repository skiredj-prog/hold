"""The Studio verifies journals with its own implementation (hold/studio/core.js). It must reach the
same verdicts as the library, decision by decision, on journals that exercise every gate outcome."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hold.profile import dump_profile, load_profile, revise
from hold.relay.base import SimulatedRelay
from hold.replay import export_journal
from hold.runtime import Habilitation
from hold.testing import SyntheticHotel, wire

from conftest import PROFILE, ROOT
from test_discernment import LAG_NOTE, Clock, Fixed, Reader

CORE = ROOT / "hold" / "studio" / "core.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

SCRIPT = """
const core = require(process.argv[2]);
const journals = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
(async () => {
  const out = [];
  for (const j of journals) {
    const chain = await core.verifyChain(j.events);
    const dec = core.decisionReplay(j.events, j.profiles);
    const fps = {};
    for (const [fp, p] of Object.entries(j.profiles)) fps[fp] = await core.fingerprint(p);
    out.push({ chain, dec, fps });
  }
  console.log(JSON.stringify(out));
})();
"""


def node(journals, tmp_path):
    f = tmp_path / "journals.json"
    f.write_text(json.dumps(journals), encoding="utf-8")
    script = tmp_path / "check.js"
    script.write_text(SCRIPT, encoding="utf-8")
    r = subprocess.run(["node", str(script), str(CORE), str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def journals(booking):
    out = []

    def run(discerner, *, relay=True, then=None, **hotel_kw):
        clock = Clock()
        hotel = SyntheticHotel(clock=clock, **hotel_kw)
        hab = Habilitation(PROFILE, store=":memory:", relay=SimulatedRelay() if relay else None, discerner=discerner, background=False, clock=clock)
        wire(hab, hotel)
        m = hab.open_mission("Lyon")
        m.call("create_booking", **booking)
        if then:
            then(hab, m, clock)
        out.append(export_journal(hab.store, m.id))
        return hab, m

    run(None, visible_after=3)  # reference: verify, verify, support, authority
    run(Fixed("CONTACT_SUPPORT"), visible_after=None, support_mode="finds")  # justified, not the reference
    run(Fixed("ASK_AUTHORITY"), visible_after=2)  # incoherent, then the floor
    run(Fixed("ASK_AUTHORITY"), relay=False, visible_after=2)  # closed move
    run(Reader(), visible_after_s=600, status_note=LAG_NOTE, then=lambda h, m, c: (c.advance(600), m.sweep()))  # wait, then verify
    run(Reader(quote="a passage that is not there"), visible_after_s=600, status_note=LAG_NOTE)  # ungrounded wait

    def answer(h, m, c):
        h.answer(m.id, m.open_request()["question_id"], "EXTEND_CHECKS", channel="whatsapp")

    run(None, visible_after=3, then=answer)  # directed check granted by the authority

    def adopt(h, m, c):
        rev2 = revise(h.profile, title="rev 2")
        h2 = Habilitation(rev2, store=h.store, relay=h.relay, background=False, clock=c)
        h2.mission(m.id, adopt=True)
    run(None, visible_after=None, then=adopt)
    return out


def test_studio_core_reaches_the_librarys_verdicts(booking, tmp_path):
    js = journals(booking)
    res = node(js, tmp_path)
    for j, r in zip(js, res):
        assert r["chain"]["valid"], r["chain"]
        assert r["dec"]["matches"], r["dec"]["mismatches"]
        assert r["dec"]["checked"] == sum(1 for e in j["events"] if e["type"] == "DECISION")
        assert all(fp == r["fps"][fp] for fp in j["profiles"])
    outcomes = {e["data"]["outcome"] for j in js for e in j["events"] if e["type"] == "DECISION"}
    assert {"ADMITTED", "MAP_REFUSED", "MOVE_REFUSED", "INCOHERENT", "REFERENCE_APPLIED"} <= outcomes


def test_studio_core_detects_tampering(booking, tmp_path):
    j = journals(booking)[2]
    forged = json.loads(json.dumps(j))
    d = next(e for e in forged["events"] if e["type"] == "DECISION" and e["data"]["outcome"] == "REFERENCE_APPLIED")
    d["data"]["verdict"]["credit"] = "discerner"  # claim the floor's move for the model
    res = node([forged], tmp_path)[0]
    assert not res["chain"]["valid"] and res["chain"]["reason"] == "invalid hash"
    # and if the forger recomputes the chain, the decision replay still refuses the claim
    assert not res["dec"]["matches"]


def test_catalog_fingerprints_match(tmp_path):
    profiles = {}
    for f in sorted((ROOT / "hold" / "profiles").glob("*.hold.yaml")):
        p = load_profile(f)
        profiles[p.fingerprint] = p.model_dump(mode="json", exclude_none=True)
    res = node([{"events": [], "profiles": profiles}], tmp_path)[0]
    assert all(fp == res["fps"][fp] for fp in profiles)
