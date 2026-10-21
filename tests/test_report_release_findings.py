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

"""Direct tests of the release finding projection in ``report.release_findings``.

The release fan-out tests exercise this projection end to end; these pin the
branches they do not reach on their own: the per-library cap and the kind
ledger of what it cut, the uncapped machine projection, a non-dict member
entry, and an impact table that comes back empty.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Change, DiffResult
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.classification import Verdict
from abicheck.report import release_findings
from abicheck.report.release_display_limits import MAX_RELEASE_FINDINGS_PER_LIBRARY
from abicheck.report.release_findings import (
    project_release_library_results,
    release_finding_dicts,
)


def _diff_with_removals(count: int) -> DiffResult:
    """A library whose only findings are *count* breaking function removals."""
    return DiffResult(
        library="libfoo.so",
        old_version="1",
        new_version="2",
        verdict=Verdict.BREAKING,
        changes=[
            Change(ChangeKind.FUNC_REMOVED, f"_Z{i}foov", f"removed f{i}")
            for i in range(count)
        ],
    )


class TestPerLibraryCap:
    def test_a_capped_projection_keeps_the_cap_and_ledgers_what_it_cut(self):
        extra = 2
        findings, cut_kinds = release_finding_dicts(
            _diff_with_removals(MAX_RELEASE_FINDINGS_PER_LIBRARY + extra)
        )
        assert len(findings) == MAX_RELEASE_FINDINGS_PER_LIBRARY
        # Every finding past the cap is ledgered under its own kind string.
        assert cut_kinds == ["func_removed"] * extra

    def test_the_uncapped_projection_keeps_everything_and_cuts_nothing(self):
        count = MAX_RELEASE_FINDINGS_PER_LIBRARY + 3
        findings, cut_kinds = release_finding_dicts(
            _diff_with_removals(count), uncapped=True
        )
        assert len(findings) == count
        assert cut_kinds == []

    def test_a_library_under_the_cap_is_neither_cut_nor_ledgered(self):
        findings, cut_kinds = release_finding_dicts(_diff_with_removals(2))
        assert len(findings) == 2
        assert cut_kinds == []


class TestProjectReleaseLibraryResults:
    def test_a_non_dict_member_entry_is_skipped_without_error(self):
        entries: list[dict[str, object]] = [
            "not-an-entry",  # type: ignore[list-item]
            {"library": "libfoo.so", "_diff_result": _diff_with_removals(1)},
        ]
        verdict = project_release_library_results(entries, [], "compatible")
        assert verdict == "compatible"
        assert entries[1]["library"] == "libfoo.so"
        assert "_diff_result" not in entries[1]
        assert entries[1]["findings"][0]["kind"] == "func_removed"

    def test_annotations_are_built_only_when_asked_for(self):
        without = [{"library": "libfoo.so", "_diff_result": _diff_with_removals(1)}]
        project_release_library_results(
            without, [], "compatible", needs_annotations=False
        )
        assert "annotations" not in without[0]

        with_ = [{"library": "libfoo.so", "_diff_result": _diff_with_removals(1)}]
        project_release_library_results(with_, [], "compatible", needs_annotations=True)
        assert with_[0]["annotations"]


class TestImpactTable:
    def test_an_impact_table_that_comes_back_empty_is_left_out_of_the_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            release_findings, "compute_impact_table", lambda _d, _c: None
        )
        entries = [{"library": "libfoo.so", "_diff_result": _diff_with_removals(1)}]
        project_release_library_results(
            entries, [], "compatible", needs_annotations=False, show_impact=True
        )
        assert "impact_table" not in entries[0]
