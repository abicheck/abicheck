# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the data-source diagnostic pack merge (cli_datasources).

``--show-data-sources`` can be handed two *split* inputs — a build-info pack
(L3) and a sources pack (L4/L5) — collected independently.
``_combine_diagnostic_packs`` folds them into one diagnostic view. These cover
the merge precedence rules and the coverage-table reconciliation; the
binary-parsing entry point (``print_data_sources``) is exercised by the
integration lane.
"""
from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.model import (
    CoverageStatus,
    DataLayer,
    LayerCoverage,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.cli_datasources import _combine_diagnostic_packs


def _build_info_pack() -> BuildSourcePack:
    """A pack carrying only L3 build evidence + an intrinsic L0 coverage row."""
    pack = BuildSourcePack.empty(Path("build-info"))
    pack.build_evidence = BuildEvidence()
    pack.manifest.coverage = [
        LayerCoverage(layer="L0", status=CoverageStatus.PRESENT, detail="binary"),
        LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            detail="from build-info",
        ),
    ]
    return pack


def _sources_pack(*, with_graph: bool = True) -> BuildSourcePack:
    """A pack carrying L4 source-ABI (and optionally L5 graph) evidence."""
    pack = BuildSourcePack.empty(Path("sources"))
    pack.source_abi = SourceAbiSurface()
    coverage = [
        LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value,
            status=CoverageStatus.PRESENT,
            detail="from sources",
        )
    ]
    if with_graph:
        pack.source_graph = SourceGraphSummary()
        coverage.append(
            LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value,
                status=CoverageStatus.PRESENT,
                detail="from sources",
            )
        )
    pack.manifest.coverage = coverage
    return pack


def _row(pack: BuildSourcePack, layer: str) -> LayerCoverage | None:
    return next((c for c in pack.manifest.coverage if c.layer == layer), None)


def test_only_build_info_returns_it_unchanged() -> None:
    bi = _build_info_pack()
    assert _combine_diagnostic_packs(bi, None) is bi


def test_only_sources_returns_it_unchanged() -> None:
    src = _sources_pack()
    assert _combine_diagnostic_packs(None, src) is src


def test_both_none_returns_none() -> None:
    assert _combine_diagnostic_packs(None, None) is None


def test_combines_build_info_l3_with_sources_l4_l5() -> None:
    bi, src = _build_info_pack(), _sources_pack()
    combined = _combine_diagnostic_packs(bi, src)
    assert combined is not None

    # Payloads: L3 comes from build-info, L4/L5 from sources.
    assert combined.build_evidence is bi.build_evidence
    assert combined.source_abi is src.source_abi
    assert combined.source_graph is src.source_graph

    # Coverage table reconciles per layer, preserving the intrinsic L0 row.
    assert _row(combined, "L0") is not None
    l3 = _row(combined, DataLayer.L3_BUILD.value)
    l4 = _row(combined, DataLayer.L4_SOURCE_ABI.value)
    l5 = _row(combined, DataLayer.L5_SOURCE_GRAPH.value)
    assert l3 is not None and l3.detail == "from build-info"
    assert l4 is not None and l4.detail == "from sources"
    assert l5 is not None and l5.detail == "from sources"


def test_missing_l5_payload_yields_not_collected_row() -> None:
    # Neither pack carries an L5 graph: the combined view must still emit an
    # explicit NOT_COLLECTED row rather than silently dropping the layer.
    bi, src = _build_info_pack(), _sources_pack(with_graph=False)
    combined = _combine_diagnostic_packs(bi, src)
    assert combined is not None
    assert combined.source_graph is None

    l5 = _row(combined, DataLayer.L5_SOURCE_GRAPH.value)
    assert l5 is not None
    assert l5.status is CoverageStatus.NOT_COLLECTED


def test_source_abi_falls_back_to_build_info_when_sources_lacks_it() -> None:
    # If only the build-info pack happens to carry an L4 surface, the combined
    # pack must pick it up rather than leave L4 empty.
    bi = _build_info_pack()
    bi.source_abi = SourceAbiSurface()
    src = _sources_pack(with_graph=False)
    src.source_abi = None
    combined = _combine_diagnostic_packs(bi, src)
    assert combined is not None
    assert combined.source_abi is bi.source_abi
