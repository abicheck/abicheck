"""Fan-in metrics preserve graph incidence and visit each adjacency once."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.surface_graph import build_surface_graph, compute_surface_metrics


def _snapshot(edges: list[set[int]], qualified: bool) -> AbiSnapshot:
    names = [f"ns{i % 3}::T{i}" for i in range(len(edges))]
    return AbiSnapshot(
        library="graph.so",
        version="1",
        types=[
            RecordType(
                name=name,
                kind="struct",
                fields=[
                    TypeField(name=f"f{j}", type=names[j] if qualified else f"T{j}")
                    for j in sorted(refs)
                ],
            )
            for name, refs in zip(names, edges)
        ],
    )


@settings(max_examples=50)
@given(
    st.lists(st.sets(st.integers(0, 15), max_size=8), min_size=1, max_size=16),
    st.booleans(),
)
def test_metrics_match_incidence_matrix(edges: list[set[int]], qualified: bool) -> None:
    n = len(edges)
    edges = [{j for j in refs if j < n} for refs in edges]
    snap = _snapshot(edges, qualified)
    # Independent oracle: column sums of the input relation, before any
    # type parsing, graph aliasing or inverse-index construction.
    expected = sorted(
        [(f"ns{j % 3}::T{j}", sum(j in row for row in edges)) for j in range(n)],
        key=lambda pair: (-pair[1], pair[0]),
    )[:10]
    assert compute_surface_metrics(snap).top_fan_in == [
        (name, count) for name, count in expected if count
    ]


@pytest.mark.parametrize("n", [8, 32, 128])
def test_metrics_scan_adjacency_once(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    snap = _snapshot([{(i + 1) % n} for i in range(n)], True)
    graph = build_surface_graph(snap)
    visited = 0

    class CountedRefs(dict):
        def values(self):
            nonlocal visited
            for refs in super().values():
                visited += 1
                yield refs

    measured = replace(graph, type_refs=CountedRefs(graph.type_refs))
    monkeypatch.setattr(
        "abicheck.surface_graph.build_surface_graph", lambda *a, **kw: measured
    )
    metrics = compute_surface_metrics(snap)
    assert metrics.top_fan_in == sorted((name, 1) for name in graph.type_refs)[:10]
    assert visited <= n, f"{visited} adjacency visits for {n} types"


def test_cli_report_equals_reference_metrics(tmp_path, monkeypatch):
    import json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import snapshot_to_json

    old = _snapshot([{1}, {0}, {1}], True)
    new = _snapshot([{1}, {0, 2}, {1}], True)
    left, right = tmp_path / "old.json", tmp_path / "new.json"
    left.write_text(snapshot_to_json(old))
    right.write_text(snapshot_to_json(new))
    result_path = tmp_path / "report.json"
    args = ["compare", str(left), str(right), "-o", f"json={result_path}"]
    indexed = CliRunner().invoke(main, args)
    indexed_report = json.loads(result_path.read_text())

    def reference(graph, names, top_n):
        counts = sorted(
            ((name, graph.fan_in(name)) for name in names),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [(name, count) for name, count in counts[:top_n] if count > 0]

    monkeypatch.setattr("abicheck.surface_graph._top_fan_in", reference)
    original = CliRunner().invoke(main, args)
    assert indexed_report == json.loads(result_path.read_text())
    assert indexed.exit_code == original.exit_code
    assert indexed_report["summary"]["total_changes"] > 0
