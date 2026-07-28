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
excluded. This applies **by default**, without requiring a
``--public-header``/``--public-header-dir`` set: ``AbiSnapshot.source_header``
is populated unconditionally by ``provenance.apply_provenance`` (only the
PUBLIC_HEADER/PRIVATE_HEADER *classification* is opt-in — see
``provenance.is_system_header``'s docstring), so "is this declaration's own
header a system header" needs no public-header input at all. ``dump
--include-dependencies`` opts out and writes the full, unscoped snapshot
(the old default).

Because this scopes by header origin rather than ABI visibility, it is a
silent no-op (not an error) on a snapshot with no header-derived
declarations at all (a binary-only/DWARF-only dump) -- unlike an opt-in
flag, default-on behavior must never fail a plain ``dump`` invocation that
has nothing for it to act on.

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

from .dwarf_advanced import AdvancedDwarfMetadata
from .dwarf_metadata import DwarfMetadata
from .model import AbiSnapshot
from .provenance import is_system_header


def _name_matches(name: str, kept_type_names: set[str]) -> bool:
    # A DWARF struct/enum key (or a record's own identity) may be
    # namespace-qualified while the header-derived kept-type-name set may
    # only carry the bare tail, or vice versa -- match either form.
    return name in kept_type_names or name.split("::")[-1] in kept_type_names


def _scoped_dwarf(
    dwarf: DwarfMetadata | None, kept_type_names: set[str]
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
            k: v for k, v in dwarf.structs.items() if _name_matches(k, kept_type_names)
        },
        enums={
            k: v for k, v in dwarf.enums.items() if _name_matches(k, kept_type_names)
        },
    )


def _scoped_dwarf_advanced(
    adv: AdvancedDwarfMetadata | None,
    kept_type_names: set[str],
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
            k for k in adv.packed_structs if _name_matches(k, kept_type_names)
        },
        all_struct_names={
            k for k in adv.all_struct_names if _name_matches(k, kept_type_names)
        },
        frame_registers={
            k: v for k, v in adv.frame_registers.items() if k in kept_symbols
        },
        callee_saved_regs={
            k: v for k, v in adv.callee_saved_regs.items() if k in kept_symbols
        },
    )


def scope_snapshot_excluding_dependencies(snap: AbiSnapshot) -> AbiSnapshot:
    """Return a copy of *snap* with toolchain/system-header declarations
    dropped, keeping everything that belongs to the library itself.

    Keeps a function/variable/type/enum unless its own ``source_header`` is
    a toolchain/system header (see :func:`provenance.is_system_header`) --
    this is a header-*origin* filter, not an ABI-visibility one: a private,
    non-exported declaration from the library's own headers is kept exactly
    like a public one, only dependency-header declarations are dropped.
    ``dwarf``/``dwarf_advanced`` are filtered by the same decision (see
    :func:`_scoped_dwarf`/:func:`_scoped_dwarf_advanced`) so a later DWARF
    diff can't silently re-observe an excluded type's layout.

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
    kept_functions = [f for f in snap.functions if not is_system_header(f.source_header)]
    kept_variables = [v for v in snap.variables if not is_system_header(v.source_header)]
    kept_types = [t for t in snap.types if not is_system_header(t.source_header)]
    kept_enums = [e for e in snap.enums if not is_system_header(e.source_header)]
    kept_type_names = {t.name for t in kept_types} | {e.name for e in kept_enums}
    kept_symbols = {f.mangled for f in kept_functions if f.mangled}
    return dataclasses.replace(
        snap,
        functions=kept_functions,
        variables=kept_variables,
        types=kept_types,
        enums=kept_enums,
        dwarf=_scoped_dwarf(snap.dwarf, kept_type_names),
        dwarf_advanced=_scoped_dwarf_advanced(snap.dwarf_advanced, kept_type_names, kept_symbols),
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
