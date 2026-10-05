# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The function-signature/parameter/qualifier/declaration facts on
``CanonicalEntity`` are a comparison-time projection of the declaration
store, never persisted (one-semantic-pipeline.md, 6B closure).

Two invariants, over generated function and variable pairs rather than one
fixed input:

1. the encoded ``semantic_ir`` document carries none of those facts, for
   any occurrence kind; and
2. comparing two snapshots gives the same findings whether each side is the
   live snapshot or its save/load round trip -- the projection is re-derived
   from the declarations, so dropping it from storage changes nothing.

The oracle for (2) is the live comparison itself, not the codec's filter.
"""

from __future__ import annotations

from _semantic_ir_persisted import PROJECTION_FIELDS, persisted_view
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Fact, Function, Param, Variable
from abicheck.model.declarations import AccessLevel
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    PROJECTION_FIELD_NAMES,
    CanonicalEntity,
    SemanticIR,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.semantic_ir_codec import (
    semantic_ir_from_document,
    semantic_ir_to_document,
)

_TYPES = st.sampled_from(["int", "long", "const char *", "char *", "double"])


@st.composite
def _function(draw) -> Function:
    mangled = "_ZN2ns1fEv"
    return Function(
        name="ns::f",
        mangled=mangled,
        return_type=draw(_TYPES),
        params=[
            Param(name=draw(st.sampled_from(["a", "b", ""])), type=t, default=d)
            for t, d in draw(
                st.lists(
                    st.tuples(_TYPES, st.sampled_from([None, "0", "1"])), max_size=3
                )
            )
        ],
        ref_qualifier=draw(st.sampled_from(["", "&", "&&"])),
        is_variadic=draw(st.sampled_from([None, True, False])),
        is_noexcept=draw(st.booleans()),
        is_virtual=draw(st.booleans()),
        is_extern_c=draw(st.booleans()),
        is_inline=draw(st.booleans()),
        is_deleted=draw(st.booleans()),
        deprecated=draw(st.sampled_from([None, "use g"])),
        entity_id=entity_id_for_function((), "f", mangled_name=mangled),
    )


@st.composite
def _variable(draw) -> Variable:
    deprecated = draw(st.sampled_from([None, "", "gone"]))
    access = draw(st.sampled_from(list(AccessLevel)))
    return Variable(
        name="ns::g",
        mangled="_ZN2ns1gE",
        type=draw(_TYPES),
        is_const=draw(st.booleans()),
        deprecated=deprecated,
        deprecated_fact=Fact.present(deprecated),
        access=access,
        access_fact=Fact.present(access),
        alignment_bits=draw(st.sampled_from([None, 32, 64])),
        entity_id=entity_id_for_variable((), "g", mangled_name="_ZN2ns1gE"),
    )


def _snap(fn: Function, var: Variable) -> AbiSnapshot:
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
        variables=[var],
    )
    return AbiSnapshot(
        library="l",
        version="1",
        functions=[fn],
        variables=[var],
        semantic_ir=ir,
        ast_producer="castxml",
    )


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(snapshot_to_dict(snap))


def _findings(old: AbiSnapshot, new: AbiSnapshot) -> list[tuple[str, str]]:
    return sorted((c.kind.value, c.symbol) for c in compare(old, new).changes)


@settings(max_examples=150, deadline=None)
@given(old=st.tuples(_function(), _variable()), new=st.tuples(_function(), _variable()))
def test_projection_is_not_persisted_and_round_trip_compares_identically(
    old: tuple[Function, Variable], new: tuple[Function, Variable]
) -> None:
    live_old, live_new = _snap(*old), _snap(*new)

    for snap in (live_old, live_new):
        document = snapshot_to_dict(snap)["semantic_ir"]
        assert document["version"] == 2
        assert document["occurrences"], "the generator must yield occurrences"
        for occurrence in document["occurrences"]:
            assert not set(occurrence["entity"]) & PROJECTION_FIELDS

    expected = _findings(live_old, live_new)
    stored_old, stored_new = _round_trip(live_old), _round_trip(live_new)
    assert _findings(stored_old, stored_new) == expected
    assert _findings(stored_old, live_new) == expected
    assert _findings(live_old, stored_new) == expected


def test_projection_field_names_match_the_cohort_modules() -> None:
    assert PROJECTION_FIELD_NAMES == PROJECTION_FIELDS


_PROJECTION_FACT = {
    "is_noexcept": Fact.present(True),
    "is_virtual": Fact.present(False),
    "is_inline": Fact.present(True),
    "return_type_spelling": Fact.present("int"),
    "parameter_type_spellings": Fact.present(("int",)),
    "access": Fact.present("public"),
}


def _winner_producer(ir: SemanticIR) -> str:
    (entity,) = ir.canonical_entities().values()
    return entity.producer


@settings(max_examples=100, deadline=None)
@given(
    projected=st.lists(st.sampled_from(sorted(_PROJECTION_FACT)), unique=True),
    persisted=st.tuples(st.booleans(), st.booleans()),
    disambiguators=st.permutations(["tu-a", "tu-b", "tu-c"]),
)
def test_canonical_winner_is_identical_before_and_after_round_trip(
    projected: list[str], persisted: tuple[bool, bool], disambiguators: list[str]
) -> None:
    """Duplicate occurrences of one entity: one carries extra persisted facts
    (template arguments / cv), one only projection facts, one neither. The
    winner must not depend on the projection facts the codec drops. Oracle:
    the winner of the independently-stripped persisted view."""
    eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
    spelling = Fact.present("int(int)")
    rich = CanonicalEntity(
        canonical_spelling=spelling,
        template_arguments=Fact.present(()) if persisted[0] else Fact.not_collected(),
        cv_qualification=Fact.present(()) if persisted[1] else Fact.not_collected(),
        producer="castxml",
    )
    projected_only = CanonicalEntity(
        canonical_spelling=spelling,
        producer="clang",
        **{name: _PROJECTION_FACT[name] for name in projected},
    )
    plain = CanonicalEntity(canonical_spelling=spelling, producer="dwarf")
    ir = SemanticIR(
        occurrences={
            OccurrenceId(eid, disambiguator=d): e
            for d, e in zip(disambiguators, (rich, projected_only, plain), strict=True)
        }
    )
    reloaded, _ = semantic_ir_from_document(semantic_ir_to_document(ir, {}))

    assert _winner_producer(ir) == _winner_producer(reloaded)
    assert _winner_producer(ir) == _winner_producer(persisted_view(ir))
    assert reloaded.canonical_entities() == persisted_view(ir).canonical_entities()
