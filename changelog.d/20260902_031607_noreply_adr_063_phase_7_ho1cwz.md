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
- **A severity-scheme `scan --against` report's own top-level `run_outcome`
  is now consulted at all.** `GateInfo.from_scan_report`'s severity-scheme
  branch reads the nested `diff.severity` gate via a recursive call over
  just that nested object, which never carries the outer report's own
  `run_outcome` key — so it was silently skipped, unlike the equivalent
  `compare` report. It's now folded in and cross-checked (fail-closed on
  contradiction) the same way `compare`'s own `severity` block already is.
- **`compare-release`'s summary JSON (both the primary `--format json`
  document and the `--output-dir summary.json` sibling) now carries
  `run_outcome` too**, closing the gap between what `docs/use/output-
  formats.md` already documented ("every JSON report... the release
  fan-out") and what the two writers actually built.
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
- **`--artifact-set`'s `bundle_incomplete` state no longer disappears when
  a stronger member verdict wins.** `run_scan_set()` keeps a member's real
  `API_BREAK`/`BREAKING` as the reported verdict even when the cross-library
  bundle audit itself never ran — `run_outcome.operational` now surfaces
  `extraction_error` in that case too, not just when the sentinel
  `BUNDLE_INCOMPLETE` verdict itself is reported.
- **`GateInfo.from_report_data` now fails closed when `severity` and
  `run_outcome` individually parse but contradict each other** (e.g.
  `severity.exit_code: 0` alongside `run_outcome.gate: abi_breaking`) —
  previously the structured `.operational` fold ran without ever
  cross-checking `.gate` itself, so a corrupted-but-well-formed report
  could silently read as the greener `severity` result. A `--used-by`/
  `--required-symbol` scoped report is correctly exempted (its
  `severity.exit_code`/`run_outcome.gate` intentionally diverge whenever a
  contract-coverage/analysis-assurance floor applies), identified by the
  presence of `full_run_outcome`.
- **`compare-release`'s severity-scheme `--fail-on-removed-library` escalation
  now agrees with itself.** `run_outcome.gate` already escalated to
  `abi_breaking` for exit 8, but the sibling `severity.exit_code` field
  didn't — for a release whose ordinary findings otherwise contributed `0`,
  this was exactly the contradiction the `GateInfo.from_report_data` fix
  above now fails closed on, turning a legitimate escalation into an
  unavailable target for `aggregate`. `severity.exit_code` now escalates to
  `4` in the same case, mirroring `buildsource/check_report.py`'s
  `_escalate_removed_library_severity`'s identical per-library escalation.
- **A legacy report re-stamped to the current schema now gets a backfilled
  `run_outcome` too.** `augment_report()` already upgraded an older report's
  `exit` block(s) before claiming the current schema version
  (`check_report_exit_backfill.backfill_exit_block_fields`); it never did
  the equivalent for `run_outcome`, so a pre-existing report re-checked
  through `check` silently claimed a schema version whose promised block it
  didn't carry. The new `backfill_run_outcome` (mirrors the exit-block
  sibling's shape) synthesizes it from whichever fields the old report
  shape actually has — reusing `run_outcome_dict_for_scan`/`_for_release`
  for a scan/release-shaped report, and the legacy verdict/severity-exit-code
  mapping otherwise — and is a no-op for a report that already carries one.
- **The release fan-out's `run_outcome.compatibility` no longer goes `null`
  when one library in the set errored.** `_format_release_json`/
  `_write_release_summary_file` fed the raw top-level `worst_verdict` (which
  can be the operational sentinel `"ERROR"`/`"not_comparable"`, not a real
  `Verdict`) straight into `run_outcome_dict_for_release`, so one library
  failing to dump/extract could mask a genuine `BREAKING` verdict on a
  *different* library in the same release. The new
  `_release_completed_compatibility_verdict` recomputes the worst REAL
  verdict across the library results (and the release-global verdict),
  excluding those two sentinels, and only that goes into
  `run_outcome.compatibility` — `verdict`/`run_outcome.operational` are
  unaffected.
- **The scoped-report exemption in `GateInfo.from_report_data`'s
  `severity`/`run_outcome` contradiction check can no longer be triggered by
  an arbitrary `full_run_outcome` key.** The exemption used to be earned by
  mere key presence (`"full_run_outcome" in data`), so a corrupted or
  partially rewritten *unscoped* report could pair a genuinely contradictory
  `severity`/`run_outcome` pair with any `full_run_outcome` value (even
  `null`) and have the authoritative fail-closed check silently disabled.
  The new `_has_valid_full_run_outcome` requires it to parse as a
  well-formed `RunOutcome` block — the only shape
  `cli_compare_fold._swap_in_scoped_run_outcome` ever actually produces —
  before granting the exemption.
- **`scan`'s `run_outcome.assurance` is no longer always `null`.** Every
  scan writer (`ScanOutcome`, `ScanResult`, `ScanSetResult`) built its
  `RunOutcome` with `assurance=None` unconditionally, even when the
  report's own `diff.analysis_assurance` block was fully computed
  (`cli_scan_baseline.py`'s `analysis_assurance_report_dict`). The three
  scan writers now thread that already-serialized block through, and
  `analysis_assurance_dict` accepts it directly (alongside the live
  `AnalysisAssurance` object `compare`'s own writers pass) since scan never
  holds the live object at the point it builds `run_outcome`.
- **A late scan abort's `run_outcome.compatibility` no longer reads `null`
  beside a real `gate`.** A `BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`
  abort that already found a real break restores `gate` from its
  persisted `exit.compatibility_contribution` (an earlier fix in this same
  fragment), but `compatibility` still parsed from the abort's own
  sentinel verdict string and stayed `null` regardless — contradicting the
  documented rule that `null` means nothing was compared. A `2`/`4`
  persisted contribution (the two unambiguous cases) now maps to the
  matching real `Verdict`; a `0` contribution stays `null`, since it can't
  be told apart from `NO_CHANGE`/`COMPATIBLE`/`COMPATIBLE_WITH_RISK`.
- **The legacy-report backfill now reads a pre-1.24 native scan abort
  report's nested `diff.exit`, not just the top-level `exit` key.**
  `cli_scan._emit_scan_abort_report`'s own persisted JSON shape nests the
  abort's preserved exit decision under `diff.exit` — a different envelope
  than `service_scan.ScanResult.report`'s top-level `exit`, which
  `scan_report_abort_compatibility_contribution` reads. Without also
  checking the nested shape, `backfill_run_outcome` silently downgraded an
  already-found ABI break to the abort's own operational-only exit floor.
- **`full_run_outcome` is now defined in the published report schema.**
  Scoped (`--used-by`/`--required-symbol`) compare JSON has emitted this
  field since `cli_compare_fold._swap_in_scoped_run_outcome` landed
  earlier in this PR, but neither copy of `compare_report.schema.json`
  declared it — unlike the analogous `full_severity`, which it mirrors.
  Also registered `run_outcome`'s fact sources under the existing
  `output-formats` documentation topic (`docs/_meta/topics.yaml`).
- **The scoped-report exemption now requires the complete scoped shape, not
  just a well-formed `full_run_outcome`.** A well-formed-but-unrelated
  `full_run_outcome` value alone still earned the exemption — the real
  writer (`cli_compare_fold._ScopedFold.into_json`) never emits it without
  also unconditionally emitting `full_verdict` and at least one of
  `used_by`/`required_symbol_contract`. `_has_valid_full_run_outcome` now
  requires all three markers together.
- **The release fan-out's `run_outcome.compatibility` no longer reports the
  dishonest `"NO_CHANGE"` floor when nothing was actually compared.** When
  every library result is `ERROR`/`not_comparable` and no bundle/matrix
  comparison ran either, `_release_completed_compatibility_verdict`'s own
  `"NO_CHANGE"` seed value previously leaked through unmodified, falsely
  claiming a clean completed comparison. It now takes an explicit
  `release_global_ran` flag and returns `None` in that case —
  `_release_global_verdict`'s own floor default is indistinguishable from a
  genuine no-change result by string value alone, so that flag has to be
  passed explicitly rather than inferred from the verdict string.
- **A scoped report's `GateInfo` no longer double-counts a coverage/
  assurance-only contribution as a compatibility break.** The scoped
  exemption retained `severity.exit_code` (which folds in the orthogonal
  contract-coverage/analysis-assurance floors for a scoped report,
  unlike an unscoped report's `severity.exit_code`) unchanged, so a
  scoped report whose only contribution was coverage/assurance
  (`run_outcome.gate: none`) still built a `GateInfo` with
  `exit_code=1`/`blocking=True` — aggregation then counted the target as
  a compatibility blocker even though that same contribution is folded
  onto the aggregate's own orthogonal axis independently. The exemption
  now rebuilds `GateInfo` purely from `run_outcome.gate` (then folds only
  `operational`, same as the unscoped path), restoring the invariant
  every other `GateInfo` already satisfies.
- **`run_outcome.gate` on a real `compare` report is no longer computed
  twice.** `report/run_outcome.py`'s `run_outcome_dict_for_diff_result`
  called `gate_decision_for_result` itself — a second, independent policy
  evaluation during rendering, contrary to `report/AGENTS.md`'s
  "a renderer... cannot calculate a new gate decision." It now takes the
  caller's already-computed `GateDecision` (threaded through
  `reporter.py`'s four JSON entry points and
  `render_json_with_side_facts`) instead of recomputing it, closing the
  gap where the two could silently drift from each other as either
  evolves.
- **`--artifact-set`'s `run_outcome.compatibility` no longer goes `null`
  when a completed member's result is followed by a different member's
  `BUDGET_OVERFLOW`.** `_aggregate_scan_set_verdict`'s own step 1 (any
  member `BUDGET_OVERFLOW` → the whole set is `BUDGET_OVERFLOW`) is
  correct for the set's own *reported* `verdict`/`exit_code`, but
  `ScanSetResult.to_dict()` derived `run_outcome` from that same
  sentinel-bearing rollup alone, silently erasing an already-completed
  member's real result from the independent compatibility axis —
  including a completed-but-*clean* member (`NO_CHANGE`/`COMPATIBLE`/
  `COMPATIBLE_WITH_RISK`), which a bare exit-code contribution can't tell
  apart (all `0`) from "nothing was compared." `run_outcome_dict_for_scan`
  gained a `member_verdicts` fallback tier (the raw per-member + bundle
  verdict strings, resolved via the new `_worst_real_verdict` to the
  worst REAL one), used only when no `report=` supplies a compatibility
  contribution, so a completed member's result survives even though the
  set itself never finished.

- **A legacy release report's `run_outcome.gate` no longer silently reads
  `none` for a real `BREAKING`/`API_BREAK` verdict.** `augment_report`
  previously ran `backfill_exit_block_fields` before `backfill_run_
  outcome`, so a legacy release report's `exit` block (present or absent)
  already had every `*_contribution` field — including
  `compatibility_contribution` — unconditionally defaulted to `0` by the
  time the run_outcome backfill saw it, indistinguishable from a real,
  confirmed-clean release. The two backfills now run in the opposite
  order, and the release branch falls back to `severity.exit_code` or the
  legacy verdict mapping (mirroring the native-compare branch) whenever
  the *original* `exit` block never carried `compatibility_contribution`
  at all (CodeRabbit review).
- Corrected `docs/use/output-formats.md`'s `run_outcome` summary: it now
  distinguishes `scan`'s schema 1.24 from `compare`/release's 2.48, states
  that a not-comparable refusal has `compatibility: null` alongside
  `operational: "not_comparable"`, and clarifies that any non-`none`
  `operational` value (not just the four named ones) means no
  compatibility verdict was produced (CodeRabbit review).
- `test_allowed_paths_are_exempt_from_the_repo_wide_scan` now asserts
  `ALLOWED_RELATIVE_PATHS` is non-empty before looping over it, so the
  four-boundary-encoder check it documents can't silently pass vacuously
  if the allowlist were ever emptied (CodeRabbit review).

### Changed

- `abicheck/report_run_outcome.py` moved to `abicheck/report/run_outcome.py`
  — new report-field code belongs to the `report` responsibility package
  per this repo's own routing table, not a new flat `report_*.py` module.
