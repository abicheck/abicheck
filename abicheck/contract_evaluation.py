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

"""ADR-049 Phase 3: shadow contract-relevance evaluator.

This module computes a :class:`ContractEvaluationDecision` -- a
:class:`~abicheck.contract_relevance_types.ContractRelevance` value plus a
stable reason code and an
:class:`~abicheck.contract_relevance_types.ContractAssurance` level -- for one
already-emitted :class:`~abicheck.checker_types.Change`, from evidence that
already exists today: :mod:`abicheck.surface`'s public-surface resolution
(ADR-024) and :mod:`abicheck.finding_identity`'s canonical-identity tiers
(ADR-049 Phase 2). **It is a shadow evaluator**: nothing in detection, policy,
the CLI, or reports calls it yet, and its output changes no verdict, no exit
code, and no report. It exists so a decision can be computed and compared
against real runs before Phase 6 wires it into anything that affects output.

Deliberately conservative, twice over:

1. **Only** :data:`~abicheck.contract_relevance_types.ContractMode.PUBLIC` and
   :data:`~abicheck.contract_relevance_types.ContractMode.ALL` are
   implemented. ``EXPORTS`` needs an export-root-closure evidence provider
   that does not exist yet (``surface.py`` only computes a *header-derived*
   public closure, not an *export-symbol-rooted* one) --
   :func:`evaluate_change_contract_relevance` raises ``NotImplementedError``
   for it rather than silently approximating it as ``PUBLIC`` or ``ALL``,
   either of which would misrepresent a mode this evaluator cannot actually
   check.
2. This evaluator **never emits**
   :data:`~abicheck.contract_relevance_types.ContractRelevance.UNKNOWN_UNPROVEN`.
   That value means "the declared evidence domain was searched completely and
   found no commitment" (ADR-049's ``closed_domain_no_commitment`` reason) --
   a closed-world completeness claim this module has no way to verify with
   today's evidence providers (there is no per-domain "did we search
   everything" signal, only ``PublicSurface.resolvable``/``has_provenance``).
   Every case that would otherwise need ``UNKNOWN_UNPROVEN`` is downgraded to
   the weaker, honestly-hedged
   :data:`~abicheck.contract_relevance_types.ContractRelevance.UNKNOWN_UNRESOLVED`
   with reason ``required_evidence_incomplete`` instead.

See :doc:`ADR-049
</contribute/adr/049-contract-relevance-and-compatibility-configuration>` and
its :doc:`implementation plan </contribute/plans/public-contract-default>`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checker_types import Change
from .contract_relevance_types import (
    CONTRACT_REASON_CODES,
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from .finding_identity import IDENTITY_TIER_REDUCED, resolve_change_identity
from .surface import (
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    REASON_NOT_EXPORTED,
    REASON_OFF_PYTHON_SURFACE,
    REASON_PRIVATE_HEADER,
    REASON_PRIVATE_INTERNAL_UNREACHABLE,
    REASON_SYSTEM_HEADER,
    PublicSurface,
    SurfaceUnions,
    classify_change_surface,
    surface_unions,
)

# --------------------------------------------------------------------------
# NOT_APPLICABLE: findings that are not about a contract entity at all.
# --------------------------------------------------------------------------
#
# Curated, deliberately non-exhaustive (ADR-049 D2's "non-entity" column):
# every kind here concerns the library's loader/deployment/security-hardening
# identity, never a specific function/variable/type an importing consumer's
# code references. A kind missing from this set simply falls through to the
# ordinary entity-surface classification below -- the failure mode is "still
# gets a relevance decision" (harmless for a shadow evaluator), not a crash,
# so this set can grow incrementally as new non-entity kinds are noticed
# without needing to be complete from day one.
_NOT_APPLICABLE_KIND_SLUGS: frozenset[str] = frozenset(
    {
        # Linker identity (SONAME/RPATH/RUNPATH).
        "soname_changed",
        "soname_missing",
        "soname_bump_recommended",
        "soname_bump_unnecessary",
        "bundle_soname_skew",
        "rpath_changed",
        "runpath_changed",
        "rpath_type_changed",
        "wheel_rpath_not_portable",
        "wheel_closure_dependency_violation",
        # Architecture / file-format identity.
        "pe_machine_changed",
        "wheel_tag_architecture_mismatch",
        "elf_class_changed",
        "elf_osabi_changed",
        "elf_endianness_changed",
        "macho_filetype_changed",
        "macho_linkage_flags_changed",
        "macho_reexport_changed",
        # Security-hardening posture (PT_GNU_STACK/RELRO/PIE/canary/FORTIFY/
        # PE DllCharacteristics/text relocations/DT_RELR) -- a property of
        # the binary's build flags, not of any one exported entity.
        "executable_stack",
        "executable_stack_removed",
        "relro_weakened",
        "pie_disabled",
        "stack_canary_removed",
        "fortify_source_weakened",
        "pe_hardening_weakened",
        "pe_hardening_improved",
        "text_relocation_introduced",
        "dt_relr_introduced",
        "dt_relr_removed",
        # Toolchain/runtime identity, not a consumer-visible entity.
        "libcpp_abi_version_changed",
        "sycl_plugin_search_path_changed",
    }
)

_SUPPORTED_MODES: frozenset[ContractMode] = frozenset(
    {ContractMode.PUBLIC, ContractMode.ALL}
)

# Surface-exclusion reasons treated as an authoritative, terminal exclusion
# (ADR-049's "terminal_authoritative_exclusion"): each is either a confident
# linkage/reachability fact (not-exported, non-public-type), a provenance
# demotion that `surface._origin_reason` only applies when *both* snapshot
# sides agree on it (private-header, system-header), or a detector-confirmed
# unreachability/off-surface determination (private-internal-unreachable,
# off-python-surface). None of these is a "we simply couldn't tell" case.
_TERMINAL_SURFACE_REASONS: frozenset[str] = frozenset(
    {
        REASON_NOT_EXPORTED,
        REASON_NON_PUBLIC_TYPE,
        REASON_PRIVATE_HEADER,
        REASON_SYSTEM_HEADER,
        REASON_PRIVATE_INTERNAL_UNREACHABLE,
        REASON_OFF_PYTHON_SURFACE,
    }
)

# `no-provenance` is `surface.py`'s own documented exception: a reachability
# demotion made *without* the provenance corroboration available for the rest
# of the snapshot (reduced confidence, by that module's own docstring) --
# never treated as terminal here.
_WEAK_SURFACE_REASONS: frozenset[str] = frozenset({REASON_NO_PROVENANCE})

_ALL_SURFACE_REASONS: frozenset[str] = _TERMINAL_SURFACE_REASONS | _WEAK_SURFACE_REASONS

assert _TERMINAL_SURFACE_REASONS.isdisjoint(_WEAK_SURFACE_REASONS), (
    "a surface-exclusion reason cannot be both terminal and weak"
)


@dataclass(frozen=True)
class ContractEvaluationDecision:
    """One finding's provisional contract-relevance decision.

    Shadow output only (see module docstring) -- never consulted by
    detection, policy, the CLI, or reports. ``reason_code`` is always one of
    :data:`~abicheck.contract_relevance_types.CONTRACT_REASON_CODES`'s keys.
    """

    relevance: ContractRelevance
    reason_code: str
    assurance: ContractAssurance

    def __post_init__(self) -> None:
        if self.reason_code not in CONTRACT_REASON_CODES:
            raise ValueError(f"unknown contract reason code: {self.reason_code!r}")


def _not_applicable_decision() -> ContractEvaluationDecision:
    return ContractEvaluationDecision(
        relevance=NON_ENTITY_RELEVANCE,
        reason_code="non_entity_finding",
        assurance=ContractAssurance.COMPLETE,
    )


def _all_mode_decision() -> ContractEvaluationDecision:
    return ContractEvaluationDecision(
        relevance=ContractRelevance.IN_CONTRACT,
        reason_code="all_mode_normalized_entity",
        assurance=ContractAssurance.COMPLETE,
    )


def _unresolved_decision(
    reason_code: str, assurance: ContractAssurance
) -> ContractEvaluationDecision:
    return ContractEvaluationDecision(
        relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
        reason_code=reason_code,
        assurance=assurance,
    )


def _decision_for_surface_reason(reason: str) -> ContractEvaluationDecision:
    """Map a surface-exclusion reason to its terminal/weak decision.

    Shared by the already-excluded-by-pipeline short-circuit and the fresh
    :func:`~abicheck.surface.classify_change_surface` fallback in
    :func:`evaluate_change_contract_relevance`, so the two paths cannot
    silently diverge if one branch's mapping changes without the other
    (CodeRabbit review).
    """
    if reason in _TERMINAL_SURFACE_REASONS:
        return ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )
    return _unresolved_decision(
        "required_evidence_incomplete", ContractAssurance.PARTIAL
    )


def evaluate_change_contract_relevance(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    unions: SurfaceUnions | None = None,
) -> ContractEvaluationDecision:
    """Compute *change*'s shadow contract-relevance decision.

    ``mode`` selects the declared contract (only ``PUBLIC``/``ALL`` are
    implemented -- see the module docstring). ``surf_old``/``surf_new`` are
    the same :class:`~abicheck.surface.PublicSurface` pair
    ``FilterNonPublicSurface`` already computes; pass a precomputed
    ``unions`` (:func:`~abicheck.surface.surface_unions`) when evaluating many
    changes against the same pair, mirroring
    :func:`~abicheck.surface.classify_change_surface`'s own guidance.

    Never raises for a *finding* it cannot confidently classify -- every such
    case degrades to ``UNKNOWN_UNRESOLVED`` (see the module docstring's
    ``UNKNOWN_UNPROVEN`` rule). It raises for an entirely invalid ``mode``
    value (``ValueError``) or for a *mode* this evaluator does not implement
    at all (``NotImplementedError``).
    """
    # `ContractMode` is a `str` Enum, so an untyped caller passing the bare
    # serialized value (e.g. `"all"` from a config/API adapter) would satisfy
    # `mode in _SUPPORTED_MODES` (equality/hash) but then silently fail the
    # `is ContractMode.ALL` identity check below, falling through to the
    # PUBLIC path for a caller that actually asked for ALL (Codex review).
    # Coercing through the enum constructor first (a no-op for an
    # already-real member) means every later `is` comparison is safe.
    try:
        mode = ContractMode(mode)
    except ValueError as exc:
        raise ValueError(
            f"mode must be one of {sorted(m.value for m in ContractMode)}, got {mode!r}"
        ) from exc
    if mode not in _SUPPORTED_MODES:
        raise NotImplementedError(
            f"contract mode {mode!r} is not implemented by the Phase 3 shadow "
            "evaluator yet -- only ContractMode.PUBLIC and ContractMode.ALL "
            "are (see abicheck.contract_evaluation's module docstring)"
        )

    if change.kind.value in _NOT_APPLICABLE_KIND_SLUGS:
        return _not_applicable_decision()

    if mode is ContractMode.ALL:
        return _all_mode_decision()

    # mode is ContractMode.PUBLIC from here on.
    # A finding already demoted to the audit ledger by an earlier pipeline
    # step (FilterNonPublicSurface / DemoteOffPythonSurface /
    # DemoteUnreachableInternalChurn, post_processing.py) already carries
    # that step's own authoritative `surface_exclusion_reason` -- consult it
    # before falling through to a from-scratch `classify_change_surface`
    # recomputation below, which can reach a *different*, weaker conclusion
    # than the specialized detector that originally produced it. Concretely:
    # a `DemoteOffPythonSurface` change necessarily has no C-header surface
    # to scope against (that step's own precondition), so recomputing here
    # would hit the unresolvable-surface branch and lose the fact that the
    # off-Python-surface determination was already conclusive; a
    # `DemoteUnreachableInternalChurn` change could be reachable under this
    # module's own (different, coarser) reachability closure even though
    # the specialized internal-leak check already proved no leak path
    # exists, which would wrongly reclassify it IN_CONTRACT (Codex review).
    if change.surface_exclusion_reason in _ALL_SURFACE_REASONS:
        return _decision_for_surface_reason(change.surface_exclusion_reason)

    if not (surf_old.resolvable and surf_new.resolvable):
        # No header-derived visibility on one or both sides: no confident
        # contract-relevance claim is possible for *any* entity finding,
        # including one whose kind `surface.py` itself would always keep
        # in-surface (a leak finding, a `python_*` finding) -- that rule is
        # about not *hiding* a finding from a report, an entirely different
        # question from "can this shadow evaluator confidently label it".
        return _unresolved_decision(
            "required_evidence_incomplete", ContractAssurance.UNAVAILABLE
        )

    identity = resolve_change_identity(change)
    if identity.tier == IDENTITY_TIER_REDUCED:
        return _unresolved_decision("identity_ambiguous", ContractAssurance.PARTIAL)

    in_surface, reason = classify_change_surface(
        change, surf_old, surf_new, unions=unions
    )
    if in_surface:
        return ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    # `classify_change_surface` only returns `in_surface=False` with a reason
    # drawn from `_ALL_SURFACE_REASONS` once both sides are resolvable (the
    # branch above already handled the unresolvable case) -- see that
    # function's own reason-code constants.
    assert reason in _ALL_SURFACE_REASONS, f"unrecognized surface reason: {reason!r}"
    return _decision_for_surface_reason(reason)


def evaluate_snapshot_pair_contract_relevance(
    changes: list[Change],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
) -> list[ContractEvaluationDecision]:
    """:func:`evaluate_change_contract_relevance` for a whole comparison's
    findings, computing the surface unions once (see that function's
    ``unions`` parameter) rather than once per finding."""
    unions = surface_unions(surf_old, surf_new)
    return [
        evaluate_change_contract_relevance(
            change, surf_old, surf_new, mode=mode, unions=unions
        )
        for change in changes
    ]
