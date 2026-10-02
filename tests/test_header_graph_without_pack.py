"""ADR-063 Phase 3/10: a header-only dump carries its header graph on
``AbiSnapshot.surface_graph`` alone, with no synthesized ``build_source`` pack.

The retired shape -- ``_attach_header_graph`` also setting ``snap.build_source =
BuildSourcePack(root=Path(""), source_graph=graph)`` with L3/L4 not-collected and
an L5 row -- is still what every *stored* pre-change document decodes to. So the
oracle here is differential and independent of the code under test: the
retired shape is rebuilt from its original construction (copied below, not
imported), and every L5 question -- which graph is the evidence, which coverage
rows are reported, which layers are present, what depth was reached, what
survives a ``--depth`` projection -- must answer the same for both shapes, over
graphs with and without edges and over every depth rung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.evidence_report import layer_presence, optional_coverage
from abicheck.buildsource.model import (
    CoverageStatus,
    DataLayer,
    LayerConfidence,
    LayerCoverage,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.evidence_depth import (
    embedded_evidence_pack,
    reported_depth_label,
    resolve_l5_source_graph,
)
from abicheck.model import AbiSnapshot
from abicheck.policy.depth_projection import project_snapshot_to_depth


def _graph(*, edges: bool, nodes: bool = True) -> SourceGraphSummary:
    node_list = (
        [
            GraphNode(id="d:f", kind="source_decl"),
            GraphNode(id="d:g", kind="source_decl"),
        ]
        if nodes
        else []
    )
    edge_list = (
        [GraphEdge(src="d:f", dst="d:g", kind="decl_calls_decl")] if edges else []
    )
    return SourceGraphSummary(nodes=node_list, edges=edge_list)


def _retired_shape(graph: SourceGraphSummary) -> AbiSnapshot:
    """The pre-Phase-10 attach, verbatim in effect: a synthesized pack sharing
    the graph with ``surface_graph``."""
    snap = AbiSnapshot(
        library="lib", version="1", from_headers=True, surface_graph=graph
    )
    pack = BuildSourcePack(root=Path(""), source_graph=graph)
    pack.manifest.coverage = [
        LayerCoverage(
            layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        ),
        LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if graph.edges else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED
            if graph.edges
            else LayerConfidence.UNKNOWN,
        ),
    ]
    snap.build_source = pack
    return snap


def _current_shape(graph: SourceGraphSummary) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib", version="1", from_headers=True, surface_graph=graph
    )


def _rows(rows: list[LayerCoverage]) -> list[tuple[str, str, str]]:
    return [(r.layer, str(r.status), str(r.confidence)) for r in rows]


_GRAPH_SHAPES = [
    pytest.param({"edges": True}, id="with-edges"),
    pytest.param({"edges": False}, id="no-edges"),
    pytest.param({"edges": False, "nodes": False}, id="empty"),
]
_DEPTHS = [None, "binary", "headers", "build", "source"]


@pytest.mark.parametrize("shape", _GRAPH_SHAPES)
@pytest.mark.parametrize("depth", _DEPTHS)
class TestBothShapesAnswerAlike:
    def _pair(self, shape: dict, depth: str | None) -> tuple[AbiSnapshot, AbiSnapshot]:
        return (
            project_snapshot_to_depth(_retired_shape(_graph(**shape)), depth),
            project_snapshot_to_depth(_current_shape(_graph(**shape)), depth),
        )

    def test_the_resolved_l5_graph(self, shape: dict, depth: str | None) -> None:
        retired, current = self._pair(shape, depth)
        old = resolve_l5_source_graph(retired, retired.build_source)
        new = resolve_l5_source_graph(current, current.build_source)
        assert (old is None) == (new is None)
        if old is not None and new is not None:
            assert [n.id for n in old.nodes] == [n.id for n in new.nodes]

    def test_the_depth_label(self, shape: dict, depth: str | None) -> None:
        retired, current = self._pair(shape, depth)
        assert reported_depth_label(
            retired, retired.build_source
        ) == reported_depth_label(current, current.build_source)

    def test_layer_presence(self, shape: dict, depth: str | None) -> None:
        retired, current = self._pair(shape, depth)
        assert layer_presence(retired, retired.build_source) == layer_presence(
            current, current.build_source
        )


@pytest.mark.parametrize("shape", _GRAPH_SHAPES)
def test_unprojected_coverage_rows_match(shape: dict) -> None:
    """The rows a report carries for an unprojected header-only dump. (A
    projected retired pack gains rows ``_mark_layers_not_collected`` stamps
    with detail text; presence and depth, compared above, are what those rows
    decide.)"""
    retired = _retired_shape(_graph(**shape))
    current = _current_shape(_graph(**shape))
    assert _rows(optional_coverage(retired.build_source, retired)) == _rows(
        optional_coverage(current.build_source, current)
    )


class TestResolverBoundaries:
    def test_an_explicit_none_beside_an_embedded_pack_resolves_nothing(self) -> None:
        """``pack=None`` while an embedded pack exists is a request for
        out-of-band evidence only (``depth_label_for``'s own contract)."""
        snap = _retired_shape(_graph(edges=True))
        assert resolve_l5_source_graph(snap, None) is None

    def test_a_structural_stand_in_is_not_evidence(self) -> None:
        class _Structural:
            nodes: list = []
            edges: list = []

        snap = AbiSnapshot(library="lib", version="1", surface_graph=_Structural())  # type: ignore[arg-type]
        assert resolve_l5_source_graph(snap, None) is None


class TestEmbeddedEvidencePack:
    def test_prefers_the_real_pack(self) -> None:
        snap = _retired_shape(_graph(edges=True))
        assert embedded_evidence_pack(snap) is snap.build_source

    def test_stands_in_for_a_header_graph_without_attaching_it(self) -> None:
        graph = _graph(edges=False)
        snap = _current_shape(graph)
        pack = embedded_evidence_pack(snap)
        assert pack is not None and pack.source_graph is graph
        assert _rows(pack.manifest.coverage) == _rows(
            _retired_shape(graph).build_source.manifest.coverage  # type: ignore[union-attr]
        )
        assert snap.build_source is None

    def test_none_without_either(self) -> None:
        assert embedded_evidence_pack(AbiSnapshot(library="lib", version="1")) is None


def test_embed_build_info_adopts_a_pack_less_header_graph(tmp_path: Path) -> None:
    """The ``--build-info`` embed backfill (``buildsource/embed.py``) adopts
    the header graph from ``surface_graph`` when no pack carries it, with the
    same L5 row the retired pack recorded."""
    import json

    from abicheck.cli_buildsource import embed_build_source

    cdb = tmp_path / "cc.json"
    cdb.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "src/foo.cpp",
                    "arguments": ["c++", "-std=c++17", "-c", "src/foo.cpp"],
                }
            ]
        )
    )
    graph = _graph(edges=False)
    snap = _current_shape(graph)

    embed_build_source(snap, cdb, None, collect_mode="build")

    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert snap.build_source.source_graph is graph
    cov = snap.build_source.manifest.coverage_for("L5_source_graph")
    assert cov is not None and cov.status == CoverageStatus.PARTIAL


class TestSurfaceGraphFollowsL5Scope:
    """``surface_graph`` is the header-only semantic graph. Its one class of
    reader is L5 (``evidence_depth.resolve_l5_source_graph`` and its callers;
    the public-surface closure deliberately does not read it), and since
    ADR-063 Phase 10 a header-only dump carries it with no synthesized pack,
    so for a pack-less snapshot it *is* the L5 evidence. Projection below
    ``source`` therefore drops it whenever no pack survives to carry an
    explicit L5 "not collected" row -- otherwise the resolver would resurrect
    L5 for a comparison whose report says L5 was excluded."""

    def _snap_with_graph(self, *, with_pack: bool = False) -> AbiSnapshot:
        from abicheck.buildsource.pack import BuildSourcePack

        graph = SourceGraphSummary()
        snap = AbiSnapshot(
            library="lib", version="1", from_headers=True, surface_graph=graph
        )
        if with_pack:
            snap.build_source = BuildSourcePack(root=Path(""))
        return snap

    @pytest.mark.parametrize("depth", ["binary", "headers", "build"])
    def test_pack_less_snapshot_drops_it_below_source(self, depth: str) -> None:
        projected = project_snapshot_to_depth(self._snap_with_graph(), depth)
        assert projected.surface_graph is None

    def test_kept_at_source(self) -> None:
        projected = project_snapshot_to_depth(self._snap_with_graph(), "source")
        assert projected.surface_graph is not None

    def test_a_surviving_pack_keeps_it_behind_its_l5_row(self) -> None:
        from abicheck.evidence_depth import resolve_l5_source_graph

        projected = project_snapshot_to_depth(
            self._snap_with_graph(with_pack=True), "build"
        )
        assert projected.surface_graph is not None
        assert projected.build_source is not None
        assert resolve_l5_source_graph(projected, projected.build_source) is None

    @pytest.mark.parametrize("with_pack", [False, True])
    @pytest.mark.parametrize("depth", ["binary", "headers", "build"])
    def test_no_projection_below_source_resolves_an_l5_graph(
        self, depth: str, with_pack: bool
    ) -> None:
        """The invariant the clearing exists for, over every rung below
        ``source`` and both pack shapes."""
        from abicheck.evidence_depth import resolve_l5_source_graph

        projected = project_snapshot_to_depth(
            self._snap_with_graph(with_pack=with_pack), depth
        )
        assert resolve_l5_source_graph(projected, projected.build_source) is None


@pytest.mark.parametrize("shape", _GRAPH_SHAPES)
class TestConsumersReadBothShapesAlike:
    """The consumers that reached the graph *off* the pack rather than through
    `resolve_l5_source_graph` -- `--use-cases` (`impact.use_case_impact`) and
    analysis assurance -- must see the same graph and coverage from either
    shape. The first version of this change missed both, and only the
    composite Action's `--use-cases` job caught it."""

    def test_use_case_impact_finds_the_graph(self, shape: dict) -> None:
        from abicheck.impact.use_case_impact import _source_graph

        old = _source_graph(_retired_shape(_graph(**shape)))
        new = _source_graph(_current_shape(_graph(**shape)))
        assert (old is None) == (new is None)
        if old is not None and new is not None:
            assert [n.id for n in old.nodes] == [n.id for n in new.nodes]

    def test_analysis_assurance_sees_the_same_pack(self, shape: dict) -> None:
        from abicheck.checker_policy import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.workflows.analysis_assurance_attach import (
            attach_analysis_assurance,
        )

        def _assurance(make):
            old, new = make(_graph(**shape)), make(_graph(**shape))
            result = DiffResult(
                old_version="1",
                new_version="1",
                library="lib",
                verdict=Verdict.NO_CHANGE,
            )
            attach_analysis_assurance(result, old, new)
            return result.analysis_assurance

        assert _assurance(_retired_shape) == _assurance(_current_shape)
