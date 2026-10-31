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

"""A snapshot's stored bytes do not depend on the process that wrote them.

Bug class ``serialization.stored_bytes_depend_on_producer_iteration_order``:
``storage.graph_table_codec.encode_graph_table`` used to emit graph rows in
in-memory insertion order, which follows producer iteration (set/dict order
that can vary with ``PYTHONHASHSEED``). The invariant is stated two ways:

* in-process, over generated graphs: every permutation of node/edge
  insertion encodes to identical JSON (oracle: the encoding of the same
  multiset inserted in plain sorted order);
* cross-process (``integration``): dumping one compiled library in separate
  interpreters with different ``PYTHONHASHSEED`` values and working
  directories, with ``SOURCE_DATE_EPOCH`` pinned, yields identical bytes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _abicheck_subprocess import abicheck_command, abicheck_env
from hypothesis import given, settings, strategies as st

from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.storage.graph_table_codec import encode_graph_table

_IDS = [f"decl://n{i}" for i in range(12)]
_KINDS = ["DECL_HAS_TYPE", "SOURCE_DECLARES", "X"]


def _build(nodes: list[str], edges: list[tuple[str, str, str]]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for node_id in nodes:
        g.add_node(GraphNode(id=node_id, kind="source_decl", attrs={"n": node_id}))
    for src, dst, kind in edges:
        g.add_edge(GraphEdge(src=src, dst=dst, kind=kind, attrs={"k": kind}))
    return g


def _encoded(g: SourceGraphSummary) -> str:
    return json.dumps(encode_graph_table(g), sort_keys=True)


@settings(max_examples=150, deadline=None)
@given(
    nodes=st.lists(st.sampled_from(_IDS), unique=True, min_size=1, max_size=12),
    edges=st.lists(
        st.tuples(
            st.sampled_from(_IDS), st.sampled_from(_IDS), st.sampled_from(_KINDS)
        ),
        unique=True,
        max_size=20,
    ),
    data=st.data(),
)
def test_insertion_order_never_reaches_the_encoding(nodes, edges, data) -> None:
    permuted_nodes = data.draw(st.permutations(nodes))
    permuted_edges = data.draw(st.permutations(edges))
    reference = _encoded(_build(sorted(nodes), sorted(edges)))
    assert _encoded(_build(list(permuted_nodes), list(permuted_edges))) == reference


def test_reversed_insertion_encodes_identically() -> None:
    nodes = _IDS[:6]
    edges = [(a, b, "X") for a, b in zip(nodes, nodes[1:], strict=False)]
    forward = _encoded(_build(nodes, edges))
    backward = _encoded(_build(nodes[::-1], edges[::-1]))
    assert forward == backward
    assert len(json.loads(forward)["nodes"]["id"]) == 6


def _corpus(seed: int, count: int) -> tuple[str, str]:
    hdr: list[str] = []
    src: list[str] = []
    for i in range(count):
        prev = f"S{(i * 7 + seed) % i}* p;" if i else ""
        hdr += [
            f"struct S{i} {{ int a{i}; {prev} }};",
            f"enum class E{i} {{ A{i}, B{i} }};",
            f"typedef S{i} T{i};",
            f"template<class X> struct Tm{i} {{ X x; S{i} s; }};",
            f'__attribute__((visibility("default"))) S{i}* f{i}(T{i}*, E{i}, int);',
            f'__attribute__((visibility("default"))) Tm{i}<int> g{i}(Tm{i}<S{i}>*);',
        ]
        src += [
            f"S{i}* f{i}(T{i}* t, E{i}, int) {{ return t; }}",
            f"Tm{i}<int> g{i}(Tm{i}<S{i}>*) {{ return {{}}; }}",
        ]
    header = f"#pragma once\nnamespace ns{seed} {{\n" + "\n".join(hdr) + "\n}\n"
    source = f'#include "lib.h"\nnamespace ns{seed} {{\n' + "\n".join(src) + "\n}\n"
    return header, source


@pytest.mark.integration
@pytest.mark.parametrize(("seed", "count"), [(0, 30), (3, 45)])
def test_dump_is_byte_identical_across_processes(
    tmp_path: Path, seed: int, count: int
) -> None:
    gxx = shutil.which("g++")
    if gxx is None or shutil.which("castxml") is None:
        pytest.skip("g++ and castxml are required")
    header, source = _corpus(seed, count)
    (tmp_path / "lib.h").write_text(header)
    (tmp_path / "lib.cpp").write_text(source)
    lib = tmp_path / "libx.so"
    subprocess.run(
        [
            gxx,
            "-g",
            "-shared",
            "-fPIC",
            "-fvisibility=hidden",
            "-o",
            str(lib),
            str(tmp_path / "lib.cpp"),
        ],
        check=True,
    )
    outputs = []
    for run, hash_seed in enumerate(("1", "4242", "random")):
        cwd = tmp_path / f"cwd{run}"
        cwd.mkdir()
        out = tmp_path / f"out{run}.json"
        env = abicheck_env(
            {"PYTHONHASHSEED": hash_seed, "SOURCE_DATE_EPOCH": "1700000000"}
        )
        subprocess.run(
            abicheck_command("dump", lib, "-H", tmp_path / "lib.h", "-o", out),
            check=True,
            cwd=cwd,
            env=env,
            capture_output=True,
        )
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]
