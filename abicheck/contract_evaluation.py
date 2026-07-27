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

from .checker_policy import ADDITION_KINDS, ChangeKind
from .checker_types import Change
from .contract_relevance_types import (
    CONTRACT_REASON_CODES,
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from .finding_identity import IDENTITY_TIER_REDUCED, resolve_change_identity
from .model import ScopeOrigin
from .post_processing import _PUBLIC_SOURCE_ABI_KINDS, _change_matches_symbols
from .surface import (
    _HIDDEN_FRIEND_KIND_NAMES,
    _MEMBER_LEVEL_TYPE_KIND_NAMES,
    _NEVER_FILTER_KIND_NAMES,
    _TYPE_LEVEL_KIND_NAMES,
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    REASON_NOT_EXPORTED,
    REASON_OFF_PYTHON_SURFACE,
    REASON_PRIVATE_HEADER,
    REASON_PRIVATE_INTERNAL_UNREACHABLE,
    REASON_SYSTEM_HEADER,
    PublicSurface,
    SurfaceUnions,
    _hidden_friend_owner_effective_origin,
    _one_sided_key_origin,
    _type_identifiers,
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
        # Linker identity (SONAME/RPATH/RUNPATH/DT_NEEDED).
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
        # DT_NEEDED loader dependency list -- which *other* shared libraries
        # this one requires, not a function/variable/type consumer code
        # references. Falling through the ordinary path made these
        # `UNKNOWN_UNRESOLVED` without headers, or `IN_CONTRACT` in `ALL`
        # mode -- both wrong for a loader-deployment fact (Codex review,
        # fresh evidence).
        "needed_added",
        "needed_removed",
        # _diff_needed_order's own reorder-only finding (the dependency SET
        # is unchanged, only DT_NEEDED entry order) -- same synthetic
        # "DT_NEEDED" subject, same loader-level state, not a different kind
        # of entity (Codex review, fresh evidence).
        "needed_order_changed",
        # _diff_dynamic_contract's own PT_INTERP/DT_* loader-control state --
        # program interpreter path, eager/lazy symbol binding, dlopen/dlclose
        # flags, and init/fini array presence are all binary-wide
        # loader-contract properties, the same synthetic-subject shape as
        # the DT_NEEDED findings immediately above, never a specific
        # function/variable/type (Codex review, fresh evidence).
        "interpreter_changed",
        "bind_now_disabled",
        "dynamic_loading_flags_changed",
        "elf_init_fini_changed",
        # DT_SYMBOLIC/DF_SYMBOLIC toggle (ELF) -- the same synthetic-subject
        # loader-control shape as the PT_INTERP/DT_* findings immediately
        # above (`diff_platform_elf_dynamic.py`'s own `symbol="DT_SYMBOLIC"`
        # construction), never a specific function/variable/type (Codex
        # review, fresh evidence).
        "symbolic_binding_mode_changed",
        # LC_ID_DYLIB compat_version (Mach-O) -- a binary-wide loader
        # contract property with its own synthetic `symbol="compat_version"`
        # subject (`diff_platform._diff_macho_compat_version`), the Mach-O
        # counterpart of the ELF loader-state findings above (Codex review,
        # fresh evidence).
        "compat_version_changed",
        # Architecture / file-format identity.
        "pe_machine_changed",
        "wheel_tag_architecture_mismatch",
        "elf_class_changed",
        "elf_osabi_changed",
        "elf_endianness_changed",
        # e_machine / decoded float-ABI-EABI drift -- binary-wide
        # architecture/calling-convention identity, the ELF analogue of
        # pe_machine_changed/macho_cpu_type_changed already covered above
        # (Codex review, fresh evidence).
        "elf_machine_changed",
        "elf_abi_flags_changed",
        # The Mach-O analogue of pe_machine_changed/elf_class_changed --
        # binary-wide CPU architecture identity, not a function/variable/type
        # (Codex review, fresh evidence). Previously fell through to
        # UNKNOWN_UNRESOLVED (PUBLIC mode, no headers) or IN_CONTRACT (ALL
        # mode), skewing shadow relevance metrics for ordinary Mach-O
        # architecture changes the same way the sibling PE/ELF kinds already
        # correctly short-circuit.
        "macho_cpu_type_changed",
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
        # W^X/CET/BTI-PAC hardening posture -- the same binary-wide
        # build-flag property as the security-hardening kinds immediately
        # above, just omitted from the original curation (Codex review,
        # fresh evidence): a writable+executable segment, and IBT/SHSTK
        # (x86 CET) / BTI/PAC (AArch64 branch protection) gained or
        # dropped, are never about one exported entity.
        "writable_executable_segment",
        "text_relocation_removed",
        "cet_protection_weakened",
        "branch_protection_weakened",
        "cet_protection_improved",
        "branch_protection_improved",
        # Toolchain/runtime identity, not a consumer-visible entity.
        "libcpp_abi_version_changed",
        "sycl_plugin_search_path_changed",
        # Deployment/runtime-floor state (ADR-049 D2's "deployment" column):
        # each is a synthesized headline finding over a synthetic subject
        # (e.g. "libc.so.6:GLIBC_2" for RUNTIME_FLOOR_RAISED, per
        # diff_platform_elf_symbols._runtime_floor_changes's own
        # `symbol=f"{lib}:{prefix}"` construction) -- the minimum
        # runtime/OS/CPU-ISA a binary now requires, never a specific
        # function/variable/type a consumer's code references (Codex
        # review, fresh evidence).
        "runtime_floor_raised",
        "platform_baseline_floor_raised",
        "macos_deployment_target_raised",
        "x86_isa_baseline_raised",
        "os_deployment_floor_raised",
    }
)

_SUPPORTED_MODES: frozenset[ContractMode] = frozenset(
    {ContractMode.PUBLIC, ContractMode.ALL}
)

# post_processing.MarkReachability's own set of L4/L5 source-derived kinds
# that are public-contract *by construction* -- each is built only from an
# already-proven-public entity (see that set's own docstring), never a bare
# namespace-name heuristic. Their `symbol` is a macro/inline-function/
# typedef/etc. name, never a real C/C++ header-surface function/variable/
# type -- `classify_change_surface` cannot place it in either the symbol or
# type universe, so it falls through to the "cannot place it -- keep it"
# conservative fallback, which `_in_surface_result_is_confirmed` correctly
# refuses to treat as genuine confirmation. Without this early-return, every
# one of these definitively-public findings was wrongly downgraded to
# `UNKNOWN_UNRESOLVED` even with fully resolvable surfaces on both sides --
# confirmed empirically for `PUBLIC_MACRO_REMOVED` (Codex review, fresh
# evidence). Imported directly (not duplicated as a literal, unlike
# `_REASON_POST_MANIFEST_NOT_COMMITTED` below): this is an *import* of an
# already-existing symbol, not a new declaration added to `post_processing.py`,
# so it does not trip that module's line-count hard cap.
_PUBLIC_SOURCE_ABI_KIND_SLUGS: frozenset[str] = frozenset(
    k.value for k in _PUBLIC_SOURCE_ABI_KINDS
)

# post_processing.FilterNonPublicSurface._run_allowlist's own
# Change.surface_exclusion_reason for a `--post-manifest` demotion -- a
# distinct pipeline's authoritative exclusion, not one of surface.py's
# REASON_* codes. Not imported from post_processing.py: that module sits at
# the repo's 2000-line hard cap (AI-readiness gate), so no shared constant
# can be added there without a separate splitting effort -- duplicated here
# as a literal instead, accepting the (documented) drift risk if that
# module's message text ever changes.
_REASON_POST_MANIFEST_NOT_COMMITTED = "not in POST manifest committed surface"

# Surface-exclusion reasons treated as an authoritative, terminal exclusion
# (ADR-049's "terminal_authoritative_exclusion"): each is either a confident
# linkage/reachability fact (non-public-type), a provenance demotion that
# `surface._origin_reason` only applies when *both* snapshot sides agree on
# it (private-header, system-header), a detector-confirmed
# unreachability/off-surface determination (private-internal-unreachable,
# off-python-surface), or an exact-manifest closed-domain exclusion
# (post-manifest-not-committed, ADR-049 D2's "exact manifests" evidence
# provider -- `--post-manifest` only ever demotes a *concrete* export absent
# from the committed allowlist, never an ambiguous case). None of these is a
# "we simply couldn't tell" case. Recognizing the post-manifest reason here
# matters: without it, a POST-manifest-demoted finding whose symbol also
# happens to be header-resolvable would fall through to a fresh
# classify_change_surface recomputation and could be wrongly reclassified
# IN_CONTRACT (Codex review).
#
# Known, deliberately-deferred gap against ADR-049 D5 (Codex review, fresh
# evidence): D5's `out_of_contract_proof_complete` requires "every selected
# provider capable of stronger-or-equal in-contract evidence completed for
# that entity/domain" before a terminal exclusion is genuinely complete --
# "Private-header provenance alone is never terminal while such a [stronger]
# provider is missing, failed, stale, partial, or identity-ambiguous."
# `REASON_PRIVATE_HEADER`/`REASON_SYSTEM_HEADER` are treated as
# unconditionally terminal here regardless of whether some *other*,
# stronger provider (an exact manifest beyond `--post-manifest`, a
# required-symbol overlay beyond `force_public_symbols`, concrete
# consumer-import evidence) was configured for this run and hasn't
# completed -- because this module has no persisted provider-completeness
# ledger to consult at all (see the module docstring's own "provider ledger
# ... is not built yet"), not because the two known, wireable providers
# this module *can* already see are ignored: `force_public_symbols` and
# `--post-manifest` (`_REASON_POST_MANIFEST_NOT_COMMITTED`) are both
# checked and, by construction, can never coexist with reaching this
# terminal-reason branch for the same finding (each fully resolves the
# finding itself, one way or the other, before this branch is reached).
# The remaining risk is entirely providers this codebase does not yet wire
# as an input anywhere (no caller anywhere constructs "an exact manifest
# was configured and is still loading" data) -- building that ledger is
# Phase 3's own gate item ("every shadow delta has evidence") and Phase 4/5
# territory (`docs/contribute/plans/public-contract-default.md`), not a
# narrow fix available with today's data model.
_TERMINAL_SURFACE_REASONS: frozenset[str] = frozenset(
    {
        REASON_NON_PUBLIC_TYPE,
        REASON_PRIVATE_HEADER,
        REASON_SYSTEM_HEADER,
        REASON_PRIVATE_INTERNAL_UNREACHABLE,
        REASON_OFF_PYTHON_SURFACE,
        _REASON_POST_MANIFEST_NOT_COMMITTED,
    }
)

# `no-provenance` is `surface.py`'s own documented exception: a reachability
# demotion made *without* the provenance corroboration available for the rest
# of the snapshot (reduced confidence, by that module's own docstring) --
# never treated as terminal here.
#
# `not-exported` (REASON_NOT_EXPORTED) is weak, not terminal, for PUBLIC mode
# specifically: ADR-049 D2 defines `public` mode's evidence domain as
# "declared-public providers" (manifests, package symbol metadata, *public
# declarations*, ...) -- a header-declared entity is in-domain independent of
# whether it happens to be ELF-exported; distinguishing exported-vs-not is
# exactly what the separate `exports` mode exists for. `surface.py`'s
# `_classify_symbol_level` emits this reason for a symbol that is `known`
# (declared, and not confidently private/system-header) but not
# `Visibility.PUBLIC` -- confirmed empirically that this fires for a function
# whose origin is `PUBLIC_HEADER` but whose visibility is e.g. `HIDDEN`
# (an inline or explicitly-hidden public-header declaration), the exact case
# ADR-049's `public` domain should still cover. Treating it as terminal here
# wrongly resolved such a finding to `PROVEN_OUT_OF_CONTRACT` with `COMPLETE`
# assurance, contradicting the ADR (Codex review).
_WEAK_SURFACE_REASONS: frozenset[str] = frozenset(
    {REASON_NO_PROVENANCE, REASON_NOT_EXPORTED}
)

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


def _symbol_authoritative_origin_is_public_header(
    change: Change, surf_old: PublicSurface, surf_new: PublicSurface
) -> bool:
    """Whether *change*'s symbol is confidently ``PUBLIC_HEADER`` on the
    authoritative side (ADR-049 D4), mirroring
    ``surface._classify_symbol_level``'s own bare/qualified-tail matching
    against ``origin_by_key`` -- the only two shapes that can ever reach
    ``REASON_NOT_EXPORTED`` in the first place.
    """
    auth = _authoritative_surface(change, surf_old, surf_new)
    sym = change.symbol or ""
    if sym in auth.origin_by_key:
        return auth.origin_by_key[sym] == ScopeOrigin.PUBLIC_HEADER
    if sym and "::" in sym:
        tail = sym.rsplit("::", 1)[1]
        if tail in auth.origin_by_key:
            return auth.origin_by_key[tail] == ScopeOrigin.PUBLIC_HEADER
    return False


def _decision_for_surface_reason(
    reason: str, change: Change, surf_old: PublicSurface, surf_new: PublicSurface
) -> ContractEvaluationDecision:
    """Map a surface-exclusion reason to its terminal/weak decision.

    Shared by the already-excluded-by-pipeline short-circuit and the fresh
    :func:`~abicheck.surface.classify_change_surface` fallback in
    :func:`evaluate_change_contract_relevance`, so the two paths cannot
    silently diverge if one branch's mapping changes without the other
    (CodeRabbit review).

    ``REASON_NOT_EXPORTED`` is weak, not terminal (see
    ``_WEAK_SURFACE_REASONS``'s own comment: ADR-049 D2's ``public`` domain
    includes "declared-public providers" independent of ELF-export status)
    -- but a bare "weak, so stay unresolved" treatment under-claims exactly
    the case D2 names: a declaration whose authoritative-side origin *is*
    confidently ``PUBLIC_HEADER`` (an inline function, or one with explicit
    hidden visibility, still declared in a public header) is genuinely
    "declared public" by ADR-049's own definition, not merely "we couldn't
    tell." Checking the authoritative side's origin directly resolves that
    case to `IN_CONTRACT` instead of leaving a definitively public-header
    declaration stuck at `UNKNOWN_UNRESOLVED` (Codex review, fresh
    evidence). A `REASON_NOT_EXPORTED` symbol whose authoritative origin is
    *not* confidently `PUBLIC_HEADER` (merely `UNKNOWN`, the "we genuinely
    can't tell" case `_origin_reason` also routes here) still degrades to
    the weak, unresolved default.
    """
    if reason in _TERMINAL_SURFACE_REASONS:
        return ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )
    if reason == REASON_NOT_EXPORTED and _symbol_authoritative_origin_is_public_header(
        change, surf_old, surf_new
    ):
        return ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )
    return _unresolved_decision(
        "required_evidence_incomplete", ContractAssurance.PARTIAL
    )


# Genuine "a wholly new entity/commitment appears" kinds that `ADDITION_KINDS`
# misses purely because that set is scoped to *compatible* additions
# (`change_registry.py`'s `default_verdict`, a verdict-category concern) --
# not because these kinds are shaped any differently. Each is the direct
# breaking/risky sibling of an addition shape already recognized:
# `type_field_added` is `type_field_added_compatible`'s same "a new field
# appears" shape, just breaking when the class isn't guaranteed final
# (`change_registry.py`: "New field shifts subsequent fields"); mirroring
# `virtual_method_added`'s own registry impact text ("A new virtual method
# was added to a class that already exists"), it is unambiguously a new
# member appearing, not an existing member gaining a property, the same
# distinction the `ADDITION_KINDS`-only check already draws correctly for
# every *_deprecated_added/*_noexcept_added/*_virtual_added-style kind
# below. `overload_added` ("a new overload was added under a public name
# that previously had exactly one declaration", per its own registry impact
# text) is the same shape again -- a new declaration appearing, not an
# existing one gaining a property -- so an added overload confirmed public
# only on the new side (no old-side header evidence at all, since the
# overload itself didn't exist yet) must use new-side authority too (Codex
# review, fresh evidence). Confirmed empirically that `_authoritative_surface`
# returned the old side for all three before their respective fixes, so a
# new-side-confirmed addition with no old-side header evidence fell to
# `UNKNOWN_UNRESOLVED` instead of `IN_CONTRACT`, contrary to ADR-049 D4.
#
# Deliberately NOT included, considered and left as a documented gap since
# no Codex finding has named these and this module's own discipline is to
# fix the verified case rather than speculatively expand it:
# `imported_symbol_added`/`target_dependency_added`/
# `public_api_internal_dependency_added`/`numpy_capi_consumption_added`/
# `symbol_version_defined_added`/`symbol_version_required_added` (each
# describes a new edge/obligation in a dependency or versioning graph
# rather than a new function/variable/type member, so whether "new-side
# authoritative" is even the right frame for them needs its own scoped
# look, not a drive-by inclusion here).
_NON_COMPATIBLE_ADDITION_SHAPE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.VIRTUAL_METHOD_ADDED,
        ChangeKind.OVERLOAD_ADDED,
    }
)


def _authoritative_surface(
    change: Change, surf_old: PublicSurface, surf_new: PublicSurface
) -> PublicSurface:
    """Which side's evidence is authoritative for *change* (ADR-049 D4).

    "Removal and modification of an existing obligation use old-side
    evidence. Addition and a new commitment use new-side evidence." --
    checked via ``checker_policy.ADDITION_KINDS`` unioned with
    :data:`_NON_COMPATIBLE_ADDITION_SHAPE_KINDS`, rather than a naive
    ``kind.value.endswith("_added")`` string heuristic: confirmed
    empirically that many kinds *ending* in ``"_added"`` are themselves
    modifications of an already-existing obligation gaining a new
    *property* -- e.g. ``func_noexcept_added``/``func_virtual_added``/
    ``ctor_explicit_added``/``*_deprecated_added`` describe an existing
    function/type acquiring a qualifier, not a new function/type
    appearing -- so a suffix match would wrongly treat those as new-side
    authoritative (Codex review, fresh evidence). ``ADDITION_KINDS`` alone
    is deliberately narrower (17 members) than the ~40 ``"_added"``-suffixed
    slugs for exactly this reason: it excludes exactly the "existing entity
    gained a property" shape -- but it is also scoped to *compatible*
    additions only, a verdict-category concern orthogonal to temporal
    direction (ADR-049 D4 says nothing about severity), so a genuinely
    new-entity-shaped kind that happens to default to a breaking/risky
    verdict (``type_field_added``, ``virtual_method_added``, ``overload_added``)
    was wrongly excluded too until :data:`_NON_COMPATIBLE_ADDITION_SHAPE_KINDS`
    was added (Codex review, fresh evidence).

    Without this, a symbol/type's public-root confirmation was checked
    against the *union* of both sides regardless of the change's direction
    -- so a `FUNC_RETURN_CHANGED` finding on a function that was private in
    `surf_old` and became public in `surf_new` was wrongly confirmed
    `IN_CONTRACT` via the new side's membership alone, even though a
    *modification* of an *existing* obligation must be judged by what that
    obligation *was* (the old side), not what it later became -- new public
    evidence cannot retroactively manufacture confidence about an old,
    unresolved/private commitment (Codex review, fresh evidence).
    """
    if (
        change.kind in ADDITION_KINDS
        or change.kind in _NON_COMPATIBLE_ADDITION_SHAPE_KINDS
    ):
        return surf_new
    return surf_old


def _hidden_friend_confirmed_public(
    change: Change, surf_old: PublicSurface, surf_new: PublicSurface
) -> bool:
    """Whether a ``hidden_friend_removed``/``hidden_friend_added`` finding's
    ``True`` verdict from ``surface._classify_hidden_friend_surface`` is
    backed by genuine origin provenance, not its own step-3 conservative
    fallback ("origin is unknown/unconfirmed: keep the finding").

    Mirrors that function's own two confirming checks -- the befriending
    owner's effective origin (``change.caused_by_type``, preferred) and,
    independently, the friend function's own recorded origin (``change.symbol``)
    -- without reimplementing the demotion side, since a ``False`` verdict
    from the classifier never reaches this helper at all (only its ``True``
    outcomes are ambiguous between "confirmed" and "kept anyway"). A hidden
    friend can never produce a real export (compiled inline into every
    caller via ADL), so it need not, and typically will not, appear in
    ``public_symbols``/``public_types`` at all -- checking those universes
    the way the generic path below does would systematically underconfirm
    this kind, which is exactly what a prior version of this function did
    (Codex review, ninth round).

    Checks only :func:`_authoritative_surface`'s side (ADR-049 D4), not
    "either side" -- ``hidden_friend_added`` is itself one of
    ``ADDITION_KINDS`` (a new friend appearing), so it is judged by the new
    side alone; ``hidden_friend_removed`` is judged by the old side alone,
    the side on which the friend/owner actually needs to have been public
    for its removal to be a real public-contract break (Codex review, fresh
    evidence).
    """
    auth = _authoritative_surface(change, surf_old, surf_new)
    owner = change.caused_by_type
    if owner:
        bare = owner.rsplit("::", 1)[-1] if "::" in owner else owner
        if (
            _hidden_friend_owner_effective_origin(auth, owner, bare)
            == ScopeOrigin.PUBLIC_HEADER
        ):
            return True
    sym = change.symbol or ""
    return (
        _one_sided_key_origin(auth, sym, auth.all_symbols) == ScopeOrigin.PUBLIC_HEADER
    )


def _in_surface_result_is_confirmed(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
) -> bool:
    """Whether ``classify_change_surface``'s ``True`` verdict for *change*
    reflects genuine public-root/closure membership, not its anti-hiding
    "cannot place it, so keep it" fallback.

    ``classify_change_surface`` returns ``(True, None)`` from several
    distinct sources with very different confidence:

    - ``_NEVER_FILTER_KIND_NAMES`` (a leak finding, or a ``constant_*``
      finding -- public-contract by construction per the dumper's own
      extraction rule, so it would never even appear in
      ``public_symbols``/``public_types``) and ``python_*``-prefixed
      findings are both unconditionally trustworthy by construction,
      independent of any universe-membership check -- but both are handled
      earlier in :func:`evaluate_change_contract_relevance` itself, before
      the resolvable-surface gate *and* the identity-ambiguity gate, not
      here: gating either on C/C++ header surface resolvability or on
      identity tiering would downgrade a definitive event whenever an
      unrelated evidence gap exists (e.g. `VISIBILITY_LEAK`'s
      `symbol="<visibility>"` sentinel resolves to `finding_identity`'s
      reduced tier, a batch finding with no real per-entity symbol --
      confirmed empirically -- so this function is never even reached for
      it now) (Codex review, eleventh and thirteenth rounds).
    - ``_HIDDEN_FRIEND_KIND_NAMES`` findings go through
      ``surface._classify_hidden_friend_surface`` instead of the ordinary
      symbol/type path -- delegated to :func:`_hidden_friend_confirmed_public`
      (Codex review, tenth round), since neither the ordinary symbol
      universe (a hidden friend never becomes a real export) nor the
      ordinary type-candidate derivation (``change.symbol`` names a
      function, not a type) applies to this kind at all.
    - A member-level finding (``_MEMBER_LEVEL_TYPE_KIND_NAMES``, e.g.
      ``TYPE_FIELD_OFFSET_CHANGED`` with ``symbol="Point::x"``) must be
      confirmed against its *owner* type, exactly like
      ``classify_change_surface`` reclassifies it: passing the full
      ``"Point::x"`` to ``_type_identifiers`` yields ``{"Point::x", "x"}``,
      never the owner ``"Point"`` that is actually the ``public_types``
      entry, so this case previously always failed confirmation and was
      downgraded to ``UNKNOWN_UNRESOLVED`` even for a genuinely public
      field/enum-member change (Codex review, ninth round).
    - Every other ``True`` comes from ``_classify_symbol_level``/
      ``_classify_type_level``, where ``sym in public_symbols``/``known &
      public_types`` is genuine confirmation. The ``public_symbols`` check
      here is gated to non-``_TYPE_LEVEL_KIND_NAMES`` findings, mirroring
      ``classify_change_surface``'s own gating -- a type-level finding's
      ``symbol`` can coincidentally collide with an unrelated real
      function/variable of the same spelling (Codex review, fourteenth
      round). ``_classify_type_level`` also returns ``(True, None)`` when
      the implicated type is entirely absent from the snapshot's own type
      universe (`known` empty, "we cannot place this finding -- keep it")
      or is deferred to the separate, more precise internal-leak detector
      (an internal-namespace type). Neither of those two is evidence of
      public membership; both
      are silently upgraded to ``IN_CONTRACT`` without this check
      (Codex review, eighth round).

    Uses ``_type_identifiers`` (mirroring, not reimplementing,
    ``classify_change_surface``'s own candidate derivation) rather than a
    naive raw-string comparison, since a raw ``caused_by_type`` spelling
    (``"const Foo *"``) would never literal-match a bare ``all_types``/
    ``public_types`` entry (``"Foo"``).

    A type-candidate match is also rejected when every matching candidate is
    ambiguous -- either the candidate itself is flagged in the authoritative
    side's own ``ambiguous_type_names`` (two distinct records/enums sharing
    one bare tail, e.g. ``one::Point``/``two::Point`` both spelled bare
    ``Point``:
    ``compute_public_surface`` deliberately keeps *both* records in
    ``public_types`` rather than silently dropping either, its own
    anti-hiding rule), or the candidate is a *qualified* name whose own
    trailing tail is ambiguous (``ns1::Mode``/``ns2::Mode`` sharing bare tail
    ``Mode``): ``_walk_type_closure`` reaches an ambiguous bare tail from a
    public signature by adding *every* matching record/enum's full qualified
    name to ``public_types`` (walking each one, so a real dependency of
    either isn't hidden) -- so a qualified candidate's presence in
    ``public_types`` does not by itself prove *that* candidate, rather than
    its ambiguous sibling, is what the public signature actually reaches
    (Codex review, twelfth round). Neither case establishes which record --
    or whether either -- this finding's root actually resolves to.

    **Known over-rejection, investigated and deliberately not fixed this
    pass (Codex review, fifteenth round):** the qualified-tail rejection
    above is intentionally blunt and can reject a genuinely exact match.
    When a public signature names ``ns1::Point`` *explicitly* (fully
    qualified) and the snapshot separately contains an unrelated
    ``ns2::Point``, ``_type_identifiers`` still derives the bare tail
    ``"Point"`` from that qualified reference (by design, so a short alias
    referenced from inside its own namespace still resolves) -- which is
    *also* how the ambiguous-bare-tail path itself reaches both siblings --
    so ``ns1::Point``'s membership in ``public_types`` and ``ns2::Point``'s
    membership are structurally indistinguishable from this function's
    inputs alone: both a genuinely exact qualified reference and a purely
    conservative ambiguous-tail guess leave the *identical* final
    ``public_types``/``ambiguous_type_names`` state, since
    ``_walk_type_closure`` queues the bare tail unconditionally alongside
    the qualified form for every ``"::"``-bearing token, not only when the
    original reference truly was bare. Confirmed empirically: constructing
    a public function taking ``ns1::Point`` by exact qualified name plus an
    unrelated ``ns2::Point`` record reproduces the wrong
    ``UNKNOWN_UNRESOLVED`` this docstring's own twelfth-round fix was
    designed to produce only for a *bare* ``"Mode"``-style reference (see
    the twelfth-round test using ``params=["Mode"]``, never
    ``params=["ns1::Mode"]``). Distinguishing the two needs new provenance
    data this function doesn't have access to: whether a *specific* route
    into ``public_types`` for a given qualified name came from
    ``_walk_type_closure`` matching a token's own full-name key directly
    (exact) or only from matching a *different*, bare token against
    ``record_by_name``/``enum_by_name``'s ambiguous tail-keyed entry
    (conservative guess) -- ``PublicSurface`` records neither route
    separately today, only the merged final sets. A real fix means adding
    that per-type provenance to ``surface.py``'s own closure walk -- the
    public-surface-scoping gate every other detector in the codebase
    depends on, not a boundary specific to this still-unwired shadow
    module -- which needs its own careful, independently-verified design
    (mirroring this file's already-documented deferral of the analogous
    ``surface.py``-vs-``type_reachability.py`` owner-seeding gap), not a
    same-PR drive-by extension of this narrower confirmation check. Left as
    a known false-negative-producing conservatism (never a false
    ``IN_CONTRACT``, only an occasional wrongly-``UNKNOWN_UNRESOLVED``) --
    the same direction every other unresolvable case in this function
    already defaults to.

    **Checks only the authoritative side (ADR-049 D4), not the old∪new
    union:** "Removal and modification of an existing obligation use
    old-side evidence. Addition and a new commitment use new-side
    evidence... If the authoritative side is unresolved, evidence from the
    other side cannot manufacture confidence." Confirmed empirically that
    checking the union let new-side evidence retroactively confirm an old,
    private/unresolved obligation: a `FUNC_RETURN_CHANGED` finding on a
    function that was private in `surf_old` and became public in
    `surf_new` was wrongly confirmed `IN_CONTRACT` purely because the
    *new* side's `public_symbols` happened to contain it, even though a
    modification must be judged by what the obligation *was* (Codex
    review, fresh evidence). Delegates to :func:`_authoritative_surface`
    (``ADDITION_KINDS`` for new-side authority, old side otherwise) and
    checks only that one side's ``public_symbols``/``public_types``/
    ``ambiguous_type_names`` throughout.
    """
    if change.kind.value in _HIDDEN_FRIEND_KIND_NAMES:
        return _hidden_friend_confirmed_public(change, surf_old, surf_new)
    auth = _authoritative_surface(change, surf_old, surf_new)
    sym = change.symbol or ""
    # `classify_change_surface` never consults `public_symbols` for a
    # type-level kind at all (`_classify_symbol_level` is only called when
    # `not type_level_finding`) -- exactly to prevent a type name colliding
    # with an unrelated exported function/variable of the same spelling
    # (surface.py's own `_TYPE_LEVEL_KIND_NAMES` docstring: castxml
    # represents an implicit same-named constructor unmangled, so a type
    # "Foo" can share a bare name with a real symbol "Foo" that has nothing
    # to do with it). Confirmed empirically: a type completely unknown to
    # either snapshot (classify_change_surface's own "cannot place it, keep
    # it" fallback -- not genuine confirmation) whose symbol happened to
    # match an unrelated public function of the same name was wrongly
    # confirmed via this check before gating it the same way (Codex
    # review).
    if change.kind.value not in _TYPE_LEVEL_KIND_NAMES:
        if sym in auth.public_symbols:
            return True
        if sym and "::" in sym and sym.rsplit("::", 1)[1] in auth.public_symbols:
            return True
    if change.kind.value in _MEMBER_LEVEL_TYPE_KIND_NAMES and sym and "::" in sym:
        candidates = {sym.rsplit("::", 1)[0]} | _type_identifiers(change.caused_by_type)
    else:
        candidates = _type_identifiers(sym) | _type_identifiers(change.caused_by_type)
    matched = candidates & auth.public_types
    ambiguous = auth.ambiguous_type_names
    confirmed_matches = {
        m
        for m in matched
        if m not in ambiguous and not ("::" in m and m.rsplit("::", 1)[1] in ambiguous)
    }
    return bool(confirmed_matches)


def evaluate_change_contract_relevance(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    unions: SurfaceUnions | None = None,
    force_public_symbols: frozenset[str] | None = None,
) -> ContractEvaluationDecision:
    """Compute *change*'s shadow contract-relevance decision.

    ``mode`` selects the declared contract (only ``PUBLIC``/``ALL`` are
    implemented -- see the module docstring). ``surf_old``/``surf_new`` are
    the same :class:`~abicheck.surface.PublicSurface` pair
    ``FilterNonPublicSurface`` already computes; pass a precomputed
    ``unions`` (:func:`~abicheck.surface.surface_unions`) when evaluating many
    changes against the same pair, mirroring
    :func:`~abicheck.surface.classify_change_surface`'s own guidance.
    ``force_public_symbols`` mirrors ``PipelineContext.force_public_symbols``
    (ADR-024 D6's widening overlay, ``FilterNonPublicSurface``'s
    ``_run_scope``/``_run_allowlist``) -- omit it (the default) when the
    caller has no such overlay configured for this run.

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
        return _decision_for_surface_reason(
            change.surface_exclusion_reason, change, surf_old, surf_new
        )

    # A `python_*` finding lives on a distinct evidence axis (the Python
    # API/stub surface) the C/C++ header-surface universes below don't cover
    # at all -- public by construction, exactly like
    # `classify_change_surface`'s own unconditional trust for this prefix.
    # `_NEVER_FILTER_KIND_NAMES` (a leak finding, or a `constant_*` finding)
    # is the identical "trusted by construction, independent of any
    # universe/identity evidence" shape -- both checked here, before the
    # resolvable-surface gate *and* the identity-ambiguity gate immediately
    # below, rather than only inside `_in_surface_result_is_confirmed`
    # (reachable only after both those gates already passed). Concretely: a
    # `VISIBILITY_LEAK` finding's `symbol="<visibility>"` sentinel resolves
    # to `finding_identity`'s reduced tier (a batch finding with no real
    # per-entity symbol) -- confirmed empirically -- so the identity check
    # below downgraded every visibility-leak finding to
    # `UNKNOWN_UNRESOLVED`/`identity_ambiguous` before this module's own
    # unconditional-trust rule for `_NEVER_FILTER_KIND_NAMES` ever got a
    # chance to apply (Codex review, thirteenth round). Gating a
    # Python-axis or never-filter finding on C/C++ header surface
    # resolvability or on this module's own identity-ambiguity tiering would
    # downgrade a definitive event (`PYTHON_API_FUNCTION_REMOVED`,
    # `VISIBILITY_LEAK`) purely because of an unrelated evidence gap.
    # `_PUBLIC_SOURCE_ABI_KIND_SLUGS` (post_processing.py's own
    # `_PUBLIC_SOURCE_ABI_KINDS`) is the identical "trusted by construction"
    # shape for the L4/L5 source-derived evidence axis: each of these kinds
    # is built only from an already-proven-public entity, so its `symbol`
    # (a macro/inline-function/typedef/template/etc. name) is never a real
    # C/C++ header-surface function/variable/type `classify_change_surface`
    # can place -- checked here, at the same early point as `python_*`/
    # `_NEVER_FILTER_KIND_NAMES`, for the identical reason: gating it on
    # C/C++ header surface resolvability or on this module's own
    # identity-ambiguity tiering would downgrade a definitive
    # `PUBLIC_MACRO_REMOVED`/`INLINE_FUNCTION_REMOVED`/etc. event purely
    # because of an unrelated evidence gap (Codex review, fresh evidence).
    if (
        change.kind.value.startswith("python_")
        or change.kind.value in _NEVER_FILTER_KIND_NAMES
        or change.kind.value in _PUBLIC_SOURCE_ABI_KIND_SLUGS
    ):
        return ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    # `force_public_symbols` (ADR-024 D6's widening overlay) is a
    # user-guaranteed override: `FilterNonPublicSurface._run_scope`/
    # `_run_allowlist` keep a matching change in-surface *unconditionally*,
    # bypassing their own demotion path entirely -- such a change therefore
    # never gets a `surface_exclusion_reason` set for this reason (the check
    # above never fires for it), and a from-scratch `classify_change_surface`
    # recomputation here has no way to know the override exists at all, so it
    # can reach a private-header/system-header conclusion that directly
    # contradicts the pipeline's own forced-public decision for the same
    # symbol. Checked at this same early-trust point, mirroring the pipeline
    # step's own "regardless of provenance/export classification" rule
    # (Codex review, fresh evidence).
    if force_public_symbols and _change_matches_symbols(change, force_public_symbols):
        return ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    # ADR-049 D4: "If the authoritative side is unresolved, evidence from
    # the other side cannot manufacture confidence." Checking the
    # *authoritative* side specifically (not "either side" -- a prior
    # version of this gate used `surf_old.resolvable or surf_new.resolvable`,
    # which let an unrelated resolvable *non*-authoritative side substitute
    # for the authoritative one) -- e.g. a `FUNC_REMOVED` finding is proven
    # by the *old* side alone (the function existed and was public there;
    # the new side's own header availability is irrelevant to that fact),
    # so only the old side's resolvability matters for it; a `FUNC_ADDED`
    # finding is the mirror image, judged by the new side alone (Codex
    # review, fresh evidence).
    auth = _authoritative_surface(change, surf_old, surf_new)
    if not auth.resolvable:
        return _unresolved_decision(
            "required_evidence_incomplete", ContractAssurance.UNAVAILABLE
        )

    identity = resolve_change_identity(change)
    if identity.tier == IDENTITY_TIER_REDUCED:
        return _unresolved_decision("identity_ambiguous", ContractAssurance.PARTIAL)

    if unions is None:
        unions = surface_unions(surf_old, surf_new)
    in_surface, reason = classify_change_surface(
        change, surf_old, surf_new, unions=unions
    )
    if in_surface:
        if _in_surface_result_is_confirmed(change, surf_old, surf_new):
            return ContractEvaluationDecision(
                relevance=ContractRelevance.IN_CONTRACT,
                reason_code="public_root_membership",
                assurance=ContractAssurance.COMPLETE,
            )
        return _unresolved_decision(
            "required_evidence_incomplete", ContractAssurance.PARTIAL
        )

    # `classify_change_surface` only returns `in_surface=False` (with a
    # reason drawn from `_ALL_SURFACE_REASONS`) when *both* sides are
    # resolvable -- its own internal gate returns `(True, None)` whenever
    # either side isn't, so this branch is only reachable with full
    # two-sided evidence even though the gate above only requires the
    # *authoritative* side to be resolvable (the non-authoritative side can
    # still be unresolvable and fall through to here, in which case
    # `classify_change_surface`'s own gate keeps it at `in_surface=True`).
    assert reason in _ALL_SURFACE_REASONS, f"unrecognized surface reason: {reason!r}"
    return _decision_for_surface_reason(reason, change, surf_old, surf_new)


def evaluate_snapshot_pair_contract_relevance(
    changes: list[Change],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    force_public_symbols: frozenset[str] | None = None,
) -> list[ContractEvaluationDecision]:
    """:func:`evaluate_change_contract_relevance` for a whole comparison's
    findings, computing the surface unions once (see that function's
    ``unions`` parameter) rather than once per finding. ``force_public_symbols``
    is passed through to every call -- see that function's own parameter."""
    unions = surface_unions(surf_old, surf_new)
    return [
        evaluate_change_contract_relevance(
            change,
            surf_old,
            surf_new,
            mode=mode,
            unions=unions,
            force_public_symbols=force_public_symbols,
        )
        for change in changes
    ]
