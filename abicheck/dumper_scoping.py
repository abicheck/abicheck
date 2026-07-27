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
"""

from __future__ import annotations

import dataclasses

from .errors import ValidationError
from .model import AbiSnapshot, Visibility
from .surface import compute_public_surface


class PublicSurfaceScopingError(ValidationError):
    """Raised when ``--public-surface-only`` is requested but the snapshot's
    public surface cannot be resolved (no header-derived public symbols —
    e.g. a binary-only/ELF-only dump with no ``-H``/``--header`` at all)."""


def scope_snapshot_to_public_surface(snap: AbiSnapshot) -> AbiSnapshot:
    """Return a copy of *snap* containing only its public ABI surface.

    Keeps: functions/variables with :data:`Visibility.PUBLIC`, and every
    record/enum/typedef in the transitive closure reachable from their
    signatures (per :func:`surface.compute_public_surface`). Drops
    unreferenced dependency internals (unused stdlib/SYCL/etc. declarations)
    that a full header-AST dump otherwise serializes wholesale.

    Raises :class:`PublicSurfaceScopingError` if the snapshot has no
    resolvable public surface at all (see
    :attr:`surface.PublicSurface.resolvable`) — scoping an export-table-only
    dump would silently drop everything, which is never the caller's intent.

    The result is a lossy artifact: a later ``compare`` against it can only
    see what this closure kept, so comparing a scoped snapshot against an
    unscoped one (or against a snapshot scoped from a differently-shaped
    public surface) is not meaningful — scope both sides of a comparison the
    same way.
    """
    surface = compute_public_surface(snap)
    if not surface.resolvable:
        raise PublicSurfaceScopingError(
            "--public-surface-only requires a resolvable public surface "
            "(pass -H/--header so functions/variables are parsed with "
            "header-derived visibility; an export-table-only/ELF-only dump "
            "has nothing to scope from)."
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
    )
