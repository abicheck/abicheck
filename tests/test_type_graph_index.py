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


"""Declaration-index predicates for the type/reference graph (ADR-041 P0).

Unit tests for the anonymous-tag predicates in
:mod:`~abicheck.buildsource.type_graph_index`: whether a node's id linkage
names an anonymous tag, and whether a declarator spells one. Their end-to-end
effect on emitted edges is pinned in ``test_type_graph_roles.py``.
"""

from __future__ import annotations

from abicheck.buildsource.type_graph_index import (
    _anonymous_tag_id_referenced,
    _declares_anonymous_enum_type,
)


def test_anonymous_tag_id_referenced_matches_via_nested_decl_fallback() -> None:
    """The two id-linkage shapes clang can carry (module docstring: "the two
    can nest either directly or one inside the other depending on clang's
    own version") -- a top-level ``ownedTagDecl.id`` (the shape every other
    fixture here uses) versus a nested ``EnumType.decl.id`` with no
    ``ownedTagDecl`` at the outer level at all."""
    assert _anonymous_tag_id_referenced(
        {
            "kind": "ElaboratedType",
            "inner": [{"kind": "EnumType", "decl": {"id": "0x1", "kind": "EnumDecl"}}],
        },
        "0x1",
    )


def test_anonymous_tag_id_referenced_rejects_non_dict_and_non_matching_input() -> None:
    """Defensive guards: a non-dict node (malformed AST) and a node with
    neither an ``ownedTagDecl`` nor a ``decl`` id match anywhere in its
    subtree both correctly report no match."""
    assert not _anonymous_tag_id_referenced("not a dict", "0x1")
    assert not _anonymous_tag_id_referenced(
        {"kind": "ElaboratedType", "inner": [{"kind": "EnumType"}]}, "0x1"
    )


def test_declares_anonymous_enum_type_rejects_missing_or_non_dict_type() -> None:
    """A node with no ``type`` key at all, or a non-dict ``type`` value, is
    not an anonymous-enum declarator -- distinct from a node whose ``type``
    is a dict that simply doesn't carry the marker string."""
    assert not _declares_anonymous_enum_type({"kind": "FieldDecl", "name": "x"})
    assert not _declares_anonymous_enum_type(
        {"kind": "FieldDecl", "name": "x", "type": "not a dict"}
    )
    assert not _declares_anonymous_enum_type(
        {"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}
    )
