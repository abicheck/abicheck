# Copyright 2026 Nikolay Petrov
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

"""Tests for ``--report-mode root-cause`` on ``--format junit`` (G29 Phase 3,
ADR-052 follow-up). Split out of ``test_junit_report.py`` (already at the
AI-readiness soft cap) rather than appended there.

JUnit's ``<testcase>`` model groups by *symbol*, not by finding — root-cause
mode does not restructure that (would break every existing JUnit consumer's
grouping assumptions). Instead each ``<failure>`` element gains additive
``rootCauseId``/``rootCause`` attributes, mirroring how SARIF's own
root-cause mode adds ``properties.rootCauseId`` rather than restructuring
its flat one-result-per-finding shape.
"""

from __future__ import annotations

from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.junit_report import to_junit_xml, to_junit_xml_multi
from abicheck.model import AbiSnapshot


def _make_result(
    changes: list[Change],
    verdict: Verdict = Verdict.BREAKING,
    library: str = "libfoo.so.1",
    old: str = "1.0",
    new: str = "2.0",
) -> DiffResult:
    return DiffResult(
        old_version=old,
        new_version=new,
        library=library,
        changes=changes,
        verdict=verdict,
    )


def _parse(xml_str: str):  # noqa: ANN201
    return xml_fromstring(xml_str)


class TestJunitRootCauseMode:
    def test_full_mode_never_sets_root_cause_attrs(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        xml = to_junit_xml(_make_result([c]), report_mode="full")
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is None
        assert fail.get("rootCause") is None

    def test_root_cause_mode_sets_attrs_on_failure(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        xml = to_junit_xml(_make_result([c]), report_mode="root-cause")
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is not None
        assert fail.get("rootCause") == "ns::detail::Impl"

    def test_shared_caused_by_type_gets_the_same_root_cause_id(self) -> None:
        removed = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::Impl::helper",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        leak = Change(
            kind=ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
            symbol="ns::detail::Impl",
            description="leaked",
            caused_by_type="ns::detail::Impl",
        )
        xml = to_junit_xml(_make_result([removed, leak]), report_mode="root-cause")
        fails = _parse(xml).findall(".//failure")
        ids = {f.get("rootCauseId") for f in fails}
        assert len(ids) == 1
        assert None not in ids

    def test_unrelated_findings_get_different_root_cause_ids(self) -> None:
        a = Change(kind=ChangeKind.FUNC_REMOVED, symbol="a", description="a removed")
        b = Change(kind=ChangeKind.FUNC_REMOVED, symbol="b", description="b removed")
        xml = to_junit_xml(_make_result([a, b]), report_mode="root-cause")
        fails = _parse(xml).findall(".//failure")
        ids = {f.get("rootCauseId") for f in fails}
        assert len(ids) == 2

    def test_extra_failure_on_same_symbol_gets_its_own_root_cause(self) -> None:
        # Two changes on the same symbol -> one <testcase>, two <failure>
        # children (the "disagree on root cause" case ADR-052 raised) -- each
        # failure carries its own root cause independently, no merging.
        first = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="shared",
            description="first",
            caused_by_type="cause_a",
        )
        second = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="shared",
            description="second",
            caused_by_type="cause_b",
        )
        xml = to_junit_xml(_make_result([first, second]), report_mode="root-cause")
        root = _parse(xml)
        tc = root.find(".//testcase[@name='shared']")
        assert tc is not None
        fails = tc.findall("failure")
        assert len(fails) == 2
        displays = {f.get("rootCause") for f in fails}
        assert displays == {"cause_a", "cause_b"}

    def test_missing_contract_label_gets_root_cause_attrs(self) -> None:
        result = _make_result([])
        result.scoped_missing_labels = ("missing_symbol_v1",)
        result.gate_scope = "used_by"
        xml = to_junit_xml(result, report_mode="root-cause")
        tc = _parse(xml).find(".//testcase[@name='missing_symbol_v1']")
        assert tc is not None
        fail = tc.find("failure")
        assert fail is not None
        assert fail.get("rootCauseId") is not None
        assert fail.get("rootCause") == "missing_symbol_v1"

    def test_other_report_modes_behave_like_full(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        xml = to_junit_xml(_make_result([c]), report_mode="leaf")
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is None

    def test_to_junit_xml_multi_forwards_report_mode(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
        xml = to_junit_xml_multi(
            [(_make_result([c]), snap)], report_mode="root-cause"
        )
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is not None


class TestRenderOutputForwardsReportModeToJunit:
    """The actual regression this slice fixes: service_render.render_output's
    'junit' branch used to drop report_mode entirely, so `--format junit
    --report-mode root-cause` silently rendered as plain `full` with no
    error — see ADR-052's "still open" note."""

    def test_render_output_junit_root_cause_sets_attrs(self) -> None:
        from abicheck.service import render_output

        old = AbiSnapshot(library="libfoo.so.1", version="1.0")
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        result = _make_result([c])
        xml = render_output("junit", result, old, report_mode="root-cause")
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is not None

    def test_render_output_junit_full_mode_unaffected(self) -> None:
        from abicheck.service import render_output

        old = AbiSnapshot(library="libfoo.so.1", version="1.0")
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="removed",
            caused_by_type="ns::detail::Impl",
        )
        result = _make_result([c])
        xml = render_output("junit", result, old, report_mode="full")
        fail = _parse(xml).find(".//failure")
        assert fail is not None
        assert fail.get("rootCauseId") is None
