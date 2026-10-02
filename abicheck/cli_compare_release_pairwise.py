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

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .checker import DiffResult
from .cli_compare_receipt import record_release_resolved_config
from .cli_compare_release_helpers import _RELEASE_VERDICT_ORDER
from .cli_resolve import _normalize_binary_input
from .frontends.cli.release_member_errors import (
    member_dispatch_failure_entry,
    member_error_entry,
)
from .frontends.cli.runtime import _safe_write_output
from .model.symbol_inventory import SymbolInventory
from .reporter import disposition_ledger_blocks, to_json
from .workflows import cache_counters, memory_trace, release_snapshot_retention
from .workflows.contracts import CompareResult
from .workflows.crosscheck_ownership import (
    release_level_checks,
    release_owned_checks_scope,
)
from .workflows.keyed_thread_pool import run_keyed_in_threads
from .workflows.release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_request,
    run_compare_kwargs,
)
from .workflows.release_snapshot_retention import release_junit_pairs

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .environment_matrix import EnvironmentMatrix
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig
    from .workflows.release_admission import MemoryAdmission


def _release_owned_crosschecks() -> frozenset[str]:
    """The checks the release level answers -- a thin wrapper so a test can
    monkeypatch this module's own name (the ``_release_job_mem_budget_gib``
    pattern below). Reached through ``workflows.crosscheck_ownership``
    because ``frontends -> policy`` is a forbidden edge."""
    return release_level_checks()


def _release_job_mem_budget_gib(depth: str | None = None) -> float:
    """Per-worker RAM budget (GiB) for the release-fan-out memory cap (R3,
    CLI-audit) -- see :mod:`abicheck.workflows.release_jobs`'s own docstring
    for the full "why". A thin wrapper (not a direct call site) purely so a
    test can monkeypatch this module's own name, matching the established
    ``_l4_*`` wrapper pattern in :mod:`abicheck.buildsource.source_replay`.
    """
    from .workflows.release_jobs import release_job_mem_budget_gib

    return release_job_mem_budget_gib(depth)


def _release_jobs_mem_cap(depth: str | None = None) -> int | None:
    """Max release-fan-out workers that fit in available RAM, or ``None``
    when RAM can't be read -- see :func:`_release_job_mem_budget_gib`'s
    docstring for why this is a thin wrapper.
    """
    from .workflows.release_jobs import release_jobs_mem_cap

    return release_jobs_mem_cap(depth)


@dataclass(frozen=True)
class ReleaseMemberContext:
    """Everything one member's comparison needs, resolved once per release.

    *request* is the parent :class:`ReleaseMemberCompareRequest`; a member
    gets it plus a :class:`MemberDelta` (its operands and debug files) via
    :func:`member_request`, never a field-by-field rebuild. The remaining
    fields are the front end's own per-member reporting inputs, which never
    reach ``service.run_compare``.
    """

    request: ReleaseMemberCompareRequest
    old_map: dict[str, Path]
    new_map: dict[str, Path]
    old_debug_dir: Path | None = None
    new_debug_dir: Path | None = None
    resolve_debug_info: Callable[[Path, Path], Path | None] | None = None
    output_dir: Path | None = None
    require_complete_analysis: bool = False
    severity_config: SeverityConfig | None = None
    pack_application: PackApplication | None = None
    collect_diff_results: bool = False
    retention: release_snapshot_retention.SnapshotRetention | None = None
    show_only: str | None = None


def release_parent_request(
    pack_application: PackApplication | None = None, **fields: Any
) -> ReleaseMemberCompareRequest:
    """The release's parent request: *fields* plus *pack_application*'s
    resolved policy overrides and internal namespaces."""
    return ReleaseMemberCompareRequest(
        pack_policy_overrides=(
            dict(pack_application.policy_overrides) if pack_application else None
        ),
        pack_internal_namespaces=(
            pack_application.internal_namespaces if pack_application else None
        ),
        **fields,
    )


def _run_compare_pair(
    request: ReleaseMemberCompareRequest,
    pack_application: PackApplication | None = None,
) -> CompareResult:
    """Run compare for one member's *request* and return its result.

    Routes through the single Tier-2 chokepoint (:func:`service.run_compare`,
    ADR-037 D1) rather than calling ``checker.compare`` directly, so a
    library gets the same verdict from a directory/package ``compare`` as
    from a single-pair one. *request* is the release's parent request with
    this member's :class:`MemberDelta` applied
    (:mod:`abicheck.workflows.release_member_request`); every one of its
    fields is forwarded by :func:`run_compare_kwargs`, so no setting the
    release resolved can be dropped here. That forwarding used to be a
    hand-written keyword list, and each historical gap in it
    (``include_dependencies``, the contract flags, ``--pack``, the compile
    context, ``--depth``, ``scope.public_header_dirs``, ``--exclude-header``,
    ``lang_explicit``, the deployment matrix) was a release member silently
    disagreeing with the identical single-pair comparison.

    *pack_application* is the release's resolved ``--pack`` contribution; its
    overrides are already on *request*. It is passed separately only for the
    rich-tier config record below.
    """
    from . import service

    # Follow GNU ld linker scripts up front so metadata/dependency analysis use
    # the resolved DSO, not the text script.
    assert request.old_input is not None and request.new_input is not None
    request = dataclasses.replace(
        request,
        old_input=_normalize_binary_input(request.old_input)[0],
        new_input=_normalize_binary_input(request.new_input)[0],
    )
    result = service.run_compare(**run_compare_kwargs(request))
    # The rich-tier config is recorded under exactly the condition the
    # single-pair CLI (`resolve_and_apply`) and the typed API
    # (`install_resolved_gate_receipt`) record one: a contract evaluation, or
    # a pack that contributed. A plain member run stays on the documented
    # baseline tier -- stamping the no-pack application's always-resolved
    # config here made every release member report a different
    # `effective_config_digest` than the identical single-pair `compare`
    # (F2 route parity). A `.abicheck.yml` override still reaches the
    # baseline tier's `policy.overrides`, read off the scoring policy file.
    resolved_config = getattr(pack_application, "resolved_config", None)
    if not request.contract_evaluation and (
        pack_application is None or pack_application.is_empty()
    ):
        resolved_config = None
    record_release_resolved_config(result.diff, resolved_config)
    return result


def _compare_one_library(
    key: str,
    ctx: ReleaseMemberContext,
) -> dict[str, object]:
    """Compare one library pair — suitable for parallel dispatch. Any
    exception yields an ERROR entry rather than aborting the release.

    The full :class:`DiffResult` is stashed under ``"_diff_result"``;
    callers needing it (bundle layer, JUnit) pop it before JSON-serialising.
    *collect_diff_results* additionally stashes ``"_bundle_key"`` plus each
    side's bundle evidence, full or compact per *retention* -- owned, with
    its reasoning, by :mod:`abicheck.workflows.release_snapshot_retention`.

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
    request = ctx.request
    old_version, new_version = request.old_version, request.new_version
    scope_to_public_surface = request.scope_to_public_surface
    contract_evaluation = request.contract_evaluation
    output_dir, severity_config = ctx.output_dir, ctx.severity_config
    require_complete_analysis = ctx.require_complete_analysis
    old_path = ctx.old_map[key]
    new_path = ctx.new_map[key]
    try:
        resolve_debug_info = ctx.resolve_debug_info
        old_dbg = (
            resolve_debug_info(old_path, ctx.old_debug_dir)
            if resolve_debug_info and ctx.old_debug_dir
            else None
        )
        new_dbg = (
            resolve_debug_info(new_path, ctx.new_debug_dir)
            if resolve_debug_info and ctx.new_debug_dir
            else None
        )
        compare_result = _run_compare_pair(
            member_request(
                request,
                MemberDelta(
                    old_input=old_path,
                    new_input=new_path,
                    old_pdb_path=old_dbg,
                    new_pdb_path=new_dbg,
                ),
            ),
            ctx.pack_application,
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
        from .report.release_member_summary import add_member_review_summary

        entry: dict[str, object] = {
            "library": old_path.name,
            "verdict": v,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": _summary.compatible_additions,
            "quality_issues": n_quality,
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
        add_member_review_summary(entry, result, severity_config)
        # Known-gaps "-H applied to every member", step 3: which of this
        # member's type findings its own export surface actually reaches.
        # Read by the release-level shared-finding fold; never rendered.
        from .workflows.release_member_attribution import member_type_attribution

        if type_attribution := member_type_attribution(
            (c.symbol for c in result.changes),
            compare_result.old_snapshot,
            compare_result.new_snapshot,
        ):
            entry["_type_attribution"] = dict(type_attribution)
        # Release schema 1.9: the recorded DT_SONAME/DT_NEEDED facts, per side.
        from .report.release_dependency_graph import member_dependencies

        if deps := member_dependencies(
            compare_result.old_snapshot, compare_result.new_snapshot
        ):
            entry["dependencies"] = deps
        # ADR-067's structured half; see `reporter.disposition_ledger_blocks`.
        entry.update(disposition_ledger_blocks(result))
        if pattern_modulations_text is not None:
            entry["_pattern_modulations_text"] = pattern_modulations_text
        # ADR-067: a passing release report may not hide which breaking
        # findings a rule disposed of. Echoed by the caller, in order.
        if result.suppression_audit is not None:
            from .cli_compare_fold import _fold_suppression_audit_into_text

            section = _fold_suppression_audit_into_text(
                "", "markdown", result.suppression_audit, demangle=True
            )
            if section.strip():
                entry["_suppression_audit_text"] = f"\n### {old_path.name}{section}"
        if ctx.collect_diff_results:
            # See this function's own docstring (CodeRabbit review #798;
            # full- vs. compact-evidence split, G38 Phase 9).
            release_snapshot_retention.stash_member_evidence(
                entry,
                key,
                compare_result.old_snapshot,
                compare_result.new_snapshot,
                ctx.retention,
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
            # ADR-067: a count alone does not say *what* was excluded or
            # why. Echoed in order by the caller so libraries cannot
            # interleave under the parallel fan-out.
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
        from .report.release_member_summary import add_member_review_summary

        add_member_review_summary(entry, result, severity_config)
        # ...and the ledger blocks, snapshotted before this pass ran and so
        # naming neither this rule nor what it hid (Codex review, PR #1284).
        entry.update(disposition_ledger_blocks(result))
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
    retention: release_snapshot_retention.SnapshotRetention | None = None,
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
    exclude_headers: tuple[str, ...] = (),
    lang_explicit: bool = False,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, SymbolInventory]]]:
    """Compare each matched library pair and collect results.

    When *collect_diff_results* is True and *retention* keeps the full OLD
    side, ``(DiffResult, old_snapshot)`` pairs are returned third -- what
    JUnit and ``--bundle-facts-out`` read.

    When *jobs* > 1, comparisons are dispatched in parallel via
    :func:`_compare_one_library` using a :class:`ThreadPoolExecutor` -- not
    a ``ProcessPoolExecutor``, as this docstring wrongly claimed before
    (Codex review, fresh evidence; see :func:`_compare_release_parallel`'s
    own docstring for why the distinction matters).

    R3 (CLI-audit): the auto default (*jobs* ``<= 0``) is sized and then
    memory-gated by ``workflows.release_jobs.plan_release_workers`` (the
    gate costs members by measured AST size); a positive *jobs* is never
    clamped. The CLI always passes ``jobs=0`` (ADR-068 D5 removed ``-j``).
    """
    from .workflows.release_jobs import plan_release_workers

    plan = plan_release_workers(jobs, depth=depth, header_roots=bool(old_h or new_h))
    effective_jobs, clamped_from, budget_gib = plan.initial_jobs, plan.clamped_from, plan.budget_gib  # fmt: skip
    if clamped_from is not None:
        click.echo(
            f"Note: parallel release workers reduced {clamped_from} -> "
            f"{effective_jobs} to fit available memory (~{budget_gib:.1f} "
            "GiB/worker budget, each holding up to two full snapshots resident); "
            "set ABICHECK_RELEASE_JOB_MEM_GIB to tune the per-worker budget.",
            err=True,
        )
    library_results: list[dict[str, object]] = []
    diff_pairs: list[tuple[DiffResult, SymbolInventory]] = []
    worst_verdict = "NO_CHANGE"

    ctx = ReleaseMemberContext(
        request=release_parent_request(
            pack_application,
            old_headers=old_h,
            new_headers=new_h,
            old_includes=old_inc,
            new_includes=new_inc,
            old_version=old_version,
            new_version=new_version,
            lang=lang,
            lang_explicit=lang_explicit,
            suppress=suppress,
            policy=policy,
            policy_file_path=policy_file_path,
            scope_to_public_surface=scope_to_public_surface,
            include_dependencies=include_dependencies,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
            compile_context=compile_context,
            depth=depth,
            public_header_dirs=public_header_dirs,
            collapse_versioned_symbols=collapse_versioned_symbols,
            project_policy_overrides=project_policy_overrides,
            env_matrix=env_matrix,
            exclude_headers=exclude_headers,
        ),
        old_map=old_map,
        new_map=new_map,
        old_debug_dir=old_debug_dir,
        new_debug_dir=new_debug_dir,
        resolve_debug_info=resolve_debug_info,
        output_dir=output_dir,
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config,
        pack_application=pack_application,
        collect_diff_results=collect_diff_results,
        retention=retention,
        show_only=show_only,
    )

    # `workflows.crosscheck_ownership`: the whole-product cross-source check
    # is answered once at release level, so the member pass must not answer
    # it per member against the complete product header surface. Entered
    # around the dispatch because a copy of *this* thread's context is what
    # reaches each parallel worker (see `_compare_release_parallel`). A
    # one-member release is excluded on purpose -- no union to take, so the
    # per-member answer already agrees with the scalar path.
    owned = _release_owned_crosschecks() if len(matched_keys) > 1 else frozenset()
    with release_owned_checks_scope(owned):
        if plan.pool_size > 1 and len(matched_keys) > 1:
            library_results.extend(
                _compare_release_parallel(
                    matched_keys, ctx, old_map, plan.pool_size, plan.admission
                ),
            )
        else:
            library_results.extend(
                _compare_release_sequential(matched_keys, ctx),
            )
    cache_counters.record_shared_cache_counters()

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
    # to recover the old side alongside each `DiffResult` -- the primary
    # pass above now stashes both directly on each library's own entry, so
    # building the pairs is a plain read, not a second comparison
    # (CodeRabbit review, PR #798): the old re-run's own failure handling
    # silently *dropped* a pair from the secondary report on a rerun error
    # even when the primary pass had already succeeded for it, which this
    # can no longer do since there is nothing left to fail. Annotations
    # were fixed the identical way earlier in this same PR (see
    # `annotation_report_entries`/`reporter_contract_blocks.
    # add_annotations`); the Action reads them straight off the JSON
    # report. The OLD operand here is the compact `SymbolInventory` a JUnit
    # render needs, never the full `AbiSnapshot`;
    # `workflows.release_snapshot_retention.release_old_snapshot_pairs` is
    # what `--bundle-facts-out` uses to recover the whole document.
    if collect_diff_results:
        diff_pairs.extend(release_junit_pairs(library_results))

    return library_results, worst_verdict, diff_pairs


def _compare_release_parallel(
    matched_keys: list[str],
    ctx: ReleaseMemberContext,
    old_map: dict[str, Path],
    max_workers: int,
    admission: MemoryAdmission | None = None,
) -> list[dict[str, object]]:
    """Per-library release comparisons in parallel, in *matched_keys* order
    (deterministic reports); see ``workflows.keyed_thread_pool`` for how the
    caller's context and memory admission reach every worker thread."""
    return run_keyed_in_threads(
        matched_keys,
        lambda key: _compare_one_library(key, ctx),
        max_workers=max_workers,
        admission=admission,
        on_error=lambda exc, key: member_dispatch_failure_entry(exc, old_map[key].name),
    )


def _compare_release_sequential(
    matched_keys: list[str],
    ctx: ReleaseMemberContext,
) -> list[dict[str, object]]:
    """Run per-library release comparisons sequentially."""
    return [
        _compare_one_library(key, ctx)
        for key in memory_trace.phase_each("release.member", matched_keys)
    ]
