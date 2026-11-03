# HOLD E1-A2 — Windows PowerShell instructions

This archive contains a controlled **post-freeze scoring amendment** of E1/v1, made after the first `A/S1/repetition 1` attempt crashed with `TypeError: '>' not supported between instances of 'str' and 'int'`, and before any completed E1 model comparison. The original E1/v1 protocol hash and E2/v2 remain untouched. The new E1-A2 hash is `52ae33b244019b8793845ef0be5a21066b1ae690c33c5910fa55f308e5af6071`.

## 1. Extract the ZIP separately (do not overwrite v1 or v2)

Put `HOLD_E1_A2_scoring_handoff.zip` in Downloads, then open **PowerShell**:

```powershell
$root = "$env:USERPROFILE\Documents\Hold\HOLD_qualification"
$v1 = "$root\E1_v1\hold"
$a2dir = "$root\E1_A2"
$a2 = "$a2dir\hold"
$py1 = "$v1\.venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $a2dir | Out-Null
Expand-Archive "$env:USERPROFILE\Downloads\HOLD_E1_A2_scoring_handoff.zip" -DestinationPath $a2dir -Force
Test-Path "$a2\pyproject.toml"   # should be True
Test-Path $py1                   # should be True
```

## 2. Reuse the working v1 Python environment (with existing portalocker Windows shim)

```powershell
cd $a2
& $py1 -c "import hold.bench; print(hold.bench.__file__)"
```

**Required:** printed path must begin with `...\HOLD_qualification\E1_A2\hold\hold\bench.py`. If it references `E1_v1`, stop: the patched package was not imported.

```powershell
& $py1 -m hold.cli bench check
& $py1 -m pytest -q
```

Expected `Frozen protocol matches the code.` and 71 passing tests (subject to platform-specific differences). The v1 environment must still have the `fcntl.py`/`portalocker` adapter previously installed; do not change the shim silently.

## 3. Check the model and rerun E1

```powershell
ollama list
cd $a2
& $py1 -m hold.cli bench run `
  --conditions A,B,C-llama `
  --model llama3.2:3b `
  --out bench/results/e1-a2-3b
```

Do **not** reuse `bench/results/e1-3b` or edit the original v1 folder. Do not run `bench freeze`.

When finished:

```powershell
$r = Get-Content "$a2\bench\results\e1-a2-3b\results.json" -Raw | ConvertFrom-Json
"Runs: $($r.results.Count)"
"Protocol: $($r.protocol_sha256)"
```

Expected 99 runs and E1-A2 hash `52ae33b244019b8793845ef0be5a21066b1ae690c33c5910fa55f308e5af6071`.

## 4. Package evidence

```powershell
Compress-Archive -Path "$a2\bench\results\e1-a2-3b" -DestinationPath "$env:USERPROFILE\Desktop\HOLD_E1_A2_3B_results.zip" -Force
```

Upload `HOLD_E1_A2_3B_results.zip`. The numeric tool argument issue is retained per run in `price_input_findings` and `model_messages`—these are model-output records, not evidence of external real-world transactions. In particular, a numeric string remains a schema mismatch. The synthetic provider is still permissive and should not be described as validating tools according to production rules.

Run `llama3.1:8b` only after 3B is complete, in a **different output folder** `bench/results/e1-a2-8b`. Keep both models separate. Use v2 for D when ready; never mix E1-A2 and E2-v2 files under one protocol hash.

## 5. If a new error occurs

Preserve the exact error, the last printed condition/scenario/repetition, the protocol hash, and any partial files. Do not alter the source or re-freeze interactively. Report the failure before retrying.
