"""The typed route's header graph sees the dump's compile options.

Bug class: the typed dump's header graph was parsed with no compile context,
while the snapshot's own header parse used the dump's -D/-I/sysroot/nostdinc.
For a header whose declarations depend on a macro, the graph then described a
different preprocessor configuration than the snapshot it was attached to, and
a declaration gated by the macro was missing from the graph.

Oracle, from the preprocessor rule and not from the implementation: a
declaration under ``#ifdef FEATURE`` exists in the header's configuration only
when ``-DFEATURE`` is given. The graph must contain it exactly then.

The test needs castxml and a C++ compiler, and it does not skip, so it belongs
to the ``integration`` lane.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.checkers import abicheck as checker

pytestmark = pytest.mark.integration

GATED = "decl://_Z9krx_gatedi"


def _graph_ids(snap) -> set[str]:
    graph = snap.surface_graph
    assert graph is not None, "the typed dump must attach the header graph"
    nodes = graph.nodes if isinstance(graph.nodes, list) else list(graph.nodes.values())
    return {str(n.id) for n in nodes}


def test_macro_gated_declaration_is_in_the_graph_only_with_its_define(
    tmp_path: Path,
) -> None:
    for tool in ("g++", "castxml"):
        assert shutil.which(tool), f"{tool} must be on PATH for this integration test"
    hdr = tmp_path / "pub.h"
    hdr.write_text(
        "#ifdef FEATURE\nint krx_gated(int a);\n#endif\nint krx_base(int a);\n",
        encoding="utf-8",
    )
    src = tmp_path / "lib.cpp"
    src.write_text(
        '#include "pub.h"\n#ifdef FEATURE\nint krx_gated(int a) { return a; }\n#endif\n'
        "int krx_base(int a) { return a; }\n",
        encoding="utf-8",
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

    assert GATED in _graph_ids(with_define), (
        "the define is in effect: the gated declaration must be in the graph"
    )
    assert GATED not in _graph_ids(without_define), (
        "no define: the gated declaration must not be in the graph"
    )
