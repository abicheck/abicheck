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
from abicheck.report.pr_comment_members import MAX_FOLDED_TARGETS
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


class TestTheFanInIsBoundedAgainstAHostileDocument:
    """Review finding: the per-member *size* cap bounded one read, and
    nothing bounded how many reads a document could ask for.

    The two are independent. A document repeating one permitted 32 MiB
    member path ten thousand times stays small itself while asking a
    privileged publisher for 320 GiB of parsing -- inside every limit the
    artifact extractor enforces, because the extractor never sees this
    amplification. Bounded two ways: distinct paths are read once, and the
    target count is capped.
    """

    @staticmethod
    def _member(symbol: str) -> str:
        return json.dumps(
            {
                "library": "libthing.so",
                "old_version": "1.0",
                "new_version": "1.1",
                "verdict": "BREAKING",
                "changes": [
                    {
                        "kind": "func_removed",
                        "symbol": symbol,
                        "severity": "breaking",
                        "description": "Function removed",
                    }
                ],
            }
        )

    @classmethod
    def _document(cls, tmp_path: Path, targets: int, *, distinct: bool) -> Path:
        blocks = []
        for i in range(targets):
            name = f"m{i}.json" if distinct else "m.json"
            # Distinct members carry distinct *content*, so a cache keyed on
            # anything but the path is detectable. Keyed on the target index
            # only when the paths differ, so the repeated-path case still
            # writes one identical file.
            symbol = f"thing_open_{i}" if distinct else "thing_open"
            (tmp_path / name).write_text(cls._member(symbol), encoding="utf-8")
            blocks.append(
                {
                    "target_id": f"t{i}",
                    "required": True,
                    "state": "analyzed",
                    "compatibility_verdict": "BREAKING",
                    "gate": {
                        "exit_code": 4,
                        "blocking": True,
                        "blocking_categories": ["abi_breaking"],
                        "from_report": True,
                    },
                    "contract_coverage_exit": 0,
                    "analysis_assurance_exit": 0,
                    "scope_completeness_exit": 0,
                    "report_path": name,
                }
            )
        document = {
            "aggregate_schema_version": "1.4",
            "status": "fail",
            "compatibility": {"verdict": "BREAKING", "analyzed_targets": targets},
            "coverage": {
                "status": "complete",
                "required_targets": targets,
                "analyzed_required_targets": targets,
                "missing_required_targets": [],
                "blocking": False,
            },
            "gate": {
                "passed": False,
                "exit_code": 4,
                "blocking_targets": [f"t{i}" for i in range(targets)],
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
            "targets": blocks,
            "unexpected_targets": [],
            "profile_matrix": [],
            "finding_matrix": [],
        }
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_a_repeated_member_path_is_read_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Observe the mechanism, not the output: an equal rendering proves
        nothing about how many times the file was opened."""
        path = self._document(tmp_path, 200, distinct=False)
        reads: list[str] = []
        real = Path.read_text

        def counting(self: Path, *a: object, **k: object) -> str:
            if self.name == "m.json":
                reads.append(str(self))
            return real(self, *a, **k)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", counting)
        model = build_model(
            json.loads(path.read_text(encoding="utf-8")), report_dir=path.parent
        )
        assert len(reads) == 1, f"the same member was read {len(reads)} times"
        # Every target still gets its own row: sharing the read must not
        # collapse two targets into one result.
        assert len(model.library_rows) == 200

    def test_distinct_members_are_each_read(self, tmp_path: Path) -> None:
        """Positive control for the cache key, stated over *content*.

        Counting rows is not enough and a mutation proved it: a cache keyed
        on a constant -- serving member 0's model for every path -- passed a
        row-count assertion in full. Each member here names a different
        symbol, so serving the wrong one is visible.
        """
        path = self._document(tmp_path, 5, distinct=True)
        model = build_model(
            json.loads(path.read_text(encoding="utf-8")), report_dir=path.parent
        )
        assert len(model.library_rows) == 5
        body = render_comment(model, sha="0123456789ab", detail="full")
        for i in range(5):
            assert f"thing_open_{i}" in body, (
                f"member {i}'s own finding is missing; the cache served "
                "another member's model for this path"
            )

    def test_a_document_past_the_target_cap_says_so(self, tmp_path: Path) -> None:
        path = self._document(tmp_path, MAX_FOLDED_TARGETS + 5, distinct=False)
        model = build_model(
            json.loads(path.read_text(encoding="utf-8")), report_dir=path.parent
        )
        assert len(model.library_rows) == MAX_FOLDED_TARGETS
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "only the first" in body
        assert str(MAX_FOLDED_TARGETS + 5) in body

    def test_a_refusal_is_cached_as_itself_but_relabelled(self, tmp_path: Path) -> None:
        """A bad path named by many targets costs one attempt -- and each
        target's limitation still names *that* target, not the first one."""
        path = self._document(tmp_path, 3, distinct=False)
        (tmp_path / "m.json").unlink()
        model = build_model(
            json.loads(path.read_text(encoding="utf-8")), report_dir=path.parent
        )
        body = render_comment(model, sha="0123456789ab", detail="full")
        for i in range(3):
            assert f"t{i}" in body


class TestAggregatePreservesMemberReviewGroups:
    """A fan-in keeps what its members carry.

    `_Fold` accumulated ten member outputs and silently dropped two of them
    — `review_groups` and `result_counts` — so an aggregate comment rendered
    no review-group section at all although every member had one, and the
    per-target rows state counts rather than the group names and transitions
    the section exists to show (CodeRabbit review).
    """

    @staticmethod
    def _member_with_groups(target: str) -> dict[str, object]:
        report = dict(_member_report("break"))
        report["review_groups"] = [
            {
                "display_name": "Widget::resize",
                "transition": "signature changed",
                "gating_findings": ["func_removed"],
                "member_finding_ids": [f"{target}-1"],
            }
        ]
        report["result_counts"] = {
            "review_groups": 1,
            "gating_review_groups": 1,
            "raw_detected": 1,
            "retained": 1,
        }
        return report

    def _document(self, tmp_path: Path, targets: list[str]) -> dict[str, object]:
        for target in targets:
            (tmp_path / f"{target}.json").write_text(
                json.dumps(self._member_with_groups(target)), encoding="utf-8"
            )
        return {
            "aggregate_schema_version": "1.4",
            "head_sha": "0123456789ab",
            "targets": [
                {
                    "target_id": target,
                    "state": "analyzed",
                    "compatibility_verdict": "BREAKING",
                    "report_path": f"{target}.json",
                }
                for target in targets
            ],
        }

    def test_every_member_group_survives_the_fold_attributed_to_its_target(
        self, tmp_path: Path
    ) -> None:
        """Three targets reporting one symbol are three groups, not one.

        The same attribution rule folded findings already follow — which is
        the cross-platform matrix case `aggregate` exists for.
        """
        targets = ["alpha", "beta", "gamma"]
        document = self._document(tmp_path, targets)
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        model = build_model(document, report_dir=path.parent)

        assert len(model.review_groups) == len(targets), (
            f"expected one group per target, got {model.review_groups}"
        )
        scopes = {str(g.get("library", "")) for g in model.review_groups}
        assert scopes == set(targets), (
            f"groups are not attributed to their targets: {scopes}"
        )

    def test_member_result_counts_are_summed_not_recomputed(
        self, tmp_path: Path
    ) -> None:
        """Each member's counts are authoritative for that member.

        Recomputing from the folded findings would undercount, since those
        are capped per member.
        """
        targets = ["alpha", "beta", "gamma"]
        document = self._document(tmp_path, targets)
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        model = build_model(document, report_dir=path.parent)

        assert model.result_counts is not None
        assert model.result_counts["review_groups"] == len(targets)
        assert model.result_counts["gating_review_groups"] == len(targets)
        assert model.result_counts["raw_detected"] == len(targets)

    def test_the_rendered_aggregate_comment_shows_the_groups(
        self, tmp_path: Path
    ) -> None:
        """The property a reviewer actually sees, not just the model field."""
        targets = ["alpha", "beta"]
        document = self._document(tmp_path, targets)
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        model = build_model(document, report_dir=path.parent)
        body = render_comment(model, sha="0123456789ab", detail="full")

        assert "Review groups:" in body
        assert "Widget::resize" in body
        for target in targets:
            assert f"{target}: **Widget::resize**" in body

    def test_a_member_without_groups_contributes_none(self, tmp_path: Path) -> None:
        """Negative control: the section must not appear from nowhere."""
        (tmp_path / "alpha.json").write_text(
            json.dumps(_member_report("break")), encoding="utf-8"
        )
        document = {
            "aggregate_schema_version": "1.4",
            "head_sha": "0123456789ab",
            "targets": [
                {
                    "target_id": "alpha",
                    "state": "analyzed",
                    "compatibility_verdict": "BREAKING",
                    "report_path": "alpha.json",
                }
            ],
        }
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        model = build_model(document, report_dir=path.parent)
        assert model.review_groups == []
        assert "Review groups:" not in render_comment(
            model, sha="0123456789ab", detail="full"
        )
