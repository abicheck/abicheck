"""``binary_exported_fact`` vs the observed ``exports`` join (ADR-063, 2026-09-24 note).

The export fact (c) and ``compare/export_join.py`` both answer "does the
artifact export this declaration?", but historically from different tables:
the header-AST producers also counted a ``.symtab``-only symbol and a bare
``name`` hit for a C++ declaration, and the DWARF producer a demangled-name
hit -- each recorded as a plain ``Fact.present(True)``, indistinguishable
from a real dynamic-export match the join makes. They disagreed silently.

The fix keeps the fact's truth value (so no finding moves) but makes the
tier explicit: :func:`abicheck.model.export_index.match_export` is the one
primitive every producer calls, and only its ``DYNAMIC`` tier -- the join's
own rule, the declaration's linker spelling in the dynamic table -- becomes
``PRESENT``; every weaker tier is ``PARTIAL`` with a named diagnostic.

Worlds are generated from ground truth (which spelling sits in which table)
and the expected tier is derived by :func:`_oracle_tier` from that ground
truth alone -- never from ``match_export``, ``export_index`` projections or
the join's helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hypothesis import given, settings, strategies as st

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.model.fact import FactStatus
from abicheck.model.graph_join import JoinState
from abicheck.model.surface_facts import binary_exported, is_binary_exported

#: Every way ground truth can place a symbol relative to one declaration.
EXPOSURES = (
    "dyn_default",  # the linker spelling in .dynsym as foo@@V / unversioned
    "dyn_nondefault",  # the linker spelling in .dynsym only as foo@V
    "static",  # the linker spelling in .symtab only
    "static_versioned_literal",  # .symtab carries "foo@@V" literally
    "dyn_bare_name",  # .dynsym carries the declaration's bare name only
    "dyn_mangling_variant",  # .dynsym carries a spelling that demangles the same
)

_BASES = st.text(alphabet="abcdefgh", min_size=1, max_size=5)


@dataclass(frozen=True)
class Entity:
    base: str
    cxx: bool
    exposures: frozenset[str]

    @property
    def name(self) -> str:
        return self.base

    @property
    def mangled(self) -> str:
        return f"_Z{len(self.base)}{self.base}v" if self.cxx else self.base

    @property
    def variant(self) -> str:
        # A distinct spelling the fake demangler maps to the same text.
        return self.mangled + "#variant"


def _fake_demangle(s: str) -> str | None:
    if not s.startswith("_Z"):
        return None
    return "demangled:" + s.split("#", 1)[0]


@st.composite
def worlds(draw):
    bases = draw(st.lists(_BASES, unique=True, max_size=8))
    return [
        Entity(
            base=b,
            cxx=draw(st.booleans()),
            exposures=frozenset(draw(st.sets(st.sampled_from(EXPOSURES)))),
        )
        for b in bases
    ]


def _tables(world):
    """``(dynsym ElfSymbols, static spelling set)`` from ground truth."""
    dyn: list[ElfSymbol] = []
    static: set[str] = set()
    for e in world:
        x = e.exposures
        if "dyn_default" in x:
            dyn.append(ElfSymbol(name=e.mangled, version="V2", is_default=True))
        if "dyn_nondefault" in x:
            dyn.append(ElfSymbol(name=e.mangled, version="V1", is_default=False))
        if "static" in x:
            static.add(e.mangled)
        if "static_versioned_literal" in x:
            static.add(e.mangled + "@@V2")
        if "dyn_bare_name" in x and e.cxx:
            dyn.append(ElfSymbol(name=e.name))
        if "dyn_mangling_variant" in x and e.cxx:
            dyn.append(ElfSymbol(name=e.variant))
    return dyn, static


def _oracle_tier(e: Entity, *, demangled_tier: bool, static_tier: bool = True) -> str:
    """Ground-truth tier, read off the exposures directly. *demangled_tier*/
    *static_tier*: whether the producer consults that evidence at all (the
    DWARF producer reads only ``.dynsym``; the header-AST ones do not demangle)."""
    x = e.exposures
    if x & {"dyn_default", "dyn_nondefault"}:
        return "dynamic"
    if e.cxx and "dyn_bare_name" in x:
        return "name_alias"
    if demangled_tier and e.cxx and "dyn_mangling_variant" in x:
        return "demangled"
    if static_tier and "static" in x:
        return "static_only"
    return "absent"


def _dyn_names(dyn):
    return {s.name for s in dyn}


# ---------------------------------------------------------------------------
# The primitive and each producer's wiring agree with ground truth
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(worlds())
def test_match_export_primitive_matches_ground_truth(world):
    from abicheck.model.export_index import match_export

    dyn, static = _tables(world)
    names = _dyn_names(dyn)
    demangled = {d for n in names if (d := _fake_demangle(n))}
    for e in world:
        got = match_export(
            (e.mangled,),
            e.name,
            dynamic=names,
            static=static | names,
            demangled_dynamic=demangled,
            demangle=_fake_demangle,
        )
        assert got.value == _oracle_tier(e, demangled_tier=True), e


@settings(max_examples=150, deadline=None)
@given(worlds())
def test_castxml_and_clang_producers_record_the_ground_truth_tier(world):
    from types import SimpleNamespace

    from abicheck.extract.headers.castxml.location import export_match
    from abicheck.extract.headers.clang.context import visibility_and_surface_facts
    from abicheck.extract.surface_fact_producers import header_ast_surface_facts

    dyn, static = _tables(world)
    names = _dyn_names(dyn)
    ctx = SimpleNamespace(
        exported_dynamic=names, exported_static=static | names, no_binary_evidence=False
    )
    for e in world:
        want = _oracle_tier(e, demangled_tier=False)
        cx = header_ast_surface_facts(exported=export_match(ctx, e.mangled, e.name))
        _, cl = visibility_and_surface_facts(names, static | names, e.mangled, e.name)
        for facts in (cx, cl):
            fn = Function(name=e.name, mangled=e.mangled, return_type="void", **facts)
            assert _recorded_tier(fn) == want, (e, facts)


@settings(max_examples=100, deadline=None)
@given(worlds())
def test_dwarf_producer_records_the_ground_truth_tier(world):
    from unittest import mock

    from abicheck import demangle as demangle_mod
    from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
    from abicheck.extract.surface_fact_producers import debug_info_surface_facts

    dyn, _ = _tables(world)
    with (
        mock.patch.object(demangle_mod, "demangle", _fake_demangle),
        mock.patch.object(
            demangle_mod,
            "demangle_batch",
            lambda xs: {x: d for x in xs if (d := _fake_demangle(x))},
        ),
    ):
        builder = _DwarfSnapshotBuilder(Path("libx.so"), ElfMetadata(symbols=dyn))
        _assert_dwarf_tiers(world, builder, debug_info_surface_facts)


def _assert_dwarf_tiers(world, builder, debug_info_surface_facts):
    for e in world:
        facts = debug_info_surface_facts(
            exported=builder._export_match(e.mangled, e.name)
        )
        fn = Function(name=e.name, mangled=e.mangled, return_type="void", **facts)
        want = _oracle_tier(e, demangled_tier=True, static_tier=False)
        assert _recorded_tier(fn) == want, e


def _recorded_tier(decl) -> str:
    from abicheck.model.surface_facts import binary_export_match

    m = binary_export_match(decl)
    return "unknown" if m is None else m.value


# ---------------------------------------------------------------------------
# The fact and the join can never silently disagree
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(worlds(), st.booleans())
def test_fact_is_present_exactly_when_the_export_join_matches(world, as_variable):
    """``PRESENT(True)`` <=> the join joined the declaration; ``PRESENT(False)``
    <=> nothing anywhere; every weaker tier is ``PARTIAL(True)`` -- still
    truthy (no finding moves) but never mistaken for the join's answer."""
    from abicheck.compare.export_join import join_exports
    from abicheck.extract.surface_fact_producers import header_ast_surface_facts
    from abicheck.model.export_index import match_export

    dyn, static = _tables(world)
    names = _dyn_names(dyn)
    demangled = {d for n in names if (d := _fake_demangle(n))}
    decls = []
    for e in world:
        tier = match_export(
            (e.mangled,),
            e.name,
            dynamic=names,
            static=static | names,
            demangled_dynamic=demangled,
            demangle=_fake_demangle,
        )
        facts = header_ast_surface_facts(exported=tier)
        cls = Variable if as_variable else Function
        kw = {"type": "int"} if as_variable else {"return_type": "void"}
        decls.append(
            (
                e,
                cls(
                    name=e.name,
                    mangled=e.mangled,
                    visibility=Visibility.PUBLIC,
                    **kw,
                    **facts,
                ),
            )
        )
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[] if as_variable else [d for _, d in decls],
        variables=[d for _, d in decls] if as_variable else [],
        elf=ElfMetadata(symbols=dyn),
    )
    j = join_exports(snap)
    id_list = j.identities.variables if as_variable else j.identities.functions
    for (e, decl), ident in zip(decls, id_list):
        joined = j.declaration(ident.node_id).state in (
            JoinState.MATCHED,
            JoinState.AMBIGUOUS,
        )
        fact = binary_exported(decl)
        tier = _oracle_tier(e, demangled_tier=True)
        assert joined == (tier == "dynamic"), e
        assert (fact.status is FactStatus.PRESENT and fact.value is True) == joined, e
        assert (fact.status is FactStatus.PRESENT and fact.value is False) == (
            tier == "absent"
        ), e
        # Truth-ness is preserved: every non-absent tier still reads exported.
        assert is_binary_exported(decl) == (tier != "absent"), e


def test_exhaustive_small_domain_reaches_every_tier():
    """Every exposure subset x linkage, exhaustively (128 worlds): the
    primitive agrees with the oracle and the oracle is not vacuous -- all
    five tiers occur, so the property tests above cannot pass on a
    constant."""
    from itertools import combinations

    from abicheck.model.export_index import ExportMatch, match_export

    seen = set()
    for cxx in (False, True):
        for k in range(len(EXPOSURES) + 1):
            for combo in combinations(EXPOSURES, k):
                e = Entity(base="f", cxx=cxx, exposures=frozenset(combo))
                dyn, static = _tables([e])
                names = _dyn_names(dyn)
                got = match_export(
                    (e.mangled,),
                    e.name,
                    dynamic=names,
                    static=static | names,
                    demangled_dynamic={d for n in names if (d := _fake_demangle(n))},
                    demangle=_fake_demangle,
                )
                want = _oracle_tier(e, demangled_tier=True)
                assert got.value == want, e
                seen.add(want)
    assert seen == {m.value for m in ExportMatch}


# ---------------------------------------------------------------------------
# Edge states of the read-back and the adapters
# ---------------------------------------------------------------------------


def _fn_with(fact):
    return Function(
        name="f", mangled="f", return_type="void", binary_exported_fact=fact
    )


def test_binary_export_match_is_none_when_the_tier_is_not_recoverable():
    from abicheck.model.fact import Fact
    from abicheck.model.surface_facts import binary_export_match

    assert binary_export_match(_fn_with(Fact.not_collected("no binary"))) is None
    assert binary_export_match(_fn_with(Fact.partial(True, "other"))) is None
    assert (
        binary_export_match(_fn_with(Fact.partial(True, "export-match:bogus"))) is None
    )
    assert (
        binary_export_match(_fn_with(Fact.partial(False, "export-match:static_only")))
        is None
    )
    # Legacy-derived (no stored fact) is not a producer's tier either.
    assert (
        binary_export_match(Function(name="f", mangled="f", return_type="void")) is None
    )


def test_castxml_rewritten_c_linkage_spelling_is_dynamic_not_alias():
    """A C-linkage entity whose pseudo-mangled name was rewritten to its bare
    name: the bare-name hit *is* its linker spelling's hit."""
    from types import SimpleNamespace

    from abicheck.extract.headers.castxml.location import export_match
    from abicheck.model.export_index import ExportMatch

    ctx = SimpleNamespace(exported_dynamic={"x"}, exported_static={"x"})
    assert export_match(ctx, "_Z1x", "x") is ExportMatch.NAME_ALIAS
    assert export_match(ctx, "_Z1x", "x", "x") is ExportMatch.DYNAMIC
    assert export_match(ctx, "_Z1x", "x", "_Z1x") is ExportMatch.NAME_ALIAS


def test_clang_export_match_is_unknown_without_a_binary():
    from abicheck.extract.headers.clang.context import export_match

    assert export_match({"f"}, {"f"}, "f", "f", True) is None


def test_dwarf_is_exported_is_any_tier_but_absent():
    from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

    b = _DwarfSnapshotBuilder(
        Path("libx.so"), ElfMetadata(symbols=[ElfSymbol(name="foo")])
    )
    assert b._is_exported("foo", "foo")
    assert not b._is_exported("bar", "bar")


def test_bare_bool_export_answer_is_a_plain_present_fact():
    from abicheck.extract.surface_fact_producers import binary_exported_fact_for

    for v in (True, False):
        f = binary_exported_fact_for(v, producer="t")
        assert f.status is FactStatus.PRESENT and f.value is v
