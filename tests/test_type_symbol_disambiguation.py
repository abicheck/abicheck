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
        ("lib::m1::Ctx", ("lib", "m0"), "lib::m1::Ctx"),
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


def test_an_absolute_spelling_is_looked_up_from_the_global_scope_only() -> None:
    from abicheck.compare.type_symbol_disambiguation import AmbiguousTypeNames

    names = AmbiguousTypeNames(
        spellings={"Ctx": frozenset({"m1::Ctx", "lib::m0::m1::Ctx"})}, by_entity={}
    )
    assert scoped_type_mentions("::m1::Ctx*", ("lib", "m0"), names) == {"m1::Ctx"}
    assert scoped_type_mentions("m1::Ctx*", ("lib", "m0"), names) == {
        "lib::m0::m1::Ctx"
    }


def test_a_rule_written_against_the_qualified_label_suppresses_it() -> None:
    """The report shows ``lib::m1::Ctx``; a rule copied from it must match,
    although suppression runs while the finding is still labelled ``Ctx``.
    The bare rule keeps matching too."""
    from abicheck.suppression import Suppression, SuppressionList

    for symbol in ("lib::m1::Ctx", "Ctx"):
        old = _snapshot("1", ["m0", "m1"], set(), unique=False)
        new = _snapshot("2", ["m0", "m1"], {"m1"}, unique=False)
        rules = SuppressionList(
            suppressions=[Suppression(symbol=symbol, reason="test")]
        )
        result = compare(old, new, suppression=rules)
        assert not [c for c in result.changes if c.kind.value == "type_size_changed"]


def test_a_mismatched_qualified_rule_suppresses_nothing() -> None:
    from abicheck.suppression import Suppression, SuppressionList

    old = _snapshot("1", ["m0", "m1"], set(), unique=False)
    new = _snapshot("2", ["m0", "m1"], {"m1"}, unique=False)
    rules = SuppressionList(
        suppressions=[Suppression(symbol="lib::m0::Ctx", reason="test")]
    )
    result = compare(old, new, suppression=rules)
    assert [
        c.symbol for c in result.changes if c.kind.value == "type_size_changed"
    ] == ["lib::m1::Ctx"]


def test_ambiguous_enums_that_change_alike_both_survive() -> None:
    from abicheck.model.entities import EnumMember, EnumType
    from abicheck.model.identity import entity_id_for_enum

    def snap(version: str, value: int) -> AbiSnapshot:
        enums = [
            EnumType(
                name="Mode",
                members=[
                    EnumMember(name="a", value=0),
                    EnumMember(name="b", value=value),
                ],
                qualified_name=f"lib::{ns}::Mode",
                entity_id=entity_id_for_enum((Namespace("lib"), Namespace(ns)), "Mode"),
            )
            for ns in ("m0", "m1")
        ]
        return AbiSnapshot(library="lib.so", version=version, enums=enums)

    result = compare(snap("1", 1), snap("2", 2))
    changed = sorted(
        c.symbol
        for c in [*result.changes, *(result.redundant_changes or [])]
        if c.entity_id is not None
        and c.entity_id.leaf_name == "Mode"
        and "value" in c.kind.value
    )
    assert len({s.split("::")[1] for s in changed if "::" in s}) == 2, changed


def test_an_ambiguous_embedding_parent_attributes_only_its_own_users() -> None:
    """``m0::Holder`` embeds ``m0::Ctx``; an unrelated ``m1::Holder`` exists.
    A change to ``m0::Ctx`` reaches the users of ``m0::Holder`` only."""

    def holder(ns: str, embeds: bool) -> RecordType:
        fields = [TypeField(name="c", type="Ctx", offset_bits=0)] if embeds else []
        fields.append(TypeField(name="x", type="int", offset_bits=64))
        return RecordType(
            name="Holder",
            kind="struct",
            size_bits=128,
            fields=fields,
            qualified_name=f"lib::{ns}::Holder",
            entity_id=entity_id_for_type((Namespace("lib"), Namespace(ns)), "Holder"),
        )

    def snap(version: str, grown: set[str]) -> AbiSnapshot:
        types = [_record(ns, "Ctx", 64 if ns in grown else 32) for ns in ("m0", "m1")]
        types += [holder("m0", True), holder("m1", False)]
        functions = [_func(ns, f"hold_{ns}", "Holder") for ns in ("m0", "m1")]
        return AbiSnapshot(
            library="lib.so", version=version, functions=functions, types=types
        )

    result = compare(snap("1", set()), snap("2", {"m0"}))
    (finding,) = [c for c in result.changes if c.kind.value == "type_size_changed"]
    assert finding.symbol == "lib::m0::Ctx"
    users = {a for a in finding.affected_symbols or [] if a.startswith("hold_")}
    assert users == {"hold_m0"}


# -- primitive edge cases ----------------------------------------------------

from abicheck.compare.type_symbol_disambiguation import (  # noqa: E402
    AmbiguousTypeNames,
    disambiguate_type_symbols,
)
from abicheck.model.change import Change  # noqa: E402
from abicheck.model.change_catalog.kinds import ChangeKind  # noqa: E402
from abicheck.model.identity import (  # noqa: E402
    Anonymous,
    InlineNamespace,
    Record,
    entity_id_for_function,
)
from abicheck.model.type_label import (  # noqa: E402
    qualified_spelling,
    qualified_type_label,
    relabelled,
)


def _eid(*scope, leaf="Ctx"):
    return entity_id_for_type(tuple(scope), leaf)


@pytest.mark.parametrize(
    ("eid", "expected"),
    [
        (None, None),
        (_eid(), "Ctx"),
        (_eid(Namespace("a"), Record("R"), InlineNamespace("v1")), "a::R::v1::Ctx"),
        (_eid(Namespace("a"), Anonymous("namespace", 0)), None),
        (_eid(Namespace("")), None),
    ],
)
def test_qualified_spelling(eid, expected) -> None:
    assert qualified_spelling(eid) == expected


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("Ctx", "a::Ctx"),
        ("Ctx::field", "a::Ctx::field"),
        ("CtxX", None),
        ("Other::Ctx", None),
        ("", None),
    ],
)
def test_relabelled(symbol, expected) -> None:
    assert relabelled(symbol, "Ctx", "a::Ctx") == expected


def _type_change(symbol="Ctx", eid=None, **kw) -> Change:
    return Change(
        kind=kw.pop("kind", ChangeKind.TYPE_SIZE_CHANGED),
        symbol=symbol,
        description=kw.pop("description", f"Size changed: {symbol}"),
        entity_id=eid,
        **kw,
    )


def test_qualified_type_label_answers_only_for_a_relabellable_type_finding() -> None:
    a_ctx = _eid(Namespace("a"))
    assert qualified_type_label(_type_change(eid=a_ctx)) == "a::Ctx"
    assert qualified_type_label(_type_change("Ctx::f", eid=a_ctx)) == "a::Ctx::f"
    assert qualified_type_label(_type_change(eid=None)) is None
    func = entity_id_for_function((Namespace("a"),), "Ctx", mangled_name="_ZN1a3CtxEv")
    assert qualified_type_label(_type_change(eid=func)) is None
    assert (
        qualified_type_label(_type_change(eid=_eid(Anonymous("namespace", 0)))) is None
    )
    # global-scope type: the label would not change
    assert qualified_type_label(_type_change(eid=_eid())) is None
    # a symbol that is not the entity's leaf
    assert qualified_type_label(_type_change("Other", eid=a_ctx)) is None


def test_disambiguate_touches_only_findings_it_can_name() -> None:
    a_ctx, b_ctx = _eid(Namespace("a")), _eid(Namespace("b"))
    names = AmbiguousTypeNames(
        spellings={"Ctx": frozenset({"a::Ctx", "b::Ctx"})},
        by_entity={a_ctx: "a::Ctx", b_ctx: "b::Ctx"},
    )
    relabel = _type_change(
        eid=a_ctx, description="Size changed: Ctx (Ctx2 and x::Ctx untouched)"
    )
    preset = _type_change(eid=b_ctx, qualified_name="kept")
    unknown = _type_change(eid=_eid(Namespace("c")))
    no_eid = _type_change()
    other_symbol = _type_change("Something", eid=a_ctx)
    func = _type_change(
        eid=entity_id_for_function((Namespace("a"),), "Ctx", mangled_name="_ZN1a3CtxEv")
    )
    changes = [relabel, preset, unknown, no_eid, other_symbol, func]

    assert disambiguate_type_symbols(changes, names) == 2
    assert relabel.symbol == "a::Ctx"
    assert relabel.qualified_name == "a::Ctx"
    assert relabel.description == "Size changed: a::Ctx (Ctx2 and x::Ctx untouched)"
    assert preset.symbol == "b::Ctx" and preset.qualified_name == "kept"
    assert [c.symbol for c in (unknown, no_eid, other_symbol, func)] == [
        "Ctx",
        "Ctx",
        "Something",
        "Ctx",
    ]
    assert (
        disambiguate_type_symbols([_type_change(eid=a_ctx)], AmbiguousTypeNames({}, {}))
        == 0
    )


def test_ambiguity_ignores_baked_names_and_unspellable_declarations() -> None:
    baked = RecordType(name="ns::Ctx", kind="struct", size_bits=32)
    anonymous = RecordType(
        name="Ctx",
        kind="struct",
        size_bits=32,
        entity_id=_eid(Anonymous("namespace", 0)),
    )
    by_name_only = RecordType(
        name="Ctx", kind="struct", size_bits=32, qualified_name="q::Ctx"
    )
    real = _record("m0", "Ctx", 32)
    snap = AbiSnapshot(
        library="l", version="1", types=[baked, anonymous, by_name_only, real]
    )
    names = ambiguous_type_spellings(snap, None)
    assert names.spellings == {"Ctx": frozenset({"q::Ctx", "lib::m0::Ctx"})}
    # only the declaration with a spellable identity is relabellable by entity
    assert set(names.by_entity.values()) == {"lib::m0::Ctx"}


def test_a_symbol_pattern_rule_matches_the_qualified_label() -> None:
    from abicheck.suppression import Suppression, SuppressionList

    old = _snapshot("1", ["m0", "m1"], set(), unique=False)
    new = _snapshot("2", ["m0", "m1"], {"m0", "m1"}, unique=False)
    rules = SuppressionList(
        suppressions=[Suppression(symbol_pattern=r"lib::m1::.*", reason="test")]
    )
    result = compare(old, new, suppression=rules)
    assert [
        c.symbol for c in result.changes if c.kind.value == "type_size_changed"
    ] == ["lib::m0::Ctx"]
