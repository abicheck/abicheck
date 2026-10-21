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

"""Analysis-assurance branches the pack-accounting and CLI suites left open:
manifest-recorded partial extractors, root targets requested or resolved on
both sides, and the public output projections' exit-floor wording.

Every oracle here is a literal taken from the documented contract, not the
implementation's own helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import checker
from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
from abicheck.buildsource.model import BuildSourceManifest, ExtractorRecord
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.policy.analysis_assurance import (
    analysis_assurance_report_dict,
    assurance_floor_diagnostic,
    compute_analysis_assurance,
)
from tests._analysis_assurance_support import header_pair


def _extractor_pack(tmp_path: Path, name: str, *records: ExtractorRecord):
    return BuildSourcePack(
        root=tmp_path / name,
        manifest=BuildSourceManifest(extractors=list(records)),
    )


def _target_pack(tmp_path: Path, name: str, scope: TargetScope) -> BuildSourcePack:
    return BuildSourcePack(
        root=tmp_path / name,
        build_evidence=BuildEvidence(target_scope=scope),
    )


class TestManifestPartialExtractorRecord:
    """A ``partial`` extractor row from a source-side family (its
    ``source_side`` flag is set from the name prefix) makes the run
    incomplete and is quoted verbatim, naming the side it came from. A
    ``partial`` row from any other family stays out of this check."""

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_partial_source_side_extractor_is_incomplete_on_either_side(
        self, tmp_path: Path, side: str
    ) -> None:
        old, new = header_pair()
        partial = _extractor_pack(
            tmp_path,
            "partial",
            ExtractorRecord(
                name="compile_commands", status="partial", detail="some TUs skipped"
            ),
        )
        packs = {"old_pack": None, "new_pack": None}
        packs[f"{side}_pack"] = partial

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(result, old, new, **packs)

        assert aa.status == "partial"
        assert (
            f"{side} side's 'compile_commands' extractor status is 'partial': "
            "some TUs skipped"
        ) in aa.notes

    def test_ok_source_side_extractor_leaves_the_run_complete(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        ok = _extractor_pack(
            tmp_path, "ok", ExtractorRecord(name="compile_commands", status="ok")
        )

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(result, old, new, old_pack=ok, new_pack=None)

        assert aa.status == "complete"

    def test_partial_record_outside_the_source_side_families_is_not_flagged(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        build_query = _extractor_pack(
            tmp_path,
            "build_query",
            ExtractorRecord(name="build_query", status="partial", detail="slow"),
        )

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=build_query, new_pack=build_query
        )

        assert aa.status == "complete"
        assert all("build_query" not in note for note in aa.notes)


class TestRootTargetsRequestedOnBothSides:
    """A root target both sides request, or both sides resolve, is reported
    once, in first-seen order; the transitive counts add across sides."""

    def test_shared_root_target_is_reported_once_in_first_seen_order(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        old_pack = _target_pack(
            tmp_path,
            "old",
            TargetScope(
                requested=["//a:lib", "//b:lib"],
                resolved=["//a:lib", "//b:lib"],
                transitive_count=3,
            ),
        )
        new_pack = _target_pack(
            tmp_path,
            "new",
            TargetScope(
                requested=["//b:lib", "//c:lib"],
                resolved=["//b:lib", "//c:lib"],
                transitive_count=4,
            ),
        )

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=old_pack, new_pack=new_pack
        )

        assert aa.target_accounting.requested == ("//a:lib", "//b:lib", "//c:lib")
        assert aa.target_accounting.resolved == ("//a:lib", "//b:lib", "//c:lib")
        assert aa.target_accounting.transitive_count == 7


class TestReportDictProjection:
    """``analysis_assurance_report_dict`` projects only a real
    ``AnalysisAssurance`` block; anything else reads as absent."""

    @pytest.mark.parametrize(
        "absent", [None, {"status": "complete"}, "complete"], ids=repr
    )
    def test_non_block_value_projects_to_none(self, absent: object) -> None:
        old, new = header_pair()
        result = checker.compare(old, new)
        result.analysis_assurance = absent

        assert analysis_assurance_report_dict(result) is None

    def test_real_block_projects_to_its_dict(self) -> None:
        old, new = header_pair()
        result = checker.compare(old, new)

        assert analysis_assurance_report_dict(result)["status"] == "complete"


class TestFloorDiagnosticWording:
    """The notice names the exit contribution relative to the compatibility
    axis's own exit code: floored when it is below the floor, equal when it
    matches, and deferred to the compatibility exit when that is higher."""

    @pytest.mark.parametrize(
        ("base_exit", "effect"),
        [
            (0, "Exit code floored to 1"),
            (1, "Contributes 1 to an exit that was already 1"),
            (
                2,
                "Contributes 1, below the compatibility axis's own exit 2, which stands",
            ),
            (
                4,
                "Contributes 1, below the compatibility axis's own exit 4, which stands",
            ),
        ],
    )
    def test_wording_states_the_effect_on_the_exit_code(
        self, tmp_path: Path, base_exit: int, effect: str
    ) -> None:
        old, new = header_pair()
        new.build_source = _extractor_pack(
            tmp_path,
            "new",
            ExtractorRecord(
                name="compile_commands", status="partial", detail="some TUs skipped"
            ),
        )
        result = checker.compare(old, new)
        assert result.analysis_assurance.status == "partial"

        diag = assurance_floor_diagnostic(
            result, require_complete=True, base_exit=base_exit
        )

        assert diag is not None
        assert diag.startswith(
            "Analysis assurance incomplete (status='partial') "
            "under assurance.require_complete: "
        )
        assert "new side's 'compile_commands' extractor status is 'partial'" in diag
        assert f"{effect} (P0.4 analysis-assurance axis)." in diag
        assert diag.endswith("Use -o json=... for the full analysis_assurance block.")

    def test_flag_unset_yields_no_notice_even_when_incomplete(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        new.build_source = _extractor_pack(
            tmp_path,
            "new",
            ExtractorRecord(name="compile_commands", status="partial"),
        )
        result = checker.compare(old, new)
        assert result.analysis_assurance.status == "partial"

        assert (
            assurance_floor_diagnostic(result, require_complete=False, base_exit=2)
            is None
        )
