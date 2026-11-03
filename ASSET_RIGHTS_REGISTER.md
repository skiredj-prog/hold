# Asset and rights register

**Project:** HOLD  
**Repository:** https://github.com/skiredj-prog/hold  
**Prepared:** 3 November 2026

This is a disclosure checklist for the hackathon submission. It is not a legal opinion. The repository's Apache-2.0 license covers repository code where the applicable copyright holder has granted that license; it does not automatically clear every asset appearing in a film, screenshot, audio track, font, or external service.

## Known project assets

| Asset category | Current status | Required disclosure / action |
|---|---|---|
| HOLD source code | Apache-2.0 license in [LICENSE](../LICENSE) | Keep the license visible in the repository and submission. |
| Meta Llama 3.2 3B | Open-weight model used locally through Ollama | Name the model and serving path; comply with the applicable model license and attribution terms. |
| Meta WhatsApp Cloud API | Implemented live relay; end-to-end delivery was not validated because access/credential restrictions prevented a live test; benchmark/demo relay is simulated | State clearly that the integration is implemented but live-unvalidated. Do not imply observed delivery or production approval. |
| Synthetic hotel provider and booking data | Synthetic demonstration data | Label as synthetic; do not present it as a real hotel, customer, or booking. |
| Repository screenshots/UI | Project-produced or captured from the project | Confirm that no personal data, credentials, phone numbers, or private account identifiers appear. |
| Benchmark traces and evidence envelopes | Project-generated experimental artefacts | Confirm that they contain no secrets or personal data before distribution. |

## Film and presentation assets requiring owner confirmation

Before final submission, the entrant should fill in the following from the actual exported film and slides:

| Asset | Owner/source | License or permission | Action |
|---|---|---|---|
| Background music | **To be confirmed from final edit** | **To be confirmed** | Record track title, source, and license or remove/replace. |
| Narration/voice | **To be confirmed** | **To be confirmed** | Confirm synthetic or human voice rights and permission to submit. |
| Fonts | **To be confirmed from project files** | **To be confirmed** | Record font family and license. |
| Illustrations/backgrounds | **To be confirmed** | **To be confirmed** | Confirm original, licensed, or generated provenance. |
| Third-party logos/marks | Meta, WhatsApp, Ollama, GitHub or other marks if shown | Follow applicable brand-use rules | Use only for factual identification; avoid implying endorsement. |
| External paper or research excerpts | Linked sources in README/paper | Citation/permission as applicable | Keep excerpts short and attributed. |

## Privacy and security check

Before publishing the final film, screenshots, or evidence package:

- Remove API tokens, webhook secrets, phone numbers, email addresses, and tunnel URLs.
- Mask authority contact details in WhatsApp screenshots and exported envelopes.
- Confirm that benchmark traces do not contain local usernames or machine paths.
- Confirm that no private credentials are committed to the repository.
- Keep live WhatsApp credentials out of the repository and out of screen recordings.

## Attestation fields for the entrant

Complete these fields from the final submission package:

- Registered human team size: **[fill from official roster]**
- Final film filename/version: **[fill]**
- Music source/license: **[fill]**
- Narration source/permission: **[fill]**
- Font licenses: **[fill]**
- Third-party assets reviewed by: **[fill]**
- Date of final rights review: **[fill]**

Any field that cannot be verified should remain disclosed as unknown rather than being presented as cleared.
