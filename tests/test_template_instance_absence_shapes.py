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

"""A template instance the new header stops instantiating, over every
template shape and both header-bearing depths (field report H2a/H2b).

A combinatorial generator over member vs free template, namespace depth 0/2,
overload count 1/2/3, primary kept vs deleted, and ``--depth headers`` vs
``source``. The oracle is the generator's own ground truth, never the rule's
helpers: when only the instance vanished (every primary overload still
declared), no removal is reported; when a primary overload was deleted, the
removal is still reported. Two more invariants:

* depth-metamorphic: the removal kinds found at ``headers`` are a subset of
  those found at ``source`` -- a lower depth may not invent a removal the
  higher depth rejects (the H2a escape: ``--depth headers`` cleared the graph
  the rule read);
* the depth projection keeps the header declaration ids without keeping the
  graph, so the L5 fallback (ADR-063 Phase 10) stays off.

The ``integration`` test at the bottom compiles the field report's shape with
``-fvisibility-inlines-hidden`` and runs it through ``abicheck dump``/
``compare`` at both depths.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from _field_shapes import materialize, replace_once, shape_sources

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph_fold import NODE_SOURCE_DECL
from abicheck.diff_symbols import _diff_functions
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.evidence_depth import resolve_l5_source_graph
from abicheck.model import AbiSnapshot, ScopeOrigin
from abicheck.model.declarations import Function, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.model.fact import Fact
from abicheck.policy.depth_projection import project_pair_to_depth

_REMOVAL_KINDS = {"func_removed", "inline_function_removed", "func_removed_elf_only"}
_DEPTHS = ("headers", "source")


@dataclass(frozen=True)
class Shape:
    member: bool
    ns_depth: int
    overloads: int
    primary_deleted: bool

    @property
    def stem(self) -> str:
        scopes = ["a", "b"][: self.ns_depth] + (["Table"] if self.member else [])
        return "::".join([*scopes, "setArrays"])

    @property
    def mangled(self) -> str:
        """``setArrays<double>(double*, unsigned long)`` returning ``int``,
        spelled independently of the rule's own parser."""
        scopes = ["a", "b"][: self.ns_depth] + (["Table"] if self.member else [])
        if not scopes:
            return "_Z9setArraysIdEiPT_m"
        nested = "".join(f"{len(s)}{s}" for s in scopes)
        return f"_ZN{nested}9setArraysIdEEiPT_m"

    def primaries(self, side: str) -> list[str]:
        ids = [f"decl://{self.stem}#sha256:{i:064x}" for i in range(self.overloads)]
        if side == "new" and self.primary_deleted:
            ids = ids[1:]  # one overload deleted from the new header
        return ids


SHAPES = [
    Shape(member, ns, n, deleted)
    for member, ns, n, deleted in itertools.product(
        (True, False), (0, 2), (1, 2, 3), (False, True)
    )
]


def _graph(ids: list[str]) -> SourceGraphSummary:
    graph = SourceGraphSummary()
    for node_id in ids:
        graph.add_node(GraphNode(id=node_id, kind=NODE_SOURCE_DECL))
    return graph


def _snap(shape: Shape, side: str) -> AbiSnapshot:
    functions = []
    if side == "old":
        functions.append(
            Function(
                name="setArrays",
                mangled=shape.mangled,
                return_type="int",
                visibility=Visibility.HIDDEN,  # -fvisibility-inlines-hidden
                is_inline=True,
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="include/t.h",
                # What castxml records for it: declared in a public header,
                # part of the contract, confirmed not exported.
                declared_in_headers_fact=Fact.present(True),
                in_public_contract_fact=Fact.present(True),
                binary_exported_fact=Fact.present(False),
            )
        )
    # -fvisibility-inlines-hidden: neither binary exports the instance.
    elf = ElfMetadata(
        machine="x86-64",
        symbols=[
            ElfSymbol(
                name="_Z5otherv", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
            )
        ],
    )
    return AbiSnapshot(
        library="libt.so",
        version="1" if side == "old" else "2",
        functions=functions,
        elf=elf,
        from_headers=True,
        surface_graph=_graph(shape.primaries(side)),
    )


def _removal_kinds(shape: Shape, depth: str) -> set[str]:
    old, new = project_pair_to_depth(_snap(shape, "old"), _snap(shape, "new"), depth)
    return {
        c.kind.value
        for c in _diff_functions(old, new)
        if c.symbol == shape.mangled and c.kind.value in _REMOVAL_KINDS
    }


@pytest.mark.parametrize("depth", _DEPTHS)
@pytest.mark.parametrize("shape", SHAPES, ids=str)
def test_instance_absence_follows_primary_presence(shape: Shape, depth: str) -> None:
    kinds = _removal_kinds(shape, depth)
    if shape.primary_deleted:
        assert kinds, f"{shape} at {depth}: deleted primary must still report"
    else:
        assert not kinds, f"{shape} at {depth}: only the instance vanished: {kinds}"


@pytest.mark.parametrize("shape", SHAPES, ids=str)
def test_headers_depth_invents_no_removal_source_depth_rejects(shape: Shape) -> None:
    assert _removal_kinds(shape, "headers") <= _removal_kinds(shape, "source")


def test_headers_projection_keeps_declaration_ids_not_the_l5_graph() -> None:
    shape = SHAPES[0]
    _, new = project_pair_to_depth(_snap(shape, "old"), _snap(shape, "new"), "headers")
    assert new.surface_graph is None
    assert resolve_l5_source_graph(new, None) is None
    assert new.header_declaration_ids == frozenset(shape.primaries("new"))


def test_binary_projection_keeps_no_declaration_ids() -> None:
    shape = SHAPES[0]
    _, new = project_pair_to_depth(_snap(shape, "old"), _snap(shape, "new"), "binary")
    assert new.header_declaration_ids is None


# --- compiled reproduction ---------------------------------------------------

_SHAPE = "overloaded_member_template"


def _overload_line(header: str, marker: str) -> str:
    (line,) = [ln for ln in header.splitlines(keepends=True) if marker in ln]
    return line


def _build(root: Path, *, overloads: int, uses: bool, keep_first: bool = True) -> Path:
    """The corpus shape (``v1`` instantiates ``setArrays`` from ``fill()``,
    ``v2`` does not; both declare two overloads), with the overload count and
    the first overload's presence varied by dropping corpus lines."""
    sources = shape_sources(_SHAPE, "v1" if uses else "v2")
    header = sources["api.h"]
    first = _overload_line(header, "setArrays(T* p, std::size_t n)")
    second = _overload_line(header, "setArrays(T* p, T* q, std::size_t n)")
    if overloads == 1:
        header = replace_once(header, second, "")
    if not keep_first:
        header = replace_once(header, first, "")
    materialize(_SHAPE, "", root, {**sources, "api.h": header})
    cmd = [
        "g++",
        "-shared",
        "-fPIC",
        "-fvisibility-inlines-hidden",
        f"-I{root}",
        str(root / "api.cpp"),
        "-o",
        str(root / "libt.so"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    out = root / "snap.json"
    dump = [
        sys.executable,
        "-m",
        "abicheck",
        "dump",
        str(root / "libt.so"),
        "-H",
        str(root / "api.h"),
        "-o",
        str(out),
    ]
    proc = subprocess.run(dump, capture_output=True, text=True)
    assert proc.returncode == 0, (dump, proc.stderr[-2000:])
    return out


def _compare_kinds(old: Path, new: Path, depth: str) -> set[str]:
    cmd = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(old),
        str(new),
        "--depth",
        depth,
        "-o",
        "json=-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    report = json.loads(proc.stdout)
    return {
        c["kind"]
        for c in report.get("changes", [])
        if "setArrays" in (c.get("symbol") or "") and c["kind"] in _REMOVAL_KINDS
    }


@pytest.mark.integration
@pytest.mark.parametrize("overloads", (1, 2))
def test_compiled_overloaded_member_template_instance_vanishes(
    tmp_path: Path, overloads: int
) -> None:
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    old = _build(tmp_path / "old", overloads=overloads, uses=True)
    new = _build(tmp_path / "new", overloads=overloads, uses=False)
    for depth in _DEPTHS:
        assert _compare_kinds(old, new, depth) == set(), depth


@pytest.mark.integration
def test_compiled_deleted_overload_still_reports(tmp_path: Path) -> None:
    """Control: the overload the old instance came from is deleted and the
    other overload is kept -- the removal is still reported at both depths."""
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    old = _build(tmp_path / "old", overloads=2, uses=True)
    new = _build(tmp_path / "new", overloads=2, uses=False, keep_first=False)
    for depth in _DEPTHS:
        assert _compare_kinds(old, new, depth), depth
