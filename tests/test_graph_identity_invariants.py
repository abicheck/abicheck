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

"""Invariant I1 ("one ID per entity") across the graph *producers*
(evidence-entity-model plan, Phase 1).

The oracle in every test is a hand-written ground-truth list of distinct
entities, never the identity helper the producers use: a test states how many
entities a fixture holds and which of them must stay apart, and checks that
the header graph and the public-surface builder -- writing into one shared
graph, as ``service_header_graph_attach`` does -- produce exactly one node per
entity. Pure-function properties of the identity helper itself live in
``tests/test_graph_entity_identity.py``.
"""

from __future__ import annotations

import random

from hypothesis import given, settings, strategies as st

from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.buildsource.source_graph_build_source_abi import (
    _augment_with_source_abi,
)
from abicheck.compare.surface_graph import build_public_surface_facts
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    Variable,
)
from abicheck.model.identity import (
    InlineNamespace,
    Namespace,
    entity_id_for_function,
    entity_id_for_type,
)

#: Node kinds that stand for a function/variable declaration, under either
#: producer's historical vocabulary.
DECL_KINDS = frozenset({"source_decl", "declaration"})
#: Node kinds that stand for a record/enum/typedef.
TYPE_KINDS = frozenset({"record_type", "enum_type", "typedef", "type"})


def _snap(**kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", **kwargs)  # type: ignore[arg-type]


def _graph(snap: AbiSnapshot):
    """Both L2 producers over one shared graph, as the dump attach step does
    when the facts pass is enabled."""
    graph = build_header_only_graph(snap)
    build_public_surface_facts(snap, graph)
    return graph


def _decl_ids(graph) -> set[str]:
    return {n.id for n in graph.nodes if n.kind in DECL_KINDS}


def _type_ids(graph) -> set[str]:
    return {n.id for n in graph.nodes if n.kind in TYPE_KINDS}


def _fn(name: str, mangled: str, *, scope=(), extern_c: bool = False) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        source_header="a.h",
        is_extern_c=extern_c,
        entity_id=entity_id_for_function(
            scope,
            name.rsplit("::", 1)[-1],
            mangled_name=None if extern_c else mangled,
            is_extern_c=extern_c,
        ),
    )


def _real_functions() -> list[Function]:
    ns = (Namespace("ns"),)
    return [
        _fn("c_fn", "c_fn", extern_c=True),
        _fn("ns::f", "_ZN2ns1fEi", scope=ns),
        _fn("ns::f", "_ZN2ns1fEd", scope=ns),
        _fn("ns::W::m", "_ZNK2ns1W1mEv", scope=ns),
    ]


def _rec(qname: str, scope) -> RecordType:
    leaf = qname.rsplit("::", 1)[-1]
    return RecordType(
        name=leaf,
        kind="struct",
        qualified_name=qname,
        source_header="a.h",
        entity_id=entity_id_for_type(scope, leaf),
    )


class TestSurfaceAndHeaderGraphAgree:
    def test_one_node_per_declaration(self) -> None:
        """Four distinct functions (one C-linkage, two overloads, one
        method) -> four declaration nodes, not one per producer."""
        graph = _graph(_snap(functions=_real_functions()))
        assert len(_decl_ids(graph)) == 4

    def test_variable_joins_across_producers(self) -> None:
        var = Variable(
            name="ns::g", mangled="_ZN2ns1gE", type="int", source_header="a.h"
        )
        graph = _graph(_snap(variables=[var]))
        assert len(_decl_ids(graph)) == 1

    def test_same_named_types_in_different_namespaces_stay_apart(self) -> None:
        """``ns::W`` and ``other::W`` are two entities; an enum is a third."""
        types = [
            _rec("ns::W", (Namespace("ns"),)),
            _rec("other::W", (Namespace("other"),)),
        ]
        enums = [EnumType(name="E", qualified_name="ns::E", source_header="a.h")]
        graph = _graph(_snap(types=types, enums=enums))
        assert len(_type_ids(graph)) == 3

    def test_inline_namespace_spellings_stay_apart_without_evidence(self) -> None:
        """castxml spells an inline-namespace member without the inline
        segment (``ns::S``), clang with it (``ns::v1::S``). Nothing in the
        record itself proves the two the same (G15), so a hybrid snapshot
        carrying both keeps two nodes -- and neither collapses onto a bare
        ``S`` shared with an unrelated record."""
        types = [
            _rec("ns::S", (Namespace("ns"),)),
            _rec("ns::v1::S", (Namespace("ns"), InlineNamespace("v1"))),
            _rec("other::S", (Namespace("other"),)),
        ]
        graph = _graph(_snap(types=types))
        assert len(_type_ids(graph)) == 3


class TestUnresolved:
    def _ctors(self) -> list[Function]:
        # castxml's constructor placeholders: not linker names.
        return [
            Function(
                name="W",
                mangled="__abicheck_ctor__ns::W(int)",
                return_type="",
                source_header="a.h",
            ),
            Function(
                name="W",
                mangled="__abicheck_ctor__ns::W(double)",
                return_type="",
                source_header="a.h",
            ),
            Function(name="~W", mangled="~ns::W", return_type="", source_header="a.h"),
        ]

    def test_placeholders_become_explicit_unresolved_nodes(self) -> None:
        graph = _graph(_snap(functions=self._ctors()))
        decls = [n for n in graph.nodes if n.kind in DECL_KINDS]
        assert len(decls) == 3
        assert all(n.attrs.get("identity") == "unresolved" for n in decls)

    def test_unresolved_never_collides_with_resolved(self) -> None:
        fns = [*self._ctors(), *_real_functions()]
        graph = _graph(_snap(functions=fns))
        assert len(_decl_ids(graph)) == 7
        unresolved = {
            n.id for n in graph.nodes if n.attrs.get("identity") == "unresolved"
        }
        assert len(unresolved) == 3

    def test_overloads_without_linker_names_never_merge(self) -> None:
        """Two overloads with no linker name at all (no evidence they are
        the same entity) stay two nodes in *every* producer -- the header
        graph used to collapse both onto ``decl://f``."""
        fns = [
            Function(
                name="f", mangled="", return_type="int", params=[], source_header="a.h"
            ),
            Function(
                name="f", mangled="", return_type="void", params=[], source_header="a.h"
            ),
        ]
        snap = _snap(functions=fns)
        header_only = build_header_only_graph(snap)
        surface_only = build_header_only_graph(_snap())
        build_public_surface_facts(snap, surface_only)
        # Each producer alone keeps both, and together they still agree on
        # two nodes -- one producer's merge cannot hide behind the other's
        # extra node.
        assert len(_decl_ids(header_only)) == 2
        assert len(_decl_ids(surface_only)) == 2
        assert _decl_ids(header_only) == _decl_ids(surface_only)
        assert len(_decl_ids(_graph(snap))) == 2


class TestAlias:
    def test_macho_decorated_spelling_is_an_alias_not_a_node(self) -> None:
        """clang reports the Mach-O decorated ``__Z...`` spelling; the AST
        replay passes strip it. One entity, one node, and the decorated
        spelling resolves to it."""
        fn = _fn("ns::f", "__ZN2ns1fEi", scope=(Namespace("ns"),))
        graph = _graph(_snap(functions=[fn]))
        assert _decl_ids(graph) == {"decl://_ZN2ns1fEi"}
        assert graph.resolve_node_id("decl://__ZN2ns1fEi") == "decl://_ZN2ns1fEi"


class TestL4Join:
    def test_source_entity_joins_the_snapshot_declaration(self) -> None:
        """An L4 ``SourceEntity`` for the same declarations (a C++ overload by
        mangled name; a C-linkage function whose extractor recorded the
        observed linker name) lands on the header graph's node."""
        snap = _snap(functions=_real_functions()[:2])
        graph = build_header_only_graph(snap)
        surface = SourceAbiSurface(
            reachable_declarations=[
                SourceEntity(
                    id="1",
                    kind="function",
                    qualified_name="ns::f",
                    mangled_name="_ZN2ns1fEi",
                    signature_hash="sha256:aa",
                ),
                SourceEntity(
                    id="2",
                    kind="function",
                    qualified_name="c_fn",
                    mangled_name="",
                    signature_hash="sha256:bb",
                    names={"linker": "c_fn"},
                ),
            ]
        )
        _augment_with_source_abi(graph, surface)
        assert len(_decl_ids(graph)) == 2


@settings(max_examples=40, deadline=None)
@given(st.randoms(use_true_random=False))
def test_ids_do_not_depend_on_input_order(rnd: random.Random) -> None:
    fns = _real_functions()
    types = [
        _rec("ns::W", (Namespace("ns"),)),
        _rec("other::W", (Namespace("other"),)),
    ]
    baseline = {n.id for n in _graph(_snap(functions=fns, types=types)).nodes}
    rnd.shuffle(fns)
    rnd.shuffle(types)
    shuffled = {n.id for n in _graph(_snap(functions=fns, types=types)).nodes}
    assert shuffled == baseline


def test_clang_extractor_records_the_c_linkage_linker_name() -> None:
    """The L4 join above needs the linker name the clang extractor observed;
    it keeps it only where ``mangledName == name`` (C linkage)."""
    from abicheck.buildsource.source_extractors.clang_nodes import _entity_names

    c_fn = {"name": "c_fn", "mangledName": "c_fn"}
    cxx = {"name": "f", "mangledName": "_Z1fv"}
    assert _entity_names("c_fn", "", c_fn)["linker"] == "c_fn"
    assert "linker" not in _entity_names("ns::f", "_Z1fv", cxx)
    assert "linker" not in _entity_names("x", "")


class TestAliasNeverCapturesAnotherEntity:
    """CodeRabbit review: on Mach-O, ``exit``'s decorated linker spelling
    ``_exit`` is the plain name of a distinct ``_exit`` function. The alias is
    ambiguous evidence, so the two stay two nodes in either seed order and
    the build never aborts."""

    def _fns(self) -> list[Function]:
        return [
            Function(
                name="exit", mangled="_exit", return_type="void", source_header="a.h"
            ),
            Function(
                name="_exit", mangled="__exit", return_type="void", source_header="a.h"
            ),
        ]

    def test_both_orders_keep_two_nodes(self) -> None:
        for fns in (self._fns(), list(reversed(self._fns()))):
            graph = _graph(_snap(functions=fns))
            assert _decl_ids(graph) == {"decl://exit", "decl://_exit"}
            assert "decl://_exit" not in graph.identity_aliases


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.from_regex(r"_{0,2}[a-z]{1,3}", fullmatch=True),
            st.from_regex(r"_{0,2}[a-z]{1,3}", fullmatch=True),
        ),
        min_size=1,
        max_size=8,
    ),
    st.randoms(use_true_random=False),
)
def test_no_alias_is_another_declarations_canonical_id(
    pairs: list[tuple[str, str]], rnd: random.Random
) -> None:
    """Oracle: the set of canonical ids the declarations themselves claim --
    an alias equal to one of them would redirect that entity's node."""
    from abicheck.model.graph_entity_identity import snapshot_identities

    fns = [
        Function(name=n, mangled=m, return_type="void", source_header="a.h")
        for n, m in pairs
    ]
    rnd.shuffle(fns)
    ids = snapshot_identities(_snap(functions=fns))
    canonical = {i.node_id for i in ids.functions}
    assert not any(a in canonical for i in ids.functions for a in i.aliases)
    _graph(_snap(functions=fns))  # builds without raising
