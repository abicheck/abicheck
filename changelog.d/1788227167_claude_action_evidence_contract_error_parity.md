### Fixed

- **CLI cleanup phase two / ADR-064, cross-front-end parity pass**: the
  composite GitHub Action's `scan` verdict mapping no longer folds a
  `_EvidenceContractError` abort (ADR-037 D5 — a pinned `--depth`/
  `--source-method` whose required source evidence was never collected)
  into the generic `ERROR` bucket a bad flag or a crash gets. `cli_scan.py`
  raises that abort as a `click.ClickException` (stderr `Error: ...`), the
  identical shape a CLI usage error produces, so `action/run.sh`'s
  `_is_cli_error` check could not tell them apart on its own. The Action
  now recognizes the native CLI's own distinguishable `verdict:
  "EVIDENCE_CONTRACT_ERROR"` JSON envelope (`_emit_scan_abort_report`) and
  publishes a matching `EVIDENCE_CONTRACT_ERROR` output verdict — still
  failing the step unconditionally, with a job-summary line and `verdict`
  output naming the real cause instead of a misleading "CLI error"
  annotation.
