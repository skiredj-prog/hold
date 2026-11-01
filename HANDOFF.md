# HOLD — handoff (1 Nov 2026, 14:45)

## State
- 59 tests pass (`pip install -e ".[server,dev]" && pytest`).
- Bench protocol v1 frozen: `bench/protocol.frozen.json`, SHA-256 `3404d1a823c719eb4215494236ede853d27dffa35ec56b2a9a390a5555252426`.
- Deterministic conditions already run under v1: `bench/results/deterministic/results.json`
  - H1: 0 violations over 55 HOLD runs (C, C2, X adversarial).
  - 21/22 C/C2 predictions met. S10 / C2 mispredicted: `C2_IMPLEMENTATION_DEFECT` (the rule splits sentences at ";").
- Decision taken: amendment A1 (fix C2's sentence boundary, with positive/negative regression controls frozen BEFORE the patch, fresh unseen paraphrases/decoys, v2 freeze). **Not done yet.**
- HOLD Studio: `hold/studio/core.js` is complete and parity-tested (independent recomputation of positions, verdicts, status). The UI page `hold/studio/studio.src.html` is written but not built/wired yet (no build script).

## What you can run now (v1, unaffected by the C2 defect)
E1 = H1/H2: the same Llama agent without HOLD (A), cautious (B), wearing HOLD (C-llama).

```bash
pip install -e ".[server,dev]"
ollama pull llama3.2:3b
ollama pull llama3.1:8b
hold bench check                                   # must say: Frozen protocol matches the code.
hold bench run --conditions A,B,C-llama --model llama3.2:3b --out bench/results/e1-3b
hold bench run --conditions A,B,C-llama --model llama3.1:8b --out bench/results/e1-8b
hold bench evaluate --results bench/results/deterministic/results.json bench/results/e1-3b/results.json
```
Each run writes one JSON per run (journal or tool log) plus `results.json`. Send back the whole `bench/results/` folder.

## Do NOT run yet
`--conditions D` (H3): per our decision, D runs only after protocol v2 (amendment A1) is frozen.

## Demos (unchanged)
```bash
hold demo --baseline
hold demo --scenario authority
hold demo --agent llama --discerner llama --scenario authority   # needs Ollama
hold journal verify <db> <mission>                                # recomputes positions, verdicts, status
```

## Next, when I'm back
1. A1: amendment document + regression controls committed first; then the C2 patch; v2 freeze; rerun C, C2, X; untouched-case analysis excluding S10.
2. D runs (3B, 8B) on your machine under v2.
3. Add "proposed vs executed overreach" counts (AuthorizationBench suggestion) as a reported metric in v2 (not a hypothesis criterion).
4. Studio build + screenshots; README for judges; WhatsApp live loop (needs your Meta token, phone number ID, app secret, verify token); video script.

## External reference (verified)
"Act on Your Behalf, Beyond Your Authorization: Benchmarking LLM Agent Authorization Alignment", Zhang, Li, Huang, Li (Northeastern, CMU, UNC), arXiv 2610.27548, 20 Oct 2026: 286 tasks, 10 domains, 6,864 trajectories, 24 configurations; 19.2% out-of-authorization; 14.0% with pre-completion consultation vs 21.6% otherwise; 23.5% benign pressure vs 17.6% injection. The "214 of 313 consulted after overstepping" figure was NOT found in the paper text.
