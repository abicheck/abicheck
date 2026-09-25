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

"""The one entry point every graph producer uses to build a real
``AbiSnapshot``'s identity table (evidence-entity-model invariant I1).

``model.graph_entity_identity.snapshot_identities`` stays a leaf -- it never
imports ``model.snapshot`` -- so the linker-name evidence its ctor/dtor
variant rule reads is supplied here, once, for every producer: the L2 header
graph, the public-surface builder and the ``exports``/``debug_type_of``
joins all call :func:`identities_for_snapshot` and therefore agree on every
node id.

**Two evidence sources, and why both.** The export table is one; the other
is the clang header AST the header graph is built from, which reports every
non-template constructor's/destructor's complete-object mangling whether or
not the binary exports it (``buildsource.ast_special_members``). The AST
evidence comes from the *headers*, so it is the same in two versions whose
declaration did not change; export evidence alone is not -- a release that
starts exporting ``C2`` of an unchanged constructor would otherwise flip
that constructor's persisted graph node from ``unresolved://`` to
``decl://``, and the graph diff would read the unchanged declaration as
having entered the public closure (oneDAL 2025.10 -> 2025.11).

The AST exists only while the header graph is built. A stored snapshot's
table is rebuilt from the export table alone -- reading the lazily-decoded
persisted graph back for its names would force every binary-depth compare
to decode it -- so the header graph records, as an identity alias, the
export-only spelling of every member the AST alone resolved. A graph reader
then lands on the same node; a non-graph reader (the ``exports`` join) keys
such a member on its placeholder, which is correct for it: no export means
the join has nothing to match either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .export_index import snapshot_export_names
from .graph_entity_identity import SnapshotIdentities, snapshot_identities
from .mangled_name import itanium_ctor_dtor_marker_span

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = [
    "ast_special_member_names",
    "identities_for_snapshot",
]

_DECL_PREFIX = "decl://"


def ast_special_member_names(keys: Iterable[str]) -> frozenset[str]:
    """The Itanium ctor/dtor linker names among *keys* (declaration keys
    from the clang AST, with or without the ``decl://`` prefix)."""
    out: set[str] = set()
    for key in keys:
        name = key[len(_DECL_PREFIX) :] if key.startswith(_DECL_PREFIX) else key
        if itanium_ctor_dtor_marker_span(name) is not None:
            out.add(name)
    return frozenset(out)


def identities_for_snapshot(
    snap: AbiSnapshot, *, ast_names: Iterable[str] = ()
) -> SnapshotIdentities:
    """:func:`snapshot_identities` with the snapshot's export tables, plus
    the header AST's ctor/dtor names when the caller holds the AST (the
    header graph build). A pure function of its arguments: it never reads
    the lazily-decoded persisted graph."""
    return snapshot_identities(
        snap,
        export_names=snapshot_export_names(snap) | ast_special_member_names(ast_names),
    )
