<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`run_outcome` report block (ADR-063 Phase 7)** — every JSON report
  (`compare`, `scan`, the release fan-out, and the ADR-050 D2
  not-comparable refusal document) now carries an additive top-level
  `run_outcome` block (`abicheck.policy.outcome.RunOutcome`): independent
  `compatibility`/`assurance`/`gate`/`operational`/`lifecycle` axes,
  alongside the unchanged `verdict`/`exit_code`/`severity` fields. `gate`
  is the new, exit-code-free `PolicyGateDecision` type; `operational` is
  the new `OperationalStatus` type covering budget-overflow/not-comparable/
  evidence-contract-error/extraction-error conditions `PolicyGateDecision`
  alone cannot represent. `abicheck.workflows.aggregate.gate.GateInfo.
  from_report_data`/`from_scan_report` read these structured fields first,
  falling back to decoding the legacy `exit_code`/`severity` only for a
  report that predates this change. Report schema bumped to 2.48
  (`compare`/release) and 1.24 (`scan`).

### Fixed

- **`compare --used-by`/`--required-symbol`'s JSON `run_outcome` now
  reflects the scoped gate, not the full-library one.** `run_outcome` is
  computed before scoping runs, so a scoped-compatible run whose full
  library carries an unrelated real break previously left a stale,
  blocking `run_outcome.gate` in the JSON body — and since the reader
  above prefers `run_outcome` over `severity`, an aggregate consuming that
  report could fail on a target whose actual, scoped process exit passed.
  The scoped gate now replaces `run_outcome` the same way it already
  replaces `verdict`/`severity`; the full-library value moves to
  `full_run_outcome`.
- **`scan`'s legacy-scheme `run_outcome.gate` no longer counts a
  coverage-only failure as a compatibility break.** ADR-049 Phase 7's
  contract-coverage axis folds a `1` onto an otherwise-compatible `0` via
  `max()` on the *same* top-level `exit_code` `run_outcome` was reading —
  under the legacy scan scheme (whose own native codes are 0/2/4/5/6) a
  bare `1` is always that orthogonal contribution, never a real
  `ADDITION_QUALITY`-level compatibility gate. The writer now reads the
  report's own declared `contract_coverage_exit_contribution` (as
  `GateInfo.from_scan_report`'s raw-code fallback already does) and emits
  `gate: none` when confirmed, fail-closed to the prior behavior otherwise.
