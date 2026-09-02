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
