# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 Phase 2 gap C: the shared report-document choke point.

``report.build.build_report_document`` is the one format-neutral build
routed through the ADR-061 "one build, many projections" shape -- see
``report/build.py``'s own module docstring for the precise, current
per-format scope. These tests state the two guarantees that choke point
exists to provide, for JSON and (below) SARIF's own reuse of it:

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
from abicheck.report.disposition_audit import compute_disposition_audit
from abicheck.report.document import ReportDocument
from abicheck.report.render_json import render_json
from abicheck.reporter import to_json
from abicheck.sarif import to_sarif, to_sarif_str
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


class TestSarifReusesSharedDocument:
    """SARIF's own ADR-061 Phase 2 gap C slice: ``to_sarif``'s
    ``report_document`` parameter reuses the shared build's
    ``disposition_audit`` field instead of an independent
    ``compute_disposition_audit`` call, and ``render_output`` builds the
    shared document exactly once for the ``sarif`` format.
    """

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_disposition_audit_matches_independent_computation(
        self, changes: list[Change]
    ) -> None:
        result = _result(changes)
        doc = build_report_document(result)
        with_doc = to_sarif(result, report_document=doc)
        without_doc = to_sarif(result)
        expected = compute_disposition_audit(result, None).to_dict()

        assert (
            with_doc["runs"][0]["properties"]["dispositionAudit"] == expected
        )
        assert (
            with_doc["runs"][0]["properties"]["dispositionAudit"]
            == without_doc["runs"][0]["properties"]["dispositionAudit"]
        )

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_to_sarif_str_is_unaffected_by_report_document_reuse(
        self, changes: list[Change]
    ) -> None:
        """Passing the shared document must not change any other field --
        only the source of the ``dispositionAudit`` value changes, and that
        value itself is proven equal above."""
        result = _result(changes)
        doc = build_report_document(result)
        with_doc = json.loads(to_sarif_str(result, report_document=doc))
        without_doc = json.loads(to_sarif_str(result))
        assert with_doc == without_doc

    def test_sarif_format_builds_the_shared_document_exactly_once(self) -> None:
        result = _result([_BREAKING, _ADDITION])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch(
            "abicheck.report.build.build_report_document",
            wraps=build_report_document,
        ) as spy:
            render_output("sarif", result, old, new)

        assert spy.call_count == 1

    def test_sarif_root_cause_mode_also_reuses_the_shared_document(self) -> None:
        """SARIF's own ``report_mode="root-cause"`` only adds per-result
        properties on top of the same shape (unlike Markdown's separate
        leaf/root-cause alternate documents) -- the shared build still runs
        exactly once for it."""
        result = _result([_BREAKING, _ADDITION])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch(
            "abicheck.report.build.build_report_document",
            wraps=build_report_document,
        ) as spy:
            render_output("sarif", result, old, new, report_mode="root-cause")

        assert spy.call_count == 1
