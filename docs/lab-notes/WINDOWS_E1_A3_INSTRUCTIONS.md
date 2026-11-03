# HOLD E1-A3 — Windows PowerShell (safe restart after second scoring failure)

The failure `TypeError: '>' not supported between instances of 'str' and 'int'` at `hold/bench.py:411` in `score_hold()` has been corrected in **E1-A3**. The earlier E1-A2 patch only fixed `run_naked()`; do not rerun E1-A2 expecting it to work. E1-A3 retains raw malformed model output and records the scoring discrepancy.

**A/B traces from the aborted E1-A2 run are not a complete experiment; preserve them, but do not mix them with E1-A3 results as a preregistered single-run comparison.**

## 1 — Preserve the old crash evidence (no modifications to the earlier directories)

Open PowerShell and run:

```powershell
$root = "$env:USERPROFILE\Documents\Hold\HOLD_qualification"
$a2 = "$root\E1_A2\hold"
$partial = "$a2\bench\results\e1-a2-3b"
if (Test-Path $partial) {
  Compress-Archive -Path $partial -DestinationPath "$root\E1_A2_INCOMPLETE_3B.zip" -Force
  Write-Host "Saved incomplete E1-A2 raw runs"
}
```

## 2 — Extract the new A3 package into its own directory

Download `HOLD_E1_A3_scoring_handoff.zip` into Downloads. Then:

```powershell
$a3dir = "$root\E1_A3"
$a3 = "$a3dir\hold"
$py1 = "$root\E1_v1\hold\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $a3dir | Out-Null
Expand-Archive "$env:USERPROFILE\Downloads\HOLD_E1_A3_scoring_handoff.zip" -DestinationPath $a3dir -Force
Test-Path "$a3\pyproject.toml"
Test-Path $py1
cd $a3
& $py1 -c "import hold.bench; print(hold.bench.__file__)"
```

The imported `bench.py` **must** be in `...\E1_A3\hold\hold\bench.py`, not E1-A2 or v1.

## 3 — Verify E1-A3 locally before running the model

```powershell
& $py1 -m hold.cli bench check
& $py1 -m pytest -q
```

Expected: `Frozen protocol matches the code.` The Linux reference suite passes 79 tests, so expect 79 on this Windows environment with Node.js installed and the already-tested `fcntl`/`portalocker` compatibility shim. If any test fails, do not proceed.

E1-A3 frozen protocol digest: `7786a750ca3591b4d32f281a6d5041f5273eb92af13969eaa3d9de68a0b7f8a9`.

## 4 — Run `C-llama` first, preserving a complete result for that condition

```powershell
cd $a3
ollama list
& $py1 -m hold.cli bench run `
  --conditions C-llama `
  --model llama3.2:3b `
  --out bench/results/e1-a3-c-3b
```

After successful completion, verify **33** runs and protocol identity:

```powershell
$c = Get-Content "bench/results/e1-a3-c-3b/results.json" -Raw | ConvertFrom-Json
"C-llama runs: $($c.results.Count)"
"Protocol: $($c.protocol_sha256)"
```

If C fails, stop and preserve the raw run JSONs and error; do not proceed to A/B or edit frozen code.

## 5 — Run A and B separately

Only after C succeeded:

```powershell
& $py1 -m hold.cli bench run `
  --conditions A `
  --model llama3.2:3b `
  --out bench/results/e1-a3-a-3b

& $py1 -m hold.cli bench run `
  --conditions B `
  --model llama3.2:3b `
  --out bench/results/e1-a3-b-3b
```

Each completed `results.json` must have 33 rows and the same protocol digest. Do not merge results before all 3 conditions finish.

## 6 — Evaluate H1/H2 and export

```powershell
& $py1 -m hold.cli bench evaluate --results `
  bench/results/e1-a3-a-3b/results.json `
  bench/results/e1-a3-b-3b/results.json `
  bench/results/e1-a3-c-3b/results.json

foreach ($name in 'e1-a3-a-3b','e1-a3-b-3b','e1-a3-c-3b') {
  $r = Get-Content "bench/results/$name/results.json" -Raw | ConvertFrom-Json
  Write-Host "$name : $($r.results.Count) rows; protocol $($r.protocol_sha256)"
}

Compress-Archive -Path "$a3\bench\results\e1-a3-a-3b", `
                       "$a3\bench\results\e1-a3-b-3b", `
                       "$a3\bench\results\e1-a3-c-3b" `
  -DestinationPath "$root\HOLD_E1_A3_3B_RESULTS.zip" -Force
```

Upload `HOLD_E1_A3_3B_RESULTS.zip` and, if available, the earlier `E1_A2_INCOMPLETE_3B.zip` for provenance.

## Notes

- C-llama uses Llama as the **task agent** and the deterministic reference for hesitation; it is **not** experiment D.
- D remains part of the separate frozen E2-v2 protocol and must not be mixed with these E1-A3 outputs.
- Do not use `bench freeze`; never edit the earlier v1, A2, or v2 folders.
- Model-generated string prices remain schema deviations. Scoring can report them but does not thereby prove production-grade tool validation.
- Retain `portalocker` and the same external Windows `fcntl.py` adapter used in previous passing tests. Do not assume these Linux suites prove the Windows compatibility automatically.
