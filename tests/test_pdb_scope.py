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

from abicheck.extract.headers.scope_segments import namespace_segment
from abicheck.extract.pdb_scope import (
    enum_entity_id,
    has_anonymous_enclosing_scope,
    record_entity_id,
    scope_path_for_qualified_name,
)
from abicheck.model.identity import EntityKind, Namespace, Record, entity_id_for_type


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


# ---------------------------------------------------------------------------
# Named descendants of an anonymous PDB record (Codex review, PR #1025,
# fresh evidence): `pdb_metadata._is_user_visible` now ADMITS a named leaf
# nested inside an anonymous ("<...>"-prefixed) enclosing scope instead of
# dropping the whole declaration -- the leaf's own layout facts are real and
# worth keeping. What this module still cannot do is build a real identity
# through that anonymous scope, so `record_entity_id`/`enum_entity_id` leave
# `entity_id` unset (None) for exactly this shape.
# ---------------------------------------------------------------------------


def test_has_anonymous_enclosing_scope_true_for_middle_segment() -> None:
    assert has_anonymous_enclosing_scope("N::<unnamed-tag>::Inner") is True


def test_has_anonymous_enclosing_scope_false_for_ordinary_name() -> None:
    assert has_anonymous_enclosing_scope("NS::Widget") is False


def test_has_anonymous_enclosing_scope_false_when_only_the_leaf_is_anonymous() -> None:
    """The leaf itself being anonymous is a different, already-handled case
    (`pdb_metadata._is_user_visible` rejects it outright, so it never
    reaches this module at all) -- this predicate is about ENCLOSING scopes
    only, matching `scope_path_for_qualified_name`'s own `segments[:-1]`
    split between scope-building segments and the leaf."""
    assert has_anonymous_enclosing_scope("N::O::<unnamed-tag>") is False


def test_record_entity_id_unset_for_named_leaf_under_anonymous_scope() -> None:
    assert record_entity_id("N::<unnamed-tag>::Inner", frozenset()) is None


def test_enum_entity_id_unset_for_named_leaf_under_anonymous_scope() -> None:
    assert enum_entity_id("N::<unnamed-tag>::Inner", frozenset()) is None


def test_record_entity_id_still_resolved_when_no_anonymous_scope_present() -> None:
    """Guard against a check that's accidentally too broad -- an ordinary
    qualified name must still resolve a real EntityId, not None."""
    assert record_entity_id("N::O::Inner", frozenset()) is not None


# ---------------------------------------------------------------------------
# Known, accepted limitation (module docstring's 4th documented gap,
# carried over from a prior Codex review round on PR #1025): CodeView's
# flat, already-qualified TPI names carry no signal distinguishing a
# user-declared `inline namespace` from an ordinary one, unlike DWARF's
# `DW_AT_export_symbols` or Clang's `isInline`. This module therefore
# always emits an ordinary Namespace segment. Pinned here as an executable
# regression guard (not merely prose) and to demonstrate the mismatch is
# LIVE, not theoretical: EntityId genuinely disagrees between a PDB-sourced
# occurrence and a header-AST-sourced occurrence of the identical
# declaration nested inside a real inline namespace.
# ---------------------------------------------------------------------------


def test_inline_looking_namespace_segment_still_classified_as_ordinary() -> None:
    """``api::v1::Widget`` is exactly the shape a real ``inline namespace
    v1`` would produce, but this module has no way to tell that apart from
    an ordinary nested namespace named ``v1`` -- see the module docstring's
    4th documented limitation."""
    scope, leaf = scope_path_for_qualified_name("api::v1::Widget", frozenset())
    assert scope == (Namespace("api"), Namespace("v1"))
    assert leaf == "Widget"


def test_pdb_inline_namespace_gap_produces_a_live_entity_id_mismatch() -> None:
    """The PDB-sourced EntityId for a declaration nested inside a real
    inline namespace disagrees with the EntityId a header-AST backend
    (which DOES resolve ``InlineNamespace`` -- see
    ``extract/headers/clang/scope.py``) would assign to the identical
    declaration, breaking cross-backend reconciliation for this case. This
    is the concrete, executable version of the module docstring's claim
    that the gap is live rather than purely theoretical."""
    pdb_scope, pdb_leaf = scope_path_for_qualified_name("api::v1::Widget", frozenset())
    pdb_entity_id = entity_id_for_type(pdb_scope, pdb_leaf)

    header_ast_scope = (
        namespace_segment("api"),
        namespace_segment("v1", is_inline=True),
    )
    header_ast_entity_id = entity_id_for_type(header_ast_scope, "Widget")

    assert pdb_entity_id != header_ast_entity_id
    assert pdb_entity_id.key != header_ast_entity_id.key
