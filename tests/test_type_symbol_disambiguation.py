# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Type findings on a bare name several declarations share.

The header backends label type findings with the record's leaf ``name``.
When N records share it (``lib::m0::Ctx`` ... ``lib::m7::Ctx`` in the real
C++ corpus), the label named none of them and impact attribution keyed on it
merged them: a change to ``m3::Ctx`` listed the functions taking ``m0::Ctx``.

Invariants, over generated namespace layouts (oracle: the namespace each
generated record and function was *placed* in, never the module under test):

* a finding on an ambiguous record is labelled with that record's own
  qualified spelling, and its ``affected_symbols`` names exactly the
  functions declared in that record's namespace;
* a finding on a unique bare name keeps its bare label (suppression rules,
  baselines and golden reports key on it);
* the relabel is a pure rename: kinds and verdict are unchanged.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.compare.type_symbol_disambiguation import (
    ambiguous_type_spellings,
    function_scope,
    resolve_in_scope,
    scoped_type_mentions,
)
from abicheck.model.declarations import Function, Param, Visibility
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.snapshot import AbiSnapshot


def _record(ns: str, leaf: str, size: int) -> RecordType:
    scope = (Namespace("lib"), Namespace(ns))
    fields = [TypeField(name="id", type="int", offset_bits=0)]
    if size > 32:
        fields.append(TypeField(name="extra", type="int", offset_bits=32))
    return RecordType(
        name=leaf,
        kind="struct",
        size_bits=size,
        fields=fields,
        qualified_name=f"lib::{ns}::{leaf}",
        entity_id=entity_id_for_type(scope, leaf),
    )


def _func(ns: str, leaf: str, ptype: str) -> Function:
    # Itanium mangling of lib::<ns>::<leaf>(lib::<ns>::<ptype>*)
    mangled = f"_ZN3lib{len(ns)}{ns}{len(leaf)}{leaf}EPNS0_{len(ptype)}{ptype}E"
    return Function(
        name=leaf,
        mangled=mangled,
        return_type="void",
        params=[Param(name="c", type=f"{ptype}*")],
        visibility=Visibility.PUBLIC,
    )


def _snapshot(
    version: str, namespaces: list[str], grown: set[str], unique: bool
) -> AbiSnapshot:
    types = [_record(ns, "Ctx", 64 if ns in grown else 32) for ns in namespaces]
    functions = [_func(ns, f"use_{ns}", "Ctx") for ns in namespaces]
    if unique:
        types.append(_record("solo", "Only", 64 if "solo" in grown else 32))
        functions.append(_func("solo", "use_solo", "Only"))
    return AbiSnapshot(
        library="lib.so", version=version, functions=functions, types=types
    )


_layouts = st.lists(
    st.sampled_from([f"m{i}" for i in range(6)]), min_size=2, max_size=5, unique=True
).flatmap(
    lambda nss: st.tuples(
        st.just(nss),
        st.sets(st.sampled_from(nss), min_size=1),
        st.booleans(),
    )
)


@given(layout=_layouts)
@settings(deadline=None, max_examples=30)
def test_ambiguous_findings_name_their_own_record_and_its_own_users(layout) -> None:
    namespaces, grown, unique_changes = layout
    old = _snapshot("1", namespaces, set(), unique=True)
    new = _snapshot(
        "2", namespaces, grown | ({"solo"} if unique_changes else set()), unique=True
    )
    result = compare(old, new)

    size_findings = [c for c in result.changes if c.kind.value == "type_size_changed"]
    labels = sorted(c.symbol for c in size_findings)
    expected = sorted(f"lib::{ns}::Ctx" for ns in grown)
    if unique_changes:
        expected = sorted([*expected, "Only"])
    assert labels == expected

    for c in size_findings:
        if c.symbol == "Only":
            continue
        ns = c.symbol.split("::")[1]
        named_functions = {
            a for a in (c.affected_symbols or []) if a.startswith("use_")
        }
        assert named_functions == {f"use_{ns}"}
        assert ns in c.description


@given(layout=_layouts)
@settings(deadline=None, max_examples=20)
def test_relabelling_changes_no_kind_and_no_verdict(layout) -> None:
    namespaces, grown, _ = layout
    old = _snapshot("1", namespaces, set(), unique=False)
    new = _snapshot("2", namespaces, grown, unique=False)
    relabelled = compare(old, new)

    import abicheck.post_processing as pp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            pp.DisambiguateTypeSymbols, "run", lambda self, changes, ctx: changes
        )
        plain = compare(
            _snapshot("1", namespaces, set(), unique=False),
            _snapshot("2", namespaces, grown, unique=False),
        )

    assert relabelled.verdict == plain.verdict
    assert sorted(c.kind.value for c in relabelled.changes) == sorted(
        c.kind.value for c in plain.changes
    )


def test_a_unique_bare_name_is_not_ambiguous() -> None:
    snap = _snapshot("1", ["m0"], set(), unique=True)
    assert not ambiguous_type_spellings(snap)


def test_ambiguity_counts_both_sides_together() -> None:
    """``a::Ctx`` only in OLD and ``b::Ctx`` only in NEW are two declarations
    a reader of the combined report cannot tell apart by ``Ctx``."""
    old = _snapshot("1", ["a"], set(), unique=False)
    new = _snapshot("2", ["b"], set(), unique=False)
    assert not ambiguous_type_spellings(old)
    names = ambiguous_type_spellings(old, new)
    assert names.spellings["Ctx"] == {"lib::a::Ctx", "lib::b::Ctx"}


@pytest.mark.parametrize(
    ("name", "scope", "expected"),
    [
        ("Ctx", ("lib", "m0"), "lib::m0::Ctx"),
        ("Ctx", ("lib", "m0", "Svc"), "lib::m0::Ctx"),  # enclosing scope
        ("Ctx", ("lib", "m0", "Inner"), "lib::m0::Inner::Ctx"),  # innermost wins
        ("Ctx", ("lib",), "lib::Ctx"),
        ("Ctx", ("other",), "Ctx"),  # global
        ("m1::Ctx", ("lib", "m0"), "lib::m1::Ctx"),  # partially qualified
        ("lib::m1::Ctx", (), "lib::m1::Ctx"),  # fully qualified
        ("m9::Ctx", ("lib",), None),  # no such declaration
        ("Unrelated", ("lib",), None),
    ],
)
def test_resolve_in_scope_follows_unqualified_lookup(name, scope, expected) -> None:
    from abicheck.compare.type_symbol_disambiguation import AmbiguousTypeNames

    spellings = frozenset(
        {"lib::m0::Ctx", "lib::m0::Inner::Ctx", "lib::m1::Ctx", "lib::Ctx", "Ctx"}
    )
    names = AmbiguousTypeNames(spellings={"Ctx": spellings}, by_entity={})
    assert resolve_in_scope(name, scope, names) == expected


def test_mentions_ignore_a_namesake_inside_a_longer_identifier() -> None:
    from abicheck.compare.type_symbol_disambiguation import AmbiguousTypeNames

    names = AmbiguousTypeNames(
        spellings={"Ctx": frozenset({"lib::m0::Ctx", "lib::m1::Ctx"})}, by_entity={}
    )
    assert scoped_type_mentions("CtxHolder*", ("lib", "m0"), names) == set()
    assert scoped_type_mentions("const Ctx&", ("lib", "m0"), names) == {"lib::m0::Ctx"}
    assert scoped_type_mentions("std::vector<m1::Ctx>", ("lib", "m0"), names) == {
        "lib::m1::Ctx"
    }


@pytest.mark.parametrize(
    ("name", "mangled", "expected"),
    [
        ("f0", "_ZN3lib2m02f0EPNS0_3CtxE", ("lib", "m0")),
        ("Ctx", "__abicheck_ctor__lib::m0::Ctx(const Ctx&)", ("lib", "m0", "Ctx")),
        ("~Ctx", "~lib::m3::Ctx", ("lib", "m3", "Ctx")),
        ("f", "f", ()),
        ("f", "", ()),
    ],
)
def test_function_scope(name, mangled, expected) -> None:
    assert function_scope(name, mangled) == expected
