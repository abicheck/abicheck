### Fixed

- **Two more scan-abort report shapes could still lose a real preserved
  decision when fed to `aggregate`** (PR review findings, immediately
  following the artifact-set-shape fix above). `_scan_abort_exit_blocks`
  now recognizes two further cases: (1) the typed Python API's own
  `ScanResult.to_dict()` dumped directly (rather than through the native
  CLI) has no `diff` key at all -- its preserved decision nests at the
  document *root*'s own `report.exit` instead, a third shape distinct from
  both the CLI's `diff.exit` and an artifact-set member's `per_artifact[i].
  report.exit`; and (2) a `scan --artifact-set` set-level abort that fires
  *after* every member already finished normally (e.g. the shared budget
  expires during the post-member bundle audit) preserves `per_artifact`
  with real, completed member results, but a completed member's own
  `ScanResult.report` is empty (no nested `exit` block at all, since it
  never aborted) -- its real result lives only in its own bare top-level
  `exit_code`, now synthesized into a minimal contribution block so it
  folds through the same `max()` machinery as a real one.
