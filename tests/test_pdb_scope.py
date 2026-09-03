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

"""``extract.pdb_scope`` -- PDB's own ``ScopePath``/``EntityId`` construction
from a flat, ``"::"``-qualified CodeView type name (ADR-063 Phase 6, PDB
EntityId slice, types only). No real MSVC toolchain is available in this
environment (see the module's own docstring) -- these are hand-built
qualified-name-string inputs, the identical "no compiler needed" pattern
``tests/test_pdb_parser.py``'s synthetic byte-stream fixtures already
establish for this backend, applied one layer up (post-parse names rather
than raw TPI bytes).
"""

from __future__ import annotations

from abicheck.extract.pdb_scope import (
    enum_entity_id,
    record_entity_id,
    scope_path_for_qualified_name,
)
from abicheck.model.identity import EntityKind, Namespace, Record


def test_bare_name_has_no_scope() -> None:
    scope, leaf = scope_path_for_qualified_name("Widget", frozenset())
    assert scope == ()
    assert leaf == "Widget"


def test_unrecognized_prefix_defaults_to_namespace() -> None:
    """Nothing in *known_record_names* says "NS" is a class, so it falls
    back to the (far more common) namespace reading."""
    scope, leaf = scope_path_for_qualified_name("NS::Widget", frozenset())
    assert scope == (Namespace("NS"),)
    assert leaf == "Widget"


def test_recognized_prefix_is_a_record() -> None:
    scope, leaf = scope_path_for_qualified_name("Outer::Inner", frozenset({"Outer"}))
    assert scope == (Record("Outer", "public"),)
    assert leaf == "Inner"


def test_mixed_namespace_and_record_chain() -> None:
    """ "NS::Outer::Inner::leaf" -- NS is an ordinary namespace, Outer and
    Inner are both known records; each accumulated prefix is checked
    independently, not just the immediate parent."""
    known = frozenset({"NS::Outer", "NS::Outer::Inner"})
    scope, leaf = scope_path_for_qualified_name("NS::Outer::Inner::leaf", known)
    assert scope == (
        Namespace("NS"),
        Record("Outer", "public"),
        Record("Inner", "public"),
    )
    assert leaf == "leaf"


def test_a_record_named_the_same_as_an_unrelated_namespace_prefix_is_not_confused() -> (
    None
):
    """The membership check is against the FULL accumulated prefix, not
    just the bare segment name -- an unrelated top-level struct named
    "Outer" existing somewhere else must not make a DIFFERENT "NS::Outer"
    segment misclassify as a record when only the bare "Outer" (not
    "NS::Outer") is in known_record_names."""
    scope, leaf = scope_path_for_qualified_name(
        "NS::Outer::Inner", frozenset({"Outer"})
    )
    assert scope == (Namespace("NS"), Namespace("Outer"))
    assert leaf == "Inner"


def test_template_arguments_stay_attached_to_their_own_segment() -> None:
    scope, leaf = scope_path_for_qualified_name(
        "ns::Vector<int>::iterator", frozenset()
    )
    assert scope == (Namespace("ns"), Namespace("Vector<int>"))
    assert leaf == "iterator"


def test_record_entity_id_uses_the_leaf_as_its_own_name() -> None:
    entity_id = record_entity_id("NS::Widget", frozenset())
    assert entity_id.kind is EntityKind.TYPE
    assert entity_id.leaf_name == "Widget"
    assert entity_id.scope == (Namespace("NS"),)


def test_enum_entity_id_uses_the_leaf_as_its_own_name() -> None:
    entity_id = enum_entity_id("NS::Color", frozenset())
    assert entity_id.kind is EntityKind.ENUM
    assert entity_id.leaf_name == "Color"
    assert entity_id.scope == (Namespace("NS"),)


def test_record_and_enum_of_the_same_qualified_name_get_distinct_entity_ids() -> None:
    """A struct and an enum can't legally share one name in one scope in
    real C++, but the identity machinery should still tell them apart by
    kind alone if it ever happened (e.g. malformed/synthetic input)."""
    record_id = record_entity_id("NS::Thing", frozenset())
    enum_id = enum_entity_id("NS::Thing", frozenset())
    assert record_id != enum_id
    assert record_id.kind is not enum_id.kind
