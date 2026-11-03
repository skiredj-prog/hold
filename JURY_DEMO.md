# HOLD — jury demo

This is the shortest reliable path for a judge. It uses the **scripted agent**, a **synthetic hotel**, and a **simulated WhatsApp relay**. It does not require Ollama, Meta credentials, a tunnel, or live network access.

## What you will see

1. The agent proposes a hotel booking.
2. The synthetic provider creates one booking but loses the response.
3. HOLD marks the outcome unknown and prevents a second booking.
4. The accountable authority grants one bounded status check.
5. HOLD finds the receipt and closes the mission only with evidence.
6. The exported evidence envelope verifies offline.

## Prerequisites

- Git
- Python 3.10 or newer
- A terminal

## Run on macOS/Linux

From a fresh clone:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,server]"
python -m experience --relay simulated --agent scripted --db hold-jury.db
```

Open [http://127.0.0.1:8787/experience](http://127.0.0.1:8787/experience).

Click **Start governed mission**. When the mission pauses, click **Allow 1 more check**. Then choose **Inspect Evidence** and export the Evidence Envelope.

Verify the downloaded archive:

```bash
python -m experience.verify HOLD-<mission-id>-evidence.zip
```

## Run on Windows PowerShell

From a fresh clone:

```powershell
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction Stop).Source }

& $py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,server]"
& .\.venv\Scripts\python.exe -m experience --relay simulated --agent scripted --db hold-jury.db
```

Open [http://127.0.0.1:8787/experience](http://127.0.0.1:8787/experience), start the mission, allow one more check, inspect the evidence, and export the ZIP.

Verify it offline:

```powershell
& .\.venv\Scripts\python.exe -m experience.verify "HOLD-<mission-id>-evidence.zip"
```

Expected verification:

```
Archive vs manifest: files match
Journal chain: valid
Positions recomputed: identical
Verdicts recomputed: identical
Status replayed event by event: identical
State implied by the events vs exported store: identical
```

## What is available

| Path | Purpose | Requirements |
|---|---|---|
| This jury demo | One reliable governed mission | Python only |
| [HOLD Studio](README.md#hold-studio--tailor-the-habilitation-follow-the-mission-reconstruct-it) | Profile editing and mission inspection | Python only |
| [Recorded benchmark replay](README.md#see-it-in-two-minutes--recorded-runs-no-model-no-network) | Inspect frozen Llama traces | Python only |
| Full E1-A6 reproduction | Re-run the exploratory benchmark | Ollama, Llama 3.2 3B, about 1h40 |
| Live WhatsApp relay | Real Meta decision transport | Meta credentials, webhook, tunnel; implemented but not live-validated |

## Source map

| Concern | Code |
|---|---|
| Execution boundary | [hold/floor.py](hold/floor.py), [hold/runtime.py](hold/runtime.py) |
| Habilitation profiles | [hold/profile.py](hold/profile.py), [hold/profiles/](hold/profiles/) |
| Unknown outcomes and recovery | [hold/hesitation.py](hold/hesitation.py), [hold/outcome.py](hold/outcome.py) |
| Evidence and replay | [hold/journal.py](hold/journal.py), [hold/replay.py](hold/replay.py), [hold/journal_verify.py](hold/journal_verify.py) |
| Meta WhatsApp relay | [hold/relay/whatsapp.py](hold/relay/whatsapp.py), [experience/gateway.py](experience/gateway.py) |
| Guided experience | [experience/](experience/) |
| Tests | [tests/](tests/) |

## Scope and limitations

- The hotel and provider are synthetic.
- The default path is scripted and does not call an LLM.
- The benchmark uses local Meta Llama 3.2 3B and recorded traces.
- The WhatsApp relay is implemented, but live end-to-end delivery was not validated because access restrictions prevented a live test.
- The evidence manifest is not independently signed.
- See [HACKATHON_CONTRIBUTIONS.md](HACKATHON_CONTRIBUTIONS.md) for the boundary between hackathon work, prior research, and future vision.
