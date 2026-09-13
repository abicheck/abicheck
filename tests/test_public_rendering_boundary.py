# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""A retired report mode is refused at *every* public rendering boundary.

Sibling of `test_view_internal_grammar.py`, which owns the `--view` grammar
a user types. This file owns the Python-side contract underneath it: there
are five separately documented ways into the same documents
(`reporter.to_json`, `report.to_markdown`, `sarif.to_sarif`,
`junit_report.to_junit_xml`, and `service_render.render_envelope`, whose
mode arrives on the envelope rather than as an argument), and a retirement
enforced at some of them is not a retirement.

Separated because that is exactly how this kept going wrong: plan slice 7o
enforced it at one entry point, then three, then four, and each round a
reviewer found another way in. A file whose whole subject is "all of them"
makes the next addition obvious.
"""

from __future__ import annotations

import pytest


class TestEveryPublicRendererRejectsARetiredMode:
    """The retirement is enforced at *every* public rendering entry point,
    not at the ones somebody remembered.

    Centralizing the check (`report/report_modes.py`) was the fix; it did
    not by itself route `sarif.to_sarif` or `junit_report.to_junit_xml`
    through it, and both docstrings still promised that `leaf` "renders as
    full" (Codex review, PR #1284). Stated as a sweep over the entry points
    rather than one call each, so a renderer added later is covered by
    adding its name here and nothing else.
    """

    def _result(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult

        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )

    def _entry_points(self):
        from abicheck.junit_report import to_junit_xml
        from abicheck.report.dispatch_markdown import to_markdown
        from abicheck.reporter import to_json
        from abicheck.sarif import to_sarif

        return {
            "to_json": to_json,
            "to_markdown": to_markdown,
            "to_sarif": to_sarif,
            "to_junit_xml": to_junit_xml,
        }

    @pytest.mark.parametrize("mode", ["leaf", "not-a-mode", ""])
    def test_each_rejects_a_retired_or_unknown_mode(self, mode):
        from abicheck.errors import ValidationError

        result = self._result()
        for name, fn in self._entry_points().items():
            with pytest.raises(ValidationError):
                fn(result, report_mode=mode)
            assert name  # names the failing entry point in the traceback

    @pytest.mark.parametrize("mode", ["full", "impact", "root-cause"])
    def test_each_still_accepts_every_supported_mode(self, mode):
        """The other half: rejection must not have narrowed what works."""
        result = self._result()
        for name, fn in self._entry_points().items():
            assert fn(result, report_mode=mode) is not None, name

    def test_render_envelope_checks_the_envelope_s_own_mode(self):
        """`render_envelope` is a fifth public entry point, and the mode it
        renders lives on the envelope rather than in an argument.

        Nothing validated it: `build_report_envelope` and a directly
        constructed `ReportEnvelope` both accept any string, and HTML
        ignores the field entirely, so a retired mode rendered a page
        (CodeRabbit review, PR #1284)."""
        from abicheck.errors import ValidationError
        from abicheck.model import AbiSnapshot
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope

        result = self._result()
        old, new = (
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
        )
        for mode in ("leaf", "not-a-mode"):
            envelope = build_report_envelope(
                result, old, new, options=RenderOptions(report_mode=mode)
            )
            for fmt in ("html", "json", "markdown"):
                with pytest.raises(ValidationError):
                    render_envelope(fmt, envelope)

    def test_render_envelope_still_renders_every_supported_mode(self):
        from abicheck.model import AbiSnapshot
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope

        result = self._result()
        old, new = (
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
        )
        for mode in ("full", "impact", "root-cause"):
            envelope = build_report_envelope(
                result, old, new, options=RenderOptions(report_mode=mode)
            )
            assert render_envelope("json", envelope), mode

    def test_the_retired_message_names_the_replacement(self):
        from abicheck.errors import ValidationError
        from abicheck.reporter import to_json

        with pytest.raises(ValidationError, match="root-cause"):
            to_json(self._result(), report_mode="leaf")
