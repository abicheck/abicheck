# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-063 Phase 2's storage v2 wire bridge: ``model.identity.EntityId`` <->
``storage.entity_ids``'s wire-schema-v2 document shape.

Split out of ``tests/unit/storage/test_identity_documents.py`` by subject
rather than size — this file is about the *bridge* between the two
independent ``EntityId`` types (``storage.entity_ids.EntityId``, this
module's own pre-existing packed-key wire DTO; ``model.identity.EntityId``,
the ``ScopePath``-based domain identity ADR-063 Phase 2 introduces), not
about either type's own in-memory contract — those stay in
``test_identity_documents.py``/``tests/test_model_identity.py``
respectively.

The property this file exists to pin: a rendered ``qualified_name`` string
(the pre-existing v1 wire shape) is not a lossless encoding of a
``ScopePath`` — two distinct ``ScopePath``s can render to the identical
string (a record nested in a record vs. the same names nested in a
namespace both render ``"A::B"``) — so the v2 bridge encodes ``ScopePath``
as an explicit list of typed segment records instead, and
``domain_entity_id_from_dto(domain_entity_id_to_dto(x)) == x`` must hold for
every segment kind, not just the ones a hand-picked example happens to
cover.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.model.identity import (
    Anonymous,
    EntityId,
    EntityKind,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    Record,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_variable,
)
from abicheck.storage.entity_ids import (
    DOMAIN_ENTITY_ID_SCHEMA_VERSION,
    domain_entity_id_from_dto,
    domain_entity_id_to_dto,
)

# --------------------------------------------------------------------------
# Hypothesis strategies for arbitrary ScopePath/EntityId values
# --------------------------------------------------------------------------

_names = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)

_segments = st.one_of(
    st.builds(Namespace, name=_names),
    st.builds(
        Record,
        name=_names,
        access=st.sampled_from(["", "public", "protected", "private"]),
    ),
    st.builds(
        InlineNamespace, name=_names, version_tag=st.sampled_from(["", "v1", "v2"])
    ),
    st.builds(
        Anonymous,
        kind=st.sampled_from(["struct", "union", "enum", "namespace"]),
        ordinal=st.integers(min_value=0, max_value=50),
    ),
)

_scope_paths = st.lists(_segments, max_size=5).map(tuple)

_entity_kinds = st.sampled_from(list(EntityKind))

_extras = st.lists(st.text(max_size=12), max_size=4).map(tuple)


@st.composite
def _entity_ids(draw: st.DrawFn) -> EntityId:
    return EntityId(
        scope=draw(_scope_paths),
        kind=draw(_entity_kinds),
        leaf_name=draw(_names),
        extra=draw(_extras),
    )


class TestRoundTrip:
    """``from_dto(to_dto(x)) == x`` for every ``ScopePath`` segment kind."""

    @given(_entity_ids())
    def test_arbitrary_entity_id_round_trips(self, entity_id: EntityId) -> None:
        assert (
            domain_entity_id_from_dto(domain_entity_id_to_dto(entity_id)) == entity_id
        )

    @given(_scope_paths, _names)
    def test_function_round_trips(self, scope: tuple, leaf_name: str) -> None:
        entity_id = entity_id_for_function(
            scope, leaf_name, param_types=("int", "char const*")
        )
        assert (
            domain_entity_id_from_dto(domain_entity_id_to_dto(entity_id)) == entity_id
        )

    def test_local_to_function_owner_round_trips(self) -> None:
        owner = entity_id_for_function((), "f", param_types=("int",))
        entity_id = EntityId(
            scope=(LocalToFunction(owner=owner, block_ordinal=1),),
            kind=EntityKind.TYPE,
            leaf_name="A",
        )
        assert (
            domain_entity_id_from_dto(domain_entity_id_to_dto(entity_id)) == entity_id
        )

    def test_nested_local_to_function_owner_round_trips(self) -> None:
        """``LocalToFunction.owner`` is recursive -- an owner that is itself
        local to another function must round-trip too."""
        outer = entity_id_for_function((), "outer")
        inner_owner = EntityId(
            scope=(LocalToFunction(owner=outer, block_ordinal=0),),
            kind=EntityKind.FUNCTION,
            leaf_name="inner",
        )
        entity_id = EntityId(
            scope=(LocalToFunction(owner=inner_owner, block_ordinal=0),),
            kind=EntityKind.TYPE,
            leaf_name="Deepest",
        )
        assert (
            domain_entity_id_from_dto(domain_entity_id_to_dto(entity_id)) == entity_id
        )

    def test_to_dto_carries_current_schema_version(self) -> None:
        entity_id = entity_id_for_type((), "X")
        assert (
            domain_entity_id_to_dto(entity_id)["schema_version"]
            == DOMAIN_ENTITY_ID_SCHEMA_VERSION
        )


class TestSegmentKindDisambiguation:
    """The exact counterexample the Design section's finding raised: two
    distinct ``ScopePath``s that render to the identical ``qualified_name``
    string must not collide once encoded as v2 documents."""

    def test_record_nested_in_record_vs_namespace_nested_in_namespace(self) -> None:
        record_nested = EntityId(
            scope=(Record("A"),), kind=EntityKind.TYPE, leaf_name="B"
        )
        namespace_nested = EntityId(
            scope=(Namespace("A"),), kind=EntityKind.TYPE, leaf_name="B"
        )
        assert record_nested != namespace_nested

        record_dto = domain_entity_id_to_dto(record_nested)
        namespace_dto = domain_entity_id_to_dto(namespace_nested)
        assert record_dto != namespace_dto

        assert domain_entity_id_from_dto(record_dto) == record_nested
        assert domain_entity_id_from_dto(namespace_dto) == namespace_nested
        assert domain_entity_id_from_dto(record_dto) != domain_entity_id_from_dto(
            namespace_dto
        )

    def test_inline_namespace_vs_ordinary_namespace_same_name(self) -> None:
        inline = EntityId(
            scope=(InlineNamespace("v1"),), kind=EntityKind.TYPE, leaf_name="X"
        )
        ordinary = EntityId(
            scope=(Namespace("v1"),), kind=EntityKind.TYPE, leaf_name="X"
        )
        assert inline != ordinary
        assert domain_entity_id_from_dto(domain_entity_id_to_dto(inline)) == inline
        assert domain_entity_id_from_dto(domain_entity_id_to_dto(ordinary)) == ordinary
        assert domain_entity_id_from_dto(
            domain_entity_id_to_dto(inline)
        ) != domain_entity_id_from_dto(domain_entity_id_to_dto(ordinary))

    def test_record_access_is_not_identity_across_the_bridge(self) -> None:
        """``Record.access`` is payload, not identity (model/identity.py's own
        contract) -- the bridge must not accidentally smuggle it into
        equality by, say, comparing dicts instead of reconstructed objects."""
        public = EntityId(
            scope=(Record("A", access="public"),), kind=EntityKind.TYPE, leaf_name="B"
        )
        private = EntityId(
            scope=(Record("A", access="private"),), kind=EntityKind.TYPE, leaf_name="B"
        )
        assert public == private
        restored_public = domain_entity_id_from_dto(domain_entity_id_to_dto(public))
        restored_private = domain_entity_id_from_dto(domain_entity_id_to_dto(private))
        assert restored_public == restored_private == public
        # But the wire document itself still records the real access level --
        # only equality treats it as non-identity, the document is not lossy.
        assert domain_entity_id_to_dto(public)["scope"][0]["access"] == "public"
        assert domain_entity_id_to_dto(private)["scope"][0]["access"] == "private"


class TestV1MigrationAdapter:
    """Every existing v1 (``kind``/``qualified_name``/``discriminator``)
    document still loads, as a documented best-effort reconstruction --
    never asserted equal to a fresh v2 encoding of the same logical entity,
    since v1 never recorded which kind a segment was."""

    def test_v1_document_with_no_explicit_schema_version_loads(self) -> None:
        v1_document = {"kind": "type", "qualified_name": "ns::Outer::Inner"}
        migrated = domain_entity_id_from_dto(v1_document)
        assert migrated.kind is EntityKind.TYPE
        assert migrated.leaf_name == "Inner"
        # Best-effort only: every "::" component became an untyped Namespace
        # segment, even though a real "ns::Outer::Inner" more plausibly nests
        # Inner inside a Record named Outer -- v1 cannot express that
        # distinction, so this is NOT asserted equal to
        # entity_id_for_type((Namespace("ns"), Record("Outer")), "Inner").
        assert migrated.scope == (Namespace("ns"), Namespace("Outer"))

    def test_v1_document_with_explicit_schema_version_1_loads(self) -> None:
        v1_document = {
            "schema_version": 1,
            "kind": "function",
            "qualified_name": "f",
            "discriminator": "_Z1fv",
        }
        migrated = domain_entity_id_from_dto(v1_document)
        assert migrated.kind is EntityKind.FUNCTION
        assert migrated.scope == ()
        assert migrated.leaf_name == "f"
        assert migrated.extra == ("_Z1fv",)

    def test_v1_document_with_bare_name_has_empty_scope(self) -> None:
        migrated = domain_entity_id_from_dto(
            {"kind": "constant", "qualified_name": "MAX"}
        )
        assert migrated.scope == ()
        assert migrated.leaf_name == "MAX"

    def test_v1_style_output_from_storage_entity_id_loads_through_the_bridge(
        self,
    ) -> None:
        """An actual document produced by the pre-existing
        ``storage.entity_ids.EntityId.to_dict()`` -- not a hand-typed
        fixture -- still loads through the domain bridge."""
        from abicheck.storage.entity_ids import EntityId as StorageEntityId

        wire = StorageEntityId(EntityKind.VARIABLE, "ns::g_counter", "int").to_dict()
        migrated = domain_entity_id_from_dto(wire)
        assert migrated.kind is EntityKind.VARIABLE
        assert migrated.leaf_name == "g_counter"
        assert migrated.scope == (Namespace("ns"),)
        assert migrated.extra == ("int",)

    def test_empty_qualified_name_components_are_preserved_not_collapsed(self) -> None:
        """``storage.entity_ids.EntityId.qualified_name`` places no grammar
        restriction on this string -- ``"A::B"`` and ``"A::::B"`` are two
        equally legal, structurally different v1 values, so filtering out
        empty ``::``-separated components collided them onto the identical
        scope/leaf (Codex review)."""
        without_empty = domain_entity_id_from_dto(
            {"kind": "type", "qualified_name": "A::B"}
        )
        with_empty = domain_entity_id_from_dto(
            {"kind": "type", "qualified_name": "A::::B"}
        )
        assert without_empty != with_empty
        assert without_empty.scope == (Namespace("A"),)
        assert with_empty.scope == (Namespace("A"), Namespace(""))
        assert without_empty.leaf_name == with_empty.leaf_name == "B"


class TestMalformedDocuments:
    def test_unknown_schema_version_is_refused(self) -> None:
        with pytest.raises(
            ValueError, match="unsupported entity-id wire schema version"
        ):
            domain_entity_id_from_dto(
                {"schema_version": 99, "kind": "type", "scope": [], "leaf_name": "X"}
            )

    def test_unrecognized_segment_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unrecognized scope-segment kind"):
            domain_entity_id_from_dto(
                {
                    "schema_version": DOMAIN_ENTITY_ID_SCHEMA_VERSION,
                    "scope": [{"kind": "bogus", "name": "X"}],
                    "kind": "type",
                    "leaf_name": "Y",
                }
            )

    def test_non_mapping_document_is_refused(self) -> None:
        with pytest.raises(TypeError):
            domain_entity_id_from_dto(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_entity_id_to_dto_rejects_unrecognized_segment_type(self) -> None:
        class _NotASegment:
            pass

        bogus = EntityId(scope=(_NotASegment(),), kind=EntityKind.TYPE, leaf_name="X")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="unrecognized ScopePath segment type"):
            domain_entity_id_to_dto(bogus)

    def test_boolean_schema_version_is_refused_not_treated_as_1(self) -> None:
        """``True == 1`` in Python -- a naive ``version == 1`` dispatch would
        silently run the lossy v1 adapter on a document whose real
        ``scope``/``extra`` fields it then discards (Codex review)."""
        v2_document = domain_entity_id_to_dto(entity_id_for_type((), "X"))
        v2_document["schema_version"] = True
        with pytest.raises(TypeError, match="schema_version must be an int"):
            domain_entity_id_from_dto(v2_document)

    def test_float_schema_version_is_refused_not_treated_as_int(self) -> None:
        """``2.0 == 2`` in Python -- must not silently dispatch to the v2
        parser on a value that was never a real integer version."""
        v2_document = domain_entity_id_to_dto(entity_id_for_type((), "X"))
        v2_document["schema_version"] = 2.0
        with pytest.raises(TypeError, match="schema_version must be an int"):
            domain_entity_id_from_dto(v2_document)

    def test_v2_document_missing_extra_is_refused(self) -> None:
        """``domain_entity_id_to_dto`` always emits ``extra`` (empty list
        included) -- a v2 document missing it entirely is truncated or
        hand-edited, not a producer that legitimately had nothing to say."""
        v2_document = domain_entity_id_to_dto(entity_id_for_type((), "X"))
        del v2_document["extra"]
        with pytest.raises(ValueError, match="missing required field 'extra'"):
            domain_entity_id_from_dto(v2_document)

    def test_record_segment_missing_access_is_refused(self) -> None:
        v2_document = domain_entity_id_to_dto(
            EntityId(scope=(Record("A"),), kind=EntityKind.TYPE, leaf_name="B")
        )
        del v2_document["scope"][0]["access"]
        with pytest.raises(ValueError, match="missing required field 'access'"):
            domain_entity_id_from_dto(v2_document)

    def test_inline_namespace_segment_missing_version_tag_is_refused(self) -> None:
        v2_document = domain_entity_id_to_dto(
            EntityId(
                scope=(InlineNamespace("v1"),), kind=EntityKind.TYPE, leaf_name="X"
            )
        )
        del v2_document["scope"][0]["version_tag"]
        with pytest.raises(ValueError, match="missing required field 'version_tag'"):
            domain_entity_id_from_dto(v2_document)

    def test_boolean_ordinal_is_refused_not_treated_as_an_int(self) -> None:
        """``bool`` subclasses ``int`` in Python -- ``true``/``1`` must not
        reconstruct to equal, same-hash ``Anonymous`` segments (Codex
        review)."""
        v2_document = domain_entity_id_to_dto(
            EntityId(
                scope=(Anonymous("struct", 1),), kind=EntityKind.TYPE, leaf_name=""
            )
        )
        v2_document["scope"][0]["ordinal"] = True
        with pytest.raises(TypeError, match="ordinal must be an int"):
            domain_entity_id_from_dto(v2_document)

    def test_boolean_block_ordinal_is_refused_not_treated_as_an_int(self) -> None:
        owner = entity_id_for_function((), "f")
        v2_document = domain_entity_id_to_dto(
            EntityId(
                scope=(LocalToFunction(owner=owner, block_ordinal=1),),
                kind=EntityKind.TYPE,
                leaf_name="A",
            )
        )
        v2_document["scope"][0]["block_ordinal"] = False
        with pytest.raises(TypeError, match="block_ordinal must be an int"):
            domain_entity_id_from_dto(v2_document)


def test_variable_mangled_branch_round_trips_through_the_bridge() -> None:
    """A non-empty-scope-collapsing branch: `entity_id_for_variable`'s
    mangled-name branch always resolves `scope=()`/`leaf_name=""` -- confirm
    the bridge preserves that degenerate shape rather than choking on it."""
    entity_id = entity_id_for_variable(
        (Namespace("ns"),), "g_x", mangled_name="_ZN2ns3g_xE"
    )
    assert entity_id.scope == ()
    assert entity_id.leaf_name == ""
    assert domain_entity_id_from_dto(domain_entity_id_to_dto(entity_id)) == entity_id
