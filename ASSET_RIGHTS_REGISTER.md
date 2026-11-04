# Asset and rights register

**Project:** HOLD  
**Repository:** https://github.com/skiredj-prog/hold  
**Prepared:** 4 November 2026

See [the non-media submission review](docs/audit/SUBMISSION_NON_MEDIA_REVIEW_2026-11-04.md) for scan scope, source references, and items requiring entrant confirmation.

This is a disclosure checklist for the hackathon submission. It is not a legal opinion. The repository's Apache-2.0 license covers repository code where the applicable copyright holder has granted that license; it does not automatically clear every asset appearing in a film, screenshot, audio track, font, or external service.

## Known project assets

| Asset category | Current status | Required disclosure / action |
|---|---|---|
| HOLD source code | Apache-2.0 license in [LICENSE](../LICENSE) | Keep the license visible in the repository and submission. |
| Meta Llama 3.2 3B | Open-weight model used locally through Ollama; governed by Meta's [Llama 3.2 Community License](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE) and [Acceptable Use Policy](https://www.llama.com/llama3_2/use-policy), not the repository's Apache-2.0 license. No model weights are tracked in this repository. | Identify the exact local Ollama tag/digest used and have the entrant confirm acceptance/applicability of the license. Do not describe the weights as Apache-2.0 or OSI-licensed. Re-check distribution-specific notice/display terms if model materials are included in the submission. |
| Meta WhatsApp Cloud API | Implemented live relay; end-to-end delivery was not validated because access/credential restrictions prevented a live test; benchmark/demo relay is simulated | State clearly that the integration is implemented but live-unvalidated. Do not imply observed delivery or production approval. |
| Synthetic hotel provider and booking data | Synthetic demonstration data | Label as synthetic; do not present it as a real hotel, customer, or booking. |
| HOLD Studio web fonts | `hold/studio/index.html` and its source load Archivo ([SIL OFL 1.1](https://github.com/google/fonts/blob/main/ofl/archivo/OFL.txt)) and JetBrains Mono ([SIL OFL 1.1](https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt)) from Google Fonts. No font binaries are tracked in the repository. | Record as Studio UI fonts only; this does not clear any font used in the film. Browser requests go to Google Fonts, so viewers' IP/browser connection metadata may be exposed to Google. For a privacy-minimizing offline demo, use system fallbacks or self-host licensed font files with their license notices. |
| Repository screenshots/UI | No tracked `.png`, `.jpg`/`.jpeg`, `.svg`, `.mp4`, `.mov`, `.webm`, `.wav`, `.mp3`, `.m4a`, `.woff`/`.woff2`, `.ttf`, or `.otf` file was found on `main` at `0c52bbf`; Studio does load the remote fonts above. | The final film and any locally held screenshots/media are outside this repository scan and remain to be reviewed from the final export. |
| Benchmark traces and evidence envelopes | Project-generated experimental artefacts | A repository-only scan of 211 result/prequalification JSON files found one unique contact/phone-keyed value, matching the test fixture's `AUTH` value. Treat it as synthetic fixture data, but confirm no live authority/contact data was substituted before publishing any local export. |

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

**Repository-only check, 4 November 2026, public `main` `0c52bbf`:** a pattern scan across 886 Git blobs reachable from the clone found no matches for the selected common GitHub, OpenAI-style, or Meta access-token formats and no PEM private-key header. The current tracked-text scan found no email-shaped addresses or local home-directory paths. The baseline WhatsApp setup guide contained a public sample verify-token string, not a credential; this branch replaces it with an unmistakable placeholder to reduce copy/paste risk. This limited pattern scan is not a comprehensive secret audit and does not inspect the entrant's local files or the final film/audio export.

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
