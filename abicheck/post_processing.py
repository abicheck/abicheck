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

from .checker_policy import ChangeKind, ReachabilityState

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot
    from .suppression import Suppression, SuppressionList
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
    # ADR-044 P1 item 5: project-configurable internal-implementation-namespace
    # convention, threaded in from PolicyFile.internal_namespaces. None means
    # "not configured" — MarkReachability/DetectInternalLeaks/
    # DemoteUnreachableInternalChurn each fall back to their own
    # DEFAULT_INTERNAL_NAMESPACES. Deliberately not consulted by
    # DetectNamespacePatterns's experimental_namespaces — a different, unrelated
    # convention (see PolicyFile.internal_namespaces's docstring).
    internal_namespaces: tuple[str, ...] | None = None
    # ADR-024 §D4: when True, FilterNonPublicSurface moves findings that are
    # not on the public-header-scoped ABI surface to ``out_of_surface``.
    scope_to_public_surface: bool = False
    # `compare --post-manifest`: an explicit committed-ABI surface (the set of
    # `pp_*`/ufunc-loop symbols a POST manifest promises). When set,
    # FilterNonPublicSurface scopes against *this* set instead of the
    # header-derived surface — an export finding whose symbol is not committed is
    # demoted, while type-level and leak findings are kept (conservative). None
    # means "not manifest-scoped".
    public_surface_allowlist: set[str] | None = None
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


def _snapshot_export_ids(snap: AbiSnapshot) -> set[str]:
    """Every identifier (name + mangled) under which a real export appears.

    Used by manifest scoping to tell a concrete exported symbol (subject to the
    committed-surface filter) from a loader/dynamic pseudo-symbol like
    ``DT_SONAME`` (which is not an export and must survive scoping).

    Includes the platform export tables (ELF ``.dynsym``, PE/Mach-O export
    directories), not just the DWARF-derived ``functions``/``variables``: a
    private ``__pp_*`` helper can appear only in ELF/PE/Mach-O metadata (e.g. a
    header-scoped or no-debug snapshot), and it must still be recognized as a
    concrete export so its findings are demoted rather than kept. Dynamic-section
    pseudo-symbols (``DT_SONAME``/``DT_NEEDED``) are not symbol-table entries, so
    they stay out of this set and survive scoping.
    """
    ids: set[str] = set()
    for coll in (snap.functions, snap.variables):
        for s in coll:
            for attr in ("mangled", "name"):
                val = getattr(s, attr, "")
                if val:
                    ids.add(val)
    for meta, attr in (
        (snap.elf, "symbols"),
        (snap.pe, "exports"),
        (snap.macho, "exports"),
    ):
        for s in getattr(meta, attr, None) or ():
            name = getattr(s, "name", "")
            if name:
                ids.add(name)
    return ids


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
        # Manifest-scoped mode (`compare --post-manifest`) takes precedence: the
        # manifest's committed `pp_*`/ufunc-loop set *is* the authoritative
        # public surface, so there is no header-provenance walk to do.
        if ctx.public_surface_allowlist is not None:
            return self._run_allowlist(changes, ctx)
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

    @staticmethod
    def _run_allowlist(changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Scope against an explicit committed-surface allowlist (POST manifest).

        A finding is demoted to ``out_of_surface`` only when it is a *concrete
        exported symbol* (a function/variable actually present in either
        snapshot's export universe) that is not in the committed set — e.g. churn
        on a private ``__pp_*`` kernel symbol. Everything else is kept
        conservatively (ADR-024 §D5): type-level and never-filter (leak)
        findings, findings with no symbol, and — crucially — loader/dynamic
        findings whose ``symbol`` is a pseudo-name (``DT_SONAME``, ``DT_NEEDED``)
        rather than a real export. A SONAME/NEEDED change breaks linked clients
        independently of the POST export set, so it must survive scoping. This
        mirrors the header path, where an unknown (non-exported) symbol is kept.
        """
        from .surface import is_symbol_level_finding

        allow = ctx.public_surface_allowlist or set()
        force_public = ctx.force_public_symbols
        export_ids = _snapshot_export_ids(ctx.old) | _snapshot_export_ids(ctx.new)
        kept: list[Change] = []
        for c in changes:
            sym = c.symbol or ""
            if not sym or not is_symbol_level_finding(c) or sym not in export_ids:
                # Non-export findings (type-level, leaks, loader/dynamic
                # pseudo-symbols) are outside the export-name filter — keep.
                kept.append(c)
                continue
            # The manifest allowlist is a set of *exact* C export names, so match
            # exactly — the suffix-tolerant `_change_matches_symbols` would let an
            # uncommitted namespaced helper (`internal::pp_foo`) pass as committed
            # `pp_foo`, contradicting the `--post-manifest` contract. The
            # `force_public` widening overlay is a header-scoping concept and is
            # only honored when header scoping is also on — the CLI warns it is
            # ignored under `--no-scope-public-headers`, so applying it here would
            # contradict that warning (e.g. force a private `__pp_impl` back in).
            if sym in allow or (
                ctx.scope_to_public_surface
                and force_public
                and _change_matches_symbols(c, force_public)
            ):
                kept.append(c)
            else:
                c.surface_exclusion_reason = "not in POST manifest committed surface"
                ctx.out_of_surface.append(c)
        return kept


#: Native C/C++ finding kinds whose *symbol* is an exported function/variable or
#: whose subject is an internal type — the API-content axis. For a CPython
#: extension module (which exports only ``PyInit_``) these are not part of any
#: ``import`` consumer's contract. Load- and linkage-level kinds (``needed_*`` /
#: ``soname_*`` / security / symbol-version) are deliberately NOT here: they
#: affect whether the ``.so`` loads, which IS part of the contract.
_EXT_INTERNAL_SYMBOL_PREFIXES = (
    "func_",
    "var_",
    "virtual_",
    "method_",
    "vtable_",
    "rtti_",
)


def _is_off_python_surface(c: Change, init_symbol: str | None) -> bool:
    """True when *c* is a native API-content finding off an extension's contract."""
    from .surface import _NEVER_FILTER_KIND_NAMES, _TYPE_LEVEL_KIND_NAMES

    v = c.kind.value
    # Authority: Python-level and CPython load-contract findings are the point.
    if v.startswith("python_"):
        return False
    # Leak / constant findings are never scoped out (ADR-024 §D5.2).
    if v in _NEVER_FILTER_KIND_NAMES:
        return False
    # The module's own init export is its one real native public symbol.
    if init_symbol and c.symbol and (c.symbol == init_symbol or "PyInit_" in c.symbol):
        return False
    return v in _TYPE_LEVEL_KIND_NAMES or v.startswith(_EXT_INTERNAL_SYMBOL_PREFIXES)


class DemoteOffPythonSurface:
    """Demote native C/C++ churn that is off a CPython extension's real contract.

    A CPython extension module's consumer contract is (a) its **Python-visible
    API** — functions/classes/methods recovered from its ``.pyi`` and diffed by
    :mod:`abicheck.diff_python_api` — and (b) its **native load contract** —
    imported ``Py*`` symbols / ``abi3`` conformance, checked by
    :mod:`abicheck.diff_python`. The module exports only ``PyInit_<mod>``; its
    other exported C/C++ symbols and internal type layout are implementation
    detail no ``import`` consumer can link or observe. When abicheck is run on
    such a module with debug info (or headers absent), the native detectors
    surface that internal churn as breaking — a **false positive** for the
    extension's real consumers.

    This step uses the recovered Python surface as the authoritative
    public-contract oracle: when the new snapshot is a recognised extension with
    a ``python_api`` surface and there is **no** C-header surface to scope
    against (headers being the stronger oracle, deferred to when present), native
    API-content findings (:func:`_is_off_python_surface`) are demoted to the
    audit ledger (``ctx.out_of_surface``, ADR-024 §D4/D5) — never dropped.

    Authority rule (ADR-028 D3): ``python_api_*`` and
    ``python_stable_abi_*``/``abi3``/``gil`` findings are never demoted here, and
    load/linkage/leak kinds are kept, so this can only ever remove native
    internal noise — never hide a real Python-level or load-contract break.
    Opt-in with ``ctx.scope_to_public_surface`` (on by default), so
    ``--no-scope-public-headers`` keeps every native finding.
    """

    name = "demote_off_python_surface"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.scope_to_public_surface:
            return changes
        new_ext = ctx.new.python_ext
        if new_ext is None or not new_ext.is_extension:
            return changes
        # Both sides must be extensions. Otherwise a normal native library that
        # is *replaced by* an extension (v1 exports `foo`; v2 is an extension
        # dropping it) would have its real `func_removed` demoted, hiding a
        # genuine break for the old library's C/C++ consumers. Only when the old
        # artifact was itself an extension is its native symbol surface known to
        # be implementation detail rather than a public contract.
        old_ext = ctx.old.python_ext
        if old_ext is None or not old_ext.is_extension:
            return changes
        # Defer to the C-header oracle when a public header surface resolved on
        # *either* side (hybrid modules that ship a real public C API):
        # FilterNonPublicSurface already scoped it. Checking both sides matters
        # for a hybrid that removes its last C API function — the old side's
        # header proves the dropped symbol was public, so its `func_removed`
        # must not be demoted just because the new side no longer resolves.
        if (ctx.surf_old is not None and ctx.surf_old.resolvable) or (
            ctx.surf_new is not None and ctx.surf_new.resolvable
        ):
            return changes
        # No recovered Python surface ⇒ no oracle ⇒ keep everything (honest
        # degradation, same posture as header-scoping's no-surface fallback).
        if ctx.new.python_api is None:
            return changes
        from .surface import REASON_OFF_PYTHON_SURFACE

        init_symbol = new_ext.init_symbol
        kept: list[Change] = []
        for c in changes:
            if _is_off_python_surface(c, init_symbol):
                c.surface_exclusion_reason = REASON_OFF_PYTHON_SURFACE
                ctx.out_of_surface.append(c)
            else:
                kept.append(c)
        return kept


# diff_types.py builds ENUM_MEMBER_*/ENUM_LAST_MEMBER_VALUE_CHANGED's symbol
# as "EnumName::member" (unlike TYPE_FIELD_* kinds, which carry the
# containing type name directly) — MarkReachability.run needs this set to
# know when to peel the member suffix before checking the owning EnumType's
# public-header origin.
_ENUM_MEMBER_KINDS = frozenset({
    ChangeKind.ENUM_MEMBER_REMOVED,
    ChangeKind.ENUM_MEMBER_ADDED,
    ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
})

# L4 (source_diff.py) / L5 (source_graph_findings.py) findings below are
# public *by construction* -- each built only from an already-proven-public
# entity, never a bare namespace-name heuristic (Codex review, many passes).
# NOT extended to SOURCE_BINARY_PROVENANCE_MISMATCH (aggregate, symbol="")
# or ODR_SOURCE_CONFLICT's sibling checks not scoped to public types.
_PUBLIC_SOURCE_ABI_KINDS = frozenset({
    ChangeKind.PUBLIC_TYPEDEF_REMOVED,
    ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
    ChangeKind.PUBLIC_MACRO_REMOVED,
    ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
    ChangeKind.INLINE_FUNCTION_REMOVED,
    ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
    ChangeKind.CONCEPT_TIGHTENED,
    ChangeKind.CONSTEXPR_VALUE_CHANGED,
    ChangeKind.DEFAULT_ARGUMENT_CHANGED,
    ChangeKind.INLINE_BODY_CHANGED,
    ChangeKind.TEMPLATE_BODY_CHANGED,
    ChangeKind.GENERATED_HEADER_CHANGED,
    ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
    ChangeKind.ODR_SOURCE_CONFLICT,
    # L5 (source_graph_findings.py) kinds whose subject is itself a
    # proven-public entry/decl/symbol, not just something touching one.
    # NOT extended to BUILD_OPTION_REACHES_PUBLIC_SYMBOL/TARGET_DEPENDENCY_
    # ADDED -- keyed on an option/target that merely reaches something
    # public, not a public entity itself.
    ChangeKind.PUBLIC_REACHABILITY_CHANGED,
    ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
    ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED,
    ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
    ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT,
    ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED,
    # _mapping_drift_findings fires only on old_sym != new_sym, and a
    # SOURCE_DECL_MAPS_TO_SYMBOL edge's target is always a genuinely
    # *exported* symbol (source_link.relink_surface_exports matches only
    # against the real export set) -- so at least one side has this decl
    # actually exported whenever it fires (Codex review).
    ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
})


class MarkReachability:
    """Tag each change with public-reachability metadata, before suppression runs.

    ADR-044 D1: the pipeline-order bug this step fixes is that
    ``ApplySuppression`` used to run before ``DetectInternalLeaks``, so a broad
    namespace/source_location suppression rule could remove the raw evidence
    (e.g. a ``type_size_changed`` on an internal type) before the internal-leak
    detector ever got a chance to see it — silently hiding a genuine leak
    through the public ABI with no trace in the report.

    This step computes the same public-surface reachability walk
    (:func:`internal_leak.compute_leak_paths`) that ``DetectInternalLeaks``
    uses, but up front — before any filtering — and tags every matching change
    with ``public_reachable``/``reachability_kind``/``reachability_proof_path``.
    ``compute_leak_paths`` is a pure function of the snapshot (function/
    variable/type declarations), not of the change list, so computing it here
    does not depend on pipeline position and does not need to be recomputed by
    ``DetectInternalLeaks`` later (which still runs after redundancy filtering
    to decide which *triggering* changes produce a synthetic leak finding).

    Deliberately does **not** also tag a change "reachable" merely because its
    own subject fails to look internal-namespaced (Codex review; reverted
    after landing — see ADR-044's "Post-merge review rounds" note). A
    ``source_location``/``namespace`` rule's whole reason to exist is
    compensating for `AbiSnapshot`'s visibility model marking *every* exported
    C/C++ symbol ``Visibility.PUBLIC`` regardless of whether the maintainer
    considers it part of the contract — ``AbiSnapshot`` carries no signal that
    distinguishes "a private helper the maintainer knows lives under
    ``internal/``" from "a genuinely public symbol that happens to be declared
    under a matching path" (both are ``Visibility.PUBLIC``, and neither name
    need contain an internal-namespace segment). Tagging any
    non-internal-namespaced subject reachable breaks the former, ordinary,
    already-relied-upon case (``tests/test_libabigail_parity_extended.py``'s
    own ``test_suppress_by_source_location`` encodes exactly this) in the
    course of trying to fix the latter — and no naming heuristic can tell them
    apart. Closing the latter gap for real needs actual dependency evidence
    (the L5 call-graph / consumer-import work already on this ADR's P1/P2
    roadmap), not a heuristic on the symbol's own spelling.

    ``RecordType.origin == ScopeOrigin.PUBLIC_HEADER`` (ADR-024's opt-in
    ``--public-header``/``--public-header-dir`` scoping) is a *different*
    signal from the naming heuristic just described — an explicit, reliable
    tag, not a guess — and is consulted directly below for a change whose
    own subject type carries it, since :func:`internal_leak.compute_leak_paths`
    only ever records *internal* types reached while walking from the public
    surface, never the public seed types themselves (Codex review).

    ``Suppression.matches()`` (ADR-044 D2) consults ``public_reachable`` to
    decide whether a broad rule may apply at all — so the underlying evidence
    for a public-reachable internal change now survives ``ApplySuppression``
    by default, and ``DetectInternalLeaks`` (still running later in the
    pipeline, unchanged position) has real evidence to correlate.

    Skipped entirely when no suppression rules are configured
    (``ctx.suppression is None``, the common case, mirroring
    ``ApplySuppression``'s own no-op check): the reachability tags this step
    computes have no other consumer in this slice, and
    :func:`internal_leak.compute_leak_paths` is a full public-surface BFS —
    running it here unconditionally would duplicate the walk
    ``DetectInternalLeaks`` always performs later, roughly doubling that cost
    on every comparison even when nothing will ever read the tag (perf
    regression caught by ``benchmark_scaling.py``'s CI gate). Likewise
    skipped when a suppression *is* configured but every rule in it is
    narrow with the default (or explicit ``"any"``) reachability (Codex
    review) — :meth:`SuppressionList.needs_reachability_evidence` proves
    such a file's rules can never actually consult the tag either, which is
    the common case (a handful of exact ``symbol:`` waivers).
    """

    name = "mark_reachability"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        # Mirrors DetectInternalLeaks/DemoteUnreachableInternalChurn's own
        # constructor (Codex review, P2): those two steps already accept an
        # internal-namespace override, so MarkReachability must too, or a
        # project whose internal-namespace convention isn't in
        # DEFAULT_INTERNAL_NAMESPACES (e.g. "priv" instead of "detail") would
        # be recognized by the leak detector but invisible to the
        # reachability tag that gates suppression — reintroducing this ADR's
        # own failure mode for exactly that convention. An explicit
        # constructor argument (this parameter) always wins; absent that,
        # ``run()`` falls back to ``ctx.internal_namespaces`` — the
        # PolicyFile.internal_namespaces value DEFAULT_PIPELINE threads
        # through on every call (ADR-044 P1 item 5) — before finally
        # defaulting to DEFAULT_INTERNAL_NAMESPACES.
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None or not ctx.suppression.needs_reachability_evidence():
            return changes

        from .internal_leak import (
            _IDENTITY_VTABLE_KINDS,
            DEFAULT_INTERNAL_NAMESPACES,
            _format_path,
            _path_has_indirection,
            _path_is_value_propagating,
            _root_type_name_for_change,
            compute_call_graph_leak_paths,
            compute_leak_paths,
            is_internal_type,
            select_preferred_path,
        )
        from .model import ScopeOrigin

        def _public_header_names(snap: AbiSnapshot) -> set[str]:
            """Names of every declaration ADR-024's opt-in ``--public-header``/
            ``--public-header-dir`` scoping marked ``ScopeOrigin.PUBLIC_HEADER``
            — ``Function``/``Variable``/``RecordType``/``EnumType`` all carry
            this field. Without that flag every origin is ``ScopeOrigin.UNKNOWN``,
            so this returns empty and degrades to the prior behavior.

            ``Function``/``Variable`` also contribute their demangled-mangled
            qualified name (:func:`diff_filtering._qualified_by_mangled`), not
            just ``.name`` — the default CastXML backend never qualifies
            ``.name`` with namespace context, so a bare-name-only set would
            never match ``Change.qualified_name`` (itself recovered the same
            way) for a namespaced public function/variable, silently
            reproducing the exact identity gap this direct-match branch
            exists to close (Codex review, fresh evidence)."""
            from .diff_filtering import _qualified_by_mangled

            names: set[str] = set()
            names.update(f.name for f in snap.functions if f.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(v.name for v in snap.variables if v.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(t.name for t in snap.types if t.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(e.name for e in snap.enums if e.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(
                _qualified_by_mangled(
                    [
                        (f.mangled, f)
                        for f in snap.functions
                        if f.origin == ScopeOrigin.PUBLIC_HEADER
                    ]
                ).values()
            )
            names.update(
                _qualified_by_mangled(
                    [
                        (v.mangled, v)
                        for v in snap.variables
                        if v.origin == ScopeOrigin.PUBLIC_HEADER
                    ]
                ).values()
            )
            return names

        namespaces = self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
        old_paths = compute_leak_paths(ctx.old, namespaces)
        new_paths = compute_leak_paths(ctx.new, namespaces)
        reachable_types = set(old_paths) | set(new_paths)
        # ADR-044 P1 item 1: a second, independent reachability signal — the
        # optional L5 call graph's DECL_CALLS_DECL/DECL_REFERENCES_DECL edges
        # (--sources/--build-info, or the now-always-on L2 header-only graph). compute_leak_paths only
        # ever sees layout/type-graph reachability (inheritance, by-value
        # fields, signatures); a public inline function's *body* calling into
        # a removed/changed internal template specialization has none of
        # that evidence at all, but is real to a linker — the exact oneDAL
        # dispatcher gap the P0 slice's own "What this ADR does not fix"
        # section named. Returns {} on both sides with no embedded graph, so
        # this degrades to the prior behavior automatically for the common
        # case.
        old_call_paths = compute_call_graph_leak_paths(ctx.old, namespaces)
        new_call_paths = compute_call_graph_leak_paths(ctx.new, namespaces)
        call_reachable = set(old_call_paths) | set(new_call_paths)
        # ScopeOrigin.PUBLIC_HEADER (Codex review, fresh evidence):
        # compute_leak_paths only ever records *internal* types reached
        # while walking from the public surface — a declaration that is
        # itself the public surface (e.g. a header-only type never
        # referenced by an exported function/variable) never becomes a key
        # in its result, so a raw change on that declaration's own layout
        # got no tag at all from the walk above. ADR-024's opt-in
        # public-header scoping is the same reliable direct signal already
        # used for the late-detector findings in diff_namespaces.py/
        # diff_templates.py — apply it here too, across every declaration
        # kind that carries the field (function/variable/type/enum), not
        # just RecordType.
        public_header_names = _public_header_names(ctx.old) | _public_header_names(ctx.new)
        # Codex review, fourth pass: this used to return early here when
        # nothing at all was found reachable (no point tagging
        # public_reachable/reachability_kind — they'd all stay at their
        # False/None defaults either way). That is no longer true for
        # reachability_state: compute_leak_paths above already ran to
        # completion regardless, and its result being empty is itself
        # conclusive proof that no declared type in this comparison is
        # public-reachable — a per-change loop below still needs to run to
        # translate that into PROVEN_UNREACHABLE for every type-shaped
        # change, or a "nothing reachable anywhere" comparison would
        # wrongly leave every declared-type change at the honest-looking
        # but incorrect UNKNOWN default. The loop itself is cheap (simple
        # dict/set membership checks) — the walk it would have skipped
        # re-running already happened above, so this isn't a perf change.

        # A change whose root names a declared type is fully covered by the
        # layout/type-graph walk above (compute_leak_paths); a function/
        # variable-shaped root never was, so only a trustworthy call graph
        # can speak to it. "Trustworthy" means both producer passes ran to
        # completion (extractor_passes["call_graph"]/["type_graph"]) on both
        # sides -- not merely "some edge exists somewhere", since a
        # header-only or partially-collected graph can carry unrelated edges
        # while never examining the decl in question, and the combined walk
        # below mixes DECL_CALLS_DECL (call_graph.py) with
        # DECL_REFERENCES_DECL (type_graph.py) edges, each gated by its own
        # pass (Codex review, three passes).
        def _call_graph_fully_trusted(snap: AbiSnapshot) -> bool:
            build_source = getattr(snap, "build_source", None)
            graph = getattr(build_source, "source_graph", None) if build_source is not None else None
            if graph is None:
                return False
            if not (graph.extractor_passes.get("call_graph") and graph.extractor_passes.get("type_graph")):
                return False
            # Both passes completed is not enough on its own: the walk only
            # ever seeds from is_consumer_compiled_public_entry() nodes, not
            # merely "declared by some header" (source_graph_findings'
            # _public_decls() doesn't filter by visibility, so a
            # private-header decl would still count there) -- require the
            # walk's own actual seed predicate to find a match (Codex
            # review, two passes).
            from .buildsource.source_graph import is_consumer_compiled_public_entry

            node_by_id = {n.id: n for n in graph.nodes}
            exported_decls = {
                e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
            }
            return any(
                is_consumer_compiled_public_entry(n.id, node_by_id, exported_decls)
                for n in graph.nodes
            )

        old_call_graph_trusted = _call_graph_fully_trusted(ctx.old)
        new_call_graph_trusted = _call_graph_fully_trusted(ctx.new)

        # Codex review, eighth pass: a ``kind.value.endswith("_removed"/"_added")``
        # heuristic also matches changed-in-place attribute toggles on a decl
        # that exists on *both* sides — e.g. FUNC_VIRTUAL_ADDED,
        # FUNC_NOEXCEPT_REMOVED, CTOR_EXPLICIT_ADDED, *_DEPRECATED_ADDED/REMOVED.
        # For those, requiring trust from only the suffix-selected side would
        # let a change on the untrusted/never-examined side slip through as
        # PROVEN_UNREACHABLE. Check the decl's *actual* presence on each
        # snapshot instead of pattern-matching the kind name, which is immune
        # to new one-sided or attribute-toggle kinds being added later.
        old_decl_names = {f.mangled for f in ctx.old.functions} | {f.name for f in ctx.old.functions}
        old_decl_names |= {v.mangled for v in ctx.old.variables} | {v.name for v in ctx.old.variables}
        new_decl_names = {f.mangled for f in ctx.new.functions} | {f.name for f in ctx.new.functions}
        new_decl_names |= {v.mangled for v in ctx.new.variables} | {v.name for v in ctx.new.variables}

        def _relevant_call_graph_trusted(change: Change, root: str) -> bool:
            """Only require trust from the side(s) *change*'s target actually
            exists on. A decl removed entirely (gone from the new snapshot)
            only ever existed on the old side, so only the old graph's
            coverage speaks to whether some old public entry called it — an
            untrusted/absent *new*-side graph (unsurprising, since the decl
            is gone there) must not turn a real old-side proof into UNKNOWN.
            Symmetric for a decl that's newly added. A decl present on both
            sides (a genuine changed-in-place attribute toggle) needs both
            sides trusted for a symmetric proof."""
            names = (root, change.qualified_name)
            existed_before = any(n is not None and n in old_decl_names for n in names)
            existed_after = any(n is not None and n in new_decl_names for n in names)
            if existed_before and not existed_after:
                return old_call_graph_trusted
            if existed_after and not existed_before:
                return new_call_graph_trusted
            return old_call_graph_trusted and new_call_graph_trusted

        # Typedef aliases (Codex review) are declared snapshot type surface
        # too — AbiSnapshot.typedefs is a flat {alias: underlying} map, not
        # a list of records/enums, so it needs its own membership check
        # alongside types/enums for TYPEDEF_REMOVED/TYPEDEF_BASE_CHANGED's
        # root (the alias name) to be recognized as layout-walk domain.
        known_type_names = (
            {t.name for t in ctx.old.types} | {e.name for e in ctx.old.enums}
            | {t.name for t in ctx.new.types} | {e.name for e in ctx.new.enums}
            | set(ctx.old.typedefs) | set(ctx.new.typedefs)
        )
        # RecordType.qualified_name (DWARF-backend only) resolves a bare name
        # like "Hidden" ("ns::detail::Hidden") for is_internal_type below --
        # only when unambiguous, else a colliding public/internal type of
        # the same bare name could leak the wrong namespace (Codex review).
        qualified_names_by_bare: dict[str, set[str]] = {}
        for t in (*ctx.old.types, *ctx.new.types):
            if t.qualified_name:
                qualified_names_by_bare.setdefault(t.name, set()).add(t.qualified_name)
        qualified_name_by_bare = {
            bare: next(iter(names))
            for bare, names in qualified_names_by_bare.items()
            if len(names) == 1
        }

        for c in changes:
            root = _root_type_name_for_change(c)
            # An enum-member finding's symbol is "EnumName::member" (diff_types.py),
            # not stripped by _root_type_name_for_change (that stripping is
            # scoped to STRUCT_FIELD_* kinds only) — peel it here so a
            # public-header-scoped EnumType's own member churn is found too.
            enum_owner = (
                root.rsplit("::", 1)[0]
                if "::" in root and c.kind in _ENUM_MEMBER_KINDS
                else None
            )
            # Codex review (fresh evidence): root is c.symbol verbatim for a
            # function/variable-shaped change, and diff_symbols.py sets that
            # to the *mangled* linker name for FUNC_REMOVED/FUNC_ADDED/etc. --
            # while _public_header_names above collects Function.name, which
            # is demangled. root == a public_header_names entry therefore
            # never matches for a real (mangled) C++ symbol, so a
            # public-header-declared C++ function/variable removal fell
            # through this direct-public-symbol check entirely, relying
            # entirely on the layout/call-graph walks below to still tag it
            # -- and a standalone public entry point that nothing else
            # references or embeds is reachable by neither, so it was
            # silently untagged and a broad suppression rule could hide it
            # with no diagnostic. c.qualified_name (EnrichSourceLocations,
            # runs before this step) is set from the demangled Function.name
            # for exactly the FUNC_REMOVED/FUNC_ADDED kinds this matters for,
            # so check it too.
            if (
                root in public_header_names
                or enum_owner in public_header_names
                or (c.qualified_name and c.qualified_name in public_header_names)
            ):
                c.public_reachable = True
                c.reachability_kind = "direct_public_symbol"
                c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                continue
            if c.kind in _PUBLIC_SOURCE_ABI_KINDS:
                c.public_reachable = True
                c.reachability_kind = "public_source_abi_surface"
                c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                continue
            tagged = False
            # An enum-member finding's root still carries the "::member"
            # suffix here (only stripped into enum_owner just above), so it
            # never matches a reachable_types key by itself even when the
            # owning enum genuinely was walked and found reachable —
            # compute_leak_paths records leaf types like enums under their
            # bare name (CodeRabbit review). Fall back to enum_owner so a
            # reachable enum's member change is tagged from this same walk
            # instead of only being caught by the coarser known_type_names
            # fallback below (which cannot distinguish reachable from
            # merely-declared).
            layout_key = (
                root
                if root in reachable_types
                else (enum_owner if enum_owner in reachable_types else None)
            )
            if layout_key is not None:
                old_pl = old_paths.get(layout_key, [])
                new_pl = new_paths.get(layout_key, [])
                paths = old_pl + [p for p in new_pl if p not in old_pl]
                # Mirror DetectInternalLeaks's own value/indirection judgment
                # (Codex review): a pure-layout change reached *only* through
                # a pointer/reference is not consumer-visible and
                # DetectInternalLeaks will not emit a leak finding for it
                # either — tagging it public_reachable anyway would refuse a
                # broad suppression rule and append a
                # suppression_would_hide_public_break diagnostic for churn
                # that was always going to be demoted as unreachable by
                # DemoteUnreachableInternalChurn, a false alarm.
                identity_or_vtable = c.kind in _IDENTITY_VTABLE_KINDS
                all_indirect = bool(paths) and all(
                    _path_has_indirection(p) for p in paths
                )
                if paths and not (all_indirect and not identity_or_vtable):
                    c.public_reachable = True
                    preferred_path = select_preferred_path(paths)
                    c.reachability_kind = "value_embedding" if _path_is_value_propagating(preferred_path) else "pointer_or_signature"
                    c.reachability_proof_path = _format_path(preferred_path)
                    c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                    tagged = True
            # ADR-044 P1 items 1/3: independent of (and checked regardless of
            # the outcome of) the layout walk above — a change can be
            # call-graph-reachable without any layout/type-graph evidence at
            # all (e.g. func_removed on an internal decl with no field/base/
            # signature reference anywhere).
            # Codex review (fresh evidence): compute_call_graph_leak_paths's
            # mangled-symbol key only exists when the graph carries a
            # SOURCE_DECL_MAPS_TO_SYMBOL edge for the target decl — the
            # build-integrated L4/L5 path (source_graph.py) creates one, but
            # the header-only path (header_graph.py, always-on since G29
            # Phase A / the implicit dump path, no real build) never does. c.qualified_name
            # (EnrichSourceLocations, runs before this step) is set from
            # Function.name — the same demangled name a graph node's own
            # label carries in EITHER mode — so it is a reliable fallback key
            # independent of which graph provenance produced the evidence.
            call_key = (
                root if root in call_reachable
                else (c.qualified_name if c.qualified_name in call_reachable else None)
            )
            if not tagged and call_key is not None:
                call_paths = old_call_paths.get(call_key, []) + [
                    p
                    for p in new_call_paths.get(call_key, [])
                    if p not in old_call_paths.get(call_key, [])
                ]
                if call_paths:
                    c.public_reachable = True
                    c.reachability_kind = "symbol_availability"
                    c.reachability_proof_path = min(call_paths, key=len)
                    c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                    tagged = True
            if not tagged:
                # Not proven reachable. A change whose root names a declared
                # type is squarely in the layout/type-graph walk's domain —
                # that walk is a complete closure over every internal type
                # reachable from the public surface, so its absence there is
                # conclusive proof regardless of call-graph coverage
                # (PROVEN_UNREACHABLE either way: whether the walk found no
                # path at all, or found only a demoted pointer-only path).
                #
                # A change whose root is *not* a declared type (a function/
                # variable-shaped change — e.g. func_removed on an internal
                # decl) was never in that walk's domain to begin with; only
                # the call graph could speak to it, so its verdict is
                # conclusive only when the side(s) its target could actually
                # exist on have a fully trusted, completed call-graph pass
                # (Codex review — neither an absent graph nor a handful of
                # incidental edges from a partial one may silently read the
                # same as a trustworthy graph that looked and found
                # nothing).
                #
                # Restricted to an *internal-namespaced* subject (Codex
                # review, sixth pass): compute_call_graph_leak_paths only
                # ever walks dependencies of consumer-compiled public
                # entries — is_consumer_compiled_public_entry() explicitly
                # excludes an ordinary out-of-line exported function — so a
                # trusted call graph can prove an *internal callee* absent,
                # but says nothing about an exported public symbol's own
                # reachability. Without this gate, a plain FUNC_REMOVED on a
                # real, directly-exported API function with no inline
                # caller would be misread as call-graph-proven-unreachable
                # and a broad proven-unreachable-only rule could suppress a
                # genuine ABI break. root is typically the *mangled* symbol
                # for a function/variable change (diff_symbols.py), which
                # has no "::" segments for is_internal_type to see — check
                # the demangled c.qualified_name too, same fallback pattern
                # used elsewhere in this walk.
                #
                # Restricted the same way for the layout walk itself
                # (Codex review, seventh pass): compute_leak_paths only ever
                # records *internal* types it finds reached from the public
                # surface — it never records the public seed types
                # themselves (see _public_header_names's own docstring
                # above). A genuinely public declared type absent from
                # reachable_types was therefore never examined by this walk
                # at all, not proven unreachable by it — treating any known
                # declared type as "layout domain" let a broad
                # `namespace: ns::*` rule suppress a real public-type
                # layout break with no diagnostic. root already having been
                # a key in reachable_types (even if later demoted to
                # pointer-only/non-value) is real positive evidence
                # regardless of naming; absence from it is only conclusive
                # for a type the walk's internal-only domain could have
                # classified in the first place.
                # An enum-member root keeps its "::member" suffix, so use
                # enum_owner (bare name) or a member literally named e.g.
                # "detail" would read as internal-namespaced (Codex review).
                internal_check_subject = enum_owner if enum_owner is not None else root
                # qualified_name_by_bare is keyed from RecordType names only
                # -- an enum's bare owner could collide with an unrelated
                # record's bare name, wrongly feeding the record's namespace
                # onto the enum (Codex review). Never resolve it for
                # enum_owner.
                type_qualified_name = (
                    qualified_name_by_bare.get(root) if enum_owner is None else None
                )
                subject_is_internal = is_internal_type(
                    internal_check_subject, namespaces
                ) or (
                    c.qualified_name is not None
                    and is_internal_type(c.qualified_name, namespaces)
                ) or (
                    type_qualified_name is not None
                    and is_internal_type(type_qualified_name, namespaces)
                )
                layout_domain = root in reachable_types or (
                    subject_is_internal
                    and (
                        root in known_type_names
                        or (enum_owner is not None and enum_owner in known_type_names)
                    )
                )
                if layout_domain or (
                    subject_is_internal and _relevant_call_graph_trusted(c, root)
                ):
                    c.reachability_state = ReachabilityState.PROVEN_UNREACHABLE
                else:
                    c.reachability_state = ReachabilityState.UNKNOWN
        return changes


class ApplySuppression:
    """Apply user-provided suppression rules.

    ADR-044 D2: a rule matches only when ``Suppression.matches()`` also passes
    its reachability/``allow_public_break`` gate — see that method for the
    semantics. A match refused by that gate is recorded as a
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic (ADR-044 D4) instead of
    being silently dropped, so the change stays visible *and* the suppression
    author sees why their rule did not apply.
    """

    name = "apply_suppression"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None:
            return changes
        filtered: list[Change] = []
        diagnostics: list[Change] = []
        for c in changes:
            outcome = ctx.suppression.evaluate(c)
            if outcome.suppressed:
                if outcome.matched_rule is not None:
                    c.suppression_rule = outcome.matched_rule.label or outcome.matched_rule.reason
                ctx.suppressed.append(c)
                continue
            filtered.append(c)
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
        filtered.extend(diagnostics)
        return filtered


def _build_suppression_overreach_change(change: Change, rule: Suppression) -> Change:
    """Build the ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic for *change*.

    ADR-044 D4. *rule* is the suppression whose selectors matched *change* but
    whose reachability/``allow_public_break`` gate withheld the match.
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    # would_withhold() only ever returns True for a *broad* rule (namespace/
    # entity_namespace/cause_namespace/source_location, no primary narrow
    # selector — see Suppression._is_broad_selector): a rule with symbol/
    # symbol_pattern/type_pattern set has _is_broad_selector=False, so
    # _passes_public_break_gate short-circuits True and would_withhold can
    # never fire for it. The selector fallback below only needs the three
    # broad-shaped fields; entity_namespace is the canonical spelling of
    # namespace (self-review finding: it was missing here even though the
    # equivalent SuppressionAudit string-building was already fixed).
    selector = (
        rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        or rule.source_location
        or "?"
    )
    proof = (
        f" via {change.reachability_proof_path}"
        if change.reachability_proof_path
        else ""
    )
    return Change(
        kind=ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: the symbol is "
            f"public-reachable{proof}. Add `allow_public_break: true` to this "
            "rule to suppress it anyway."
        ),
        caused_by_type=change.symbol,
    )


def _build_suppression_unknown_reachability_change(change: Change, rule: Suppression) -> Change:
    """Build the ``SUPPRESSION_REACHABILITY_UNKNOWN`` diagnostic for *change*.

    impact-analysis-layer P0 slice. *rule* is the suppression whose selectors
    matched *change*, whose resolved ``reachability`` is
    ``"proven-unreachable-only"``, but whose graph coverage could not prove
    *change* unreachable (``Change.reachability_state`` is ``UNKNOWN``).
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    selector = (
        rule.symbol
        or rule.symbol_pattern
        or rule.type_pattern
        or rule.namespace
        or rule.entity_namespace
        or rule.cause_namespace
        or rule.source_location
        or "?"
    )
    return Change(
        kind=ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN,
        symbol=change.symbol,
        description=(
            f"Suppression rule {selector!r} matched {change.symbol!r} "
            f"({change.kind.value}) but was not applied: graph coverage was "
            "insufficient to prove the change unreachable from the public ABI "
            "surface (reachability: proven-unreachable-only). Add "
            "`allow_unknown_reachability: true` to this rule to suppress it "
            "anyway once you have manually confirmed it is safe."
        ),
        caused_by_type=change.symbol,
    )


def _merge_findings_respecting_suppression(
    changes: list[Change],
    new_findings: list[Change],
    ctx: PipelineContext,
) -> None:
    """Append deduplicated ``new_findings`` to ``changes``, respecting suppression.

    Mutates ``changes`` in place. Shared by every post-``ApplySuppression``
    detector that builds fresh ``Change`` objects (``DetectCppPatterns``,
    ``DetectTemplatePatterns``, ``DetectNamespacePatterns``) — those findings
    never passed through ``ApplySuppression`` itself, so they must run their
    own suppression check here.

    Uses :meth:`SuppressionList.evaluate` (not the cheaper ``is_suppressed``)
    so a broad rule whose selectors matched but was withheld by the
    reachability/``allow_public_break`` gate still emits the same
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic ``ApplySuppression``
    produces for changes it sees directly (ADR-044 D4; Codex review, fresh
    evidence) — otherwise a late finding that a broad rule *would* have
    hidden, had it been reachable earlier, is silently kept with no
    explanation of why the matching rule didn't apply.
    """
    seen_keys = {(c.kind, c.symbol) for c in changes}
    diagnostics: list[Change] = []
    for c in new_findings:
        if ctx.suppression is not None:
            outcome = ctx.suppression.evaluate(c)
            if outcome.suppressed:
                ctx.suppressed.append(c)
                continue
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
        key = (c.kind, c.symbol)
        if key in seen_keys:
            continue
        changes.append(c)
        seen_keys.add(key)
    changes.extend(diagnostics)


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
        # LONG_DOUBLE_ABI_CHANGED re-pairs a removed↔added symbol pair (its
        # old_value/new_value are the mangled symbols), so its redundant
        # func_removed/func_added halves collapse into it just like a rename.
        _pairing_kinds = (
            ChangeKind.FUNC_LIKELY_RENAMED,
            ChangeKind.LONG_DOUBLE_ABI_CHANGED,
        )
        for c in changes:
            if c.kind in _pairing_kinds and c.old_value and c.new_value:
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

        sycl_findings, sycl_suppressed = detect_sycl_overload_set_removal(
            ctx.old, ctx.new
        )
        new_findings.extend(sycl_findings)

        isa_findings, isa_suppressed = detect_cpu_dispatch_isa_dropped(ctx.old, ctx.new)
        new_findings.extend(isa_findings)

        new_findings.extend(detect_tag_type_renamed(ctx.old, ctx.new))
        new_findings.extend(detect_default_template_arg_changed(ctx.old, ctx.new))
        new_findings.extend(
            detect_inline_body_renamed_member(ctx.old, ctx.new, changes)
        )

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

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        new_findings, suppressed_keys = self._run_all_detectors(ctx, changes)

        if suppressed_keys:
            self._suppress_grouped_children(changes, suppressed_keys, ctx)

        if new_findings:
            _merge_findings_respecting_suppression(changes, new_findings, ctx)

        return changes


class DetectTemplatePatterns:
    """Run the generic template / overload-set pattern detectors.

    Lives in :mod:`abicheck.diff_templates`. Covers internal-template
    leaks (function-template analogue of PR #238), CPO kind flips,
    overload-set rerouting, mandatory-template-param additions, and
    unspecified-return flips.
    """

    name = "detect_template_patterns"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        # Mirrors MarkReachability/DetectInternalLeaks/
        # DemoteUnreachableInternalChurn's own constructor (Codex review,
        # fresh evidence): detect_internal_template_leaks's
        # _INTERNAL_TEMPLATE_NAMESPACES is the same internal-implementation
        # convention those three steps use (detail/impl/internal/__detail/
        # _impl, plus __internal) -- unlike DetectNamespacePatterns's
        # unrelated experimental_namespaces, PolicyFile.internal_namespaces
        # should reach this step too.
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        from .diff_templates import (
            _INTERNAL_TEMPLATE_NAMESPACES,
            detect_template_patterns,
        )

        namespaces = self._namespaces or ctx.internal_namespaces or _INTERNAL_TEMPLATE_NAMESPACES
        new_findings = detect_template_patterns(ctx.old, ctx.new, namespaces)
        if not new_findings:
            return changes
        _merge_findings_respecting_suppression(changes, new_findings, ctx)
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
        _merge_findings_respecting_suppression(changes, new_findings, ctx)
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
            detect_call_graph_leaks,
            detect_internal_leaks,
        )

        namespaces = self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
        extra = detect_internal_leaks(changes, ctx.old, ctx.new, namespaces)
        # ADR-044 P1 items 1-2: the call-graph analogue, for a triggering
        # change with no layout/type-graph evidence at all (see
        # MarkReachability's own call-graph fallback, same namespaces).
        extra = extra + detect_call_graph_leaks(changes, ctx.old, ctx.new, namespaces)
        if not extra:
            return changes
        # Synthetic leak findings must respect user suppression rules too.
        # ``ApplySuppression`` ran earlier in the pipeline, so we apply the
        # same check by hand here (via the shared helper, which also emits
        # the withheld-rule diagnostic ``ApplySuppression`` would have)
        # rather than re-running the whole step.
        _merge_findings_respecting_suppression(changes, extra, ctx)
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

        namespaces = self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
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
                ChangeKind.FUNC_REMOVED,
                ChangeKind.FUNC_REMOVED_ELF_ONLY,
                ChangeKind.VAR_REMOVED,
                ChangeKind.FUNC_LIKELY_RENAMED,
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

    @staticmethod
    def _candidate_forms(
        name: str,
        c: Change,
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> list[str]:
        """Collect every plausible C++-qualified form of *name*."""
        # Imported lazily so this module stays free of import cycles.
        from .demangle import demangle
        from .diff_filtering import _qualified_name_for_change

        # The plausible forms are:
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
        return forms

    @classmethod
    def _match(
        cls,
        name: str | None,
        c: Change,
        patterns: list[str],
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> str | None:
        """Return the first frozen-namespace pattern matching *name*, or None."""
        # Imported lazily so this module stays free of import cycles.
        import fnmatch

        from .internal_leak import _strip_template_args

        if not name:
            return None
        for form in cls._candidate_forms(name, c, old_qualified, new_qualified):
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

    @classmethod
    def _tag(
        cls,
        c: Change,
        patterns: list[str],
        old_qualified: dict[str, str],
        new_qualified: dict[str, str],
    ) -> None:
        """Tag *c* with the matching frozen-namespace pattern, if any."""
        if c.frozen_namespace_violation is not None:
            # Already tagged by an earlier step (e.g. internal-leak
            # overlay that synthesised a finding with the field set).
            return
        pat = (
            cls._match(c.symbol, c, patterns, old_qualified, new_qualified)
            or cls._match(c.caused_by_type, c, patterns, old_qualified, new_qualified)
            or cls._match(c.qualified_name, c, patterns, old_qualified, new_qualified)
        )
        if pat is None:
            return
        c.frozen_namespace_violation = pat
        if not c.description.startswith("[frozen-namespace violation"):
            c.description = f"[frozen-namespace violation: {pat}] " + c.description

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if not ctx.frozen_namespaces:
            return changes
        # Imported lazily so this module stays free of import cycles.
        from .diff_filtering import _qualified_functions_by_mangled

        patterns = list(ctx.frozen_namespaces)
        old_qualified = _qualified_functions_by_mangled(ctx.old)
        new_qualified = _qualified_functions_by_mangled(ctx.new)

        for c in changes:
            self._tag(c, patterns, old_qualified, new_qualified)
        # ``compare()`` computes the verdict on kept + redundant, so
        # findings moved into ctx.redundant by FilterRedundant must also
        # be tagged — otherwise a downgrade override could silently
        # apply to a redundant-but-frozen finding.
        for c in ctx.redundant:
            self._tag(c, patterns, old_qualified, new_qualified)
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
        public_surface_allowlist: set[str] | None = None,
        # Appended after the existing optional parameters (Codex review) —
        # inserting it earlier would silently break a positional caller of
        # any parameter after it (e.g. `.run(c, old, new, sup, fns, True)`
        # for scope_to_public_surface would instead bind `True` here and
        # leave scoping disabled, with no error).
        internal_namespaces: tuple[str, ...] | None = None,
    ) -> PipelineContext:
        """Run all steps, returning the final PipelineContext."""
        ctx = PipelineContext(
            old=old,
            new=new,
            suppression=suppression,
            frozen_namespaces=list(frozen_namespaces or []),
            internal_namespaces=internal_namespaces,
            scope_to_public_surface=scope_to_public_surface,
            force_public_symbols=set(force_public_symbols or set()),
            collapse_versioned_symbols=collapse_versioned_symbols,
            public_surface_allowlist=public_surface_allowlist,
        )
        # ``FilterRedundant`` sets ``ctx.kept = kept`` — an *aliasing* contract,
        # not a snapshot: every step from that point on is required to either
        # leave ``changes`` untouched, mutate it in place (``changes[:] = ...``),
        # or explicitly resync ``ctx.kept`` to whatever new list it returns (see
        # ``DetectVersionedSymbolScheme``). If a future step instead rebinds
        # ``changes = [c for c in changes if ...]`` without updating ``ctx.kept``,
        # ``ctx.kept`` silently keeps pointing at the stale pre-filter list and
        # any suppression/demotion recorded downstream is lost from the verdict
        # with no visible error (this happened once already — see
        # ``DetectCppPatterns``/``DemoteUnreachableInternalChurn``'s in-place
        # comments). Enforce the invariant here instead of trusting every future
        # step author to remember it.
        kept_tracking_active = False
        for step in self.steps:
            changes = step.run(changes, ctx)
            if step.name == FilterRedundant.name:
                kept_tracking_active = True
            elif kept_tracking_active and ctx.kept is not changes:
                raise RuntimeError(
                    f"post-processing step {step.name!r} broke the ctx.kept "
                    "aliasing contract established by FilterRedundant: it "
                    "returned a `changes` list that is not the same object as "
                    "`ctx.kept`, which silently discards any suppression or "
                    "demotion tracked via ctx.kept from the verdict. Fix the "
                    "step to mutate `changes[:] = ...` in place, or to "
                    "explicitly resync `ctx.kept = changes` before returning."
                )
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
        # Runs immediately after FilterNonPublicSurface so it can read the
        # resolved C-header surface (ctx.surf_new) and defer to it; otherwise it
        # uses the recovered Python API as the extension's public-contract oracle.
        DemoteOffPythonSurface(),
        # ADR-044 D1: must run before ApplySuppression so a broad suppression
        # rule can see whether the change it is about to remove is part of the
        # effective public ABI.
        MarkReachability(),
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
