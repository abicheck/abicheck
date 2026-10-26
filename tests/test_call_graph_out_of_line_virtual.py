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

"""Known gap "A virtual call through an out-of-line definition is classified
as direct": virtual-ness belongs to the method, not to the redeclaration a
call site happens to resolve to.

Bug class: a per-method property read off one redeclaration node. The
oracle is the C++ source itself -- each shape states, independently of the
implementation, whether the static callee is a virtual method and whether
the call is explicitly qualified -- checked on hand-built AST shapes and on
real clang output for several sibling shapes (in-class vs out-of-line,
base vs override, ``override`` vs ``final`` vs repeated ``virtual``,
non-virtual control, qualified control).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.buildsource.call_graph import parse_clang_ast_calls
from abicheck.model.graph_vocabulary import (
    CALL_KIND_DIRECT,
    CALL_KIND_VIRTUAL,
    RESOLUTION_EXACT,
    RESOLUTION_OVERAPPROX,
)


def _ast(decl_attrs: dict, def_previous: str | None, extra_decls=()) -> dict:
    """``struct S { <decl>; }; <def out of line>; void use(S &s) { s.f(); }``
    with the member call resolving to the *definition* (id ``0xdef``), the
    real clang shape for an out-of-line definition."""
    decl = {
        "kind": "CXXMethodDecl",
        "id": "0xdecl",
        "name": "f",
        "mangledName": "_ZN1S1fEv",
        **decl_attrs,
    }
    definition: dict = {
        "kind": "CXXMethodDecl",
        "id": "0xdef",
        "name": "f",
        "mangledName": "_ZN1S1fEv",
        "inner": [{"kind": "CompoundStmt", "inner": []}],
    }
    if def_previous is not None:
        definition["previousDecl"] = def_previous
    call = {
        "kind": "CXXMemberCallExpr",
        "inner": [
            {
                "kind": "MemberExpr",
                "name": "f",
                "isArrow": False,
                "referencedMemberDecl": "0xdef",
                "inner": [{"kind": "DeclRefExpr"}],
            }
        ],
    }
    use = {
        "kind": "FunctionDecl",
        "id": "0xu",
        "name": "use",
        "mangledName": "_Z3useR1S",
        "inner": [{"kind": "CompoundStmt", "inner": [call]}],
    }
    record = {"kind": "CXXRecordDecl", "name": "S", "inner": [decl, *extra_decls]}
    return {"kind": "TranslationUnitDecl", "inner": [record, definition, use]}


@pytest.mark.parametrize(
    ("decl_attrs", "previous", "expected"),
    [
        ({"virtual": True}, "0xdecl", CALL_KIND_VIRTUAL),
        ({"inner": [{"kind": "OverrideAttr"}]}, "0xdecl", CALL_KIND_VIRTUAL),
        ({"inner": [{"kind": "FinalAttr"}]}, "0xdecl", CALL_KIND_VIRTUAL),
        ({}, "0xdecl", CALL_KIND_DIRECT),  # non-virtual method
        ({"virtual": True}, None, CALL_KIND_DIRECT),  # no chain recorded
        ({"virtual": True}, "0xmissing", CALL_KIND_DIRECT),  # dangling link
    ],
    ids=["virtual", "override", "final", "non-virtual", "no-link", "dangling"],
)
def test_out_of_line_definition_follows_previous_decl(
    decl_attrs: dict, previous: str | None, expected: str
) -> None:
    edges = parse_clang_ast_calls(_ast(decl_attrs, previous))
    assert [(e.callee, e.call_kind) for e in edges] == [("_ZN1S1fEv", expected)]


def test_redeclaration_chain_is_walked_transitively_and_cycle_guarded() -> None:
    # def -> mid -> decl(virtual): two hops.
    mid = {"kind": "CXXMethodDecl", "id": "0xmid", "name": "f",
           "mangledName": "_ZN1S1fEv", "previousDecl": "0xdecl"}  # fmt: skip
    edges = parse_clang_ast_calls(_ast({"virtual": True}, "0xmid", [mid]))
    assert [e.call_kind for e in edges] == [CALL_KIND_VIRTUAL]
    # A cycle between non-virtual redeclarations terminates as direct.
    loop = {"kind": "CXXMethodDecl", "id": "0xloop", "name": "f",
            "mangledName": "_ZN1S1fEv", "previousDecl": "0xdef"}  # fmt: skip
    edges = parse_clang_ast_calls(_ast({}, "0xloop", [loop]))
    assert [e.call_kind for e in edges] == [CALL_KIND_DIRECT]


# --- real clang ------------------------------------------------------------

#: (source, callee mangled name, expected (call_kind, resolution)); the
#: expectation follows from the C++ alone.
_CLANG_SHAPES = {
    "base-out-of-line": (
        "struct B { virtual int f() const; };\n"
        "int B::f() const { return 0; }\n"
        "int use(const B& b) { return b.f(); }\n",
        "_ZNK1B1fEv",
        (CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
    ),
    "base-in-class": (
        "struct B { virtual int f() const { return 0; } };\n"
        "int use(const B& b) { return b.f(); }\n",
        "_ZNK1B1fEv",
        (CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
    ),
    "override-out-of-line": (
        "struct B { virtual int f() const; };\n"
        "struct D : B { int f() const override; };\n"
        "int D::f() const { return 1; }\n"
        "int use(const D& d) { return d.f(); }\n",
        "_ZNK1D1fEv",
        (CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
    ),
    "redeclared-virtual-out-of-line": (
        "struct B { virtual int f() const; };\n"
        "struct D : B { virtual int f() const; };\n"
        "int D::f() const { return 1; }\n"
        "int use(const D& d) { return d.f(); }\n",
        "_ZNK1D1fEv",
        (CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
    ),
    "nonvirtual-out-of-line": (
        "struct B { int f() const; };\n"
        "int B::f() const { return 0; }\n"
        "int use(const B& b) { return b.f(); }\n",
        "_ZNK1B1fEv",
        (CALL_KIND_DIRECT, RESOLUTION_EXACT),
    ),
    "qualified-out-of-line": (
        "struct B { virtual int f() const; };\n"
        "int B::f() const { return 0; }\n"
        "int use(const B& b) { return b.B::f(); }\n",
        "_ZNK1B1fEv",
        (CALL_KIND_DIRECT, RESOLUTION_EXACT),
    ),
}


def _clang_ast(tmp_path: Path, source: str) -> dict:
    src = tmp_path / "tu.cpp"
    src.write_text(source)
    proc = subprocess.run(
        # The callee table spells Itanium names; pin the target so a Windows
        # host (MSVC mangling by default) parses the same TU the same way.
        [
            "clang++",
            "--target=x86_64-unknown-linux-gnu",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


@pytest.mark.integration
@pytest.mark.parametrize("shape", sorted(_CLANG_SHAPES))
def test_real_clang_call_kind_matches_the_source(tmp_path: Path, shape: str) -> None:
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not available")
    source, callee, expected = _CLANG_SHAPES[shape]
    edges = parse_clang_ast_calls(_clang_ast(tmp_path, source))
    got = [(e.call_kind, e.resolution) for e in edges if e.callee == callee]
    assert got == [expected], (shape, edges)


@pytest.mark.integration
def test_out_of_line_virtual_call_reaches_the_dispatch_join(tmp_path: Path) -> None:
    """The real producer end to end: the L5 pass over a TU whose base method
    is defined out of line now joins the call onto the derived override."""
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not available")
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.l5_ast_pass import fold_semantic_graphs
    from abicheck.model.source_graph import SourceGraphSummary

    tu = tmp_path / "tu.cpp"
    tu.write_text(
        "struct Shape { virtual double area() const; virtual ~Shape(); };\n"
        "struct Sq : Shape { double area() const override; };\n"
        "double Shape::area() const { return 0; }\n"
        "double Sq::area() const { return 1; }\n"
        "double use(const Shape& s) { return s.area(); }\n"
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(
                id=f"cu://{tu}",
                source=str(tu),
                directory=str(tmp_path),
                language="CXX",
            )
        ]
    )
    graph = SourceGraphSummary()
    fold_semantic_graphs(graph, build, "clang++", [])
    dispatch = {
        (e.src, e.dst) for e in graph.edges if e.kind == "VIRTUAL_CALL_MAY_DISPATCH_TO"
    }
    # Mangling-agnostic (Itanium on ELF/Mach-O, MSVC on Windows): the caller
    # is ``use`` and the joined target is ``Sq::area``.
    assert any("use" in s and "area" in d and "Sq" in d for s, d in dispatch), sorted(
        dispatch
    )
