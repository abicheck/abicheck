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

"""ADR-062 A2.5: a finished release member must not keep its whole surface resident.

The fan-out keeps every member's ``DiffResult`` until the release-level folds
have run, so whatever one result retains is multiplied by the member count.
Measured with ``tracemalloc`` over a sequential fan-out, ~75% of that
per-member residue was the contract evidence's ``TypeGraphSnapshot`` -- one
per side, the size of the whole declaration surface, held as a tuple of
string pairs. It is now held packed (``contract_evidence.TypeGraphSnapshot``).

Two kinds of test:

* the measurement: peak and retained traced memory for N and 2N synthetic
  members, asserting the 2N run does not pay ~N more members' worth;
* the differential: the packed graph reads back exactly what the old tuple
  form held -- checked against an independent reference (``sorted(set())``),
  not the implementation's own helper -- and so serializes identically.
"""

from __future__ import annotations

import gc
import json
import pickle
import tracemalloc
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.contract_evidence import TypeGraphSnapshot
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_to_json
from abicheck.workflows import release_members

# ---------------------------------------------------------------------------
# Differential: the packed form is the old tuple form, read back.
# ---------------------------------------------------------------------------

_names = st.text(min_size=0, max_size=12)
_edges = st.lists(st.tuples(_names, _names), max_size=40)


def _reference(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, list]:
    """What the tuple-backed graph held: sorted, deduplicated sets."""
    return {
        "nodes": sorted(set(nodes)),
        "edges": [list(e) for e in sorted(set(edges))],
    }


class TestPackedGraphReadsBackTheTupleForm:
    @settings(max_examples=200, deadline=None)
    @given(nodes=st.lists(_names, max_size=40), edges=_edges)
    def test_nodes_and_edges_match_reference(self, nodes, edges) -> None:
        g = TypeGraphSnapshot(nodes=nodes, edges=edges)
        ref = _reference(nodes, edges)
        assert list(g.nodes) == ref["nodes"]
        assert [list(e) for e in g.edges] == ref["edges"]
        # The persisted shape `contract_context_io` writes, byte for byte.
        assert json.dumps(
            {"nodes": list(g.nodes), "edges": [list(e) for e in g.edges]}
        ) == json.dumps(ref)

    @settings(max_examples=100, deadline=None)
    @given(nodes=st.lists(_names, max_size=30), edges=_edges, data=st.data())
    def test_equality_and_hash_ignore_order_and_multiplicity(
        self, nodes, edges, data
    ) -> None:
        g = TypeGraphSnapshot(nodes=nodes, edges=edges)
        shuffled = TypeGraphSnapshot(
            nodes=data.draw(st.permutations(nodes + nodes)),
            edges=[list(e) for e in data.draw(st.permutations(edges))],
        )
        assert g == shuffled and hash(g) == hash(shuffled)
        assert pickle.loads(pickle.dumps(g)) == g  # nosec B301 - round-trips bytes this test just produced

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ({"nodes": ["a"]}, {"nodes": ["b"]}),
            ({"edges": [("a", "b")]}, {"edges": [("b", "a")]}),
            # An endpoint that is not a node is not a node.
            ({"edges": [("a", "b")]}, {"nodes": ["a", "b"], "edges": [("a", "b")]}),
        ],
    )
    def test_different_graphs_stay_different(self, a, b) -> None:
        assert TypeGraphSnapshot(**a) != TypeGraphSnapshot(**b)

    def test_immutable(self) -> None:
        g = TypeGraphSnapshot(nodes=["a"])
        with pytest.raises(AttributeError):
            g.nodes = ("b",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Measurement: N vs 2N members.
# ---------------------------------------------------------------------------

_DECLS = 1200


def _snap(lib: str, version: str, drop_first: bool) -> AbiSnapshot:
    funcs = [
        Function(
            name=f"f{i}",
            mangled=f"_Z2f{i}v",
            return_type="int",
            visibility=Visibility.PUBLIC,
            params=[Param(name="a", type=f"S{i % 40}*")],
        )
        for i in range(_DECLS)
        if not (drop_first and i == 0)
    ]
    types = [
        RecordType(
            name=f"S{j}",
            kind="struct",
            size_bits=256,
            fields=[
                TypeField(name=f"x{m}", type="int", offset_bits=32 * m)
                for m in range(8)
            ],
        )
        for j in range(40)
    ]
    return AbiSnapshot(
        library=lib, version=version, functions=funcs, types=types, from_headers=True
    )


def _release(root: Path, members: int) -> tuple[dict[str, Path], dict[str, Path]]:
    maps: tuple[dict[str, Path], dict[str, Path]] = ({}, {})
    for side, (version, drop) in enumerate((("1", False), ("2", True))):
        d = root / ("old", "new")[side]
        d.mkdir(parents=True)
        for i in range(members):
            path = d / f"lib{i}.json"
            path.write_text(snapshot_to_json(_snap(f"lib{i}.so", version, drop)))
            maps[side][f"lib{i}"] = path
    return maps


def _traced(root: Path, members: int) -> tuple[float, float]:
    """(peak, retained) traced MiB for a sequential fan-out of *members*;
    *retained* is what the held ``library_results`` keep after it returns."""
    old_map, new_map = _release(root, members)
    ctx = release_members.ReleaseMemberContext(
        request=release_members.release_parent_request(
            old_version="1", new_version="2", lang="c++"
        ),
        old_map=old_map,
        new_map=new_map,
    )
    keys = sorted(old_map)
    # A warm-up pair, so one-time imports and module caches are not counted.
    release_members.compare_release_members(keys[:1], ctx, jobs=1)
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        results, verdict, _ = release_members.compare_release_members(keys, ctx, jobs=1)
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert verdict == "BREAKING" and len(results) == members
    return (peak - base) / 2**20, (current - base) / 2**20


@pytest.mark.slow
def test_fan_out_memory_does_not_scale_with_member_count(tmp_path: Path) -> None:
    n = 3
    peak_n, kept_n = _traced(tmp_path / "n", n)
    peak_2n, kept_2n = _traced(tmp_path / "2n", 2 * n)
    per_member_kept = (kept_2n - kept_n) / n
    # Measured at this size: 0.92 MiB kept per member before A2.5, 0.24
    # after -- its packed graph, its public-declaration roots and its compact
    # bundle evidence. The bound fails on the tuple-backed graph.
    assert per_member_kept < 0.35, (kept_n, kept_2n)
    # Peak is set by the pair being compared, not by how many came before:
    # doubling the members may not come close to doubling the peak.
    # (Measured: 24.6 -> 28.0 MiB for 3 -> 6 members.)
    assert peak_2n < 1.3 * peak_n, (peak_n, peak_2n)
