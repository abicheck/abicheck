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
- **A scan abort report's `run_outcome.gate` now preserves any real
  compatibility break found before the abort fired.** A late
  `BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR` persists its own prior
  `exit.compatibility_contribution` (`scan_abort_result_fields`), but the
  writer was still deriving `gate` from the abort's own unrelated top-level
  exit code (5/1, outside the 0/1/2/4 compatibility scheme), zeroing a
  real `abi_breaking` gate to `none`. The writer now reads the persisted
  contribution for an abort report the same way it already prefers a
  severity-scheme report's nested `diff.severity.exit_code`.
- **`ScanSetResult`'s `run_outcome.operational` no longer silently drops a
  member's `EVIDENCE_CONTRACT_ERROR` when a different member's stronger
  `API_BREAK`/`BREAKING` wins the set-level verdict.** `per_artifact` still
  recorded the aborted member, but `run_outcome` had no signal for it at
  all; it now folds `operational: evidence_contract_error` in whenever any
  member aborted that way, without touching `gate`.
- **`--artifact-set`'s `BUNDLE_INCOMPLETE` verdict no longer reads as a real
  `addition_quality` compatibility gate.** `run_scan_set()` floors its own
  exit code at `1` when the cross-library bundle audit itself never ran (a
  discovered member silently dropped out of resolution) — with no
  `report=` for the writer to read a real compatibility contribution from,
  that floor is now recognized as `OperationalStatus.EXTRACTION_ERROR`,
  matching `compatibility` already being `None` for the identical reason.
- **`GateInfo.from_report_data`/`from_scan_report` now fail closed
  (`_MalformedGate`) on a *present but unparseable* `run_outcome` block**,
  instead of silently falling through to the legacy `severity`/raw
  `exit_code` decode path — the same fail-closed handling the `severity`
  block itself already had. A corrupt, policy-blocked report could
  previously read as a clean legacy verdict once the structured-first
  reader landed.
- **`scan --format json`'s own budget-overflow/evidence-contract-error
  abort envelope (`cli_scan._emit_scan_abort_report`) now carries
  `run_outcome` too.** This is a fourth, independent scan-report writer —
  distinct from `ScanOutcome`/`ScanResult`/`ScanSetResult` — that claimed
  `scan_schema_version` 1.24 while never emitting the new structured block
  at all, forcing a consumer back onto legacy exit-code/sentinel decoding
  for exactly the operational failures this axis exists to represent.
