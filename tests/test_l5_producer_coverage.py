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

"""Evidence-entity-model gap A5: five L5 edge producers stamp coverage, and the
findings that rest on their edges' *absence* read the typed answer.

``SOURCE_DECL_MAPS_TO_SYMBOL``/``SOURCE_DECLARES`` (the L4 source-ABI fold, or
a header-only declarations pass), ``TARGET_HAS_PUBLIC_HEADER``/
``TARGET_DEPENDS_ON`` (the build-target fold) and ``BUILD_OPTION_AFFECTS_SYMBOL``
(the option linker) used to stamp nothing, so mapping-drift,
public-reachability, generated-closure, build-option and target-dependency
findings diffed raw edge sets: a side that never ran the producer read as
"producer ran, found nothing". Each finding now needs the absent side's
producer to have covered the project (``edge_query.source_graph_covers``); an
unflagged graph answers ``unknown``.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.source_graph import GraphEdge, GraphNode, SourceGraphSummary

# How the side whose *absence* the finding rests on was produced.
STATES = ("ran", "unflagged", "narrowed", "degraded", "other_pass_only")


def _flags(state: str, pass_name: str) -> dict[str, dict[str, bool]]:
    return {
        "ran": {"extractor_passes": {pass_name: True}},
        "unflagged": {},
        "narrowed": {"narrowed_passes": {pass_name: True}},
        "degraded": {"degraded_passes": {pass_name: True}},
        # A graph that recorded some *other* producer: flagged, but this
        # family's producer never ran.
        "other_pass_only": {"extractor_passes": {"include_graph": True}},
    }[state]


def _g(nodes, edges, state: str, pass_name: str) -> SourceGraphSummary:
    g = SourceGraphSummary(nodes=list(nodes), edges=list(edges))
    for attr, flags in _flags(state, pass_name).items():
        getattr(g, attr).update(flags)
    return g


def _N(i: str, kind: str, **attrs: str) -> GraphNode:
    return GraphNode(id=i, kind=kind, label=i, attrs=dict(attrs))


def _E(s: str, d: str, k: str) -> GraphEdge:
    return GraphEdge(src=s, dst=d, kind=k)


# One scenario per finding family: (pass, kind, nodes, old edges, new edges),
# where the finding is "NEW has an edge OLD lacks", so OLD is the absent side.
SCENARIOS = {
    "target_dependency": (
        "build_targets",
        ChangeKind.TARGET_DEPENDENCY_ADDED,
        [_N("t:a", "target"), _N("t:b", "target")],
        [],
        [_E("t:a", "t:b", "TARGET_DEPENDS_ON")],
    ),
    "generated_closure": (
        "build_targets",
        ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
        [_N("t:a", "target"), _N("gen:config.h", "generated_file")],
        [],
        [_E("t:a", "gen:config.h", "TARGET_HAS_PUBLIC_HEADER")],
    ),
    "public_reachability": (
        "source_abi",
        ChangeKind.PUBLIC_REACHABILITY_CHANGED,
        [_N("hdr", "header"), _N("d", "source_decl"), _N("d2", "source_decl")],
        [_E("hdr", "d2", "SOURCE_DECLARES")],
        [_E("hdr", "d2", "SOURCE_DECLARES"), _E("hdr", "d", "SOURCE_DECLARES")],
    ),
    "mapping_drift": (
        "source_abi",
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
        [_N("d", "source_decl"), _N("s", "binary_symbol")],
        [],
        [_E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL")],
    ),
    "build_option": (
        "build_options",
        ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL,
        [_N("opt:-fshort-enums", "build_option"), _N("s", "binary_symbol")],
        [],
        [_E("opt:-fshort-enums", "s", "BUILD_OPTION_AFFECTS_SYMBOL")],
    ),
}


def _expected(old_state: str) -> bool:
    """By hand: only a producer that covered the whole project proves OLD's
    absence."""
    return old_state == "ran"


@pytest.mark.parametrize(
    ("family", "old_state"), list(itertools.product(sorted(SCENARIOS), STATES))
)
def test_finding_needs_the_absent_sides_producer(family: str, old_state: str) -> None:
    pass_name, kind, nodes, old_edges, new_edges = SCENARIOS[family]
    old_nodes = list(nodes)
    if family == "build_option":
        old_nodes = [n for n in nodes if n.kind != "build_option"]
    old = _g(old_nodes, old_edges, old_state, pass_name)
    new = _g(nodes, new_edges, "ran", pass_name)
    kinds = {c.kind for c in diff_source_graph_findings(old, new)}
    assert (kind in kinds) is _expected(old_state), (family, old_state, kinds)


def test_mapping_drift_is_symmetric_in_which_side_lacks_the_edge() -> None:
    """Losing a mapping needs NEW's producer, gaining one OLD's."""
    _pass, kind, nodes, edges_without, edges_with = SCENARIOS["mapping_drift"]
    for with_state, without_state, want in (
        ("ran", "ran", True),
        ("ran", "unflagged", False),
        ("unflagged", "ran", True),  # the side *with* the edge needs nothing
    ):
        lost = diff_source_graph_findings(
            _g(nodes, edges_with, with_state, "source_abi"),
            _g(nodes, edges_without, without_state, "source_abi"),
        )
        assert (kind in {c.kind for c in lost}) is want, (with_state, without_state)


def test_the_oracle_is_not_constant() -> None:
    assert {_expected(s) for s in STATES} == {True, False}


# --- producers stamp their passes ---------------------------------------


def _surface(families: dict[str, str] | None):
    from abicheck.buildsource.source_abi import SourceAbiSurface

    surface = SourceAbiSurface(library="libx.so", target_id="t:libx")
    if families is not None:
        surface.coverage["fact_family_states"] = families
    return surface


@pytest.mark.parametrize(
    ("families", "expect"),
    [
        (None, "ran"),
        ({"functions": "complete", "types": "empty-confirmed"}, "ran"),
        ({"functions": "partial"}, "degraded"),
        ({"types": "failed"}, "degraded"),
    ],
)
def test_build_source_graph_stamps_every_producer(
    families: dict[str, str] | None, expect: str
) -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_graph_build import build_source_graph

    bare = build_source_graph(BuildEvidence())
    assert bare.extractor_passes.get("build_targets") is True
    assert "source_abi" not in bare.extractor_passes  # no L4 surface: not run
    assert "build_options" not in bare.extractor_passes

    graph = build_source_graph(BuildEvidence(), _surface(families))
    assert graph.extractor_passes.get("build_options") is True
    ran = graph.extractor_passes.get("source_abi") is True
    degraded = graph.degraded_passes.get("source_abi") is True
    assert (ran, degraded) == (expect == "ran", expect == "degraded")
    assert graph.coverage["pass_flags_recorded"] is True


def test_edge_query_answers_the_new_kinds() -> None:
    from abicheck.compare.edge_query import EdgeEvidence
    from abicheck.model import AbiSnapshot

    nodes = [_N("t:a", "target"), _N("t:b", "target")]
    for state, want in (("ran", "proven_absent"), ("unflagged", "unknown")):
        graph = _g(nodes, [], state, "build_targets")
        ev = EdgeEvidence(AbiSnapshot(library="l", version="1"), source_graph=graph)
        got = ev.query("TARGET_DEPENDS_ON", "t:a").answer.value
        assert got == want, (state, got)


@pytest.mark.parametrize(
    ("old_flags", "finding", "absence"),
    [({"build_targets": True}, True, "proven_absent"), ({}, False, "unknown")],
    ids=["flagged_baseline", "unflagged_stored_baseline"],
)
def test_cli_report_through_an_embedded_l5_graph(
    tmp_path, old_flags: dict[str, bool], finding: bool, absence: str
) -> None:
    """Through `abicheck compare` on stored snapshots: an unflagged (pre-A5)
    baseline graph neither yields the finding nor hides the gap -- the
    report's coverage section answers ``unknown``."""
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    def graph(edges, flags):
        g = SourceGraphSummary(
            nodes=[_N("t:a", "target"), _N("t:b", "target")], edges=list(edges)
        )
        g.extractor_passes.update(flags)
        return g.finalize()

    old = AbiSnapshot(
        library="l", version="1",
        build_source=BuildSourcePack(root="", source_graph=graph([], old_flags)),
    )  # fmt: skip
    new = AbiSnapshot(
        library="l", version="2",
        build_source=BuildSourcePack(
            root="",
            source_graph=graph(
                [_E("t:a", "t:b", "TARGET_DEPENDS_ON")], {"build_targets": True}
            ),
        ),
    )  # fmt: skip
    old_p, new_p, rep = tmp_path / "o.json", tmp_path / "n.json", tmp_path / "r.json"
    save_snapshot(old, old_p)
    save_snapshot(new, new_p)
    res = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "-o", f"json={rep}"]
    )
    assert res.exit_code in (0, 1, 2, 4), res.output
    doc = json.loads(rep.read_text())
    kinds = {c["kind"] for c in doc["changes"]}
    assert ("target_dependency_added" in kinds) is finding
    assert doc["edge_coverage"]["old"]["TARGET_DEPENDS_ON"]["absence"] == absence
