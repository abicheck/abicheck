<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **Pin the baseline-set archive's bytes with `expected-archive-digest`.** The root Action's baseline-set fallback (`baseline-profile` + `baseline-target`) and `actions/resolve-baseline` take a SHA-256 of the archive file and refuse any archive whose raw bytes differ, before a single member is extracted. A consistently repacked archive (its manifest rewritten to match) is now detectable: the manifest's own digests cannot catch it, because they live inside the same archive. `actions/stage-baseline` emits the matching `archive-sha256` output, which a consumer pins. The root Action reports `baseline-archive-digest` and `baseline-archive-verified`, and `actions/resolve-baseline` reports `archive-digest` and `archive-digest-verified`. The archive verify-and-extract step now lives in one owner, `abicheck/workflows/baseline_archive.py`, exposed as `abicheck.frontends.action.cli prepare-baseline-set`, which both consumers call.
