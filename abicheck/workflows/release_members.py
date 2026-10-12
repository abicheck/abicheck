# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release member fan-out: every matched member compared, then the
release-level dispositions that need all of them.

Moved out of ``cli_compare_release_pairwise.py`` (which could only be reached
through Click) into the release compare owner
(:mod:`abicheck.workflows.release_compare`):

* :func:`compare_member_pair` -- one member's operands normalized (GNU ld
  linker scripts followed) and compared by
  :func:`~abicheck.workflows.member_compare.compare_member`, the per-member
  primitive the scalar compare shares. That is what makes a one-member
  release *structurally* the scalar compare of its pair.
* :func:`compare_release_member` -- the worker: that comparison plus every
  workflow fact the release folds read (exit contributions, assurance,
  type attribution, the bundle evidence stash).
* :func:`compare_release_members` -- the dispatch (parallel under a memory
  admission, or sequential) and the two passes that need every sibling:
  :func:`dispose_release_unreached_types` and
  :func:`suppress_lockstep_soname_findings`.

Report projection, ``--output-dir`` writes and terminal text go through the
caller's :class:`~abicheck.workflows.release_compare_reporter.
ReleaseCompareReporter`, at exactly the points they always ran.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..checker_types import DiffResult
from ..model.symbol_inventory import SymbolInventory
from . import cache_counters, memory_trace, release_snapshot_retention
from .crosscheck_ownership import release_level_checks, release_owned_checks_scope
from .gate import worse_release_verdict
from .keyed_thread_pool import run_keyed_in_threads
from .member_compare import compare_member
from .release_compare_reporter import (
    ComparedMember,
    ReleaseCompareReporter,
    SilentReleaseReporter,
)
from .release_member_failures import classify_member_failure, dispatch_failure_entry
from .release_member_headers import (
    ReleaseHeaderOwnership,
    member_header_operand,
    resolve_release_header_ownership,
)
from .release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_request,
)
from .release_snapshot_retention import release_junit_pairs

if TYPE_CHECKING:
    from ..pack_application import PackApplication
    from .contracts import CompareResult
    from .gate import SeverityConfig
    from .release_admission import MemoryAdmission

__all__ = [
    "ReleaseMemberContext",
    "compare_member_pair",
    "compare_release_member",
    "compare_release_members",
    "dispose_release_unreached_types",
    "normalize_member_operand",
    "release_owned_crosschecks",
    "release_parent_request",
    "suppress_lockstep_soname_findings",
]


def release_owned_crosschecks() -> frozenset[str]:
    """The checks the release level answers -- a module-level seam so a
    test can monkeypatch it."""
    return release_level_checks()


@dataclass(frozen=True)
class ReleaseMemberContext:
    """Everything one member's comparison needs, resolved once per release.

    *request* is the parent :class:`ReleaseMemberCompareRequest`; a member
    gets it plus a :class:`MemberDelta` (its operands and debug files) via
    :func:`member_request`, never a field-by-field rebuild.
    """

    request: ReleaseMemberCompareRequest
    old_map: dict[str, Path]
    new_map: dict[str, Path]
    reporter: ReleaseCompareReporter = field(default_factory=SilentReleaseReporter)
    old_debug_dir: Path | None = None
    new_debug_dir: Path | None = None
    resolve_debug_info: Callable[[Path, Path], Path | None] | None = None
    require_complete_analysis: bool = False
    severity_config: SeverityConfig | None = None
    pack_application: PackApplication | None = None
    collect_diff_results: bool = False
    retention: release_snapshot_retention.SnapshotRetention | None = None
    header_ownership: ReleaseHeaderOwnership | None = None


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


def normalize_member_operand(path: Path, notice: Callable[[str], None]) -> Path:
    """*path*, or the shared library a GNU ld linker script at *path* names.

    Followed up front so metadata/dependency analysis use the resolved DSO,
    not the text script; *notice* receives the one-line note that says so.
    """
    from .extraction import detect_binary_format, resolve_linker_script

    if detect_binary_format(path) is not None:
        return path
    target, is_ld = resolve_linker_script(path)
    if is_ld and target is not None and target.resolve() != path.resolve():
        notice(
            f"Note: '{path}' is a GNU ld linker script; following its "
            f"INPUT()/GROUP() directive to '{target}'."
        )
        return target
    return path


def compare_member_pair(
    request: ReleaseMemberCompareRequest,
    pack_application: PackApplication | None = None,
    *,
    notice: Callable[[str], None] = lambda _text: None,
) -> CompareResult:
    """Normalize one member's operands and compare them with
    :func:`~abicheck.workflows.member_compare.compare_member` -- the
    per-member primitive the scalar compare's ``run_compare_request`` shares.
    """
    assert request.old_input is not None and request.new_input is not None
    request = dataclasses.replace(
        request,
        old_input=normalize_member_operand(request.old_input, notice),
        new_input=normalize_member_operand(request.new_input, notice),
    )
    return compare_member(request, pack_application)


def compare_release_member(key: str, ctx: ReleaseMemberContext) -> dict[str, object]:
    """Compare one member -- suitable for parallel dispatch. Any exception
    yields that member's classified failure entry rather than aborting the
    release.

    The full :class:`DiffResult` is stashed under ``"_diff_result"`` for the
    release-level passes (bundle analysis, the severity fold, JUnit); a
    renderer strips every ``_``-prefixed key before serializing.
    *collect_diff_results* additionally stashes ``"_bundle_key"`` plus each
    side's bundle evidence, full or compact per *retention*
    (:mod:`abicheck.workflows.release_snapshot_retention`).
    """
    request = ctx.request
    reporter = ctx.reporter
    contract_evaluation = request.contract_evaluation
    severity_config = ctx.severity_config
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
        compare_result = compare_member_pair(
            member_request(
                request,
                MemberDelta(
                    old_input=old_path,
                    new_input=new_path,
                    old_pdb_path=old_dbg,
                    new_pdb_path=new_dbg,
                    **member_header_operand(ctx.header_ownership, key, request),
                ),
            ),
            ctx.pack_application,
            notice=reporter.notice,
        )
        result = compare_result.diff
        entry: dict[str, object] = {
            "library": old_path.name,
            "verdict": result.verdict.value,
        }
        reporter.project(
            entry,
            ComparedMember(
                key=key,
                old_path=old_path,
                compared=compare_result,
                scoped=request.scope_to_public_surface,
            ),
        )
        entry["_diff_result"] = result
        # Which of this member's type findings its own exports reach; read by
        # the shared-finding fold and `dispose_release_unreached_types`.
        from .release_member_attribution import member_type_attribution

        if type_attribution := member_type_attribution(
            (c.symbol for c in result.changes),
            compare_result.old_snapshot,
            compare_result.new_snapshot,
        ):
            entry["_type_attribution"] = dict(type_attribution)
        if ctx.collect_diff_results:
            release_snapshot_retention.stash_member_evidence(
                entry,
                key,
                compare_result.old_snapshot,
                compare_result.new_snapshot,
                ctx.retention,
            )
        # Every exit axis this member contributes to the release fold, from
        # the resolver a single-pair `compare` of it uses (ADR-064).
        from .member_compare import stamp_member_exit_contributions

        stamp_member_exit_contributions(
            entry,
            result,
            severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        )
        if contract_evaluation:
            from .gate import coverage_failure_count

            # The *count* of failures is independent of the exit floor --
            # `contract.unresolved: warn` zeroes the floor while the failures
            # stay real; without the count a warn-accepted coverage gap is
            # invisible in the release JSON.
            entry["contract_coverage_failure_count"] = coverage_failure_count(result)
        if request.scope_to_public_surface:
            # Per-library public-surface scoping outcome (ADR-024, issue
            # #235), aggregated into the release-level scope block.
            entry["scope_resolved"] = result.scope_resolved
            entry["filtered_internal_count"] = result.out_of_surface_count
        reporter.persist(entry, result)
        return entry
    except Exception as exc:
        failure = classify_member_failure(exc, library=old_path.name)
        if failure.not_comparable_kind is not None:
            reporter.persist_not_comparable(
                old_path,
                failure.not_comparable_kind,
                str(exc),
                old_version=request.old_version,
                new_version=request.new_version,
            )
        return failure.entry


def dispose_release_unreached_types(
    library_results: list[dict[str, object]],
    reporter: ReleaseCompareReporter,
    *,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool,
    contract_evaluation: bool,
) -> int:
    """Take each member's sibling-owned unreached type findings off its
    verdict; returns how many findings moved.

    A member's verdict is stamped inside its own worker, before any sibling
    is compared; whether a type the member provably does not reach is
    *reached by a sibling* is known only once every worker is done. Runs
    only over entries scoped to the public surface: the moved findings are
    recorded in that scope's ledger, so a run that turned scoping off keeps
    counting them rather than hiding them somewhere it does not render.
    """
    from .member_compare import stamp_member_exit_contributions
    from .release_member_attribution import (
        dispose_unreached_type_findings,
        unreached_types_owned_elsewhere,
    )

    attributions = {
        str(entry.get("library", "")): attribution
        for entry in library_results
        if isinstance(attribution := entry.get("_type_attribution"), dict)
    }
    plan = unreached_types_owned_elsewhere(attributions)
    moved_total = 0
    for entry in library_results:
        owners = plan.get(str(entry.get("library", "")))
        result = entry.get("_diff_result")
        if not owners or not isinstance(result, DiffResult):
            continue
        if not result.scope_to_public_surface:
            continue
        moved = dispose_unreached_type_findings(result, owners)
        if not moved:
            continue
        moved_total += len(moved)
        entry["verdict"] = result.verdict.value
        reporter.reproject(entry, result, scope_changed=True)
        stamp_member_exit_contributions(
            entry,
            result,
            severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        )
        entry["filtered_internal_count"] = result.out_of_surface_count
        reporter.persist(entry, result)
    return moved_total


def suppress_lockstep_soname_findings(
    library_results: list[dict[str, object]],
    worst_verdict: str,
    reporter: ReleaseCompareReporter,
    *,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
) -> int:
    """Drop ``SONAME_BUMP_UNNECESSARY`` when the release is a coordinated break.

    A library earns ``SONAME_BUMP_UNNECESSARY`` when *it* had no breaking
    change yet its SONAME was bumped. In a release where a sibling suffered
    a genuine *binary* ABI break, bumping every member's SONAME in lockstep
    is the correct practice, so the per-library signal is a false positive
    at the release level. Only a binary-incompatible (``BREAKING``) finding
    justifies the bump; a source-only ``API_BREAK`` does not.

    The suppression is recorded where every other suppression is: the
    result's ``suppressed_changes``/``suppressed_count`` and its disposition
    ledger (``lockstep_soname_bump``), then the member's gate contribution
    and report fields are re-stamped and its complete report re-persisted --
    each was computed before this release-wide decision could be made.
    Returns the number of findings suppressed.
    """
    if worst_verdict != "BREAKING":
        return 0
    from ..model.change_catalog.kinds import ChangeKind
    from .disposition import supersede_as_suppressed
    from .member_compare import member_exit_decision

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
        result.suppressed_changes = [*result.suppressed_changes, *unnecessary]
        result.suppressed_count += len(unnecessary)
        suppressed += len(unnecessary)
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
        # The hidden findings no longer count toward this member's gate.
        entry["_compatibility_contribution"] = member_exit_decision(
            result, severity_config, require_complete_analysis
        ).compatibility_contribution
        reporter.reproject(entry, result, scope_changed=False)
        reporter.persist(entry, result)
    return suppressed


def _member_status_notice(entry: dict[str, object]) -> str | None:
    """The one stderr line a member whose comparison did not complete owes."""
    v = str(entry["verdict"])
    if v == "ERROR":
        if "error" in entry:
            return f"Error comparing {entry['library']}: {entry['error']}"
    elif v == "not_comparable":
        if "reason" in entry:
            return f"Not comparable: {entry['library']}: {entry['reason']}"
    elif v == "unsupported":
        return f"Unsupported: {entry['library']}: {entry.get('reason', '')}"
    elif v == "failed":
        return f"Failed: {entry['library']}: {entry.get('reason', '')}"
    return None


def compare_release_members(
    compare_keys: list[str],
    ctx: ReleaseMemberContext,
    *,
    jobs: int = 0,
    depth: str | None = None,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, SymbolInventory]]]:
    """Compare every member in *compare_keys* and run the sibling passes.

    Returns ``(library_results, worst_verdict, junit_pairs)``; the pairs are
    a plain read of what the primary pass stashed (never a second
    comparison) and are empty unless *ctx* collects diff results.

    The auto default (*jobs* ``<= 0``) is sized and memory-gated by
    :func:`~abicheck.workflows.release_jobs.plan_release_workers`; a
    positive *jobs* is never clamped.
    """
    from .release_jobs import plan_release_workers

    reporter = ctx.reporter
    old_h, new_h = ctx.request.old_headers, ctx.request.new_headers
    plan = plan_release_workers(jobs, depth=depth, header_roots=bool(old_h or new_h))
    if plan.clamped_from is not None:
        reporter.notice(
            f"Note: parallel release workers reduced {plan.clamped_from} -> "
            f"{plan.initial_jobs} to fit available memory (~{plan.budget_gib:.1f} "
            "GiB/worker budget, each holding up to two full snapshots resident); "
            "set ABICHECK_RELEASE_JOB_MEM_GIB to tune the per-worker budget."
        )
    ctx = dataclasses.replace(
        ctx,
        # Each member parses the headers it claims, not the union.
        header_ownership=resolve_release_header_ownership(
            compare_keys, ctx.old_map, ctx.new_map, list(old_h), list(new_h)
        ),
    )

    # `workflows.crosscheck_ownership`: the whole-product cross-source check
    # is answered once at release level, entered around the dispatch so each
    # parallel worker inherits it. A one-member release has no union to take.
    owned = release_owned_crosschecks() if len(compare_keys) > 1 else frozenset()
    with release_owned_checks_scope(owned):
        if plan.pool_size > 1 and len(compare_keys) > 1:
            library_results = _compare_parallel(
                compare_keys, ctx, plan.pool_size, plan.admission
            )
        else:
            library_results = _compare_sequential(compare_keys, ctx)
    cache_counters.record_shared_cache_counters()
    dispose_release_unreached_types(
        library_results,
        reporter,
        severity_config=ctx.severity_config,
        require_complete_analysis=ctx.require_complete_analysis,
        contract_evaluation=ctx.request.contract_evaluation,
    )

    # Each member's held-back text and status line, after every worker
    # completed and in member order -- never from a worker thread, where
    # siblings would interleave.
    worst_verdict = "NO_CHANGE"
    for entry in library_results:
        reporter.settle(entry)
        if (status := _member_status_notice(entry)) is not None:
            reporter.notice(status)
        worst_verdict = worse_release_verdict(worst_verdict, str(entry["verdict"]))

    # A coordinated SONAME bump across a breaking release is not "unnecessary".
    suppressed_soname = suppress_lockstep_soname_findings(
        library_results,
        worst_verdict,
        reporter,
        severity_config=ctx.severity_config,
        require_complete_analysis=ctx.require_complete_analysis,
    )
    if suppressed_soname:
        reporter.notice(
            f"Note: suppressed {suppressed_soname} 'soname_bump_unnecessary' "
            "finding(s) — the release contains coordinated ABI breaks, so "
            "lockstep SONAME bumps are justified."
        )

    junit_pairs: list[tuple[DiffResult, SymbolInventory]] = (
        list(release_junit_pairs(library_results)) if ctx.collect_diff_results else []
    )
    return library_results, worst_verdict, junit_pairs


def _compare_parallel(
    compare_keys: list[str],
    ctx: ReleaseMemberContext,
    max_workers: int,
    admission: MemoryAdmission | None = None,
) -> list[dict[str, object]]:
    """Members in parallel, in *compare_keys* order (deterministic reports);
    see ``workflows.keyed_thread_pool`` for how the caller's context and
    memory admission reach every worker thread."""

    def _on_error(exc: Exception, key: str) -> dict[str, object]:
        entry, notice = dispatch_failure_entry(exc, ctx.old_map[key].name)
        ctx.reporter.notice(notice)
        return entry

    return run_keyed_in_threads(
        compare_keys,
        lambda key: compare_release_member(key, ctx),
        max_workers=max_workers,
        admission=admission,
        on_error=_on_error,
    )


def _compare_sequential(
    compare_keys: list[str], ctx: ReleaseMemberContext
) -> list[dict[str, object]]:
    """Members one after another."""
    return [
        compare_release_member(key, ctx)
        for key in memory_trace.phase_each("release.member", compare_keys)
    ]
