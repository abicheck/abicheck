# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Dump-time public-surface scoping (``dump --public-surface-only``).

``surface.compute_public_surface()`` already computes the transitive-closure
public ABI surface of a snapshot (public functions/variables plus every type
reachable from their signatures/fields/bases) — but until now that closure was
only ever applied at *compare* time, to demote findings. A full ``dump``
serializes every declaration the header AST parser saw, including the entire
transitive dependency surface pulled in by ``#include`` (every libstdc++/SYCL
internal, whether or not the library's own public API ever references it) —
for a library with a large or heavily-templated dependency stack this can put
the snapshot JSON in the hundreds-of-MB range, most of which is dependency
surface no consumer's public API reaches.

This module reuses the same closure to filter what a dump *writes*, instead of
computing a second, parallel notion of "public". A type reachable from the
public API (including a `std::`/SYCL type actually named in a public
signature or field — dropping those would blind layout-based detectors to a
real ABI break in a used dependency type) is kept; anything the public API
never reaches is not.

**Known limitation (Codex review, investigated, deliberately not fixed
here):** this filters the flat snapshot lists (`functions`/`variables`/
`types`/`enums`/`typedefs`) only. `service._attach_header_graph` (G29 Phase A,
always-on by default -- `_HEADER_GRAPH_ENABLED`) separately embeds a semantic
header-only graph (`snap.build_source.source_graph`, a
`buildsource.source_graph.SourceGraphSummary`) built from the *same*
unscoped header AST, and `scope_snapshot_to_public_surface` leaves it
untouched -- a `--public-surface-only` dump can therefore still carry graph
nodes/edges for dependency declarations the flat-list scoping just dropped.
Correctly scoping the graph too needs its own closure walk over
`GraphNode`/`GraphEdge` (each carrying its own `facts`/`resolved`/
`conflicts`/`provenance`/`confidence` evidence-merge state -- ADR-046 D2),
handling dangling edges when a node is removed, without corrupting a
legitimate real L3/L4/L5 collection merged into the same pack from an
explicit `--sources`/`--build-info` alongside this flag. That's a separate,
independently-scoped project, not a drive-by extension of this flag's flat
public-surface closure.
"""

from __future__ import annotations

import dataclasses

from .dwarf_metadata import DwarfMetadata
from .errors import ValidationError
from .model import AbiSnapshot, Visibility
from .surface import PublicSurface, compute_public_surface


def _keep_dwarf_name(name: str, public_types: set[str]) -> bool:
    # Mirrors diff_platform._diff_dwarf's own _allow_name: a DWARF struct/enum
    # key may be namespace-qualified while surface.public_types also carries
    # the bare tail (surface._index_surface_types indexes both).
    return name in public_types or name.split("::")[-1] in public_types


def _scoped_dwarf(
    dwarf: DwarfMetadata | None, surface: PublicSurface
) -> DwarfMetadata | None:
    """Filter a DWARF layout map to the same public-surface closure.

    Without this, an ELF snapshot with both a header model and DWARF debug
    info can scope its flat ``types``/``enums`` lists down to nothing (e.g. a
    public API that only uses primitive types) while leaving ``snap.dwarf``
    untouched -- ``diff_platform._diff_dwarf`` treats an empty
    header-model-derived allow-set as "no header model at all" and falls back
    to comparing *every* DWARF struct/enum, including private ones the
    scoping was supposed to exclude. That falls straight back into a false
    ``struct_size_changed``/etc. finding on an unreachable private type, even
    between two ``--public-surface-only`` snapshots (Codex review). Filtering
    ``dwarf.structs``/``dwarf.enums`` here the same way keeps that allow-set
    genuinely empty end to end, instead of behaving like DWARF-only mode.
    """
    if dwarf is None or not dwarf.has_dwarf:
        return dwarf
    return dataclasses.replace(
        dwarf,
        structs={
            k: v
            for k, v in dwarf.structs.items()
            if _keep_dwarf_name(k, surface.public_types)
        },
        enums={
            k: v
            for k, v in dwarf.enums.items()
            if _keep_dwarf_name(k, surface.public_types)
        },
    )


class PublicSurfaceScopingError(ValidationError):
    """Raised when ``--public-surface-only`` is requested but the snapshot has
    no header-AST-derived declarations to scope (no ``-H``/``--header`` at
    all, or a DWARF-only/export-table-only dump)."""


def scope_snapshot_to_public_surface(snap: AbiSnapshot) -> AbiSnapshot:
    """Return a copy of *snap* containing only its public ABI surface.

    Keeps: functions/variables with :data:`Visibility.PUBLIC`, and every
    record/enum/typedef in the transitive closure reachable from their
    signatures (per :func:`surface.compute_public_surface`). Drops
    unreferenced dependency internals (unused stdlib/SYCL/etc. declarations)
    that a full header-AST dump otherwise serializes wholesale. When the
    snapshot also carries DWARF (an ELF dump with both a header model and
    debug info), ``dwarf.structs``/``dwarf.enums`` are filtered by the same
    closure (see :func:`_scoped_dwarf`) — leaving them unfiltered would let
    ``diff_platform._diff_dwarf``'s own empty-allow-set fallback silently
    re-expand to comparing every DWARF type, defeating the scoping (Codex
    review).

    Raises :class:`PublicSurfaceScopingError` if the snapshot has no
    resolvable public surface at all (see
    :attr:`surface.PublicSurface.resolvable`), **or** if it was not parsed
    from headers at all (:attr:`AbiSnapshot.from_headers` False) — scoping an
    export-table-only dump would silently drop everything, which is never the
    caller's intent, and gating on ``resolvable`` alone is not enough: a
    DWARF-derived dump (no ``-H`` at all, e.g. the auto-DWARF-fallback path or
    ``--dwarf-only``) can carry ``Visibility.PUBLIC`` declarations with full
    type layout from debug info, making ``resolvable`` True even though this
    flag's whole premise — a full header-AST dump pulling in unreferenced
    transitive ``#include`` dependency declarations — does not apply to it at
    all (Codex review).

    The result is a lossy artifact: a later ``compare`` against it can only
    see what this closure kept, so comparing a scoped snapshot against an
    unscoped one (or against a snapshot scoped from a differently-shaped
    public surface) is not meaningful — scope both sides of a comparison the
    same way.
    """
    surface = compute_public_surface(snap)
    if not surface.resolvable or not snap.from_headers:
        raise PublicSurfaceScopingError(
            "--public-surface-only requires a header-AST-derived snapshot "
            "(-H/--header, or --dump-manifest); a DWARF-only, "
            "export-table-only, or source-only dump has no header-parsed "
            "declarations to scope."
        )
    return dataclasses.replace(
        snap,
        functions=[f for f in snap.functions if f.visibility == Visibility.PUBLIC],
        variables=[v for v in snap.variables if v.visibility == Visibility.PUBLIC],
        types=[t for t in snap.types if t.name in surface.public_types],
        enums=[e for e in snap.enums if e.name in surface.public_types],
        typedefs={
            k: v for k, v in snap.typedefs.items() if k in surface.public_typedefs
        },
        dwarf=_scoped_dwarf(snap.dwarf, surface),
        # dataclasses.replace() otherwise carries these lazy lookup-index
        # caches over from *snap* verbatim: if the input snapshot's index()
        # was already called (e.g. by an earlier pipeline step), the copy
        # would keep pointing at the unscoped functions/types lists even
        # though its own .functions/.types are now filtered, so
        # func_by_mangled()/type_by_name() on the *returned* snapshot could
        # resolve a declaration this scoping just dropped (Codex review).
        # None forces a lazy rebuild from the scoped lists on next access.
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )
