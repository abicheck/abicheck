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

"""Codex review, fresh evidence (PR #1154 follow-up: "Move release
filtering out of the Markdown renderer"): ``report/AGENTS.md``'s renderer
contract forbids a renderer from filtering findings itself -- it may only
format an already-computed, immutable projection. An earlier revision of
``_release_md_bundle_findings``/``_release_md_matrix_findings`` violated
this by calling ``reporter_markdown.release_bundle_findings_for_view``/
``release_matrix_changes_for_view`` (a ``--view show=`` filter) directly;
the caller (``cli_compare_release_helpers.py``) now computes the filtered
view once and passes it in as a plain parameter.

This module pins both halves of the fix: a structural check that the
renderer module no longer references either filter function at all, and a
behavioral check that each renderer function renders *exactly* the
findings/changes it is handed -- never more (it doesn't fall back to the
result object's own full, unfiltered set) and never less (it doesn't
re-filter what it's given).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.bundle_models import BundleDiffResult, BundleFinding
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.report.render_release_markdown import (
    _release_md_bundle_findings,
    _release_md_matrix_findings,
)


class TestRendererDoesNotFilter:
    def test_module_does_not_call_the_filter_functions(self) -> None:
        import abicheck.report.render_release_markdown as mod

        # A call, not merely a mention -- the module's own docstrings name
        # these functions to explain *why* they're no longer called here,
        # so the check looks for an actual invocation (an open paren right
        # after the name), not a bare substring.
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "release_bundle_findings_for_view(" not in source
        assert "release_matrix_changes_for_view(" not in source
        assert not hasattr(mod, "release_bundle_findings_for_view")
        assert not hasattr(mod, "release_matrix_changes_for_view")

    def test_bundle_renderer_renders_exactly_the_given_findings(self) -> None:
        finding_a = BundleFinding(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="a",
            description="a removed",
            consumer_library="libapp.so",
            provider_library="libfoo.so",
        )
        finding_b = BundleFinding(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="b",
            description="b removed",
            consumer_library="libapp.so",
            provider_library="libbar.so",
        )
        bundle_result = BundleDiffResult(
            old_root=Path("old"),
            new_root=Path("new"),
            per_library=[],
            bundle_findings=[finding_a, finding_b],
        )

        # A narrower view than bundle_result's own full finding set --
        # proves the renderer uses exactly what it's handed, not
        # bundle_result.bundle_findings itself.
        lines = _release_md_bundle_findings(bundle_result, [finding_a])
        text = "\n".join(lines)
        assert "a removed" in text
        assert "b removed" not in text

        # The empty view: renders no findings section at all, even though
        # bundle_result itself has two.
        lines_empty = _release_md_bundle_findings(bundle_result, [])
        assert "Bundle (Cross-Library) Findings" not in "\n".join(lines_empty)

    def test_matrix_renderer_renders_exactly_the_given_changes(self) -> None:
        change_a = Change(kind=ChangeKind.FUNC_REMOVED, symbol="a", description="a gone")
        change_b = Change(kind=ChangeKind.FUNC_REMOVED, symbol="b", description="b gone")
        matrix_result = DiffResult(
            old_version="1",
            new_version="2",
            library="x",
            changes=[change_a, change_b],
        )

        lines = _release_md_matrix_findings(matrix_result, [change_a])
        text = "\n".join(lines)
        assert "a gone" in text
        assert "b gone" not in text

        lines_empty = _release_md_matrix_findings(matrix_result, [])
        assert "Build-Configuration (Matrix) Findings" not in "\n".join(lines_empty)
