# SPDX-License-Identifier: Apache-2.0
"""``renumber_anonymous_closure_identities``'s marker-pruned rewrite is exact.

The pruned walk skips every subtree holding no closure/anonymous marker.
Oracle: the unpruned ``_walk_rewrite_strings`` over the same containers --
the implementation before the prefilter -- on generated snapshots mixing
marker-bearing and marker-free declarations in every walked field shape
(list items, dict keys and values, nested dataclasses, frozen ``Fact``
siblings, tuples).
"""

from __future__ import annotations

import copy

from hypothesis import given, settings, strategies as st

from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Variable,
    Visibility,
)
from abicheck.model.entities import TypeField
from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, Record, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments_walk import (
    _collect_strings,
    _walk_rewrite_strings,
    collect_and_flag,
)
from abicheck.storage import closure_identity
from abicheck.storage.closure_identity import renumber_anonymous_closure_identities


def _closure(kind: str, header: str, line: int, col: int) -> str:
    return strip_anonymous_type_location(f"({kind} at /src/{header}:{line}:{col})")


_marker = st.builds(
    _closure,
    st.sampled_from(["lambda", "unnamed struct", "anonymous union"]),
    st.sampled_from(["a.h", "b.h"]),
    st.integers(1, 40),
    st.integers(1, 9),
)
_plain = st.sampled_from(["int", "Foo", "ns::Bar", "std::vector<int>", "(int)"])
_spelling = st.one_of(_plain, st.builds(lambda m, p: f"W<{m}>{p}", _marker, _plain))
_name = st.sampled_from(["f", "g", "h", "k"])


@st.composite
def _snapshots(draw: st.DrawFn) -> AbiSnapshot:
    functions = [
        Function(
            name=draw(_name),
            mangled=f"_Z{i}",
            return_type=draw(_spelling),
            visibility=Visibility.PUBLIC,
            params=[
                Param(name="p", type=draw(_spelling))
                for _ in range(draw(st.integers(0, 2)))
            ],
        )
        for i in range(draw(st.integers(0, 5)))
    ]
    variables = [
        Variable(name=draw(_name), mangled=f"_V{i}", type=draw(_spelling))
        for i in range(draw(st.integers(0, 3)))
    ]
    types = []
    for _ in range(draw(st.integers(0, 4))):
        qn = draw(_spelling)
        rec = RecordType(
            name=qn,
            kind="struct",
            qualified_name=qn,
            size_bits=8,
            fields=[
                TypeField(name="x", type=draw(_spelling))
                for _ in range(draw(st.integers(0, 2)))
            ],
        )
        if draw(st.booleans()):
            rec.qualified_name_fact = Fact.present(qn)  # type: ignore[attr-defined]
        types.append(rec)
    typedefs = {
        draw(_spelling): draw(_spelling) for _ in range(draw(st.integers(0, 3)))
    }
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        functions=functions,
        variables=variables,
        types=types,
        typedefs=typedefs,
    )
    if draw(st.booleans()):
        occurrences = {}
        for i in range(draw(st.integers(1, 4))):
            scope = draw(_spelling)
            eid = entity_id_for_type((Namespace("ns"), Record(scope)), f"R{i}")
            occurrences[OccurrenceId(eid)] = CanonicalEntity(
                canonical_spelling=Fact.present(draw(_spelling))
            )
        snap.semantic_ir = SemanticIR(occurrences=occurrences)
    snap.fact_provenance = {  # type: ignore[assignment]
        f"type:{draw(_spelling)}": {"source": draw(_spelling)}
        for _ in range(draw(st.integers(0, 2)))
    }
    return snap


@settings(max_examples=300, deadline=None)
@given(_snapshots())
def test_pruned_rewrite_matches_full_walk(snap: AbiSnapshot) -> None:
    oracle = copy.deepcopy(snap)
    original = closure_identity._rewrite_marked_subtrees
    closure_identity._rewrite_marked_subtrees = (  # type: ignore[assignment]
        lambda value, rewrite, pred, field_name=None: _walk_rewrite_strings(
            value, rewrite
        )
    )
    try:
        renumber_anonymous_closure_identities(oracle)
    finally:
        closure_identity._rewrite_marked_subtrees = original  # type: ignore[assignment]
    renumber_anonymous_closure_identities(snap)
    for field in closure_identity._LAMBDA_IDENTITY_FIELDS:
        assert repr(getattr(snap, field, None)) == repr(getattr(oracle, field, None)), (
            field
        )


@given(
    st.recursive(
        st.one_of(_spelling, st.integers()),
        lambda kids: st.one_of(
            st.lists(kids, max_size=3),
            st.tuples(kids, kids),
            st.dictionaries(_spelling, kids, max_size=3),
        ),
        max_leaves=12,
    )
)
def test_collect_and_flag_is_collect_plus_a_walk_superset(value: object) -> None:
    """Its strings are exactly ``_collect_strings``'; its flag is set
    whenever the walk would hand ``rewrite`` a marker-bearing string."""
    seen: list[str] = []

    def record(text: str) -> str:
        seen.append(text)
        return text + "!"

    out: list[str] = []
    flagged = collect_and_flag(value, out, closure_identity._may_hold_marker)
    expected: list[str] = []
    _collect_strings(value, expected)
    assert out == expected
    _walk_rewrite_strings(copy.deepcopy(value), record)
    assert flagged == any(closure_identity._may_hold_marker(s) for s in seen)


def test_vacuity_guard_markers_really_get_renumbered() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        types=[
            RecordType(
                name="x",
                kind="struct",
                qualified_name=f"W<{_closure('lambda', 'a.h', 7, 3)}>",
                size_bits=8,
            )
        ],
    )
    renumber_anonymous_closure_identities(snap)
    assert snap.types[0].qualified_name == "W<(lambda:a.h#1)>"
