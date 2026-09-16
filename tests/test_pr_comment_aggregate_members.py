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

"""An aggregate document's member reports are untrusted input.

The companion to `test_pr_comment_aggregate.py`, split out at the
architecture gate's per-test-file ceiling along a real seam: that module
states what a *well-formed* fan-in document must render as, this one states
what happens when a member report is not one.

Every case here is a **negative control** — the refusal must both raise with
a stated reason at the primitive level and surface as a visible limitation
in the rendered comment. A refusal at only one of those two levels is the
silent drop this boundary exists to prevent: a dropped target is how a
fan-in comment learns to lie.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.pr_comment import build_model, render_comment, should_post
from abicheck.report.pr_comment_aggregate import (
    MemberReportRefused,
    load_member_report,
    resolve_member_path,
)
from tests._aggregate_documents import (
    CLEAN_HEADLINES,
    headline_of as _headline,
    member_report as _member_report,
)


class TestMemberReportRefusal:
    """Every way a member report can be unusable is refused explicitly.

    Each case is a *negative control*: the refusal must both raise with a
    stated reason at the primitive level and surface as a visible limitation
    in the rendered comment. A refusal that only happens at one of those two
    levels is the silent drop this boundary exists to prevent.
    """

    def _doc_naming(self, tmp_path: Path, raw_path: str) -> dict[str, object]:
        return {
            "aggregate_schema_version": "1.4",
            "status": "pass",
            "compatibility": {"verdict": "COMPATIBLE", "analyzed_targets": 1},
            "coverage": {
                "status": "complete",
                "required_targets": 1,
                "analyzed_required_targets": 1,
                "missing_required_targets": [],
                "blocking": False,
            },
            "gate": {
                "passed": True,
                "exit_code": 0,
                "blocking_targets": [],
                "coverage_blocking": False,
            },
            "contract_coverage": {"exit_contribution": 0, "incomplete_targets": []},
            "analysis_assurance": {"exit_contribution": 0, "incomplete_targets": []},
            "scope_completeness": {"exit_contribution": 0, "incomplete_targets": []},
            "disposition_audit_missing_targets": [],
            "effective_policy": {
                "missing_required": "fail",
                "unexpected_target": "include",
                "source": "default",
            },
            "targets": [
                {
                    "target_id": "alpha-target",
                    "required": True,
                    "state": "analyzed",
                    "compatibility_verdict": "COMPATIBLE",
                    "gate": {
                        "exit_code": 0,
                        "blocking": False,
                        "blocking_categories": [],
                        "from_report": True,
                    },
                    "contract_coverage_exit": 0,
                    "analysis_assurance_exit": 0,
                    "scope_completeness_exit": 0,
                    "report_path": raw_path,
                }
            ],
            "unexpected_targets": [],
            "profile_matrix": [],
            "finding_matrix": [],
        }

    @pytest.mark.parametrize(
        "raw",
        [
            "/etc/passwd",
            "/opt/elsewhere.json",
            "C:\\Windows\\win.ini",
            "C:/Windows/win.ini",
            "../outside.json",
            "nested/../../outside.json",
            "./../outside.json",
            "",
        ],
        ids=[
            "absolute-posix",
            "absolute-posix-other",
            "absolute-windows-backslash",
            "absolute-windows-slash",
            "parent-traversal",
            "nested-traversal",
            "dot-parent-traversal",
            "empty",
        ],
    )
    def test_path_shapes_outside_the_document_directory_are_refused(
        self, tmp_path: Path, raw: str
    ) -> None:
        with pytest.raises(MemberReportRefused):
            resolve_member_path(tmp_path, raw)

    def test_symlinked_leaf_is_refused_even_when_it_points_inside(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real.json"
        real.write_text("{}", encoding="utf-8")
        link = tmp_path / "link.json"
        link.symlink_to(real)
        with pytest.raises(MemberReportRefused, match="symlink"):
            resolve_member_path(tmp_path, "link.json")

    def test_symlinked_directory_component_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-tree"
        outside.mkdir(exist_ok=True)
        (outside / "member.json").write_text("{}", encoding="utf-8")
        (tmp_path / "reports").symlink_to(outside, target_is_directory=True)
        with pytest.raises(MemberReportRefused, match="symlink"):
            resolve_member_path(tmp_path, "reports/member.json")

    def test_directory_named_as_a_member_report_is_refused(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "member.json").mkdir()
        with pytest.raises(MemberReportRefused, match="regular file"):
            load_member_report(tmp_path, "member.json")

    def test_oversized_member_report_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "member.json").write_text("{}" + " " * 4096, encoding="utf-8")
        with pytest.raises(MemberReportRefused, match="limit"):
            load_member_report(tmp_path, "member.json", max_bytes=16)

    @pytest.mark.parametrize(
        "content", ["not json at all", "[1, 2, 3]", '"a bare string"', "", "{"]
    )
    def test_non_object_member_report_is_refused(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / "member.json").write_text(content, encoding="utf-8")
        with pytest.raises(MemberReportRefused):
            load_member_report(tmp_path, "member.json")

    def test_missing_member_report_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MemberReportRefused):
            load_member_report(tmp_path, "nowhere.json")

    @pytest.mark.parametrize(
        "raw", ["/etc/passwd", "../outside.json", "member.json", "link.json"]
    )
    def test_every_refusal_surfaces_as_a_limitation_not_a_drop(
        self, tmp_path: Path, raw: str
    ) -> None:
        """The end-to-end half of each negative control above: a refused
        member must reach the rendered body, naming the target, and must
        never leave the comment claiming a clean result."""
        if raw == "member.json":
            (tmp_path / "member.json").write_text("not json", encoding="utf-8")
        if raw == "link.json":
            real = tmp_path / "real.json"
            real.write_text("{}", encoding="utf-8")
            (tmp_path / "link.json").symlink_to(real)
        document = self._doc_naming(tmp_path, raw)
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        model = build_model(document, report_dir=path.parent)
        assert model.has_incomplete
        assert should_post(model, "changes")
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "alpha-target" in body
        assert not any(phrase in _headline(body) for phrase in CLEAN_HEADLINES)

    def test_without_a_known_directory_no_member_is_read(self, tmp_path: Path) -> None:
        """``report_dir=None`` must not silently resolve members against the
        process's working directory, which the document said nothing about."""
        (tmp_path / "member.json").write_text(
            json.dumps(_member_report("break")), encoding="utf-8"
        )
        document = self._doc_naming(tmp_path, "member.json")
        model = build_model(document, report_dir=None)
        assert model.has_incomplete
        assert not model.breaking

    def test_an_unrenderable_member_report_is_a_limitation_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """One bad member may not cost every other target's result."""
        (tmp_path / "member.json").write_text(
            json.dumps({"scan_schema_version": "1.0"}), encoding="utf-8"
        )
        document = self._doc_naming(tmp_path, "member.json")
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        model = build_model(document, report_dir=path.parent)
        assert model.has_incomplete
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "alpha-target" in body


class TestMemberPathEdgeCases:
    @pytest.mark.parametrize("raw", ["C:relative.json", "D:dir/x.json"])
    def test_a_drive_relative_windows_path_is_refused(
        self, tmp_path: Path, raw: str
    ) -> None:
        """Not absolute under either flavour, but still names a drive."""
        with pytest.raises(MemberReportRefused, match="drive"):
            resolve_member_path(tmp_path, raw)

    @pytest.mark.parametrize("raw", ["./", "."])
    def test_a_path_naming_no_file_is_refused(self, tmp_path: Path, raw: str) -> None:
        with pytest.raises(MemberReportRefused):
            resolve_member_path(tmp_path, raw)

    def test_an_undecodable_member_report_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "member.json").write_bytes(b"\xff\xfe not utf-8 at all")
        with pytest.raises(MemberReportRefused, match="could not be read"):
            load_member_report(tmp_path, "member.json")
