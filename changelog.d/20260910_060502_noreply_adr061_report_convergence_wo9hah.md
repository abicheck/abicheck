### Fixed

- **`ReportEnvelope` also copies `dict`-valued `DiffResult` attributes** —
  `_snapshot_diff_result` now gives dict-valued attributes (e.g.
  `comparability_assurance`) the same fresh-copy treatment already applied
  to lists and tuples, so a caller mutating one after the envelope was
  built cannot change what HTML's/Markdown's confidence sections report.
- **HTML's envelope-driven render no longer raises `KeyError` under
  `--show-only` with a dangling correlation note** —
  `_suppress_dangling_correlation_notes` hands the renderer shallow
  `Change` copies whenever a filtered-out change's `correlated_change_kind`
  target is gone; `html_report.py` now resolves those through
  `envelope.findings_for(display_changes)` (the primitive built for exactly
  this) instead of indexing `envelope.findings` directly, which has no
  entry for a copy.
- **The `--format review` digest's merge-effect phrase reuses the
  envelope's own `GateDecision`** instead of an independent
  `compute_exit_code` call that happened to agree with it, closing the last
  format-specific re-derivation of a severity-aware gate decision.
