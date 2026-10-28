# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Release compare: execute a resolved
:class:`~abicheck.workflows.release_request.ReleaseComparePlan`.

The release fan-out's ``Request -> ResolvedPlan -> Result`` trio, completed:
:mod:`abicheck.workflows.release_request` resolves a
:class:`~abicheck.workflows.release_request.ReleaseCompareRequest` into a
plan (input discovery, pairing, ADR-065 scope evidence, the gate), and
:func:`execute_release_compare` runs that plan under
:class:`ReleaseCompareOptions` into a :class:`ReleaseCompareOutcome`:

1. every member compared (:mod:`abicheck.workflows.release_members`), each
   through the per-member primitive the scalar compare shares
   (:func:`~abicheck.workflows.member_compare.compare_member`), plus the two
   passes that need every sibling (unreached types, lockstep SONAME);
2. the members a stored side marks degraded or ``--dso-only`` could not
   classify recorded as failures, and the ADR-065 acquisition record and
   scope decision built from what ran;
3. support-promise findings (ADR-065 D1/S3), the release public surface
   reconciled against the bundle's exports, the ``--bundle-facts-out``
   baseline captured, bundle analysis and the build-configuration (probe)
   matrix folded in;
4. every exit axis folded: the contract-coverage floor and failure count,
   the assurance decision (ADR-071), the severity exit including the
   release-global contributions, and the proven-removed-member floor.

Before this module the whole sequence lived in the body of the Click
command (``cli_compare_release.compare_release_cmd``) and its three
``cli_compare_release_*`` siblings, reachable only through Click. Now the
CLI parses options, resolves the plan, calls this, and renders the outcome;
report projection, ``--output-dir`` writes and terminal text reach it through
a :class:`~abicheck.workflows.release_compare_reporter.ReleaseCompareReporter`.

**One member is the scalar path.** A one-member release runs that member
through exactly the request a single-pair ``compare`` of the same operands
builds (``release_member_request.member_compare_request``), and no sibling pass
fires at N=1 (both need a sibling). ``tests/test_release_compare_workflow.py``
pins it through this function; ``tests/test_compare_cardinality_invariance.py``
through the CLI.

The caller owns two scopes, because each must also cover work before this
call: ``policy_file.dedup_validate_overrides_warnings`` (the CLI's early
suppression validation loads the same policy file) and the plan's temporary
directories (``cleanup_release_compare_plan``). The AST acquisition scope is
entered here (it nests), so the member pass and the surface reconciliation
share parsed headers and the table is released before the folds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model.header_exclusion_record import (
    canonical_exclusion_identity,
    release_exclusion_identity,
)
from ..model.scope_acquisition import AcquisitionState
from ..model.symbol_inventory import SymbolInventory
from .gate import resolve_scope_decision, worse_release_verdict
from .release_compare_reporter import ReleaseCompareReporter, SilentReleaseReporter
from .release_scope import (
    DIRECT_PAIR_KEY,
    StrandedLibraryResolution,
    build_release_scope_record,
    out_of_scope_provider_names,
    resolve_release_scope_result,
    scoped_bundle_maps,
)

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult
    from ..checker_types import DiffResult
    from ..compile_context import CompileContext
    from ..environment_matrix import EnvironmentMatrix
    from ..model import AbiSnapshot
    from ..pack_application import PackApplication
    from ..policy.release_assurance import ReleaseAssuranceDecision
    from .gate import ScopeDecision, SeverityConfig
    from .release_public_surface import ReleaseSurfaceStage
    from .release_request import ReleaseComparePlan

__all__ = [
    "ReleaseCompareOptions",
    "ReleaseCompareOutcome",
    "collect_matrix_result",
    "execute_release_compare",
]


@dataclass(frozen=True)
class ReleaseCompareOptions:
    """How a resolved release plan is compared and concluded.

    The first group is applied to every member exactly as a single-pair
    compare applies it (forwarded through the release's parent
    :class:`~abicheck.workflows.release_member_request.
    ReleaseMemberCompareRequest`); the second configures the release-level
    stages. Inputs a plan already carries -- operands, headers, includes,
    the gate, the selection, ``on_incomplete_scope``,
    ``fail_on_removed_library`` -- are read from the plan, never restated.
    """

    old_version: str = ""
    new_version: str = ""
    lang: str = "c++"
    lang_explicit: bool = False
    suppress: Path | tuple[Path, ...] | None = None
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    scope_public_headers: bool = True
    include_dependencies: bool = False
    contract_evaluation: bool = True
    contract_mode: str | None = None
    require_complete_analysis: bool = False
    pack_application: PackApplication | None = None
    compile_context: CompileContext | None = None
    depth: str | None = None
    public_header_dirs: list[Path] | None = None
    collapse_versioned_symbols: bool = False
    project_policy_overrides: dict[Any, Any] | None = None
    env_matrix: EnvironmentMatrix | None = None
    exclude_headers: tuple[str, ...] = ()
    acknowledgments_path: Path | None = None
    acknowledgment_unacknowledged_additions: str | None = None
    acknowledgment_breaks: str | None = None
    #: ADR-065 D1/S3: the support-promise contract (``off`` emits nothing).
    support_promise: str = "off"
    manifest_path: Path | None = None
    bundle_system_providers: tuple[str, ...] = ()
    bundle_cohorts: tuple[str, ...] = ()
    probe_matrix_old: Path | None = None
    probe_matrix_new: Path | None = None
    #: Where to persist the OLD side as a ``BundleFacts`` baseline.
    bundle_facts_out: Path | None = None
    #: Keep each member's compact OLD inventory for a JUnit render
    #: (``workflows.release_snapshot_retention``).
    junit_inventory: bool = False
    #: Worker count; ``0`` auto-sizes under the memory admission.
    jobs: int = 0


@dataclass
class ReleaseCompareOutcome:
    """Everything a release render reads, decided once.

    *library_results* still carries each member's private ``_``-prefixed
    working keys (``_diff_result`` and the evidence stash); a renderer
    projects what it needs from them and strips them
    (``report.release_findings.project_release_library_results``).
    *worst_verdict* already includes every release-global contributor and
    the proven-removed-member floor.
    """

    worst_verdict: str
    library_results: list[dict[str, object]]
    junit_pairs: list[tuple[DiffResult, SymbolInventory]]
    removed_keys: list[str]
    added_keys: list[str]
    old_map: dict[str, Path]
    new_map: dict[str, Path]
    warnings: list[str]
    scope_decision: ScopeDecision
    assurance_decision: ReleaseAssuranceDecision
    surface_stage: ReleaseSurfaceStage
    type_attribution: dict[str, dict[str, str]]
    severity_config: SeverityConfig | None
    severity_exit_code: int | None
    contract_coverage_exit_contribution: int
    contract_coverage_failure_count: int
    env_matrix_source_sha256: str | None
    excluded_header_patterns: str
    bundle_result: BundleDiffResult | None = None
    matrix_result: DiffResult | None = None


def collect_matrix_result(
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    policy: str,
    worst_verdict: str,
    *,
    suppress: Path | tuple[Path, ...] | None = None,
    policy_file_path: Path | None = None,
    old_version: str = "",
    new_version: str = "",
    pack_application: PackApplication | None = None,
    project_policy_overrides: Any = None,
) -> tuple[DiffResult | None, str]:
    """Run the build-configuration (probe) matrix findings through the
    compare pipeline and fold their verdict.

    Returns ``(matrix_result, worst_verdict)``; ``None`` and the unchanged
    verdict when no matrix was given or it holds no change. The findings
    (``CXX_STANDARD_FLOOR_RAISED``, ``API_DEPENDS_ON_CONSUMER_ENV``,
    ``BEHAVIOURAL_DEFAULT_CHANGED``) are release-global: they ride in as
    ``extra_changes`` over two empty snapshots -- the path the single-pair
    ``compare`` uses -- so ``--suppress``, the scoring policy (``--pack`` and
    ``.abicheck.yml`` overrides folded in by
    :func:`~abicheck.pack_application.resolve_bundle_policy_file`, the
    resolver the bundle stage shares) and verdict composition all apply.

    A one-sided matrix is a :class:`~abicheck.errors.ReleaseOperandUsageError`.
    """
    from ..errors import ReleaseOperandUsageError, ValidationError
    from ..pack_application import resolve_bundle_policy_file
    from .compare_policy import compare_snapshots, load_suppression_and_policy
    from .pair_evidence import load_probe_matrix_changes
    from .snapshot_factory import new_snapshot

    try:
        matrix_changes = load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    except ValidationError as exc:
        raise ReleaseOperandUsageError(str(exc)) from exc
    if not matrix_changes:
        return None, worst_verdict

    suppression, _ = load_suppression_and_policy(suppress, policy, None)
    pf = resolve_bundle_policy_file(
        suppress,
        policy,
        policy_file_path,
        pack_application,
        project_policy_overrides,
    )
    name = "<build-config matrix>"
    result = compare_snapshots(
        new_snapshot(library=name, version=old_version or "old"),
        new_snapshot(library=name, version=new_version or "new"),
        suppression=suppression,
        policy=policy,
        policy_file=pf,
        scope_to_public_surface=False,
        extra_changes=matrix_changes,
    )
    return result, worse_release_verdict(worst_verdict, result.verdict.value)


def _int_entries(library_results: list[dict[str, object]], key: str) -> list[int]:
    return [
        value
        for entry in library_results
        if isinstance(entry, dict) and isinstance(value := entry.get(key, 0), int)
    ]


def execute_release_compare(
    plan: ReleaseComparePlan,
    options: ReleaseCompareOptions,
    *,
    reporter: ReleaseCompareReporter | None = None,
) -> ReleaseCompareOutcome:
    """Compare every member of *plan* and conclude the release.

    *reporter* (default: :class:`~abicheck.workflows.
    release_compare_reporter.SilentReleaseReporter`) receives the member
    projection hooks and every notice/warning this run emits; warnings are
    also returned in the outcome. Raises the typed
    :class:`~abicheck.errors.ReleaseOperandError` family for an unusable
    ``--manifest``/embedded manifest, ``--bundle-facts-out`` or one-sided
    probe matrix; a member's own failure never raises -- it becomes that
    member's entry.
    """
    from ..pack_application import resolve_bundle_policy_file
    from ..policy.release_outcome import apply_removed_member_floor
    from .comparison_input_receipt import env_matrix_content_digest
    from .extraction import ast_acquisition_scope, resolve_package_debug_info
    from .header_exclusion_audit import observed_member_exclusion_identities
    from .release_assurance_members import release_assurance_from_entries
    from .release_bundle import (
        collect_bundle_result,
        resolve_bundle_manifest,
        write_bundle_facts_out,
    )
    from .release_exit_fold import (
        fold_release_global_severity,
        release_severity_exit_code,
    )
    from .release_inputs import wheel_release_env_matrix
    from .release_members import (
        ReleaseMemberContext,
        compare_release_members,
        release_parent_request,
    )
    from .release_public_surface import (
        reconcile_release_public_surface,
        release_surface_severity_exit,
        release_surface_verdict,
    )
    from .release_scope_notices import release_scope_warnings
    from .release_snapshot_retention import (
        release_old_snapshot_pairs,
        resolve_snapshot_retention,
    )
    from .release_stored_inventory import stored_side_degraded_members
    from .release_support_promise import support_promise_results

    reporter = reporter if reporter is not None else SilentReleaseReporter()
    o = options
    request = plan.request
    old_dir, new_dir = request.old_dir, request.new_dir
    scope_plan = plan.scope
    old_map, new_map = dict(scope_plan.old_map), dict(scope_plan.new_map)
    matched_keys = list(scope_plan.matched_keys)
    old_h, new_h = list(plan.old_headers), list(plan.new_headers)
    old_inc, new_inc = list(plan.old_includes), list(plan.new_includes)
    warning_msgs = list(plan.warnings)
    old_unclassified, new_unclassified = plan.old_unclassified, plan.new_unclassified
    old_inventory, new_inventory = plan.old_inventory, plan.new_inventory
    gate = plan.gate
    severity_config = gate.severity
    degraded = plan.degraded
    assert degraded is not None  # always resolved by resolve_release_compare_plan
    compare_keys = plan.compare_keys
    # G26/G27: a wheel's own tag and METADATA become the deployment contract
    # its members are checked against, unless `runtime_floors` was declared.
    env_matrix = wheel_release_env_matrix(o.env_matrix, new_dir)
    # The release's resolved configuration: the caller's (the CLI resolves it
    # from the flags the user typed), or resolved here from the typed options
    # through the same resolver, so a typed release records on each member
    # what the identical typed scalar compare records.
    pack_application = (
        o.pack_application
        if o.pack_application is not None
        else _resolve_typed_release_configuration(plan, o)
    )

    with ast_acquisition_scope() as ast_scope:
        member_ctx = ReleaseMemberContext(
            request=release_parent_request(
                pack_application,
                old_headers=old_h,
                new_headers=new_h,
                old_includes=old_inc,
                new_includes=new_inc,
                old_version=o.old_version,
                new_version=o.new_version,
                lang=o.lang,
                lang_explicit=o.lang_explicit,
                suppress=o.suppress,
                policy=o.policy,
                policy_file_path=o.policy_file_path,
                scope_to_public_surface=o.scope_public_headers,
                include_dependencies=o.include_dependencies,
                contract_evaluation=o.contract_evaluation,
                contract_mode=o.contract_mode,
                compile_context=o.compile_context,
                depth=o.depth,
                public_header_dirs=o.public_header_dirs,
                collapse_versioned_symbols=o.collapse_versioned_symbols,
                project_policy_overrides=o.project_policy_overrides,
                env_matrix=env_matrix,
                exclude_headers=o.exclude_headers,
                acknowledgments_path=o.acknowledgments_path,
                acknowledgment_unacknowledged_additions=o.acknowledgment_unacknowledged_additions,
                acknowledgment_breaks=o.acknowledgment_breaks,
            ),
            old_map=old_map,
            new_map=new_map,
            reporter=reporter,
            old_debug_dir=plan.old_debug_dir,
            new_debug_dir=plan.new_debug_dir,
            resolve_debug_info=resolve_package_debug_info,
            require_complete_analysis=o.require_complete_analysis,
            severity_config=severity_config,
            pack_application=pack_application,
            # Bundle analysis always runs and reads each member's diff
            # results and evidence (G38 Phase 4).
            collect_diff_results=True,
            # JUnit and `--bundle-facts-out` read the OLD side only; what each
            # needs kept is owned by `workflows.release_snapshot_retention`.
            retention=resolve_snapshot_retention(
                junit=o.junit_inventory,
                bundle_facts_out=o.bundle_facts_out is not None,
            ),
        )
        library_results, worst_verdict, junit_pairs = compare_release_members(
            compare_keys, member_ctx, jobs=o.jobs, depth=o.depth
        )

        # ADR-065 D8: a matched member a stored package marks degraded is not
        # compared (its snapshot is the ELF-only stand-in) but recorded
        # `failed` on the scope axis.
        degraded_matched = degraded.matched
        for key in matched_keys:
            if key in degraded_matched:
                library_results.append(
                    {
                        "library": old_map[key].name,
                        "verdict": "failed",
                        "reason": degraded_matched[key],
                    }
                )
                reporter.notice(f"Failed: {old_map[key].name}: {degraded_matched[key]}")

        # ADR-065 D1/D2/D6/D7 (S2): the per-member acquisition record. From
        # here on removed/added are the *proven* sets (empty unless the
        # lacking side's inventory is proven complete).
        scope_failed = {
            "old_failed": {**degraded.old_unmatched, **old_unclassified},
            "new_failed": {**degraded.new_unmatched, **new_unclassified},
            # ADR-065 S3: declared by the inventory, absent after extraction.
            "old_unproduced": dict(old_inventory.unproduced)
            if old_inventory is not None
            else {},
            "new_unproduced": dict(new_inventory.unproduced)
            if new_inventory is not None
            else {},
        }
        release_selection = request.release_selection
        if release_selection is not None and not (
            scope_plan.evidence.direct_pair or matched_keys == [DIRECT_PAIR_KEY]
        ):
            # ADR-065 S1: an explicit selection overrides D9's inference.
            from .release_plan import build_declared_selection_record

            scope_record = build_declared_selection_record(
                scope_plan.old_map,
                scope_plan.new_map,
                scope_plan.matched_keys,
                library_results,
                scope_plan.evidence,
                release_selection,
                **scope_failed,
            )
        else:
            scope_record = build_release_scope_record(
                scope_plan.old_map,
                scope_plan.new_map,
                scope_plan.matched_keys,
                library_results,
                scope_plan.evidence,
                **scope_failed,
            )
        scope_result = resolve_release_scope_result(scope_plan, scope_record)
        # A member --dso-only could not classify is this run's own
        # acquisition failure -- an operational `ERROR` -- unless D9 narrowed
        # it out of scope, in which case it is listed on the record only.
        scope_states = {m.member: m.state for m in scope_result.record.members}
        for key in sorted(set(old_unclassified) | set(new_unclassified)):
            reason = old_unclassified.get(key) or new_unclassified[key]
            if scope_states.get(key) is AcquisitionState.OUT_OF_SCOPE:
                continue
            library_results.append(
                {"library": key, "verdict": "ERROR", "error": reason}
            )
            worst_verdict = worse_release_verdict(worst_verdict, "ERROR")
            reporter.notice(f"Failed: {key}: {reason}")
        # Decided by policy once; every consumer reads this one decision.
        scope_decision = resolve_scope_decision(
            scope_result.record, request.on_incomplete_scope
        )
        removed_keys = [m.member for m in scope_result.record.proven_removed_members]
        added_keys = [m.member for m in scope_result.record.proven_added_members]
        # ADR-065 S4: unmatched/removed/added notices from the record, so a
        # line says "removed" only when an inventory proves it.
        for msg in release_scope_warnings(scope_result.record):
            warning_msgs.append(msg)
            reporter.warning(msg)
        # ADR-065 D1/S3: support-promise changes become ordinary
        # per-component results, so every fold and renderer sees them.
        for entry in support_promise_results(scope_result.record, o.support_promise):
            library_results.append(entry)
            worst_verdict = worse_release_verdict(worst_verdict, str(entry["verdict"]))

        # One product contract, many binary providers: each side's public
        # surface acquired once and reconciled against the union of the
        # bundle's exports while the per-member export evidence is stashed.
        # The one scoring policy every release-level path reads.
        release_policy_file = resolve_bundle_policy_file(
            o.suppress,
            o.policy,
            o.policy_file_path,
            pack_application,
            o.project_policy_overrides,
        )
        surface_stage = reconcile_release_public_surface(
            library_results,
            # Keyed by the OLD path's basename, the spelling each member
            # entry's `library` carries.
            expected_members=[
                (old_map[key] if key in old_map else new_map[key]).name
                for key in compare_keys
            ],
            old_headers=old_h,
            new_headers=new_h,
            old_includes=old_inc,
            new_includes=new_inc,
            lang=o.lang,
            lang_explicit=o.lang_explicit,
            exclude_headers=o.exclude_headers,
            public_header_dirs=o.public_header_dirs,
            compile_context=o.compile_context,
            depth=o.depth,
            include_dependencies=o.include_dependencies,
        )
        # The last reader of this request's parsed header ASTs; flush the
        # table so the tail does not carry roots nobody reads again.
        ast_scope.release_completed()

        worst_verdict = worse_release_verdict(
            worst_verdict,
            release_surface_verdict(
                surface_stage, policy=o.policy, policy_file=release_policy_file
            ),
        )
        warning_msgs.extend(surface_stage.warnings)

        if o.bundle_facts_out is not None:
            write_bundle_facts_out(
                o.bundle_facts_out,
                # The whole OLD snapshot `old_full` retention kept, not
                # JUnit's compact inventory.
                release_old_snapshot_pairs(library_results),
                o.manifest_path,
                old_map,
                resolve_stranded_library=lambda old_path: _resolve_stranded_library(
                    old_path, plan, o, reporter
                ),
                # ADR-065 D8: a stored OLD package's own marker survives.
                inherited_degraded=stored_side_degraded_members(
                    old_dir, variant_id=request.old_variant
                ),
                # ADR-065 D2: every member OLD enumerated was walked --
                # unless --dso-only left one unclassified.
                inventory_complete=not old_unclassified,
                public_surface=surface_stage.old_surface,
                # ADR-062 A1.7: OLD's own manifest only -- this baseline
                # describes OLD alone.
                resolved_manifest=resolve_bundle_manifest(
                    o.manifest_path,
                    old_dir,
                    new_dir,
                    old_map,
                    new_map,
                    old_variant=request.old_variant,
                    new_variant=request.new_variant,
                    old_side_only=True,
                ),
            )

        # ADR-049 Phase 7: the contract-coverage floor, `max`-folded so one
        # member's incomplete evidence raises the release exit; the failure
        # count is summed (it stays real under `contract.unresolved: warn`).
        contract_coverage_exit_contribution = max(
            _int_entries(library_results, "contract_coverage_exit_contribution"),
            default=0,
        )
        contract_coverage_failure_count = sum(
            _int_entries(library_results, "contract_coverage_failure_count")
        )
        # ADR-071 D1/D2: the assurance axis, `max`-folded; the release
        # contract's own shortfall joins it.
        assurance_decision = release_assurance_from_entries(
            library_results,
            require_complete=o.require_complete_analysis,
            release_shortfalls=surface_stage.assurance_shortfalls(),
        )
        # Severity-aware exit, while per-member results are still stashed.
        severity_exit_code = (
            None
            if severity_config is None
            else release_severity_exit_code(library_results, gate)
        )

        # Bundle-level cross-library analysis always runs. ADR-065 D2: the
        # bundle graph sees matched members and *proven* removals/additions.
        bundle_old_map, bundle_new_map = scoped_bundle_maps(
            old_map, new_map, scope_result.record
        )
        bundle_result, worst_verdict = collect_bundle_result(
            library_results,
            bundle_old_map,
            bundle_new_map,
            worst_verdict,
            manifest_path=o.manifest_path,
            bundle_system_providers=(
                *o.bundle_system_providers,
                *out_of_scope_provider_names(scope_result.record),
            ),
            bundle_cohorts=o.bundle_cohorts,
            policy=o.policy,
            policy_file=release_policy_file,
            old_root=old_dir,
            new_root=new_dir,
            old_variant=request.old_variant,
            new_variant=request.new_variant,
            scope_record=scope_result.record,
            notice=reporter.notice,
        )

        # Read before a renderer strips the `_diff_result` carrying it: a
        # stored snapshot is never restamped with the current
        # `--exclude-header`, so the *observed* identity is the fact.
        observed_member_exclusions = observed_member_exclusion_identities(
            library_results
        )
        type_attribution: dict[str, dict[str, str]] = {
            str(entry.get("library", "")): dict(attribution)
            for entry in library_results
            if isinstance(entry, dict)
            and isinstance(attribution := entry.get("_type_attribution"), dict)
        }
        worst_verdict = apply_removed_member_floor(
            worst_verdict, removed=bool(removed_keys)
        )

        matrix_result, worst_verdict = collect_matrix_result(
            o.probe_matrix_old,
            o.probe_matrix_new,
            o.policy,
            worst_verdict,
            suppress=o.suppress,
            policy_file_path=o.policy_file_path,
            old_version=o.old_version,
            new_version=o.new_version,
            pack_application=pack_application,
            project_policy_overrides=o.project_policy_overrides,
        )

        # Release-global bundle/matrix/surface findings belong to no member,
        # so they fold into the severity exit here -- a clean-per-member
        # release with a release-level break is not masked.
        if severity_exit_code is not None:
            severity_exit_code = fold_release_global_severity(
                severity_exit_code, bundle_result, matrix_result, gate
            )
            severity_exit_code = max(
                severity_exit_code,
                release_surface_severity_exit(
                    surface_stage,
                    gate.severity,
                    policy=o.policy,
                    policy_file=release_policy_file,
                ),
            )

    return ReleaseCompareOutcome(
        worst_verdict=worst_verdict,
        library_results=library_results,
        junit_pairs=junit_pairs,
        removed_keys=removed_keys,
        added_keys=added_keys,
        old_map=old_map,
        new_map=new_map,
        warnings=warning_msgs,
        scope_decision=scope_decision,
        assurance_decision=assurance_decision,
        surface_stage=surface_stage,
        type_attribution=type_attribution,
        severity_config=severity_config,
        severity_exit_code=severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        contract_coverage_failure_count=contract_coverage_failure_count,
        # The release-wide deployment-floor digest from the resolved matrix
        # itself, correct however many members completed.
        env_matrix_source_sha256=env_matrix_content_digest(env_matrix),
        # The release's one canonical `--exclude-header` identity: what the
        # members observed, the request only when none completed.
        excluded_header_patterns=release_exclusion_identity(
            observed_member_exclusions,
            canonical_exclusion_identity(o.exclude_headers),
        ),
        bundle_result=bundle_result,
        matrix_result=matrix_result,
    )


def _resolve_typed_release_configuration(
    plan: ReleaseComparePlan, options: ReleaseCompareOptions
) -> PackApplication:
    """The release's configuration for a typed caller: *options* and the
    plan's gate stated at the ``API_REQUEST`` tier, the way
    ``abicheck.workflows.compare_input_adapters.compare_request_inputs`` states a
    typed scalar request, plus the project overrides at ``PROJECT_CONFIG``.
    """
    from ..compatibility_evaluation_frontend import (
        ExplicitCompatibilityInputs,
        FrontEnd,
    )
    from .compare_configuration import resolve_release_pack_application
    from .compare_gate_receipt import project_inputs_for_overrides
    from .compare_input_sources import SuppressionSource
    from .evaluation_identities import stated_policy_base
    from .ownership_contract_inputs import header_dir_spellings
    from .policy_file import PolicyFile

    policy_file = (
        PolicyFile.load(options.policy_file_path)
        if options.policy_file_path is not None
        else None
    )
    request = plan.request
    explicit = ExplicitCompatibilityInputs(
        contract_mode=options.contract_mode,
        # Like a typed request: only `False` (the legacy alias for `all`)
        # states a domain.
        scope_public_headers=None if options.scope_public_headers else False,
        policy_base=stated_policy_base(options.policy, policy_file),
        policy_file=policy_file,
        suppression=(SuppressionSource.from_paths(options.suppress)),
        severity_preset=request.severity_preset,
        severity_abi_breaking=request.severity_abi_breaking,
        severity_potential_breaking=request.severity_potential_breaking,
        severity_quality_issues=request.severity_quality_issues,
        severity_addition=request.severity_addition,
        header_dirs=header_dir_spellings(
            (*plan.new_headers, *(options.public_header_dirs or ()))
        ),
    )
    return resolve_release_pack_application(
        explicit,
        front_end=FrontEnd.API,
        project=project_inputs_for_overrides(
            (options.project_policy_overrides or {}).items()
        ),
        policy_file=policy_file,
        base_policy=options.policy,
        contract_evaluation=options.contract_evaluation,
    )


def _resolve_stranded_library(
    old_path: Path,
    plan: ReleaseComparePlan,
    options: ReleaseCompareOptions,
    reporter: ReleaseCompareReporter,
) -> StrandedLibraryResolution:
    """Dump one OLD library no successful member pair captured, for the
    ``--bundle-facts-out`` baseline.

    The same ``DumpRequest -> resolve_dump_request -> execute_dump_request``
    pipeline ``dump`` uses (ADR-063 D1), with the extraction context every
    member was compared under -- including the canonical
    ``--exclude-header`` rules, so it never re-parses a header tree the run
    was told how to narrow. A failure degrades to an ELF-only snapshot that
    *carries* its failure (ADR-065 D8: persisted as a degraded member), with
    a notice -- never an abort and never a silently impoverished OLD side.
    """
    from . import extraction
    from .dump.request import execute_dump_request, resolve_dump_request
    from .request_inputs import InputSpec
    from .service_requests import DumpRequest
    from .snapshot_factory import new_snapshot

    old_dbg = (
        extraction.resolve_package_debug_info(old_path, plan.old_debug_dir)
        if plan.old_debug_dir
        else None
    )
    try:
        dump_request = DumpRequest(
            input=InputSpec.of(
                old_path,
                headers=list(plan.old_headers),
                includes=list(plan.old_includes),
                version=options.old_version,
                pdb=old_dbg,
                compile=options.compile_context,
                include_dependencies=options.include_dependencies,
                public_header_dirs=options.public_header_dirs,
                exclude_headers=options.exclude_headers,
            ),
            lang=options.lang,
            lang_explicit=options.lang_explicit,
            depth=options.depth,
        )
        resolved = resolve_dump_request(dump_request)
        return StrandedLibraryResolution(execute_dump_request(resolved).snapshot)
    except Exception as exc:
        from ..elf_metadata import ElfMetadata
        from ..extract.parse_failures import recording_export_read_failures

        reporter.notice(f"{old_path.name}: ELF-only ({exc})")
        with recording_export_read_failures() as export_failures:
            elf = extraction.parse_elf_metadata(old_path)
        failure = f"ELF-only degraded capture: {exc}"
        if export_failures:
            # The same rule the dump applies: a table that was not read is
            # not persisted as a partial one. The member keeps an unread
            # export table, which every reader already treats as FAILED.
            elf = ElfMetadata()
            reasons = "; ".join(dict.fromkeys(export_failures))
            failure += f"; export table not read: {reasons}"
        snapshot: AbiSnapshot = new_snapshot(
            library=old_path.name,
            version="",
            elf=elf,
            # ADR-065 D8: no header/DWARF evidence at all -- every detector
            # that special-cases elf_only_mode must see the reduced tier.
            elf_only_mode=True,
        )
        return StrandedLibraryResolution(snapshot, failure=failure)
