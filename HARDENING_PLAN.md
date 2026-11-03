# HOLD hardening plan

**Owner:** Abdelaziz Skiredj  
**Purpose:** final-mile submission and runtime hardening without changing the frozen E1-A6 benchmark perimeter.

## Sprint 1 — Submission integrity

Status: **complete**

Completed:

- Public hackathon contribution/provenance record.
- Explicit AI-assistance disclosure.
- Asset and rights register.
- README separation of demonstrated work, prior research, and future vision.
- Cross-version GitHub Actions test workflow.
- Explicit distinction between implemented WhatsApp relay and unvalidated live delivery.

Acceptance criteria:

- A judge can identify the hackathon contribution in under one minute.
- No simulated relay is presented as live Meta delivery.
- AI assistance, code license, and unresolved asset rights are disclosed.
- Repository links resolve and the test workflow is present.

Remaining:

- Confirm official roster and track.
- Confirm final media rights and privacy review.
- Observe a successful CI run or attach a dated local test result.

## Sprint 2 — Runtime safety

Status: **verified**

Completed:

- FastAPI webhook payload-size limit (256 KiB) with regression coverage.
- Gateway already restricts public exposure to the signed webhook path and rejects oversized/chunked requests.
- Studio API remains local-only unless an explicit bearer token is configured.

Acceptance criteria:

- Invalid signatures are rejected.
- Oversized payloads are rejected before JSON processing.
- Duplicate inbound message IDs are idempotent.
- Sender, mission, question, and authority are bound before accepting a decision.
- No webhook route exposes Studio mission data.

Remaining review:

- Run the full relay/server test suite.
- Review token, log, and error-message redaction.
- Decide whether to fix the legacy hold journal verify exit-code defect after the frozen protocol is retired.

## Sprint 3 — Reproducibility and judge handoff

Status: **verified**

Acceptance criteria:

- Clean Windows clone starts the scripted demo with documented commands.
- One evidence envelope exported and verified offline: manifest, journal chain, positions, verdicts, status, and state all matched.
- The judge path does not require Ollama or live WhatsApp.
- Llama and live WhatsApp are clearly marked as optional/conditional paths.
- The final film, README, and submission form use the same claim set.

Observed run evidence (3 November 2026): `177 passed, 1 warning`; scripted mission `CLOSED_COMPLETED`, one synthetic booking, valid chain; evidence envelope verification exit code 0.

## Frozen perimeter

Do not modify the E1-A6 protocol, benchmark traces, scoring rules, or frozen experiment code during submission hardening. Runtime, server, tests, documentation, and packaging changes must not be described as new benchmark evidence unless a new protocol is declared and run.
