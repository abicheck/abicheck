# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Dump-time dependency scoping (``dump --include-system-declarations`` opt-out).

A header-AST dump serializes every declaration the parser saw, including the
entire transitive dependency surface pulled in by ``#include`` (every
libstdc++/SYCL internal a public *or* private header happens to reach) —
for a library with a large or heavily-templated dependency stack this can
put the snapshot JSON in the hundreds-of-MB range, most of which is
dependency surface that belongs to the toolchain/standard library, not to
the library under test.

This is deliberately **not** a public-API-surface filter: the library's own
private/internal declarations are kept, same as its public ones — only
declarations whose own defining header is a toolchain/system header
(``/usr/include``, the MSVC ``VC/Tools`` tree, the Xcode/macOS SDK, ...) are
excluded, and even that is overridden whenever the header is one of the
dump's own ``-H``/``--header`` roots (or lives under one) — see
:func:`provenance.is_dependency_header`'s docstring for why an installed
library analyzed via its real system-prefixed install path
(``-H /usr/include/mylib/api.h``) must not have its own headers misread as
toolchain headers (Codex review). This applies **by default**, without
requiring a public-header set:
``AbiSnapshot.source_header`` is populated unconditionally by
``provenance.apply_provenance``. ``dump --include-system-declarations`` opts out
and writes the full, unscoped snapshot (the old default).

Because this scopes by header origin rather than ABI visibility, it is a
silent no-op (not an error) on a snapshot with no header-derived
declarations at all (a binary-only/DWARF-only dump) -- unlike an opt-in
flag, default-on behavior must never fail a plain ``dump`` invocation that
has nothing for it to act on.

**Direct-reference retention (status-review follow-up, closes the P0 flagged
against PR #649):** a dependency-header type/enum that is *directly* named
by a kept (non-dependency) declaration's own signature -- a public
function's return/parameter type, a public variable's type, or a kept
type's own field/base -- is retained even though its own ``source_header``
is a toolchain/system header. This is the dump-time half of the same
direct-vs-transitive distinction :mod:`abicheck.type_reachability` already
draws at diff time: ``void foo(std::string value)`` means the library's ABI
genuinely depends on ``std::string``'s layout, so a scoped dump must not
throw that fact away before ``compare`` ever gets to see it -- unlike
``std::string::_Alloc_hider``, which is reachable only through
``std::string``'s own internals and is dropped exactly as before. Retention
is single-hop only: a directly-referenced dependency type's *own* fields
are not chased for further dependency references, so its private internals
(``_Alloc_hider`` and the like) stay excluded even though the type that
embeds them is kept. See :func:`_directly_referenced_dependency_names`.

**Remaining trade-off, by design (CodeRabbit review):** a genuine
ABI-relevant layout change confined entirely to a dependency type that is
*not* directly referenced anywhere in the kept surface (e.g. an internal
allocator/iterator helper type only reachable through another dependency
type's own internals) still becomes invisible to a later ``compare`` once
both snapshots are scoped -- the type is absent from both sides
symmetrically, not merely demoted. That is the intended effect of "we
don't want a dump of the standard dependency"'s implementation internals,
not a bug, but it does mean `dump`'s default output alone is still not a
toolchain/stdlib ABI-drift detector for *transitively*-reached dependency
internals across compiler or C++ standard library upgrades; pass
``--include-system-declarations`` on both sides of a comparison if that detection
is needed.

**Known limitation (investigated, deliberately not fixed here):** this
filters the flat snapshot lists (``functions``/``variables``/``types``/
``enums``) and the DWARF/DWARF-advanced collections keyed off them.
``typedefs`` (``dict[str, str]``, name -> target spelling) carry no
per-entry header provenance at all, so they are kept unconditionally --
typically a small fraction of a dump's size next to full record layouts,
so this is a low-cost simplification, not a hidden accuracy gap the way
skipping type layouts would be. `service._attach_header_graph` (G29 Phase
A, always-on by default -- `_HEADER_GRAPH_ENABLED`) separately embeds a
semantic header-only graph (`snap.surface_graph`, a
`model.source_graph.SourceGraphSummary`) built from the *same*
unscoped header AST; this module leaves it untouched for the same reasons
the previous (now-superseded) public-surface design documented: a correct
filter needs its own closure walk over `GraphNode`/`GraphEdge` (each
carrying its own `facts`/`resolved`/`conflicts`/`provenance`/`confidence`
evidence-merge state -- ADR-046 D2) without corrupting a legitimate real
L3/L4/L5 collection merged into the same pack from an explicit
`--sources`/`--build-info`. That's a separate, independently-scoped
project.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Container, Sequence
from pathlib import Path

from ...extract.flat_map_dependency_scope import kept_reference_text, scope_flat_maps
from ...extract.header_exclusions import scoping_header_predicate
from ...extract.occurrence_dependency_scope import (
    scoped_occurrences_excluding_dependencies,
)
from ...model import AbiSnapshot, EnumType, Function, RecordType, Variable
from ...model.dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata
from ...model.header_parse_coverage import AllBut, header_parse_excluded
from ...model.semantic_ir import SemanticIR, semantic_ir_conflict_key
from ...model.surface_facts import in_public_surface
from ...type_reachability import (
    _NON_PUBLIC_ORIGINS,
)
from .dependency_retention import (
    _candidate_identity,
    _directly_referenced_dependency_names,
    _kept_identifiers,
    _kept_signature_haystack,
    _name_matches,
)


def _scoped_dwarf(
    dwarf: DwarfMetadata | None, kept_identifiers: Container[str]
) -> DwarfMetadata | None:
    """Filter a DWARF layout map to the declarations kept from the flat
    ``types``/``enums`` lists (same dependency-exclusion decision, applied
    to the DWARF side so a later ``diff_platform._diff_dwarf`` can't
    silently re-expand to comparing an excluded dependency type's layout)."""
    if dwarf is None or not dwarf.has_dwarf:
        return dwarf
    return dataclasses.replace(
        dwarf,
        structs={
            k: v for k, v in dwarf.structs.items() if _name_matches(k, kept_identifiers)
        },
        enums={
            k: v for k, v in dwarf.enums.items() if _name_matches(k, kept_identifiers)
        },
        struct_odr_conflicts={
            k: v
            for k, v in dwarf.struct_odr_conflicts.items()
            if _name_matches(k, kept_identifiers)
        },
        enum_odr_conflicts={
            k: v
            for k, v in dwarf.enum_odr_conflicts.items()
            if _name_matches(k, kept_identifiers)
        },
    )


def _scoped_dwarf_advanced(
    adv: AdvancedDwarfMetadata | None,
    kept_identifiers: Container[str],
    excluded_symbols: set[str],
) -> AdvancedDwarfMetadata | None:
    """Filter Sprint-4 advanced DWARF metadata the same way: type-keyed
    collections (``packed_structs``/``all_struct_names``) via
    :func:`_name_matches`, function-keyed collections (keyed by mangled
    ``linkage_name``) by dropping only *excluded_symbols* -- every
    dependency-header function's own ``mangled`` spelling -- rather than
    requiring a match against the kept set (Codex review). The two
    directions need different tolerances for an unreliable bare
    ``mangled == name`` header-AST spelling (see ``tu_merge.py``'s own
    documented limitation on why that field isn't always real mangling):
    for a *kept* (non-dependency) function it must never be trusted to
    positively identify the entry, since a perfectly ordinary C++ function
    can carry one too (an auto-detected-as-C header parse, or an
    uninstantiated template) and DWARF's real key won't match the bare
    guess -- dropping on that mismatch would lose the function's own real
    finding. For an *excluded* function, the bare spelling is safe to
    exclude by: a genuinely unmangled symbol (C/``extern "C"``) carries the
    *same* bare spelling at the real linker level too, so it still matches
    DWARF's actual key; the residual failure mode (an excluded function
    whose true mangled DWARF key isn't its bare name either) merely leaves
    that one entry unfiltered -- the same false-negative-over-false-positive
    bias this module uses throughout, not a new risk to any kept function
    (a kept function's own real mangled name is never bare-equal to an
    unrelated excluded symbol's bare name in a valid binary: two distinct
    globals sharing one unmangled C symbol name would already be an ODR
    violation the linker itself would have rejected).

    Codex review, re-confirmed with a concrete repro (excluded C++ dependency
    function whose header-AST ``mangled`` is an unreliable bare ``"dep"``
    while DWARF's real ``linkage_name`` is ``_ZN3dep3depEv``): this is
    exactly the already-accepted residual failure mode above, not a new gap
    -- ``excluded_symbols`` has no way to recover the real DWARF key from an
    unreliable bare spelling without either a genuine Function-to-DWARF
    correlation this codebase doesn't have, or re-demangling every
    ``linkage_name`` (a real perf cost on every dump/compare, for a benefit
    this false-negative-biased filter already deliberately forgoes
    elsewhere). Left unfiltered under its real key, same as any other
    excluded function whose true mangled DWARF key isn't its bare name."""
    if adv is None or not adv.has_dwarf:
        return adv
    return dataclasses.replace(
        adv,
        calling_conventions={
            k: v
            for k, v in adv.calling_conventions.items()
            if k not in excluded_symbols
        },
        value_abi_traits={
            k: v for k, v in adv.value_abi_traits.items() if k not in excluded_symbols
        },
        return_value_sizes={
            k: v for k, v in adv.return_value_sizes.items() if k not in excluded_symbols
        },
        return_memory_classified={
            k for k in adv.return_memory_classified if k not in excluded_symbols
        },
        packed_structs={
            k for k in adv.packed_structs if _name_matches(k, kept_identifiers)
        },
        all_struct_names={
            k for k in adv.all_struct_names if _name_matches(k, kept_identifiers)
        },
    )


def resolve_dependency_scope(
    snap: AbiSnapshot,
    include_dependencies: bool,
    header_roots: Sequence[Path | str] | None = None,
) -> AbiSnapshot:
    """The single choke point both ``dump``'s serialization step and
    ``service.run_dump`` (compare's live-binary dumping, scan, ...) call:
    apply :func:`scope_snapshot_excluding_dependencies` (``dependency_scope``
    ``"filtered"``) unless *include_dependencies* opts out, in which case
    just record the user's actual intent as ``"full"`` (a no-op when there
    are no header-derived declarations to tag at all — see
    ``AbiSnapshot.dependency_scope``'s own docstring). Applying the same
    function at both choke points is what makes ``dump`` and ``compare``'s
    live-binary dumping filter consistently instead of only ``dump``
    filtering by default while ``compare`` silently never does."""
    if not include_dependencies:
        return scope_snapshot_excluding_dependencies(snap, header_roots)
    if not snap.from_headers:
        return snap
    return dataclasses.replace(snap, dependency_scope="full")


def _scoped_semantic_ir(
    semantic_ir: SemanticIR | None,
    semantic_ir_conflicts: dict[str, str],
    kept_types: list[RecordType],
    kept_enums: list[EnumType],
    kept_functions: list[Function],
    kept_variables: list[Variable],
    header_roots: Sequence[Path | str] | None = None,
) -> tuple[SemanticIR | None, dict[str, str]]:
    """The ``SemanticIR``/``semantic_ir_conflicts`` counterpart of this
    module's flat functions/variables/types/enums filtering (ADR-063 Phase
    6, second/third slices).

    Keeps exactly the occurrences whose ``EntityId`` names a record/enum/
    function/variable that survived the same header-origin filter
    *kept_types*/*kept_enums*/*kept_functions*/*kept_variables* already
    applied -- an excluded dependency declaration's occurrence is dropped
    from the IR the identical way it is dropped from the flat lists, so a
    ``SemanticIR``-aware consumer cannot see more than a
    ``functions``/``types``-reading one does (Codex review: the third
    slice's own functions/variables addition left this function only ever
    checking `EntityKind.TYPE`/`EntityKind.ENUM`, so a dependency-header
    function/variable's occurrence stayed reachable through `semantic_ir`
    after its flat counterpart was already excluded). Typedef/constant
    occurrences are not handled here: they are dropped together with their
    flat ``typedefs``/``constants`` entries by
    :func:`~abicheck.extract.flat_map_dependency_scope.scope_flat_maps`,
    which owns the header attribution those maps carry. Also see :mod:`~abicheck.extract.occurrence_dependency_scope`
    for a second, per-occurrence check applied here (Codex review, PR
    #1024) -- it never drops every occurrence of a kept identity, only a
    dependency one that a surviving non-dependency sibling occurrence can
    stand in for.

    Every ``semantic_ir_conflicts`` entry belonging to an excluded
    occurrence is dropped too (Codex review, PR #1001, on an earlier
    revision that left this dict unfiltered) -- computed as an exact key
    set via :func:`~abicheck.model.semantic_ir.semantic_ir_conflict_key`
    over each excluded occurrence's own :meth:`~abicheck.model.semantic_ir.
    CanonicalEntity.fact_items`, not a text/substring operation, so there is
    no risk of the packed-key corruption
    :func:`~abicheck.model.semantic_ir.renumber_conflict_keys` was built to
    avoid for the *renumbering* case (this is a pure membership filter, not
    a rewrite).

    ``None`` in, ``None`` out for the IR half (a binary-only/DWARF-only
    snapshot, or one a backend that doesn't populate ``semantic_ir`` yet
    produced) -- *semantic_ir_conflicts* passes through unchanged in that
    case, matching every other field this function leaves alone.
    """
    if semantic_ir is None:
        return None, semantic_ir_conflicts
    kept_entity_ids = (
        {t.entity_id for t in kept_types if t.entity_id is not None}
        | {e.entity_id for e in kept_enums if e.entity_id is not None}
        | {f.entity_id for f in kept_functions if f.entity_id is not None}
        | {v.entity_id for v in kept_variables if v.entity_id is not None}
    )
    kept_occurrences, excluded_ids = scoped_occurrences_excluding_dependencies(
        semantic_ir.occurrences, kept_entity_ids, header_roots
    )
    excluded_occurrences = [
        (occ_id, semantic_ir.occurrences[occ_id]) for occ_id in excluded_ids
    ]
    if not excluded_occurrences:
        return semantic_ir, semantic_ir_conflicts
    scoped_ir = dataclasses.replace(semantic_ir, occurrences=kept_occurrences)
    if not semantic_ir_conflicts:
        return scoped_ir, semantic_ir_conflicts
    excluded_keys = {
        semantic_ir_conflict_key(occ_id, fact_name)
        for occ_id, entity in excluded_occurrences
        for fact_name, _fact in entity.fact_items()
    }
    scoped_conflicts = {
        key: value
        for key, value in semantic_ir_conflicts.items()
        if key not in excluded_keys
    }
    return scoped_ir, scoped_conflicts


def scope_snapshot_excluding_dependencies(
    snap: AbiSnapshot,
    header_roots: Sequence[Path | str] | None = None,
) -> AbiSnapshot:
    """Return a copy of *snap* with toolchain/system-header declarations
    dropped, keeping everything that belongs to the library itself.

    Keeps a function/variable/type/enum unless its own ``source_header`` is
    a toolchain/system header (see :func:`provenance.is_dependency_header`)
    -- this is a header-*origin* filter, not an ABI-visibility one: a
    private, non-exported declaration from the library's own headers is
    kept exactly like a public one, only dependency-header declarations are
    dropped. ``header_roots`` should be the actual ``-H``/``--header``
    paths the dump was invoked with, so a header that *is* one of them (or
    lives under one, e.g. an installed library's own private headers under
    ``/usr/include/mylib/``) is never misclassified as a dependency just
    because it happens to sit under a system prefix -- pass ``None`` only
    when no such root set is available (falls back to a bare path-heuristic
    check). ``dwarf``/``dwarf_advanced`` are filtered by the same decision
    (see :func:`_scoped_dwarf`/:func:`_scoped_dwarf_advanced`) so a later
    DWARF diff can't silently re-observe an excluded type's layout.

    A no-op (returns *snap* unchanged) when the snapshot has no
    header-derived declarations at all (:attr:`AbiSnapshot.from_headers`
    False -- a binary-only or DWARF-only dump) -- this runs by default, so
    unlike an opt-in flag it must never fail a plain invocation that has
    nothing for it to act on.

    The result is a lossy artifact: a later ``compare`` against it can only
    see what this filter kept, so comparing a scoped snapshot against one
    dumped with ``--include-system-declarations`` is not meaningful — scope both
    sides of a comparison the same way.
    """
    if not snap.from_headers:
        return snap

    # Prepared once, memoized per distinct header -- this asks per declaration,
    # twice over. `extract.dependency_header_roots` owns the cost that hoists.
    _is_dep = scoping_header_predicate(header_roots, snap.excluded_header_patterns)
    decls = snap.declarations
    kept_functions = [f for f in decls.functions if not _is_dep(f.source_header)]
    kept_variables = [v for v in decls.variables if not _is_dep(v.source_header)]
    kept_types = [t for t in snap.declarations.types if not _is_dep(t.source_header)]
    kept_enums = [e for e in snap.declarations.enums if not _is_dep(e.source_header)]

    dep_types = [t for t in snap.declarations.types if _is_dep(t.source_header)]
    dep_enums = [e for e in snap.declarations.enums if _is_dep(e.source_header)]
    if dep_types or dep_enums:
        # Direct-reference retention roots are restricted to the *public*
        # subset of kept_functions/kept_variables (kept_functions/
        # kept_variables themselves are NOT narrowed -- every non-dependency
        # declaration still stays in the final snapshot, matching this
        # function's own header-origin-only contract). A hidden/private
        # function or variable naming a dependency type in its own
        # signature must not itself cause that dependency type to be
        # retained: if that private declaration is later removed, the
        # dependency type silently drops out of the *next* scoped
        # snapshot's retention set too, and `compare` then reports a
        # spurious TYPE_REMOVED for a type whose real public-surface
        # relevance never changed (Codex review, fresh evidence). A public
        # declaration's own removal causing the same drop is not a false
        # positive by the same reasoning -- the public surface's real
        # dependency on that type has genuinely ended. Reuses
        # type_reachability._NON_PUBLIC_ORIGINS, the same public-surface
        # predicate its own directly_referenced_stdlib_types() already
        # applies for the identical reason. RecordType/EnumType have no
        # `visibility` field, but both do carry `origin` (ADR-015, schema
        # v6) -- a prior version of this comment incorrectly claimed
        # neither field existed at all and passed kept_types/kept_enums
        # through unfiltered as retention roots, so a kept type/enum whose
        # own header is private/generated/system (but which is still, by
        # this function's header-origin-only contract, retained in the
        # final snapshot) could keep an unrelated dependency type alive
        # through its own fields even though no *public* declaration
        # reaches it (Codex review, fresh evidence). Filtered on `origin`
        # alone here, since there is no `visibility` to additionally check.
        public_root_functions = [
            f
            for f in kept_functions
            if in_public_surface(f) and f.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_variables = [
            v
            for v in kept_variables
            if in_public_surface(v) and v.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_types = [
            t for t in kept_types if t.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_enums = [
            e for e in kept_enums if e.origin not in _NON_PUBLIC_ORIGINS
        ]
        directly_referenced = _directly_referenced_dependency_names(
            public_root_functions,
            public_root_variables,
            public_root_types,
            public_root_enums,
            [*dep_types, *dep_enums],
            snap.declarations.typedefs,
        )
        if directly_referenced:
            kept_types = kept_types + [
                t for t in dep_types if _candidate_identity(t) in directly_referenced
            ]
            kept_enums = kept_enums + [
                e for e in dep_enums if _candidate_identity(e) in directly_referenced
            ]

    kept_identifiers = _kept_identifiers(
        {t.name for t in kept_types} | {e.name for e in kept_enums},
        {t.qualified_name for t in kept_types if t.qualified_name}
        | {e.qualified_name for e in kept_enums if e.qualified_name},
    )
    # Gap A3: with top-level headers dropped from the parse, "no kept header
    # names this DWARF type" is not evidence it is a dependency's -- keep all
    # but the confidently-identified dependency types (never silently drop).
    deps: list[RecordType | EnumType] = [*dep_types, *dep_enums]
    dwarf_ids = (
        AllBut(
            ({_candidate_identity(t) for t in deps} | {t.name for t in deps})
            - kept_identifiers
        )
        if header_parse_excluded(snap)
        else kept_identifiers
    )
    excluded_functions = [f for f in decls.functions if _is_dep(f.source_header)]
    # A kept function's own header-AST spelling can be exactly this same
    # ambiguous shape too -- a kept `extern "C" foo` genuinely has mangled ==
    # name == "foo", and an unrelated excluded C++ dependency function can
    # independently fail to recover its own real (different) mangled name,
    # falling back to a bare spelling that happens to equal that same "foo"
    # (no ODR conflict: the two are distinct real symbols, e.g. "foo" vs
    # "_ZN3dep3fooEi" -- collision only in this unreliable *spelling*, not at
    # the linker). Trusting the excluded function's bare spelling there would
    # wrongly drop the *kept* function's own real DWARF-advanced entry
    # (Codex review, fresh evidence). Any excluded mangled spelling that also
    # matches a kept function's own *mangled* field is therefore never
    # trusted to exclude anything -- deliberately checked against
    # kept_functions' ``mangled`` only, not their bare ``name`` too: a kept
    # C++ function named e.g. "dep" with a real, different mangled key
    # (``_ZN4mine3depEv``) must not itself shadow an unrelated excluded C
    # function genuinely keyed ``"dep"`` -- their real DWARF keys don't
    # collide, so excluding the latter is still correct and safe (a second,
    # independent Codex review round, fresh evidence).
    kept_mangled = {f.mangled for f in kept_functions if f.mangled}
    excluded_symbols = {
        f.mangled
        for f in excluded_functions
        if f.mangled and f.mangled not in kept_mangled
    }
    scoped_semantic_ir, scoped_semantic_ir_conflicts = _scoped_semantic_ir(
        snap.canonical_ir,
        snap.semantic_ir_conflicts,
        kept_types,
        kept_enums,
        kept_functions,
        kept_variables,
        header_roots,
    )
    flat = scope_flat_maps(
        snap,
        _is_dep,
        kept_reference_text(
            _kept_signature_haystack(kept_functions, kept_variables, kept_types),
            kept_functions,
            kept_types,
            kept_enums,
        ),
        scoped_semantic_ir,
        scoped_semantic_ir_conflicts,
    )
    return dataclasses.replace(
        snap,
        functions=kept_functions,
        variables=kept_variables,
        types=kept_types,
        enums=kept_enums,
        dwarf=_scoped_dwarf(snap.declarations.debug_layout, dwarf_ids),
        dwarf_advanced=_scoped_dwarf_advanced(
            snap.declarations.debug_advanced, dwarf_ids, excluded_symbols
        ),
        # ADR-063 Phase 6 (second slice, Codex review, PR #1001): without
        # this, dataclasses.replace() below carries snap.semantic_ir/
        # semantic_ir_conflicts over verbatim -- every excluded dependency
        # record/enum (and any hybrid-merge conflict recorded against it)
        # stays reachable through the "filtered" snapshot's own canonical
        # IR even though the flat kept_types/kept_enums lists above
        # correctly dropped it, defeating this function's whole
        # size/surface contract for any SemanticIR-aware consumer.
        semantic_ir=flat.semantic_ir,
        semantic_ir_conflicts=flat.semantic_ir_conflicts,
        # PR #1001's omission, for the flat maps: extract/flat_map_dependency_scope.
        constants=flat.constants,
        constant_entity_ids=flat.constant_entity_ids,
        typedefs=flat.typedefs,
        typedefs_qualified=flat.typedefs_qualified,
        typedef_entity_ids=flat.typedef_entity_ids,
        # Records that this snapshot went through dependency-exclusion —
        # comparability.check_contracts_comparable uses this to refuse to
        # compare a filtered snapshot against an unfiltered one (see
        # AbiSnapshot.dependency_scope's own docstring).
        dependency_scope="filtered",
        # dataclasses.replace() otherwise carries these lazy lookup-index
        # caches over from *snap* verbatim: if the input snapshot's index()
        # was already called (e.g. by an earlier pipeline step), the copy
        # would keep pointing at the unscoped functions/types lists even
        # though its own .functions/.types are now filtered, so
        # func_by_mangled()/type_by_name() on the *returned* snapshot could
        # resolve a declaration this scoping just dropped.
        # None forces a lazy rebuild from the scoped lists on next access.
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )
