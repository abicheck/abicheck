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

"""The header graph parses headers in the language the primary pass chose.

Known gap "The header-graph parse picks C mode for C++ headers without a
recognised C++ construct": with no ``--lang``, the primary pass decides C or
C++ from header text *and* the binary's mangled exports
(``extract.headers.toolchain._resolve_force_cpp``), but the header graph's own
clang parse -- often started before the primary pass finishes -- decided from
header text alone. A C++ ``.h`` header whose only C++ markers are not in
``ast_config._CPP_ONLY_PATTERNS`` parsed as C, failed, and the graph fell back
to declarations only (``header_call_graph`` degraded).

Oracle: the compiler. Each C++ fixture is built with ``g++`` (so it is C++,
and its exports are Itanium-mangled) and contains an inline caller whose call
edge the graph must hold; each C fixture is built with ``gcc`` as plain C,
and must stay a C parse even where its text would break a C++ parse.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Itanium fixture",
    ),
]

#: name -> (header, source). Each header's only C++-ness is a construct
#: `_CPP_ONLY_PATTERNS` does not match, and each has an inline caller.
CXX_HEADERS: dict[str, tuple[str, str]] = {
    "non_const_reference": (
        "struct Slot { int v; };\nint get(Slot& s);\n"
        "inline int twice(Slot& s) { return get(s) * 2; }\n",
        "int get(Slot& s) { return s.v; }\n",
    ),
    "std_function": (
        "#include <functional>\nvoid subscribe(std::function<void(int)> f);\n"
        "void on_tick(int);\ninline void wire() { subscribe(on_tick); }\n",
        "void subscribe(std::function<void(int)> f) { f(1); }\nvoid on_tick(int) {}\n",
    ),
    "overload": (
        "int pick(int x);\nint pick(double x);\n"
        "inline int both(int x) { return pick(x) + pick(1.5); }\n",
        "int pick(int x) { return x; }\nint pick(double) { return 2; }\n",
    ),
    "default_argument": (
        "int scale(int x, int factor = 2);\n"
        "inline int doubled(int x) { return scale(x); }\n",
        "int scale(int x, int factor) { return x * factor; }\n",
    ),
}

#: Valid C that a C++ parse rejects (`new` is a C++ keyword), built as C.
C_HEADERS: dict[str, tuple[str, str]] = {
    "keyword_as_field": (
        "struct options { int new; };\nint apply(struct options *o);\n"
        "static inline int apply_twice(struct options *o) { return apply(o) * 2; }\n",
        "int apply(struct options *o) { return o->new; }\n",
    ),
    "plain_c": (
        "int add(int a, int b);\nstatic inline int inc(int a) { return add(a, 1); }\n",
        "int add(int a, int b) { return a + b; }\n",
    ),
}


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _dump(
    d: Path, header_src: str, source: str, *, cxx: bool, frontend: str = "castxml"
) -> object:
    from abicheck.serialization import load_snapshot

    header = d / "api.h"
    header.write_text(header_src)
    src = d / ("api.cpp" if cxx else "api.c")
    src.write_text('#include "api.h"\n' + source)
    lib = d / "libapi.so"
    compiler = ["g++", "-std=c++17"] if cxx else ["gcc"]
    subprocess.run(
        [*compiler, "-shared", "-fPIC", "-o", str(lib), str(src)],
        check=True,
        cwd=d,
        capture_output=True,
    )
    out = d / "snap.json"
    proc = subprocess.run(
        abicheck_command(
            "dump",
            str(lib),
            "-H",
            str(header),
            "-o",
            str(out),
        ),
        cwd=d,
        env={
            **os.environ,
            "XDG_CACHE_HOME": str(d / "cache"),
            "ABICHECK_AST_FRONTEND": frontend,
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return load_snapshot(out)


#: castxml (the default) prefetches the graph's parse beside the primary
#: pass; the clang frontend hands its own AST to the graph instead; hybrid
#: merges one leg of each and attaches the graph once, to the merge.
FRONTENDS = ("castxml", "clang", "hybrid")


def _modes(snap: object) -> set[str]:
    """Every mode the stored snapshot records: the bare key, or a hybrid
    merge's one key per leg."""
    tc = snap.ast_toolchain  # type: ignore[attr-defined]
    keys = (
        "resolved_lang_mode",
        "castxml_resolved_lang_mode",
        "clang_resolved_lang_mode",
    )
    return {tc[k] for k in keys if k in tc}


@pytest.mark.parametrize("frontend", FRONTENDS)
@pytest.mark.parametrize("name", sorted(CXX_HEADERS))
def test_a_cxx_header_without_a_recognised_marker_gets_a_full_graph(
    tmp_path: Path, name: str, frontend: str
) -> None:
    _require("g++", "clang++", "castxml")
    snap = _dump(tmp_path, *CXX_HEADERS[name], cxx=True, frontend=frontend)
    assert _modes(snap) == {"c++"}, name
    graph = snap.surface_graph
    assert graph is not None
    assert "header_call_graph" not in graph.degraded_passes, (
        name,
        graph.degraded_passes,
    )
    assert graph.extractor_passes.get("header_call_graph") is True
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges), name
    # One clang parse of these headers, in one mode: the graph's parse reaches
    # the cache key the primary pass's decision produced (and, under the
    # clang frontend, its in-process AST), rather than a second, separately
    # keyed C++ parse.
    entries = sorted((tmp_path / "cache" / "abi_check" / "clang").glob("*.json"))
    entries = [e for e in entries if not e.name.endswith(".projection.json")]
    assert len(entries) == 1, (name, frontend, [e.name for e in entries])


@pytest.mark.parametrize("frontend", FRONTENDS)
@pytest.mark.parametrize("name", sorted(C_HEADERS))
def test_a_c_header_stays_a_c_parse(tmp_path: Path, name: str, frontend: str) -> None:
    _require("gcc", "clang", "castxml")
    snap = _dump(tmp_path, *C_HEADERS[name], cxx=False, frontend=frontend)
    assert _modes(snap) == {"c"}, name
    graph = snap.surface_graph
    assert graph is not None
    assert "header_call_graph" not in graph.degraded_passes, (
        name,
        graph.degraded_passes,
    )
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges), name
