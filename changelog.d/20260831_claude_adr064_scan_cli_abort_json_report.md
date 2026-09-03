### Added

- **`scan --format json` now gets a real report on a budget-overflow or
  evidence-contract-error abort, instead of empty stdout** (ADR-064 stage
  1b, native-CLI half). Previously, `cli_scan.py`'s `scan_cmd` wrote only a
  stderr message and exited on `_BudgetOverflow`/`_EvidenceContractError`,
  regardless of `--format` — a `--format json` invocation that hit either
  abort produced no stdout content at all, so a caller parsing it as JSON
  was already broken. The CLI now prints the same minimal
  `{scan_schema_version, exit}` report shape the typed `ScanResult` API
  already persists for these two aborts (including the prior gate/coverage/
  assurance contributions on a late budget overflow, when one was already
  resolved), so the CLI and library JSON payloads agree. Exit codes are
  unchanged (5 for budget overflow, 1 for an evidence-contract error).
  `--format text` is unchanged: the existing stderr message already reads
  as the human-facing explanation for this path.
