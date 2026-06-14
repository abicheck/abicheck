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

"""Post-processing pipeline for ABI change lists.

Each step is independently testable, reorderable, and self-documenting.
The pipeline transforms the raw detector output into the final change list
through filtering, deduplication, enrichment, and suppression.

Architecture review: Problem C — explicit pipeline replaces imperative chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot
    from .suppression import SuppressionList
    from .surface import PublicSurface


@dataclass
class PipelineContext:
    """Shared state passed through the pipeline."""

    old: AbiSnapshot
    new: AbiSnapshot
    suppression: SuppressionList | None = None
    # Glob patterns identifying contractually frozen namespaces (e.g.
    # ``**::detail::r1``). Threaded in from PolicyFile.frozen_namespaces.
    # Consumed by EscalateFrozenNamespaceViolations to tag matching
    # findings with Change.frozen_namespace_violation.
    frozen_namespaces: list[str] = field(default_factory=list)
    # ADR-024 §D4: when True, FilterNonPublicSurface moves findings that are
    # not on the public-header-scoped ABI surface to ``out_of_surface``.
    scope_to_public_surface: bool = False
    # G15 (opt-in): when True, DetectVersionedSymbolScheme reclassifies the
    # version-rename pairs (ICU `u_*_NN`) as compatible so the verdict reflects
    # the real delta instead of the rename churn. Off by default (authority rule).
    collapse_versioned_symbols: bool = False
    # ADR-024 §D6 widening overlay: symbol names (mangled or demangled) the
    # user *guarantees* are public even when header provenance can't see them
    # (asm stubs, .def exports, extern "C" shims, MSVC-mangling gaps). Matching
    # findings are forced to stay in-surface under scoping. Widening only ever
    # *keeps* a finding, so it cannot hide a break.
    force_public_symbols: set[str] = field(default_factory=set)
    # Set True when scoping was requested but the public surface could not be
    # resolved, so the step fell back to the full export table (keeps every
    # finding). Consumers surface this as "manual review required" — scoping
    # must never silently read as confident compatibility (issue #235).
    scope_fell_back: bool = False
    # Public surfaces computed by FilterNonPublicSurface, cached here so the
    # caller can reuse them (e.g. surface_scope_confidence) instead of repeating
    # the type-closure walk. None when scoping was not run.
    surf_old: PublicSurface | None = None
    surf_new: PublicSurface | None = None
    # Accumulated side-outputs
    opaque_filtered: list[Change] = field(default_factory=list)
    suppressed: list[Change] = field(default_factory=list)
    redundant: list[Change] = field(default_factory=list)
    kept: list[Change] = field(default_factory=list)
    # ADR-024: findings filtered out as not-public (full audit trail).
    out_of_surface: list[Change] = field(default_factory=list)
    # Set when collapsed version-rename churn was paired with an observed
    # SONAME change. The late SONAME policy should not call that bump
    # unnecessary after this step has moved the matched removals out of kept.
    versioned_scheme_soname_relink_required: bool = False


class PipelineStep(Protocol):
    """Protocol for a single post-processing step."""

    name: str

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Transform the change list, returning the updated list."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_index(snap: AbiSnapshot) -> bool:
    """Index ``snap`` for lookups, tolerating partial snapshots.

    Returns ``True`` when the snapshot indexed cleanly and is safe to read
    from, ``False`` otherwise. Keeping the swallowed exception out of a
    ``try/except/continue`` loop body avoids a silently-ignored-error pattern.
    """
    try:
        snap.index()
    except Exception:  # noqa: BLE001 — defensive; snapshots may be partial
        return False
    return True


def _matches_suppression_key(symbol: str, key: str) -> bool:
    """Return ``True`` iff *symbol* is suppressed by *key*.

    Used by :class:`DetectCppPatterns` to match per-symbol
    ``Change.symbol`` strings against the suppression set built by the
    grouped SYCL / ISA detectors.

    Match rule:

    * Always honour exact equality.
    * Allow substring match (``key in symbol``) only when the key is
      *structured enough* to be unambiguous — contains a namespace
      separator (``::``), an underscore (``_``), or is at least 12
      characters long. This guards against false suppressions where a
      short leaf name like ``compute`` would otherwise hit unrelated
      symbols (``precompute``, ``Recompute_xyz``).

    The substring fallback exists because ``Change.symbol`` can be a
    *different* mangled encoding from ``fn.mangled``: on Linux the
    castxml-derived Itanium mangled name; on Windows the PE export-
    table name (MSVC mangling). The demangled function name (e.g.
    ``kmeans_compute_avx512``) is a substring of both encodings.
    """
    if not key:
        return False
    if symbol == key:
        return True
    if len(key) < 12 and "::" not in key and "_" not in key:
        return False
    return key in symbol


# ---------------------------------------------------------------------------
# Concrete pipeline steps
# ---------------------------------------------------------------------------


class FilterReservedFieldRenames:
    """Suppress TYPE_FIELD_REMOVED false positives from reserved-field renames."""

    name = "filter_reserved_field_renames"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_reserved_field_renames

        return _filter_reserved_field_renames(changes)


class FilterOpaqueSizeChanges:
    """Suppress size-only growth for opaque pointer-handle types."""

    name = "filter_opaque_size_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_opaque_size_changes

        changes, filtered = _filter_opaque_size_changes(changes, ctx.old, ctx.new)
        ctx.opaque_filtered.extend(filtered)
        return changes


class DowngradeOpaqueStructChanges:
    """Downgrade changes for types opaque in both snapshots."""

    name = "downgrade_opaque_struct_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _downgrade_opaque_struct_changes

        return _downgrade_opaque_struct_changes(changes, ctx.old, ctx.new)


class DeduplicateAstDwarf:
    """Collapse AST/DWARF duplicate findings."""

    name = "deduplicate_ast_dwarf"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _deduplicate_ast_dwarf

        return _deduplicate_ast_dwarf(changes)


class DeduplicateCrossDetector:
    """Collapse overlapping reports from different detectors."""

    name = "deduplicate_cross_detector"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _deduplicate_cross_detector

        return _deduplicate_cross_detector(changes)


class DowngradeOpaqueTypeChanges:
    """Suppress structural changes for opaque types."""

    name = "downgrade_opaque_type_changes"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _downgrade_opaque_type_changes

        return _downgrade_opaque_type_changes(changes, ctx.old, ctx.new)


class EnrichSourceLocations:
    """Add source location metadata for suppression matching."""

    name = "enrich_source_locations"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _enrich_source_locations

        _enrich_source_locations(changes, ctx.old, ctx.new)
        return changes


def _change_matches_symbols(change: Change, symbols: set[str]) -> bool:
    """True if *change*'s symbol matches the widening allowlist.

    Matches the raw symbol (mangled or demangled, as recorded on the change)
    and — for qualified names — the trailing ``::`` segment, so an entry like
    ``foo`` matches ``ns::foo`` as well as the exact spelling.
    """
    sym = change.symbol or ""
    if not sym:
        return False
    if sym in symbols:
        return True
    return "::" in sym and sym.rsplit("::", 1)[1] in symbols


class FilterNonPublicSurface:
    """Move findings outside the public-header surface to an audit ledger.

    Opt-in (``ctx.scope_to_public_surface``). Mirrors what libabigail
    ``--headers-dir`` / abi-compliance-checker do: a change to a symbol or
    type that is not part of the public-header-scoped ABI surface is not a
    public-compatibility break. Per ADR-024 §D4/D5 these findings are
    *recorded* (``ctx.out_of_surface``), never silently dropped, and
    internal-leak findings are exempt.
    """

    name = "filter_non_public_surface"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.scope_to_public_surface:
            return changes
        from .surface import (
            classify_change_surface,
            compute_public_surface,
            surface_unions,
        )

        surf_old = compute_public_surface(ctx.old)
        surf_new = compute_public_surface(ctx.new)
        # Cache for reuse (surface_scope_confidence) — avoids a second walk.
        ctx.surf_old = surf_old
        ctx.surf_new = surf_new
        if not (surf_old.resolvable or surf_new.resolvable):
            # No header-derived surface to scope against — keep everything and
            # record the fallback so the verdict is not mistaken for a
            # confidently-clean public surface (issue #235).
            ctx.scope_fell_back = True
            return changes
        force_public = ctx.force_public_symbols
        # Compute the old∪new surface universes once for the whole pass; doing
        # this per change is O(findings × surface) and makes large comparisons
        # quadratic.
        unions = surface_unions(surf_old, surf_new)
        kept: list[Change] = []
        for c in changes:
            # Widening overlay (ADR-024 §D6): a user-guaranteed public symbol
            # stays in-surface regardless of provenance/export classification.
            if force_public and _change_matches_symbols(c, force_public):
                kept.append(c)
                continue
            in_surface, reason = classify_change_surface(
                c, surf_old, surf_new, unions=unions
            )
            if in_surface:
                kept.append(c)
            else:
                # Tag with the ledger reason (ADR-024 §D5.1) before demoting.
                c.surface_exclusion_reason = reason
                ctx.out_of_surface.append(c)
        return kept


class ApplySuppression:
    """Apply user-provided suppression rules."""

    name = "apply_suppression"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None:
            return changes
        filtered: list[Change] = []
        for c in changes:
            if ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
            else:
                filtered.append(c)
        return filtered


class SuppressRenamedPairs:
    """Suppress FUNC_REMOVED + FUNC_ADDED pairs when a FUNC_LIKELY_RENAMED exists.

    When the fingerprint rename detector identifies a rename (old_name → new_name),
    the corresponding FUNC_REMOVED(old_name) and FUNC_ADDED(new_name) are redundant
    noise.  This step moves them to ctx.redundant and annotates the rename change
    with caused_count.
    """

    name = "suppress_renamed_pairs"

    @staticmethod
    def _build_rename_maps(
        changes: list[Change],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, Change]]:
        """Return (renamed_old, renamed_new, rename_changes) from FUNC_LIKELY_RENAMED entries."""
        from .checker_policy import ChangeKind

        renamed_old: dict[str, str] = {}  # old_value → new_value
        renamed_new: dict[str, str] = {}  # new_value → old_value
        rename_changes: dict[str, Change] = {}  # old_value → the rename Change
        for c in changes:
            if c.kind == ChangeKind.FUNC_LIKELY_RENAMED and c.old_value and c.new_value:
                renamed_old[c.old_value] = c.new_value
                renamed_new[c.new_value] = c.old_value
                rename_changes[c.old_value] = c
        return renamed_old, renamed_new, rename_changes

    @staticmethod
    def _try_suppress_removed(
        c: Change,
        renamed_old: dict[str, str],
        rename_changes: dict[str, Change],
        ctx: PipelineContext,
    ) -> bool:
        """Suppress a FUNC_REMOVED/FUNC_REMOVED_ELF_ONLY change if it belongs to a rename pair.

        Returns True when the change was suppressed (caller should skip appending it).
        """
        old_name = c.old_value or c.symbol
        if old_name not in renamed_old:
            return False
        c.caused_by_type = f"rename:{old_name}→{renamed_old[old_name]}"
        ctx.redundant.append(c)
        rc = rename_changes.get(old_name)
        if rc is not None:
            rc.caused_count += 1
        return True

    @staticmethod
    def _try_suppress_added(
        c: Change,
        renamed_new: dict[str, str],
        rename_changes: dict[str, Change],
        ctx: PipelineContext,
    ) -> bool:
        """Suppress a FUNC_ADDED change if it belongs to a rename pair.

        Returns True when the change was suppressed (caller should skip appending it).
        """
        new_name = c.new_value or c.symbol
        if new_name not in renamed_new:
            return False
        old_name = renamed_new[new_name]
        c.caused_by_type = f"rename:{old_name}→{new_name}"
        ctx.redundant.append(c)
        rc = rename_changes.get(old_name)
        if rc is not None:
            rc.caused_count += 1
        return True

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind

        renamed_old, renamed_new, rename_changes = self._build_rename_maps(changes)
        if not renamed_old:
            return changes

        removed_kinds = (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY)
        kept: list[Change] = []
        for c in changes:
            if c.kind in removed_kinds:
                if self._try_suppress_removed(c, renamed_old, rename_changes, ctx):
                    continue
            elif c.kind == ChangeKind.FUNC_ADDED:
                if self._try_suppress_added(c, renamed_new, rename_changes, ctx):
                    continue
            kept.append(c)
        return kept


class FilterRedundant:
    """Split changes into kept + redundant (derived from root type changes)."""

    name = "filter_redundant"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _filter_redundant

        kept, redundant = _filter_redundant(changes)
        ctx.redundant.extend(redundant)
        # opaque_filtered are kept separate - they are compatible changes that should not affect verdict
        ctx.kept = kept
        return kept


class EnrichAffectedSymbols:
    """For type changes, find functions that use the affected type."""

    name = "enrich_affected_symbols"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _enrich_affected_symbols

        _enrich_affected_symbols(changes, ctx.old)
        return changes


class AttributeStdlibEmbedding:
    """Attribute an unattributed owner size/offset change to an embedded ``std::``
    member by value (the layout-closure case the redundancy filter can't link)."""

    name = "attribute_stdlib_embedding"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_filtering import _attribute_stdlib_embedding

        _attribute_stdlib_embedding(changes, ctx.new)
        return changes


class DetectCppPatterns:
    """Run the C++ library-family detectors added in PR #239 (case77–case89).

    Each individual detector lives in :mod:`abicheck.diff_cpp_patterns`;
    this pipeline step wires them together, dedupes findings against the
    existing change list, and respects user suppression.

    Detectors run:

    * ``detect_serialization_tag_changes``
    * ``detect_missing_instantiations``
    * ``detect_sycl_overload_set_removal`` (also suppresses redundant
      per-symbol ``func_removed`` children)
    * ``detect_cpu_dispatch_isa_dropped`` (likewise)
    * ``detect_tag_type_renamed``
    * ``detect_default_template_arg_changed``
    * ``detect_inline_body_renamed_member``
    """

    name = "detect_cpp_patterns"

    @staticmethod
    def _run_all_detectors(
        ctx: PipelineContext,
        changes: list[Change],
    ) -> tuple[list[Change], set[str]]:
        """Invoke every sub-detector and return ``(new_findings, suppressed_keys)``.

        ``suppressed_keys`` is the union of the per-symbol keys emitted by the
        SYCL and ISA grouped detectors; these identify ``FUNC_REMOVED`` children
        that must be moved to ``ctx.suppressed`` so they don't inflate the verdict.
        """
        from .diff_cpp_patterns import (
            detect_cpu_dispatch_isa_dropped,
            detect_default_template_arg_changed,
            detect_inline_body_renamed_member,
            detect_sycl_overload_set_removal,
            detect_tag_type_renamed,
        )
        from .diff_serialization import detect_serialization_tag_changes
        from .diff_templates import detect_missing_instantiations

        new_findings: list[Change] = []
        new_findings.extend(detect_serialization_tag_changes(ctx.old, ctx.new))
        new_findings.extend(detect_missing_instantiations(ctx.old, ctx.new))

        sycl_findings, sycl_suppressed = detect_sycl_overload_set_removal(ctx.old, ctx.new)
        new_findings.extend(sycl_findings)

        isa_findings, isa_suppressed = detect_cpu_dispatch_isa_dropped(ctx.old, ctx.new)
        new_findings.extend(isa_findings)

        new_findings.extend(detect_tag_type_renamed(ctx.old, ctx.new))
        new_findings.extend(detect_default_template_arg_changed(ctx.old, ctx.new))
        new_findings.extend(detect_inline_body_renamed_member(ctx.old, ctx.new, changes))

        return new_findings, sycl_suppressed | isa_suppressed

    @staticmethod
    def _suppress_grouped_children(
        changes: list[Change],
        suppressed_keys: set[str],
        ctx: PipelineContext,
    ) -> None:
        """Remove FUNC_REMOVED children subsumed by a grouped SYCL/ISA finding.

        Mutates ``changes`` in place (via slice assignment) and appends the
        removed entries to ``ctx.suppressed``.

        Two reasons to use ``ctx.suppressed`` (not ``ctx.redundant``):
        (a) ``compare()`` computes verdict on ``kept + redundant`` —
            redundant items still drive the verdict. Putting the
            children there would let per-symbol BREAKING outrank the
            grouped RISK finding. ``ctx.suppressed`` is excluded from
            verdict computation, which is what we want for children
            subsumed by a grouped finding.
        (b) ``FilterRedundant`` (earlier in the pipeline) sets
            ``ctx.kept = changes`` — that's a *reference* to this same
            list. If we rebind ``changes`` to a new filtered list,
            ``ctx.kept`` still points at the old one and our
            suppression is silently lost. Mutate in place instead.

        Matching uses BOTH exact equality and a guarded substring containment
        (see ``_matches_suppression_key`` for the unambiguity rules).
        """
        from .checker_policy import ChangeKind

        to_keep: list[Change] = []
        for ch in changes:
            if ch.kind == ChangeKind.FUNC_REMOVED and any(
                _matches_suppression_key(ch.symbol, key) for key in suppressed_keys
            ):
                ctx.suppressed.append(ch)
                continue
            to_keep.append(ch)
        changes[:] = to_keep

    @staticmethod
    def _merge_new_findings(
        changes: list[Change],
        new_findings: list[Change],
        ctx: PipelineContext,
    ) -> None:
        """Append deduplicated ``new_findings`` to ``changes``, respecting suppression."""
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        new_findings, suppressed_keys = self._run_all_detectors(ctx, changes)

        if suppressed_keys:
            self._suppress_grouped_children(changes, suppressed_keys, ctx)

        if new_findings:
            self._merge_new_findings(changes, new_findings, ctx)

        return changes


class DetectTemplatePatterns:
    """Run the generic template / overload-set pattern detectors.

    Lives in :mod:`abicheck.diff_templates`. Covers internal-template
    leaks (function-template analogue of PR #238), CPO kind flips,
    overload-set rerouting, mandatory-template-param additions, and
    unspecified-return flips.
    """

    name = "detect_template_patterns"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_templates import detect_template_patterns

        new_findings = detect_template_patterns(ctx.old, ctx.new)
        if not new_findings:
            return changes
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)
        return changes


class DetectNamespacePatterns:
    """Run the generic namespace-shape detectors.

    These cover header-only / template-library failure modes that aren't
    bound to any one library: experimental graduations, silent removals
    from experimental namespaces, and ``using std::X;`` re-export drops.
    Lives in :mod:`abicheck.diff_namespaces`.
    """

    name = "detect_namespace_patterns"

    def __init__(
        self,
        experimental_namespaces: tuple[str, ...] | None = None,
    ) -> None:
        self._experimental_namespaces = experimental_namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_namespaces import (
            DEFAULT_EXPERIMENTAL_NAMESPACES,
            detect_namespace_patterns,
        )

        namespaces = self._experimental_namespaces or DEFAULT_EXPERIMENTAL_NAMESPACES
        new_findings = detect_namespace_patterns(
            ctx.old,
            ctx.new,
            experimental_namespaces=namespaces,
        )
        if not new_findings:
            return changes
        seen_keys = {(c.kind, c.symbol) for c in changes}
        for c in new_findings:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            key = (c.kind, c.symbol)
            if key in seen_keys:
                continue
            changes.append(c)
            seen_keys.add(key)
        return changes


class DetectInternalLeaks:
    """Detect internal-namespace (``detail::``, ``impl::``, …) types whose
    changes leak through the public ABI surface.

    Runs after dedup / redundancy filtering so the trigger set only
    contains semantically distinct findings. Emitted leak entries are
    added to the change list and become part of the verdict computation.
    """

    name = "detect_internal_leaks"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .internal_leak import (
            DEFAULT_INTERNAL_NAMESPACES,
            detect_internal_leaks,
        )

        namespaces = self._namespaces or DEFAULT_INTERNAL_NAMESPACES
        extra = detect_internal_leaks(changes, ctx.old, ctx.new, namespaces)
        if not extra:
            return changes
        # Avoid duplicates if the pipeline is re-run.
        seen_symbols = {(c.kind, c.symbol) for c in changes}
        # Synthetic leak findings must respect user suppression rules
        # too. ``ApplySuppression`` ran earlier in the pipeline, so we
        # apply the same predicate by hand here rather than re-running
        # the whole step.
        for c in extra:
            if ctx.suppression is not None and ctx.suppression.is_suppressed(c):
                ctx.suppressed.append(c)
                continue
            if (c.kind, c.symbol) not in seen_symbols:
                changes.append(c)
                seen_symbols.add((c.kind, c.symbol))
        return changes


class DemoteUnreachableInternalChurn:
    """Demote internal-namespace layout churn that is unreachable from the public API.

    The surface-scoping anti-hiding rule (``surface.classify_change_surface``)
    deliberately keeps every internal-namespace (``detail::``, ``impl::``,
    ``internal::``) type-level finding in-surface so :class:`DetectInternalLeaks`
    — which runs just before this step and seeds from a broader root set — can
    decide whether the type actually leaks through the public ABI.

    When that detector finds NO leak path for an internal type (no
    ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` finding for it), the raw layout churn
    on that type is truly private: it cannot be observed by any public consumer,
    so it must not drive a hard binary ABI verdict. This is the oneTBB case
    (ISSUE-15): ``tbb::detail::*`` / ``rml::internal::*`` DWARF-only churn with
    no exported-symbol impact, which libabigail also reports as ABI-clean.

    The demoted findings are recorded in ``ctx.out_of_surface`` (ADR-024 §D4/D5,
    audit ledger) — never silently dropped — and a genuine leak is still
    surfaced through the separate ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` finding,
    so this can only ever remove confirmed-private noise.
    """

    name = "demote_unreachable_internal_churn"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        import fnmatch

        from .checker_policy import ChangeKind
        from .internal_leak import (
            _LEAK_TRIGGERING_KINDS,
            DEFAULT_INTERNAL_NAMESPACES,
            _root_type_name_for_change,
            _strip_template_args,
            is_internal_type,
        )
        from .surface import REASON_PRIVATE_INTERNAL_UNREACHABLE

        namespaces = self._namespaces or DEFAULT_INTERNAL_NAMESPACES
        frozen = list(ctx.frozen_namespaces)

        def _is_frozen(type_name: str) -> bool:
            # A contractually frozen namespace (PolicyFile.frozen_namespaces) is
            # an explicit user declaration that changes there must NOT be
            # downgraded. Keep such a finding in-surface so the later
            # EscalateFrozenNamespaceViolations step can tag it and the verdict
            # honours the contract, even when it is otherwise unreachable.
            if not frozen:
                return False
            cand = _strip_template_args(type_name)
            while True:
                if any(fnmatch.fnmatchcase(cand, pat) for pat in frozen):
                    return True
                if "::" not in cand:
                    return False
                cand = cand.rsplit("::", 1)[0]

        # Internal types the leak detector confirmed DO leak through public API.
        leaked_types = {
            c.symbol
            for c in changes
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        }
        kept: list[Change] = []
        for c in changes:
            root = _root_type_name_for_change(c)
            if (
                c.kind in _LEAK_TRIGGERING_KINDS
                and is_internal_type(root, namespaces)
                and root not in leaked_types
                and not _is_frozen(root)
            ):
                c.surface_exclusion_reason = REASON_PRIVATE_INTERNAL_UNREACHABLE
                ctx.out_of_surface.append(c)
                continue
            kept.append(c)
        # Mutate in place: ``ctx.kept`` aliases this list (set by FilterRedundant
        # and appended to by DetectInternalLeaks), so rebinding would lose the
        # demotion. See DetectCppPatterns for the same in-place contract.
        changes[:] = kept
        return changes


def _scheme_soname(snap: AbiSnapshot) -> str:
    """The *observed* ELF ``DT_SONAME`` for the versioned-scheme cross-check.

    Only an actual recorded SONAME is used — never the snapshot's ``library``
    name, which for source-only or hand-authored snapshots is just the input name
    and may differ from the runtime SONAME. Inferring a SONAME bump from a name
    change would overstate the relink requirement (the report's main visible
    finding under collapse), so absent ELF metadata yields "" and no relink note.
    """
    elf = getattr(snap, "elf", None)
    return (getattr(elf, "soname", "") or "").strip()


class DetectVersionedSymbolScheme:
    """Emit one advisory ``versioned_symbol_scheme_detected`` finding when most
    removed symbols reappear as added symbols differing only by a version token
    (field-eval P08: ICU ``u_*_75`` → ``u_*_78``). Additive by default — it
    explains the churn, the individual func_removed/func_added findings and their
    verdict are untouched.

    When ``ctx.collapse_versioned_symbols`` is set (opt-in, G15 second half), the
    matched version-rename pairs are additionally **reclassified as compatible**:
    moved to ``ctx.suppressed`` and dropped from the kept set, so the verdict
    reflects the real delta instead of the rename churn. This is deliberately
    behind a flag (authority rule: it downgrades artifact-level removals); a real
    SONAME bump or non-versioned removals still drive their own verdict."""

    name = "detect_versioned_symbol_scheme"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .checker_policy import ChangeKind
        from .versioned_symbol_scheme import analyze_versioned_scheme

        if any(c.kind is ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED for c in changes):
            return changes  # idempotent if the pipeline is re-run
        advisory, matched = analyze_versioned_scheme(changes)
        if advisory is None:
            return changes
        # G15: cross-check the version token against the SONAME. A versioned
        # scheme normally bumps the SONAME too (libicui18n.so.75 -> .78); the
        # rename churn is cosmetic, but a new SONAME still means dependents must
        # **relink** against the new shared object. Surface that relink signal on
        # the advisory so the collapse never hides it.
        old_so, new_so = _scheme_soname(ctx.old), _scheme_soname(ctx.new)
        if old_so and new_so and old_so != new_so:
            ctx.versioned_scheme_soname_relink_required = True
            advisory.description += (
                f" The SONAME also changed ({old_so} -> {new_so}): a new shared-object "
                "version, so dependents must relink against the new library even though "
                "the symbol churn is a version-rename."
            )
        if ctx.suppression is not None and ctx.suppression.is_suppressed(advisory):
            ctx.suppressed.append(advisory)
        else:
            changes.append(advisory)
        if ctx.collapse_versioned_symbols and matched:
            # G15: report the collapse count in the summary. caused_count is the
            # number of old-side version-rename pairs reclassified as compatible;
            # the reporter renders it ("N version-renames collapsed").
            old_side_kinds = (
                ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY,
                ChangeKind.VAR_REMOVED, ChangeKind.FUNC_LIKELY_RENAMED,
            )
            advisory.caused_count = sum(1 for c in matched if c.kind in old_side_kinds)
            advisory.description += (
                f" [{advisory.caused_count} version-renames collapsed as compatible]"
            )
            matched_ids = {id(c) for c in matched}
            ctx.suppressed.extend(matched)
            kept = [c for c in changes if id(c) not in matched_ids]
            ctx.kept = kept  # keep verdict source in sync (set mid-pipeline by FilterRedundant)
            return kept
        return changes


class EscalateFrozenNamespaceViolations:
    """Tag findings whose symbol / caused_by_type lies in a contractually
    frozen namespace (e.g. ``**::detail::r1``).

    A "frozen namespace" is one that the library author has declared
    off-limits for changes: it is configured via
    :attr:`PolicyFile.frozen_namespaces` and threaded in through
    :attr:`PipelineContext.frozen_namespaces`.

    Action per matched change:

    * Set :attr:`Change.frozen_namespace_violation` to the matching glob
      pattern. The verdict computation (:meth:`PolicyFile.compute_verdict`)
      uses this field to refuse any policy_override that would downgrade
      the change.
    * Prefix the description with ``[frozen-namespace violation:
      <pattern>] `` so the reporter surfaces the policy context.

    No new ChangeKind is introduced — the underlying kind (e.g.
    ``FUNC_REMOVED``) is preserved so downstream tools that already know
    how to react to it continue to work unchanged.

    Matching uses :func:`fnmatch.fnmatchcase` against ``::``-joined name
    segments of the symbol (and, when set, ``caused_by_type``).  Template
    arguments are stripped before matching so
    ``ns::detail::r1::foo<int>(int)`` correctly matches
    ``**::detail::r1::*``.
    """

    name = "escalate_frozen_namespace_violations"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.frozen_namespaces:
            return changes
        # Imported lazily so this module stays free of import cycles.
        import fnmatch

        from .demangle import demangle
        from .diff_filtering import (
            _qualified_functions_by_mangled,
            _qualified_name_for_change,
        )
        from .internal_leak import _strip_template_args

        patterns = list(ctx.frozen_namespaces)
        old_qualified = _qualified_functions_by_mangled(ctx.old)
        new_qualified = _qualified_functions_by_mangled(ctx.new)

        def _match(name: str | None, c: Change) -> str | None:
            if not name:
                return None
            # Collect every plausible C++-qualified form of *name*:
            # 1. the raw value (mangled, demangled, or already qualified);
            # 2. the demangled form when the raw value looks Itanium-mangled;
            # 3. the snapshot-recorded qualified name (Function.name), which
            #    is the only form that recovers the namespace of an
            #    ``extern "C"`` symbol whose export name is unqualified.
            forms: list[str] = [name]
            if name.startswith("_Z"):
                dm = demangle(name)
                if dm:
                    forms.append(dm)
            if name == c.symbol:
                qual = _qualified_name_for_change(c, old_qualified, new_qualified)
                if qual:
                    forms.append(qual)

            for form in forms:
                # Walk every ancestor prefix so ``**::detail::r1`` matches
                # both ``ns::detail::r1::foo`` and the deeper
                # ``ns::detail::r1::sub::foo``.
                candidate = _strip_template_args(form)
                while True:
                    for pat in patterns:
                        if fnmatch.fnmatchcase(candidate, pat):
                            return pat
                    if "::" not in candidate:
                        break
                    candidate = candidate.rsplit("::", 1)[0]
            return None

        def _tag(c: Change) -> None:
            if c.frozen_namespace_violation is not None:
                # Already tagged by an earlier step (e.g. internal-leak
                # overlay that synthesised a finding with the field set).
                return
            pat = (
                _match(c.symbol, c)
                or _match(c.caused_by_type, c)
                or _match(c.qualified_name, c)
            )
            if pat is None:
                return
            c.frozen_namespace_violation = pat
            if not c.description.startswith("[frozen-namespace violation"):
                c.description = f"[frozen-namespace violation: {pat}] " + c.description

        for c in changes:
            _tag(c)
        # ``compare()`` computes the verdict on kept + redundant, so
        # findings moved into ctx.redundant by FilterRedundant must also
        # be tagged — otherwise a downgrade override could silently
        # apply to a redundant-but-frozen finding.
        for c in ctx.redundant:
            _tag(c)
        return changes


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


class PostProcessingPipeline:
    """Execute a sequence of post-processing steps on a change list.

    Each step receives the current change list and a shared context,
    and returns the (possibly modified) change list for the next step.
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self.steps = list(steps)

    def run(
        self,
        changes: list[Change],
        old: AbiSnapshot,
        new: AbiSnapshot,
        suppression: SuppressionList | None = None,
        frozen_namespaces: list[str] | None = None,
        scope_to_public_surface: bool = False,
        force_public_symbols: set[str] | None = None,
        collapse_versioned_symbols: bool = False,
    ) -> PipelineContext:
        """Run all steps, returning the final PipelineContext."""
        ctx = PipelineContext(
            old=old,
            new=new,
            suppression=suppression,
            frozen_namespaces=list(frozen_namespaces or []),
            scope_to_public_surface=scope_to_public_surface,
            force_public_symbols=set(force_public_symbols or set()),
            collapse_versioned_symbols=collapse_versioned_symbols,
        )
        for step in self.steps:
            changes = step.run(changes, ctx)
        # Ensure ctx.kept is set even if FilterRedundant didn't run
        if not ctx.kept and changes:
            ctx.kept = changes
        return ctx

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]


# Default pipeline matching the current compare() post-processing order.
DEFAULT_PIPELINE = PostProcessingPipeline(
    [
        FilterReservedFieldRenames(),
        FilterOpaqueSizeChanges(),
        DowngradeOpaqueStructChanges(),
        DeduplicateAstDwarf(),
        DeduplicateCrossDetector(),
        DowngradeOpaqueTypeChanges(),
        EnrichSourceLocations(),
        FilterNonPublicSurface(),
        ApplySuppression(),
        SuppressRenamedPairs(),
        FilterRedundant(),
        EnrichAffectedSymbols(),
        AttributeStdlibEmbedding(),
        DetectInternalLeaks(),
        # Must run immediately after DetectInternalLeaks: it consumes that step's
        # leak verdict to demote confirmed-unreachable internal-namespace churn.
        DemoteUnreachableInternalChurn(),
        DetectCppPatterns(),
        DetectNamespacePatterns(),
        DetectTemplatePatterns(),
        # Advisory overlay: explains a versioned-symbol-scheme churn (P08). Runs
        # after rename suppression so it only sees residual removed/added pairs.
        DetectVersionedSymbolScheme(),
        # Runs last so it can tag both raw findings and the synthetic
        # overlays added by DetectInternalLeaks / DetectCppPatterns.
        EscalateFrozenNamespaceViolations(),
    ]
)
