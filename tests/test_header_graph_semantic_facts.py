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

"""Headers-only (default L2) graphs carry the template, override and callback
facts the same AST holds, identical to what the L5 source pass derives.

Known gap "The default L2 header projection drops template/override/callback
facts": the header projection used to keep only type/call edges, so a plain
``abicheck dump lib.so -H api.h`` had none of these families although the
clang AST it parsed contains them -- a false-clean for any comparison that
reads them.

Two independent oracles, per fixture:

* a hand-written list of edges each fixture is built to contain (the C++
  semantics, not the implementation, says they must exist), and
* the L5 pass (``fold_semantic_graphs``) over a translation unit that only
  includes the same header: a different producer path (per-TU replay of a
  compile unit) reading the same declarations. The family edge sets must be
  equal, not merely overlapping.

Each fixture is dumped through the public ``abicheck dump`` CLI, on both the
whole-tree and the streamed projection path (``ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB``
drives the latter over small documents), so the parity also covers the
streamed reader.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Itanium fixture: the expected edges name Itanium linker names",
    ),
]

#: The edge kinds of the three families (plus the virtual-dispatch join that
#: reads the override facts). INSTANTIATION_EMITS_SYMBOL is excluded from the
#: parity check only because it joins onto ``binary_symbol`` nodes, which the
#: header graph seeds from the binary and an L5-only graph does not have.
FAMILY_EDGE_KINDS = frozenset(
    {
        "DECL_INSTANTIATES_TEMPLATE",
        "TEMPLATE_USES_TYPE",
        "TEMPLATE_USES_DECL",
        "METHOD_POSSIBLE_OVERRIDE",
        "VIRTUAL_CALL_MAY_DISPATCH_TO",
        "TYPE_HAS_VTABLE",
        "DECL_REGISTERS_CALLBACK",
        "DECL_TAKES_ADDRESS_OF",
        "CALLBACK_MAY_INVOKE",
    }
)

TEMPLATES = (
    """\
#pragma once
namespace detail { struct Impl { int v; }; }
template <typename T> struct Box { T value; T get() const { return value; } };
template <typename T> T twice(T x) { return x + x; }
struct Api {
  Box<detail::Impl> held;
  int run() const;
};
inline int use_twice(int x) { return twice(x); }
""",
    "int Api::run() const { return held.get().v; }\n",
)
OVERRIDES = (
    """\
#pragma once
struct Base { virtual ~Base(); virtual int f(int) const; virtual void g(); };
struct Derived : Base { int f(int) const override; void g() override; };
inline int call(const Base& b) { return b.f(1); }
""",
    """\
Base::~Base() {}
int Base::f(int x) const { return x; }
void Base::g() {}
int Derived::f(int x) const { return x + 1; }
void Derived::g() {}
""",
)
CALLBACKS = (
    """\
#pragma once
namespace ev {
typedef void (*handler_t)(int);
void register_handler(handler_t h);
void on_event(int);
struct Slot { handler_t cb; };
inline void setup() { register_handler(&on_event); }
inline void fire(Slot& s, int x) { s.cb(x); }
inline void bind(Slot& s) { s.cb = on_event; }
}
""",
    """\
namespace ev {
static handler_t g_h;
void register_handler(handler_t h) { g_h = h; }
void on_event(int) {}
}
""",
)
STD_FUNCTION = (
    """\
#pragma once
#include <functional>
namespace tick {
void on_tick(int);
void subscribe(std::function<void(int)> f);
inline void wire() { subscribe(on_tick); }
}
""",
    """\
namespace tick {
void on_tick(int) {}
void subscribe(std::function<void(int)> f) { f(1); }
}
""",
)

FIXTURES = {
    "templates": TEMPLATES,
    "overrides": OVERRIDES,
    "callbacks": CALLBACKS,
    "std_function": STD_FUNCTION,
}

#: fixture -> edges (kind, src substring, dst substring) the C++ requires.
EXPECTED: dict[str, list[tuple[str, str, str]]] = {
    "templates": [
        ("DECL_INSTANTIATES_TEMPLATE", "Box<detail::Impl>", "template_decl://Box"),
        ("TEMPLATE_USES_TYPE", "Box<detail::Impl>", "type://detail::Impl"),
        ("DECL_INSTANTIATES_TEMPLATE", "_Z5twiceIiET_S0_", "template_decl://twice"),
    ],
    "overrides": [
        ("METHOD_POSSIBLE_OVERRIDE", "_ZNK7Derived1fEi", "_ZNK4Base1fEi"),
        ("METHOD_POSSIBLE_OVERRIDE", "_ZN7Derived1gEv", "_ZN4Base1gEv"),
        ("VIRTUAL_CALL_MAY_DISPATCH_TO", "_Z4callRK4Base", "_ZNK7Derived1fEi"),
    ],
    "callbacks": [
        ("DECL_REGISTERS_CALLBACK", "_ZN2ev8on_eventEi", "decl://h"),
        ("DECL_TAKES_ADDRESS_OF", "_ZN2ev8on_eventEi", "decl://cb"),
    ],
    "std_function": [
        (
            "DECL_INSTANTIATES_TEMPLATE",
            "function<void (int)>",
            "template_decl://std::function",
        ),
        ("DECL_TAKES_ADDRESS_OF", "_Function_handler", "_M_invoker"),
    ],
}


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _family_edges(graph) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (e.kind, e.src, e.dst) for e in graph.edges if e.kind in FAMILY_EDGE_KINDS
    )


def _build(d: Path, fixture: str) -> tuple[Path, Path]:
    header_src, source = FIXTURES[fixture]
    header = d / "api.h"
    header.write_text(header_src)
    (d / "api.cpp").write_text('#include "api.h"\n' + source)
    lib = d / "libapi.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(lib), str(d / "api.cpp")],
        check=True,
        cwd=d,
        capture_output=True,
    )
    return lib, header


def _dump_header_graph(d: Path, lib: Path, header: Path, *, streamed: bool):
    from abicheck.serialization import load_snapshot

    out = d / ("streamed.json" if streamed else "whole.json")
    env = {
        **os.environ,
        # A private cache: the projection sidecar must not answer for the
        # other path, and the two dumps must each really parse.
        "XDG_CACHE_HOME": str(d / ("cache-s" if streamed else "cache-w")),
        "ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB": "0" if streamed else "100000",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "dump",
            str(lib),
            "-H",
            str(header),
            "-o",
            str(out),
        ],
        cwd=d,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    snap = load_snapshot(out)
    assert snap.surface_graph is not None, "the dump attached no header graph"
    return snap.surface_graph


def _l5_graph(d: Path):
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.l5_ast_pass import fold_semantic_graphs
    from abicheck.extract.headers.sysinc import _resolve_clang_system_includes
    from abicheck.model.source_graph import SourceGraphSummary

    # The same system include directories and GCC-compat define the header
    # parse injects, so the TU sees the same standard library: a different
    # libstdc++ is a different AST, not a parity failure.
    system_dirs = _resolve_clang_system_includes(
        "c++", gcc_path=None, gcc_prefix=None, sysroot=None, nostdinc=False,
        force_cpp=True,
    )  # fmt: skip

    tu = d / "tu.cpp"
    tu.write_text('#include "api.h"\n')
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(
                id=f"cu://{tu}",
                source=str(tu),
                directory=str(d),
                language="CXX",
                # No -std: the header parse passes none either (clang's own
                # default dialect), and a different dialect is a different
                # AST -- <functional> instantiates other library templates.
                include_paths=[str(d)],
                system_include_paths=list(system_dirs),
                defines={"__malloc__(...)": "__malloc__"},
            )
        ]
    )
    graph = SourceGraphSummary()
    fold_semantic_graphs(graph, merged, "clang++", [])
    return graph


@pytest.fixture(scope="module", params=sorted(FIXTURES))
def built(request, tmp_path_factory):
    _require("g++", "clang++", "castxml")
    fixture = request.param
    d = tmp_path_factory.mktemp(fixture)
    lib, header = _build(d, fixture)
    return fixture, d, lib, header


def test_headers_only_graph_has_the_expected_family_edges(built) -> None:
    fixture, d, lib, header = built
    graph = _dump_header_graph(d, lib, header, streamed=False)
    edges = _family_edges(graph)
    for kind, src, dst in EXPECTED[fixture]:
        assert any(k == kind and src in s and dst in t for k, s, t in edges), (
            fixture,
            kind,
            src,
            dst,
            sorted(edges)[:40],
        )


@pytest.mark.parametrize("streamed", [False, True], ids=["whole", "streamed"])
def test_headers_only_family_edges_equal_the_l5_pass(built, streamed) -> None:
    fixture, d, lib, header = built
    header_edges = _family_edges(_dump_header_graph(d, lib, header, streamed=streamed))
    l5_edges = _family_edges(_l5_graph(d))
    assert l5_edges, f"{fixture}: the L5 oracle produced no family edges"
    assert header_edges == l5_edges, (
        fixture,
        sorted(header_edges - l5_edges)[:20],
        sorted(l5_edges - header_edges)[:20],
    )


#: fixture -> (v2 header, v2 source, edges that must leave, edges that must
#: arrive). Each pair is one real edit a consumer can observe: a template
#: argument, an override, a registered callback.
CHANGES: dict[
    str, tuple[str, str, list[tuple[str, str, str]], list[tuple[str, str, str]]]
] = {
    "templates": (
        TEMPLATES[0]
        .replace("Box<detail::Impl> held;", "Box<detail::Other> held;")
        .replace(
            "struct Impl { int v; };",
            "struct Impl { int v; }; struct Other { int v; };",
        ),
        TEMPLATES[1],
        [("TEMPLATE_USES_TYPE", "Box<detail::Impl>", "type://detail::Impl")],
        [("TEMPLATE_USES_TYPE", "Box<detail::Other>", "type://detail::Other")],
    ),
    "overrides": (
        OVERRIDES[0].replace(" void g() override;", ""),
        OVERRIDES[1].replace("void Derived::g() {}\n", ""),
        [("METHOD_POSSIBLE_OVERRIDE", "_ZN7Derived1gEv", "_ZN4Base1gEv")],
        [],
    ),
    "callbacks": (
        CALLBACKS[0]
        .replace("void on_event(int);", "void on_event(int);\nvoid on_other(int);")
        .replace("register_handler(&on_event)", "register_handler(&on_other)"),
        CALLBACKS[1].replace(
            "void on_event(int) {}", "void on_event(int) {}\nvoid on_other(int) {}"
        ),
        [("DECL_REGISTERS_CALLBACK", "_ZN2ev8on_eventEi", "decl://h")],
        [("DECL_REGISTERS_CALLBACK", "_ZN2ev8on_otherEi", "decl://h")],
    ),
}


def _has(edges, kind, src, dst) -> bool:
    return any(e.kind == kind and src in e.src and dst in e.dst for e in edges)


def _versions(tmp_path_factory, fixture):
    _require("g++", "clang++", "castxml")
    old_dir = tmp_path_factory.mktemp(f"{fixture}-v1")
    new_dir = tmp_path_factory.mktemp(f"{fixture}-v2")
    lib1, h1 = _build(old_dir, fixture)
    header2, source2 = CHANGES[fixture][:2]
    FIXTURES[f"{fixture}-v2"] = (header2, source2)
    try:
        lib2, h2 = _build(new_dir, f"{fixture}-v2")
    finally:
        del FIXTURES[f"{fixture}-v2"]
    old = _dump_header_graph(old_dir, lib1, h1, streamed=False)
    new = _dump_header_graph(new_dir, lib2, h2, streamed=False)
    return old, new


@pytest.mark.parametrize("fixture", sorted(CHANGES))
def test_a_change_in_the_family_facts_surfaces_in_the_graph_diff(
    tmp_path_factory, fixture
) -> None:
    from abicheck.buildsource.source_graph_compare import diff_source_graph

    old, new = _versions(tmp_path_factory, fixture)
    diff = diff_source_graph(old, new)
    _, _, gone, arrived = CHANGES[fixture]
    for kind, src, dst in gone:
        assert _has(diff.removed_edges, kind, src, dst), (fixture, kind, src, dst)
    for kind, src, dst in arrived:
        assert _has(diff.added_edges, kind, src, dst), (fixture, kind, src, dst)
    assert diff.coverage_gaps == []


@pytest.mark.parametrize("fixture", sorted(CHANGES))
def test_a_graph_stored_before_the_header_pass_projected_them_is_a_gap(
    tmp_path_factory, fixture
) -> None:
    """The old side is the real v1 graph with what a pre-fix header pass
    never produced taken out: the family flags and every edge the family
    folds. Its silence must read as a coverage gap, not as removals."""
    from abicheck.buildsource.source_graph_compare import diff_source_graph
    from abicheck.model.source_graph import SourceGraphSummary
    from abicheck.model.source_graph_coverage import SEMANTIC_FAMILIES

    old, new = _versions(tmp_path_factory, fixture)
    family_kinds = frozenset().union(*(f.edge_kinds for f in SEMANTIC_FAMILIES))
    family_node_kinds = frozenset().union(*(f.node_kinds for f in SEMANTIC_FAMILIES))
    stored = SourceGraphSummary.from_dict(old.to_dict())
    for f in SEMANTIC_FAMILIES:
        stored.extractor_passes.pop(f.header_pass, None)
    stored = SourceGraphSummary.from_dict(
        {
            **stored.to_dict(),
            "edges": [e.to_dict() for e in stored.edges if e.kind not in family_kinds],
            "nodes": [
                n.to_dict() for n in stored.nodes if n.kind not in family_node_kinds
            ],
        }
    )
    for older, newer in ((stored, new), (new, stored)):
        diff = diff_source_graph(older, newer)
        assert not any(
            e.kind in family_kinds for e in diff.added_edges + diff.removed_edges
        ), fixture
        assert {f.name for f in SEMANTIC_FAMILIES} <= set(diff.coverage_gaps)
