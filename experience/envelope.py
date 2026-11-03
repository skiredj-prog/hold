"""Evidence *export* for one demonstration mission.

Not a regulatory attestation, notarized envelope, trusted timestamp, independent
custody, or evidence of a real provider. No model/provider calls are made here.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hold.replay import decision_replay, export_journal, journal_profiles, state_replay

MAX_ENVELOPE_MEMBERS = 32
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _source_revision() -> tuple[str | None, bool | None]:
    try:
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=2)
        head = r.stdout.strip() if r.returncode == 0 else None
        state = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"],
                               cwd=root, capture_output=True, text=True, timeout=2)
        return head, bool(state.stdout.strip()) if state.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def capture(mission, hotel, *, channel: str, agent: str) -> tuple[dict[str, Any], bytes]:
    """Capture the CURRENT record; safe for incomplete missions (partial is labeled)."""
    store = mission.store
    events = mission.events()
    j = export_journal(store, mission.id)
    chain = mission.verify()
    replay = {
        "chain": {"valid": bool(chain.valid), "count": chain.count, "broken_at": chain.broken_at, "reason": chain.reason},
        "state": state_replay(store, mission.id, mission.profile),
        "decisions": decision_replay(events, journal_profiles(store, events)),
    }
    relay = [dict(r) for r in store.read(
        "SELECT direction, kind, mode, status, question_id, peer, detail, error, created_at "
        "FROM relay_messages WHERE mission=? ORDER BY id", (mission.id,)
    )]
    # The test provider is a distinct in-memory ledger; include its observed results.
    provider = {
        "kind": "SYNTHETIC_HOTEL_ONLY",
        "create_count": hotel.created,
        "bookings": [
            {"operation_id": b["operation_id"], "receipt": b["receipt"], "attributes": b["attrs"], "checks": b["checks"], "created_at": b["created_at"]}
            for b in hotel.bookings
        ],
        "requests": list(hotel.requests),
    }
    effects = mission.effects()
    confirmed_booking = any(e["action"] == "create_booking" and e["status"] == "CONFIRMED" and e["receipt"] for e in effects)
    confirmed_itinerary = any(e["action"] == "send_itinerary" and e["status"] == "CONFIRMED" and e["receipt"] for e in effects)
    completed = mission.status == "CLOSED_COMPLETED"
    findings = {
        "format": "hold.experience-report/v0",
        "mission_id": mission.id,
        "status": mission.status,
        "agent": agent,
        "relay": channel,
        "bookings_created_synthetic": hotel.created,
        "provider_booking_confirmed": bool(confirmed_booking),
        "itinerary_confirmed": bool(confirmed_itinerary),
        "completed_with_evidence": bool(completed and confirmed_booking and confirmed_itinerary and hotel.created == 1
                                         and replay["chain"]["valid"] and replay["state"]["matches"] and replay["decisions"]["matches"]),
        "checks": {"chain_valid": replay["chain"]["valid"], "state_matches": replay["state"]["matches"], "decisions_match": replay["decisions"]["matches"]},
        "limitations": [
            "Hotel is a synthetic non-idempotent provider; no real reservation or payment.",
            "Journal integrity is local; no independent timestamp, witness, or key custody.",
            "WhatsApp send acceptance is not proof of delivery or human legal identity.",
            "Replay recomputes deterministic governance decisions, not model outputs or external actions.",
            "This single demonstration is outside the frozen E1-A6 scientific experiment.",
        ],
    }
    files = {
        "mission-journal.json": _json(j),
        "governance-replay.json": _json(replay),
        "synthetic-provider.json": _json(provider),
        "authority-relay.json": _json(relay),
        "findings.json": _json(findings),
    }
    created = datetime.now(timezone.utc).isoformat()
    root = Path(__file__).resolve().parents[1]
    source_sha = {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in ("hold/runtime.py", "hold/floor.py", "hold/relay/whatsapp.py",
                    "hold/server.py", "hold/demo.py", "hold/testing.py", "hold/replay.py",
                    "experience/core.py", "experience/envelope.py",
                    "experience/index.html", "experience/gateway.py")
        if (root / rel).is_file()
    }
    revision, dirty = _source_revision()
    manifest = {
        "format": "hold.experience-envelope/v0",
        "generated_at": created,
        "mission_id": mission.id,
        "source_commit": revision,
        "source_worktree_dirty": dirty,
        "source_files_sha256": source_sha,
        "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "verification_scope": "Local integrity and deterministic decision replay; no independent custody, no regulatory certification.",
    }
    files["manifest.json"] = _json(manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return {"findings": findings, "manifest": manifest}, out.getvalue()


def _safe_zip_members(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = z.infolist()
    if len(infos) > MAX_ENVELOPE_MEMBERS:
        raise ValueError("too many archive members")
    indexed: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        if not info.filename or info.filename in indexed or info.is_dir():
            raise ValueError("invalid archive layout")
        if info.file_size > MAX_MEMBER_BYTES:
            raise ValueError(f"archive member too large: {info.filename}")
        if info.compress_size == 0 and info.file_size:
            raise ValueError(f"invalid compression metadata: {info.filename}")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ValueError(f"suspicious compression ratio: {info.filename}")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("archive expands beyond verification budget")
        indexed[info.filename] = info
    return indexed


def verify_export(raw_zip: bytes) -> dict[str, Any]:
    """Offline verification of file digests (does NOT authenticate the manifest's author)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
            members = _safe_zip_members(z)
            names = list(members)
            if "manifest.json" not in names:
                return {"valid": False, "reason": "Invalid archive layout"}
            manifest = json.loads(z.read(members["manifest.json"]))
            hashes = manifest.get("files_sha256", {})
            if not isinstance(hashes, dict) or sorted(n for n in names if n != "manifest.json") != sorted(hashes):
                return {"valid": False, "reason": "Manifest file list mismatch"}
            problems = [n for n, want in hashes.items() if hashlib.sha256(z.read(members[n])).hexdigest() != want]
            return {"valid": not problems, "mismatched": problems,
                    "caveat": "Local SHA-256 equality only; the manifest itself has no independent signature."}
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        return {"valid": False, "reason": f"Invalid evidence archive: {type(exc).__name__}: {exc}"}
