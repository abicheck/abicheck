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
import contextlib
import importlib
import inspect
import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from unittest import mock

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, LibraryMetadata, Verdict
from abicheck.junit_report import to_junit_xml
from abicheck.model import AbiSnapshot
from abicheck.policy.disposition_close import finalize_ledger
from abicheck.policy.disposition_ledger import DispositionLedger
from abicheck.policy_file import PolicyFile
from abicheck.report.build import (
    _snapshot_change,
    build_report_document,
    build_report_envelope,
)
from abicheck.report.disposition_audit import compute_disposition_audit
from abicheck.report.document import ReportDocument
from abicheck.report.envelope import RenderOptions, ReportEnvelope
from abicheck.report.render_json import render_json
from abicheck.reporter import to_json
from abicheck.sarif import to_sarif, to_sarif_str
from abicheck.service_render import render_envelope, render_output
from abicheck.severity import SeverityConfig, SeverityLevel


def _import_attr(dotted: str) -> object:
    """The live callable a ``module.attr`` patch target names."""
    module_name, _, attr = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), attr)

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

    # ``review`` and the ``md`` alias are in the set too: gap C's claim is
    # about every format of one evaluation, and an alias that silently took a
    # different path would be exactly the kind of divergence this class exists
    # to catch.
    _FORMATS = ("json", "html", "sarif", "junit", "markdown", "md", "review")

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

    # ------------------------------------------------------------------
    # ADR-061 gap C closure package 3: the same guarantees for ONE shared
    # `ReportEnvelope` rendered into every format, which is the stronger
    # statement the two tests above cannot make. Above, each format render
    # builds its own document from the same `DiffResult` (N documents that
    # happen to agree); below, N formats project ONE completed envelope, so
    # they cannot disagree by construction.
    # ------------------------------------------------------------------

    #: Every decision function a projection must NOT reach, keyed by the
    #: module attribute a renderer actually resolves. Each is patched with
    #: ``wraps=`` the real callable, so a projection that still calls one
    #: renders correctly and is *counted* rather than broken -- the test
    #: fails on the count, not on a mangled render.
    #: ``build.py``/``envelope.py`` each do ``from .finding import
    #: build_report_findings`` -- that binds a name in *their own* module
    #: namespace at import time, so patching
    #: ``abicheck.report.finding.build_report_findings`` alone never sees a
    #: call issued through either bound reference; the patch must target the
    #: name each consumer actually calls (CodeRabbit review).
    _DECISION_SITES = (
        "abicheck.report.build.build_report_document",
        "abicheck.policy.gate_decision.gate_decision_for_result",
        "abicheck.report.finding.build_report_findings",
        "abicheck.report.finding.report_findings_for",
        "abicheck.report.surface_changes.build_report_findings",
        "abicheck.report.build.build_report_findings",
        "abicheck.report.envelope.build_report_findings",
    )

    @staticmethod
    def _envelope(
        result: DiffResult, old: AbiSnapshot, new: AbiSnapshot, **options: object
    ) -> ReportEnvelope:
        return build_report_envelope(
            result, old, new, options=RenderOptions(**options)  # type: ignore[arg-type]
        )

    @staticmethod
    def _render_all_from(
        formats: tuple[str, ...], envelope: ReportEnvelope
    ) -> dict[str, str]:
        return {fmt: render_envelope(fmt, envelope) for fmt in formats}

    @contextlib.contextmanager
    def _decision_spies(self) -> Iterator[dict[str, mock.MagicMock]]:
        """Patch every decision site at once, each wrapping the real callable."""
        with contextlib.ExitStack() as stack:
            yield {
                target: stack.enter_context(
                    mock.patch(target, wraps=_import_attr(target))
                )
                for target in self._DECISION_SITES
            }

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    @pytest.mark.parametrize(
        "options",
        [
            {},
            {"show_only": "breaking"},
            {"show_impact": True},
            {"contract_evaluation": True, "require_complete_analysis": True},
        ],
        ids=["plain", "show_only", "impact", "contract"],
    )
    def test_one_envelope_renders_byte_identically_in_any_format_order(
        self, changes: list[Change], options: dict[str, object]
    ) -> None:
        """The acceptance test ADR-061 gap C states: render the same completed
        document repeatedly, in different format orders, and every format's
        bytes must be identical every time.

        Parametrized over several change sets *and* several option sets rather
        than one fixed input: a fixed example would only foreclose the one
        envelope it names, and the invariant claimed here ("a projection is a
        pure function of the envelope") is a statement about all of them.
        """
        result = _result(changes)
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new, **options)

        forward = self._render_all_from(self._FORMATS, envelope)
        backward = self._render_all_from(tuple(reversed(self._FORMATS)), envelope)
        again = self._render_all_from(self._FORMATS, envelope)

        for fmt in self._FORMATS:
            assert forward[fmt] == backward[fmt], (
                f"{fmt!r} output differs depending on render order"
            )
            assert forward[fmt] == again[fmt], (
                f"{fmt!r} output differs when the same envelope is rendered twice"
            )

    @pytest.mark.parametrize("changes", _CHANGE_COMBINATIONS)
    @pytest.mark.parametrize(
        "options",
        [{}, {"show_only": "breaking"}, {"show_impact": True}],
        ids=["plain", "show_only", "impact"],
    )
    def test_envelope_projection_matches_render_output_byte_for_byte(
        self, changes: list[Change], options: dict[str, object]
    ) -> None:
        """``render_output`` is exactly "build one envelope, project it".

        Without this, the two entry points could drift: ``render_output``
        could keep threading an option a projection ignores (or vice versa),
        and every other test here would still pass.
        """
        result = _result(changes)
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new, **options)

        for fmt in self._FORMATS:
            assert render_envelope(fmt, envelope) == render_output(
                fmt, result, old, new, **options  # type: ignore[arg-type]
            ), f"{fmt!r} disagrees between render_output and render_envelope"

    def test_no_projection_re_runs_policy_or_gate_resolution(self) -> None:
        """Every decision runs once, during envelope construction -- and none
        of them runs again no matter how many formats are rendered after.

        This is the half a byte-comparison cannot prove: five renderers each
        re-deriving the same value from the same ``DiffResult`` produce
        identical bytes too (that is exactly the pre-envelope state gap C
        describes), so only a call count separates "cannot disagree" from
        "happens to agree today".
        """
        result = _result([_BREAKING, _ADDITION, _QUALITY])
        old, new = _snapshot("1.0"), _snapshot("2.0")

        with self._decision_spies() as spies:
            envelope = build_report_envelope(result, old, new)
            build_calls = {t: s.call_count for t, s in spies.items()}
            # Render every format twice, in both orders: 10 projections.
            for formats in (self._FORMATS, tuple(reversed(self._FORMATS))):
                self._render_all_from(formats, envelope)
            after = {t: s.call_count for t, s in spies.items()}

        assert build_calls["abicheck.report.build.build_report_document"] == 1
        assert build_calls["abicheck.policy.gate_decision.gate_decision_for_result"] == 1
        for target in self._DECISION_SITES:
            assert after[target] == build_calls[target], (
                f"{target} ran again while projecting an already-completed "
                f"envelope ({after[target] - build_calls[target]} extra call(s))"
            )

    def test_mutating_the_caller_s_result_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """A live handle on the ``DiffResult`` passed in must not leak.

        Every projection above reads ``envelope.result`` directly for its own
        presentation (HTML's/JUnit's bucketing, SARIF's rule catalog), not
        only through ``document``/``findings``/``gate``. If the envelope held
        the caller's own, still-mutable ``DiffResult`` object, a later
        mutation of it -- appending a change, as a caller/sibling pass in
        this codebase legitimately does elsewhere (``post_manifest.py``), or
        reassigning ``.changes`` wholesale (``cli_scan_baseline.py``) --
        would desynchronize those direct readers from the document/findings/
        gate the envelope already froze, defeating the whole point of
        building one envelope and projecting it into several formats
        (reported by an external review of this refactor). This test
        reproduces exactly that scenario: mutate the original object *after*
        the envelope was built, then confirm every projection still reports
        the pre-mutation state.
        """
        result = _result([])
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        # Mutate the caller's own object after envelope construction: append
        # a breaking change (the concrete scenario reported), and separately
        # reassign `.changes` outright to prove reassignment can't leak in
        # either.
        result.changes.append(_BREAKING)
        result.changes = [_BREAKING, _ADDITION]

        assert envelope.result.changes == [], (
            "the envelope's own result mutated when the caller's did"
        )
        assert envelope.document.to_mapping()["changes"] == [], (
            "the frozen document disagreed with the envelope's own result"
        )
        assert envelope.findings == (), (
            "findings disagreed with the envelope's own (unmutated) result"
        )

        rendered = self._render_all_from(self._FORMATS, envelope)
        for fmt in self._FORMATS:
            assert "_Z3foov" not in rendered[fmt], (
                f"{fmt!r} rendered a change appended to the caller's DiffResult "
                "after the envelope was already built"
            )

    def test_mutating_a_shared_change_s_verdict_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """The element-level sibling of the container-mutation test above.

        Snapshotting the containing lists is not enough on its own: ``Change``
        is itself an ordinary mutable dataclass (pattern-verdict modulation
        legitimately reassigns ``effective_verdict`` on one *during*
        ``compare()``), so the envelope must not share the caller's own
        ``Change`` objects either -- reported as a follow-up finding on the
        fix above, since copying only the list containers still left the
        mutable ``Change`` elements inside them shared by reference. This
        reproduces the exact scenario reported: escalate a ``COMPATIBLE``
        addition to ``BREAKING`` by reassigning ``effective_verdict`` after
        the envelope was already built, then confirm every projection still
        reports the pre-mutation, additive verdict.
        """
        addition = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result([addition])
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        assert envelope.result.changes[0] is not addition, (
            "the envelope shared the caller's own Change object by reference"
        )

        addition.effective_verdict = Verdict.BREAKING

        assert envelope.result.changes[0].effective_verdict is None, (
            "the envelope's own Change mutated when the caller's did"
        )
        assert envelope.findings[0].verdict != Verdict.BREAKING, (
            "findings disagreed with the envelope's own (unmutated) Change"
        )

        sarif = to_sarif(envelope.result, envelope=envelope)
        levels = {r["level"] for r in sarif["runs"][0]["results"]}
        assert "error" not in levels, (
            "SARIF escalated a change mutated on the caller's object after "
            "the envelope was already built"
        )

        # Markdown's own static verdict legend always mentions "BREAKING" (a
        # key explaining the marker, not a per-finding classification), so a
        # bare substring check would false-positive on it; check the line
        # naming the mutated symbol specifically instead.
        rendered = self._render_all_from(self._FORMATS, envelope)
        for fmt in self._FORMATS:
            symbol_lines = [
                line for line in rendered[fmt].splitlines() if "_Z3newv" in line
            ]
            assert symbol_lines, f"{fmt!r} lost the mutated change entirely"
            assert not any("BREAKING" in line for line in symbol_lines), (
                f"{fmt!r} reported a change mutated on the caller's own "
                "Change object after the envelope was already built"
            )

    def test_snapshot_change_decouples_a_nested_mutable_field(self) -> None:
        """Codex review, fresh evidence: a shallow per-field list copy still
        shares the *elements* of a nested container. ``impact_proof_path``
        is ``list[dict[str, object]]`` -- copying the outer list decouples
        appends/reassignment of the whole field, but mutating
        ``impact_proof_path[0]["label"]`` in place would still reach both
        the original and the "snapshot" through the same shared dict.
        ``_snapshot_change`` is a primitive with its own contract
        (independent of any one caller), so it gets its own direct test
        rather than only an envelope-level one.
        """
        original = Change(
            ChangeKind.FUNC_REMOVED,
            "_Z3foov",
            "removed",
            impact_proof_path=[{"label": "original"}],
        )
        snapshot = _snapshot_change(original)
        assert snapshot.impact_proof_path is not None
        assert snapshot.impact_proof_path[0] is not original.impact_proof_path[0]

        original.impact_proof_path[0]["label"] = "mutated"

        assert snapshot.impact_proof_path[0]["label"] == "original"

    def test_mutating_a_shared_dict_attribute_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """The dict-attribute sibling of the two mutation tests above.

        ``DiffResult.comparability_assurance`` (and ``evidence_metrics``) are
        mutable ``dict``s read straight off ``envelope.result`` by Markdown's/
        HTML's confidence sections (CodeRabbit review). Reassigning or
        mutating one after the envelope was already built must not reach
        those sections either.
        """
        assurance = {"abi": "high"}
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[],
            policy="strict_abi",
            comparability_assurance=assurance,
        )
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        assert envelope.result.comparability_assurance is not assurance, (
            "the envelope shared the caller's own comparability_assurance dict"
        )

        assurance["abi"] = "low"
        assurance["injected_dimension"] = "unexpected"

        assert envelope.result.comparability_assurance == {"abi": "high"}, (
            "the envelope's own dict mutated when the caller's did"
        )

        rendered = self._render_all_from(self._FORMATS, envelope)
        for fmt in self._FORMATS:
            assert "injected_dimension" not in rendered[fmt], (
                f"{fmt!r} reported a key injected into the caller's own dict "
                "after the envelope was already built"
            )

    def test_review_digest_merge_effect_reuses_the_envelope_s_gate(self) -> None:
        """CodeRabbit review: the review digest's merge-effect phrase called
        ``compute_exit_code`` independently even when an envelope had already
        resolved a ``GateDecision`` -- a second, redundant severity
        evaluation that happened to agree, not a projection of the one the
        envelope already made. A ``FUNC_ADDED`` configured ``severity.
        addition: error`` is ``COMPATIBLE`` but gate-blocking: the digest's
        phrase must say so, and must do it by reading ``envelope.gate``, not
        by re-deriving one.
        """
        addition = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result([addition])
        old, new = _snapshot("1.0"), _snapshot("2.0")
        severity_config = SeverityConfig(addition=SeverityLevel.ERROR)
        envelope = build_report_envelope(
            result, old, new, severity_config=severity_config
        )
        assert envelope.gate is not None
        assert envelope.gate.blocking

        # `_severity_merge_effect` does `from .severity import
        # compute_exit_code` as a function-local (call-time) import, so the
        # name it resolves is `abicheck.severity`'s own re-export -- not
        # `abicheck.policy.severity`'s origin function, which `abicheck.
        # severity` already copied a static reference to at its own import
        # time (patching the origin wouldn't touch that copy).
        with mock.patch(
            "abicheck.severity.compute_exit_code"
        ) as compute_exit_code_spy:
            digest = render_envelope("review", envelope)

        compute_exit_code_spy.assert_not_called()
        assert "blocked by severity policy" in digest

    def test_sarif_level_reuses_the_envelope_s_finding_not_a_live_policy_file(
        self,
    ) -> None:
        """Codex review, fresh evidence: SARIF's own ``_severity`` read
        ``result.policy_file``/``result.policy`` directly for every result --
        live, shared state a caller could still mutate after the envelope
        was already built, even though ``build.py``'s snapshot decouples
        every list/tuple/dict attribute and every ``Change`` object.
        Mutating ``PolicyFile.overrides`` after construction (escalating a
        ``FUNC_ADDED`` to ``BREAKING``) must not change SARIF's ``level``
        for a finding the envelope already resolved as ``COMPATIBLE``.
        """
        addition = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        policy_file = PolicyFile()
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[addition],
            policy="strict_abi",
            policy_file=policy_file,
        )
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)
        assert envelope.findings[0].verdict == Verdict.COMPATIBLE

        policy_file.overrides[ChangeKind.FUNC_ADDED] = Verdict.BREAKING

        sarif = to_sarif(envelope.result, envelope=envelope)
        levels = {r["level"] for r in sarif["runs"][0]["results"]}
        assert "error" not in levels, (
            "SARIF re-derived a finding's level from a PolicyFile mutated "
            "after the envelope was already built"
        )

    def test_snapshotting_changes_does_not_break_the_disposition_ledger(self) -> None:
        """Codex review, fresh evidence, P1: ``DispositionLedger`` keys every
        lookup on ``id(change)`` against the objects ``checker.compare()``
        recorded it with. Replacing every ``Change`` with an independent
        copy (this envelope's whole point) would otherwise desynchronize
        ``with_gate``'s own ``id(change)`` membership test from the
        snapshot's new objects, reading a real, blocking, gating finding as
        ``effective_total: 0`` -- exactly the D3 conservation invariant
        (``counts()`` sums to ``detected_total``) this ledger exists to
        guarantee.
        """
        breaking = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        result = _result([breaking])
        ledger = finalize_ledger(DispositionLedger(), result)
        result.disposition_ledger = ledger  # type: ignore[attr-defined]
        old, new = _snapshot("1.0"), _snapshot("2.0")
        severity_config = SeverityConfig()

        pre_audit = compute_disposition_audit(result, severity_config)
        assert pre_audit.effective_total == 1

        envelope = build_report_envelope(
            result, old, new, severity_config=severity_config
        )

        assert envelope.result.disposition_ledger is not ledger
        post_audit = compute_disposition_audit(envelope.result, severity_config)
        assert post_audit.effective_total == pre_audit.effective_total == 1
        assert dict(post_audit.counts)["gating"] == 1
        assert sum(dict(post_audit.counts).values()) == post_audit.detected_total

    def test_mutating_a_shared_policy_file_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """Codex review, fresh evidence: ``PolicyFile`` is a custom mutable
        object, not a list/tuple/dict the container-copy loop catches, so it
        stayed shared with the caller even after every other fix in this
        class. HTML's own ``compatibility_metrics`` call classifies straight
        from ``envelope.result.policy_file`` (its own independent decision,
        distinct from the per-finding verdict SARIF/JSON/Markdown already
        read off the envelope) -- reassigning an override after construction
        must not move the rendered binary-compatibility percentage.
        """
        addition = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        policy_file = PolicyFile()
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[addition],
            policy="strict_abi",
            policy_file=policy_file,
        )
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        assert envelope.result.policy_file is not policy_file, (
            "the envelope shared the caller's own PolicyFile object"
        )

        html_before = render_envelope("html", envelope)
        policy_file.overrides[ChangeKind.FUNC_ADDED] = Verdict.BREAKING
        html_after = render_envelope("html", envelope)

        assert html_before == html_after, (
            "HTML's binary-compatibility percentage moved when a PolicyFile "
            "mutated after the envelope was already built"
        )

    def test_mutating_a_shared_library_metadata_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """Codex review, fresh evidence: ``old_metadata``/``new_metadata``
        (``LibraryMetadata``) are custom mutable objects too, read directly
        by SARIF's own artifact-hash block and HTML's file-metadata section
        -- reassigning ``old_metadata.path`` after construction must not
        reach either.
        """
        old_metadata = LibraryMetadata(
            path="/old/libfoo.so", sha256="a" * 64, size_bytes=100
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[],
            policy="strict_abi",
            old_metadata=old_metadata,
        )
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        assert envelope.result.old_metadata is not old_metadata, (
            "the envelope shared the caller's own LibraryMetadata object"
        )

        sarif_before = render_envelope("sarif", envelope)
        old_metadata.path = "/mutated/path.so"
        sarif_after = render_envelope("sarif", envelope)

        assert sarif_before == sarif_after, (
            "SARIF's artifact path moved when LibraryMetadata mutated after "
            "the envelope was already built"
        )

    def test_mutating_the_caller_s_snapshots_after_construction_cannot_reach_the_envelope(
        self,
    ) -> None:
        """Codex review, fresh evidence: ``envelope.old``/``envelope.new``
        were the caller's own, still-live ``AbiSnapshot`` operands.
        ``build_report_document`` never reads them (JSON's version strings
        come from ``result.old_version``/``new_version``, already immune),
        but HTML's own version fields are read straight from ``envelope.
        old``/``envelope.new`` -- reassigning ``old.version`` after
        construction must not reach it.
        """
        result = _result([])
        old, new = _snapshot("1.0"), _snapshot("2.0")
        envelope = self._envelope(result, old, new)

        assert envelope.old is not old and envelope.new is not new, (
            "the envelope shared the caller's own AbiSnapshot objects"
        )

        old.version = "9.9.9-mutated"
        new.version = "9.9.9-mutated"

        assert envelope.old.version == "1.0"
        assert envelope.new is not None and envelope.new.version == "2.0"

        rendered = self._render_all_from(self._FORMATS, envelope)
        for fmt in self._FORMATS:
            assert "9.9.9-mutated" not in rendered[fmt], (
                f"{fmt!r} reported a version mutated on the caller's own "
                "AbiSnapshot after the envelope was already built"
            )


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
