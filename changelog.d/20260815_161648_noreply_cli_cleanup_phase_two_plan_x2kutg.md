<!-- Third round of the --profile quick scoped-gate fix, found by Codex review. -->

### Fixed

- **`compare --profile quick`'s scoped one-liner now counts an ordinary
  in-scope removal.** The previous fix only tallied *synthesized*
  scoped-only findings (e.g. a missing required symbol), not an ordinary
  full-library finding that is also scoped-relevant (removing a function a
  `--used-by`/`--required-symbol` consumer actually calls). That finding
  lives in `result.changes` and is marked relevant via
  `scoped_relevant_finding_ids` (the same set `sarif`/`junit` already read),
  which the one-liner's count previously ignored entirely -- printing
  `no changes (0 total)` while exiting non-zero for the common case, not
  just an edge case.
