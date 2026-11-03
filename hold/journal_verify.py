"""Verify a mission journal with a correct exit code: python -m hold.journal_verify DB MISSION

`hold journal verify DB MISSION` prints the right checks, then fails on an undefined name while computing
its exit code (hold/cli.py, line 86). That file is in the E1-A6 frozen perimeter, so the fix waits for a
post-hackathon amendment. This module, outside the perimeter, runs the same checks and exits 0 only if the
chain is valid, every position, verdict and status transition recomputes identically, and the state the
events imply matches the store; 1 otherwise; 2 for an unknown mission.
"""
from __future__ import annotations

import sys

from .journal import verify_chain
from .replay import journal_profiles, state_replay, verify_decisions
from .store import Store


def check(db: str, mission: str) -> tuple[int, list[str]]:
    store = Store(db)
    if not store.mission(mission):
        return 2, ["Unknown mission"]
    events = store.events(mission)
    profiles = journal_profiles(store, events)
    chain = verify_chain(events)
    sr = state_replay(store, mission, profiles)
    vd = verify_decisions(events, profiles)
    ok = lambda r: "all recomputed identical" if r["matches"] else f"{len(r['mismatches'])} mismatch(es)"  # noqa: E731
    lines = [
        f"Chain: {'valid' if chain.valid else 'INVALID at ' + str(chain.broken_at) + ' (' + str(chain.reason) + ')'} · {chain.count} events",
        f"Positions recomputed from the history: {vd['positions']['checked']}, {ok(vd['positions'])}",
        f"Verdicts recomputed from those positions: {vd['decisions']['checked']}, {ok(vd['decisions'])}",
        f"Mission status recomputed event by event: {ok(vd['status'])}",
        f"Final state vs store: {'identical' if sr['matches'] else 'DIFFERENT ' + ', '.join(sr['differences'])}",
        "Not verifiable from the journal alone: the provider's register, the authority's identity (checked at runtime), model outputs, completeness, clock.",
    ]
    good = chain.valid and sr["matches"] and vd["positions"]["matches"] and vd["decisions"]["matches"] and vd["status"]["matches"]
    return (0 if good else 1), lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m hold.journal_verify DB MISSION", file=sys.stderr)
        return 2
    code, lines = check(*args)
    print("\n".join(lines), file=sys.stderr if code == 2 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
