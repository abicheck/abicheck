"""A failed clang AST must not leave the header type pass reading as complete.

When the header AST fails, the flat fallback still stamps ``header_type_graph``
as collected, from the snapshot's own declarations. That pass also owns the
AST-only type edges (parameter and variable ``DECL_HAS_TYPE``, ``DECL_REFERENCES_DECL``),
which were never read, so the family must come out not collected and degraded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import abicheck.service_header_graph_attach as attach
from abicheck.buildsource.header_graph_ast_projection import (
    HEADER_TYPE_GRAPH_PASS,
    degrade_header_type_pass,
)
from abicheck.buildsource.inline_graph_fold import _role_coverage_key
from abicheck.model import AbiSnapshot
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import ROLE_COVERAGE_MATRIX


def _attach_with_ast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ast_failure: str | None
) -> SourceGraphSummary:
    header = tmp_path / "api.h"
    header.write_text("struct Rec { int x; };\nvoid f(Rec r);\n")

    def _acquire(*_args: object, **_kwargs: object) -> attach.HeaderGraphAst:
        return attach.HeaderGraphAst(None, ast_failure, [header], [], ())

    monkeypatch.setattr(attach, "acquire_header_graph_ast", _acquire)
    snap = AbiSnapshot(library="lib", version="1.0", from_headers=True)
    attached = attach._attach_header_graph(
        snap, True, False, [header], [], None, None, None, None
    )
    graph = attached.surface_graph
    assert isinstance(graph, SourceGraphSummary)
    return graph


def test_an_ast_failure_reads_the_header_type_pass_as_degraded_not_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph = _attach_with_ast(monkeypatch, tmp_path, "clang AST parse failed: boom")

    assert graph.degraded_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert graph.coverage["type_edges"]["collected"] is False
    assert HEADER_TYPE_GRAPH_PASS not in graph.extractor_passes
    for edge_kind, roles in ROLE_COVERAGE_MATRIX.items():
        for role in roles:
            key = _role_coverage_key(HEADER_TYPE_GRAPH_PASS, edge_kind, role)
            assert key not in graph.extractor_passes


def test_without_an_ast_failure_the_flat_type_pass_stays_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Control: with no AST failure (clang simply not selected) the flat
    # structural pass is the honest answer it always was, so it is unchanged.
    graph = _attach_with_ast(monkeypatch, tmp_path, None)

    assert graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert HEADER_TYPE_GRAPH_PASS not in graph.degraded_passes
    assert graph.coverage["type_edges"]["collected"] is True


def test_degrade_header_type_pass_drops_role_keys_a_partial_parse_stamped() -> None:
    graph = SourceGraphSummary()
    graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] = True
    for edge_kind, roles in ROLE_COVERAGE_MATRIX.items():
        for role in roles:
            graph.extractor_passes[
                _role_coverage_key(HEADER_TYPE_GRAPH_PASS, edge_kind, role)
            ] = True
    graph.extractor_passes["call_graph"] = True

    degrade_header_type_pass(graph)

    assert graph.degraded_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert set(graph.extractor_passes) == {"call_graph"}
