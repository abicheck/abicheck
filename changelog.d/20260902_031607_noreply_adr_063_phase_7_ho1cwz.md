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
- **`scan`'s `run_outcome.assurance` is no longer always `null` for a
  single-binary result.** `ScanOutcome`/`ScanResult` built their
  `RunOutcome` with `assurance=None` unconditionally, even when the
  report's own `diff.analysis_assurance` block was fully computed
  (`cli_scan_baseline.py`'s `analysis_assurance_report_dict`). Both now
  thread that already-serialized block through, and `analysis_assurance_
  dict` accepts it directly (alongside the live `AnalysisAssurance` object
  `compare`'s own writers pass) since scan never holds the live object at
  the point it builds `run_outcome`. **`ScanSetResult` (a `scan
  --artifact-set` run) still always serializes `assurance: null`** (Codex
  review, fresh evidence) — it calls `run_outcome_dict_for_scan()` with no
  `report=`/assurance input at all, so there is no single per-set analysis-
  assurance block to thread even though individual members may carry their
  own; a set-level rollup is a real design decision (how to merge several
  members' assurance blocks) left open rather than attempted here.
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
- **A legacy release report's backfilled `run_outcome.compatibility` no
  longer goes `null` just because the top-level `verdict` is the
  `"ERROR"`/`"not_comparable"` operational sentinel.** `check_report_
  run_outcome.backfill_run_outcome`'s release branch now recovers the
  worst REAL per-library verdict from `out["libraries"]` (via the new,
  shared `policy.outcome.worst_real_verdict` helper) for both the
  `compatibility` axis and the `compatibility_contribution` exit-code
  fallback, mirroring the sentinel-excluding precision a native release
  writer already gets from `cli_compare_release_helpers._release_
  completed_compatibility_verdict` (Codex review).
- `policy/outcome.py` now imports `Verdict` directly from its canonical
  owner (`model.change_catalog.registry`) rather than through the
  `change_registry_types` back-compat shim, so this new module doesn't add
  a fresh caller onto the legacy re-export path (Codex review).
- **A legacy `--artifact-set` scan report's backfilled `run_outcome.
  compatibility` no longer goes `null` just because the set-level verdict
  is `BUDGET_OVERFLOW`/`BUNDLE_INCOMPLETE`.** A pre-1.24 report (before
  `ScanSetResult.to_dict()` carried `run_outcome` itself) has no `diff`/
  `exit` block to read a compatibility contribution from; `backfill_run_
  outcome` now recovers the member/bundle verdicts from the legacy
  `per_artifact`/`bundle_verdict` envelope and passes them through as
  `member_verdicts`, so an already-completed member result survives
  upgrade the same way it already does for a native writer (Codex review).
- **A pre-2.48 synthetic Action report (`build_operational_error_report`/
  `build_bootstrap_report`/`build_new_target_report`'s own shape, from
  before those builders carried `run_outcome` themselves) no longer loses
  its one real axis on backfill.** The generic-report fallback previously
  defaulted every such report to `operational: none`/`lifecycle:
  existing`; `backfill_run_outcome` now recognizes the `operational_
  errors`/`baseline_bootstrap`/`baseline_new_target` marker keys each
  builder writes and reuses `synthetic_run_outcome` for them, so a
  resolve-baseline failure keeps `operational: extraction_error` and a
  bootstrap/new-target pass keeps its `lifecycle` (Codex review).
- **A pre-2.48 legacy compare report's backfilled `run_outcome.assurance`
  no longer contradicts its own existing `analysis_assurance` block.**
  The generic-report fallback previously hard-coded `assurance: None`;
  it now passes the report's own already-serialized top-level
  `analysis_assurance` block through unchanged when present (Codex
  review).
- **A legacy `--artifact-set` scan report's `bundle_incomplete`/an
  `EVIDENCE_CONTRACT_ERROR` member no longer disappears when the set's
  root verdict is a real, stronger `BREAKING`/`API_BREAK`.** The scan-set
  backfill now derives these two independent operational signals from the
  legacy envelope unconditionally (mirroring `ScanSetResult.to_dict()`'s
  own unconditional wiring for a native writer), instead of only
  recovering member/bundle *compatibility* verdicts (Codex review).
- **A legacy release report's `exit.compatibility_contribution` is no
  longer trusted merely for existing.** A present-but-malformed value (a
  string, `None`, a bool) previously bypassed the `severity.exit_code`/
  legacy-verdict fallback and was silently normalized to `0` by
  `run_outcome_dict_for_release`'s own `_int_contribution`, turning a
  corrupted `BREAKING` report into a falsely clean target; the backfill
  now falls back the same way it already does for a missing key (Codex
  review).
- **`workflows/aggregate/gate.py`'s orthogonal-axis fold no longer drops
  a real operational category beside a stronger compatibility gate.**
  `operational_status_exit_code` caps every non-`NONE` member at exit `1`,
  so beside e.g. `gate: abi_breaking` (exit `4`) the numeric `max()` never
  raised `exit_code` and the whole `replace()` — including the category
  union — was skipped, silently hiding that part of the run (e.g. an
  `EVIDENCE_CONTRACT_ERROR` scan-set member) never completed. The category
  union is now independent of whether the numeric exit code itself needs
  raising (Codex review).
- **The scoped-gate exemption in `workflows/aggregate/gate.py` now
  validates the complete `full_run_outcome` shape, not just that it
  parses.** `RunOutcome.from_dict` is a deliberately lenient reader (only
  `gate`/`operational` must parse; `compatibility`/`lifecycle` degrade
  silently) for its other callers, so a minimal, forged two-key
  `full_run_outcome` alongside `full_verdict`/`used_by` still earned the
  exemption on an otherwise-unscoped, corrupted report. The exemption now
  additionally requires every key `$defs.run_outcome`
  (`compare_report.schema.json`) declares required to actually be present
  (Codex review).
- **A legacy release report's `exit.compatibility_contribution` is now
  also rejected when it's an out-of-scheme integer** (e.g. `99`), not just
  a non-int/bool value — falling back to `severity.exit_code`/the legacy
  verdict mapping the same way a missing key already does, instead of
  letting `run_outcome_dict_for_release`'s own scheme-membership check
  silently normalize it to `0` (Codex review).
- Corrected `docs/use/output-formats.md`'s `run_outcome` summary: `
  operational` is described as an independent axis rather than proof that
  no compatibility result exists — a late budget/evidence abort can retain
  an already-completed verdict, and a release/scan set can report one
  member's real result alongside a different member's independent
  operational failure (Codex review).
- **`--fail-on-removed-library`'s exit-8 escalation now updates
  `run_outcome.gate` even when the report carries no `severity` block at
  all.** `_escalate_removed_library_severity`'s own `severity`-gated early
  return previously skipped the `run_outcome.gate` escalation too on a
  pre-severity legacy compare-release report -- deferred aggregation (which
  reads structured-first) trusted the unescalated `gate: none` even though
  `policy_gate_decision` itself correctly recorded `fail`. The two
  escalations are now independent (Codex review).
- **The `severity.exit_code` fallback for a legacy release's
  `compatibility_contribution` is now also range-checked.** The out-of-
  scheme-integer fix above closed the gap for `exit.compatibility_
  contribution` itself, but its own fallback to `severity.exit_code` had
  the identical, unchecked gap — a corrupted `severity.exit_code: 99`
  forwarded unchanged instead of falling through to the legacy verdict
  mapping (Codex review).
- **`workflows/aggregate/gate.py`'s structured-first `run_outcome` reads —
  both the ordinary top-level one and the scoped exemption's
  `full_run_outcome` — now validate the complete schema shape, not merely
  that `RunOutcome.from_dict` parses.** That reader is deliberately lenient
  for its other callers (`compatibility`/`lifecycle` degrade silently
  instead of failing, and `schema_version`/`assurance` aren't checked at
  all), so a minimal, schema-incomplete `run_outcome` (e.g. `{"gate":
  "none", "operational": "none"}`) previously read as authoritative — most
  dangerously when no `severity` block exists at all to cross-check
  against. Both reads now share one strict validator,
  `_is_schema_valid_run_outcome`, checking every required key's declared
  type/enum against `$defs.run_outcome` (Codex review, two rounds).
- **A legacy scan report's missing/malformed top-level `exit_code` no
  longer defaults to `0` unconditionally.** For a genuinely `BREAKING`/
  `API_BREAK` verdict, that default read as a false `gate: none` once
  forwarded (a report this corrupted has no `severity`/`exit` block for
  `report=` to find a real contribution in either). The fallback is now
  derived from the real verdict via the same legacy-verdict mapping the
  release/compare branches already use (Codex review).
- **A legacy release report's `operational` axis is now inferred from the
  top-level/member `"ERROR"`/`"not_comparable"` sentinels when the newer
  `operational_error_contribution`/`not_comparable_contribution` exit
  keys are absent entirely** (a release produced before those fields
  existed). Forwarding the legacy `exit` block unchanged previously left
  `run_outcome.operational: none` despite a library having failed or
  refused comparison (Codex review).
- **A pre-2.48 standalone comparability refusal (`report.not_comparable`'s
  own `verdict: null` + `reason.kind` shape, from before that writer
  carried `run_outcome` itself) no longer loses its `not_comparable`
  status on backfill.** The generic-report fallback previously hard-coded
  `operational: none` for this shape, contradicting the current native
  writer (Codex review).
- **`compare-release --output-dir`'s own `summary.json` shape
  (`libraries`+`unmatched_old`, but no `old_dir`/`new_dir`) is now
  recognized by the release-report backfill branch.** The prior shape
  check (`"old_dir" in out`) missed this writer entirely and fell through
  to the single-compare fallback, discarding a completed per-library
  verdict alongside the release's own operational failure (Codex review,
  fresh evidence).
- **`gate.py`'s scoped-run (`--used-by`/`--required-symbol`) gate
  exemption no longer trusts a `full_verdict`/`used_by`/
  `required_symbol_contract` that is present-but-explicitly-`null`.** The
  real writer (`_ScopedFold.into_json`) never emits these keys as null —
  only omits them — so a forged report carrying an explicit null for
  `full_verdict` (which fails `Verdict` parsing) or for both scoped
  markers previously still earned the exemption, letting an
  otherwise-BREAKING severity block bypass the gate check (Codex review,
  fresh evidence).
- **`compare --old-bundle-facts`'s own JSON summary now carries
  `run_outcome`, closing the one remaining exception to this repo's
  "every compare/release JSON report carries `run_outcome`" contract**
  (Codex review, fresh evidence). Reuses `report.run_outcome.
  run_outcome_dict_for_diff_result` (already used by `reporter.py`'s own
  JSON entry points) rather than a new `policy` import, since
  `frontends` may not import `policy` directly.
- **`aggregate`'s `_load_report_file` now recognizes `scan`'s two
  comparability-refusal sentinels, `NOT_COMPARABLE` and
  `BUNDLE_INCOMPLETE`, as blocking aborts too** (`_scan_abort_categories`
  previously only covered `BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`).
  Neither sentinel is a `Verdict` enum member, so without this a scan
  that refused to compare, or whose cross-library bundle audit never
  ran, previously read as an ordinary unavailable/verdictless report —
  silently discarding a blocking `run_outcome.operational` and letting a
  warn/optional/tolerated-unexpected target policy pass it (Codex
  review, fresh evidence).
- **`_load_report_file` now dispatches a scan-shaped document
  (`scan_schema_version` present) to `GateInfo.from_scan_report` before
  `GateInfo.from_report_data`, not after.** A native `scan` report
  carries its own top-level `run_outcome` (ADR-063 Phase 7) but no
  top-level `severity` block — `from_report_data`'s own "no `severity`
  block, read `run_outcome` alone" branch previously returned straight
  from the root `run_outcome` without ever reaching `from_scan_report`,
  the only reader that validates/cross-checks a severity-scheme scan's
  nested `diff.severity` gate against it. A nested severity exit 4 paired
  with a root `run_outcome.gate: "none"` was accepted as a nonblocking
  gate instead of failing closed (Codex review, fresh evidence).
- **A `compare-release` summary's own lowercase `"not_comparable"`
  sentinel (ADR-050 D2 — a real string, distinct from `scan`'s uppercase
  `NOT_COMPARABLE` and from a native `compare`'s `verdict: null` +
  `reason.kind` shape) is now recognized as a blocking refusal too.**
  Previously caught by neither special-case branch, it fell through to
  the generic "report carried no ABI verdict" unavailable reading,
  silently discarding `run_outcome.operational: "not_comparable"` and
  letting a warn/optional/tolerated-unexpected target policy pass a
  refused release comparison. Reuses `GateInfo.from_report_data` (Codex
  review, fresh evidence).
- **`BUNDLE_INCOMPLETE` now preserves the worst completed member's real
  compatibility verdict instead of forcing `verdict: None` the way a
  true scan abort does.** Unlike `BUDGET_OVERFLOW`/
  `EVIDENCE_CONTRACT_ERROR`/`NOT_COMPARABLE` (where nothing was ever
  compared), `BUNDLE_INCOMPLETE` fires only after every member scanned
  cleanly and just the cross-library bundle audit itself never ran —
  `run_outcome.compatibility` already carries that real result, and
  discarding it wrongly reported the target as unavailable/unanalyzed
  even though it had a real, already-established outcome (Codex review,
  fresh evidence).
- **Three more forced-blocking `_load_report_file` branches now read a
  report's real completed compatibility axis from `run_outcome`
  wherever available, instead of always fabricating a synthetic
  verdict/exit code** (Codex review, fresh evidence, second round):
  - The operational `"ERROR"` branch (a release library that failed to
    dump/extract/compare) previously always forced `Verdict.BREAKING`,
    discarding a *sibling* library's real completed result (e.g.
    `COMPATIBLE_WITH_RISK`) that `run_outcome.compatibility` already
    carries. The gate's own exit-4 floor stays unconditional either way.
  - `run_outcome.compatibility` is now read unconditionally for all four
    scan abort sentinels, not only `BUNDLE_INCOMPLETE` — a *late*
    `BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR` can carry a real
    completed verdict too, reconstructed by the writer from whatever
    finished before the abort fired. Pure widening: a report where
    nothing genuinely completed still reads `None`, unchanged.
  - A native `compare`'s own `verdict: null` + `reason.kind` refusal
    (`report.not_comparable.not_comparable_document()`) now reads its
    own top-level `run_outcome` when present, rather than always forcing
    `Verdict.BREAKING`/exit 4 — the orthogonal fold floors at exit 1
    ("only the operational axis blocks"), matching every other
    operational-failure sentinel in this module. A report predating
    `run_outcome` (schema < 2.48) still gets the original forced
    exit-4/BREAKING fallback, unchanged.
- **`_run_outcome_compatibility_verdict` now requires the whole
  `run_outcome` block to be schema-valid before trusting
  `compatibility`, not merely that `compatibility` itself parses**
  (CodeRabbit review, fresh evidence): a forged/truncated
  `{"run_outcome": {"compatibility": "BREAKING"}}` — missing
  `gate`/`operational`/`schema_version`/`lifecycle` — previously still
  earned the opportunistic verdict-recovery the fixes above added,
  letting a bare compatibility string alone make a genuinely incomplete
  abort report read as analyzed and preserve its findings/digest. A
  present-but-invalid block is treated the same as an absent one (falls
  back to the legacy synthetic behavior), since this helper is a pure
  opportunistic enrichment and every call site already has its own
  independently fail-closed gate.
- **`check_report_run_outcome.py`'s legacy release backfill no longer
  fabricates a `"NO_CHANGE"` compatibility floor when neither a
  completed library nor the root verdict names a real result**
  (CodeRabbit review, fresh evidence): `run_outcome_dict_for_release`'s
  own docstring states the contract explicitly (`compatibility` stays
  unknown, never the dishonest `"NO_CHANGE"` floor) — the backfill now
  honors it by passing `None` instead.
- **The `_load_report_file` refusal branch (native `compare`'s own
  `verdict: null` + `reason.kind` shape) now catches `_MalformedGate`
  from its own `run_outcome` read** (Codex review, fresh evidence):
  `_run_outcome_gate_and_operational` raises that exception for a
  PRESENT but schema-invalid `run_outcome`, and every other branch
  that calls it wraps the call in exactly this `try`/`except` — this
  refusal branch previously called it bare, so a corrupt block aborted
  the whole aggregation command instead of landing the target
  unavailable with a malformed-gate reason like every sibling case.
- **A legacy scan report's `exit_code` fallback now also fires for a
  real int outside `scan`'s legacy exit scheme `{0, 2, 4, 5, 6}`, not
  only a missing/non-int value** (Codex review, fresh evidence):
  `run_outcome_for_scan_fields` itself silently floors an out-of-scheme
  `compat_exit_code` to `0` (folding it into `operational` instead) —
  correct for a code that genuinely is operational-only, but a bogus
  code like `99` discarded a real `BREAKING`/`API_BREAK` verdict's own
  gate contribution, backfilling `run_outcome.gate: "none"` for a
  report whose `verdict` string still said otherwise.
- **The legacy release backfill's `not_comparable`/`ERROR` operational
  inference now overrides a declared-but-invalid contribution, not
  only a missing key** (Codex review, fresh evidence, second round): a
  legacy/malformed `exit` block can already carry `not_comparable_
  contribution: 0` (or any non-`1` value) even though a member's own
  verdict genuinely is the `not_comparable` sentinel — the old
  key-presence-only check skipped inference entirely for that shape,
  letting the refusal normalize to `operational: none`.
- **A `compare-release` summary's lowercase `"not_comparable"` refusal now
  falls back to a blocking gate, and preserves a completed sibling's
  findings, instead of reading gate-less/unavailable** (Codex review, fresh
  evidence): `GateInfo.from_report_data` legitimately returns `None` for a
  genuinely pre-2.48 legacy release report (neither `severity` nor
  `run_outcome`) that still refused the comparison — that `None` previously
  read as "no gate decision available," letting a warn/optional/
  tolerated-unexpected target policy pass a refused comparison; it now
  falls back to the same forced exit-4/blocking `GateInfo` the native
  `compare` refusal branch already uses for this shape. Separately, when a
  sibling library or the global bundle/matrix comparison completed even
  though this library refused (`run_outcome.compatibility` non-null), real
  `bundle_findings`/`matrix_findings` the release writer can still emit in
  this state were unconditionally dropped — now preserved, mirroring the
  `ERROR`/scan-abort branches' own incomplete-findings preservation.
- **A release operational-ERROR report whose `run_outcome.compatibility` is
  legitimately JSON `null` no longer has a `Verdict.BREAKING` fabricated for
  it** (Codex review, fresh evidence, third round): the operational-ERROR
  branch in `_load_report_file` only distinguished "recovered a real
  verdict" from "recovered nothing," treating a valid, schema-complete
  `run_outcome` block whose `compatibility` genuinely is `null` (e.g.
  `build_operational_error_report`'s own extraction-failure report:
  `compatibility: null`, `gate: none`, `operational: extraction_error`) the
  same as a report with no `run_outcome` block at all — fabricating an
  ABI-break verdict and an "analyzed" target count for a comparison that
  never ran. A new `_has_valid_run_outcome_block` helper (moved to
  `workflows/aggregate/gate.py` alongside `_run_outcome_compatibility_
  verdict`, to keep `load.py` under the architecture gate's new-file-size
  cap) now distinguishes the two cases; the gate's own exit-4/blocking floor
  stays unconditional either way.
- **The legacy release backfill's candidate-verdict list now also includes
  `bundle_verdict`/`matrix_verdict`, not only the root and per-library
  verdicts** (Codex review, fresh evidence): a pre-2.48 release whose
  top-level verdict is `ERROR`/`not_comparable` and whose every per-library
  entry carries only that same sentinel can still have a completed GLOBAL
  bundle-audit or cross-profile matrix comparison recording a real verdict —
  omitting these two fields backfilled `run_outcome.compatibility: null` for
  a report that in fact had a genuine, completed comparison to preserve,
  denying the release-refusal findings-preservation fix above any non-null
  verdict to key off.
- **A `compare-release` ERROR report carrying a *present-but-schema-invalid*
  `run_outcome` no longer fabricates `Verdict.BREAKING`** (Codex review,
  fresh evidence): the operational-ERROR branch's malformed-vs-absent
  distinction only covered "valid block, null compatibility" — a present
  but schema-invalid block (missing required keys/enum values) still read
  `False` from `_has_valid_run_outcome_block` the same as a genuinely
  absent one, silently falling through to the legacy fabricated-`BREAKING`
  path instead of failing the target unavailable/malformed the way every
  other structured-`run_outcome` reader in this module already does. The
  branch now calls `_run_outcome_gate_and_operational` first (raising
  `_MalformedGate`, caught and turned into the same fail-closed
  unavailable/malformed `_LoadedReport` shape the null-verdict and release
  refusal branches already return) before recovering a real verdict.
- **A legacy scan's nested `exit.compatibility_contribution` is now
  rejected when out-of-scheme, not just when missing/non-int** (Codex
  review, fresh evidence): `scan_report_abort_compatibility_contribution`
  previously accepted any int, so a legacy scan carrying a valid root
  `exit_code: 4` alongside a corrupted nested `compatibility_contribution:
  99` had that bogus value normalized straight to `gate: none` by
  `run_outcome_for_scan_fields`, silently turning a real `BREAKING` scan
  nonblocking. Now restricted to the shared 0/1/2/4 scheme
  (`_GATE_EXIT_CODE.values()`); an out-of-scheme value falls back to the
  root verdict/exit code the same way a missing value already does.
- **A `scan` abort report (`BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`/
  `NOT_COMPARABLE`/`BUNDLE_INCOMPLETE`) now honors a valid `run_outcome.
  gate` when its legacy `diff.exit`/member contribution blocks are absent
  or stale** (Codex review, fresh evidence): the scan-abort branch
  recovered `run_outcome.compatibility` but computed the gate solely from
  `_scan_abort_prior_exit`, so a current-schema budget-overflow report
  with `run_outcome.compatibility: "BREAKING"`/`run_outcome.gate:
  "abi_breaking"` loaded as exit 1 with only `budget_overflow`, losing the
  real exit-4/`abi_breaking` category the structured gate recorded. New
  `_run_outcome_gate_exit_and_category` (`gate.py`) reads and folds it in
  via the same `max()`/category-union machinery. Unlike the ERROR/release
  refusal branches, this branch's own gate is unconditional either way (the
  `COVERAGE_INCOMPLETE_EXIT` floor), so a present-but-invalid `run_outcome`
  here degrades to "nothing to add" rather than failing the whole target
  unavailable — preserving this branch's existing fail-open behavior for a
  malformed block (pinned by
  `test_bundle_incomplete_with_a_truncated_run_outcome_does_not_recover_a_
  verdict`).
- **A `compare-release` operational-ERROR report's recovered gate now
  preserves the real compatibility category too, not only the numeric
  exit** (Codex review, fresh evidence): when one member errors after a
  sibling produces a real `BREAKING` result, `run_outcome.compatibility`
  was already loaded correctly, but the returned `GateInfo.blocking_
  categories` hard-coded only `("operational_error",)`, discarding the
  recorded `run_outcome.gate: "abi_breaking"` — the aggregate report hid
  the real compatibility blocker even though the numeric exit was already
  correct at 4. Now unions the recovered gate category in via the same
  `_run_outcome_gate_exit_and_category` helper the scan-abort fix above
  introduced.

### Changed

- `abicheck/report_run_outcome.py` moved to `abicheck/report/run_outcome.py`
  — new report-field code belongs to the `report` responsibility package
  per this repo's own routing table, not a new flat `report_*.py` module.
