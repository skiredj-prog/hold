# A6 pre-qualification runs — raw traces (DEV scenarios, calibration, never evidence)

Run by the operator on Windows on 2 November 2026 with Ollama, one repetition per DEV scenario, C-llama only. Sent as `HOLD_A6_prequal_raw.zip` and added here unchanged; `MANIFEST.sha256` holds the SHA-256 of every file as received.

| Folder | Model | Code | HOLD contract | Recorded verdict |
|---|---|---|---|---|
| `smoke-llama3.2-3b-hold-min-20261102-205539` | llama3.2:3b | checkpoint 5 (`d3c7702`), `--contract hold-min` | `hold-min` text | GO, continued after a block 16/17 |
| `smoke-llama3.2-3b-hold-min-20261102-214516` | llama3.2:3b | checkpoint 5 (`d3c7702`), `--contract hold-min` | `hold-min` text | GO, 15/17 |
| `smoke-llama3.1-8b-hold-20261102-220414` | llama3.1:8b | checkpoint 7 (`0eaffe6`), default contract | the same text (in force since checkpoint 6) | NO-GO, 2/6 |

Each folder: one raw run per DEV scenario (journal with its hash chain, provider state, every model message), `results.json` (the rows scored at run time) and `smoke.json` (the printed summary).

Anyone can re-check them without the model:

```
hold bench reverify --out bench/prequalification/<folder>
```

It verifies each journal's hash chain, replays every decision, re-scores each run from its journal and compares it with the recorded row, and recomputes the GO / NO-GO with the current gate (exact DEV coverage). `tests/test_e1_a6_contract.py::test_the_recorded_prequalifications_reverify` runs this on the three folders, and checks the manifest.
