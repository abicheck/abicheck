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

from typing import TYPE_CHECKING, Literal

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
from .comparability import check_contracts_comparable
from .compare.dwarf_advanced_diff import (
    diff_advanced_dwarf,  # noqa: F401 — re-export for monkeypatching
)
from .confidence import _compute_confidence
from .contract_pipeline import (
    ContractEvaluationStage,
    build_contract_stage,
    evaluated_for_policy,
)
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
from .diff_layout_coherence import (  # noqa: F401 — triggers detector registration
    _diff_dwarf_layout_coherence,
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
from .model import AbiSnapshot
from .policy_file import PolicyFile

if TYPE_CHECKING:
    from .environment_matrix import EnvironmentMatrix
    from .model.identity import EntityId
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
    stage: ContractEvaluationStage | None = None,
) -> Verdict:
    """Compute verdict using either a PolicyFile or the named policy profile.

    *stage* (ADR-049 D9) is the authoritative contract-relevance stage, set
    only when the caller opted into ``contract_evaluation=True``. When
    present, contract relevance is classified *first* and compatibility
    policy then runs over the ``EVALUATED`` findings alone: a finding proven
    outside the declared contract, or one whose required evidence is missing,
    has no compatibility decision and no gate contribution (D1).

    Classification happens here rather than at each call site because "before
    the verdict" is several points in ``compare()`` -- the two opt-in
    ``--surface-metrics``/``--pattern-verdicts`` steps append findings and
    recompute -- and a finding that reached the policy unclassified would be
    scored under exactly the pre-ADR-049 rule this ordering replaces.
    ``stage.classify`` is idempotent, so passing an already-classified list
    costs a set lookup per finding.

    With *stage* ``None`` (every run that did not opt in, which is the
    default) this is bit-for-bit the previous behaviour: no finding carries a
    relevance, so none is excluded.
    """
    if stage is not None:
        stage.classify(all_unsuppressed)
        all_unsuppressed = evaluated_for_policy(all_unsuppressed)
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
            diagnostics.append(
                _build_suppression_overreach_change(c, outcome.withheld_rule)
            )
        if outcome.withheld_unknown_rule is not None:
            diagnostics.append(
                _build_suppression_unknown_reachability_change(
                    c, outcome.withheld_unknown_rule
                )
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
    stage: ContractEvaluationStage | None = None,
    old_public_entity_ids: frozenset[EntityId] | None = None,
    new_public_entity_ids: frozenset[EntityId] | None = None,
) -> tuple[list[Change], Verdict]:
    """Compute aggregate surface-metric findings (ADR-027 A1/D1.2) and return
    the updated *kept* list and (possibly recomputed) *verdict*.

    Called only when ``surface_metrics=True``.  *current_verdict* is the
    verdict already established before this step; it is returned unchanged
    when no new metric findings are visible.

    *stage* is forwarded to the verdict recomputation so this step's own
    freshly-appended findings are contract-classified before they can score
    (ADR-049 D9) — they are appended after the first classification pass, so
    without it they would reach compatibility policy unclassified.

    *old_public_entity_ids*/*new_public_entity_ids* pass through unchanged
    to :func:`diff_surface_metrics` (ADR-063 Phase 3 D5).
    """
    from .diff_surface_metrics import diff_surface_metrics

    visible = _filter_suppressed_changes(
        list(
            diff_surface_metrics(
                old,
                new,
                old_public_entity_ids=old_public_entity_ids,
                new_public_entity_ids=new_public_entity_ids,
            )
        ),
        suppression,
        suppressed,
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
    return kept, _compute_verdict_for(
        kept + verdict_redundant, policy, policy_file, stage
    )


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
            diagnostics.append(
                _build_suppression_overreach_change(c, outcome.withheld_rule)
            )
        if outcome.withheld_unknown_rule is not None:
            diagnostics.append(
                _build_suppression_unknown_reachability_change(
                    c, outcome.withheld_unknown_rule
                )
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
    stage: ContractEvaluationStage | None = None,
    old_public_entity_ids: frozenset[EntityId] | None = None,
    new_public_entity_ids: frozenset[EntityId] | None = None,
) -> tuple[list[Change], Verdict, list[dict[str, object]]]:
    """Apply ADR-027 A4 pattern-aware verdict modulation.

    Returns the updated *kept* list, (possibly recomputed) *verdict*, and the
    *pattern_modulations* ledger.  Called only when ``pattern_verdicts=True``.
    *current_verdict* is returned unchanged when pattern_modulations is empty.

    *stage* is forwarded for the same reason as in
    :func:`_apply_surface_metrics`: this step's synthetic findings are
    appended after the first contract-classification pass, so the verdict
    recomputation is where they must be classified (ADR-049 D9).

    *old_public_entity_ids*/*new_public_entity_ids* pass through unchanged
    to :func:`apply_pattern_verdicts` (ADR-063 Phase 3 D5).
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
        kept,
        old,
        new,
        evidence_tier=evidence_tier,
        protected_kinds=protected_kinds,
        old_public_entity_ids=old_public_entity_ids,
        new_public_entity_ids=new_public_entity_ids,
    )

    if suppression is not None and len(kept) > pre_pattern_count:
        kept, pattern_modulations = _filter_pattern_synthetic(
            kept, pre_pattern_count, suppression, suppressed, pattern_modulations
        )

    if pattern_modulations:
        return (
            kept,
            _compute_verdict_for(kept + verdict_redundant, policy, policy_file, stage),
            pattern_modulations,
        )
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
) -> tuple[
    list[Change],
    list[Change],
    list[Change],
    list[Change],
    list[Change],
    bool,
    PipelineContext,
]:
    """Run the post-processing pipeline and unpack results.

    Returns ``(kept, redundant, opaque_filtered, suppressed, out_of_surface,
    scope_resolved, pp_ctx)`` where *pp_ctx* is retained for ``surf_old``/
    ``surf_new`` access.
    """
    from .post_processing import DEFAULT_PIPELINE

    frozen_ns = list(policy_file.frozen_namespaces) if policy_file is not None else []
    internal_ns = _internal_namespaces(policy_file) or None
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
    stage: ContractEvaluationStage | None = None,
) -> list[Change]:
    """Apply ELF version-node demotion and SONAME bump-policy check.

    Mutates *kept* in-place (appends visible SONAME advisories) and returns
    the updated *kept* list.  SONAME advisories that are suppressed are
    appended to *suppressed*.

    Runs after post-processing so downstream dedup/rename collapsing is
    already settled before the policy reads ``kept + verdict_redundant``.

    *stage* (ADR-049 D9) makes this policy read the same finding set
    compatibility policy will. The SONAME check *derives a new finding* from
    the presence of breaking ones, so an excluded finding reaching it does
    not merely fail to be ignored — it manufactures a
    ``soname_bump_recommended`` advisory that is itself ``NOT_APPLICABLE``,
    hence evaluated, hence able to move a ``NO_CHANGE`` verdict to
    ``COMPATIBLE`` and, under a policy that escalates the advisory, to gate.
    A change proven outside the contract must not be able to launder itself
    into the gate through a derived finding (Codex review, confirmed with a
    proven-out-of-contract layout change against an unchanged SONAME).
    """
    from .diff_templates import demote_lambda_closure_unexported_findings
    from .diff_versioning import demote_internal_version_node_findings
    from .elf_metadata import ElfMetadata as _ElfMetadata

    _old_elf = getattr(old, "elf", None) or _ElfMetadata()
    _new_elf = getattr(new, "elf", None) or _ElfMetadata()

    # Demote findings for ELF-internal symbols before the bump check so a
    # demoted internal change neither drives a BREAKING verdict nor triggers a
    # spurious bump recommendation (validation parity class A — nettle 3.6→3.7).
    demote_internal_version_node_findings(kept + verdict_redundant, _old_elf, _new_elf)

    # Same reasoning, same ordering requirement, for a lambda-closure
    # function-level finding confirmed absent from both binaries' real
    # export tables — see the function's own docstring.
    demote_lambda_closure_unexported_findings(kept + verdict_redundant, old, new)

    # Classify before deriving, not after: everything appended to `kept` up to
    # this point (the declared-floor, wheel and numpy checks above included) is
    # covered, and `classify` is idempotent, so the later verdict computation
    # re-runs over the same findings for free.
    policy_input = kept + verdict_redundant
    if stage is not None:
        stage.classify(policy_input)
        policy_input = evaluated_for_policy(policy_input)

    soname_changes = check_soname_bump_policy(policy_input, _old_elf, _new_elf)
    if versioned_scheme_soname_relink_required:
        soname_changes = [
            c
            for c in soname_changes
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


def _internal_namespaces(policy_file: PolicyFile | None) -> tuple[str, ...]:
    """The policy file's internal-namespace hints, or an empty tuple.

    One derivation shared by post-processing's own ``internal_namespaces``
    argument and the persisted evaluation context's ``surface`` hints
    (CodeRabbit review: the two had independent copies, so a change to the
    resolution rule would have silently applied to only one of them).
    Post-processing wants ``None`` for "none configured" and converts at its
    own call site; this returns the empty tuple, which is what the typed
    config field takes.
    """
    if policy_file is None or not policy_file.internal_namespaces:
        return ()
    return tuple(policy_file.internal_namespaces)


def _old_public_symbol_count(old: AbiSnapshot) -> int | None:
    """Return the count of public-visibility symbols in *old*, or None if zero."""
    count = sum(1 for f in old.functions if f.visibility in _PUBLIC_VIS) + sum(
        1 for v in old.variables if v.visibility in _PUBLIC_VIS
    )
    return count if count > 0 else None


def _contract_coverage_status(
    old: AbiSnapshot, new: AbiSnapshot
) -> Literal["partial"] | None:
    """ADR-050 D2 — ``"partial"`` when an axis of the comparability contract
    was never actually checked because exactly one side carries it.

    Mirrors :func:`check_contracts_comparable`'s own per-fingerprint-independent
    gating (Codex review, PR #624): a per-``contract``-object check misses the
    case where both sides carry a real contract but only one has a
    ``profile_fingerprint`` — e.g. a symbols-only side with only scope
    provenance compared against a full L2 side — which
    ``check_contracts_comparable`` correctly skips the profile check for, but a
    coarse "contract is None" comparison would still report as fully covered
    even though that axis was never checked.

    ``dependency_scope`` is included for the same reason (Codex review, fresh
    evidence): ``_check_dependency_scope_comparable`` deliberately permits a
    ``None`` (pre-v18/genuinely-untagged) side against an explicitly-tagged one
    rather than raising, since there is no way to recover which mode the
    untagged side actually used — but that means the comparison silently
    proceeds without ever having verified this axis matches, and a legacy
    "full"-mode snapshot compared against a freshly "filtered" one could
    produce a wrong verdict with the report still reading as fully verified.

    Report-level metadata, not a ``Change``/``ChangeKind`` finding.
    """
    old_profile = old.contract.profile_fingerprint if old.contract else None
    new_profile = new.contract.profile_fingerprint if new.contract else None
    old_scope = old.contract.scope_fingerprint if old.contract else None
    new_scope = new.contract.scope_fingerprint if new.contract else None
    mixed = (
        (old_profile is None) != (new_profile is None)
        or (old_scope is None) != (new_scope is None)
        or (old.dependency_scope is None) != (new.dependency_scope is None)
    )
    return "partial" if mixed else None


def _env_matrix_contract_changes(
    new: AbiSnapshot,
    kept: list[Change],
    verdict_redundant: list[Change],
    suppression: SuppressionList | None,
    suppressed: list[Change],
    env_matrix: EnvironmentMatrix | None,
) -> list[Change]:
    """Every declared-runtime-floor / wheel-packaging check, run under one
    ``env_matrix.runtime_floors`` gate.

    Two different things happen here, in this order:

    * ``apply_runtime_floor_contract`` *reclassifies in place* — it only
      touches an existing version-requirement *delta* finding already in
      ``kept``/``verdict_redundant`` (ADR-020b), producing nothing new.
    * every other check is standalone: it reads the new binary's own evidence
      and can fire even when the floor never moved between old and new, which
      is exactly the manylinux-tag violation case (a binary that has always
      required a newer glibc than its wheel tag promises). Their findings are
      returned, suppression-filtered, for the caller to fold into ``kept``.

    The wheel checks (``G27``) each additionally require the dedicated
    ``runtime_floors["WHEEL_CONTEXT"]`` key *inside themselves* — not just any
    declared floor, since GLIBC/GLIBCXX/CXXABI are a general-purpose ADR-020b
    mechanism unrelated to wheel packaging, so an ordinary non-wheel DSO
    declaring one of those must not get wheel-portability findings it never
    opted into (Codex review #583). The gate here is just the cheap "any floors
    declared at all" pre-filter.
    """
    if env_matrix is None or not env_matrix.runtime_floors:
        return []
    floors = env_matrix.runtime_floors

    from .diff_versioning import (
        apply_runtime_floor_contract,
        check_musllinux_glibc_dependency,
        check_platform_baseline_floor,
    )
    from .diff_wheel_deployment import (
        check_macos_deployment_target_floor,
        check_wheel_closure_dependency_violation,
        check_wheel_rpath_not_portable,
        check_wheel_tag_architecture_mismatch,
    )
    from .elf_metadata import ElfMetadata

    apply_runtime_floor_contract(kept + verdict_redundant, floors)

    new_elf = getattr(new, "elf", None)
    new_macho = getattr(new, "macho", None)
    # The floor checks substitute an empty ElfMetadata for a missing one (they
    # answer "does this binary's own declared requirement violate the floor",
    # which an absent ELF answers "no"); the wheel checks take the raw value,
    # since a non-ELF input has no wheel-portability claim to check at all.
    # Filtered per check, not once over the concatenation (Codex review):
    # _filter_suppressed_changes appends the SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
    # / unknown-reachability diagnostics it raises to the END of its own return
    # value, so one combined call would move every check's diagnostics past
    # every other check's findings -- turning the report's
    # [floor, floor-diagnostic, musl, musl-diagnostic, ...] into
    # [floor, musl, ..., floor-diagnostic, musl-diagnostic, ...]. DiffResult
    # .changes preserves list order and the reporters render it, so that is an
    # observable output change, not an internal detail.
    produced: list[Change] = []
    for check_changes in (
        check_platform_baseline_floor(new_elf or ElfMetadata(), floors),
        check_musllinux_glibc_dependency(new_elf or ElfMetadata(), floors),
        check_macos_deployment_target_floor(new_macho, floors),
        check_wheel_tag_architecture_mismatch(new_elf, new_macho, floors),
        check_wheel_rpath_not_portable(new_elf, floors),
        check_wheel_closure_dependency_violation(new_elf, floors),
    ):
        produced.extend(
            _filter_suppressed_changes(check_changes, suppression, suppressed)
        )
    return produced


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
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    old_public_entity_ids: frozenset[EntityId] | None = None,
    new_public_entity_ids: frozenset[EntityId] | None = None,
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
            for user-defined per-kind (``overrides:``) or selector-scoped
            (``reclassify:``) verdict re-classification.  When provided,
            *policy* is used only as the ``base_policy`` fallback inside the
            file (i.e. the file's own ``base_policy`` field takes precedence).
        env_matrix: Optional declared deployment constraints (ADR-020b). When
            its ``runtime_floors`` field is set, new symbol-version
            requirements are classified against the declared floors
            (≤ floor → COMPATIBLE, > floor → BREAKING) instead of the default
            deployment-RISK verdict.
        diagnostic_comparison: ADR-050 D2's one sanctioned escape hatch. By
            default, a genuine ``contract`` mismatch between *old* and *new*
            (ADR-050 D1's profile/scope fingerprints) raises
            :class:`~abicheck.errors.ProfileMismatchError` or
            :class:`~abicheck.errors.ScopeMismatchError` before any diff runs.
            Setting this True downgrades that hard-fail into an ordinary diff
            whose ``DiffResult.assurance`` is stamped ``"none"`` instead.
        contract_evaluation: ADR-049 contract evaluation. When True, stamps
            every finding's ``Change.contract_relevance``/
            ``contract_reason_code``/``contract_assurance`` from
            :func:`~abicheck.contract_evaluation.evaluate_snapshot_pair_contract_relevance`
            -- ``contract=public`` when *scope_to_public_surface* is True,
            ``contract=all`` otherwise (the exact `--no-scope-public-headers`
            alias, per the ADR-049 plan) unless *contract_mode* says
            otherwise. Also stamps each finding's ``contract_evidence_refs``
            (Phase 3's provider ledger: which observed evidence records the
            decision rests on) and populates ``DiffResult.contract_context``
            with Phase 4's three persisted blocks, so the decision can later
            be replayed or re-evaluated under a different contract without
            re-reading either binary (:mod:`abicheck.contract_replay`).

            **Authoritative as of ADR-049 Phase 7, no longer a shadow
            field.** Relevance is classified *before* compatibility policy
            (D9's normative order), and policy then scores only the
            ``EVALUATED`` findings -- ``IN_CONTRACT`` and ``NOT_APPLICABLE``.
            A ``PROVEN_OUT_OF_CONTRACT``, ``UNKNOWN_UNPROVEN`` or
            ``UNKNOWN_UNRESOLVED`` finding is ``NOT_EVALUATED``: its
            ``compatibility_decision`` is ``None`` (JSON ``null``, not a
            sixth verdict meaning "compatible") and it contributes nothing
            to the verdict or the change gate, while staying fully present
            in ``DiffResult.changes`` and every audit ledger -- D9 conserves
            every detector fact in exactly one visible outcome, and the
            orthogonal contract-coverage ledger still contributes its own
            exit ``1`` for the unresolved case.

            Off by default, so every existing caller behaves exactly as
            before: with no opt-in, no finding carries a relevance and none
            is excluded from anything.
        contract_mode: ADR-049 Phase 6 -- which evidence domain
            *contract_evaluation* judges against: ``"public"`` (header-derived
            declared surface), ``"exports"`` (the binary's own export table
            and the raw type closure from it, ``export_surface.py``), or
            ``"all"`` (no root/closure evidence required). ``None`` keeps the
            legacy derivation from *scope_to_public_surface* described above;
            an explicit value outranks it, per ADR-049 D7's precedence
            (``explicit_cli`` > ``legacy_alias``). Selects the *domain*
            only -- which evidence a relevance decision is made against, not
            whether that decision is authoritative (it is, per
            *contract_evaluation* above).
        old_public_entity_ids: ADR-063 Phase 3 (D5) -- *old*'s resolved
            public-surface ``EntityId`` set; *new_public_entity_ids* is
            *new*'s (never swap). ``None`` (default) preserves prior behavior.

    Raises:
        ProfileMismatchError: *old* and *new* were extracted under
            different, incompatible compile contexts (ADR-050 D1/D2), and
            *diagnostic_comparison* was not set.
        ScopeMismatchError: *old* and *new* do not cover the same declared
            surface (ADR-050 D1/D2), and *diagnostic_comparison* was not set.
        ValueError: *contract_mode* is not one of ``ContractMode``'s values.
            Only raised when *contract_evaluation* is set, since the mode is
            not consulted otherwise -- see the note below.

    Note:
        *contract_mode* is **inert unless** *contract_evaluation* is set:
        the contract-evaluation stage is the only thing that reads it, and
        it does not run otherwise. This Tier-1 verb deliberately does not reject
        that combination, though every Tier-2 front end does
        (``service.compare_snapshots``, ``api_types.CompareRequest.
        validation_errors``, and the ``compare`` CLI all raise a usage
        error) -- ADR-037 D10.1 puts request validation at the service
        boundary, not in the core verb, so duplicating it here would give
        two places to keep in sync for no added safety on the supported
        paths.
    """
    mismatch = check_contracts_comparable(old, new, diagnostic=diagnostic_comparison)
    assurance: Literal["none"] | None = "none" if mismatch is not None else None
    # Propagate *why* a diagnostic-mode comparison is untrustworthy into the
    # existing human-readable coverage_warnings disclosure (CodeRabbit
    # review, PR #624): the non-diagnostic (raising) path already surfaces
    # mismatch.reason via the exception message, but the diagnostic escape
    # hatch previously reduced it to a bare assurance == "none" with no
    # explanation of which axis mismatched -- undermining the stated purpose
    # of the escape hatch ("the caller can still see a result but knows not
    # to trust it").
    comparability_warnings = [mismatch.reason] if mismatch is not None else []
    contract_coverage = _contract_coverage_status(old, new)

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
    (
        kept,
        redundant,
        opaque_filtered,
        suppressed,
        out_of_surface,
        scope_resolved,
        pp_ctx,
    ) = _run_post_processing(
        changes,
        old,
        new,
        suppression,
        policy_file,
        scope_to_public_surface,
        force_public_symbols,
        collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
    )

    # ADR-049 D9 — contract relevance is classified *before* compatibility
    # policy, not after it. Post-processing has settled canonical identity,
    # dedup and the explicit consumer/manifest scope by this point, which is
    # exactly the input the normative pipeline order calls for; everything
    # below that computes or recomputes a verdict routes through
    # `_compute_verdict_for(..., stage)`, which classifies first and then
    # scores the EVALUATED findings alone.
    #
    # Building the stage here rather than at the end is what makes the
    # decision authoritative instead of shadow: until this ordering landed,
    # a finding the evaluator labelled PROVEN_OUT_OF_CONTRACT had already
    # driven the verdict (and the process exit) by the time it was labelled.
    # `None` — and therefore no behaviour change at all — for every run that
    # did not opt into `contract_evaluation=True`, which remains the default.
    stage: ContractEvaluationStage | None = None
    if contract_evaluation:
        stage = build_contract_stage(
            old,
            new,
            scope_to_public_surface=scope_to_public_surface,
            force_public_symbols=force_public_symbols,
            pp_ctx=pp_ctx,
            contract_mode=contract_mode,
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

    # Declared-runtime-floor and wheel-packaging contracts (ADR-020b / G10 /
    # G27). Before the SONAME policy so a floor-decided BREAKING finding also
    # drives the soname_bump_recommended advisory (check_soname_bump_policy
    # honors effective_verdict), and so the internal-node demotion inside
    # _apply_soname_policy — which skips findings already carrying an
    # effective_verdict — cannot race it (Codex review #510).
    kept.extend(
        _env_matrix_contract_changes(
            new,
            kept,
            verdict_redundant,
            suppression,
            suppressed,
            env_matrix,
        )
    )

    # NumPy C-API compatibility-envelope delta (G26): needs only the two
    # snapshots' own numpy_capi field (no external wheel metadata), so this
    # runs unconditionally — unlike the wheel-metadata cross-check
    # (check_numpy_metadata_contract), which needs a declared numpy
    # requirement compare() has no access to and stays a standalone,
    # programmatic-use function (same "not yet wired into the CLI path"
    # precedent as G10's package.parse_manylinux_glibc_floor).
    from .diff_numpy_capi import diff_numpy_capi_surfaces

    kept.extend(
        _filter_suppressed_changes(
            diff_numpy_capi_surfaces(
                getattr(old, "numpy_capi", None), getattr(new, "numpy_capi", None)
            ),
            suppression,
            suppressed,
        )
    )

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
        stage=stage,
    )

    all_unsuppressed = kept + verdict_redundant
    verdict = _compute_verdict_for(all_unsuppressed, policy, policy_file, stage)
    effective_policy = policy_file.base_policy if policy_file is not None else policy

    # opaque_filtered changes are visible under --show-redundant for audit, but their
    # label in the reporter is distinct from true display-dedup redundant changes.
    # redundant_count reflects only the display-dedup set; opaque_filtered is additive.
    redundant_for_report = redundant + opaque_filtered
    true_redundant_count = len(
        redundant
    )  # dedup-only (not opaque); used for report label

    # Compute evidence tiers and confidence from detector results.
    evidence_tiers, confidence, coverage_warnings, evidence_tier = _compute_confidence(
        detector_results,
        old,
        new,
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
            old,
            new,
            kept,
            verdict_redundant,
            suppressed,
            suppression,
            policy,
            policy_file,
            verdict,
            stage,
            old_public_entity_ids=old_public_entity_ids,
            new_public_entity_ids=new_public_entity_ids,
        )

    # ADR-027 A4: pattern-aware verdict modulation. Runs after post-processing
    # and before the (recomputed) verdict so a demotion/raise reaches both the
    # reported findings and the exit code. Off by default (opt-in via
    # --pattern-verdicts); a no-op that leaves `kept`/`verdict` untouched
    # otherwise.
    pattern_modulations: list[dict[str, object]] = []
    if pattern_verdicts:
        kept, verdict, pattern_modulations = _apply_pattern_verdicts_step(
            old,
            new,
            kept,
            verdict_redundant,
            suppressed,
            suppression,
            policy,
            policy_file,
            evidence_tier,
            verdict,
            stage,
            old_public_entity_ids=old_public_entity_ids,
            new_public_entity_ids=new_public_entity_ids,
        )

    # ADR-049 D9's closing half: relevance was already decided above; here we
    # (a) classify the audit ledgers -- findings that never reach `kept` but
    # are still rendered and owed the same contract fields; (b) record each
    # finding's compatibility decision now that policy (incl. the pattern-
    # verdict modulations above) has finished; and (c) persist the Phase 4
    # context over every finding the stage saw. `kept` is passed again
    # deliberately: `classify` is idempotent, so this only picks up a late
    # step's additions, and no future step can slip in uncovered.
    #
    # Each ledger below is here for its own reason (Codex, PR #658), not
    # "everything in scope for symmetry": `pp_ctx.out_of_surface` already
    # carries `surface_exclusion_reason` from FilterNonPublicSurface;
    # `redundant_for_report` is restored into the report by
    # `--show-redundant` long after this runs and would otherwise render
    # unstamped; `suppressed` findings keep their contract relevance under
    # the ADR-013 audit trail despite being gated; `reconciled` findings are
    # cleared from `kept` by `--reconcile-build-context` before this point
    # and would otherwise never be classified at all.
    #
    # `pp_ctx.public_surface_allowlist`/`force_public_symbols` reach the
    # decision through the stage itself (`build_contract_stage`): without
    # them, a `kept` finding committed by POST-manifest despite
    # private-header provenance could reach PROVEN_OUT_OF_CONTRACT even
    # though the manifest already proved it committed.
    contract_context: object | None = None
    if stage is not None:
        stage.classify(
            kept
            + pp_ctx.out_of_surface
            + redundant_for_report
            + suppressed
            + reconciled
        )
        stage.record_compatibility_decisions(
            stage.changes,
            policy=effective_policy,
            policy_file=policy_file,
        )
        contract_context = stage.build_context(
            policy=effective_policy,
            policy_file=policy_file,
            suppression=suppression,
            internal_namespaces=_internal_namespaces(policy_file),
        )

    from .contract_context import suppression_config_for

    # `suppression.source_sha256` alone is `None` for a digest-less but
    # fully active list (the public constructor, ABICC's -skip-symbols
    # lists, SuppressionList.merge() all produce this shape) --
    # `suppression_config_for`'s rule_identities()-content-digest fallback
    # handles it (Codex review, PR #803).
    _suppression_config = suppression_config_for(suppression)
    suppression_source_sha256 = (
        _suppression_config.sha256 if _suppression_config is not None else None
    )

    # Canonical content digest of every resolved explicit-scope input (Codex
    # review, PR #803): both `force_public_symbols` and
    # `public_surface_allowlist` are already-resolved `set[str] | None` here
    # and each independently changes which findings `compare()` retains, so
    # a digest of just one axis would let two differing-only-by-the-other
    # runs collide. Keyed JSON (not delimiter-joined) keeps the axes
    # distinguishable, avoiding the non-injective-join bug class already
    # fixed elsewhere in this digest work.
    #
    # `public_surface_allowlist` is gated on `is not None`, not truthiness
    # (Codex review, PR #803): an empty allowlist is a real, distinct,
    # active configuration -- a POST manifest committing to zero exports --
    # matching `scope_active`'s identical `is not None` check above (line
    # ~1038); collapsing `set()` to "no scope" would hash an absent manifest
    # identically to a zero-export one. `force_public_symbols` deliberately
    # keeps plain truthiness: every other consumer in this codebase already
    # treats an empty set as equivalent to `None` for that axis, so the two
    # are intentionally asymmetric here.
    #
    # `content_digest` is the same canonical-JSON-then-SHA-256 primitive
    # `contract_context.py` uses for overlay digests (CodeRabbit, PR #803) --
    # reused rather than a second hand-rolled hashing convention.
    from .contract_evidence_collect import content_digest

    _explicit_scope_sources: dict[str, list[str]] = {}
    if force_public_symbols:
        _explicit_scope_sources["force_public_symbols"] = sorted(force_public_symbols)
    if public_surface_allowlist is not None:
        _explicit_scope_sources["public_surface_allowlist"] = sorted(
            public_surface_allowlist
        )
    explicit_scope_source_sha256 = (
        "sha256:" + content_digest(_explicit_scope_sources)
        if _explicit_scope_sources
        else None
    )

    # Canonical content digest of the resolved --env-matrix (Codex review,
    # PR #803, fresh evidence): `dataclasses.asdict` recursively serializes
    # `EnvironmentMatrix`'s own nested `SyclConstraints`/`CudaConstraints`
    # dataclasses into a plain, JSON-safe dict for `content_digest`. `None`
    # when no --env-matrix was given at all.
    import dataclasses as _dataclasses

    env_matrix_source_sha256 = (
        "sha256:" + content_digest(_dataclasses.asdict(env_matrix))
        if env_matrix is not None
        else None
    )

    result = DiffResult(
        old_version=old.version,
        new_version=new.version,
        library=old.library,
        changes=kept,
        verdict=verdict,
        suppressed_count=len(suppressed),
        suppressed_changes=suppressed,
        suppression_file_provided=suppression is not None,
        suppression_source_sha256=suppression_source_sha256,
        explicit_scope_source_sha256=explicit_scope_source_sha256,
        pattern_verdicts_enabled=bool(pattern_verdicts),
        collapse_versioned_symbols_enabled=bool(collapse_versioned_symbols),
        surface_metrics_enabled=bool(surface_metrics),
        env_matrix_source_sha256=env_matrix_source_sha256,
        reconcile_build_context_enabled=bool(reconcile_build_context),
        scope_to_public_surface_requested=bool(scope_to_public_surface),
        detector_results=detector_results,
        policy=effective_policy,
        policy_file=policy_file,
        redundant_changes=redundant_for_report,
        redundant_count=true_redundant_count,
        old_symbol_count=_old_public_symbol_count(old),
        confidence=confidence,
        evidence_tiers=evidence_tiers,
        coverage_warnings=coverage_warnings + comparability_warnings,
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
        contract_coverage=contract_coverage,
        assurance=assurance,
        contract_context=contract_context,
    )
    # P0.4 — computed last, from data the pipeline above already produced
    # (evidence tiers, comparability outcome, contract context, whatever
    # BuildSourcePack either side already carries). A pure rollup over the
    # just-built `result`, never a new probe; see analysis_assurance.py's
    # own module docstring for the full rationale and what is deferred.
    #
    # `checker.compare()` only ever sees each snapshot's own *embedded*
    # BuildSourcePack (`old.build_source`/`new.build_source`) — it has no
    # visibility into an out-of-band `--old/new-build-info`/
    # `--old/new-sources` pack directory the `compare` CLI may have
    # resolved separately (`_resolve_side_pack`), so it passes exactly what
    # it has. A caller that resolved such a pack must recompute this with
    # the real pack passed explicitly (P1 review; see
    # `compute_analysis_assurance`'s own docstring and
    # `cli_compare_helpers._report_compare_result`'s recomputation).
    from .analysis_assurance import compute_analysis_assurance

    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        old_pack=getattr(old, "build_source", None),
        new_pack=getattr(new, "build_source", None),
    )
    return result
