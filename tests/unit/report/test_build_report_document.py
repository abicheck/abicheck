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

import ast
import inspect
import json
import xml.etree.ElementTree as ET
from unittest import mock

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.junit_report import to_junit_xml
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


class TestJunitReusesSharedDocument:
    """JUnit's own ADR-061 Phase 2 gap C slice: ``to_junit_xml``'s
    ``report_document`` parameter reuses the shared build's
    ``disposition_audit`` field instead of an independent
    ``compute_disposition_audit`` call, and ``render_output`` builds the
    shared document exactly once for the ``junit`` format.

    JUnit's per-finding verdict/category resolution, its symbol/testcase
    tree, and its root-cause grouping are *not* asserted here as reused --
    they remain JUnit's own computation today, same as SARIF's rule catalog
    and HTML's bucketing; see ``junit_report._build_testsuite``'s own
    docstring for why (the shared document builds no ``Change``-keyed
    ``ReportFinding`` set, and JUnit's own change sequence differs from
    ``result.changes`` by folding in ``scoped_only_changes``).
    """

    @staticmethod
    def _audit_properties(xml_text: str) -> dict[str, str]:
        root = ET.fromstring(xml_text)
        testsuite = root.find("testsuite")
        assert testsuite is not None
        props = testsuite.find("properties")
        assert props is not None
        return {
            p.get("name", ""): p.get("value", "")
            for p in props.findall("property")
            if (p.get("name") or "").startswith("abicheck.disposition")
            or (p.get("name") or "")
            in ("abicheck.detected_total", "abicheck.effective_total")
        }

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_disposition_audit_matches_independent_computation(
        self, changes: list[Change]
    ) -> None:
        result = _result(changes)
        doc = build_report_document(result)
        with_doc = self._audit_properties(to_junit_xml(result, report_document=doc))
        without_doc = self._audit_properties(to_junit_xml(result))
        expected = compute_disposition_audit(result, None).to_dict()

        assert with_doc["abicheck.detected_total"] == str(expected["detected_total"])
        assert with_doc == without_doc

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    def test_to_junit_xml_is_unaffected_by_report_document_reuse(
        self, changes: list[Change]
    ) -> None:
        """Passing the shared document must not change any other field --
        only the source of the disposition-audit properties changes, and
        that value itself is proven equal above."""
        result = _result(changes)
        doc = build_report_document(result)
        with_doc = to_junit_xml(result, report_document=doc)
        without_doc = to_junit_xml(result)
        assert with_doc == without_doc

    def test_junit_format_builds_the_shared_document_exactly_once(self) -> None:
        result = _result([_BREAKING, _ADDITION])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch(
            "abicheck.report.build.build_report_document",
            wraps=build_report_document,
        ) as spy:
            render_output("junit", result, old, new)

        assert spy.call_count == 1

    def test_junit_root_cause_mode_also_reuses_the_shared_document(self) -> None:
        """JUnit's own ``report_mode="root-cause"`` only adds ``rootCauseId``/
        ``rootCause`` attributes to each ``<failure>`` on top of the same
        shape -- the shared build still runs exactly once for it."""
        result = _result([_BREAKING, _ADDITION])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        with mock.patch(
            "abicheck.report.build.build_report_document",
            wraps=build_report_document,
        ) as spy:
            render_output("junit", result, old, new, report_mode="root-cause")

        assert spy.call_count == 1


class TestRendererOrderIndependence:
    """ADR-061 Phase 2 gap C acceptance test: with every one of the five
    named formats (JSON, Markdown/review, HTML, SARIF, JUnit) now crossing
    the shared ``build_report_document`` choke point, rendering the SAME
    completed ``DiffResult`` through several formats, in more than one
    order, must produce byte-identical per-format output regardless of
    order, and each format's own render must call the shared build exactly
    once -- never fewer (a stale/shared cache leaking between formats) and
    never more (redundant re-decision).
    """

    _FORMATS = ("json", "html", "sarif", "junit", "markdown")

    @staticmethod
    def _render_all(
        formats: tuple[str, ...], result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
    ) -> dict[str, str]:
        return {fmt: render_output(fmt, result, old, new) for fmt in formats}

    def test_output_is_byte_identical_regardless_of_render_order(self) -> None:
        result = _result([_BREAKING, _ADDITION, _QUALITY])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        forward = self._render_all(self._FORMATS, result, old, new)
        backward = self._render_all(tuple(reversed(self._FORMATS)), result, old, new)

        for fmt in self._FORMATS:
            assert forward[fmt] == backward[fmt], (
                f"{fmt!r} output differs depending on render order"
            )

    def test_shared_build_runs_exactly_once_per_format_render_in_either_order(
        self,
    ) -> None:
        result = _result([_BREAKING, _ADDITION, _QUALITY])
        old = _snapshot("1.0")
        new = _snapshot("2.0")

        for formats in (self._FORMATS, tuple(reversed(self._FORMATS))):
            with mock.patch(
                "abicheck.report.build.build_report_document",
                wraps=build_report_document,
            ) as spy:
                self._render_all(formats, result, old, new)
            # One call per format render (markdown's report_mode="full"
            # branch builds it too) -- never zero (would mean a format fell
            # back to its own legacy independent build) and never more than
            # len(formats) (would mean a format rebuilt the document a
            # second time within its own render).
            assert spy.call_count == len(formats)


class TestSarifAndJunitDecisionBoundary:
    """ADR-061 Phase 2 gap C acceptance test: a guard for SARIF's and
    JUnit's own pure-render code paths, the sibling of ``test_render_html.
    test_render_html_imports_no_decision_making_module``.

    HTML's guard works because ``report/render_html.py``/
    ``render_html_document.py`` are a real, already-established render-only
    module split from ``html_report.py``'s compute half (ADR-061 Phase 2
    item 4's fact-vs-formatting split). SARIF and JUnit have no equivalent
    split today: ``sarif.to_sarif``/``junit_report._build_testsuite`` are
    still one function each that both decides (rule catalog, per-result
    level, root-cause grouping, testcase tree) and formats its own
    format-specific shape -- exactly the "remains its own computation"
    scope this slice's own docstrings describe. Asserting those two modules
    import no decision-making module at all would be false today (they
    legitimately import ``severity``/``checker_policy`` for that
    not-yet-converged work), and asserting it of their actual pure-render
    helpers (``report.render_json.render_mapping_as_json``,
    ``report.render_xml.render_element_as_xml``) would be vacuous -- both
    are generic, format-agnostic JSON/XML serializers with no domain
    knowledge to begin with, shared by other formats too, so a guard there
    proves nothing about SARIF's or JUnit's *own* boundary.

    The real, honest boundary available today is narrower, and this is that
    boundary made executable: once a caller supplies a shared
    ``report_document``, the one piece of SARIF's/JUnit's own decision-making
    this slice actually converged -- the disposition-audit block -- must be
    read from it rather than re-decided via a second
    ``compute_disposition_audit`` call. This is the real, current contract;
    it does not claim SARIF/JUnit have become pure projections overall.
    """

    def test_to_sarif_does_not_recompute_disposition_audit_when_document_given(
        self,
    ) -> None:
        result = _result([_BREAKING, _ADDITION])
        doc = build_report_document(result)

        with mock.patch(
            "abicheck.report.disposition_audit.compute_disposition_audit"
        ) as spy:
            to_sarif(result, report_document=doc)

        spy.assert_not_called()

    def test_to_junit_xml_does_not_recompute_disposition_audit_when_document_given(
        self,
    ) -> None:
        result = _result([_BREAKING, _ADDITION])
        doc = build_report_document(result)

        with mock.patch(
            "abicheck.report.disposition_audit.compute_disposition_audit"
        ) as spy:
            to_junit_xml(result, report_document=doc)

        spy.assert_not_called()

    def test_sarif_and_junit_reuse_helper_is_the_single_disposition_audit_source(
        self,
    ) -> None:
        """Structural check that the reuse actually goes through the shared
        helper (not e.g. a hand-rolled ``report_document.to_mapping()[...]``
        at each call site that happens to work today but could drift): both
        ``sarif.to_sarif`` and ``junit_report._add_disposition_audit_properties``
        call ``disposition_audit_dict_reusing_document``, found via an AST
        scan of each function's own source rather than by re-running it --
        the assertion is about what the code *does when given a document*,
        which a black-box call-count test alone does not pin (a future edit
        could satisfy the two tests above by deleting the disposition-audit
        block entirely, not just by reusing it correctly)."""
        import abicheck.junit_report as junit_report_module
        import abicheck.sarif as sarif_module

        def _calls_reuse_helper(func: object) -> bool:
            source = inspect.getsource(func)  # type: ignore[arg-type]
            tree = ast.parse(source)
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        names.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        names.add(node.func.attr)
            return "disposition_audit_dict_reusing_document" in names

        assert _calls_reuse_helper(sarif_module.to_sarif)
        assert _calls_reuse_helper(
            junit_report_module._add_disposition_audit_properties
        )
