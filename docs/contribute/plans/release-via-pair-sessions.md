---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Release compare via pair sessions

**Owner ADRs:** [ADR-061](../adr/061-responsibility-package-architecture.md) (orchestration
belongs in `workflows/`), [ADR-063](../adr/063-one-semantic-pipeline.md)
("one model, any cardinality"), [ADR-065](../adr/065-comparison-scope-selection-and-completeness.md)
(membership, scope selection and completeness).
**Depends on:** the `ExitDecision` owner refactor (PR #1838) and the compare-session refactor
(PR #1840, continuing [Compare orchestration out of the CLI](compare-orchestration-workflow-seam.md),
whose slice S4 also covers release dispatch and dry-run). Both have merged.
**Status:** Phase 1 done (PR #1837). Phase 2 in progress: P3, P9's items 10 and 12, and P2's
gate derivation are done (see the Phase 2 table). P1 is re-scoped below. Line references in the
duplication list are as of Phase 1; re-check them before each slice.
**Effort:** L · **Risk:** high. `compare DIR DIR` gates releases. Every slice must leave
outputs and exit codes byte-identical.

## Problem

A directory or package `compare` ("compare-release") and a single-pair `compare` already share
their *semantic* core. Each member goes through `workflows/member_compare.compare_member`, which
calls `run_compare_request`. That is the typed scalar pipeline: `classify_compare_pair` →
`compare_finalize.finalize_classified_pair`. `tests/test_release_compare_workflow.py` pins this.
So the remaining problem is not a second detector or classifier. It is everything around the
member. The release path has its own copy of each step a pair session already owns:

```
cli_compare_helpers.run_compare (split at the directory/package branch)
  -> commands/compare_routing._dispatch_release_compare
  -> cli_compare_release.compare_release_cmd          (757 lines)
  -> frontends/cli/release_compare_request            (early policy load, plan resolve)
  -> workflows/release_request.resolve_release_compare_plan
  -> workflows/release_compare.execute_release_compare (796 lines)
       -> release_members -> member_compare.compare_member -> run_compare_request
  -> frontends/cli/release_member_reporter            (per-member ledger and JSON)
  -> frontends/cli/release_summary._finalize_release_output
  -> frontends/cli/release_report.render_release_summary
  -> report/release_outcome_projection.project_release_outcome
  -> frontends/cli/release_exit._exit_compare_release
       -> policy/release_exit_decision
```

The scalar CLI follows a third route: `cli_resolve._resolve_compare_pair` →
`compare_classify.classify_resolved_pair` → `frontends/cli/compare_result._report_compare_result`.
It never calls `run_compare_request`. So today a release member is scored like a **typed-API
pair**, not like a **scalar CLI pair**. The two agree only because the F2 route-parity harness
holds them together.

## Goal and acceptance criteria

A release runs every member through **the same pair session** that a scalar `compare` uses. It
is the same object (from the compare-session refactor), not a parallel builder that happens to
call the same classifier. The release path keeps only three release-specific concerns:

1. **Membership and matching**: discovery, keying, pairing, selection (`release_inputs`,
   `release_scope`, `binary_utils.build_match_map`).
2. **Completeness**: ADR-065 states, proven removal or addition, the scope floor
   (`release_scope_notices`, `policy/scope_completeness`).
3. **Aggregation**: release-global bundle and probe-matrix findings, the worst-of fold, and the
   release summary document.

Acceptance:

- No release module re-implements operand normalization, policy or suppression loading,
  severity or gate resolution, a per-member exit decision, per-member report rendering, or the
  `-o` export loop. Each of the duplications listed below either names one owner or is deleted.
- Dry run and real run resolve membership through one function, so the preview cannot list a
  pairing the run would not make.
- Zero behavior change. These all stay green, unmodified:
  - `tests/test_release_one_member_property.py`
  - `tests/test_compare_cardinality_invariance.py`
  - `tests/test_release_compare_workflow.py`
  - the F2 route and release-member parity suites
  - `tests/test_exit_code_integrity.py`
  - `tests/test_exit_precedence_matrix.py`
  - the golden tests

## Step map: release step → pair equivalent

Status key: **S** means both paths already call one owner. **D** means the same logic is written
twice. **R** means release-only, which is legitimate.

| # | Step | Release site | Pair equivalent | Status | Phase 2 target |
|---|---|---|---|---|---|
| 1 | CLI parse and option fold | `cli_compare_helpers.run_compare` directory/package branch; `compare_routing._dispatch_release_compare` (format allow-list); `cli_compare_release.compare_release_cmd` (~60 forwarded kwargs) | `run_compare` scalar branch, `_normalize_compare_options`, `model/sided_inputs.resolve_per_side_inputs` | Config load (`resolve_project_compare_config`) is S. The option fold is D, and the release branch skips `_normalize_compare_options` | One option fold produces the session request. The release receives the folded request, not kwargs |
| 2 | Request build | `ReleaseCompareRequest` / `ReleaseCompareOptions` (`cli_compare_release`); `release_members.release_parent_request`; `release_member_request.member_compare_request` | `workflows/compare/invocation.build_resolved_compare_request` | D: two request builders. The member request leaves `severity_preset` at its `CompareRequest` default (the release gate is applied afterwards) | A member request is the pair session's request plus a `MemberDelta`, nothing else |
| 3 | Plan resolve | `release_request.resolve_release_compare_plan` → `release_inputs.prepare_release_inputs` / `resolve_release_side` | `cli_resolve._resolve_compare_pair`; `workflows/compare/resolve.resolve_compare_request` | R, except header composition (`compose_sided_paths`), which is S | Unchanged: resolving membership is release-specific |
| 4 | Gate and severity | `policy/release_gate_options.resolve_release_gate_options` (from `release_request`) | Typed: `compare_finalize` calls the **same** function (S). Scalar CLI: `compare_configuration` lines ~446–465 plus `workflows/gate.effective_gate_for_resolved_compare_config` | D between the CLI and release | The session's resolved gate is the release gate. One derivation |
| 5 | Dry run | `frontends/cli/release_dry_run` → `workflows/release_plan.build_release_plan_from_directories` | `frontends/cli/compare_dry_run.build_compare_dry_run_result`, which embeds the release section | Rendering is mostly S. Selection parsing and pairing are D (see duplication 11) | The preview renders the real run's resolved membership plan |
| 6 | Member pairing | `release_inputs.match_release_keys`, `build_match_map`, `release_scope.resolve_release_scope_plan` | none | R | Unchanged |
| 7 | Per-member compare | `release_members.compare_release_member` → `compare_member_pair` → `member_compare.compare_member` → `run_compare_request` | Typed: `run_compare_request`. Scalar CLI: `classify_resolved_pair` | Classification is S. Operand normalization and the exit decision are D (duplications 1 and 3) | `compare_member` becomes "run the pair session for this member". The session owns normalization and the decision |
| 8 | Per-member reporting | `release_member_reporter.CliReleaseReporter` (`project`, `persist`, `settle`); `report/release_member_entry` | `runtime._finalize_compare_result`, `compare_report._render_compare_report` | Ledger helpers are S. JSON rendering and post-processing are D or missing (duplication 8) | A member's complete report is the session's rendered report |
| 9 | Summary projection | `report/release_outcome_projection.project_release_outcome`; `release_report.render_release_summary`; `release_summary._finalize_release_output` | `render_output` via `compare_report._render_output` | R (the release summary document) | Unchanged. It is aggregation, and it consumes session results |
| 10 | Exit fold | `release_exit_fold.*`; `release_exit._exit_compare_release` → `policy/release_exit_decision` | `runtime._exit_with_severity_or_verdict` → `exit_decision_precedence.resolve_compare_exit_decision_with_abort_axes` | The per-member decision is S (`resolve_compare_exit_decision`). The fold is R. The base-exit wording is D (duplication 4) | The fold consumes each session's `ExitDecision` (from the exit-decision refactor) and adds only the release axes |
| 11 | ADR-065 scope notices | `release_scope_notices.release_scope_warnings`; `policy/scope_completeness.incomplete_scope_diagnostic` | none | R | Unchanged |
| 12 | Output writing | `cli_compare_release._render_release_outcome` export loop; `release_summary._finalize_release_output` | `compare_result` export loop (memoizes one text per format) | D (duplication 9) | One export writer, parameterized by document |

## Exact duplication

Each item cites both sites. Line numbers are as of this plan's commit. Re-check them before
starting a slice.

1. **Following GNU ld linker scripts.** There are three copies with the same `Note: '<path>' is
   a GNU ld linker script; following its INPUT()/GROUP() directive to '<target>'.` text:
   `cli_resolve._maybe_follow_linker_script` (~164–179, scalar CLI),
   `workflows/release_members.normalize_member_operand` (~122–139), and
   `workflows/input_resolution` (~642–655, typed route, behind `InputSpec.follow_linker_scripts`,
   default `True`). A release member is normalized by `normalize_member_operand` and then reaches
   the typed route's follow, which is a no-op on the already-followed path.
2. **Severity config resolution.** `policy/release_gate_options` (~166–183: "any severity field
   set → `resolve_severity_config`") vs `workflows/compare_configuration` (~455–465, the same
   predicate as `severity_active`). The scalar CLI then re-derives
   `cfg.severity if cfg.severity_active else None` in `workflows/gate.py`.
3. **Exit decision computed twice per member.** `compare_finalize.finalize_classified_pair`
   computes `exit_decision` under the member request's severity (none, per row 2). Then
   `member_compare.member_exit_decision` recomputes it under the release `severity_config` and
   stamps `_compatibility_contribution`. The first decision is discarded. The two calls also
   differ: finalize uses `resolve_compare_exit_decision_with_abort_axes` and
   `effective_gate_for_compare(result=…)`, while the member uses plain
   `resolve_compare_exit_decision` with `EffectiveGate.from_severity`. *Phase 2 correction:* the
   different resolver is not a parity risk. The release folds the evidence-contract axis on its
   own (`evidence_contract_error_contribution`, `policy/release_exit_decision`), so the plain
   resolver is the right one for the compatibility contribution. And a release member never
   carries a scoped gate (`_apply_scoped_gating` runs only in the scalar CLI's render step), so
   the two gate derivations agree. P2 now derives the member's gate with
   `effective_gate_for_compare` anyway, so there is one derivation.
4. **Base exit and legacy mapping restated.** `release_exit_fold.release_compatibility_base_exit`
   (`not_comparable → 16`, the `ERROR → 4` floor, `legacy_exit_code`) restates precedence that
   `policy/release_exit_decision` and `policy/exit_decision` own. It exists only to word a stderr
   notice.
5. **The "floored to / contributes … below" wording ladder.** `policy/contract_coverage_exit`,
   `policy/analysis_assurance/_outputs`, `policy/release_assurance` and
   `policy/scope_completeness` each word it, and `frontends/cli/release_summary` (~425–454)
   hand-copies `_coverage_message` instead of calling `announce_coverage_floor`.
6. **Policy and suppression loading.** `frontends/cli/options/params._load_suppression_and_policy`
   (raises a Click error) is used by the scalar CLI and by the release's early check in
   `release_compare_request`. `workflows/compare_policy.load_suppression_and_policy` (raises
   `ValidationError`) runs once per member in `compare/execute`, and again for the bundle stage
   via `pack_application.resolve_bundle_policy_file`. `release_compare._resolve_typed_release_configuration`
   performs a third `PolicyFile.load`. A release therefore loads the suppression file
   1 + N (+1) times.
7. **Recording the resolved config.** `cli_compare_receipt.record_resolved_config` (scalar) vs
   `member_compare.record_release_resolved_config`. Its own docstring calls it the "release
   fan-out sibling", and it deliberately skips `with_resolved_gate` because the release has no
   per-member gate config.
8. **Per-member rendering.** The filtered-surface ledger header is written twice:
   `cli_audit.echo_filtered_surface` and `release_member_reporter._scope_ledger_text`.
   Per-library JSON is `reporter.to_json` called directly in `release_member_reporter.persist`,
   while the scalar path goes through `render_output` plus its folds in `compare_report`. Some
   scalar post-processing never runs for a member: the redundant-change merge, the reconciled
   ledger, the scope-fallback warning, and `_warn_all_suppressed` (`runtime.py` ~494–531). These
   are absent on the release side, not duplicated. They are the main way a pair session would
   *change* member output, so each needs a characterization test before it is reached.
9. **The export loop.** `frontends/cli/compare_result` (~239–263, memoizes one text per format)
   vs `cli_compare_release` (~716–737) plus `release_summary` (~315–348, no memoization; primary
   and secondary exports take separate calls).
10. **Mapping the not-comparable kind.** `cli_compare_fold` (~686–690:
    `"profile_mismatch" if isinstance(exc, ProfileMismatchError) else "scope_mismatch"`) vs
    `workflows/release_member_failures` (~73–85).
11. **Dry run vs real run.** Selection is parsed twice (`ReleaseSelection.from_lists` in
    `release_dry_run` and in `cli_compare_release`). Pairing is computed twice: the preview uses
    `collect_release_inputs` + `build_match_map` (`release_plan`), while the run uses
    `resolve_release_side` → `discover_files` → `match_release_keys`. The preview ignores
    `dso_only`/`include_private_dso` and skips package and stored operands. The preview's
    exit-scheme label comes from the CLI config (`dry_run_scheme_label`), but the run gates on
    `resolve_release_gate_options`. This is the `compare_dry_run` vs `release_dry_run`
    duplication, and the preview-drift failure mode `abicheck/workflows/AGENTS.md` warns about
    ("a preview computed by a second resolver looks authoritative while being connected to
    nothing").
12. **Header composition, inside the release path itself.** `release_inputs.resolve_release_headers`
    (~297–323) repeats the logic inlined at ~552–556, and only tests call it
    (`tests/test_sided_include_composition.py`).
13. **Compile-context include split.** The compile context is shared by construction
    (`_compile_context_kwargs` feeds both `resolve_compile_context` and
    `cli_resolve.resolve_directory_compile_context`), but `includes[len(base):]` is still written
    twice in `cli_compare_helpers` (~696, ~844).

These are already shared and need no work: the gate-pack fold (`policy/gate_pack_fold`), the
compatibility configuration (`compare_configuration.resolve_release_pack_application` →
`resolve_compare_configuration`), classification (`classify_resolved_pair`), the per-member exit
resolver (`resolve_compare_exit_decision`), and the report-mode fold (`normalize_report_mode`).

## Phase 1 (this change)

- This mapping.
- `tests/test_release_one_member_property.py`: a Hypothesis property over **generated** operand
  pairs. Each side is an arbitrary function and variable surface, and half the draws are
  perturbations of the old side, so modifications are reached and not only additions and
  removals. For each pair it runs, through the CLI, a scalar `compare` and a one-member directory
  `compare`. It asserts that all of the following equal their scalar counterparts:
  - the member's complete report (F2 normalization);
  - the release process exit code, exactly rather than as a fold, and the release `exit` block;
  - the release verdict;
  - the member row's verdict and per-category counts;
  - the release `change_inventory` compatibility counts;
  - the release `disposition_audit`;
  - the release `surface_changes` lists.

  It sits alongside the fixed-corpus `test_compare_cardinality_invariance.py` and
  `test_release_compare_workflow.py`. It has a vacuity guard (every verdict is reachable) and a
  negative control (a release step that drops one member finding must fail it). A 600-example
  local run found no divergence. The CI setting is 60 examples, about 4 s.

One divergence is out of scope and deliberately not asserted. The release `run_outcome.assurance`
is `null`, while the scalar report carries the full analysis-assurance block. The member report
does carry it. The release summary has no aggregated assurance owner yet
([G47](g47-analysis-assurance.md)), so the property does not compare `run_outcome`.

## Phase 2

Slices, in order. Each one is behavior-preserving and lands separately:

| Slice | Change | Removes duplication | Guard | Status |
|---|---|---|---|---|
| P1 | Split `workflows/compare/session.py` into a run-level part and a pair-level part (see "P1, re-scoped" below). The release runs the run-level part once and the pair-level part per member, instead of `member_compare_request` → `run_compare_request` | 2, 7 | `test_release_compare_workflow.py`, the property test, `test_compare_session.py` | Next |
| P2 | One gate derivation: `member_exit_decision` derives the member's gate with `workflows.gate.effective_gate_for_compare`, the single-pair derivation. The second per-member decision stays until P5: the member request deliberately carries no severity, and forwarding it would move the gate receipt `finalize_classified_pair` installs | 3 (derivation) | `test_release_compare_workflow.py` (`_compatibility_contribution` equals the scalar decision's under every severity axis) | Done (derivation) |
| P3 | One linker-script follower: `binary_utils.follow_linker_script` (re-exported by `workflows.extraction`) serves the single-pair session, each release member and `dump`, and `linker_script_note` is the one wording `resolve_input` also uses. `release_members.normalize_member_operand`, `session_inputs.normalize_operand`, `cli_resolve._maybe_follow_linker_script` and `_normalize_binary_input` are deleted | 1 | `test_compare_session.py`, `test_compare_input_modes.py`, `test_release_evaluation_config.py` | Done |
| P4 | Policy and suppression are loaded once per release and handed to each session. One loader with one error type, translated by the frontend | 6 | `test_family_f2_route_parity.py` suppress and policy-file axes; error-code characterization first | Open |
| P5 | Severity and gate resolution: the release gate *is* the session gate | 2 (severity), 4 | `test_release_gate_pack_fold_parity.py`, `test_typed_api_gate_options.py` | Open |
| P6 | A member's complete report is the session's rendered report. First characterize which scalar post-processing steps change a member's output (duplication 8). Each one is either adopted, with a changelog entry for the visible change, or recorded as release-intentional | 8 | Golden tests, `test_cli_compare_release_single_pair_parity.py` | Open |
| P7 | Dry run: the preview renders `resolve_release_compare_plan`'s membership instead of `build_release_plan_from_directories` | 11 | New test: preview pairing equals the run's pairing over package, stored and `dso_only` operands | Open |
| P8 | One export writer for scalar and release documents | 9 | `test_release_output_finalization.py` | Open |
| P9 | Clean-up: the wording ladder (5), the not-comparable mapping (10), `resolve_release_headers` (12), the include split (13) | 5, 10, 12, 13 | Existing unit tests | 10 done (`ProfileMismatchError`/`ScopeMismatchError.not_comparable_kind`; `release_scope.mismatch_kind` deleted). 12 done (`release_inputs.release_side_headers`, one per-side rule the release path and its test share). 5 and 13 open |

### P1, re-scoped

Members and the scalar session already share classification: both reach
`compare_classify.classify_resolved_pair`. What differs is the fold *before* classification. The
scalar CLI runs `run_pair_session`, and a member runs the typed `resolve_compare_request`.
Calling `run_pair_session` once per member as it stands would change output. The session also
does run-level work: it reads the project config, folds `compile:`, and warns about ignored
inputs, policy-override downgrades and `scope.public_symbols`. Per member, each of those notes
would print N times. So P1 first splits the session into:

- **run-level steps**, which the release runs once: the collect-mode fold, the compile context,
  the policy documents, the evaluation configuration, `policy.overrides`, acknowledgments and
  the run's notes;
- **pair-level steps**, which run per member: operand following, resolution, out-of-band packs
  and classification.

A one-member release must still print exactly the scalar run's notes. Extend the property test
to compare stderr before landing P1.

P6 is the only slice that can *intentionally* change output. If characterization shows a scalar
post-processing step would change a member report, that slice needs its own ADR note and
changelog entry, and does not land under "zero behavior change".

## Constraints

- `cli_compare_helpers.py` (1204) and `cli_compare_fold.py` (926) are at their
  `architecture/debt.yaml` `no_growth` baselines. Any slice that touches them must move code out,
  never in.
- `workflows/release_compare.py` (796), `release_inputs.py` (776), `release_scope.py` (779) and
  `cli_compare_release.py` (757) are close to the 800-line cap. Slices should shrink them.
- Import direction: the session lives in `workflows/`, and `release_*` workflow modules may call
  it. The CLI release modules stay in `frontends/` and reach it only through `workflows`.

## Out of scope

- Changing what the release summary reports, its schema (`release_schema_version`), or its
  ADR-065 states.
- An aggregated release-level analysis assurance ([G47](g47-analysis-assurance.md)).
- The bundle stage (`release_bundle`), which is release-global aggregation by definition.
