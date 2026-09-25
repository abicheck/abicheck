"""A castxml constructor/destructor placeholder resolves to its linker
identity through the export table (evidence-entity-model plan, Phase 1 gap:
castxml ctor/dtor placeholders are ``unresolved``).

castxml 0.7.0 records no ``mangled`` attribute on a ``Constructor``/
``Destructor`` element, so the castxml parser keys one on a placeholder
(``__abicheck_ctor__ns::W(int)``, ``~ns::W``). The identity table pairs such a
placeholder with an exported Itanium variant family (``C1``/``C2``/``C3``,
``D0``/``D1``/``D2``) one-to-one, and the node id becomes the complete-object
spelling clang itself reports.

The oracle is independent of the implementation: the expected symbols are
built here by a small Itanium mangler for the test's own type domain
(builtins and namespace-scoped class names, no substitutions needed), and the
expected resolution follows the rule stated in prose -- "resolved iff the
owner exports at least one variant of exactly this overload and no other
declared overload of that owner has the same unqualified signature" -- never
by calling the module's own helpers.
"""

from __future__ import annotations

import random
import shutil

import pytest

from abicheck.demangle import demangle
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.graph_join import JoinState

_HAS_DEMANGLER = demangle("_ZN2ns1WC1Ei") == "ns::W::W(int)"
needs_demangler = pytest.mark.skipif(
    not _HAS_DEMANGLER and shutil.which("c++filt") is None,
    reason="constructor parameter matching needs cxxfilt or c++filt",
)

# (castxml spellings, Itanium code). Several castxml spellings per builtin:
# castxml/clang print `unsigned long`, a typedef target may read
# `long unsigned int` -- both name one type.
_BUILTINS = {
    "int": (("int",), "i"),
    "long": (("long", "long int"), "l"),
    "ulong": (("unsigned long", "long unsigned int"), "m"),
    "double": (("double",), "d"),
    "char": (("char",), "c"),
}


def _source_name(parts):
    return "".join(f"{len(p)}{p}" for p in parts)


def _ctor(scope, codes, variant="C1"):
    return f"_ZN{_source_name(scope)}{variant}E{''.join(codes) or 'v'}"


def _dtor(scope, variant="D1"):
    return f"_ZN{_source_name(scope)}{variant}Ev"


def _ctor_placeholder(scope, spellings):
    return f"__abicheck_ctor__{'::'.join(scope)}({','.join(spellings)})"


def _fn(key, name="W", *params):
    return Function(
        name=name,
        mangled=key,
        return_type="void",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions, exports, typedefs_qualified=None):
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=list(functions),
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exports]),
        typedefs_qualified=dict(typedefs_qualified or {}),
    )


def _node(ids, i):
    return ids.functions[i]


def _identities(snap):
    from abicheck.model.snapshot_identity_table import identities_for_snapshot

    return identities_for_snapshot(snap)


# ---------------------------------------------------------------------------
# Generated domain: the resolution rule as an invariant.
# ---------------------------------------------------------------------------


def _scenario(rng, complete=None):
    """*complete*, when given, collects every declared member's
    complete-object spelling -- what a header AST would report."""
    scopes = [("ns", "W"), ("ns", "V"), ("other", "W"), ("W",), ("a", "b", "W")]
    functions, exports, expected = [], set(), {}
    for scope in rng.sample(scopes, rng.randint(1, 3)):
        overloads = set()
        for _ in range(rng.randint(1, 3)):
            overloads.add(
                tuple(rng.choice(sorted(_BUILTINS)) for _ in range(rng.randint(0, 2)))
            )
        for params in sorted(overloads):
            spellings = [rng.choice(_BUILTINS[p][0]) for p in params]
            codes = [_BUILTINS[p][1] for p in params]
            idx = len(functions)
            functions.append(_fn(_ctor_placeholder(scope, spellings), scope[-1]))
            if complete is not None:
                complete.append(_ctor(scope, codes))
            variants = rng.sample(["C1", "C2", "C3"], rng.randint(0, 2))
            names = {_ctor(scope, codes, v) for v in variants}
            exports |= names
            if names:
                expected[idx] = (
                    f"decl://{_ctor(scope, codes)}",
                    {f"decl://{n}" for n in names} - {f"decl://{_ctor(scope, codes)}"},
                )
        idx = len(functions)
        functions.append(_fn(f"~{'::'.join(scope)}", f"~{scope[-1]}"))
        if complete is not None:
            complete.append(_dtor(scope))
        variants = rng.sample(["D0", "D1", "D2"], rng.randint(0, 3))
        names = {_dtor(scope, v) for v in variants}
        exports |= names
        if names:
            expected[idx] = (
                f"decl://{_dtor(scope)}",
                {f"decl://{n}" for n in names} - {f"decl://{_dtor(scope)}"},
            )
    return functions, exports, expected


@needs_demangler
def test_placeholder_resolves_to_its_exported_variant_family():
    """60 generated scenarios in one test, so a failure names every
    disagreeing seed at once and the oracle cannot be vacuous."""
    failures = {}
    resolved_total = 0
    for seed in range(60):
        rng = random.Random(seed)
        functions, exports, expected = _scenario(rng)
        resolved_total += len(expected)
        ids = _identities(_snap(functions, exports))
        got = {
            i: (ident.node_id, set(ident.aliases))
            for i, ident in enumerate(ids.functions)
            if ident.resolved
        }
        if got != expected:
            failures[seed] = (got, expected)
        for i, ident in enumerate(ids.functions):
            if i not in expected and not ident.node_id.startswith("unresolved://"):
                failures.setdefault(seed, ("resolved without evidence", i))
    assert resolved_total > 60  # vacuity guard on the generator itself
    assert failures == {}


@needs_demangler
@pytest.mark.parametrize("seed", range(10))
def test_resolution_does_not_depend_on_input_order(seed):
    rng = random.Random(seed)
    functions, exports, _ = _scenario(rng)
    base = _identities(_snap(functions, exports))
    by_key = {f.mangled: i for i, f in zip(base.functions, functions)}
    shuffled = functions[:]
    rng.shuffle(shuffled)
    again = _identities(_snap(shuffled, sorted(exports, reverse=True)))
    assert {f.mangled: i for i, f in zip(again.functions, shuffled)} == by_key


# ---------------------------------------------------------------------------
# Named sibling cases.
# ---------------------------------------------------------------------------


class TestSpecialMemberCases:
    @needs_demangler
    def test_complete_and_base_object_ctor_variants(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "W"), ["int"]))],
                ["_ZN2ns1WC1Ei", "_ZN2ns1WC2Ei"],
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ei"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ei",)

    @needs_demangler
    def test_only_base_object_variant_exported_still_keys_on_complete(self):
        ids = _identities(
            _snap([_fn(_ctor_placeholder(("ns", "W"), []))], ["_ZN2ns1WC2Ev"])
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ev"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ev",)

    def test_deleting_complete_and_base_dtor_variants(self):
        ids = _identities(
            _snap(
                [_fn("~ns::W", "~W")], ["_ZN2ns1WD0Ev", "_ZN2ns1WD1Ev", "_ZN2ns1WD2Ev"]
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WD1Ev"
        assert set(ids.functions[0].aliases) == {
            "decl://_ZN2ns1WD0Ev",
            "decl://_ZN2ns1WD2Ev",
        }

    def test_inline_ctor_with_no_export_stays_unresolved(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "I"), [])), _fn("~ns::I", "~I")],
                ["_Z3foov"],
            )
        )
        assert all(i.node_id.startswith("unresolved://") for i in ids.functions)

    @needs_demangler
    def test_implicit_copy_ctor_matches_its_substituted_mangling(self):
        # castxml spells the implicit copy ctor's parameter relative to its
        # scope; the mangling uses a substitution (S0_) the demangler expands.
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "Imp"), ["const Imp&"]))],
                ["_ZN2ns3ImpC1ERKS0_", "_ZN2ns3ImpC2ERKS0_"],
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns3ImpC1ERKS0_"

    def test_template_owner_stays_unresolved(self):
        ids = _identities(_snap([_fn("~ns::B<int>", "~B")], ["_ZN2ns1BIiED1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")

    def test_same_leaf_class_in_another_namespace_does_not_join(self):
        ids = _identities(_snap([_fn("~other::W", "~W")], ["_ZN2ns1WD1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")

    @needs_demangler
    def test_overloads_indistinguishable_after_leaf_reduction_resolve_neither(self):
        fns = [
            _fn(_ctor_placeholder(("A", "W"), ["p::X"])),
            _fn(_ctor_placeholder(("A", "W"), ["q::X"])),
        ]
        ids = _identities(_snap(fns, ["_ZN1A1WC1EN1p1XE", "_ZN1A1WC1EN1q1XE"]))
        assert all(i.node_id.startswith("unresolved://") for i in ids.functions)

    @needs_demangler
    def test_typedef_parameter_found_by_scope_lookup(self):
        ids = _identities(
            _snap(
                [_fn(_ctor_placeholder(("ns", "W"), ["size_type"]))],
                ["_ZN2ns1WC1Em"],
                {"ns::W::size_type": "long unsigned int"},
            )
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Em"

    def test_spelling_owned_by_another_declaration_is_refused(self):
        fns = [_fn("~ns::W", "~W"), _fn("_ZN2ns1WD1Ev", "~W")]
        ids = _identities(_snap(fns, ["_ZN2ns1WD1Ev"]))
        assert ids.functions[0].node_id.startswith("unresolved://")
        assert ids.functions[1].node_id == "decl://_ZN2ns1WD1Ev"

    def test_real_ctor_linker_name_gains_observed_variant_aliases(self):
        ids = _identities(
            _snap([_fn("_ZN2ns1WC1Ei")], ["_ZN2ns1WC1Ei", "_ZN2ns1WC2Ei"])
        )
        assert ids.functions[0].node_id == "decl://_ZN2ns1WC1Ei"
        assert ids.functions[0].aliases == ("decl://_ZN2ns1WC2Ei",)

    def test_class_named_like_a_variant_code_is_not_rewritten(self):
        # `C1Evil` embeds "C1E"; only the structural marker is a variant code.
        ids = _identities(
            _snap([_fn("~C1Evil", "~C1Evil")], ["_ZN6C1EvilD1Ev", "_ZN6C2EvilD2Ev"])
        )
        assert ids.functions[0].node_id == "decl://_ZN6C1EvilD1Ev"
        assert ids.functions[0].aliases == ()


class TestExportJoinOfSpecialMembers:
    @needs_demangler
    def test_placeholder_joins_every_variant_export(self):
        from abicheck.compare.export_join import join_exports

        snap = _snap(
            [_fn(_ctor_placeholder(("ns", "W"), ["int"])), _fn("~ns::W", "~W")],
            [
                "_ZN2ns1WC1Ei",
                "_ZN2ns1WC2Ei",
                "_ZN2ns1WD0Ev",
                "_ZN2ns1WD1Ev",
                "_ZN2ns1WD2Ev",
            ],
        )
        j = join_exports(snap)
        ctor = j.declaration("decl://_ZN2ns1WC1Ei")
        assert ctor.state is JoinState.MATCHED
        assert ctor.candidates == (
            "binary_symbol://elf/_ZN2ns1WC1Ei",
            "binary_symbol://elf/_ZN2ns1WC2Ei",
        )
        assert j.declaration("decl://_ZN2ns1WD1Ev").state is JoinState.MATCHED
        assert {r.state for r in j.join.right.values()} == {JoinState.MATCHED}


# ---------------------------------------------------------------------------
# Version symmetry: identity must not flip when only export evidence changes.
# ---------------------------------------------------------------------------


def _header_graph(functions, exports, *, ast_names, ast_edges=()):
    from abicheck.buildsource.header_graph import build_header_only_graph
    from abicheck.buildsource.header_graph_ast_projection import (
        HeaderGraphAstProjection,
    )

    snap = _snap(functions, exports)
    projection = HeaderGraphAstProjection(
        type_edges=list(ast_edges), special_member_names=frozenset(ast_names)
    )
    graph = build_header_only_graph(
        snap, ast_projection=projection, header_paths=["/inc/a.h"]
    )
    snap.surface_graph = graph
    return snap, graph


def _declared(functions):
    for f in functions:
        f.source_header = "/inc/a.h"
    return functions


@needs_demangler
def test_export_evidence_change_never_flips_an_unchanged_members_identity():
    """Generated version pairs: the declarations and the header AST are
    identical, only which variants the binary exports differs (each side a
    random subset). Every placeholder keeps one node id across the two
    versions -- in the table rebuilt from each stored snapshot -- and the
    graph diff reports no reachability change. Before the AST evidence, an
    unchanged constructor whose ``C2`` export appeared in the new release
    flipped from ``unresolved://`` to ``decl://`` and read as having entered
    the public closure (oneDAL 2025.10 -> 2025.11, ``BatchBase``)."""
    from abicheck.buildsource.source_graph_findings import (
        diff_source_graph_findings,
    )
    from abicheck.buildsource.type_graph import TypeEdge
    from abicheck.model.change_catalog.kinds import ChangeKind

    flips = {}
    for seed in range(40):
        rng = random.Random(seed)
        complete: list[str] = []
        functions, exports, _ = _scenario(rng, complete)
        # The AST's type pass also keys a node on the member (the premise of
        # the original false finding: the old graph held it, unlinked).
        edges = [TypeEdge(src=n, dst="int", kind="DECL_HAS_TYPE") for n in complete]
        sides = []
        for _ in range(2):
            subset = {e for e in sorted(exports) if rng.random() < 0.5}
            fns = _declared([_fn(f.mangled, f.name) for f in functions])
            sides.append(
                _header_graph(fns, subset, ast_names=complete, ast_edges=edges)
            )
        (old_snap, old_g), (new_snap, new_g) = sides
        # What a reader of each stored snapshot sees: the table rebuilt from
        # the export table, resolved through that side's persisted graph.
        old_ids = [
            old_g.resolve_node_id(i.node_id) for i in _identities(old_snap).functions
        ]
        new_ids = [
            new_g.resolve_node_id(i.node_id) for i in _identities(new_snap).functions
        ]
        if old_ids != new_ids:
            flips[seed] = ("table", old_ids, new_ids)
        kinds = {c.kind for c in diff_source_graph_findings(old_g, new_g)}
        if ChangeKind.PUBLIC_REACHABILITY_CHANGED in kinds:
            flips[seed] = ("reachability", kinds)
    assert flips == {}


def test_without_ast_evidence_an_unexported_member_stays_unresolved():
    snap, _graph = _header_graph(_declared([_fn("~ns::W", "~W")]), [], ast_names=())
    assert _identities(snap).functions[0].node_id.startswith("unresolved://")


def test_ast_evidence_alone_resolves_an_unexported_destructor():
    snap, graph = _header_graph(
        _declared([_fn("~ns::W", "~W")]), [], ast_names=["_ZN2ns1WD1Ev"]
    )
    assert "decl://_ZN2ns1WD1Ev" in {n.id for n in graph.nodes}
    # The stored snapshot's export-only table keys it on the placeholder,
    # which the graph records as an alias of the AST-resolved node.
    table_id = _identities(snap).functions[0].node_id
    assert table_id.startswith("unresolved://")
    assert graph.resolve_node_id(table_id) == "decl://_ZN2ns1WD1Ev"
