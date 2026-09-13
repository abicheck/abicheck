# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``compare-release``'s per-pair/per-library comparison engine (split
from :mod:`abicheck.cli_compare_release`).

The single-pair comparison primitive (:func:`_run_compare_pair`), the
per-library comparison it feeds (:func:`_compare_one_library`), the
opt-in lockstep-SONAME suppression pass
(:func:`_suppress_lockstep_soname_findings`), and the release-wide
sequential/parallel dispatch over every matched library
(:func:`_compare_release_libraries`/:func:`_compare_release_parallel`/
:func:`_compare_release_sequential`).

Extracted purely to keep :mod:`abicheck.cli_compare_release` itself under
the AI-readiness 2000-line hard cap -- see this module's own sibling,
:mod:`abicheck.cli_compare_release_matrix` (matrix-result collection,
output finalization, gating, and input-discovery), for the fuller
rationale: `architecture/debt.yaml` pins :mod:`abicheck.cli_compare_release`
at its exact adoption-time line count, and a single combined engine module
would itself have landed over the 800-line cap for a *new* file. A
mechanical extraction (unchanged function bodies).
:mod:`abicheck.cli_compare_release` re-exports every name here that an
existing test or caller imports directly for back-compat -- new code
should import from here directly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .bundle_models import BundleSignatureEvidence
from .checker import DiffResult
from .cli_compare_receipt import record_release_resolved_config
from .cli_compare_release_helpers import _RELEASE_VERDICT_ORDER
from .cli_resolve import _normalize_binary_input
from .frontends.cli.release_member_errors import member_error_entry
from .frontends.cli.runtime import _safe_write_output
from .model import AbiSnapshot
from .reporter import to_json
from .workflows.contracts import CompareResult

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .environment_matrix import EnvironmentMatrix
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig


def _release_job_mem_budget_gib() -> float:
    """Per-worker RAM budget (GiB) for the release-fan-out memory cap (R3,
    CLI-audit) -- see :mod:`abicheck.workflows.release_jobs`'s own docstring
    for the full "why". A thin wrapper (not a direct call site) purely so a
    test can monkeypatch this module's own name, matching the established
    ``_l4_*`` wrapper pattern in :mod:`abicheck.buildsource.source_replay`.
    """
    from .workflows.release_jobs import release_job_mem_budget_gib

    return release_job_mem_budget_gib()


def _release_jobs_mem_cap() -> int | None:
    """Max release-fan-out workers that fit in available RAM, or ``None``
    when RAM can't be read -- see :func:`_release_job_mem_budget_gib`'s
    docstring for why this is a thin wrapper.
    """
    from .workflows.release_jobs import release_jobs_mem_cap

    return release_jobs_mem_cap()


_CompareReleaseCommonArgs = tuple[
    dict[str, Path],
    dict[str, Path],
    Path | None,
    Path | None,
    Callable[[Path, Path], Path | None],
    list[Path],
    list[Path],
    list[Path],
    list[Path],
    str,
    str,
    str,
    Path | None,
    str,
    Path | None,
    Path | None,
    bool,
    bool,
    bool,
    str | None,
    bool,  # require_complete_analysis (ADR-071)
    "SeverityConfig | None",
    "PackApplication | None",
    bool,
    bool,
    "CompileContext | None",
    "str | None",
    "str | None",
    "list[Path] | None",
    bool,
    "dict[Any, Any] | None",
    "EnvironmentMatrix | None",
]


def _run_compare_pair(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path],
    new_headers: list[Path],
    old_includes: list[Path],
    new_includes: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
    scope_to_public_surface: bool = True,
    # ADR-068 D3 #7/D4: pattern-verdict modulation is unconditional on every
    # `compare` path now, directory/package release fan-out included -- "one
    # model, any cardinality" means a library compared here must get the
    # identical treatment it would from a single-pair `compare` of the same
    # library (no caller of this function ever passed a non-default value,
    # so this was previously a silent capability gap between the two paths).
    pattern_verdicts: bool = True,
    include_dependencies: bool = True,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    pack_application: PackApplication | None = None,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    public_header_dirs: list[Path] | None = None,
    collapse_versioned_symbols: bool = False,
    project_policy_overrides: dict[Any, Any] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
) -> CompareResult:
    """Run compare for one old/new pair and return result + resolved snapshots.

    Routes through the single Tier-2 chokepoint (:func:`service.run_compare`,
    ADR-037 D1) rather than calling ``checker.compare`` directly — this is what
    keeps ``compare-release`` and ``compare`` on one classification path so a
    library gets the same verdict from either command (no ``scope_public``
    default drift). ``include_dependencies`` (default ``True``) is the same
    reasoning applied to dependency-scope: without threading it through here
    too, a directory/package `compare` would silently stay unfiltered
    regardless of `--include-system-declarations`, drifting from a single-pair
    `compare` of the identical library (Codex review). ``contract_evaluation``/
    ``contract_mode`` (CLI-audit P1, release/package contract parity) are the
    same pass-through: ``service.run_compare`` already runs ADR-049's whole
    contract-relevance pipeline internally when asked, so threading these two
    flags here is what makes a library compared through the release fan-out
    get the identical contract decision it would from comparing it alone.

    *pack_application* (CLI cleanup phase two, "PR B" slice 1) is this run's
    already-resolved ``--pack`` contribution (``resolve_release_pack_
    application``, resolved once for the whole release, not per library) --
    forwarded to ``service.run_compare`` as ``pack_policy_overrides``/
    ``pack_internal_namespaces``, which ``service_compare_pipeline.
    classify_compare_pair`` folds into *this pair's own* freshly-loaded
    ``PolicyFile`` the same way a single-pair ``compare`` folds its packs
    into its one ambient policy file.

    *compile_context* is the release's already-resolved, both-sides L2
    header-AST compile context (``--ast-frontend``/``--compiler``/
    ``--compiler-prefix``/``--compiler-option``/``--sysroot``/``--nostdinc``/
    ``--frontend-context``, resolved once for the whole release by
    ``cli_compare_helpers.run_compare`` the same way a single-pair
    ``compare`` resolves it -- see ``cli_options.resolve_compile_context``).
    Forwarded to ``service.run_compare`` unchanged so each library's header
    dump parses with the same cross-toolchain/frontend context a single-pair
    ``compare`` of that library would use, closing the gap this function's
    own historical docstring used to flag ("the per-library fan-out does not
    thread the L2 compile context" -- see AGENTS.md's whole-product-bundle
    known-gap entry). ``None`` (the default) is a true no-op, matching every
    pre-existing caller.

    *depth* is the run's ``--depth`` pin -- any rung of the public ladder,
    forwarded unchanged to ``service.run_compare`` so this pair is resolved,
    floor-checked (``enforce_requested_depth``) and depth-projected
    (``project_pair_to_depth``) exactly as a single-pair ``compare --depth
    X`` would be. It read ``"binary"``-only while a CLI allow-list rejected
    the other three rungs; nothing here was ever ``binary``-specific. See
    :func:`~abicheck.cli_compare_options._resolve_depth_for_set_inputs`.

    *public_header_dirs* (CodeRabbit review, PR #1138): a project's
    ``.abicheck.yml`` ``scope.public_header_dirs``, resolved once for the
    whole release the same way *pack_application*/*compile_context* are --
    forwarded unchanged to ``service.run_compare``'s own identically-named
    parameter, closing the gap where this fan-out never threaded the config
    key a single-pair ``compare`` already honors.

    *env_matrix* (ADR-020b / ADR-068 D5): the project's declared deployment
    constraints, resolved once for the whole release from ``.abicheck.yml``'s
    ``deployment:`` config key (the former ``--env-matrix FILE``, which used
    to be rejected outright for a directory/package compare) -- forwarded
    unchanged to ``service.run_compare``'s own identically-named parameter so
    every library in the fan-out gets the same declared-floor symbol-version
    reclassification a single-pair ``compare`` of that library would.
    """
    from . import service

    # Follow GNU ld linker scripts up front so metadata/dependency analysis use
    # the resolved DSO, not the text script.
    old_input, _ = _normalize_binary_input(old_input)
    new_input, _ = _normalize_binary_input(new_input)

    result = service.run_compare(
        old_input,
        new_input,
        old_headers=old_headers,
        new_headers=new_headers,
        old_includes=old_includes,
        new_includes=new_includes,
        old_version=old_version,
        new_version=new_version,
        lang=lang,
        suppress=suppress,
        policy=policy,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        policy_file_path=policy_file_path,
        old_pdb_path=old_pdb_path,
        new_pdb_path=new_pdb_path,
        scope_to_public_surface=scope_to_public_surface,
        pattern_verdicts=pattern_verdicts,
        include_dependencies=include_dependencies,
        pack_policy_overrides=(
            dict(pack_application.policy_overrides) if pack_application else None
        ),
        pack_internal_namespaces=(
            pack_application.internal_namespaces if pack_application else None
        ),
        compile_context=compile_context,
        depth=depth,
        public_header_dirs=public_header_dirs,
        collapse_versioned_symbols=collapse_versioned_symbols,
        project_policy_overrides=project_policy_overrides,
        env_matrix=env_matrix,
    )
    record_release_resolved_config(
        result.diff, getattr(pack_application, "resolved_config", None)
    )
    return result


def _compare_one_library(
    key: str,
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_debug_dir: Path | None,
    new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    output_dir: Path | None,
    scope_to_public_surface: bool = True,
    include_dependencies: bool = True,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    require_complete_analysis: bool = False,
    severity_config: SeverityConfig | None = None,
    pack_application: PackApplication | None = None,
    collect_diff_results: bool = False,
    need_full_snapshots: bool = False,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    show_only: str | None = None,
    public_header_dirs: list[Path] | None = None,
    collapse_versioned_symbols: bool = False,
    project_policy_overrides: dict[Any, Any] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
) -> dict[str, object]:
    """Compare one library pair — suitable for parallel dispatch. Any
    exception yields an ERROR entry rather than aborting the release.

    The full :class:`DiffResult` is stashed under ``"_diff_result"``;
    callers needing it (bundle layer, JUnit) pop it before JSON-serialising.
    *collect_diff_results* additionally stashes ``"_bundle_key"`` plus
    either ``"_old_snapshot"``/``"_new_snapshot"`` (when
    *need_full_snapshots* -- JUnit/``--bundle-facts-out``) or the much
    smaller ``"_old_bundle_evidence"``/``"_new_bundle_evidence"`` (G38
    Phase 9's memory fix; see :class:`~abicheck.bundle_models.
    BundleSignatureEvidence`).

    *severity_config* is forwarded to the ``--output-dir`` JSON write below
    (Codex review): without it, that write always used the legacy
    exit-code scheme regardless of the release's own severity config.
    *show_only* is accepted but deliberately NOT forwarded to that write
    (Codex review, fresh evidence, PR #1154 follow-up: "Keep per-library
    output-dir reports unfiltered") -- ``--output-dir`` is the "always
    full" escape hatch a truncated aggregate report directs a reader to,
    matching a secondary ``-o``'s own contract; the display-filtered
    ``findings``/``findings_view`` split lives one level up, in
    :func:`~abicheck.cli_compare_release_matrix._strip_diff_results_and_adjust_verdict`,
    which has the real live ``DiffResult`` to filter from.
    This library's pattern-verdict modulation ledger is always rendered
    (plan slice 7o: disclosure is unconditional, ADR-067) (``cli_audit.render_pattern_modulations``, same content a
    single-pair `compare --view patterns` echoes) and stashes it under
    ``"_pattern_modulations_text"`` rather than echoing it directly --
    this function runs inside a `ThreadPoolExecutor` worker when the
    release fan-out is parallel (the default), and several independent
    `click.echo` calls from different worker threads can interleave
    nondeterministically (Codex review, fresh evidence: "Serialize
    pattern-ledger output after parallel comparison"). The caller
    (:func:`_compare_release_libraries`) echoes each library's stashed
    text after every future has completed, in `matched_keys` order --
    the same deterministic-ordering guarantee it already gives the rest
    of the release report.
    """
    old_path = old_map[key]
    new_path = new_map[key]
    try:
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        compare_result = _run_compare_pair(
            old_path,
            new_path,
            old_h,
            new_h,
            old_inc,
            new_inc,
            old_version,
            new_version,
            lang,
            suppress,
            policy,
            policy_file_path,
            old_pdb_path=old_dbg,
            new_pdb_path=new_dbg,
            scope_to_public_surface=scope_to_public_surface,
            include_dependencies=include_dependencies,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
            pack_application=pack_application,
            compile_context=compile_context,
            depth=depth,
            public_header_dirs=public_header_dirs,
            collapse_versioned_symbols=collapse_versioned_symbols,
            project_policy_overrides=project_policy_overrides,
            env_matrix=env_matrix,
        )
        result = compare_result.diff
        # Plan slice 7o: unconditional, like every other disposition ledger
        # (ADR-067). Captured as text rather than echoed here because a
        # per-library worker thread's echo could interleave with a sibling's
        # -- see `render_pattern_modulations`'s own docstring.
        from .cli_audit import render_pattern_modulations

        pattern_modulations_text: str | None = None
        if result.pattern_modulations:
            pattern_modulations_text = (
                f"\n== {old_path.name} ==\n{render_pattern_modulations(result)}"
            )
        v = result.verdict.value
        # compatible_additions/quality_issues must agree with the scalar
        # `compare` report's own split (`build_summary`'s effective-category
        # logic), not a raw `c.kind not in ADDITION_KINDS` test -- the two
        # previously disagreed on the same one-library comparison (Codex
        # review, findings-fixes round 10/11).
        from .report_summary import build_summary

        _summary = build_summary(result)
        n_quality = _summary.quality_issues
        # ADR-067 C-S2: this library's own raw-versus-effective disposition
        # audit, the same shape scalar `compare`'s JSON report carries
        # (`report.disposition_audit.compute_disposition_audit`/`.to_dict()`)
        # -- stamped here, before `_strip_diff_results_and_adjust_verdict`
        # discards `_diff_result`, so the release-level fold
        # (`cli_compare_receipt.release_disposition_audit_block`) has
        # something to read per library.
        from .report.disposition_audit import compute_disposition_audit

        entry: dict[str, object] = {
            "library": old_path.name,
            "verdict": v,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": _summary.compatible_additions,
            "quality_issues": n_quality,
            "disposition_audit": compute_disposition_audit(
                result, severity_config
            ).to_dict(),
            "_diff_result": result,
            **(
                {"coverage_warnings": list(result.coverage_warnings)}
                if result.coverage_warnings
                else {}
            ),  # e.g. same-binary; never reached this entry before (Codex review)
            # Codex review, P2: stamped here (before `_diff_result` is
            # discarded below) so this library's declared-deployment-floor
            # digest survives `_strip_diff_results_and_adjust_verdict`; also
            # promoted once to the release envelope (`_format_release_json`).
            **(
                {"env_matrix_source_sha256": result.env_matrix_source_sha256}
                if result.env_matrix_source_sha256 is not None
                else {}
            ),
        }
        if pattern_modulations_text is not None:
            entry["_pattern_modulations_text"] = pattern_modulations_text
        # ADR-067: a passing release report may not hide which breaking
        # findings a rule disposed of. Captured as text and echoed by the
        # caller, the same shape `_pattern_modulations_text` uses, so
        # parallel libraries cannot interleave their sections.
        if result.suppression_audit is not None:
            from .cli_compare_fold import _fold_suppression_audit_into_text

            section = _fold_suppression_audit_into_text(
                "", "markdown", result.suppression_audit, demangle=True
            )
            if section.strip():
                entry["_suppression_audit_text"] = f"\n### {old_path.name}{section}"
        if collect_diff_results:
            # See this function's own docstring (CodeRabbit review #798;
            # full- vs. compact-evidence split, G38 Phase 9).
            entry["_bundle_key"] = key
            if need_full_snapshots:
                entry["_old_snapshot"] = compare_result.old_snapshot
                entry["_new_snapshot"] = compare_result.new_snapshot
            else:
                entry["_old_bundle_evidence"] = BundleSignatureEvidence.from_snapshot(
                    compare_result.old_snapshot
                )
                entry["_new_bundle_evidence"] = BundleSignatureEvidence.from_snapshot(
                    compare_result.new_snapshot
                )
        # ADR-064's evidence-contract axis (exit 7), per member. `compare`'s
        # depth-shortfall contract is this axis -- recorded by
        # `service_compare_pipeline.classify_compare_pair`, never raised --
        # so the release has to fold each member's own contribution the way
        # it already folds the contract-coverage floor below, or a pinned
        # `--depth build`/`source` the members did not reach would exit 7
        # from a single-pair `compare` and 0 from a directory one (PR #1195,
        # Codex review). `0` unless this member actually recorded it, which
        # needs an explicit `--depth` pin, a `build`/`source` rung, and a
        # live side that fell short -- so every unpinned run is unchanged.
        # Through `workflows.gate`, which re-exports it: ADR-061 forbids a
        # `frontends -> policy` import, and this module is a frontend.
        from .workflows.gate import EXIT_EVIDENCE_CONTRACT_ERROR

        entry["evidence_contract_error_contribution"] = (
            EXIT_EVIDENCE_CONTRACT_ERROR if result.evidence_contract_error else 0
        )
        # `.abicheck.yml`'s `assurance.require_complete`, per member -- the
        # same orthogonal 0/1 floor, folded with max() into the release exit
        # by `_exit_compare_release`. Library count must not change what the
        # setting means: this is exactly the contribution a single-pair
        # `compare` of the same library would compute, so a release of one
        # library and that library compared on its own agree. Recorded
        # unconditionally (the flag's own `require_complete` gate lives in
        # the fold, not here) so the per-library status is readable in the
        # release JSON even on a run that did not opt in.
        # ADR-071 D1/D6: which keys this axis owns, and the fail-open status
        # read behind them, are `stamp_member_assurance`'s (see its docstring).
        from .workflows.release_assurance_members import stamp_member_assurance

        stamp_member_assurance(
            entry, result, require_complete=require_complete_analysis
        )
        if contract_evaluation:
            # ADR-049 Phase 7's orthogonal contract-coverage floor (0/1),
            # read off this library's own persisted contract context --
            # aggregated with max() into the release-level exit code in
            # _exit_compare_release, the same "raises a clean 0 to 1, never
            # lowers a real 2/4" rule a single-pair `compare` applies.
            from .workflows.gate import coverage_exit_floor, coverage_failure_count

            entry["contract_coverage_exit_contribution"] = coverage_exit_floor(result)
            # The *count* of failures is independent of the exit floor above
            # -- `contract.unresolved: warn` deliberately zeroes the floor
            # while the failures themselves stay real and unsuppressible
            # (AGENTS.md's contract_coverage_exit.py entry: "an acceptance
            # of incomplete assurance, not a way to hide it"). Without a
            # separate count, a release-level `warn`-accepted coverage gap
            # is invisible everywhere the release JSON is read from, since
            # this schema has no per-library `contract_coverage_failures`
            # array the way a single-pair `compare` report does (Codex
            # review, CLI-audit P2 follow-up).
            entry["contract_coverage_failure_count"] = coverage_failure_count(result)
        if scope_to_public_surface:
            # Per-library public-surface scoping outcome (ADR-024, issue #235),
            # aggregated into the release-level scope block by the formatter.
            entry["scope_resolved"] = result.scope_resolved
            entry["filtered_internal_count"] = result.out_of_surface_count
            # A count alone does not say *what* was excluded or why, so a
            # release whose entire breaking set was scoped out could pass
            # while explaining nothing -- ADR-067 again, the same gap the
            # suppression audit had (Codex review, PR #1284). Captured as
            # text and echoed in order by the caller, like the audit and
            # the pattern ledger, so parallel libraries cannot interleave.
            if result.out_of_surface_changes:
                from .cli_audit import ledger_lines_for

                entry["_scope_ledger_text"] = "\n".join(
                    [
                        f"\nFiltered as non-public ABI surface in "
                        f"{old_path.name} ({result.out_of_surface_count} "
                        f"{'finding' if result.out_of_surface_count == 1 else 'findings'}):",
                        *ledger_lines_for(
                            result.out_of_surface_changes,
                            contract_evaluation=contract_evaluation,
                        ),
                    ]
                )
        if output_dir:
            lib_report_path = output_dir / f"{old_path.stem}.json"
            # Codex review, fresh evidence ("Keep per-library output-dir
            # reports unfiltered"): `show_only` is deliberately NOT
            # forwarded here -- `_release_md_library_findings` directs a
            # reader to `--output-dir` as the one uncapped, *complete*
            # per-library source when the aggregate report's own findings
            # list was truncated, the same "always full" contract a
            # secondary `-o` already gets (see
            # `cli_compare_release_helpers._release_findings_for_render`).
            # Applying the primary display filter here too would let
            # `--view show=...` make a genuinely truncated (or simply
            # filtered-out) finding disappear from the one place that note
            # promises the complete list.
            _safe_write_output(
                lib_report_path,
                # ADR-071 (Codex P2): without this an incomplete member's own
                # `{library}.json` said `exit.code: 0` for a run it floored to `1`.
                to_json(
                    result,
                    severity_config=severity_config,
                    require_complete_analysis=require_complete_analysis,
                ),
            )
            # The unambiguous index a truncated machine document owes its
            # reader: this member's *complete*, uncapped report, by path.
            # `entry["findings"]` is a capped presentation projection, and a
            # machine consumer that cannot tell where the rest is has been
            # handed an implicitly truncated document.
            entry["complete_report"] = str(lib_report_path)
        return entry
    except Exception as exc:
        # One classification, four outcomes, ordering constraints of its own
        # -- owned by `cli_compare_release_member_errors`, not by this
        # function's argument list.
        return member_error_entry(
            exc,
            old_path=old_path,
            old_version=old_version,
            new_version=new_version,
            output_dir=output_dir,
        )


def _suppress_lockstep_soname_findings(
    library_results: list[dict[str, object]],
    worst_verdict: str,
    output_dir: Path | None,
    severity_config: SeverityConfig | None = None,
    show_only: str | None = None,
    require_complete_analysis: bool = False,
) -> int:
    """Drop ``SONAME_BUMP_UNNECESSARY`` when the release is a coordinated break.

    A library only earns ``SONAME_BUMP_UNNECESSARY`` when *it* had no breaking
    change yet its SONAME was bumped. In a multi-library release where a sibling
    or dependency suffered a genuine *binary* ABI break, bumping every member's
    SONAME in lockstep is the correct, intentional practice — so the per-library
    "unnecessary" signal is a false positive at the release level. Mutates the
    affected per-library results (and re-writes their JSON when ``output_dir`` is
    set) and returns the number of findings suppressed. Also records the
    suppression on the library's own ``disposition_ledger`` and recomputes its
    ``disposition_audit`` (Codex review, fresh evidence: "Record lockstep
    SONAME suppression before folding audits") -- without that, the audit
    already stamped by ``_compare_one_library`` (before this release-wide
    decision could be made) kept labelling the now-hidden finding
    ``non_gating`` with no suppression rule or reason at all. *severity_config*
    is forwarded to both that recomputation and the re-write's own ``to_json``
    call (Codex review, fresh evidence): without it, a severity-aware
    release's per-library report file would revert to the legacy exit-code
    scheme's ``exit`` block on this second write, even though
    ``_compare_one_library``'s first write already used the severity-aware
    one — so which scheme a report's ``exit`` block reflects would depend on
    whether this suppression fired, not on the release's actual
    configuration.

    Only a binary-incompatible (``BREAKING``) finding justifies a SONAME bump; a
    source-only ``API_BREAK`` does not, so the warning is preserved in that case.
    """
    if worst_verdict != "BREAKING":
        return 0
    from .checker_policy import ChangeKind

    suppressed = 0
    for entry in library_results:
        result = entry.get("_diff_result")
        if not isinstance(result, DiffResult):
            continue
        unnecessary = [
            c for c in result.changes if c.kind == ChangeKind.SONAME_BUMP_UNNECESSARY
        ]
        if not unnecessary:
            continue
        result.changes = [
            c for c in result.changes if c.kind != ChangeKind.SONAME_BUMP_UNNECESSARY
        ]
        # Codex review, fresh evidence ("Preserve lockstep findings in the
        # suppression trail"): removing `unnecessary` from `result.changes`
        # alone drops it from the finding-level audit trail entirely --
        # `to_json()`'s own `suppression` block reads `result.suppressed_
        # changes`/`suppressed_count` (the same fields every other
        # suppression path in this codebase populates,
        # `checker._filter_suppressed_changes`), not the disposition
        # ledger this function already updates below. Without this, the
        # rewritten per-library `--output-dir` JSON reported
        # `suppression.suppressed_count: 0` and an empty
        # `suppressed_changes` array while its own `disposition_audit`
        # (recomputed a few lines down) said one finding was suppressed --
        # two supposedly-agreeing views of the same report disagreeing on
        # whether a suppression happened at all.
        result.suppressed_changes = [*result.suppressed_changes, *unnecessary]
        result.suppressed_count += len(unnecessary)
        suppressed += len(unnecessary)
        # Codex review, fresh evidence ("Record lockstep SONAME suppression
        # before folding audits"): this library's own `disposition_audit`
        # was already stamped (in `_compare_one_library`, before the
        # release-wide `worst_verdict` this suppression depends on was even
        # known) against the pre-suppression `result.disposition_ledger` --
        # left untouched, it kept labelling the now-hidden finding
        # `non_gating` with no suppression rule or reason at all. Recorded
        # on a copy of the ledger (`with_suppressed`, mirroring `with_gate`'s
        # own "return a copy relabelled" shape) before `entry["disposition_
        # audit"]` is recomputed below, so the release-level fold
        # (`cli_compare_receipt.release_disposition_audit_block`) sees the
        # suppression too. Applied via `workflows.disposition` (Codex
        # review, fresh evidence: "Move release suppression out of report
        # projection") -- mutating the ledger is the policy decision
        # itself, not a projection of one already made, so it does not
        # belong behind a `report/` crossing-point; `workflows/
        # disposition.py` is the established "a frontend legitimately
        # touches the disposition ledger through here" home every other
        # such need in this codebase already uses.
        from .report.disposition_audit import compute_disposition_audit
        from .workflows.disposition import supersede_as_suppressed

        supersede_as_suppressed(
            result,
            unnecessary,
            application_point="lockstep_soname_suppression",
            rule_id="lockstep_soname_bump",
            reason=(
                "release contains a coordinated binary ABI break; lockstep "
                "SONAME bumps across every member are justified"
            ),
        )
        entry["disposition_audit"] = compute_disposition_audit(
            result, severity_config
        ).to_dict()
        # Recompute the cached per-library counts via `build_summary`,
        # same as above (Codex review, findings-fixes round 10/11).
        from .report_summary import build_summary

        _summary = build_summary(result)
        entry["breaking"] = len(result.breaking)
        entry["source_breaks"] = len(result.source_breaks)
        entry["risk_changes"] = len(result.risk)
        entry["compatible_additions"] = _summary.compatible_additions
        entry["quality_issues"] = _summary.quality_issues
        if output_dir is not None:
            lib_report_path = output_dir / f"{Path(str(entry['library'])).stem}.json"
            # Codex review, fresh evidence ("Keep per-library output-dir
            # reports unfiltered"): `show_only` deliberately NOT forwarded
            # -- see `_compare_one_library`'s identical first write above
            # for the full rationale (the "always full" `--output-dir`
            # contract `_release_md_library_findings` documents).
            _safe_write_output(
                lib_report_path,
                # Same ADR-071 threading as the first write above.
                to_json(
                    result,
                    severity_config=severity_config,
                    require_complete_analysis=require_complete_analysis,
                ),
            )
    return suppressed


def _compare_release_libraries(
    matched_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_debug_dir: Path | None,
    new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    output_dir: Path | None,
    collect_diff_results: bool = False,
    *,
    need_full_snapshots: bool = False,
    jobs: int = 1,
    scope_to_public_surface: bool = True,
    include_dependencies: bool = True,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    require_complete_analysis: bool = False,
    pack_application: PackApplication | None = None,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    show_only: str | None = None,
    public_header_dirs: list[Path] | None = None,
    collapse_versioned_symbols: bool = False,
    project_policy_overrides: dict[Any, Any] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, AbiSnapshot]]]:
    """Compare each matched library pair and collect results.

    When *collect_diff_results* and *need_full_snapshots* are both True,
    ``(DiffResult, old_snapshot)`` pairs are collected and returned as the
    third element of the tuple (used by the JUnit output format).

    When *jobs* > 1, comparisons are dispatched in parallel via
    :func:`_compare_one_library` using a :class:`ThreadPoolExecutor` -- not
    a ``ProcessPoolExecutor``, as this docstring wrongly claimed before
    (Codex review, fresh evidence; see :func:`_compare_release_parallel`'s
    own docstring for why the distinction matters).

    R3 (CLI-audit): the auto default (*jobs* ``<= 0``) additionally clamps
    to available RAM via :func:`_release_jobs_mem_cap` -- see that
    function's own docstring for why a bare ``os.cpu_count()`` default can
    wildly oversubscribe memory on a very-high-core-count host or a
    cpu-count-vs-memory-mismatched container. A positive *jobs* is never
    clamped -- unlike the ``ABICHECK_L4_JOBS`` env-var override this
    pattern is mirrored from (:mod:`abicheck.buildsource.source_replay`),
    which clamps even an explicit override since it has no equivalent "the
    caller deliberately chose this" signal to respect. There is no CLI flag
    for *jobs* any more (ADR-068 D5 / plan Phase 7h removed ``-j``/``--jobs``
    outright -- always auto-detect and memory-clamp); the CLI's own call
    site always passes ``jobs=0``, and *jobs* stays a Tier-2 parameter for
    direct callers only.
    """
    import os as _os

    effective_jobs = jobs if jobs > 0 else (_os.cpu_count() or 1)
    if jobs <= 0:
        mem_cap = _release_jobs_mem_cap()
        if mem_cap is not None and mem_cap < effective_jobs:
            click.echo(
                f"Note: parallel release workers reduced {effective_jobs} -> "
                f"{mem_cap} to fit available memory (~{_release_job_mem_budget_gib():.1f} "
                "GiB/worker budget, each holding up to two full snapshots resident); "
                "set ABICHECK_RELEASE_JOB_MEM_GIB to tune the per-worker budget.",
                err=True,
            )
            effective_jobs = mem_cap
    library_results: list[dict[str, object]] = []
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    worst_verdict = "NO_CHANGE"

    common_args = (
        old_map,
        new_map,
        old_debug_dir,
        new_debug_dir,
        resolve_debug_info,
        old_h,
        new_h,
        old_inc,
        new_inc,
        old_version,
        new_version,
        lang,
        suppress,
        policy,
        policy_file_path,
        output_dir,
        scope_to_public_surface,
        include_dependencies,
        contract_evaluation,
        contract_mode,
        require_complete_analysis,
        severity_config,
        pack_application,
        collect_diff_results,
        need_full_snapshots,
        compile_context,
        depth,
        show_only,
        public_header_dirs,
        collapse_versioned_symbols,
        project_policy_overrides,
        env_matrix,
    )

    if effective_jobs > 1 and len(matched_keys) > 1:
        library_results.extend(
            _compare_release_parallel(
                matched_keys, common_args, old_map, effective_jobs
            ),
        )
    else:
        library_results.extend(
            _compare_release_sequential(matched_keys, common_args),
        )

    # Post-process all results: compute worst verdict, collect annotations,
    # and optionally collect diff_pairs (for JUnit).
    for entry in library_results:
        # Echoed here, after every future has completed, in matched_keys
        # order -- not inside _compare_one_library's own worker thread,
        # which could interleave with a sibling library's echo
        # nondeterministically under the parallel (default) fan-out (Codex
        # review, fresh evidence: "Serialize pattern-ledger output after
        # parallel comparison").
        pattern_modulations_text = entry.pop("_pattern_modulations_text", None)
        if pattern_modulations_text is not None:
            click.echo(pattern_modulations_text, err=True)
        suppression_audit_text = entry.pop("_suppression_audit_text", None)
        if suppression_audit_text is not None:
            click.echo(suppression_audit_text, err=True)
        scope_ledger_text = entry.pop("_scope_ledger_text", None)
        if scope_ledger_text is not None:
            click.echo(scope_ledger_text, err=True)
        v = str(entry["verdict"])
        if v == "ERROR":
            if "error" in entry:
                click.echo(
                    f"Error comparing {entry['library']}: {entry['error']}", err=True
                )
        elif v == "not_comparable":
            if "reason" in entry:
                click.echo(
                    f"Not comparable: {entry['library']}: {entry['reason']}", err=True
                )
        elif v == "unsupported":
            click.echo(
                f"Unsupported: {entry['library']}: {entry.get('reason', '')}", err=True
            )
        elif v == "failed":
            click.echo(
                f"Failed: {entry['library']}: {entry.get('reason', '')}", err=True
            )
        if _RELEASE_VERDICT_ORDER.get(v, 0) > _RELEASE_VERDICT_ORDER.get(
            worst_verdict, 0
        ):
            worst_verdict = v

    # Cross-library coupling: a coordinated SONAME bump across the release is not
    # "unnecessary" just because one member had no break of its own.
    suppressed_soname = _suppress_lockstep_soname_findings(
        library_results,
        worst_verdict,
        output_dir,
        severity_config,
        show_only,
        require_complete_analysis=require_complete_analysis,
    )
    if suppressed_soname:
        click.echo(
            f"Note: suppressed {suppressed_soname} 'soname_bump_unnecessary' "
            "finding(s) — the release contains coordinated ABI breaks, so "
            "lockstep SONAME bumps are justified.",
            err=True,
        )

    # collect_diff_results (JUnit / a secondary `-o junit=...` render)
    # used to need an independent re-run (`_collect_release_extras`) purely
    # to recover the old `AbiSnapshot` alongside each `DiffResult` -- the
    # primary pass above now stashes both directly in each library's own
    # `entry["_diff_result"]`/`entry["_old_snapshot"]`, so building the
    # pairs is a plain read, not a second comparison (CodeRabbit review,
    # PR #798): the old re-run's own failure handling silently *dropped* a
    # pair from the secondary report on a rerun error even when the
    # primary pass had already succeeded for it, which this can no longer
    # do since there is nothing left to fail. Annotations were fixed the
    # identical way earlier in this same PR (see `annotation_report_
    # entries`/`reporter_contract_blocks.add_annotations`); the Action
    # reads them straight off the JSON report.
    if collect_diff_results:
        for entry in library_results:
            diff = entry.get("_diff_result")
            old_snap = entry.get("_old_snapshot")
            if isinstance(diff, DiffResult) and isinstance(old_snap, AbiSnapshot):
                diff_pairs.append((diff, old_snap))

    return library_results, worst_verdict, diff_pairs


def _compare_release_parallel(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
    old_map: dict[str, Path],
    max_workers: int,
) -> list[dict[str, object]]:
    """Run per-library release comparisons in parallel.

    Results are collected by key and returned in *matched_keys* order so the
    report is deterministic regardless of completion timing (parallel is now the
    default via ``jobs=0``, auto-detect); CI snapshots and downstream diffs
    depend on this.

    Uses a :class:`ThreadPoolExecutor` (real OS threads sharing this
    process's memory), *not* a ``ProcessPoolExecutor`` -- a stale claim in
    an earlier revision of this docstring said otherwise (Codex review,
    fresh evidence). That distinction matters for `policy_file.
    dedup_validate_overrides_warnings()`: a `ContextVar` set in the calling
    thread is *not* automatically visible to a new thread `ThreadPoolExecutor`
    spawns -- each worker thread starts with the `ContextVar`'s default value
    -- so submitting bare `_compare_one_library` calls would silently escape
    the caller's dedup scope and warn once per library even under the
    default (`jobs=0`, auto-detected CPU count > 1) parallel path. Fixed by
    explicitly propagating a copy of the calling thread's
    `contextvars.Context` into each submitted call via ``Context.run``.

    Two subtleties this went through, both caught by a real (initially
    intermittent, then reliably reproducing) test failure rather than by
    inspection -- worth recording so a future edit here doesn't reintroduce
    either:

    1. ``copy_context()`` must be called in *this* (the calling) thread, at
       submission time -- not inside the function a worker thread executes.
       Calling it from within the submitted callable copies whatever context
       that already-new worker thread started with (the `ContextVar`
       default), not this thread's dedup scope, silently reproducing the
       exact bug this fix exists to close.
    2. Each submission needs its *own* fresh copy, not one `Context` object
       shared across tasks -- ``Context.run`` raises ``RuntimeError`` if the
       same `Context` object is entered from more than one thread
       concurrently.

    Every copy still shares the same mutable dedup ``set`` object the
    `ContextVar` points to (copying a context copies variable *bindings*,
    not the values they point to), so every worker's dedup check is against
    the one real, shared set regardless of which thread runs it -- guarded
    by `policy_file`'s own dedup lock against the resulting cross-thread
    race on that shared set (also caught by the same test failure).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextvars import Context, copy_context

    def _run_in_context(ctx: Context, key: str) -> dict[str, object]:
        # `ctx` was captured in the calling thread at submission time (see
        # point 1 in the docstring above) -- this closure has a concrete
        # signature (rather than `executor.submit(ctx.run, fn, *args)`
        # directly) so it stays checkable by mypy: `Context.run`'s own
        # ParamSpec-generic signature otherwise defeats `submit`'s overload
        # resolution against `_compare_one_library`'s real params.
        return ctx.run(_compare_one_library, key, *common_args)

    results_by_key: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            # copy_context() runs here, in the calling thread -- see point 1
            # above. A fresh copy per key -- see point 2.
            executor.submit(_run_in_context, copy_context(), key): key
            for key in matched_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results_by_key[key] = future.result()
            except Exception as exc:
                click.echo(f"Error comparing {old_map[key].name}: {exc}", err=True)
                results_by_key[key] = {
                    "library": old_map[key].name,
                    "verdict": "ERROR",
                    "error": str(exc),
                }
    return [results_by_key[key] for key in matched_keys if key in results_by_key]


def _compare_release_sequential(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
) -> list[dict[str, object]]:
    """Run per-library release comparisons sequentially."""
    return [_compare_one_library(key, *common_args) for key in matched_keys]
