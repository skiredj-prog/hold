"""Replay a recorded run for a reader: what the agent proposed, what HOLD decided, what the journal proves.

    python -m hold.showcase TRACE.json [--color]     one run, step by step
    python -m hold.showcase FOLDER                    every run of a campaign folder, one line each

No model, no provider, no network: everything is read from the raw trace written by `hold bench run`.
The trace is shown as recorded; its SHA-256 is printed and looked up in any MANIFEST*.sha256 found next to it
or up to three folders above, so a reader can tie it to a qualified campaign.

Reading order. HOLD's events are printed in journal order, which is authoritative. The agent's turns are placed
from its own message history: each call it made is matched with the floor's verdict on it. Calls the harness did
not execute (written in the same turn, before the agent had seen an earlier result) never reach HOLD and are shown
as such. When a call comes back HELD, HOLD keeps reconciling after the agent's reply; those events are printed after it.

This module is outside the E1-A6 frozen perimeter (bench.CODE): it reads traces, it does not produce them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import bench
from .journal import Event, verify_chain
from .narrate import C, describe

DISPATCH_EVENTS = {"ACTION_RESULT", "EFFECT_RESERVED", "EFFECT_OUTCOME", "HOLD_ENTERED", "EVIDENCE"}
QUIET = {"ACTION_RESULT", "POSITION", "PROPOSAL", "EVIDENCE", "MISSION_OPENED", "RUNTIME_BOUND"}


class Out:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str = "", tone: str | None = None) -> None:
        if self.color and tone:
            text = f"{C.get(tone, '')}{text}{C['off']}"
        print(text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_entry(path: Path, digest: str) -> tuple[Path, str] | None:
    """The first MANIFEST*.sha256 (next to the trace or up to three folders above) that lists this digest."""
    folder = path.resolve().parent
    for _ in range(4):
        for m in sorted(folder.glob("MANIFEST*.sha256")):
            for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower() == digest:
                    return m, parts[-1]
        if folder.parent == folder:
            break
        folder = folder.parent
    return None


def _short(v: Any, n: int = 42) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


def _said(text: str) -> str:
    lines = [x.strip() for x in _short(text, 700).splitlines() if x.strip()]
    return "  ✎ agent says: “" + "\n                 ".join(lines) + "”"


def _call(name: str, args: Any) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return f"{name}({_short(args)})"
    args = args or {}
    keys = ["hotel_id", "price", "currency", "booking_reference"] if name in ("create_booking", "send_itinerary") else list(args)
    shown = [f"{k}={_short(args[k])}" for k in keys if k in args]
    hidden = len([k for k in args if k not in keys])
    return f"{name}({', '.join(shown)}{', …' if hidden else ''})"


def agent_steps(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The agent's history in order: calls with the result it received, its text turns, and HOLD's updates to it."""
    steps: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    users = 0
    for m in messages or []:
        role = m.get("role")
        if role == "user":
            users += 1
            if users > 1:  # the first user message is the task
                steps.append({"kind": "update", "text": m.get("content") or ""})
        elif role == "assistant":
            if m.get("tool_calls"):
                for c in m["tool_calls"]:
                    f = c.get("function") or {}
                    step = {"kind": "call", "name": f.get("name"), "args": f.get("arguments"), "result": None}
                    steps.append(step)
                    pending.append(step)
            elif (m.get("content") or "").strip():
                steps.append({"kind": "text", "text": m["content"].strip()})
        elif role == "tool" and pending:
            step = pending.pop(0)
            try:
                step["result"] = json.loads(m.get("content") or "{}")
            except ValueError:
                step["result"] = {"status": "?", "message": m.get("content")}
    return steps


def replay(path: Path, color: bool = False) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "scenario" not in raw or "model_messages" not in raw:
        print(f"{path.name} is not a raw run written by `hold bench run` (a results.json or a smoke summary?)", file=sys.stderr)
        return 2
    out = Out(color)
    digest = _sha256(path)
    listed = _manifest_entry(path, digest)
    scn = next((s for s in bench.EVAL + bench.DEV if s["id"] == raw.get("scenario")), None)
    out("HOLD · replay of a recorded run", "bold")
    out(f"  run      {path.stem} · condition {raw.get('condition')} · scenario {raw.get('scenario')} ({raw.get('family')})")
    out(f"  trace    sha256 {digest[:16]}… · " + (f"listed in {listed[0].name} ({listed[1]})" if listed else "not found in a manifest"), "dim")
    if not raw.get("hold"):
        return _replay_without_hold(raw, out)

    events = raw["journal"]["events"]
    objs = [Event(**e) for e in events]
    memory: dict[int, Any] = {}
    for e in objs:
        if e.type == "PROFILE_ATTACHED":
            out("  " + describe(e, memory)[1], "dim")
        if e.type == "RUNTIME_BOUND":
            d = e.data.get("discerner") or {}
            out(f"  hold     {e.data.get('core')} · discerner: {d.get('name')} ({d.get('kind')}{', not a model' if d.get('kind') == 'rule' else ''})", "dim")
    out()

    # A call HOLD makes itself, right after the authority's one-time approval, is not one of the agent's calls.
    by_hold = {i for i, e in enumerate(objs) if e.type in ("ACTION_ADMITTED", "ACTION_REFUSED") and i > 0
               and objs[i - 1].type == "AUTHORITY_GRANT" and (objs[i - 1].data.get("grant") or {}).get("kind") == "ENVELOPE_ONCE"}
    actions = [i for i, e in enumerate(objs) if e.type in ("ACTION_ADMITTED", "ACTION_REFUSED") and i not in by_hold]
    steps = agent_steps(raw.get("model_messages"))
    dispatched = [s for s in steps if s["kind"] == "call" and (s["result"] or {}).get("status") != "NOT_EXECUTED"]
    aligned = len(dispatched) == len(actions)
    if not aligned:
        out(f"  (the agent's {len(dispatched)} dispatched calls and the journal's {len(actions)} verdicts do not pair one to one; "
            "the journal is shown alone)", "amber")
        steps = []
    p = next((i for i, e in enumerate(objs) if e.type == "RUNTIME_BOUND"), -1) + 1

    def hold_events(upto: int) -> None:
        nonlocal p
        while p < upto:
            e = objs[p]
            p += 1
            if e.type in QUIET:
                if e.type == "POSITION":
                    describe(e, memory)
                continue
            if p - 1 in by_hold:
                out(f"      HOLD  submits {e.data.get('action')} itself, under the authority's one-time approval", "plum")
            r = describe(e, memory)
            if r:
                out(f"      HOLD  {r[1]}", r[0])

    a = 0
    for s in steps:
        if s["kind"] == "text":
            out(_said(s["text"]), "bold")
            continue
        if s["kind"] == "update":
            stop = next((i + 1 for i in range(p, len(objs)) if objs[i].type == "RESUMED"), p)
            hold_events(stop)
            out(f"  ◂ HOLD tells the agent: “{_short(s['text'], 200)}”", "plum")
            continue
        res = s["result"] or {}
        if res.get("status") == "NOT_EXECUTED":
            out(f"  · {_call(s['name'], s['args'])}", "dim")
            out("      not executed by the harness: written in the same turn, before the agent saw an earlier result", "dim")
            continue
        idx = actions[a]
        a += 1
        hold_events(idx)
        e = objs[idx]
        p = idx + 1
        out(f"  ▸ {_call(s['name'], s['args'])}", "bold")
        if e.type == "ACTION_ADMITTED":
            out(f"      HOLD  admitted ({e.data.get('kind')})" + (f" · values checked against an earlier result: {', '.join(e.data['provenance_checked'])}"
                                                               if e.data.get("provenance_checked") else ""), "green")
        else:
            out(f"      HOLD  REFUSED ({e.data['rule']}): {e.data['reason']}", "red")
        if res.get("status") == "OK" and isinstance(res.get("data"), dict) and res["data"].get("reconciled"):
            stop = next((i + 1 for i in range(p, len(objs)) if objs[i].type == "RESUMED"), p)
        else:
            stop = p
            while stop < len(objs) and objs[stop].type in DISPATCH_EVENTS:
                stop += 1
        hold_events(stop)
        if e.type == "ACTION_REFUSED" and res.get("status") == "REFUSED" and (res.get("message") or "").startswith(e.data["reason"][:40]):
            out("      → the agent receives REFUSED, with the reason above", "dim")
        else:
            out(f"      → the agent receives {res.get('status')}: {_short(res.get('message') or '', 160)}", "dim")
        if res.get("next"):
            out(f"        next: {_short(res['next'], 160)}", "dim")
    hold_events(len(objs))
    return _summary(raw, scn, events, out)


def _summary(raw: dict[str, Any], scn: dict[str, Any] | None, events: list[dict[str, Any]], out: Out) -> int:
    chain = verify_chain(events)
    s = bench.score_hold(raw, scn) if scn else None
    out()
    out("What the journal proves", "bold")
    status = raw["journal"]["mission"]["status"]
    out(f"  mission      {status}", "green" if status == "CLOSED_COMPLETED" else "amber")
    confirmed = [e["data"] for e in events if e["type"] in ("EFFECT_OUTCOME", "RECONCILED") and e["data"].get("receipt")]
    for d in confirmed:
        action = next((x["data"]["action"] for x in events if x["type"] == "EFFECT_RESERVED" and x["data"].get("effect_key") == d.get("effect_key")), "effect")
        out(f"  confirmed    {action} · receipt {d['receipt']}")
    done = {next((x["data"]["action"] for x in events if x["type"] == "EFFECT_RESERVED" and x["data"].get("effect_key") == d.get("effect_key")), None) for d in confirmed}
    for action in ("create_booking", "send_itinerary"):
        if action not in done:
            out(f"  not done     {action}: no receipt in the journal", "amber")
    out(f"  provider     {raw['provider']['created']} booking(s) created for this stay")
    if s:
        refused = s["overreach"]["refused_by_rule"]
        out(f"  floor        {s['effect_proposals']} effect proposal(s) examined · refused: "
            + (", ".join(f"{k} ×{v}" for k, v in sorted(refused.items())) if refused else "none"))
        out("  violations   " + ("; ".join(s["violations"]) if s["violations"] else "none (scored from the journal and the provider, independently of the runtime)"),
            "red" if s["violations"] else "green")
        if s["report_fidelity"] == "overclaim":
            out(f"  agent report OVERCLAIM: it claims {', '.join(s['report_overclaims'])}; the journal says {status}. The journal is authoritative.", "red")
        elif s["report_fidelity"] == "faithful":
            out("  agent report faithful: no claim beyond the evidence was found by the declared rule", "green")
        else:
            out("  agent report none (its last turn was not a text)", "dim")
    out(f"  integrity    hash chain {'valid' if chain.valid else 'INVALID'} ({chain.count} events)"
        + (f" · every floor decision replayed: {'identical' if s['verified'] else 'MISMATCH'}" if s else ""),
        "green" if chain.valid and (not s or s["verified"]) else "red")
    return 0 if chain.valid and (not s or s["verified"]) else 1


def _replay_without_hold(raw: dict[str, Any], out: Out) -> int:
    out("  no HOLD in this condition: every call went straight to the provider", "dim")
    out()
    for s in agent_steps(raw.get("model_messages")):
        if s["kind"] == "text":
            out(_said(s["text"]), "bold")
        elif s["kind"] == "call":
            res = s["result"] or {}
            out(f"  ▸ {_call(s['name'], s['args'])}", "bold")
            out(f"      → executed: {res.get('status')} {_short(res.get('message') or '', 140)}", "dim")
    out()
    out("What the provider shows", "bold")
    out(f"  provider     {raw['provider']['created']} booking(s) created")
    out("  violations   " + ("; ".join(raw["violations"]) if raw.get("violations") else "none"), "red" if raw.get("violations") else "green")
    out(f"  completed    {raw.get('completed')}")
    return 0


def table(folder: Path) -> int:
    rows = []
    for f in sorted(folder.glob("*_r*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        scn = next((s for s in bench.EVAL + bench.DEV if s["id"] == raw.get("scenario")), None)
        if raw.get("hold") and scn:
            s = bench.score_hold(raw, scn)
            rows.append((f.name, s["status"], "yes" if s["completed"] else "no", str(s["effect_proposals"]),
                         ",".join(f"{k}×{v}" for k, v in sorted(s["overreach"]["refused_by_rule"].items())) or "-",
                         str(len(s["violations"])), s["report_fidelity"]))
        else:
            rows.append((f.name, "-", "yes" if raw.get("completed") else "no", "-", "-", str(len(raw.get("violations") or [])), raw.get("report_fidelity", "-")))
    head = ("run", "status", "completed", "proposals", "refused", "violations", "report")
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(len(head))]
    for r in [head] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print(f"\n{len(rows)} runs · completed {sum(r[2] == 'yes' for r in rows)} · violations {sum(int(r[5]) for r in rows)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hold.showcase", description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="a raw run (.json) or a campaign folder")
    ap.add_argument("--color", action="store_true", help="ANSI colours")
    a = ap.parse_args(argv)
    p = Path(a.path)
    if a.color and os.name == "nt":
        os.system("")  # Windows consoles: turns on ANSI colour handling
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: the arrows and quotes are UTF-8
    return table(p) if p.is_dir() else replay(p, color=a.color)


if __name__ == "__main__":
    sys.exit(main())
