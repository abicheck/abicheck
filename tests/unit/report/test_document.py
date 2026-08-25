# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot
from abicheck.report.document import FrozenObject, ReportDocument
from abicheck.report.render_json import render_json
from abicheck.reporter import to_json, to_stat_json


def test_document_takes_an_immutable_defensive_snapshot() -> None:
    source: dict[str, object] = {"summary": {"breaking": 1}, "changes": ["a"]}
    document = ReportDocument.from_mapping(source)

    source["changes"] = []
    source_summary = source["summary"]
    assert isinstance(source_summary, dict)
    source_summary["breaking"] = 0

    assert document.to_mapping() == {
        "summary": {"breaking": 1},
        "changes": ["a"],
    }
    with pytest.raises(FrozenInstanceError):
        document.root = FrozenObject(())  # type: ignore[misc]


def test_projections_cannot_mutate_the_document() -> None:
    document = ReportDocument.from_mapping({"changes": [{"kind": "removed"}]})
    first = document.to_mapping()
    changes = first["changes"]
    assert isinstance(changes, list)
    changes.clear()

    assert document.to_mapping() == {"changes": [{"kind": "removed"}]}


def test_json_projection_preserves_order_and_shape() -> None:
    document = ReportDocument.from_mapping(
        {"report_schema_version": "2.41", "summary": {"total_changes": 0}}
    )

    assert json.loads(render_json(document, indent=2)) == document.to_mapping()


def test_document_rejects_non_json_values() -> None:
    with pytest.raises(TypeError, match="not JSON-compatible"):
        ReportDocument.from_mapping({"bad": object()})


@pytest.mark.parametrize("report_mode", ["full", "leaf", "root-cause"])
def test_every_native_json_mode_crosses_document_boundary(
    monkeypatch: pytest.MonkeyPatch, report_mode: str
) -> None:
    result = compare(AbiSnapshot("lib.so", "1"), AbiSnapshot("lib.so", "2"))
    calls = 0
    original = ReportDocument.from_mapping

    def recording_builder(value: dict[str, object]) -> ReportDocument:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(ReportDocument, "from_mapping", staticmethod(recording_builder))

    assert json.loads(to_json(result, report_mode=report_mode))["changes"] == []
    assert calls == 1


def test_stat_json_crosses_document_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    result = compare(AbiSnapshot("lib.so", "1"), AbiSnapshot("lib.so", "2"))
    calls = 0
    original = ReportDocument.from_mapping

    def recording_builder(value: dict[str, object]) -> ReportDocument:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(ReportDocument, "from_mapping", staticmethod(recording_builder))

    assert "changes" not in json.loads(to_stat_json(result))
    assert calls == 1
