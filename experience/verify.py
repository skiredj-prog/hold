"""Verify a downloaded evidence ZIP offline, without HOLD's database, a model or a provider.

    python -m experience.verify HOLD-<mission>-evidence.zip

Two independent checks:
1. the archive matches its manifest (SHA-256 of every file);
2. the journal inside is re-verified from scratch: the hash chain, every position and verdict recomputed
   from the events and the profile in force, and the state those events imply compared with the store
   snapshot exported beside them.
The second check does not trust the manifest: rewriting a file and its manifest entry still breaks the
chain or the replay. Neither check authenticates who produced the archive (the manifest is not signed),
nor proves that nothing was left out.
"""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from hold.journal import Event, verify_chain
from hold.profile import load_profile
from hold.replay import fold, verify_decisions

from .envelope import verify_export


def _plain(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str, sort_keys=True))


def verify_journal(journal: dict[str, Any]) -> dict[str, Any]:
    """Re-verify an exported hold.journal/v1 document."""
    events = [Event(**e) for e in journal["events"]]
    chain = verify_chain(events)
    profiles = {fp: load_profile(p) for fp, p in journal["profiles"].items()}
    vd = verify_decisions(events, profiles)
    implied, recorded = _plain(fold(events, profiles)), _plain(journal["store"])
    differences = sorted(k for k in implied if implied.get(k) != recorded.get(k))
    ok = chain.valid and vd["positions"]["matches"] and vd["decisions"]["matches"] and vd["status"]["matches"] and not differences
    return {
        "valid": bool(ok),
        "chain": {"valid": bool(chain.valid), "events": chain.count, "broken_at": chain.broken_at, "reason": chain.reason},
        "positions": {"checked": vd["positions"]["checked"], "matches": vd["positions"]["matches"]},
        "decisions": {"checked": vd["decisions"]["checked"], "matches": vd["decisions"]["matches"]},
        "status_replay": vd["status"]["matches"],
        "state_vs_store": {"matches": not differences, "differences": differences},
    }


def verify_archive(raw_zip: bytes) -> dict[str, Any]:
    manifest = verify_export(raw_zip)
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
            journal = verify_journal(json.loads(z.read("mission-journal.json")))
    except (KeyError, ValueError, TypeError, OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        journal = {"valid": False, "reason": f"journal unreadable: {type(exc).__name__}: {exc}"}
    return {"valid": bool(manifest["valid"] and journal["valid"]), "manifest": manifest, "journal": journal,
            "scope": "Local re-verification only: no signature on the manifest, no proof of completeness, no provider or model re-run."}


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify a HOLD evidence ZIP offline")
    ap.add_argument("file", type=Path)
    result = verify_archive(ap.parse_args().file.read_bytes())
    m, j = result["manifest"], result["journal"]
    print(f"Archive vs manifest: {'files match' if m['valid'] else 'MISMATCH ' + str(m.get('mismatched') or m.get('reason'))}")
    if "chain" in j:
        print(f"Journal chain: {'valid' if j['chain']['valid'] else 'INVALID at ' + str(j['chain']['broken_at'])} · {j['chain']['events']} events")
        print(f"Positions recomputed: {j['positions']['checked']}, {'identical' if j['positions']['matches'] else 'MISMATCH'}")
        print(f"Verdicts recomputed: {j['decisions']['checked']}, {'identical' if j['decisions']['matches'] else 'MISMATCH'}")
        print(f"Status replayed event by event: {'identical' if j['status_replay'] else 'MISMATCH'}")
        print(f"State implied by the events vs exported store: {'identical' if j['state_vs_store']['matches'] else 'DIFFERENT ' + ', '.join(j['state_vs_store']['differences'])}")
    else:
        print(j["reason"])
    print(result["scope"])
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
