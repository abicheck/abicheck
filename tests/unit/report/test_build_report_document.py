# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 Phase 2 gap C: JSON's shared report-document choke point.

``report.build.build_report_document`` is the first (and so far only)
format-neutral build routed through the ADR-061 "one build, many
projections" shape -- see ``report/build.py``'s own module docstring for
the precise, current per-format scope. These tests state the two guarantees
that choke point exists to provide:

- rendering the shared document produces byte-identical JSON to the
  previous per-call ``to_json`` pipeline (no behavior change from the
  refactor);
- ``abicheck.service_render.render_output`` calls the shared build exactly
  once per top-level render for the JSON format, never once per format
  branch and never twice for one JSON render.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.model import AbiSnapshot
from abicheck.report.build import build_report_document
from abicheck.report.document import ReportDocument
from abicheck.report.render_json import render_json
from abicheck.reporter import to_json
from abicheck.service_render import render_output

_BREAKING = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
_ADDITION = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
_QUALITY = Change(ChangeKind.VISIBILITY_LEAK, "_Z3barv", "visibility leak")

_CHANGE_COMBINATIONS: list[list[Change]] = [
    [],
    [_BREAKING],
    [_ADDITION],
    [_BREAKING, _ADDITION, _QUALITY],
]


def _result(changes: list[Change], *, show_only: str | None = None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=list(changes),
        policy="strict_abi",
    )


def _snapshot(version: str) -> AbiSnapshot:
    return AbiSnapshot(library="libtest.so.1", version=version)


class TestBuildReportDocumentMatchesToJson:
    """The shared build must not change what JSON callers already see."""

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_render_json_of_shared_document_equals_to_json(
        self, changes: list[Change]
    ) -> None:
        result = _result(changes)
        expected = json.loads(to_json(result))
        doc = build_report_document(result)
        assert isinstance(doc, ReportDocument)
        actual = json.loads(render_json(doc))
        assert actual == expected

    def test_show_only_is_threaded_through(self) -> None:
        result = _result([_BREAKING, _ADDITION])
        expected = json.loads(to_json(result, show_only="removed"))
        actual = json.loads(
            render_json(build_report_document(result, show_only="removed"))
        )
        assert actual == expected


class TestBuildCalledExactlyOnce:
    """``render_output`` must build the shared document exactly once."""

    def test_json_format_builds_the_shared_document_exactly_once(self) -> None:
        result = _result([_BREAKING, _ADDITION])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch(
            "abicheck.report.build.build_report_document",
            wraps=build_report_document,
        ) as spy:
            render_output("json", result, old, new)

        assert spy.call_count == 1

    def test_full_mode_json_never_falls_back_to_the_legacy_to_json_pipeline(
        self,
    ) -> None:
        """A regression guard for the refactor itself: ``to_json``'s own

        independent dict-building pass must not run a second time for the
        default (``report_mode="full"``) JSON render -- only the shared
        document build may.
        """
        result = _result([_BREAKING])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch("abicheck.reporter.to_json") as legacy:
            render_output("json", result, old, new)

        legacy.assert_not_called()
