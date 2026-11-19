"""The typed route's header graph sees the dump's compile options.

Bug class: the typed dump's header graph was parsed with no compile context,
while the snapshot's own header parse used the dump's -D/-I/sysroot/nostdinc.
For a header whose declarations depend on a macro, the graph then described a
different preprocessor configuration than the snapshot it was attached to.

Oracle, from the preprocessor rule and not from the implementation: inside
``#ifdef FEATURE`` the body of ``krx_user`` calls ``krx_gated``; without the
define that call is absent. Both functions are inline and live only in the
header, so the snapshot's exported symbols do not seed them. A call edge from
``krx_user`` to ``krx_gated`` can therefore come only from the graph's own AST
parse, and it must exist exactly when the define is in effect. The call-graph
pass must also have completed, so an absent edge is not a silent pass.

The test needs castxml and a C++ compiler. It skips only when a tool is absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.checkers import abicheck as checker

pytestmark = pytest.mark.integration

USER = "decl://_Z8krx_useri"
GATED = "decl://_Z9krx_gatedi"
CALL_KIND = "DECL_CALLS_DECL"

HEADER = """\
#ifdef FEATURE
inline int krx_gated(int a) { return a + 1; }
#endif
inline int krx_user(int a) {
#ifdef FEATURE
  return krx_gated(a);
#else
  return a;
#endif
}
int krx_base(int a);
"""


def _require_tools() -> None:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH; this integration test needs it")


def _call_edges(snap) -> set[tuple[str, str]]:
    graph = snap.surface_graph
    assert graph is not None, "the typed dump must attach the header graph"
    edges = graph.edges if isinstance(graph.edges, list) else list(graph.edges.values())
    return {(str(e.src), str(e.dst)) for e in edges if str(e.kind) == CALL_KIND}


def _call_graph_ran(snap) -> bool:
    return bool(snap.surface_graph.extractor_passes.get("header_call_graph"))


def test_macro_gated_call_edge_is_in_the_graph_only_with_its_define(
    tmp_path: Path,
) -> None:
    _require_tools()
    hdr = tmp_path / "pub.h"
    hdr.write_text(HEADER, encoding="utf-8")
    src = tmp_path / "lib.cpp"
    src.write_text(
        '#include "pub.h"\nint krx_base(int a) { return a; }\n', encoding="utf-8"
    )
    so = tmp_path / "libx.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-std=c++17",
            "-DFEATURE=1",
            "-I",
            str(tmp_path),
            str(src),
            "-o",
            str(so),
        ],
        check=True,
        cwd=tmp_path,
    )

    with_define = checker.snapshot(
        so,
        [hdr],
        version="1",
        is_cxx=True,
        backend="castxml",
        gcc_options="-DFEATURE=1",
    )
    without_define = checker.snapshot(
        so, [hdr], version="1", is_cxx=True, backend="castxml", gcc_options=None
    )

    assert _call_graph_ran(with_define), (
        "the call-graph pass must complete with the define"
    )
    assert (USER, GATED) in _call_edges(with_define), (
        "the define is in effect: the graph AST must record krx_user calling krx_gated"
    )
    assert (USER, GATED) not in _call_edges(without_define), (
        "no define: the graph AST must not record the gated call"
    )
