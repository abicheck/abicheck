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

"""Known gap "A refused clang AST still leaves the header type pass
collected", end to end through ``abicheck dump`` with a real clang failure.

The unit test (``test_header_graph_ast_failure_coverage.py``) stubs the AST
acquisition. Here the header graph's own clang parse really fails: a header
that castxml accepts (castxml predefines ``__castxml__``) but a plain clang
parse rejects with ``#error``. The oracle is the input, not the code: when
any header's AST was refused, the AST-only type edges were not read for it,
so the type pass must not read as complete; with every header parsed, it
must. Three sibling shapes: the only header refused, one of two refused
(the bisection recovers a partial projection), and a control with none.
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
        not sys.platform.startswith("linux"), reason="ELF fixture built with g++"
    ),
]

_REFUSED = (
    "#pragma once\n"
    "#ifndef __castxml__\n"
    '#error "the header graph clang parse refuses this header"\n'
    "#endif\n"
    "struct Rec { int x; };\n"
    "int refused_f(Rec r, Rec *p);\n"
)
_PLAIN = "#pragma once\nstruct Other { long y; };\nint plain_g(Other o);\n"


def _dump(tmp_path: Path, headers: dict[str, str]):
    from abicheck.serialization import load_snapshot

    for tool in ("g++", "clang++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    src = tmp_path / "api.cpp"
    body = ""
    for name, text in headers.items():
        (tmp_path / name).write_text(text)
        body += f'#define __castxml__ 1\n#include "{name}"\n'
    body += "int refused_f(Rec, Rec*) { return 0; }\n" if "refused.h" in headers else ""
    body += "int plain_g(Other) { return 1; }\n" if "plain.h" in headers else ""
    src.write_text(body)
    lib = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(lib), str(src)],
        check=True,
        capture_output=True,
        cwd=tmp_path,
    )
    out = tmp_path / "snap.json"
    argv = abicheck_command("dump", str(lib), "-o", str(out))
    for name in headers:
        argv += ["-H", str(tmp_path / name)]
    proc = subprocess.run(
        argv,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    graph = load_snapshot(out).surface_graph
    assert graph is not None, "the dump attached no header graph"
    return graph


@pytest.mark.parametrize(
    ("headers", "refused"),
    [
        ({"refused.h": _REFUSED}, True),
        ({"plain.h": _PLAIN, "refused.h": _REFUSED}, True),
        ({"plain.h": _PLAIN}, False),
    ],
    ids=["only-header-refused", "one-of-two-refused", "control-none-refused"],
)
def test_type_pass_complete_only_when_every_header_ast_was_read(
    tmp_path: Path, headers: dict[str, str], refused: bool
) -> None:
    from abicheck.buildsource.header_graph_ast_projection import (
        HEADER_TYPE_GRAPH_PASS,
    )
    from abicheck.model.source_graph_coverage import HEADER_CALL_GRAPH_PASS

    graph = _dump(tmp_path, headers)
    type_degraded = bool(graph.degraded_passes.get(HEADER_TYPE_GRAPH_PASS))
    call_degraded = bool(graph.degraded_passes.get(HEADER_CALL_GRAPH_PASS))
    # Both passes read the same AST, so they degrade together.
    assert type_degraded is refused
    assert call_degraded is refused
    assert graph.coverage["type_edges"]["collected"] is (not refused)
    assert (HEADER_TYPE_GRAPH_PASS in graph.extractor_passes) is (not refused)
    # The declarations themselves are still in the graph either way: the
    # failure narrows the type-edge claim, it does not empty the surface.
    assert any("refused_f" in n.id or "plain_g" in n.id for n in graph.nodes)
