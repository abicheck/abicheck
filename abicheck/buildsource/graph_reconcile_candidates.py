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

"""Which graph nodes may ever be a rename/move candidate (ADR-048 D2).

:mod:`.graph_reconcile` pairs a removed node with an added one only when both
are candidates. A candidate is a user-declared entity: a declarable kind, and
a name that is not a fundamental type. Nothing here compares two nodes; the
match tiers in :mod:`.graph_reconcile` own that.
"""

from __future__ import annotations

from ..model.fundamental_type import is_fundamental_type_spelling
from ..model.graph_facts import GraphNode

#: Node kinds a rename/move reconciliation applies to — declarations and
#: types (the same set ``source_graph.DECL_NODE_KINDS`` already uses for
#: visibility classification); file/build/link-graph nodes are structural
#: build facts, not "the same entity renamed" candidates.
RECONCILABLE_KINDS: frozenset[str] = frozenset(
    {"source_decl", "record_type", "enum_type", "typedef"}
)


#: Layout facts a record node carries when the header graph recorded them.
_LAYOUT_ATTRS: tuple[str, ...] = ("size_bits", "alignment_bits")


def layout_contradicts(old: GraphNode, new: GraphNode) -> bool:
    """Whether two nodes' recorded layouts differ, so they cannot be one declaration.

    A rename or move never changes a record's size or alignment. Only a fact both
    sides recorded is compared: a layout one side did not collect proves nothing.
    """
    for attr in _LAYOUT_ATTRS:
        old_value = old.attrs.get(attr)
        new_value = new.attrs.get(attr)
        if old_value is not None and new_value is not None and old_value != new_value:
            return True
    return False


def is_reconcilable_candidate(node: GraphNode) -> bool:
    """Whether *node* can ever be a rename/move candidate.

    Two gates, both required. The kind must be a user-declarable kind
    (:data:`RECONCILABLE_KINDS`). And the node must name a user-declared
    entity, not a fundamental type: ``int``, ``float _Complex``, ``__int128``,
    ``const char *`` and their vector/SIMD forms have no declaration and no
    declaring file. A structural-context tier would otherwise pair any two of
    them that sit in the same parent position (``_Complex float`` ->
    ``_Complex double`` as a parameter of the same function), and report that
    as "the same declaration, renamed" -- a different size called a rename.
    The check reads the spelling the node is labelled and qualified by, since
    the producer stamps no declaration attribute on a builtin node.
    """
    if node.kind not in RECONCILABLE_KINDS:
        return False
    for spelling in (node.label, node.attrs.get("qualified_name")):
        if isinstance(spelling, str) and is_fundamental_type_spelling(spelling):
            return False
    return True
