# HOLD — One governed mission, one inspectable record

A **separate integration demonstration** layered on the existing HOLD runtime.
No edits to the frozen E1-A6 protocol, kernel, experiment code or campaign results.

## What the viewer experiences

1. **Habilitation:** A mission with a €300 ceiling and accountable authority.
2. **Execution:** The scripted demo agent searches and proposes one synthetic hotel booking; the provider creates it but loses the response.
3. **HOLD:** The existing kernel and reference reconciliation logic hold dependent effects, check status, and issue a Decision Request when needed.
4. **Authority:** A real WhatsApp button tap (live mode), or a labeled simulated tap, grants **one additional verification** rather than another booking. The live path is implemented but was not end-to-end validated because access restrictions prevented a live test.
5. **Continuity:** The existing provider receipt is found; the agent sends the itinerary and the mission can close only with evidence.
6. **Evidence:** Download a ZIP of the event journal, original profile, provider register, authority message metadata, deterministic decision replay, and SHA-256 manifest.

The film/demo should always disclose that the hotel is synthetic and the scripted mode **does not** call an LLM. In Llama mode, the agent uses local Ollama and is not guaranteed to complete. No E1-A6 benchmark results are produced or modified here.

## Quick start (Windows PowerShell)

From the HOLD repository directory:

```powershell
& $py -m pip install -e ".[server]"
& $py -m pytest -q tests/test_experience.py
& $py -m experience --relay simulated --agent scripted --db hold-experience.db
```

Open **http://127.0.0.1:8787/experience**. Click **Start governed mission**; after HOLD requests authority, use **Allow 1 more check**. Download the Evidence Envelope and verify it offline (files against the manifest, then the journal itself: chain, positions, verdicts, state against the exported store):

```powershell
& $py -m experience.verify "HOLD-<mission_id>-evidence.zip"
```

For a new run, exit and restart with a **new database filename**. The experience deliberately supports one mission per process and persists the journal.

## Real WhatsApp option

Requires four local environment variables (keep private): `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`. The contact must be an authorized international number in digits-only format.

Terminal 1 — private HOLD runtime / UI:

```powershell
& $py -m experience --relay whatsapp --agent scripted --contact 2126XXXXXXXX --db hold-live-demo.db
```

Terminal 2 — public **webhook only** gateway:

```powershell
& $py -m experience.gateway
```

Terminal 3 — HTTPS tunnel to **gateway port 8788 only**, never to Studio port 8787:

```powershell
cloudflared tunnel --url http://127.0.0.1:8788
```

Configure Meta WhatsApp callback as `https://<tunnel-host>/api/whatsapp/webhook`, verify with `WHATSAPP_VERIFY_TOKEN`, and subscribe to `messages`. Message your test business number from the authorized phone to open the standard 24-hour service window (or configure an approved template). Start the mission in the local UI, and answer on the phone.

A successful Cloud API response **does not prove delivery**. Verify that the signed reply was accepted, the mission is actually completed, and the real relay metadata appears in the exported envelope. A human reply alone does not prove a provider effect.

### Offline model option

`& $py -m experience --relay simulated --agent llama` requires Ollama serving `llama3.2:3b` locally and may diverge from the scripted flow. If it does not reach authority, do not manipulate the result or claim a live WhatsApp demonstration.

## Evidence format and limitations

Export format: `hold.experience-envelope/v0` — a **prototype export**, not an attestation standard.

| File | Meaning |
|---|---|
| `mission-journal.json` | Exact recorded mission events and attached habilitation profile |
| `governance-replay.json` | Chain validity, state reconstruction and decision replay |
| `synthetic-provider.json` | Independent-in-process synthetic hotel register and lookup history |
| `authority-relay.json` | Message direction, mode, status and masked authority contact |
| `findings.json` | Explicit outcome and verification limitations |
| `manifest.json` | File SHA-256, source revision, timestamp and scope statement |

`experience.verify` also re-checks the journal from scratch, so rewriting a file together with its manifest entry is detected. The manifest still proves only internal file equality, **not external origin, completeness, trustworthy timestamps, legal admissibility, independent key custody or regulatory compliance**. Review all exports for sensitive data before sharing.

## Acceptance

Run `python -m pytest -q tests/test_experience.py`. It checks: a bounded mission, exactly one synthetic booking, no early completed outcome, the signed webhook path, reply binding/duplicate rejection, completed mission with receipt/itinerary, replay parity, manifest tamper detection and protection of the local control API.

## Experience v2: show the causal chain and operational benefit

The landing screen now starts with the **hotel booking use case** and three
explicit benefits: the user avoids an unverified repeat, the agent has a
legitimate next step through uncertainty, and the organization has a record
it can inspect. The six-stage **causal timeline** is derived from the mission
journal and synthetic provider request log; the UI never advances it on a timer.

At the incident, a side-by-side comparison distinguishes the **synthetic
provider's lab ground truth** (one booking created, response lost) from
**HOLD's established knowledge** (`UNKNOWN`, no correlated receipt yet).
The prominent authority panel makes one bounded status check the primary
choice. The agent can then resume only if actual reconciliation succeeds.

The **Meta role is explicit and mode-dependent**: Meta Llama 3.2 is the model
used in frozen E1-A6 research and can be selected for this demo using
`--agent llama`; the default `--agent scripted` does not run a model.
Meta WhatsApp Cloud API is used only with `--relay whatsapp` and working
provider credentials/webhook. Simulated mode never sends a real message.
The separate HOLD execution kernel and evidence code do not inherit
capabilities or guarantees from either Meta service.

Choose **Inspect Evidence**, then select the JSON tabs (findings, manifest,
journal, replay, provider, authority). These are read from the same ZIP bytes
as the export. The file digests are recomputed, and the manifest is explicitly
labeled as *not independently signed*. Because authority metadata may be
sensitive, review before displaying or distributing it.

Note: v2 refines the interface and adds local evidence inspection. It does
not alter the frozen E1-A6 protocol, raise the status of H3, certify
interoperability or prove the WhatsApp live round trip.


## Guided jury UI (v3, 2026-11-03)

The user-facing `/experience` route begins by naming the booking task, why the
response-loss scenario is dangerous, what the audience will see, and the practical
benefit for the agent and traveler. The human decision controls live in the same
next-action panel as the explanation. Raw event codes are secondary.

No governance, relay, provider, evidence verification, or scenario state transition
was changed. The UI milestone track follows recorded journal/provider events and
does not advance on a visual timer. The default run is scripted/simulated.
