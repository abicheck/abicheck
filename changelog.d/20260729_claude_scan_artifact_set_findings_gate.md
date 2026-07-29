<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set` (ADR-056/G35): two more Codex-review bugs fixed
  before merge.** (1) The text-format artifact-set report previously showed
  only `path: verdict` per member, discarding every crosscheck/pattern/
  preprocessor finding description and evidence a member scan produced —
  unlike the single-binary `scan`'s report and the aggregate JSON's nested
  `report`, leaving CLI and GitHub Action-summary users unable to act on
  the result. `_render_artifact_set_text` now renders each member's
  findings via the same section renderers the single-binary path already
  uses. (2) When `run_scan_set()`'s bundle audit is skipped because a
  member's ELF metadata silently failed to parse (`bundle_incomplete=True`),
  the set-level exit code and verdict were computed purely from the
  (successfully scanned) member verdicts — so a set where every member
  scanned clean still exited 0, meaning a CI gate that only checks the exit
  code silently accepted a skipped cross-library audit as a full pass. The
  exit code is now floored at 1 and the verdict reports a dedicated
  `BUNDLE_INCOMPLETE` when no worse, already-dominant member problem
  (`API_BREAK`/`BREAKING`/`EVIDENCE_CONTRACT_ERROR`/`BUDGET_OVERFLOW`) is
  already being reported.
