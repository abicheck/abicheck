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

All three ADR-049 D2 modes are implemented. ``PUBLIC`` and ``ALL`` read the
header-derived :class:`~abicheck.surface.PublicSurface` pair; ``EXPORTS``
reads its own domain's evidence provider,
:class:`~abicheck.export_surface.ExportSurface`
(:func:`~abicheck.export_surface.compute_export_surface`) -- roots observed in
the binary's own export table, closure over the raw type graph, no
header-origin classification anywhere. That pair is a required argument for
that mode, never approximated from the header surface: silently answering an
export-domain question with header-derived evidence would misrepresent the
mode rather than implement it.

Deliberately conservative: ``UNKNOWN_UNPROVEN`` (``closed_domain_no_commitment``)
comes only from :mod:`abicheck.policy.contract_closed_domain`'s proof (``public``:
an export the complete header domain's raw text never spells); every other such
case is downgraded to the weaker, honestly-hedged
:data:`~abicheck.contract_relevance_types.ContractRelevance.UNKNOWN_UNRESOLVED`
with reason ``required_evidence_incomplete`` instead.

See :doc:`ADR-049
</contribute/adr/049-contract-relevance-and-compatibility-configuration>` and
its :doc:`implementation plan </contribute/plans/public-contract-default>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .compare.finding_identity import IDENTITY_TIER_REDUCED, resolve_change_identity
from .contract_relevance_types import (
    CONTRACT_REASON_CODES,
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractMode,
    ContractRelevance,
    coerce_contract_mode,
)
from .export_surface import ExportSurface
from .model import ScopeOrigin
from .model.change import Change
from .model.change_catalog.kinds import ChangeKind
from .policy.class_metadata_identity import (
    class_metadata_owner_confirmed,
    is_mangled_identity as _is_mangled_identity,
)
from .policy.classification import ADDITION_KINDS
from .policy.contract_closed_domain import closed_domain_decision
from .policy.finding_type_candidates import (
    qualified_type_candidates as _qualified_type_candidates,
    type_candidates as _type_candidates,
)
from .post_processing import _change_matches_symbols
from .post_processing_reachability import _PUBLIC_SOURCE_ABI_KINDS
from .surface import (
    _HIDDEN_FRIEND_KIND_NAMES,
    MEMBER_LEVEL_TYPE_KIND_NAMES,
    NEVER_FILTER_KIND_NAMES,
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    REASON_NOT_EXPORTED,
    REASON_OFF_PYTHON_SURFACE,
    REASON_PRIVATE_HEADER,
    REASON_PRIVATE_INTERNAL_UNREACHABLE,
    REASON_SYSTEM_HEADER,
    TYPE_LEVEL_KIND_NAMES,
    PublicSurface,
    SurfaceUnions,
    _hidden_friend_owner_effective_origin,
    _one_sided_key_origin,
    classify_change_surface,
    surface_unions,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Imported for annotations only: at runtime `contract_evidence_collect`
    # is pulled in inside the two functions that need it, keeping this
    # module free of a module-level edge the `import-cycle-growth` gate
    # would have to reason about (that module imports `surface`/
    # `export_surface`, as this one does).
    from .contract_evidence import ContractEvidenceBlock

# --------------------------------------------------------------------------
# NOT_APPLICABLE: findings that are not about a contract entity at all.
# --------------------------------------------------------------------------


def _non_declaration_kind_slugs() -> frozenset[str]:
    """Every kind whose catalog ``contract_subject`` is not a declaration.

    A dependency edge (``DT_NEEDED``, an import, a required version node), a
    binary-wide property (format, loader state, hardening, a runtime floor), a
    build or toolchain fact, or a statement about the analysis names nothing a
    contract domain can commit to or exclude, so relevance is not its question
    in any mode: ``NOT_APPLICABLE`` keeps it scored by policy exactly as
    before. Derived from the catalog's mandatory per-kind field (ADR-049
    amendment) rather than listed by hand: the hand-curated list it replaced
    grew one field report at a time, and every kind it missed read
    ``UNKNOWN_UNRESOLVED`` under a stated contract and raised the
    contract-coverage exit (``toolchain_flag_drift``, the ``bundle_*`` and
    ``sycl_*`` families, ``imported_symbol_*`` in the SVS report).
    """
    from .change_registry import REGISTRY
    from .model.change_catalog.dimensions import ContractSubject

    return REGISTRY.contract_subject_kinds(
        *(s for s in ContractSubject if s is not ContractSubject.DECLARATION)
    )


_NOT_APPLICABLE_KIND_SLUGS: frozenset[str] = _non_declaration_kind_slugs()


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
# `target_dependency_added`/`public_api_internal_dependency_added`/
# `numpy_capi_consumption_added`/`symbol_version_defined_added` (each a new
# edge/obligation in a dependency or versioning graph, not a new member;
# `imported_symbol_*` is NOT_APPLICABLE via its catalog `contract_subject`).
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

    The kind test itself lives in :func:`_new_side_is_authoritative` so the
    ``exports`` domain's own side selection
    (:func:`_authoritative_export_surface`, over a differently-typed surface
    pair) applies the identical ADR-049 D4 rule instead of a second copy of
    these two kind sets.
    """
    return surf_new if _new_side_is_authoritative(change) else surf_old


def _new_side_is_authoritative(change: Change) -> bool:
    """ADR-049 D4's side rule as a plain predicate -- see
    :func:`_authoritative_surface` for the reasoning and the empirical
    evidence behind both kind sets."""
    return (
        change.kind in ADDITION_KINDS
        or change.kind in _NON_COMPATIBLE_ADDITION_SHAPE_KINDS
    )


def authoritative_side(change: Change) -> str:
    """``"old"`` or ``"new"`` -- which side's evidence decides *change*.

    The public spelling of :func:`_new_side_is_authoritative`, for callers
    that need to *name* the authoritative side rather than select a surface
    with it -- specifically
    :func:`~abicheck.contract_evidence_collect.evidence_refs_for_reason`,
    which cites the provider record for that side (ADR-049 D4). Exposed here
    rather than re-derived there so the two cannot drift apart from the two
    carefully-scoped kind sets :func:`_authoritative_surface` documents.
    """
    return "new" if _new_side_is_authoritative(change) else "old"


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
    *,
    directly_referenced_stdlib_old: frozenset[str] | None = None,
    directly_referenced_stdlib_new: frozenset[str] | None = None,
) -> bool:
    """Whether ``classify_change_surface``'s ``True`` verdict for *change*
    reflects genuine public-root/closure membership, not its anti-hiding
    "cannot place it, so keep it" fallback.

    ``classify_change_surface`` returns ``(True, None)`` from several
    distinct sources with very different confidence:

    - ``NEVER_FILTER_KIND_NAMES`` (a leak finding, or a ``constant_*``
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
    - A member-level finding (``MEMBER_LEVEL_TYPE_KIND_NAMES``, e.g.
      ``TYPE_FIELD_OFFSET_CHANGED`` with ``symbol="Point::x"``) must be
      confirmed against its *owner* type -- handled by :func:`_type_candidates`
      (see its docstring); before that, this case always failed confirmation
      and was downgraded to ``UNKNOWN_UNRESOLVED`` even for a genuinely
      public field/enum-member change (Codex review, ninth round).
    - Every other ``True`` comes from ``_classify_symbol_level``/
      ``_classify_type_level``, where ``sym in public_symbols``/``known &
      public_types`` is genuine confirmation. The ``public_symbols`` check
      here is gated to non-``TYPE_LEVEL_KIND_NAMES`` findings, mirroring
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

    Candidate derivation (:func:`_type_candidates`) is a shared helper -- see
    its own docstring; the ``exports`` domain asks the identical question
    against its own root/closure sets, but (see below) no longer through the
    same confirmation predicate this domain uses.

    **Type-level confirmation goes through ``exact_type_identities``, not
    :func:`_confirmed_type_matches`, as of Codex review's sixteenth round.**
    That function's own name-level ambiguity check (rejecting a candidate
    only when *its own* spelling, or its bare tail, collides with something
    else) is unsound on its own: a candidate whose spelling never collides
    with anything can still have been discovered only by walking a
    *different*, ambiguously-resolved ancestor's fields/bases -- two
    same-bare-tail ``Container`` siblings, where only one happens to declare
    a field of an otherwise-unique type, reproduces it (confirmed
    empirically; see :func:`_confirmed_type_matches`'s own docstring). This
    domain now checks ``auth.exact_type_identities`` directly instead --
    :func:`~abicheck.surface._walk_exact_type_closure`'s own answer to "was
    this candidate reached via a chain where *every* step resolved
    unambiguously", computed by a walk that stops the instant it hits an
    ambiguous fork rather than (like the ordinary closure walk) walking
    every colliding candidate anyway. A signature that *only* ever spells a
    bare, colliding tail (never the qualified form) never gives that walk an
    unambiguous entry point, so it correctly remains ``UNKNOWN_UNRESOLVED``
    (``test_a_public_header_sibling_no_public_api_reaches_is_not_confirmed``,
    and the ``ambiguous_namespaced_leaf`` fp-rate-corpus case, whose public
    function only ever names the bare, colliding spelling and so has no
    exact route to disambiguate from either). The ``exports`` domain has no
    equivalent walk yet, so it still goes through
    :func:`_confirmed_type_matches`'s original (narrower, and for the
    ancestor-ambiguity hazard above, not-yet-fixed) logic -- its own
    docstring explains why extending it is a separately-scoped follow-up.

    **Checks only the authoritative side (ADR-049 D4), not the old-union-new
    universe:** "Removal and modification of an existing obligation use
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

    ``directly_referenced_stdlib_old``/``directly_referenced_stdlib_new``
    (:func:`~abicheck.type_reachability.directly_referenced_stdlib_type_spellings`,
    threaded from :func:`~abicheck.contract_pipeline.build_contract_stage`,
    which is the only place both snapshots are still in scope -- neither
    this function nor its callers receive one) are a second, independent
    confirmation source alongside ``auth.public_types``: a stdlib type a
    public signature names outright is directly ABI-relevant regardless of
    ``surface.py``'s own closure, which deliberately excludes stdlib types
    as non-ABI-surface toolchain internals (that exclusion is exactly what
    lets ``diff_types._is_abi_surface_type`` filter ordinary stdlib-internal
    churn) -- so the finding can reach this function on real evidence
    (``diff_types.py``'s own reachability gate is what let it be *emitted*
    at all) that ``public_types`` was never built to record. Matched against
    ``change.symbol``/``change.caused_by_type`` by *exact* equality, not
    :func:`_type_candidates`'s decomposed tokens: a
    ``TYPE_SIZE_CHANGED``-shaped finding's ``symbol`` is the type's own full
    spelling (``"vector<int, std::allocator<int> >"``), and
    ``_type_identifiers`` -- built for matching a *set* of possibly-several
    referenced type names out of an arbitrary signature -- tokenizes a
    template instantiation into its bare head and each argument
    separately (confirmed empirically: it never yields the type's own full
    spelling back), so intersecting it against a set of full spellings
    could never match.

    Deliberately **not** boundary-safe *containment* (an earlier revision
    of this check) --
    reproduced empirically (Codex review, fresh evidence) that containment
    lets an unrelated, *smaller* directly-referenced identity confirm an
    unrelated *larger* finding purely because one spelling is a substring
    of the other: an authoritative side that directly references
    ``std::allocator<int>`` (a real, independent public root) would also
    "confirm" a `TYPE_SIZE_CHANGED` finding on the unrelated, non-authoritative
    -side-only ``std::vector<int, std::allocator<int> >`` that merely embeds
    that spelling in its own template arguments, letting new-side-only
    evidence manufacture confidence about an old, unresolved obligation --
    exactly the D4 violation ``_authoritative_surface`` exists to prevent.
    Exact-string matching only ever matches a finding against a type that
    *is itself* the reachability walk's own root/reached record, never a
    template argument nested inside a coincidentally-larger finding.

    Checked without the bare-tail ambiguity guard ``_confirmed_type_matches``
    applies to ``public_types``: these spellings come from a per-type
    signature scan over the snapshot's own stdlib ``RecordType`` identities,
    a different, unambiguous data source, not ``surface.py``'s
    shared-bare-key closure where two distinct records can collide.
    """
    if change.kind.value in _HIDDEN_FRIEND_KIND_NAMES:
        return _hidden_friend_confirmed_public(change, surf_old, surf_new)
    auth = _authoritative_surface(change, surf_old, surf_new)
    if _symbol_matches(change, auth.public_symbols):
        return True
    # Deliberately NOT `_confirmed_type_matches` (that function's own
    # docstring explains why its name-level ambiguity check is unsound for
    # this domain) -- `exact_type_identities` is `surface.py`'s own,
    # separately-verified answer to "was this candidate reached via a chain
    # where every step resolved unambiguously", computed by a walk that
    # never even enters an ambiguous fork in the first place. No further
    # ambiguity check is needed on top of it.
    if (
        _type_candidates(change) | _qualified_type_candidates(change)
    ) & auth.exact_type_identities:
        return True
    if class_metadata_owner_confirmed(
        change.symbol or "", auth.exact_type_identities, auth.class_owner_paths
    ):
        return True
    directly_referenced_stdlib = (
        directly_referenced_stdlib_new
        if _new_side_is_authoritative(change)
        else directly_referenced_stdlib_old
    )
    if not directly_referenced_stdlib:
        return False
    return bool(
        (change.symbol and change.symbol in directly_referenced_stdlib)
        or (
            change.caused_by_type
            and change.caused_by_type in directly_referenced_stdlib
        )
    )


def _symbol_matches(
    change: Change, symbols: set[str], *, allow_tail_fallback: bool = True
) -> bool:
    """Whether *change*'s own symbol is a member of *symbols*.

    ``classify_change_surface`` never consults a symbol universe for a
    type-level kind at all (``_classify_symbol_level`` is only called when
    ``not type_level_finding``) -- exactly to prevent a type name colliding
    with an unrelated exported function/variable of the same spelling
    (surface.py's own ``TYPE_LEVEL_KIND_NAMES`` docstring: castxml
    represents an implicit same-named constructor unmangled, so a type
    "Foo" can share a bare name with a real symbol "Foo" that has nothing
    to do with it). Confirmed empirically: a type completely unknown to
    either snapshot (classify_change_surface's own "cannot place it, keep
    it" fallback -- not genuine confirmation) whose symbol happened to
    match an unrelated public function of the same name was wrongly
    confirmed via this check before gating it the same way (Codex review).
    That gate is narrowed, not lifted, for a *mangled* symbol -- see the
    comment on the branch itself.

    Shared by the ``public`` domain's :func:`_in_surface_result_is_confirmed`
    and the ``exports`` domain's :func:`_exports_mode_decision` -- both ask
    the identical "is this finding's own symbol a root of that domain"
    question, differing only in which set is passed.

    *allow_tail_fallback* controls the "a finding may name only the bare leaf"
    tolerance. The ``exports`` domain turns it **off** (Codex review): its
    root set legitimately contains a bare matched export name -- a real C
    symbol ``foo`` -- and deriving the tail of an unrelated qualified finding
    (``ns::foo``) then matches that root through the same spelling, which is
    the alias collision the rootness fix removed from the *other* side.
    Turning it off loses nothing there: an unambiguous bare tail is already a
    key in ``export_symbols`` (``_symbol_keys`` puts it there), so the honest
    cases still match exactly; only the derive-a-tail-from-the-finding step,
    which cannot tell a leaf from a different symbol entirely, is dropped.
    """
    sym = change.symbol or ""
    if change.kind.value in TYPE_LEVEL_KIND_NAMES:
        # ...but a *mangled* symbol carries no such collision risk, and one
        # type-level producer emits exactly that: `diff_symbols.
        # _check_vtable_index_change` reuses `TYPE_VTABLE_CHANGED` for a
        # moved virtual slot and sets `symbol=mangled`, the method's own
        # linker name. The blanket rejection left a vtable-slot change on an
        # observed *exported* method `UNKNOWN_UNRESOLVED` even though its
        # exact mangled name is a root, and `_type_candidates` cannot recover
        # the owning class from an Itanium encoding to recover via the
        # closure either (Codex review, confirmed with a minimal snapshot).
        # Only an *exact* hit counts: `_Z`/`__Z`/`?` are mangling-scheme
        # prefixes no C++ type spelling can occupy, so a bare type/function
        # name cannot reach this branch at all.
        return bool(sym) and _is_mangled_identity(sym) and sym in symbols
    if sym in symbols:
        return True
    if not allow_tail_fallback:
        return False
    return bool(sym and "::" in sym and sym.rsplit("::", 1)[1] in symbols)


def _confirmed_type_matches(
    candidates: set[str],
    types: set[str],
    ambiguous: set[str],
) -> set[str]:
    """*candidates* present in *types* whose identity is not ambiguous.

    A match is rejected when the candidate itself is flagged in *ambiguous*
    (two distinct records/enums sharing one bare tail, e.g. ``one::Point``/
    ``two::Point`` both spelled bare ``Point``: ``compute_public_surface``
    deliberately keeps *both* records rather than silently dropping either,
    its own anti-hiding rule), or when the candidate is a *qualified* name
    whose own trailing tail is ambiguous (``ns1::Mode``/``ns2::Mode`` sharing
    bare tail ``Mode``): ``_walk_type_closure`` reaches an ambiguous bare tail
    from a signature by adding *every* matching record/enum's full qualified
    name (walking each one, so a real dependency of either isn't hidden) --
    so a qualified candidate's presence does not by itself prove *that*
    candidate, rather than its ambiguous sibling, is what the signature
    actually reaches. Neither case establishes which record -- or whether
    either -- this finding's root actually resolves to.

    **An "indecisive ambiguity" shortcut was tried here and reverted; do not
    re-derive it.** The idea was that when every colliding entry is
    public-header origin, both readings put the finding in the contract, so
    the ambiguity has nothing to decide and the finding can be confirmed --
    which would recover the real gate coverage this rejection costs
    (``measure_contract_shadow.py``'s ``unresolved_losses`` counts it). The
    premise is false: ``ScopeOrigin.PUBLIC_HEADER`` records *where a
    declaration was written*, not whether it is reachable from a public
    root, and this module's whole notion of membership is reachability --
    that is what ``compute_public_surface`` computes and what ``types``
    (``public_surface.public_types``) means here. A type declared in a
    public header that no public API reaches is not in the contract.

    Confirmed empirically before reverting (Codex review): with
    ``one::api(Point *)`` reaching ``one::Point`` and an unrelated,
    unreachable ``two::Point`` also declared in a public header, the origin
    count equals the arity, so a layout finding on ``two::Point`` was
    confirmed ``IN_CONTRACT`` and gated the run -- turning a withheld
    finding into a false positive.

    **Only ever called for the ``exports`` domain now** (Codex review,
    sixteenth round): the ``public`` domain has since gained
    ``PublicSurface.exact_type_identities`` -- a genuinely path-aware
    exactness signal computed by a *separate*, ambiguity-vetoing walk
    (:func:`~abicheck.surface._walk_exact_type_closure`) -- which
    :func:`_in_surface_result_is_confirmed` now checks directly instead of
    going through this function. This function's own *name*-level ambiguity
    check (the ``ambiguous``/tail-ambiguous clauses above) is unsound on its
    own for exactly the reason the "indecisive ambiguity" shortcut was: a
    candidate whose *own* spelling never collides with anything can still
    have been discovered only by walking a *different*, ambiguously-resolved
    ancestor's fields/bases -- ``one::Container``/``two::Container`` sharing
    the bare tail ``Container``, where only ``one::Container`` happens to
    declare a field of the otherwise-unique type ``one::Point``, reproduces
    it (confirmed empirically). The ``exports`` domain does not yet have an
    equivalent path-aware closure (``export_surface.py``'s own, separate
    implementation), so it keeps this function's original, narrower (and
    for this one hazard, still theoretically unsound) behavior rather than
    silently losing its only ambiguity check -- fixing that is its own,
    separately-scoped follow-up, not a drive-by extension here.
    """
    return {
        m
        for m in candidates & types
        if m not in ambiguous and not ("::" in m and m.rsplit("::", 1)[1] in ambiguous)
    }


def _authoritative_export_surface(
    change: Change, exp_old: ExportSurface, exp_new: ExportSurface
) -> ExportSurface:
    """:func:`_authoritative_surface`'s ADR-049 D4 side selection, for the
    ``exports`` domain's own surface pair. Same rule, same predicate
    (:func:`_new_side_is_authoritative`) -- only the surface type differs."""
    return exp_new if _new_side_is_authoritative(change) else exp_old


def _export_root_decision() -> ContractEvaluationDecision:
    return ContractEvaluationDecision(
        relevance=ContractRelevance.IN_CONTRACT,
        reason_code="export_root_membership",
        assurance=ContractAssurance.COMPLETE,
    )


def _is_trusted_by_construction(change: Change) -> bool:
    """Whether *change* is definitive regardless of any surface evidence.

    Three families, each built only from an already-proven-in-contract
    entity, so no universe-membership or identity check can add information
    about them -- it can only take it away. See
    :func:`evaluate_change_contract_relevance`'s own call site for the full
    per-family rationale.
    """
    return (
        change.kind.value.startswith("python_")
        or change.kind.value in NEVER_FILTER_KIND_NAMES
        or change.kind.value in _PUBLIC_SOURCE_ABI_KIND_SLUGS
    )


def _trusted_by_construction_decision() -> ContractEvaluationDecision:
    return ContractEvaluationDecision(
        relevance=ContractRelevance.IN_CONTRACT,
        reason_code="public_root_membership",
        assurance=ContractAssurance.COMPLETE,
    )


#: The trusted-by-construction kinds whose construction rests on *this*
#: domain's own evidence -- the observed export table -- and which may
#: therefore skip the ``exports`` resolvability/identity gates.
#:
#: Deliberately not the whole of the `public` path's trusted set, which an
#: earlier revision reused wholesale and an existing test immediately caught:
#: those families are grounded in *header*/source/Python-axis evidence, which
#: says nothing about export membership. A `PUBLIC_MACRO_REMOVED` has no
#: linker symbol at all, and `constant_*`/`internal_*_leaks_via_public_api`
#: are derived from the public API surface, so under a contract defined as
#: "exported roots and their closure" they stay `UNKNOWN_UNRESOLVED` rather
#: than being asserted in-domain.
#:
#: `VISIBILITY_LEAK` qualifies because `diff_platform_elf_dynamic.
#: _diff_visibility_leak` builds it exclusively from `Visibility.ELF_ONLY`
#: declarations -- entries of the ELF symbol table itself. Another kind
#: belongs here only on the same footing: its producer must draw solely from
#: the export table.
_EXPORT_GROUNDED_KIND_NAMES: frozenset[str] = frozenset(
    {ChangeKind.VISIBILITY_LEAK.value}
)


def _exports_mode_decision(
    change: Change,
    exp_old: ExportSurface,
    exp_new: ExportSurface,
) -> ContractEvaluationDecision:
    """*change*'s relevance under ADR-049's ``exports`` contract domain.

    Plan Section 7's ``exports`` row, clause by clause: "its domain is only
    exported function/variable roots and closure computed from the raw type
    graph. Roots/closure are ``IN_CONTRACT``; an entity proven unreachable
    after complete root/graph traversal is ``PROVEN_OUT_OF_CONTRACT``;
    incomplete root/graph or identity evidence is ``UNKNOWN_UNRESOLVED``;
    non-entity findings are ``NOT_APPLICABLE``" (that last clause is already
    handled by the caller's mode-independent ``_NOT_APPLICABLE_KIND_SLUGS``
    short-circuit).

    Header-derived evidence is deliberately *not* consulted anywhere here --
    "Public-header/manifest/consumer failures are unrelated and advisory" for
    this domain -- which is why the caller dispatches here *before* its own
    ``_ALL_SURFACE_REASONS`` fast path. A private-header type reached from a
    real export is in this contract; a public-header declaration that is not
    exported and not reached is not.

    **Neither declared-public overlay can widen this domain** (Codex review).
    ``force_public_symbols`` (``--public-symbol``) and
    ``public_surface_allowlist`` (``--post-manifest``) both assert something
    about the *declared-public* surface; neither observes an export, and a
    user assertion cannot make an unexported declaration exported. Honoring
    them here -- as the ``public`` path rightly does, since there they *are*
    the domain's own evidence -- would report an unexported declaration
    ``IN_CONTRACT`` under a contract defined as "only exported roots and
    their closure", and could gate on it.

    The POST manifest's *exclusion* half does not apply here either, which is
    why the caller dispatches this mode ahead of that shortcut too: Section
    7's ``exports`` row is unconditional both ways -- "Roots/closure are
    ``IN_CONTRACT`` ... Public-header/manifest/consumer failures are unrelated
    and advisory" -- so a symbol the binary demonstrably exports stays in this
    contract even when a manifest omits it.

    Never claims ``PROVEN_OUT_OF_CONTRACT`` without both an observed export
    table *and* an entity this snapshot actually knows about: an entity
    absent from the snapshot's own universe (a macro, a Python-API-axis
    finding, an inline function the dumper never recorded as a declaration)
    cannot be placed in the export domain at all, which is "incomplete
    root/graph evidence", not proof of exclusion.
    """
    # Checked before this domain's own resolvability and identity gates,
    # exactly as the `public` path checks its own trusted-by-construction
    # families before its equivalents (Codex review, confirmed empirically):
    # a `VISIBILITY_LEAK` carries the producer's synthetic
    # `symbol="<visibility>"`, which resolves to `finding_identity`'s reduced
    # tier, so the identity gate below stamped every one of them
    # `UNKNOWN_UNRESOLVED`.
    if change.kind.value in _EXPORT_GROUNDED_KIND_NAMES:
        return _trusted_by_construction_decision()

    auth = _authoritative_export_surface(change, exp_old, exp_new)
    if not auth.resolvable:
        return _unresolved_decision(
            "required_evidence_incomplete", ContractAssurance.UNAVAILABLE
        )

    identity = resolve_change_identity(change)
    if identity.tier == IDENTITY_TIER_REDUCED:
        return _unresolved_decision("identity_ambiguous", ContractAssurance.PARTIAL)

    if _symbol_matches(change, auth.export_symbols, allow_tail_fallback=False):
        return _export_root_decision()
    # An observed export no declaration accounted for is still an exported
    # root -- this domain is defined by the export table, not by which
    # entries the headers happen to explain. Without this, removing an
    # undeclared export (an accidental one a consumer reaches via `dlsym`)
    # was `UNKNOWN_UNRESOLVED` in the very domain whose evidence observed it.
    if (change.symbol or "") in auth.unmatched_exports:
        return _export_root_decision()

    # Closure membership answers a *type-level* question only. A symbol-level
    # finding (`FUNC_RETURN_CHANGED` on an unexported helper, say) often
    # carries a `caused_by_type` that some *other* exported signature does
    # reach -- classifying the helper itself `IN_CONTRACT` on that basis would
    # gate an internal function merely for using a public type (Codex review).
    # Its own membership is decided by its own linker symbol being a root,
    # which the check above already answered.
    type_scoped = (
        change.kind.value in TYPE_LEVEL_KIND_NAMES
        or change.kind.value in MEMBER_LEVEL_TYPE_KIND_NAMES
    )
    candidates = _type_candidates(change) if type_scoped else set()
    if _confirmed_type_matches(
        candidates, auth.export_types, auth.ambiguous_type_names
    ):
        return _export_root_decision()

    # A candidate that matched only ambiguously proves nothing in either
    # direction -- which of the colliding records the finding is really about
    # decides its membership, and that is exactly what is unknown. Crucially
    # this must not fall through to the proven-out branch below merely
    # because no *unambiguous* match confirmed. The reason code is
    # deliberately `identity_ambiguous` (the registry's own "identity
    # coverage for the affected entity is incomplete, so root/closure
    # membership cannot be decided") rather than the coarser
    # `required_evidence_incomplete` the `public` path reports for the same
    # shape: the difference is the reason string only -- both paths agree on
    # the relevance and assurance -- and this domain has the more precise one
    # available because it reaches the ambiguity decision directly instead of
    # through `classify_change_surface`'s own in-surface fallback.
    if candidates & auth.export_types:
        return _unresolved_decision("identity_ambiguous", ContractAssurance.PARTIAL)

    # Nothing is provable unless the observed export table was fully
    # accounted for: an export no declaration matched (a mangling-scheme gap,
    # or an entry point absent from the parsed headers) is a real entry point
    # whose own signature -- and therefore whose own type closure -- this
    # snapshot knows nothing about, so an absence from the root/closure sets
    # could simply mean the missing export is what reaches this entity
    # (`ExportSurface.exclusion_is_provable`; Codex review).
    if not auth.exclusion_is_provable:
        return _unresolved_decision(
            "required_evidence_incomplete", ContractAssurance.PARTIAL
        )

    known_types = candidates & auth.all_types
    # An untyped export root -- one absent from the parsed headers, or
    # recorded with the export-only `"?"` sentinel -- has an unknown closure
    # of its own that could contain this very type, so it must not permit an
    # exclusion either (ADR-024 D5.2's rule, applied to this domain; Codex
    # review). That used to be a second check right here; it now lives
    # inside `exclusion_is_provable` above, which is where the property's own
    # docstring always claimed every incompleteness signal was (CodeRabbit
    # review). Both paths returned the identical decision, so folding them
    # loses nothing and leaves one place to add the next signal to.
    # An unknown symbol (a macro name, a Python-API entity, a synthetic
    # per-batch sentinel) is unplaceable, not proven-excluded. `all_symbols`
    # is keyed by `surface._symbol_keys` exactly like the root set, so the
    # same membership rule applies -- hence `_symbol_matches` again rather
    # than a second copy of it (CodeRabbit review).
    if known_types or _symbol_matches(
        change, auth.all_symbols, allow_tail_fallback=False
    ):
        return ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )
    return _unresolved_decision(
        "required_evidence_incomplete", ContractAssurance.PARTIAL
    )


def _mode_dispatch_decision(
    change: Change,
    mode: ContractMode,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    exports_old: ExportSurface | None,
    exports_new: ExportSurface | None,
) -> ContractEvaluationDecision | None:
    """The decisions settled by the finding's kind and the selected contract
    domain alone, before any header-surface evidence is consulted. ``None``
    when none applies and the PUBLIC path continues.
    """
    if change.kind.value in _NOT_APPLICABLE_KIND_SLUGS:
        return _not_applicable_decision()
    # A cross-source hygiene finding (`exported_not_public`,
    # `private_header_leak`, ...; `workflows.cross_source_hygiene`) states
    # how one build's evidence sources agree, not that a contract entity
    # changed between OLD and NEW -- its evolution stamp is what says so.
    # Relevance is therefore not the question for it: `NOT_APPLICABLE` keeps
    # it scored by policy exactly as it was before contract evaluation became
    # the default (ADR-049 Phase 7), advisory where ADR-035 D1 says it is,
    # instead of letting an `UNKNOWN_UNRESOLVED` decision about a hygiene
    # observation raise the contract-coverage exit of every audit.
    if getattr(change, "cross_source_evolution", None) is not None:
        return _not_applicable_decision()

    # `--post-manifest`'s own exclusion reason is an explicit-evidence
    # exclusion (an exact, closed-domain manifest proved this concrete export
    # was not committed), not a header-origin classification the way the rest
    # of `_ALL_SURFACE_REASONS` is -- unlike private-header/system-header
    # provenance (which ALL mode deliberately treats as in-domain, since
    # that's the whole point of "no header-origin scoping"), a POST-manifest
    # demotion is authoritative for both remaining modes. (`EXPORTS` is
    # dispatched above, ahead of this: manifest evidence is advisory in that
    # domain -- see `_exports_mode_decision`.) Checked before the ALL-mode
    # shortcut below so a change `FilterNonPublicSurface._run_allowlist` already
    # demoted to `pp_ctx.out_of_surface` for this reason can't simultaneously
    # be stamped `IN_CONTRACT` by the shortcut, contradicting its own
    # presence in `surface_scope.out_of_surface_changes` (Codex review,
    # fresh evidence: `--post-manifest` + `--no-scope-public-headers`).
    if mode is ContractMode.EXPORTS:
        # Dispatched ahead of *every* header-domain shortcut, including the
        # POST-manifest exclusion below. ADR-049 plan Section 7's `exports`
        # row is unconditional -- "Roots/closure are `IN_CONTRACT` ...
        # Public-header/manifest/consumer failures are unrelated and
        # advisory" -- so a symbol the binary demonstrably exports stays in
        # this contract even when a manifest omits it (Codex review; an
        # earlier revision of this function had the manifest exclusion win,
        # on the reasoning that a committed-export manifest can only narrow
        # the export set, which the plan does not say).
        assert exports_old is not None and exports_new is not None  # gated above
        return _exports_mode_decision(change, exports_old, exports_new)

    if change.surface_exclusion_reason == _REASON_POST_MANIFEST_NOT_COMMITTED:
        return _decision_for_surface_reason(
            change.surface_exclusion_reason, change, surf_old, surf_new
        )

    if mode is ContractMode.ALL:
        return _all_mode_decision()
    return None


def _pipeline_authority_decision(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    force_public_symbols: frozenset[str] | None,
    public_surface_allowlist: frozenset[str] | None,
) -> ContractEvaluationDecision | None:
    """Decisions an earlier pipeline step already made authoritatively, which a
    from-scratch ``classify_change_surface`` recomputation here cannot see and
    could contradict. ``None`` when none applies.
    """
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
    # `NEVER_FILTER_KIND_NAMES` (a leak finding, or a `constant_*` finding)
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
    # unconditional-trust rule for `NEVER_FILTER_KIND_NAMES` ever got a
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
    # `NEVER_FILTER_KIND_NAMES`, for the identical reason: gating it on
    # C/C++ header surface resolvability or on this module's own
    # identity-ambiguity tiering would downgrade a definitive
    # `PUBLIC_MACRO_REMOVED`/`INLINE_FUNCTION_REMOVED`/etc. event purely
    # because of an unrelated evidence gap (Codex review, fresh evidence).
    if _is_trusted_by_construction(change):
        return _trusted_by_construction_decision()

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

    # `public_surface_allowlist` (the `--post-manifest` committed-export set)
    # is the identical "pipeline already made an unconditional, authoritative
    # decision this module cannot see" shape as `force_public_symbols` above
    # -- `FilterNonPublicSurface._run_allowlist` keeps a matching change in
    # `kept` *without* ever setting `Change.surface_exclusion_reason` (that
    # field is only set on the demotion branch), so the already-excluded-
    # by-pipeline fast path earlier in this function never fires for it
    # either, and a from-scratch `classify_change_surface` recomputation
    # below has no way to know the commitment exists -- reaching e.g.
    # `REASON_PRIVATE_HEADER` (terminal) for a symbol POST-manifest just
    # proved is a real, committed export, contradicting the pipeline's own
    # decision for that same finding (Codex review, fresh evidence). Matched
    # by *exact* membership, not `_change_matches_symbols`'s suffix-tolerant
    # rule -- `_run_allowlist` itself deliberately uses exact-name matching
    # only (its own comment: a suffix-tolerant match would let an
    # uncommitted namespaced helper masquerade as a committed bare name,
    # contradicting the manifest's exact-name contract), so this check must
    # mirror that, not the widening overlay's looser rule.
    if public_surface_allowlist and (change.symbol or "") in public_surface_allowlist:
        return ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )
    return None


def _surface_classification_decision(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    unions: SurfaceUnions | None,
    directly_referenced_stdlib_old: frozenset[str] | None,
    directly_referenced_stdlib_new: frozenset[str] | None,
) -> ContractEvaluationDecision:
    """The from-scratch header-surface classification, reached only once every
    earlier authority has declined to settle the finding and both the
    authoritative surface and the finding's identity have resolved.
    """
    if closed := closed_domain_decision(change, surf_old, surf_new):
        return ContractEvaluationDecision(*closed)
    if unions is None:
        unions = surface_unions(surf_old, surf_new)
    in_surface, reason = classify_change_surface(
        change, surf_old, surf_new, unions=unions
    )
    if in_surface:
        if _in_surface_result_is_confirmed(
            change,
            surf_old,
            surf_new,
            directly_referenced_stdlib_old=directly_referenced_stdlib_old,
            directly_referenced_stdlib_new=directly_referenced_stdlib_new,
        ):
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
    return _decision_for_surface_reason(reason or "", change, surf_old, surf_new)


def evaluate_change_contract_relevance(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    unions: SurfaceUnions | None = None,
    force_public_symbols: frozenset[str] | None = None,
    public_surface_allowlist: frozenset[str] | None = None,
    exports_old: ExportSurface | None = None,
    exports_new: ExportSurface | None = None,
    directly_referenced_stdlib_old: frozenset[str] | None = None,
    directly_referenced_stdlib_new: frozenset[str] | None = None,
) -> ContractEvaluationDecision:
    """Compute *change*'s shadow contract-relevance decision.

    ``mode`` selects the declared contract (all three ADR-049 D2 values are
    implemented). ``surf_old``/``surf_new`` are
    the same :class:`~abicheck.surface.PublicSurface` pair
    ``FilterNonPublicSurface`` already computes; pass a precomputed
    ``unions`` (:func:`~abicheck.surface.surface_unions`) when evaluating many
    changes against the same pair, mirroring
    :func:`~abicheck.surface.classify_change_surface`'s own guidance.
    ``exports_old``/``exports_new`` are the corresponding
    :class:`~abicheck.export_surface.ExportSurface` pair
    (:func:`~abicheck.export_surface.compute_export_surface`), required when
    and only when ``mode`` is ``EXPORTS`` -- that domain's roots are the
    binary's own export table, evidence a ``PublicSurface`` does not carry.
    Omitting them under ``EXPORTS`` is a caller error (``ValueError``), not a
    silent degradation to a header-derived answer for a domain that is not
    header-derived.
    ``directly_referenced_stdlib_old``/``directly_referenced_stdlib_new``
    are each side's
    :func:`~abicheck.type_reachability.directly_referenced_stdlib_type_spellings`
    -- see :func:`_in_surface_result_is_confirmed`'s docstring for why this
    is a second confirmation source, independent of ``public_types``. Omit
    them (the default) for a caller with no snapshot in scope; a `PUBLIC`
    evaluation degrades to exactly its pre-existing behaviour, never raising.
    ``force_public_symbols`` mirrors ``PipelineContext.force_public_symbols``
    (ADR-024 D6's widening overlay, ``FilterNonPublicSurface``'s
    ``_run_scope``/``_run_allowlist``) -- omit it (the default) when the
    caller has no such overlay configured for this run. ``public_surface_allowlist``
    mirrors ``PipelineContext.public_surface_allowlist`` (the ``--post-manifest``
    committed-export set consulted by ``FilterNonPublicSurface._run_allowlist``)
    -- omit it (the default) for a run with no POST manifest configured.

    Never raises for a *finding* it cannot confidently classify -- every such
    case degrades to ``UNKNOWN_UNRESOLVED`` (see the module docstring's
    ``UNKNOWN_UNPROVEN`` rule). It raises for an entirely invalid ``mode``
    value, and for ``EXPORTS`` without an ``ExportSurface`` pair (both
    ``ValueError``).
    """
    # `ContractMode` is a `str` Enum, so an untyped caller passing the bare
    # serialized value (e.g. `"all"` from a config/API adapter) would compare
    # equal to a member (equality/hash) but then silently fail the
    # `is ContractMode.ALL` identity check below, falling through to the
    # PUBLIC path for a caller that actually asked for ALL (Codex review).
    # Coercing through the enum constructor first (a no-op for an
    # already-real member) means every later `is` comparison is safe.
    mode = coerce_contract_mode(mode)
    if mode is ContractMode.EXPORTS and (exports_old is None or exports_new is None):
        raise ValueError(
            "contract mode 'exports' requires an ExportSurface pair "
            "(exports_old/exports_new, from "
            "abicheck.export_surface.compute_export_surface) -- its roots are "
            "the binary's own export table, which a PublicSurface does not carry"
        )

    mode_decision = _mode_dispatch_decision(
        change, mode, surf_old, surf_new, exports_old, exports_new
    )
    if mode_decision is not None:
        return mode_decision

    # mode is ContractMode.PUBLIC from here on.
    pipeline_decision = _pipeline_authority_decision(
        change,
        surf_old,
        surf_new,
        force_public_symbols=force_public_symbols,
        public_surface_allowlist=public_surface_allowlist,
    )
    if pipeline_decision is not None:
        return pipeline_decision

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

    return _surface_classification_decision(
        change,
        surf_old,
        surf_new,
        unions=unions,
        directly_referenced_stdlib_old=directly_referenced_stdlib_old,
        directly_referenced_stdlib_new=directly_referenced_stdlib_new,
    )


def evidence_refs_for_change(
    change: Change,
    decision: ContractEvaluationDecision,
    *,
    mode: ContractMode,
    block: ContractEvidenceBlock,
    force_public_symbols: frozenset[str] | None = None,
    public_surface_allowlist: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Which provider records *decision* actually rests on (Phase 3's gate).

    Mostly a thin wrapper over
    :func:`~abicheck.contract_evidence_collect.evidence_refs_for_reason`,
    which maps a reason code plus the selected domain onto the domain's own
    provider. The wrapping exists for the two cases a reason code alone
    cannot distinguish, and it lives *here* rather than in the collector so
    the attribution uses this module's own matching rules instead of a
    second copy of them:

    - an ``IN_CONTRACT`` decision this function's own overlay short-circuits
      produced (``--public-symbol``'s widening overlay, or a
      ``--post-manifest`` commitment) carries reason
      ``public_root_membership``, identical to a genuine header-derived
      root membership -- but it rests on the *overlay*, which the evaluator
      consulted before ever looking at the surface. Citing the header
      provider for it would be a false citation in an audit ledger whose
      entire purpose is evidence honesty;
    - a ``--post-manifest``-driven *exclusion* carries
      ``terminal_authoritative_exclusion``, shared with header-origin
      terminal exclusions, and rests on the manifest for the same reason.

    Matching mirrors each overlay's own rule exactly: suffix-tolerant for
    the widening overlay (``_change_matches_symbols``, what
    ``FilterNonPublicSurface._run_scope`` uses) and exact-name for the
    manifest (what ``_run_allowlist`` uses -- a suffix-tolerant match there
    would let an uncommitted namespaced helper masquerade as a committed
    bare name). ``exports`` never consults either overlay (plan Section 7:
    manifest/consumer evidence is unrelated and advisory in that domain), so
    the attribution is skipped for it entirely.
    """
    from .contract_evidence_collect import (
        PROVIDER_FORCED_PUBLIC,
        PROVIDER_POST_MANIFEST,
        evidence_record_id,
        evidence_refs_for_reason,
    )

    side = authoritative_side(change)
    if coerce_contract_mode(mode) is not ContractMode.EXPORTS:
        available = {(e.record.provider, e.record.side) for e in block.providers}
        overlay: str | None = None
        if (
            decision.relevance is ContractRelevance.IN_CONTRACT
            and force_public_symbols
            and _change_matches_symbols(change, force_public_symbols)
        ):
            overlay = PROVIDER_FORCED_PUBLIC
        # `is not None`, not truthiness: a manifest that validly commits to
        # zero exports is a *selected* source that scopes everything out, and
        # it is exactly what a resulting exclusion rests on. Skipping the
        # attribution for it cited `public_header` for a decision the empty
        # manifest alone produced -- the same absent-vs-empty distinction
        # `collect_contract_evidence` already draws (Codex review, fresh
        # evidence). The `IN_CONTRACT` half below is unaffected either way:
        # nothing is a member of an empty set.
        elif public_surface_allowlist is not None and (
            (
                decision.relevance is ContractRelevance.IN_CONTRACT
                and (change.symbol or "") in public_surface_allowlist
            )
            or change.surface_exclusion_reason == _REASON_POST_MANIFEST_NOT_COMMITTED
        ):
            overlay = PROVIDER_POST_MANIFEST
        if overlay is not None and (overlay, side) in available:
            return (evidence_record_id(overlay, side),)
    return evidence_refs_for_reason(
        decision.reason_code, mode=mode, block=block, authoritative_side=side
    )


def evaluate_snapshot_pair_contract_relevance(
    changes: list[Change],
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    *,
    mode: ContractMode = ContractMode.PUBLIC,
    force_public_symbols: frozenset[str] | None = None,
    public_surface_allowlist: frozenset[str] | None = None,
    exports_old: ExportSurface | None = None,
    exports_new: ExportSurface | None = None,
    directly_referenced_stdlib_old: frozenset[str] | None = None,
    directly_referenced_stdlib_new: frozenset[str] | None = None,
) -> list[ContractEvaluationDecision]:
    """:func:`evaluate_change_contract_relevance` for a whole comparison's
    findings, computing the surface unions once (see that function's
    ``unions`` parameter) rather than once per finding. ``force_public_symbols``/
    ``public_surface_allowlist``/``exports_old``/``exports_new``/
    ``directly_referenced_stdlib_old``/``directly_referenced_stdlib_new`` are
    passed through to every call -- see that function's own parameters."""
    unions = surface_unions(surf_old, surf_new)
    return [
        evaluate_change_contract_relevance(
            change,
            surf_old,
            surf_new,
            mode=mode,
            unions=unions,
            force_public_symbols=force_public_symbols,
            public_surface_allowlist=public_surface_allowlist,
            exports_old=exports_old,
            exports_new=exports_new,
            directly_referenced_stdlib_old=directly_referenced_stdlib_old,
            directly_referenced_stdlib_new=directly_referenced_stdlib_new,
        )
        for change in changes
    ]
