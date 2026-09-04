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

"""P1 review finding, split out of ``test_analysis_assurance.py`` (``_extra``
-style sibling, matching e.g. ``test_analysis_assurance_depth_and_graph_
overlap.py``) purely to stay under the AI-readiness file-size no-growth
debt baseline -- the parent file is already over its adoption baseline.

Finding: both sides can carry a fully-``parsed`` ``dwarf_advanced`` channel
with no basic ``dwarf`` channel at all (a valid shape for API-constructed or
persisted snapshots). Because that is SYMMETRIC on both sides,
``_dwarf_context_status`` alone reads ``"clean"`` (nothing to compare
between old/new) even though ``diff_platform.py``'s DWARF-based struct/enum
layout diff still requires the basic channel and silently skips its own
comparison whenever it is absent. Fixed by a dedicated receipt-level
``basic_unavailable`` check in ``compute_analysis_assurance`` -- the exact
symmetric complement of the pre-existing ``advanced_unavailable`` check.

Duplicates the two small fixtures it needs (``_fn``/``_compare``) rather
than importing them from the parent module -- every other ``_extra``-style
sibling test file in this suite is self-contained the same way.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.analysis_assurance import AnalysisAssurance
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _compare(tmp_path: Path, pair: tuple[AbiSnapshot, AbiSnapshot], *extra: str):
    old, new = pair
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return CliRunner().invoke(main, ["compare", str(old_p), str(new_p), *extra])


class TestSymmetricBasicChannelAbsence:
    def _basic_absent_advanced_parsed_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]

        def _side(version: str) -> AbiSnapshot:
            return AbiSnapshot(
                version=version,
                library="libfoo.so.1",
                functions=fns,
                dwarf=DwarfMetadata(has_dwarf=False, evidence_state="not_available"),
                dwarf_advanced=AdvancedDwarfMetadata(
                    has_dwarf=True, evidence_state="parsed"
                ),
            )

        return _side("1.0"), _side("2.0")

    def test_dwarf_context_status_alone_reads_clean(self) -> None:
        """The pure per-channel status stays "clean" -- proving this really
        is the gap ``dwarf_context_status`` structurally cannot see, not a
        bug in that helper."""
        from abicheck.analysis_assurance import _dwarf_context_status

        old, new = self._basic_absent_advanced_parsed_pair()
        status, notes = _dwarf_context_status(old, new)
        assert status == "clean"
        assert notes == []

    def test_overall_status_is_not_complete(self) -> None:
        old, new = self._basic_absent_advanced_parsed_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.dwarf_context_status == "clean"
        assert aa.status != "complete"
        assert any("basic DWARF channel" in n for n in aa.notes), aa.notes

    def test_require_complete_analysis_exits_nonzero(self, tmp_path: Path) -> None:
        old, new = self._basic_absent_advanced_parsed_pair()
        res = _compare(
            tmp_path,
            (old, new),
            "--no-scope-public-headers",
            "--require-complete-analysis",
        )
        assert res.exit_code != 0, res.output

    def test_reverse_shape_basic_parsed_advanced_missing_also_incomplete(
        self,
    ) -> None:
        """Sanity check that the pre-existing complement (basic parsed,
        advanced missing on both sides) is still caught too -- both
        directions of this symmetric gap must be incomplete."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]

        def _side(version: str) -> AbiSnapshot:
            return AbiSnapshot(
                version=version,
                library="libfoo.so.1",
                functions=fns,
                dwarf=DwarfMetadata(has_dwarf=True, evidence_state="parsed"),
                dwarf_advanced=AdvancedDwarfMetadata(
                    has_dwarf=False, evidence_state="not_available"
                ),
            )

        old, new = _side("1.0"), _side("2.0")
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status != "complete"
