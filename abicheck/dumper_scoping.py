# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Dump-time dependency scoping (``dump --include-dependencies`` opt-out).

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
requiring a ``--public-header``/``--public-header-dir`` set:
``AbiSnapshot.source_header`` is populated unconditionally by
``provenance.apply_provenance``. ``dump --include-dependencies`` opts out
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
``--include-dependencies`` on both sides of a comparison if that detection
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
semantic header-only graph (`snap.build_source.source_graph`, a
`buildsource.source_graph.SourceGraphSummary`) built from the *same*
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
from collections.abc import Sequence
from pathlib import Path

from .dwarf_advanced import AdvancedDwarfMetadata
from .dwarf_metadata import DwarfMetadata
from .model import AbiSnapshot, EnumType, Function, RecordType, Variable
from .provenance import is_dependency_header
from .type_reachability import type_string_references_name


def _kept_identifiers(names: set[str], qualified_names: set[str]) -> set[str]:
    return names | qualified_names


def _candidate_identity(candidate: RecordType | EnumType) -> str:
    """The most specific spelling identifying *candidate*: its fully-qualified
    name when the producer populated one, else its bare ``name``."""
    return getattr(candidate, "qualified_name", None) or candidate.name


def _directly_referenced_dependency_names(
    kept_functions: Sequence[Function],
    kept_variables: Sequence[Variable],
    kept_types: Sequence[RecordType],
    dep_candidates: Sequence[RecordType | EnumType],
    typedefs: dict[str, str] | None = None,
) -> set[str]:
    """Which *dep_candidates* (dependency-header types/enums about to be
    dropped) are directly named by a kept, non-dependency declaration's own
    signature -- i.e. reachable at distance one from what
    :func:`scope_snapshot_excluding_dependencies` is keeping anyway, as
    opposed to only reachable transitively through another dependency
    type's own internals. Mirrors
    :func:`abicheck.type_reachability.directly_referenced_stdlib_types`'s
    direct-vs-transitive distinction, but generalized to any dependency
    header (not stdlib-namespace-prefixed only -- e.g. ``struct tm`` from
    ``<time.h>``) since dump-time scoping excludes by header origin, not by
    namespace.

    Deliberately single-hop: only the kept, already-retained declarations'
    own signatures are searched, never a *dependency* candidate's own
    fields/bases -- chasing further would re-admit the transitive
    implementation closure (e.g. ``std::string``'s own
    ``_Alloc_hider`` field) this scoping exists to drop.

    Returns each retained candidate's :func:`_candidate_identity` (not its
    bare ``name``): two dependency candidates can share the same bare name
    under different fully-qualified identities (``std::Thing`` vs.
    ``vendor::Thing``), and returning bare names would let one's match
    re-admit the other's unrelated layout (Codex review). For the same
    reason, a candidate's bare ``name`` is only used as a matching spelling
    when it is unique among *dep_candidates* -- an ambiguous bare name is
    not trusted to mean any one of them.

    *typedefs* (``AbiSnapshot.typedefs``, alias -> underlying-type spelling)
    is consulted so a dependency type only reachable through a typedef
    alias in the kept signatures (e.g. a signature spells ``std::string``
    while the record's own identity is the underlying
    ``std::__cxx11::basic_string<...>``) is still recognized (Codex review)
    -- mirrors :func:`abicheck.type_reachability`'s own typedef-following.
    """
    signature_texts: list[str] = []
    for fn in kept_functions:
        signature_texts.append(fn.return_type)
        signature_texts.extend(p.type for p in fn.params)
    for var in kept_variables:
        signature_texts.append(var.type)
    for rec in kept_types:
        signature_texts.extend(f.type for f in rec.fields)
        signature_texts.extend(rec.bases)
        signature_texts.extend(rec.virtual_bases)
    haystack = "\n".join(t for t in signature_texts if t)

    aliases_by_target: dict[str, list[str]] = {}
    for alias, target in (typedefs or {}).items():
        aliases_by_target.setdefault(target, []).append(alias)

    bare_name_identities: dict[str, set[str]] = {}
    for candidate in dep_candidates:
        bare_name_identities.setdefault(candidate.name, set()).add(
            _candidate_identity(candidate)
        )

    referenced: set[str] = set()
    for candidate in dep_candidates:
        identity = _candidate_identity(candidate)
        qualified_name = getattr(candidate, "qualified_name", None)
        spellings: set[str] = set()
        if qualified_name:
            spellings.add(qualified_name)
        if len(bare_name_identities[candidate.name]) == 1:
            spellings.add(candidate.name)
        for key in (identity, candidate.name):
            spellings.update(aliases_by_target.get(key, ()))

        for spelling in spellings:
            # Cheap substring pre-check (fast C-level scan) before paying
            # for the boundary-aware regex-equivalent match -- most
            # candidates never appear in the haystack at all.
            if spelling in haystack and type_string_references_name(haystack, spelling):
                referenced.add(identity)
                break
    return referenced


def _name_matches(name: str, kept_identifiers: set[str]) -> bool:
    """Exact-only match against the kept types'/enums' own spellings.

    Deliberately does **not** fall back to bare-tail matching (a DWARF key
    ``ns::Foo`` reducing to a bare ``Foo`` and comparing against a kept
    type's bare name): two distinct types sharing a leaf name -- a kept
    ``mine::Thing`` and an excluded ``std::Thing`` -- would otherwise both
    satisfy a tail match against the single bare name ``"Thing"``, letting
    the excluded dependency type's DWARF/DWARF-advanced entry survive the
    filter under its own qualified spelling even though the flat type list
    correctly dropped it (Codex review). ``kept_identifiers`` already
    carries both the bare ``name`` and (when present) the fully-qualified
    ``qualified_name`` of every *kept* type/enum, so an exact match still
    succeeds whichever form a real DWARF/castxml backend spells the same
    kept entity with -- only an actually-ambiguous bare-vs-qualified
    mismatch with no qualified_name recorded at all is missed, the same
    conservative "only drop what's confidently identified" bias the rest
    of this module already uses.
    """
    return name in kept_identifiers


def _scoped_dwarf(
    dwarf: DwarfMetadata | None, kept_identifiers: set[str]
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
    )


def _scoped_dwarf_advanced(
    adv: AdvancedDwarfMetadata | None,
    kept_identifiers: set[str],
    kept_symbols: set[str],
) -> AdvancedDwarfMetadata | None:
    """Filter Sprint-4 advanced DWARF metadata the same way: type-keyed
    collections (``packed_structs``/``all_struct_names``) via
    :func:`_name_matches`, function-keyed collections (keyed by mangled
    ``linkage_name``) via *kept_symbols*."""
    if adv is None or not adv.has_dwarf:
        return adv
    return dataclasses.replace(
        adv,
        calling_conventions={
            k: v for k, v in adv.calling_conventions.items() if k in kept_symbols
        },
        value_abi_traits={
            k: v for k, v in adv.value_abi_traits.items() if k in kept_symbols
        },
        return_value_sizes={
            k: v for k, v in adv.return_value_sizes.items() if k in kept_symbols
        },
        return_memory_classified={
            k for k in adv.return_memory_classified if k in kept_symbols
        },
        packed_structs={
            k for k in adv.packed_structs if _name_matches(k, kept_identifiers)
        },
        all_struct_names={
            k for k in adv.all_struct_names if _name_matches(k, kept_identifiers)
        },
        frame_registers={
            k: v for k, v in adv.frame_registers.items() if k in kept_symbols
        },
        callee_saved_regs={
            k: v for k, v in adv.callee_saved_regs.items() if k in kept_symbols
        },
    )


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
    dumped with ``--include-dependencies`` is not meaningful — scope both
    sides of a comparison the same way.
    """
    if not snap.from_headers:
        return snap

    def _is_dep(source_header: str | None) -> bool:
        return is_dependency_header(source_header, header_roots)

    kept_functions = [f for f in snap.functions if not _is_dep(f.source_header)]
    kept_variables = [v for v in snap.variables if not _is_dep(v.source_header)]
    kept_types = [t for t in snap.types if not _is_dep(t.source_header)]
    kept_enums = [e for e in snap.enums if not _is_dep(e.source_header)]

    dep_types = [t for t in snap.types if _is_dep(t.source_header)]
    dep_enums = [e for e in snap.enums if _is_dep(e.source_header)]
    if dep_types or dep_enums:
        directly_referenced = _directly_referenced_dependency_names(
            kept_functions,
            kept_variables,
            kept_types,
            [*dep_types, *dep_enums],
            snap.typedefs,
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
    kept_symbols = {f.mangled for f in kept_functions if f.mangled}
    return dataclasses.replace(
        snap,
        functions=kept_functions,
        variables=kept_variables,
        types=kept_types,
        enums=kept_enums,
        dwarf=_scoped_dwarf(snap.dwarf, kept_identifiers),
        dwarf_advanced=_scoped_dwarf_advanced(
            snap.dwarf_advanced, kept_identifiers, kept_symbols
        ),
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
