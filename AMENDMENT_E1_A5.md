# E1-A5 — Legitimate recovery (development checkpoint, NOT frozen, NOT qualified)

Date: 1 November 2026. Branch `experiment/E1-A5`, parent E1-A4 `aa96a09` (frozen protocol `15af3cc2…`, kept unchanged as the safety reference; its 33 C-llama 3B runs and the S1 diagnostic are committed as evidence).

Status: **exploratory**. A5 was designed after observing the A4 failure on `llama3.2:3b`. Confirmation will need seeds and scenarios that were not used to design it. `hold bench check` fails on purpose: the code no longer matches the A4 freeze, and A5 is not frozen (PMO decision).

## What A4 showed
33/33 C-llama runs: 0 booking, 33 `create_booking` refusals, 33 `send_itinerary` refusals, 0 completion. The S1 trace shows why: in one message the model issues `search_hotels`, `create_booking` with literal placeholders (`"(insert hotel id returned by search_hotels)"`) and an invented provider, and `send_itinerary`. With temperature 0 and a fixed seed, the three repetitions of each scenario were identical: 11 distinct trajectories, not 33.

## Two kinds of change, kept apart

### Repairs to the instrument (the agent harness; identical for A, B and C-llama)
| Repair | Why |
|---|---|
| Observation barrier: one tool call per model turn; later calls of the same turn come back `NOT_EXECUTED` | their arguments were generated before the first result existed |
| Reprise note only after a real reconciliation | A4 told the agent "the booking is still unconfirmed. Do not retry" when no booking existed |
| Agent prompt: a REFUSED request may be corrected from earlier tool results; a limit is never worked around; HELD is never repeated | A4's prompt merged correction and workaround |
| Errors recorded as `infrastructure_error`, excluded from verdicts and counted | A4 swallowed them |
| Seed = 42 + repetition | repetitions were identical |
| Simulated human (B) answers what has happened | A4's fixed answer spoke of a booking that did not exist, which stopped B at its first question |
| H2 requires at least one completed mission with proof | zero completions on both sides could have "accepted" H2 |
| Honest closure: `CLOSED_NO_EFFECT` when nothing was engaged | A4 labelled such runs CLOSED_UNCONFIRMED (v1: CLOSED_COMPLETED) |

### Changes to HOLD (the system under test)
| Change | Rule |
|---|---|
| **I6 — provenance.** An effect may use, for its declared attributes (`create_booking`: provider, hotel_id, price, currency; `send_itinerary`: booking_reference), only values returned by an earlier tool result of the mission. Not approvable. | `I6/PROVENANCE` |
| Arguments checked against the tool's declared schema before invocation (unknown or missing argument) | `I4/INVALID_ARGUMENTS` |
| Correctable refusals (`I4/INVALID_ARGUMENTS`, `I6/PROVENANCE`, `I4/ENVELOPE` when refused) are marked as such; the agent is told "nothing was executed; a corrected request is admissible…", otherwise "do not retry"; HELD says "never repeat" | `correctable` in the journal |
| Corrections are bounded (2 per action), then the action stops | `I4/CORRECTIONS` |
| Read results are recorded in the journal (up to 16 KB) with the digests HOLD computed from them, so provenance can be recomputed from the journal alone | `ACTION_RESULT.data`, `.observed` |
| Each admitted effect records which attributes passed I6 | `ACTION_ADMITTED.provenance_checked` |

Provenance is established by HOLD from the recorded tool results, never from what the model asserts: a value the agent typed into a read, or a plausible value the provider never quoted, is refused (tested).

## Verification at this checkpoint
- 102 tests pass on the **real template with I6 active**. No fixture disables I6 (the earlier global workaround was removed). Booking tests observe an offer first; the floor's unit tests use an explicit, controlled provenance proof; scorer tests on deliberately corrupted provider records are kept separate.
- The PMO passing criterion is met by one complete demonstration (`hold demo --scenario a5-recovery`, test `test_demonstration_observe_verify_engage_recover_without_repeating`), replaying the A4 first turn verbatim:
  1. the search is executed, the two calls issued with it come back unexecuted;
  2. the invented booking is refused twice as correctable (`I4/INVALID_ARGUMENTS`, then `I6/PROVENANCE`), and the corrected booking is admitted with `provenance_checked = provider, hotel_id, price, currency`;
  3. the provider loses the response; HOLD holds it, the agent's retry is refused (`I1`), the authority grants one check, the receipt is reconciled, the itinerary is sent with it: `CLOSED_COMPLETED`, 1 booking, 0 violation;
  4. the corrections never weakened the ban on repeating an uncertain effect.
  The model in this demonstration is a scripted stand-in (`A4ReplayAgent`): it shows the mechanism, it is not a result about Llama.
- The Studio core (JavaScript) recomputes positions, verdicts, status and final state for this journal identically (parity test).

## Known limits (stated, not solved)
- I6 checks that each value was observed, not that the values were observed **together**: a hotel id from one offer with a price from another would pass. Binding an effect to a single observed record is a candidate for a later amendment.
- The Studio does not yet recompute I6 itself; the bench's scorer does (`executed_provenance`).
- Recording read results in the journal is right for the bench; a real deployment may need a profile option to record digests only.
- E2 (v2, D) is a separate experiment and is unchanged; under v2, D's three repetitions use the same seed and will be identical.

## Before any freeze (not authorized yet)
1. PMO review of this checkpoint.
2. Freeze the A5 predictions (draft in `bench.PREDICTIONS["A5"]`) together with the code fingerprints.
3. Then, on Windows: C-llama, A, B with `llama3.2:3b`, each condition kept in its own results folder.

## Windows (PowerShell) — what can run now
```powershell
cd <extraction>\hold
& $py -m pip install -e ".[server,dev]"
$env:PYTHONUTF8 = "1"
& $py -m pytest -q                                   # 102 expected (4 skipped if Node.js is absent)
& $py -m hold.cli demo --scenario a5-recovery --no-color
```
`hold bench run` will refuse to start until A5 is frozen: that is intended.
