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

"""Which include roots carry public *ownership*, and which are only compile
context.

``-I`` answers "where may the parser search for an ``#include``". It does
not answer "which declarations does this library own and promise to
export". Conflating the two made every declaration under a *dependency's*
include directory an export obligation of the library that merely needed to
parse its header -- Intel MKL passes an MPI include directory solely so
``mkl_cdft.h`` can parse ``#include <mpi.h>``, and its report carried 2,211
``public_not_exported`` findings about an API it does not own.

A leaf of its own rather than a branch inside ``provenance.py``, because
two independent consumers must reach the identical answer: per-declaration
provenance (``provenance.apply_provenance``) and header-level graph node
classification (``buildsource.header_graph``). When those disagreed, a
transitively-included header classified one way for its declarations and
the other way for its own node.

Deliberately pure, over already-segmented paths: it imports nothing, so
``provenance`` can depend on it without a cycle. Turning a raw ``-I`` root
into segments, and dropping a bare system prefix, stay in ``provenance``
next to the rest of that module's path-segmentation vocabulary.
"""

from __future__ import annotations


def roots_a_declared_public_surface(
    dir_seg: tuple[str, ...],
    declared_segs: list[tuple[str, ...]],
) -> bool:
    """Whether *dir_seg* is an ancestor of (or equal to) a declared public root.

    The structural test that separates the two concepts an ``-I`` root used
    to conflate. ``-I include`` alongside ``-H include/api.h`` names the
    library's *own* public include root: the declared public surface lives
    underneath it, so a header ``api.h`` pulls in from ``include/detail/``
    is part of that same declared tree rather than a private implementation
    detail that merely happens to be reachable. ``-I /opt/mpi/include``
    alongside the same ``-H`` names no declared public root at all -- it is
    there purely so the compiler can resolve ``#include <mpi.h>``, and
    nothing about it says MKL promises to export ``MPI_Init``.
    """
    return any(
        len(dir_seg) <= len(declared) and declared[: len(dir_seg)] == dir_seg
        for declared in declared_segs
    )


def retain_owning_roots(
    root_segs: list[tuple[str, ...]],
    declared_segs: list[tuple[str, ...]] | None,
) -> list[tuple[str, ...]]:
    """*root_segs* minus every root that owns no declared public surface.

    "The compiler needed this directory to resolve an ``#include``" is not
    "the library owns every declaration in it", and conflating the two is
    what made a *dependency's* include directory into a blanket public-API
    root: Intel MKL passes an MPI include directory solely so
    ``mkl_cdft.h`` can parse ``#include <mpi.h>``, and every ``MPI_*``/
    ``PMPI_*``/``QMPIX_*`` declaration underneath it became an MKL export
    obligation -- 2,211 ``public_not_exported`` findings about an API MKL
    does not own and never promised to export.

    The containment test is the whole rule, and it is deliberately
    structural rather than a filter on names, paths, or prefixes: nothing
    here knows about MPI, ``mpi.h``, or any vendor. An ``-I`` root keeps its
    widening power exactly when the run's own declared public headers live
    underneath it -- which is what the transitively-reached-header fix this
    widening was originally added for always relied on anyway (``-I
    include`` with ``-H include/api.h``), and is precisely what a dependency
    search path does not satisfy.

    A declaration under a dropped root is still parsed, still in the
    snapshot, and still reachable through type closure when an owned public
    declaration references its types -- it simply stops being an independent
    public-surface obligation of its own.

    *declared_segs* of ``None`` keeps the pre-containment behavior, for a
    caller with no declared set to test against.
    """
    if declared_segs is None:
        return root_segs
    return [s for s in root_segs if roots_a_declared_public_surface(s, declared_segs)]
