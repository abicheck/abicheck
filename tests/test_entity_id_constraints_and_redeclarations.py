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

"""``EntityId`` for C++20-constrained overloads and out-of-line redeclarations.

Bug class: two declarations clang treats as DISTINCT entities collapse onto
one ``EntityId`` (overloads that differ only in a ``requires``-clause or
type-constraint), and two declarations clang treats as the SAME entity get
two (an out-of-line member definition scoped by lexical, not semantic,
nesting). Known gap "EntityId scope collisions in template declarations".

Oracle: clang's own redeclaration chain, independent of the code under
test. Every function/variable node clang dumps either starts a chain or
names the earlier declaration it redeclares (``previousDecl``). The
invariant checked over each header: two parsed declarations share an
``EntityId`` exactly when they belong to the same clang chain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.clang.constraints import FunctionConstraintIndex
from abicheck.extract.headers.clang.locations import materialize_locations

_CONCEPTS = """
namespace a { template <class T> concept C = sizeof(T) > 1; }
namespace b { template <class T> concept C = sizeof(T) > 1; }
template <class T> concept C1 = sizeof(T) > 1;
template <class T> concept C2 = sizeof(T) > 2;
template <class T> concept C3 = sizeof(T) > 3;
"""

#: Sibling groups. Each declaration is legal and distinct to clang unless it
#: is written as a redeclaration (a repeated signature, possibly renamed).
_SIBLINGS = {
    "single_concept": """
        template <class T> requires C1<T> void f();
        template <class T> requires C2<T> void f();
        template <class T> requires C3<T> void f();
        template <class T> void f();
    """,
    "same_named_concepts_in_two_namespaces": """
        template <class T> requires a::C<T> void f();
        template <class T> requires b::C<T> void f();
    """,
    "conjunction_disjunction_order": """
        template <class T> requires C1<T> && C2<T> void f();
        template <class T> requires C2<T> && C1<T> void f();
        template <class T> requires C1<T> || C2<T> void f();
        template <class T> requires (C1<T> && C2<T>) || C3<T> void f();
        template <class T> requires C1<T> && (C2<T> || C3<T>) void f();
    """,
    "clause_vs_constrained_param_vs_trailing": """
        template <class T> requires C1<T> void f(T);
        template <C1 T> void f(T);
        template <class T> void f(T) requires C1<T>;
        template <C2 T> void f(T);
    """,
    "multiple_constrained_params": """
        template <C1 T, C2 U> void f(T, U);
        template <C2 T, C1 U> void f(T, U);
        template <C1 T, C1 U> void f(T, U);
        template <class T, C2 U> void f(T, U);
    """,
    "non_concept_requires_expressions": """
        template <class T> requires (sizeof(T) > 4) void f();
        template <class T> requires (sizeof(T) > 8) void f();
        template <class T> requires requires(T x) { x.go(); } void f();
        template <class T> requires requires(T x) { x.stop(); } void f();
    """,
    "redeclarations_share_identity": """
        template <class T> requires C1<T> void f();
        template <class U> requires C1<U> void f();
        template <class T> requires C2<T> void f();
        template <class V> requires C2<V> void f() {}
        template <C3 T> void g(T);
        template <C3 W> void g(W) {}
    """,
    "class_template_members": """
        template <class T> struct A {
          void m() requires C1<T>;
          void m() requires C2<T>;
          template <class U> requires C1<U> void t(U);
          template <class U> requires C2<U> void t(U);
          template <class U> requires C1<U> && C1<T> void t(U);
        };
        template <class T> template <class U> requires C2<U> void A<T>::t(U) {}
        template <class X> template <class Y> requires C1<Y> void A<X>::t(Y) {}
    """,
    "out_of_line_member_templates": """
        namespace n {
        struct A {
          template <class T> void f(T);
          template <class T> void f(T*);
          struct B { template <class T> void h(T); };
          static int s;
        };
        template <class T> void A::f(T) {}
        template <class T> void A::f(T*) {}
        template <class T> void A::B::h(T) {}
        int A::s = 0;
        template <class U> struct C {
          template <class T> void k(T);
          template <class T> requires C1<T> void k(T);
          static int s;
          struct D { template <class T> void d(T); };
        };
        template <class U> template <class T> void C<U>::k(T) {}
        template <class V> template <class T> requires C1<T> void C<V>::k(T) {}
        template <class U> int C<U>::s = 0;
        template <class U> template <class T> void C<U>::D::d(T) {}
        }
    """,
}


def _dump(text: str, tmp_path: Path, name: str) -> dict[str, Any]:
    header = tmp_path / f"{name}.hpp"
    header.write_text(text)
    out = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-std=c++20",
            "--target=x86_64-unknown-linux-gnu",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # The production backend materializes locations before parsing.
    return materialize_locations(json.loads(out.stdout))


def _clang_chains(root: dict[str, Any]) -> dict[str, str]:
    """``decl id -> id of the first declaration of its clang chain``."""
    previous: dict[str, str] = {}
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("previousDecl"), str):
                previous[node["id"]] = node["previousDecl"]
            stack.extend(node.get("inner") or [])

    def first(decl_id: str) -> str:
        while decl_id in previous:
            decl_id = previous[decl_id]
        return decl_id

    return {k: first(k) for k in previous}


def _identity_by_chain(
    root: dict[str, Any], *, constraints: bool = True
) -> list[tuple[str, Any]]:
    """``(clang chain, EntityId)`` per parsed function and variable."""
    parser = _ClangAstParser(root, set(), set())
    chains = _clang_chains(root)
    if not constraints:
        from abicheck.extract.headers.clang import functions as fmod

        functions = fmod.parse_functions(
            parser._functions,
            exported_dynamic=set(),
            exported_static=set(),
            virtual_mangled_names=frozenset(),
            target_triple=None,
            default_value=lambda _p: None,
        )
    else:
        functions = parser.parse_functions()
    entries = [e.node for e in parser._functions if e.node.get("name")]
    variables = parser.parse_variables()
    var_nodes = [e.node for e in parser._variables]
    assert len(functions) == len(entries)
    assert len(variables) == len(var_nodes)
    out = [
        (chains.get(n["id"], n["id"]), f.entity_id)
        for n, f in zip(entries, functions, strict=True)
    ]
    out += [
        (chains.get(n["id"], n["id"]), v.entity_id)
        for n, v in zip(var_nodes, variables, strict=True)
    ]
    return out


@pytest.mark.parametrize("group", sorted(_SIBLINGS))
@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_entity_id_partitions_declarations_exactly_like_clang(
    group: str, tmp_path: Path
) -> None:
    root = _dump(_CONCEPTS + textwrap.dedent(_SIBLINGS[group]), tmp_path, group)
    pairs = _identity_by_chain(root)
    assert len(pairs) >= 2
    for i, (chain_a, id_a) in enumerate(pairs):
        for chain_b, id_b in pairs[i + 1 :]:
            assert (chain_a == chain_b) == (id_a == id_b), (group, id_a, id_b)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_template_parameter_rename_keeps_constrained_identity(tmp_path: Path) -> None:
    body = """
        template <{p} T> requires C1<T> && (sizeof(T) > 4) void f(T);
        template <class T> requires requires(T x) {{ x.go(); }} void g();
        template <class T> struct A {{ void m() requires C2<T>; }};
    """
    renamed = body.replace("T", "Q").replace("x", "y")
    left = _identity_by_chain(
        _dump(_CONCEPTS + textwrap.dedent(body.format(p="class")), tmp_path, "l")
    )
    right = _identity_by_chain(
        _dump(_CONCEPTS + textwrap.dedent(renamed.format(p="class")), tmp_path, "r")
    )
    assert [i for _c, i in left] == [i for _c, i in right]
    assert all(i.extra.count("requires") == 1 for _c, i in left)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_unconstrained_declarations_keep_their_previous_identity(
    tmp_path: Path,
) -> None:
    """Negative control: with no constraint anywhere, the constraint index
    changes no ``EntityId`` -- identical to a parse that never consults it."""
    header = """
        namespace n {
        template <class T> void f(T);
        template <int N> void f();
        template <class T, class... R> T g(R...);
        template <class T> struct A { void m(T) const; template <class U> void t(U); };
        void plain(int);
        struct S { void q(); };
        }
    """
    root = _dump(textwrap.dedent(header), tmp_path, "plain")
    with_index = _identity_by_chain(root)
    without_index = _identity_by_chain(root, constraints=False)
    assert with_index == without_index
    assert all("requires" not in (i.extra or ()) for _c, i in with_index)


def test_unresolvable_concept_location_reads_unknown_not_a_guess() -> None:
    """A use whose ``ImplicitConceptSpecializationDecl`` location matches no
    ``ConceptDecl`` resolves to ``?`` rather than borrowing another name."""
    fn = {"id": "0x2", "kind": "FunctionDecl", "name": "f", "inner": []}
    use = {
        "kind": "ConceptSpecializationExpr",
        "inner": [
            {
                "kind": "ImplicitConceptSpecializationDecl",
                "loc": {"offset": 99, "file": "h.hpp", "line": 9, "col": 1},
                "inner": [],
            }
        ],
    }
    concept = {
        "kind": "ConceptDecl",
        "name": "Known",
        "loc": {"offset": 10, "file": "h.hpp", "line": 1, "col": 1},
    }
    tmpl = {
        "kind": "FunctionTemplateDecl",
        "inner": [{"kind": "TemplateTypeParmDecl", "name": "T"}, use, fn],
    }
    root = {"kind": "TranslationUnitDecl", "inner": [concept, tmpl]}
    index = FunctionConstraintIndex(root)
    assert index.signature(fn) == ("requires:concept ?<>",)
    concept["loc"] = dict(use["inner"][0]["loc"])
    assert FunctionConstraintIndex(root).signature(fn) == ("requires:concept Known<>",)
    assert FunctionConstraintIndex(root).signature({"kind": "FunctionDecl"}) == ()
