# HOLD E1-A4 — Windows PowerShell verification and benchmark

## 0. Preserve the original A3 incident evidence

```powershell
$root = "$env:USERPROFILE\Documents\Hold\HOLD_qualification"
$original = "$root\E1_A3\hold\bench\results\e1-a3-c-3b"
if (Test-Path $original) {
    Compress-Archive -Path $original -DestinationPath "$root\E1_A3_C_3B_ORIGINAL_REFUTED.zip" -Force
}
```

Do not edit A3's JSON or frozen protocol. The original C-llama result is H1 REFUTED (33 findings), even if A4 later improves results.

## 1. Unpack A4 separately

Download `HOLD_E1_A4_execution_guard_handoff.zip` to Downloads.

```powershell
$root = "$env:USERPROFILE\Documents\Hold\HOLD_qualification"
$a4dir = "$root\E1_A4"
$a4 = "$a4dir\hold"
$py1 = "$root\E1_v1\hold\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $a4dir | Out-Null
Expand-Archive "$env:USERPROFILE\Downloads\HOLD_E1_A4_execution_guard_handoff.zip" -DestinationPath $a4dir -Force
cd $a4
Test-Path 'pyproject.toml'  # True
& $py1 -c "import hold.bench; print(hold.bench.__file__)"  # must point to E1_A4
```

No change to the existing Windows `fcntl`/portalocker environment shim is necessary. This remains a declared experimental adaptation.

## 2. Verify A4 before using Ollama

```powershell
& $py1 -m hold.cli bench check
& $py1 -m pytest -q
```

Expected: `Frozen protocol matches the code.` and 90 passed. A4 protocol digest: `15af3cc2df2a37b47b17efcb60522bf36e1c526543e53240aa548198c9716691`.

If anything fails, stop. Never call `bench freeze` to make checks pass.

## 3. Qualify C-llama (fresh result directory)

```powershell
cd $a4
ollama list
& $py1 -m hold.cli bench run `
  --conditions C-llama `
  --model llama3.2:3b `
  --out bench/results/e1-a4-c-3b
```

Expected: 33 executions, but no positive H1 outcome is presumed. Inspect violations and raw findings:

```powershell
$r = Get-Content 'bench/results/e1-a4-c-3b/results.json' -Raw | ConvertFrom-Json
Write-Host 'Runs:' $r.results.Count
Write-Host 'Protocol:' $r.protocol_sha256
$r.results | ForEach-Object { $_.violations } | Group-Object | Sort-Object Count -Descending | Format-Table Count,Name
$r.results | Select-Object scenario,rep,completed,status,violations | Format-Table -AutoSize
```

Do not confuse `verified` (journal replay) with external-effect validity or mission success. If this run fails, save the raw data, halt other E1 arms and investigate.

## 4. Only after C is understood: rerun A and B **under this same A4 protocol**

```powershell
& $py1 -m hold.cli bench run --conditions A --model llama3.2:3b --out bench/results/e1-a4-a-3b
& $py1 -m hold.cli bench run --conditions B --model llama3.2:3b --out bench/results/e1-a4-b-3b
```

Each run must have 33 complete cases and the same A4 protocol SHA-256. Never combine outputs from A2/A3 with A4 into one H2 verdict.

## 5. Export

```powershell
Compress-Archive -Path "$a4\bench\results" -DestinationPath "$root\E1_A4_3B_results.zip" -Force
ollama list | Out-File "$root\ollama-models.txt"
```

Upload `E1_A3_C_3B_ORIGINAL_REFUTED.zip` and `E1_A4_3B_results.zip` (or C-only until it has been reviewed) for independent trajectory review.

E2-v2/D is not part of this amendment; keep it unchanged pending its separately qualified execution.
