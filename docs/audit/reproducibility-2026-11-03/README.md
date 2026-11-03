# HOLD E1-A6 reproducibility handoff

Built from public repository `https://github.com/skiredj-prog/hold`.

Frozen experiment identity: tag `e1-a6-frozen`, commit `12250bca32b9fbc77113994db8a68036f9948f9d`; protocol digest `d467a86b2bb9b22f16fd35cfe09da9ad9d0bcf05a6b9799b574abd22adedc081`.

## Files

- `FROZEN_SOURCE_e1-a6-frozen_LF.zip`: the exact 23 source/profile files listed in the frozen protocol's `protocol.code` map, read directly from Git blobs at the tag, plus the frozen protocol JSON and an embedded SHA-256 manifest. No working-tree line-ending conversion was involved.
- `hold-e1-a6-history.bundle`: Git bundle of `main`, `experiment/E1-A6`, and `e1-a6-frozen`, including reachable Git history. Included tips: `main` `0c52bbf15361a2a38bcf5a6994902c8d7636dc0f`; experiment `35d4e80d2f324b530e10054b647cbc0ed189e9ea`; frozen tag object `6a9490e68eede2b73a629df3851756c9441ae22e`, resolving to the commit above.
- `source-export-verification.txt`, `bundle-verification.txt`, `frozen-bench-check.txt`, and `trace-manifest-verification.txt`: logs from the checks run while preparing this handoff.
- `SHA256SUMS`: hashes for the package files other than itself.

## Verify the byte-preserving source archive

```sh
unzip -q FROZEN_SOURCE_e1-a6-frozen_LF.zip -d source-export
(cd source-export && sha256sum -c FROZEN_SOURCE_MANIFEST.sha256)
```

Expected: 23 source files report `OK`. Compare the embedded protocol JSON digest with its outer wrapper and inspect its `protocol.code` map.

## Verify the Git bundle and frozen code

```sh
git bundle verify hold-e1-a6-history.bundle
git bundle list-heads hold-e1-a6-history.bundle
git clone --branch main hold-e1-a6-history.bundle hold-check
cd hold-check
git switch --detach e1-a6-frozen
git rev-parse HEAD
python -m pip install -e '.[server,dev]'
python -m hold.bench check
```

`git rev-parse HEAD` must print `12250bca32b9fbc77113994db8a68036f9948f9d`; `hold bench check` must exit successfully. Run the frozen fingerprint check on the tag, not current `main`, whose post-freeze code is intentionally different.

## Verify the recorded traces

In the bundle clone, before switching away from `main` (or after `git switch main`):

```sh
sha256sum -c bench/results/MANIFEST_E1_A6.sha256
```

This verifies files against the repository's own manifest, not their authorship, collection time, model/server execution provenance, or an external provider event. Git history and these files are hash-addressed but not signed attestations. A genuinely independent reviewer should fetch the published artifacts and report their own verification; this package does not claim that external review has occurred.
