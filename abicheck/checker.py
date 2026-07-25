# Copyright 2026 Nikolay Petrov
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

"""Checker — diff two AbiSnapshots, classify changes, produce a verdict."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    diff_abi_tags,  # noqa: F401 — triggers detector registration
    diff_atomic,  # noqa: F401 — triggers detector registration
    diff_bit_int,  # noqa: F401 — triggers detector registration
    diff_char8t,  # noqa: F401 — triggers detector registration
    diff_integer_model,  # noqa: F401 — triggers detector registration
)
from .checker_policy import (
    API_BREAK_KINDS as _API_BREAK_KINDS,
    BREAKING_KINDS as _BREAKING_KINDS,
    COMPATIBLE_KINDS as _COMPATIBLE_KINDS,
    RISK_KINDS as _RISK_KINDS,
    ChangeKind,
    EvidenceTier,
    Verdict,
    compute_verdict,
)
from .checker_types import (  # noqa: F401
    Change,
    DetectorSpec,
    DiffResult,
    LibraryMetadata,
)
from .confidence import _compute_confidence
from .detector_registry import registry as _detector_registry
from .diff_elf_layout import (  # noqa: F401 — triggers detector registration
    _diff_elf_layout,
)
from .diff_filtering import (  # noqa: F401
    _ROOT_TYPE_CHANGE_KINDS,
    _deduplicate_ast_dwarf,
    _deduplicate_cross_detector,
    _downgrade_opaque_struct_changes,
    _downgrade_opaque_type_changes,
    _enrich_affected_symbols,
    _enrich_source_locations,
    _filter_opaque_size_changes,
    _filter_redundant,
    _filter_reserved_field_renames,
    _match_root_type,
)
from .diff_kabi import (  # noqa: F401 — triggers detector registration
    _diff_kabi,
)
from .diff_layout import (  # noqa: F401 — triggers detector registration
    _diff_layout_descriptor,
)
from .diff_long_double import (  # noqa: F401 — triggers detector registration
    _diff_long_double,
)
from .diff_platform import (  # noqa: F401
    _diff_dwarf,
    _diff_elf,
    _diff_elf_deleted_fallback,
    _diff_elf_symbol_metadata,
    _diff_leaked_dependency_symbols,
    _diff_macho,
    _diff_pe,
    _diff_struct_layouts,
    _diff_template_inner_types,
    _extract_template_args,
    _template_outer,
)
from .diff_python import _diff_python_ext  # noqa: F401 — triggers detector registration
from .diff_python_api import (
    _diff_python_api,  # noqa: F401 — triggers detector registration
)
from .diff_reconcile import reconcile_build_context as reconcile_build_context_findings
from .diff_stdlib_impl import (  # noqa: F401 — triggers detector registration
    _diff_stdlib_implementation,
)
from .diff_sycl import _diff_sycl  # noqa: F401 — triggers detector registration
from .diff_symbols import _PUBLIC_VIS
from .diff_time64 import (  # noqa: F401 — triggers detector registration
    _diff_time64_abi,
)
from .diff_types import (  # noqa: F401
    _diff_const_overloads,
    _diff_enum_renames,
    _diff_enums,
    _diff_field_qualifiers,
    _diff_field_renames,
    _diff_method_qualifiers,
    _diff_reserved_fields,
    _diff_type_kind_changes,
    _diff_typedefs,
    _diff_types,
    _diff_unions,
    _diff_var_values,
    _is_version_stamped_typedef,
)
from .diff_unnamed_types import (  # noqa: F401 — triggers detector registration
    _diff_unnamed_types,
)
from .diff_versioning import (  # noqa: F401 — re-export for testing
    check_soname_bump_policy,
    detect_version_node_changes,
    detect_version_script_missing,
)
from .diff_vtable_layout import (  # noqa: F401 — triggers detector registration
    _diff_vtable_layout,
)
from .dwarf_advanced import (
    diff_advanced_dwarf,  # noqa: F401 — re-export for monkeypatching
)
from .model import AbiSnapshot
from .policy_file import PolicyFile

if TYPE_CHECKING:
    from .environment_matrix import EnvironmentMatrix
    from .post_processing import PipelineContext
    from .suppression import SuppressionList

__all__ = [
    "ChangeKind",
    "Verdict",
    "_BREAKING_KINDS",
    "_COMPATIBLE_KINDS",
    "_API_BREAK_KINDS",
    "_RISK_KINDS",
    "_SOURCE_BREAK_KINDS",  # deprecated alias
    "Change",
    "LibraryMetadata",
    "DiffResult",
    "compare",
    "_ROOT_TYPE_CHANGE_KINDS",
]

# Deprecated alias — kept for external consumers; will be removed in v2.0
_SOURCE_BREAK_KINDS = _API_BREAK_KINDS


# _DetectorSpec is now DetectorSpec in checker_types; keep alias for internal use.
_DetectorSpec = DetectorSpec


def _compute_verdict_for(
    all_unsuppressed: list[Change],
    policy: str,
    policy_file: PolicyFile | None,
) -> Verdict:
    """Compute verdict using either a PolicyFile or the named policy profile."""
    if policy_file is not None:
        return policy_file.compute_verdict(all_unsuppressed)
    return compute_verdict(all_unsuppressed, policy=policy)


def _filter_suppressed_changes(
    changes: list[Change],
    suppression: SuppressionList | None,
    suppressed: list[Change],
) -> list[Change]:
    """Remove suppressed advisories (SONAME/platform-floor) from *changes*,
    appending them to *suppressed* in-place. Returns the visible subset.

    Uses :meth:`SuppressionList.evaluate` (not the cheaper ``is_suppressed``)
    so a broad rule whose selectors matched a public-reachable change but was
    withheld by the reachability/``allow_public_break`` gate still produces
    the same ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic
    ``ApplySuppression`` produces for changes it sees directly (ADR-044 D4;
    P1 item 6) — mirrors ``_filter_pattern_synthetic``'s established pattern
    for this same plain-``SuppressionList``-plus-``list[Change]`` shape.
    """
    if suppression is None or not changes:
        return changes
    from .post_processing import (
        _build_suppression_overreach_change,
        _build_suppression_unknown_reachability_change,
    )

    visible: list[Change] = []
    diagnostics: list[Change] = []
    for c in changes:
        outcome = suppression.evaluate(c)
        if outcome.suppressed:
            c.suppression_rule = outcome.rule_label()
            suppressed.append(c)
            continue
        visible.append(c)
        if outcome.withheld_rule is not None:
            diagnostics.append(_build_suppression_overreach_change(c, outcome.withheld_rule))
        if outcome.withheld_unknown_rule is not None:
            diagnostics.append(
                _build_suppression_unknown_reachability_change(c, outcome.withheld_unknown_rule)
            )
    visible.extend(diagnostics)
    return visible


def _apply_surface_metrics(
    old: AbiSnapshot,
    new: AbiSnapshot,
    kept: list[Change],
    verdict_redundant: list[Change],
    suppressed: list[Change],
    suppression: SuppressionList | None,
    policy: str,
    policy_file: PolicyFile | None,
    current_verdict: Verdict,
) -> tuple[list[Change], Verdict]:
    """Compute aggregate surface-metric findings (ADR-027 A1/D1.2) and return
    the updated *kept* list and (possibly recomputed) *verdict*.

    Called only when ``surface_metrics=True``.  *current_verdict* is the
    verdict already established before this step; it is returned unchanged
    when no new metric findings are visible.
    """
    from .diff_surface_metrics import diff_surface_metrics

    visible = _filter_suppressed_changes(
        list(diff_surface_metrics(old, new)), suppression, suppressed
    )
    if not visible:
        return kept, current_verdict
    kept.extend(visible)
    # These roll-ups are COMPATIBLE, never breaking, but they are still
    # changes: appending them after `verdict` was computed above would leave
    # a NO_CHANGE verdict alongside e.g. a `public_surface_grew` finding,
    # making the CLI/JSON summary inconsistent with the finding set. Recompute
    # so NO_CHANGE flips to COMPATIBLE when the only findings are these
    # roll-ups (ADR-027 review).
    return kept, _compute_verdict_for(kept + verdict_redundant, policy, policy_file)


def _filter_pattern_synthetic(
    kept: list[Change],
    pre_pattern_count: int,
    suppression: SuppressionList,
    suppressed: list[Change],
    pattern_modulations: list[dict[str, object]],
) -> tuple[list[Change], list[dict[str, object]]]:
    """Filter newly-added synthetic pattern findings through suppression.

    Moves suppressed synthetics from *kept* to *suppressed* and prunes them
    from *pattern_modulations*.  Returns the updated (kept, pattern_modulations)
    pair.  Called only when suppression is active and new synthetic items exist.

    Uses :meth:`SuppressionList.evaluate` (not the cheaper ``is_suppressed``)
    so a broad rule whose selectors matched a public-reachable pattern
    finding (e.g. ``OPAQUE_INVARIANT_BROKEN``) but was withheld by the
    reachability/``allow_public_break`` gate still gets the same
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic ``ApplySuppression``
    produces for changes it sees directly (ADR-044 D4; Codex review, fresh
    evidence) — this ADR-027 pattern-verdict path builds its findings well
    after ``post_processing.DEFAULT_PIPELINE`` runs, so nothing else would
    ever emit that diagnostic for them.
    """
    from .post_processing import (
        _build_suppression_overreach_change,
        _build_suppression_unknown_reachability_change,
    )

    retained = kept[:pre_pattern_count]
    diagnostics: list[Change] = []
    suppressed_synthetic: set[tuple[str, str | None]] = set()
    for c in kept[pre_pattern_count:]:
        outcome = suppression.evaluate(c)
        if outcome.suppressed:
            c.suppression_rule = outcome.rule_label()
            suppressed.append(c)
            # Drop this synthetic finding's disclosure row too, so a
            # fully-suppressed handle/opaque/anti-pattern transition does
            # not linger in the pattern_modulations ledger while it is
            # absent from `changes` and the verdict (ADR-027 review).
            suppressed_synthetic.add((c.symbol, c.modulation_rule))
            continue
        retained.append(c)
        if outcome.withheld_rule is not None:
            diagnostics.append(_build_suppression_overreach_change(c, outcome.withheld_rule))
        if outcome.withheld_unknown_rule is not None:
            diagnostics.append(
                _build_suppression_unknown_reachability_change(c, outcome.withheld_unknown_rule)
            )
    retained.extend(diagnostics)
    if suppressed_synthetic:
        pattern_modulations = [
            m
            for m in pattern_modulations
            if (m.get("symbol"), m.get("rule_id")) not in suppressed_synthetic
        ]
    return retained, pattern_modulations


def _apply_pattern_verdicts_step(
    old: AbiSnapshot,
    new: AbiSnapshot,
    kept: list[Change],
    verdict_redundant: list[Change],
    suppressed: list[Change],
    suppression: SuppressionList | None,
    policy: str,
    policy_file: PolicyFile | None,
    evidence_tier: EvidenceTier,
    current_verdict: Verdict,
) -> tuple[list[Change], Verdict, list[dict[str, object]]]:
    """Apply ADR-027 A4 pattern-aware verdict modulation.

    Returns the updated *kept* list, (possibly recomputed) *verdict*, and the
    *pattern_modulations* ledger.  Called only when ``pattern_verdicts=True``.
    *current_verdict* is returned unchanged when pattern_modulations is empty.
    """
    from .pattern_verdicts import apply_pattern_verdicts

    pre_pattern_count = len(kept)
    # A user policy override on a kind is authoritative: a pattern demotion
    # must not lower it, or the aggregate verdict (which applies the
    # override) would disagree with per-finding classification (ADR-027
    # review). Protect every explicitly-overridden kind from demotion.
    protected_kinds = (
        frozenset(policy_file.overrides) if policy_file is not None else frozenset()
    )
    pattern_modulations: list[dict[str, object]] = apply_pattern_verdicts(
        kept, old, new, evidence_tier=evidence_tier, protected_kinds=protected_kinds
    )

    if suppression is not None and len(kept) > pre_pattern_count:
        kept, pattern_modulations = _filter_pattern_synthetic(
            kept, pre_pattern_count, suppression, suppressed, pattern_modulations
        )

    if pattern_modulations:
        return kept, _compute_verdict_for(kept + verdict_redundant, policy, policy_file), pattern_modulations
    return kept, current_verdict, pattern_modulations


@_detector_registry.detector(
    "advanced_dwarf",
    requires_support=lambda o, n: (
        o.dwarf_advanced is not None and n.dwarf_advanced is not None,
        "missing DWARF advanced metadata",
    ),
)
def _diff_advanced_dwarf(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Sprint 4: calling convention, packing, toolchain flag drift.

    Kept in checker.py (not diff_platform) so that tests can monkeypatch
    ``checker_mod.diff_advanced_dwarf`` and have the patch take effect.
    """
    from .dwarf_advanced import AdvancedDwarfMetadata

    o: AdvancedDwarfMetadata = (
        getattr(old, "dwarf_advanced", None) or AdvancedDwarfMetadata()
    )
    n: AdvancedDwarfMetadata = (
        getattr(new, "dwarf_advanced", None) or AdvancedDwarfMetadata()
    )

    _kind_map = {
        "calling_convention_changed": ChangeKind.CALLING_CONVENTION_CHANGED,
        "value_abi_trait_changed": ChangeKind.VALUE_ABI_TRAIT_CHANGED,
        "struct_return_convention_changed": ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED,
        "struct_packing_changed": ChangeKind.STRUCT_PACKING_CHANGED,
        "toolchain_flag_drift": ChangeKind.TOOLCHAIN_FLAG_DRIFT,
        "vector_abi_changed": ChangeKind.VECTOR_ABI_CHANGED,
        "wchar_model_changed": ChangeKind.WCHAR_MODEL_CHANGED,
        "type_visibility_changed": ChangeKind.TYPE_VISIBILITY_CHANGED,
        "frame_register_changed": ChangeKind.FRAME_REGISTER_CHANGED,
    }

    return [
        Change(
            kind=_kind_map[kind_str],
            symbol=sym,
            description=desc,
            old_value=old_val,
            new_value=new_val,
        )
        for kind_str, sym, desc, old_val, new_val in diff_advanced_dwarf(o, n)
        if kind_str in _kind_map
    ]


def _run_post_processing(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None,
    policy_file: PolicyFile | None,
    scope_to_public_surface: bool,
    force_public_symbols: set[str] | None,
    collapse_versioned_symbols: bool,
    public_surface_allowlist: set[str] | None = None,
) -> tuple[list[Change], list[Change], list[Change], list[Change], list[Change], bool, PipelineContext]:
    """Run the post-processing pipeline and unpack results.

    Returns ``(kept, redundant, opaque_filtered, suppressed, out_of_surface,
    scope_resolved, pp_ctx)`` where *pp_ctx* is retained for ``surf_old``/
    ``surf_new`` access.
    """
    from .post_processing import DEFAULT_PIPELINE

    frozen_ns = list(policy_file.frozen_namespaces) if policy_file is not None else []
    internal_ns = (
        tuple(policy_file.internal_namespaces)
        if policy_file is not None and policy_file.internal_namespaces
        else None
    )
    pp_ctx = DEFAULT_PIPELINE.run(
        changes,
        old,
        new,
        suppression=suppression,
        frozen_namespaces=frozen_ns,
        internal_namespaces=internal_ns,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
    )
    # scoping is "resolved" unless it was requested and had to fall back to the
    # full export table (issue #235: an unconfirmed scope must not read as a
    # confidently-clean public surface).
    scope_resolved = not (scope_to_public_surface and pp_ctx.scope_fell_back)
    return (
        pp_ctx.kept,
        pp_ctx.redundant,
        pp_ctx.opaque_filtered,
        pp_ctx.suppressed,
        pp_ctx.out_of_surface,
        scope_resolved,
        pp_ctx,
    )


def _apply_soname_policy(
    kept: list[Change],
    verdict_redundant: list[Change],
    suppressed: list[Change],
    suppression: SuppressionList | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    versioned_scheme_soname_relink_required: bool = False,
) -> list[Change]:
    """Apply ELF version-node demotion and SONAME bump-policy check.

    Mutates *kept* in-place (appends visible SONAME advisories) and returns
    the updated *kept* list.  SONAME advisories that are suppressed are
    appended to *suppressed*.

    Runs after post-processing so downstream dedup/rename collapsing is
    already settled before the policy reads ``kept + verdict_redundant``.
    """
    from .diff_versioning import demote_internal_version_node_findings
    from .elf_metadata import ElfMetadata as _ElfMetadata

    _old_elf = getattr(old, "elf", None) or _ElfMetadata()
    _new_elf = getattr(new, "elf", None) or _ElfMetadata()

    # Demote findings for ELF-internal symbols before the bump check so a
    # demoted internal change neither drives a BREAKING verdict nor triggers a
    # spurious bump recommendation (validation parity class A — nettle 3.6→3.7).
    demote_internal_version_node_findings(kept + verdict_redundant, _old_elf, _new_elf)

    soname_changes = check_soname_bump_policy(
        kept + verdict_redundant, _old_elf, _new_elf
    )
    if versioned_scheme_soname_relink_required:
        soname_changes = [
            c for c in soname_changes
            if c.kind is not ChangeKind.SONAME_BUMP_UNNECESSARY
        ]
    soname_changes = _filter_suppressed_changes(soname_changes, suppression, suppressed)
    if soname_changes:
        kept.extend(soname_changes)
    return kept


def _compute_scope_confidence(
    old: AbiSnapshot,
    new: AbiSnapshot,
    scope_to_public_surface: bool,
    pp_ctx: PipelineContext,
) -> tuple[str, list[str]]:
    """Compute structured surface-scope confidence (ADR-024 §D5.3).

    Reuses the surfaces already computed by FilterNonPublicSurface to avoid
    repeating the type-closure walk.
    """
    from .surface import surface_scope_confidence

    return surface_scope_confidence(
        old,
        new,
        scope_enabled=scope_to_public_surface,
        surf_old=pp_ctx.surf_old,
        surf_new=pp_ctx.surf_new,
    )


def _old_public_symbol_count(old: AbiSnapshot) -> int | None:
    """Return the count of public-visibility symbols in *old*, or None if zero."""
    count = sum(1 for f in old.functions if f.visibility in _PUBLIC_VIS) + sum(
        1 for v in old.variables if v.visibility in _PUBLIC_VIS
    )
    return count if count > 0 else None


def compare(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = False,
    surface_metrics: bool = False,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    reconcile_build_context: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
) -> DiffResult:
    """Diff two AbiSnapshots and return a DiffResult with verdict.

    Args:
        old: Old ABI snapshot.
        new: New ABI snapshot.
        suppression: Optional suppression list to filter known changes.
        policy: Policy profile name to use for verdict classification.
            Available: "strict_abi" (default), "sdk_vendor", "plugin_abi".
            Ignored when *policy_file* is provided.
        policy_file: Optional :class:`~abicheck.policy_file.PolicyFile` instance
            for user-defined per-kind verdict overrides.  When provided,
            *policy* is used only as the ``base_policy`` fallback inside the
            file (i.e. the file's own ``base_policy`` field takes precedence).
        env_matrix: Optional declared deployment constraints (ADR-020b). When
            its ``runtime_floors`` field is set, new symbol-version
            requirements are classified against the declared floors
            (≤ floor → COMPATIBLE, > floor → BREAKING) instead of the default
            deployment-RISK verdict.
    """

    # Discover any diff_* detector modules not already imported above, then run
    # all registered detectors via the self-registering registry. ensure_loaded
    # is a no-op for the modules checker already imports (they fix the canonical
    # registration order); it only catches newly-added modules.
    _detector_registry.ensure_loaded()
    changes, detector_results = _detector_registry.run_all(old, new)

    # Merge externally-computed findings (e.g. build-configuration / probe-matrix
    # findings from diff_matrix(), which need multi-config inputs compare() does
    # not have). They join the normal pipeline so suppression, reporting, and
    # verdict composition treat them uniformly (G2: probe → compare).
    if extra_changes:
        changes.extend(extra_changes)

    # Run the post-processing pipeline (filtering, dedup, enrichment, suppression).
    # PolicyFile.frozen_namespaces is threaded in so the late-stage
    # EscalateFrozenNamespaceViolations step can tag matching findings.
    kept, redundant, opaque_filtered, suppressed, out_of_surface, scope_resolved, pp_ctx = (
        _run_post_processing(
            changes, old, new, suppression, policy_file, scope_to_public_surface,
            force_public_symbols, collapse_versioned_symbols,
            public_surface_allowlist=public_surface_allowlist,
        )
    )

    # Verdict computed on unsuppressed semantic changes.
    # NOTE: opaque_filtered changes are intentionally excluded from verdict
    # (they are compatibility-preserving noise, e.g. opaque handle size drift).
    #
    # rename: redundant changes are excluded too. When SuppressRenamedPairs
    # collapses a FUNC_REMOVED/FUNC_ADDED pair into a FUNC_LIKELY_RENAMED, it
    # moves the removed/added halves into `redundant` tagged "rename:…". The
    # surviving FUNC_LIKELY_RENAMED (a RISK kind, in `kept`) is what should
    # drive the verdict; counting the redundant FUNC_REMOVED would re-escalate
    # the downgraded rename back to BREAKING. They stay in redundant_changes
    # for audit (--show-redundant); they just don't drive the verdict.
    verdict_redundant = [
        c for c in redundant if not (c.caused_by_type or "").startswith("rename:")
    ]

    # ADR-039 — build-context reconciliation. Opt-in: when enabled and the
    # snapshots carry build-time defines, move context-free header-parse
    # artifacts (a conditional field's phantom add/remove the build proves never
    # changed) out of the verdict into an audit bucket. Runs *before* the SONAME
    # policy so a phantom breaking finding cannot trigger a stale
    # ``soname_bump_recommended`` that would survive reconciliation and turn the
    # advertised NO_CHANGE into COMPATIBLE + a spurious bump advisory (Codex
    # review #498). Authority-rule-safe: it only clears a finding the build
    # defines prove is a non-change (see diff_reconcile).
    reconciled: list[Change] = []
    if reconcile_build_context:
        kept, reconciled = reconcile_build_context_findings(kept, old, new)

    # Declared-runtime-floor contract (ADR-020b): before the SONAME policy so
    # a floor-decided BREAKING finding also drives the soname_bump_recommended
    # advisory (check_soname_bump_policy honors effective_verdict), and so the
    # internal-node demotion inside _apply_soname_policy — which skips findings
    # already carrying an effective_verdict — cannot race it (Codex review #510).
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_versioning import apply_runtime_floor_contract

        apply_runtime_floor_contract(
            kept + verdict_redundant, env_matrix.runtime_floors
        )

    # Platform-baseline floor check (G10, ADR-020b runtime_floors reused):
    # unlike apply_runtime_floor_contract above (which only reclassifies an
    # existing version-requirement *delta* finding), this is a standalone
    # check of the new binary's own required floor against the declared
    # baseline — it fires even when the floor never moved between old and
    # new, which is exactly the manylinux-tag violation case (a binary that
    # has always required a newer glibc than its wheel tag promises).
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_versioning import check_platform_baseline_floor
        from .elf_metadata import ElfMetadata as _ElfMetadataFloor

        new_elf_for_floor = getattr(new, "elf", None) or _ElfMetadataFloor()
        floor_changes = check_platform_baseline_floor(
            new_elf_for_floor, env_matrix.runtime_floors
        )
        floor_changes = _filter_suppressed_changes(
            floor_changes, suppression, suppressed
        )
        if floor_changes:
            kept.extend(floor_changes)

    # musllinux glibc-dependency check (G27): same declared-floor gate as the
    # platform-baseline check above, but a yes/no compatibility check rather
    # than a numeric floor comparison — see check_musllinux_glibc_dependency's
    # docstring for why musl has no version-floor concept to compare against.
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_versioning import check_musllinux_glibc_dependency
        from .elf_metadata import ElfMetadata as _ElfMetadataMusl

        new_elf_for_musl = getattr(new, "elf", None) or _ElfMetadataMusl()
        musllinux_changes = check_musllinux_glibc_dependency(
            new_elf_for_musl, env_matrix.runtime_floors
        )
        musllinux_changes = _filter_suppressed_changes(
            musllinux_changes, suppression, suppressed
        )
        if musllinux_changes:
            kept.extend(musllinux_changes)

    # macOS deployment-target floor check (G27, the macOS half of G10's
    # manylinux glibc-floor idea): same declared-floor gate, checked against
    # the new snapshot's Mach-O evidence instead of ELF.
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_wheel_deployment import check_macos_deployment_target_floor

        macos_floor_changes = check_macos_deployment_target_floor(
            getattr(new, "macho", None), env_matrix.runtime_floors
        )
        macos_floor_changes = _filter_suppressed_changes(
            macos_floor_changes, suppression, suppressed
        )
        if macos_floor_changes:
            kept.extend(macos_floor_changes)

    # Wheel-tag architecture-mismatch check (G27): same declared-claim gate,
    # checked against whichever of ELF/Mach-O evidence the new snapshot
    # carries.
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_wheel_deployment import check_wheel_tag_architecture_mismatch

        arch_mismatch_changes = check_wheel_tag_architecture_mismatch(
            getattr(new, "elf", None),
            getattr(new, "macho", None),
            env_matrix.runtime_floors,
        )
        arch_mismatch_changes = _filter_suppressed_changes(
            arch_mismatch_changes, suppression, suppressed
        )
        if arch_mismatch_changes:
            kept.extend(arch_mismatch_changes)

    # RPATH/RUNPATH portability + vendored-dependency-closure checks (G27):
    # ELF-only; each check function requires the dedicated
    # runtime_floors["WHEEL_CONTEXT"] key itself (not just any declared
    # floor — GLIBC/GLIBCXX/CXXABI are a general-purpose ADR-020b mechanism
    # unrelated to wheel packaging, so an ordinary non-wheel DSO declaring
    # one of those must not get wheel-portability findings it never opted
    # into; Codex review #583). This outer check is just the cheap
    # "any floors declared at all" pre-filter.
    if env_matrix is not None and env_matrix.runtime_floors:
        from .diff_wheel_deployment import (
            check_wheel_closure_dependency_violation,
            check_wheel_rpath_not_portable,
        )

        new_elf_for_rpath = getattr(new, "elf", None)
        rpath_changes = check_wheel_rpath_not_portable(
            new_elf_for_rpath, env_matrix.runtime_floors
        )
        rpath_changes = _filter_suppressed_changes(
            rpath_changes, suppression, suppressed
        )
        if rpath_changes:
            kept.extend(rpath_changes)

        closure_changes = check_wheel_closure_dependency_violation(
            new_elf_for_rpath, env_matrix.runtime_floors
        )
        closure_changes = _filter_suppressed_changes(
            closure_changes, suppression, suppressed
        )
        if closure_changes:
            kept.extend(closure_changes)

    # NumPy C-API compatibility-envelope delta (G26): needs only the two
    # snapshots' own numpy_capi field (no external wheel metadata), so this
    # runs unconditionally — unlike the wheel-metadata cross-check
    # (check_numpy_metadata_contract), which needs a declared numpy
    # requirement compare() has no access to and stays a standalone,
    # programmatic-use function (same "not yet wired into the CLI path"
    # precedent as G10's package.parse_manylinux_glibc_floor).
    from .diff_numpy_capi import diff_numpy_capi_surfaces

    numpy_capi_changes = diff_numpy_capi_surfaces(
        getattr(old, "numpy_capi", None), getattr(new, "numpy_capi", None)
    )
    numpy_capi_changes = _filter_suppressed_changes(
        numpy_capi_changes, suppression, suppressed
    )
    if numpy_capi_changes:
        kept.extend(numpy_capi_changes)

    # Post-detector: SONAME bump policy check.  Runs after post-processing so
    # rename collapsing and other dedup is already settled before reading `kept`.
    kept = _apply_soname_policy(
        kept,
        verdict_redundant,
        suppressed,
        suppression,
        old,
        new,
        versioned_scheme_soname_relink_required=(
            pp_ctx.versioned_scheme_soname_relink_required
        ),
    )

    all_unsuppressed = kept + verdict_redundant
    verdict = _compute_verdict_for(all_unsuppressed, policy, policy_file)
    effective_policy = policy_file.base_policy if policy_file is not None else policy

    # opaque_filtered changes are visible under --show-redundant for audit, but their
    # label in the reporter is distinct from true display-dedup redundant changes.
    # redundant_count reflects only the display-dedup set; opaque_filtered is additive.
    redundant_for_report = redundant + opaque_filtered
    true_redundant_count = len(redundant)  # dedup-only (not opaque); used for report label

    # Compute evidence tiers and confidence from detector results.
    evidence_tiers, confidence, coverage_warnings, evidence_tier = _compute_confidence(
        detector_results, old, new,
    )

    # ADR-024 §D5.3: structured confidence in the surface resolution itself.
    # Reuse the surfaces FilterNonPublicSurface already computed (when scoping
    # ran) to avoid repeating the type-closure walk. Confidence is a
    # header-provenance notion, so it stays gated on the header flag — a POST
    # manifest surface is an explicit contract and does not depend on it.
    scope_confidence, scope_notes = _compute_scope_confidence(
        old, new, scope_to_public_surface, pp_ctx
    )

    # A POST manifest allowlist scopes the comparison just as much as header
    # scoping does — it moves non-committed findings to `out_of_surface`. Mark
    # scoping active whenever *either* is in effect so the report always emits
    # the surface-scope ledger; otherwise a manifest run combined with
    # --no-scope-public-headers would silently filter findings and a clean
    # verdict would hide that (Codex review).
    scope_active = scope_to_public_surface or public_surface_allowlist is not None

    # ADR-027 A1/D1.2: aggregate surface-metric drift (opt-in --surface-metrics).
    # COMPATIBLE informational roll-ups; suppressible like any finding and never
    # breaking, so they leave the verdict unchanged unless NO_CHANGE flips to COMPATIBLE.
    if surface_metrics:
        kept, verdict = _apply_surface_metrics(
            old, new, kept, verdict_redundant, suppressed, suppression, policy, policy_file, verdict
        )

    # ADR-027 A4: pattern-aware verdict modulation. Runs after post-processing
    # and before the (recomputed) verdict so a demotion/raise reaches both the
    # reported findings and the exit code. Off by default (opt-in via
    # --pattern-verdicts); a no-op that leaves `kept`/`verdict` untouched
    # otherwise.
    pattern_modulations: list[dict[str, object]] = []
    if pattern_verdicts:
        kept, verdict, pattern_modulations = _apply_pattern_verdicts_step(
            old, new, kept, verdict_redundant, suppressed, suppression, policy, policy_file, evidence_tier, verdict
        )

    return DiffResult(
        old_version=old.version,
        new_version=new.version,
        library=old.library,
        changes=kept,
        verdict=verdict,
        suppressed_count=len(suppressed),
        suppressed_changes=suppressed,
        suppression_file_provided=suppression is not None,
        detector_results=detector_results,
        policy=effective_policy,
        policy_file=policy_file,
        redundant_changes=redundant_for_report,
        redundant_count=true_redundant_count,
        old_symbol_count=_old_public_symbol_count(old),
        confidence=confidence,
        evidence_tiers=evidence_tiers,
        coverage_warnings=coverage_warnings,
        out_of_surface_changes=out_of_surface,
        out_of_surface_count=len(out_of_surface),
        reconciled_changes=reconciled,
        reconciled_count=len(reconciled),
        scope_to_public_surface=scope_active,
        scope_resolved=scope_resolved,
        surface_scope_confidence=scope_confidence,
        surface_scope_notes=scope_notes,
        evidence_tier=evidence_tier,
        pattern_modulations=pattern_modulations,
    )
