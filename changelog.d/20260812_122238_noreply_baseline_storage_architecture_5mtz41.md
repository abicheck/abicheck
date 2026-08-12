<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh`'s release-contract baseline-set fallback** now rejects
  an archive containing a symlink, mirroring
  `actions/resolve-baseline/run.sh` (the canonical baseline-set consumer)
  — `TarExtractor`'s own member validation only rejects a symlink escaping
  the extraction root, not one that stays inside it, so the same archive
  could previously be silently accepted by the root Action's fallback
  while being rejected as `ambiguous` through `check-target`/
  `resolve-baseline`, two consumers of the identical unified baseline-set
  protocol disagreeing on whether the same archive is usable.
- **`publish-baseline.yml`'s "Upload release asset" safe-retry check** now
  also validates `fact_set` (the evidence-producer identity) against the
  local manifest this same job just built, before accepting an existing
  asset's matching content digest as a safe no-op — `compute_content_digest()`
  deliberately excludes `fact_set` (as it already does `profile` and
  `project_ref`), so an existing asset published with a different (or
  edited/corrupted) `fact_set` could previously still reach a matching
  content digest and be treated as a safe retry, even though a real
  consumer's `_evidence_incompatibility()` check
  (`abicheck/buildsource/baseline_set.py`) depends on that field
  independently of snapshot/binary content.
