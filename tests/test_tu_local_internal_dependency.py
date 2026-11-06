# SPDX-License-Identifier: Apache-2.0
"""TU-local entities are not internal dependencies of the public API.

Bug class: ``public_api_internal_dependency_added`` /
``public_to_internal_dependency`` fired when a public function's body started
using an entity with *internal linkage* defined in its own ``.cpp`` (an
anonymous-namespace cache, a file-scope ``static`` helper or its private
type; catalog case232). No other translation unit and no consumer can name
such an entity, so it is the function's own implementation, not a separately
changeable internal component. The invariant: an entity with internal linkage
defined in a source file is never an internal-dependency target; an entity
with external linkage, or an internal-linkage entity defined in a *header*
(compiled into every includer), still is.

Oracles independent of the implementation: the linker's own view of linkage
(``nm`` lowercase = local symbol) for the mangling reader, and a real clang
AST for the extractor.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from l5_graph_support import producers_ran_unless_flagged

from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.buildsource.type_graph import TypeEdge, _merge_type_edges
from abicheck.buildsource.type_graph_walk import parse_clang_ast_types
from abicheck.checker_policy import ChangeKind
from abicheck.model.source_graph_query import (
    is_tu_local_file,
    itanium_has_internal_linkage,
)

_DEP_KIND = ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value


# ── Mangling reader ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mangled", "internal"),
    [
        ("_ZN2ui12_GLOBAL__N_110cache_headE", True),  # anon-namespace var
        ("_ZN12_GLOBAL__N_14headE", True),
        ("_ZL10cache_head", True),  # file-scope static var
        ("_ZL6helperv", True),  # file-scope static function
        ("_ZN2uiL10cache_headE", True),  # namespace-scope static var
        ("_ZN1a1bL1kE", True),
        ("_ZN2ui11make_widgetEi", False),
        ("_ZN2ui6Widget5countE", False),  # static data member: external
        ("_ZNK2ui6Widget3getEv", False),
        ("_Z3fooi", False),
        ("_ZNSt6vectorIiSaIiEE9push_backERKi", False),
        ("cache_head", False),  # C spelling: no linkage evidence
        ("", False),
    ],
)
def test_itanium_internal_linkage_spellings(mangled: str, internal: bool) -> None:
    assert itanium_has_internal_linkage(mangled) is internal


@pytest.mark.parametrize(
    ("path", "tu_local"),
    [
        ("/p/src/impl.cpp", True),
        ("impl.cc", True),
        ("impl.c", True),
        ("impl.cxx", True),
        ("impl.C", True),
        ("/p/include/api.h", False),
        ("/p/include/api.hpp", False),
        ("detail.inl", False),
        ("/p/dir.cpp/header", False),
        ("", False),
    ],
)
def test_tu_local_file_is_a_source_not_a_header(path: str, tu_local: bool) -> None:
    assert is_tu_local_file(path) is tu_local


_LINKAGE_SOURCE = """
namespace ui {
namespace { int anon_var = 1; int anon_fn() { return anon_var; } }
static int static_var = 2;
static int static_fn() { return static_var; }
int extern_var = 3;
int extern_fn() { return anon_fn() + static_fn(); }
struct W { static int member; int get() const; };
int W::member = 4;
int W::get() const { return member; }
}
int global_extern = 5;
static int global_static = 6;
int use() { return global_static + ui::extern_var + ui::W::member; }
"""


@pytest.mark.integration
@pytest.mark.skipif(
    not (shutil.which("g++") and shutil.which("nm")), reason="needs g++ and nm"
)
def test_mangling_reader_agrees_with_the_linker(tmp_path: Path) -> None:
    """Every mangled symbol's linkage, as the object file's symbol table says."""
    src = tmp_path / "linkage.cpp"
    src.write_text(_LINKAGE_SOURCE)
    obj = tmp_path / "linkage.o"
    subprocess.run(
        ["g++", "-std=c++17", "-O0", "-c", str(src), "-o", str(obj)], check=True
    )
    out = subprocess.run(
        ["nm", str(obj)], check=True, capture_output=True, text=True
    ).stdout
    checked = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        letter, name = parts[1], parts[2]
        # Mach-O prefixes every C symbol with one extra underscore (``__Z…``).
        if name.startswith("__Z"):
            name = name[1:]
        if not name.startswith("_Z"):
            continue
        if letter.upper() not in "TDB":
            continue
        assert itanium_has_internal_linkage(name) is letter.islower(), line
        checked += 1
    assert checked >= 8, out


# ── Extractor: the clang AST marks TU-local targets ─────────────────────────


def _ast_tree(file: str) -> dict:
    """A hand-built clang JSON AST: ``ui::{anon}::{Node, head}``, a static and
    an external variable, and a public function referencing all three."""

    def var(name: str, mangled: str, qual: str, **extra: object) -> dict:
        return {
            "kind": "VarDecl",
            "id": f"0x{name}",
            "name": name,
            "mangledName": mangled,
            "type": {"qualType": qual},
            **extra,
        }

    def ref(node: dict) -> dict:
        return {
            "kind": "DeclRefExpr",
            "referencedDecl": {k: node[k] for k in ("id", "kind", "name")},
        }

    head = var("head", "_ZN2ui12_GLOBAL__N_14headE", "Node *")
    sv = var("sv", "_ZN2uiL2svE", "int", storageClass="static")
    ev = var("ev", "_ZN2ui2evE", "int")
    anon = {
        "kind": "NamespaceDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "Node",
                "tagUsed": "struct",
                "completeDefinition": True,
                "inner": [
                    {"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}
                ],
            },
            head,
        ],
    }
    fn = {
        "kind": "FunctionDecl",
        "name": "pub",
        "mangledName": "_ZN2ui3pubEv",
        "type": {"qualType": "int ()"},
        "inner": [{"kind": "CompoundStmt", "inner": [ref(head), ref(sv), ref(ev)]}],
    }
    ns = {
        "kind": "NamespaceDecl",
        "name": "ui",
        "loc": {"file": file},
        "inner": [anon, sv, ev, fn],
    }
    return {"kind": "TranslationUnitDecl", "inner": [ns]}


def _tu_local_dsts(edges: list[TypeEdge]) -> dict[str, bool]:
    return {e.dst: e.dst_tu_local for e in edges}


def test_ast_marks_anonymous_namespace_and_static_targets_in_a_source() -> None:
    dsts = _tu_local_dsts(parse_clang_ast_types(_ast_tree("/p/src/impl.cpp")))
    assert dsts == {
        "ui::Node": True,
        "_ZN2ui12_GLOBAL__N_14headE": True,
        "_ZN2uiL2svE": True,
        "_ZN2ui2evE": False,
    }


def test_ast_never_marks_internal_linkage_declared_in_a_header() -> None:
    dsts = _tu_local_dsts(parse_clang_ast_types(_ast_tree("/p/include/detail.h")))
    assert dsts, "the tree produced no edges, so nothing was tested"
    assert not any(dsts.values()), dsts


def test_merge_keeps_tu_local_only_when_every_unit_agrees() -> None:
    local = TypeEdge("a", "b", "DECL_HAS_TYPE", dst_file="x.cpp", dst_tu_local=True)
    external = TypeEdge("a", "b", "DECL_HAS_TYPE", dst_file="x.cpp")
    assert _merge_type_edges(local, local).dst_tu_local
    assert not _merge_type_edges(local, external).dst_tu_local
    assert not _merge_type_edges(external, local).dst_tu_local


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_live_clang_ast_marks_tu_local_targets(tmp_path: Path) -> None:
    src = tmp_path / "impl.cpp"
    src.write_text(
        "namespace ui {\n"
        "struct Widget { int id; };\n"
        "namespace { struct Node { Widget w; Node *next; }; Node *head = nullptr; }\n"
        "static int counter = 0;\n"
        "int shared = 0;\n"
        "Widget *make(int id) { ++counter; ++shared;\n"
        "  head = new Node{Widget{id}, head}; return &head->w; }\n"
        "}\n"
    )
    out = subprocess.run(
        [
            "clang++",
            "-std=c++17",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    edges = parse_clang_ast_types(json.loads(out))
    flags = {e.dst: e.dst_tu_local for e in edges if e.dst_file == str(src)}
    assert flags, edges
    tu_local = {d for d, f in flags.items() if f}
    external = {d for d, f in flags.items() if not f}
    assert any("Node" in d for d in tu_local), flags
    assert any("head" in d for d in tu_local), flags
    assert any("counter" in d for d in tu_local), flags
    assert any("shared" in d for d in external), flags
    assert any("Widget" in d for d in external), flags


# ── Detector: graph-level siblings ──────────────────────────────────────────


def _graph(extra_nodes: list[GraphNode], new_edges: list[GraphEdge]):
    nodes = [
        GraphNode(id="decl://pub", kind="source_decl", label="pub()"),
        GraphNode(id="sym://pub", kind="binary_symbol", label="pub"),
        GraphNode(id="hdr", kind="header", label="api.h"),
        *extra_nodes,
    ]
    base = [
        GraphEdge(src="decl://pub", dst="sym://pub", kind="SOURCE_DECL_MAPS_TO_SYMBOL"),
        GraphEdge(src="hdr", dst="decl://pub", kind="SOURCE_DECLARES"),
        GraphEdge(src="decl://pub", dst="decl://pub", kind="DECL_CALLS_DECL"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(nodes=nodes, edges=base + new_edges)
    old, new = producers_ran_unless_flagged(old, new)
    return [
        c for c in diff_source_graph_findings(old, new) if c.kind.value == _DEP_KIND
    ]


def _call(dst: str) -> GraphEdge:
    return GraphEdge(src="decl://pub", dst=dst, kind="DECL_CALLS_DECL")


_SRC = "/p/src/impl.cpp"
_HDR = "/p/src/detail.h"


@pytest.mark.parametrize(
    ("node", "reported"),
    [
        # TU-local by extractor marker, defined in a source: own implementation.
        (
            GraphNode(
                id="decl://helper",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _SRC, "tu_local": True},
            ),
            False,
        ),
        # TU-local by mangled identity alone (another producer, no marker).
        (
            GraphNode(
                id="decl://_ZN2ui12_GLOBAL__N_14headE",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _SRC},
            ),
            False,
        ),
        (
            GraphNode(
                id="decl://_ZL6helperv",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _SRC},
            ),
            False,
        ),
        # Internal linkage but in a header: compiled into every includer.
        (
            GraphNode(
                id="decl://_ZL6helperv",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _HDR},
            ),
            True,
        ),
        (
            GraphNode(
                id="decl://helper",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _HDR, "tu_local": True},
            ),
            True,
        ),
        # External linkage in a source file or private header: still internal.
        (
            GraphNode(
                id="decl://_ZN6detail6helperEv",
                kind="source_decl",
                attrs={"defined_in_project": True, "def_file": _SRC},
            ),
            True,
        ),
        (
            GraphNode(
                id="decl://_ZN6detail6helperEv",
                kind="source_decl",
                attrs={"visibility": "private_header"},
            ),
            True,
        ),
    ],
)
def test_internal_dependency_target_requires_external_linkage(
    node: GraphNode, reported: bool
) -> None:
    findings = _graph([node], [_call(node.id)])
    assert bool(findings) is reported, [c.description for c in findings]


def test_dependency_through_a_tu_local_entity_is_still_reported() -> None:
    """A TU-local wrapper is skipped as a target, not as a path: an external
    internal helper reached through it is still a new dependency."""
    wrapper = GraphNode(
        id="decl://_ZL4wrapv",
        kind="source_decl",
        attrs={"defined_in_project": True, "def_file": _SRC},
    )
    helper = GraphNode(
        id="decl://_ZN6detail6helperEv",
        kind="source_decl",
        attrs={"visibility": "private_header"},
    )
    findings = _graph(
        [wrapper, helper],
        [
            _call(wrapper.id),
            GraphEdge(src=wrapper.id, dst=helper.id, kind="DECL_CALLS_DECL"),
        ],
    )
    assert len(findings) == 1
    assert "helper" in findings[0].description
    assert "_ZL4wrapv" not in findings[0].new_value


def test_projection_cache_codec_keeps_the_tu_local_flag() -> None:
    """The header-projection cache encodes edges positionally; a dropped
    trailing ``dst_tu_local`` would decode as False for every edge."""
    from abicheck.buildsource.header_graph_ast_projection import (
        project_header_graph_ast,
    )
    from abicheck.buildsource.header_graph_projection_cache import (
        decode_projection,
        encode_projection,
    )

    projection = project_header_graph_ast(_ast_tree("/p/src/impl.cpp"))
    flagged = dataclasses.replace(
        projection,
        type_edges=[
            dataclasses.replace(e, dst_tu_local=True) for e in projection.type_edges
        ],
    )
    assert flagged.type_edges, "the tree projected no type edges"
    decoded = decode_projection(encode_projection(flagged))
    assert decoded is not None
    assert [e.dst_tu_local for e in decoded.type_edges] == [True] * len(
        flagged.type_edges
    )


def test_a_type_name_shared_with_an_external_declaration_is_not_tu_local() -> None:
    """``ns::W`` and ``ns::{anon}::W`` spell the same qualified name; an edge to
    it must not be hidden as TU-local, whichever declaration comes first."""
    external = {"kind": "CXXRecordDecl", "name": "W", "completeDefinition": True}
    anon = {"kind": "NamespaceDecl", "inner": [dict(external)]}
    fn = {
        "kind": "FunctionDecl",
        "name": "pub",
        "mangledName": "_ZN2ns3pubENS_1WE",
        "type": {"qualType": "void (W)"},
        "inner": [{"kind": "ParmVarDecl", "name": "w", "type": {"qualType": "W"}}],
    }
    for order in ([anon, external, fn], [external, anon, fn]):
        tree = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "NamespaceDecl",
                    "name": "ns",
                    "loc": {"file": "/p/src/impl.cpp"},
                    "inner": [dict(n) for n in order],
                }
            ],
        }
        dsts = _tu_local_dsts(parse_clang_ast_types(tree))
        assert dsts.get("ns::W") is False, dsts


def test_a_function_local_type_does_not_unmark_a_same_named_anonymous_type() -> None:
    """A type declared in a function body is indexed without the function's
    name, so it can spell like ``ns::{anon}::Node``; it is not an externally
    visible declaration and must not cancel the anonymous one's TU-local mark."""
    anon = {
        "kind": "NamespaceDecl",
        "inner": [
            {"kind": "CXXRecordDecl", "name": "Node", "completeDefinition": True}
        ],
    }
    helper = {
        "kind": "FunctionDecl",
        "name": "helper",
        "mangledName": "_ZN2ns6helperEv",
        "type": {"qualType": "void ()"},
        "inner": [
            {
                "kind": "CompoundStmt",
                "inner": [
                    {
                        "kind": "DeclStmt",
                        "inner": [
                            {
                                "kind": "CXXRecordDecl",
                                "name": "Node",
                                "completeDefinition": True,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    pub = {
        "kind": "FunctionDecl",
        "name": "pub",
        "mangledName": "_ZN2ns3pubEPNS_12_GLOBAL__N_14NodeE",
        "type": {"qualType": "void (Node *)"},
        "inner": [{"kind": "ParmVarDecl", "name": "n", "type": {"qualType": "Node *"}}],
    }
    for order in ([anon, helper, pub], [helper, anon, pub]):
        tree = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "NamespaceDecl",
                    "name": "ns",
                    "loc": {"file": "/p/src/impl.cpp"},
                    "inner": [dict(n) for n in order],
                }
            ],
        }
        dsts = _tu_local_dsts(parse_clang_ast_types(tree))
        assert dsts.get("ns::Node") is True, dsts
