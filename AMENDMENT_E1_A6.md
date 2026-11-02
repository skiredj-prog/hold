# E1-A6 — Continuation contract and equal model context (development checkpoint 1, NOT frozen, NOT qualified)

> Branch `experiment/E1-A6`, from `94d0319` (the code frozen for E1-A5). E1-A5 and its results are untouched (`E1_A5_RESULTS.md`).
> Status: **exploratory.** A6 was designed after reading the E1-A5 traces, on the same evaluation scenarios and seeds. It can show whether the defects found explain E1-A5; it cannot confirm a performance claim. Confirmation would need scenarios not used to design it.

## What A6 changes, and why

| E1-A5 defect | A6 change | Test |
|---|---|---|
| The contract never named NOT_EXECUTED; "at most once" counted proposals | The HOLD system prompt names NOT_EXECUTED and REFUSED ("nothing was executed… not a repetition"), counts **executed** bookings, and uses the same "Complete the task" imperative as A and B. The NOT_EXECUTED result itself carries a `next` step (all conditions). | `test_the_contract_names_not_executed_and_counts_executed_bookings` |
| A, B and C-llama read different tool definitions (French in C only), a different offer, different messages | The four shared tools are defined once, from HOLD's registry (profile descriptions, now in English; wired signatures), in the same order for every condition; A and B add their own tools after them. `search_hotels` returns the same offer record everywhere; shared tools answer with the same messages. The protocol declares and fingerprints what each condition reads (`model_context`). | `test_a_b_and_c_llama_read_the_same_definitions_of_the_tools_they_share`, `test_search_returns_the_same_offer_record_with_and_without_hold` |
| A refusal said only what is forbidden | A correctable refusal (arguments, I6) also says what would be admissible: the declared attributes copied unchanged from one result of the authorized source, and that source first if it has returned nothing yet. It names fields and tools, never values. | `test_a_correctable_refusal_says_what_would_be_admissible`, `test_a_placeholder_argument_refusal_also_says_what_would_be_admissible` |
| I2/I3 said "Do not retry" | "Not admissible yet: do it once that one is confirmed." Budget, revocation and similar refusals still say "Do not retry". | `test_a_dependency_refusal_says_not_yet_rather_than_never` |
| H1 could be accepted with a floor nothing reached | H1 is **inconclusive** unless, in each HOLD condition, at least one effect proposal reached the floor (`effect_proposals`, written by the runner; rows without it are "not measured"). A violation still refutes H1. Under this rule, E1-A5's H1 would have been inconclusive. | `test_h1_is_inconclusive_when_no_effect_proposal_reached_the_floor_as_in_e1_a5`, `…_not_measure_the_floor`, `…_still_refutes_h1…` |
| The real model never ran before a freeze | `hold bench smoke --conditions C-llama,A,B --model …`: the real model on the three **DEV** scenarios (never the evaluation scenarios), one repetition. It prints each run's executed steps, NOT_EXECUTED count, refusals, bookings and the model's last message. Its rows carry no protocol digest and can never be evidence. | `test_smoke_runs_the_dev_scenarios_only_and_its_rows_can_never_be_evidence` |
| The demonstration's agent did what the model did not | The demonstration now states that its agent is a scripted stand-in and that the model is measured only by `smoke` and `run`. | — |

Mechanism check (scripted, not a result): starting from the verbatim E1-A5 first turn, an agent that follows the new results completes S1 through the floor: two NOT_EXECUTED with their next step, booking admitted on the observed offer, itinerary sent, `CLOSED_COMPLETED`, zero violations (`test_the_e1_a5_first_turn_can_now_lead_to_a_completed_mission_through_the_floor`). Also checked end to end through the CLI and HTTP against a stand-in Ollama server; it shows the pipeline works, not that Llama will follow.

## What A6 does not change
Invariants I1–I6 and every admit/refuse decision (only the text of refusals changes); hesitation and discernment; the observation barrier; the scenarios, seeds (42 + repetition), turn budget (16), models; A's and B's system prompts and the simulated human; the scoring of violations. The tool surface per condition is unchanged: in C-llama, checking, support, waiting and asking the authority stay with HOLD.

## Predictions (written before any A6 run)
In `bench.PREDICTIONS["A6"]`. On llama3.2:3b: C-llama, effect proposals reach the floor in most runs, zero executed violations, some corrected requests after correctable refusals, completions possible (no number predicted). A: unchanged from A5. B: as A, except when it asks first. llama3.1:8b: no prior.

## Before any freeze (proposal to the PMO)
1. PMO review of this checkpoint.
2. **Smoke with the real model, before the freeze**: `hold bench smoke --conditions C-llama,A,B --model llama3.2:3b`. Its full output goes to the PMO. Any change made after it is declared here before the freeze. The DEV scenarios are not used for evaluation.
3. After authorization: freeze (`bench/protocol.frozen.json` committed alone, SHA-256 in the message, tag `e1-a6-frozen`, recorded by the PMO), then C-llama, A, B on llama3.2:3b, each in its own folder; then, if wanted, llama3.1:8b.

## Verification at this checkpoint
138 tests pass (A5 checkpoint 6: 127, plus 11 for A6). Two A5 tests changed their expected H1 verdict from accepted to inconclusive: their stand-in agents only read, as the model did in E1-A5. The synthetic campaign rows of the A5 tests now carry `effect_proposals`. The A5 demonstration still passes (1 booking, 0 violations, `CLOSED_COMPLETED`).

## Windows (PowerShell)
```powershell
cd <extraction>\hold-a6
& $py -m pip install -e ".[server,dev]"
$env:PYTHONUTF8 = "1"
& $py -m pytest -q                                                       # 138 expected (4 skipped if Node.js is absent)
& $py -m hold.cli bench smoke --conditions C-llama,A,B --model llama3.2:3b   # ~9 runs; output to bench/results/smoke-llama3.2-3b
```
Use a new folder: the E1-A5 folder and its results stay as they are. `hold bench run` refuses to start until A6 is frozen.
