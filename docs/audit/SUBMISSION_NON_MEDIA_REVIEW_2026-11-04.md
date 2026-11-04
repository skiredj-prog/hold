# HOLD submission readiness — non-media review

**Reviewed:** 4 November 2026  
**Baseline:** public `main` at `0c52bbf15361a2a38bcf5a6994902c8d7636dc0f`  
**Scope:** repository disclosures, code/model/font licensing references, public-repository privacy indicators, and hackathon roster/track documentation.  
**Excluded by request:** the final video, its changing audio, and any local files not present in the repository. No final-cut media rights are cleared by this review.

## Executive summary

The repository already has useful transparency documents, but one known fact should be added before submission: the AI disclosure lists Claude Opus 5.5 and Codex but omits Manus assistance documented in this project history. A proposed correction is included in this review branch. It identifies the earlier Manus-assisted video script/narration draft as history only and leaves any generated media retained in the final cut explicitly pending inspection of the final export.

The source repository has a root Apache-2.0 license. The Llama 3.2 3B weights used locally through Ollama are governed separately by Meta's Llama 3.2 Community License and Acceptable Use Policy; the weights are not present in the repository. HOLD Studio loads Archivo and JetBrains Mono remotely from Google Fonts; both font families publish SIL Open Font License 1.1 files. That external font request is a privacy/network consideration and is not a statement about fonts in the film.

A limited scan found no matches for selected common GitHub/OpenAI/Meta key formats or PEM private-key blocks across 886 Git blobs reachable from the cloned refs. It found no email-shaped addresses or local home-directory paths in the current tracked text. Of 211 benchmark/prequalification JSON files, there was one unique string under contact/phone-keyed fields; it matches the synthetic `AUTH` test fixture. This is a bounded static scan, not a comprehensive secret audit or review of the final media and the entrant's local machine.

## Actions prepared on the review branch

The proposed branch updates `AI_DISCLOSURE.md` to add Manus and makes clear that the event participant must follow the actual official rules and submission form. It updates `ASSET_RIGHTS_REGISTER.md` with the official Llama license/AUP references, the Studio font sources/licenses and remote request note, and the scope/results of the repository-only scan. It replaces the old fixed public verify-token example in `docs/whatsapp-live.md` with an unmistakable placeholder so that a reader is less likely to copy a fixed public value into a live webhook configuration.

These are documentation changes on a separate review branch. They do not mark entrant attestation boxes complete, change the frozen protocol/results, or edit any video or audio asset.

## Facts checked

### AI assistance and responsibility

The current public AI disclosure says Claude Opus 5.5 and Codex were used for development, documentation, review and submission-readiness work. Manus also assisted in repository review, research, technical/operational documentation, evidence/reproducibility packaging and an earlier video script/narration draft. The disclosure should name Manus as an assistant, not as a human entrant or teammate. Whether any generated audio or other generated media remains in the final cut is deliberately left unresolved until the user completes the audio update and checks the exported film.

The event page reviewed does not provide a specific AI-disclosure field. That does not establish that the accepted official terms or submission form lack one; the entrant must follow those actual documents.

### Copyright and third-party model/fonts

- The repository includes Apache License 2.0 and `pyproject.toml` declares Apache-2.0 for the project package.
- Meta's `meta-llama/Llama-3.2-3B` model page and official model-repository license identify the governing terms as the **Llama 3.2 Community License Agreement**, with the Acceptable Use Policy incorporated by reference. This is a custom community license, not the repository's Apache license. The entrant should confirm the exact Ollama tag/digest used and the applicable license acceptance. The repository does not distribute model weights.
- `hold/studio/index.html` imports Archivo and JetBrains Mono from Google Fonts. The Google Fonts source repository publishes SIL OFL 1.1 license files for both. The repository does not contain those font binaries. If the Studio runs in a browser, the browser makes font requests to Google; use system fallbacks or self-host licensed copies if avoiding that third-party request is important. This finding applies to the Studio, not the final video.
- No tracked `.png`, `.jpg`/`.jpeg`, `.svg`, `.mp4`, `.mov`, `.webm`, `.wav`, `.mp3`, `.m4a`, `.woff`/`.woff2`, `.ttf`, or `.otf` file was found on the baseline `main`. The final film and local assets were not inspected.

### Privacy and credential checks

The targeted scan covered 886 blob objects reachable from the cloned repository refs and checked selected common GitHub PAT, OpenAI-style key, Meta access-token, and private-key header patterns. It found no matches for those selected formats. The current tracked-text scan found zero email-shaped addresses and zero local home-directory paths. This method can miss secrets in other formats and does not establish that a credential was never exposed outside those patterns.

The repository contains many authority/contact field names because the product and benchmark model human authorization. Across 211 JSON result/prequalification traces, the scan found one unique string under fields named contact/phone; it matches the test fixture's synthetic `AUTH` constant. This indicates consistency with the fixture, not a general guarantee that every value in every artifact is harmless. The entrant should not substitute real contact details into public traces or exported evidence.

The single high-confidence scan candidate in the baseline setup guide was an intentional public example verify token, not an actual secret. The proposed branch replaces it with `<generate-random-token>` and explicitly tells readers not to reuse a repository example as a live token.

## Entrant confirmations still required

The repository cannot establish these registration or final-package facts. Leave them blank until checked against the actual records:

1. Confirm the official entrant/registered human roster and selected track from the accepted application and event system. The event listing describes an individual-oriented Indie Developer track and a Startup track, but does not prove which one this entrant selected or the exact rules that apply.
2. Confirm whether the exact local Llama model tag/digest was used and that the downloader/licensee accepted Meta's applicable terms.
3. After the audio update, inspect the exact final video export for retained AI-generated narration/media, third-party music, fonts, images, logos, personal contact details, tunnel URLs, or credentials; then fill the rights register from that file, not an earlier draft.
4. Complete the final film version, third-party asset reviewer, and review date in the register. Keep unknown items explicitly marked unknown rather than checking the rights attestation without evidence.
5. Paste the actual submission-form questions into the project record and answer any AI-use, team, asset-rights, or source-code/license questions with the form's own wording.

## External sources

- Meta Llama 3.2 3B model page: https://huggingface.co/meta-llama/Llama-3.2-3B
- Meta's Llama 3.2 Community License: https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE
- Meta's Llama 3.2 Acceptable Use Policy: https://www.llama.com/llama3_2/use-policy
- Archivo SIL OFL 1.1: https://github.com/google/fonts/blob/main/ofl/archivo/OFL.txt
- JetBrains Mono SIL OFL 1.1: https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt
- Meta / Cerebral Valley event listing: https://cerebralvalley.ai/e/meta-ai-developer-hackathon

This document is a factual checklist, not legal advice, an entrant attestation, or a clearance opinion.
