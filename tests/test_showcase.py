"""The replay a reader (or a jury) runs on a recorded trace: no model, no provider, the journal's own checks."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from hold import bench, showcase  # noqa: E402
from test_e1_a6_contract import FollowsTheContract  # noqa: E402


def _trace(tmp_path, scenario="S1"):
    scn = next(s for s in bench.EVAL if s["id"] == scenario)
    inner = FollowsTheContract(model="stand-in", max_steps=bench.AGENT_MAX_STEPS)
    r = bench.run_hold(scn, bench.ReferenceDiscerner(), bench._HoldLlamaAgent(inner), label="C-llama/stand-in")
    f = tmp_path / f"C-llama_stand-in_{scenario}_r1.json"
    f.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return f


def test_the_replay_pairs_every_dispatched_call_with_the_floor_verdict_and_states_what_the_journal_proves(tmp_path, capsys):
    f = _trace(tmp_path)
    assert showcase.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "do not pair" not in out
    assert "not executed by the harness" in out  # the E1-A5 batch: two calls never reached HOLD
    assert "HOLD  admitted (effect)" in out and "Reconciled" in out
    assert "mission      CLOSED_COMPLETED" in out and "hash chain valid" in out and "replayed: identical" in out
    assert "1 booking(s) created" in out


def test_the_replay_finds_the_trace_in_a_manifest_and_flags_a_tampered_journal(tmp_path, capsys):
    f = _trace(tmp_path)
    digest = showcase._sha256(f)
    (tmp_path / "MANIFEST_TEST.sha256").write_text(f"{digest}  results/{f.name}\n", encoding="ascii")
    showcase.main([str(f)])
    assert "listed in MANIFEST_TEST.sha256" in capsys.readouterr().out
    raw = json.loads(f.read_text(encoding="utf-8"))
    ev = next(e for e in raw["journal"]["events"] if e["type"] == "EFFECT_OUTCOME")
    ev["data"]["status"] = "CONFIRMED"  # rewrite history without recomputing the chain
    f.write_text(json.dumps(raw, default=str), encoding="utf-8")
    assert showcase.main([str(f)]) == 1
    out = capsys.readouterr().out
    assert "hash chain INVALID" in out and "not found in a manifest" in out


def test_a_folder_gives_one_line_per_run_and_a_results_file_is_refused(tmp_path, capsys):
    _trace(tmp_path, "S1")
    _trace(tmp_path, "S2")
    (tmp_path / "results.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    assert showcase.main([str(tmp_path)]) == 0
    assert "2 runs · completed 2 · violations 0" in capsys.readouterr().out
    assert showcase.main([str(tmp_path / "results.json")]) == 2
