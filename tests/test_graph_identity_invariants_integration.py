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

"""I1 on real toolchain output: one header, dumped through the castxml, clang
and hybrid frontends, attaches one header graph per dump; the public-surface
builder then writes into that same graph. Every declaration must be one node,
and the same node id under all three frontends.

The oracle is the fixed entity list below -- each entity names a substring
that appears in exactly the one id that stands for it (a linker name, which
is also how the extern "C" and the overloaded entities are told apart).
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
    # The fixture is an ELF shared object and the oracle names Itanium
    # linker symbols (`_ZN2ns1fEi`); a PE/Mach-O build spells them otherwise.
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Itanium fixture: linker-name oracle is Linux-specific",
    ),
]

HEADER = """\
extern "C" int c_fn(int);
extern "C" inline int c_inline(int x) { return c_fn(x) + 1; }
namespace ns {
struct W { int m() const; };
int f(int);
int f(double);
inline int g(int x) { return f(x) + W().m(); }
}
"""
SOURCE = """\
#include "a.hpp"
int c_fn(int x) { return x; }
namespace ns { int W::m() const { return 0; } int f(int) { return 0; } int f(double) { return 1; } }
"""
#: entity -> the substring its (single) node id must contain.
ENTITIES = {
    "c_fn": "decl://c_fn",
    "c_inline": "decl://c_inline",
    "ns::f(int)": "_ZN2ns1fEi",
    "ns::f(double)": "_ZN2ns1fEd",
    "ns::W::m": "_ZNK2ns1W1mEv",
    "ns::g": "_ZN2ns1gEi",
}
FRONTENDS = ("castxml", "clang", "hybrid")


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not available")


@pytest.fixture(scope="module")
def dumps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    for tool in ("g++", "castxml", "clang"):
        _require(tool)
    d = tmp_path_factory.mktemp("i1")
    (d / "a.hpp").write_text(HEADER)
    (d / "a.cpp").write_text(SOURCE)
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(d / "liba.so"), str(d / "a.cpp")],
        check=True,
        cwd=d,
    )
    out: dict[str, Path] = {}
    for fe in FRONTENDS:
        target = d / f"{fe}.json"
        proc = subprocess.run(
            [
                "abicheck",
                "dump",
                str(d / "liba.so"),
                "-H",
                str(d / "a.hpp"),
                "-o",
                str(target),
            ],
            cwd=d,
            env={**os.environ, "ABICHECK_AST_FRONTEND": fe},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        out[fe] = target
    return out


def _decl_node_ids(path: Path) -> set[str]:
    from abicheck.compare.surface_graph import build_public_surface_facts
    from abicheck.serialization import load_snapshot

    snap = load_snapshot(path)
    graph = snap.surface_graph
    assert graph is not None, "the dump attached no header graph"
    build_public_surface_facts(snap, graph)
    return {n.id for n in graph.nodes if n.kind in {"source_decl", "declaration"}}


def test_one_node_per_declaration_and_one_id_across_frontends(
    dumps: dict[str, Path],
) -> None:
    ids_by_frontend: dict[str, dict[str, str]] = {}
    for fe, path in dumps.items():
        node_ids = _decl_node_ids(path)
        per_entity: dict[str, str] = {}
        for entity, needle in ENTITIES.items():
            matching = sorted(i for i in node_ids if needle in i)
            assert len(matching) == 1, (fe, entity, matching)
            per_entity[entity] = matching[0]
        # A declaration seen only through its bare name or the AST's
        # qualified#signature spelling is a second node for one entity.
        assert not any("#sha256:" in i and "c_" in i for i in node_ids), (fe, node_ids)
        ids_by_frontend[fe] = per_entity
    assert (
        ids_by_frontend["castxml"]
        == ids_by_frontend["clang"]
        == ids_by_frontend["hybrid"]
    )
