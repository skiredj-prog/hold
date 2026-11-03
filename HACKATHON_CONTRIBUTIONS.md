# Hackathon contribution record

**Project:** HOLD  
**Repository:** https://github.com/skiredj-prog/hold  
**Prepared:** 3 November 2026

This record is a submission aid. It separates the implementation demonstrated for the hackathon from earlier research, architectural foundations, and future proposals.

## What this submission demonstrates

The jury demo presents the following working implementation in this repository:

| Demonstrated contribution | Repository evidence | What the demo claims |
|---|---|---|
| HOLD execution boundary | [hold/floor.py](../hold/floor.py), [hold/runtime.py](../hold/runtime.py) | Consequential proposals are admitted, refused, or held before provider execution. |
| Habilitation/profile enforcement | [hold/profile.py](../hold/profile.py), [hold/profiles/](../hold/profiles/) | A human-authored profile defines the mission, authority, limits, and escalation path. |
| Unknown-outcome handling and bounded recovery | [hold/hesitation.py](../hold/hesitation.py), [hold/outcome.py](../hold/outcome.py), [hold/replay.py](../hold/replay.py) | A lost provider response is not treated as success or failure; HOLD checks and escalates within a bounded policy. |
| Evidence journal and offline replay | [hold/journal.py](../hold/journal.py), [hold/journal_verify.py](../hold/journal_verify.py), [hold/showcase.py](../hold/showcase.py) | The mission closes from correlated evidence and the recorded decisions can be replayed without the model. |
| Guided governed-mission experience | [experience/](../experience/) | A viewer can follow one synthetic booking, answer one bounded decision, export an evidence envelope, and verify it offline. |
| Meta Llama integration and benchmark | [bench/](../bench/), [E1_A6_RESULTS.md](../E1_A6_RESULTS.md) | Llama 3.2 3B proposes actions; the HOLD boundary determines what is executed and what counts as established. |
| Implemented Meta WhatsApp Cloud API decision relay | [hold/relay/](../hold/relay/), [docs/whatsapp-live.md](../docs/whatsapp-live.md) | The demo can label a simulated relay; a live WhatsApp path is separate and must not be implied unless credentials and delivery evidence are present. |

## Prior work and excluded claims

The following are **not claimed as newly created during the hackathon**:

- Earlier TENIR/REG research, papers, and architectural ideas that motivated HOLD.
- Any capability shown only as a roadmap, product vision, interoperability target, SDK/API concept, enterprise offering, or commercial model.
- Universal safety, production readiness, regulatory compliance, or an independently certified attestation standard.
- A live WhatsApp round trip when the film uses the simulated relay.
- H3/discernment results; the repository records H3 as not evaluated.

The hackathon contribution is the implemented HOLD runtime, its profile/floor/journal/replay path, the guided experience, the Meta Llama integration, and the exploratory benchmark/evidence package presented as such.

## Provenance statement

The public Git history contains concentrated development activity from 1–3 November 2026, including the HOLD runtime, experience, benchmark, evidence, and documentation work. Git timestamps are supporting evidence of development history, not independent proof of authorship or exclusive development during the event.

The official team roster is an event-registration record and is not inferable from Git authors, co-authored commits, or tool/session metadata. The entrant should confirm the registered human team separately and keep it within the event limit.

## Reproduction path

For the shortest governed experience, follow [experience/README.md](../experience/README.md). For the frozen exploratory benchmark and its scope, follow [E1_A6_RESULTS.md](../E1_A6_RESULTS.md).

## Submission discipline

The jury film should show working hackathon contributions first. Future interoperability, commercialization, and ecosystem opportunities belong in a clearly labeled vision section or separate written material.
