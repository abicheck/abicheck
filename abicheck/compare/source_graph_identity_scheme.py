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

"""Whether two stored source graphs can be diffed node by node
(evidence-entity-model Phase 1, invariant I1).

The graph-layer counterpart of ``comparability.py``'s snapshot contract, kept
in its own leaf module: it answers one question about the L5/header graph's
identity scheme, not the header-declared-surface/compile-context contract
that module owns, and it must not refuse the rest of a comparison.
"""

from __future__ import annotations

from ..model.source_graph import GRAPH_IDENTITY_SCHEME_VERSION


def source_graph_identity_mismatch(old_version: int, new_version: int) -> str | None:
    """Why two L5/header source graphs cannot be diffed node-by-node, or
    ``None`` when they can (evidence-entity-model Phase 1, invariant I1).

    Graphs written before ``SOURCE_GRAPH_VERSION`` 3 key some entities under
    ids the I1 identity function no longer produces (a C-linkage function as
    ``qualified#signature``, a flat-path type by its bare leaf, a castxml
    constructor placeholder as ``decl://``). Diffing such a graph against a
    v3 one would read every renamed node as removed-and-added. The old ids
    cannot be rewritten on load without evidence the stored graph does not
    carry (the AST ``qualType`` behind a signature hash, or which scope a
    bare-leaf node stood for), so the L5 layer of such a pair is reported
    *not compared* instead -- never silently mismatched. Two graphs on the
    same side of the boundary compare as before. Re-dumping the older side
    restores the comparison.
    """
    v3 = GRAPH_IDENTITY_SCHEME_VERSION
    if (old_version >= v3) == (new_version >= v3):
        return None
    return (
        f"source graph not compared: identity scheme v{old_version} vs "
        f"v{new_version} (graphs before v{v3} use pre-I1 node ids; re-dump "
        "the older side to compare)"
    )
