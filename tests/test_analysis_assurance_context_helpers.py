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

"""Direct unit tests for the per-axis status helpers behind the
``AnalysisAssurance`` rollup: the bounded-comparability notes
(``_comparability``) and the header / DWARF / L3 context statuses
(``_context``).

The end-to-end paths are covered by ``test_analysis_assurance_context_asymmetry``;
these pin each branch's status word and the side it names, against the
helper's own contract rather than through a full comparison."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.debug_evidence import DebugInfoEvidence
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, AdvancedExtractionStatus
from abicheck.policy.analysis_assurance._comparability import (
    bounded_comparability_notes,
    is_comparability_bounded,
    unverified_dimensions,
)
from abicheck.policy.analysis_assurance._context import (
    _dwarf_context_status,
    _header_context_status,
    _l3_context_status,
)


def _snap(*, from_headers: bool = False) -> AbiSnapshot:
    return AbiSnapshot(
        version="1.0",
        library="libfoo.so.1",
        functions=[],
        from_headers=from_headers,
    )


def _result(changes: list[Change] | None = None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so.1",
        changes=changes or [],
    )


class TestUnverifiedDimensions:
    def test_none_and_all_trusted_contribute_nothing(self) -> None:
        assert unverified_dimensions(None) == []
        assert unverified_dimensions({"layout": "trusted"}) == []
        assert not is_comparability_bounded(None)
        assert not is_comparability_bounded({"layout": "trusted"})

    def test_returns_sorted_unverified_dimension_names_only(self) -> None:
        mapping = {
            "layout": "unverified",
            "declaration": "unverified",
            "symbols": "trusted",
        }
        assert unverified_dimensions(mapping) == ["declaration", "layout"]
        assert is_comparability_bounded(mapping)


class TestBoundedComparabilityNotes:
    def test_no_unverified_dimension_yields_no_note(self) -> None:
        assert bounded_comparability_notes(None) == []
        assert bounded_comparability_notes({"symbols": "trusted"}) == []

    def test_unverified_dimensions_are_named_in_one_note(self) -> None:
        notes = bounded_comparability_notes(
            {"layout": "unverified", "declaration": "unverified"}
        )
        assert notes == [
            "extraction contexts were not provably identical; conclusions on "
            "the declaration, layout dimension(s) carry reduced assurance"
        ]


class TestHeaderContextStatus:
    def test_neither_side_from_headers_is_not_evaluated(self) -> None:
        status, notes = _header_context_status(_result(), _snap(), _snap())
        assert status == "not_evaluated"
        assert notes == []

    @pytest.mark.parametrize(
        ("old_headers", "new_headers", "missing"),
        [(True, False, "new"), (False, True, "old")],
    )
    def test_one_sided_header_evidence_names_the_missing_side(
        self, old_headers: bool, new_headers: bool, missing: str
    ) -> None:
        status, notes = _header_context_status(
            _result(),
            _snap(from_headers=old_headers),
            _snap(from_headers=new_headers),
        )
        assert status == "asymmetric"
        assert len(notes) == 1
        assert f"the {missing} side carries no header/API-level evidence" in notes[0]

    def test_both_sides_from_headers_without_drift_is_clean(self) -> None:
        status, notes = _header_context_status(
            _result(), _snap(from_headers=True), _snap(from_headers=True)
        )
        assert status == "clean"
        assert notes == []

    def test_header_parse_context_drift_is_detected_in_any_change_list(self) -> None:
        drift = Change(
            kind=ChangeKind.HEADER_PARSE_CONTEXT_DRIFT,
            symbol="foo.h",
            description="parsed under a different build context",
        )
        result = _result()
        result.out_of_surface_changes = [drift]
        status, notes = _header_context_status(
            result, _snap(from_headers=True), _snap(from_headers=True)
        )
        assert status == "drift_detected"
        assert notes and "header_parse_context_drift" in notes[0]


class TestDwarfContextStatus:
    @pytest.fixture
    def channels(self, monkeypatch: pytest.MonkeyPatch):
        """Replace the per-side channel facts with the given ones, keyed by
        snapshot identity, so each case states exactly what each side carries."""
        table: dict[int, DebugInfoEvidence] = {}

        def fake(snap: AbiSnapshot | None) -> DebugInfoEvidence:
            if snap is None:
                return DebugInfoEvidence(False, False, False)
            return table[id(snap)]

        monkeypatch.setattr("abicheck.model.debug_evidence.debug_info_evidence", fake)
        return table

    def test_no_channel_on_either_side_is_not_evaluated(self, channels) -> None:
        old, new = _snap(), _snap()
        channels[id(old)] = DebugInfoEvidence(False, False, False)
        channels[id(new)] = DebugInfoEvidence(False, False, False)
        assert _dwarf_context_status(old, new) == ("not_evaluated", [])

    def test_basic_channel_on_one_side_names_the_other_side(self, channels) -> None:
        old, new = _snap(), _snap()
        channels[id(old)] = DebugInfoEvidence(True, False, True)
        channels[id(new)] = DebugInfoEvidence(False, False, False)
        status, notes = _dwarf_context_status(old, new)
        assert status == "asymmetric"
        assert len(notes) == 1
        assert "basic dwarf channel" in notes[0]
        assert "the new side" in notes[0]

    def test_advanced_channel_on_one_side_names_the_other_side(self, channels) -> None:
        old, new = _snap(), _snap()
        channels[id(old)] = DebugInfoEvidence(False, False, False)
        channels[id(new)] = DebugInfoEvidence(False, True, False)
        status, notes = _dwarf_context_status(old, new)
        assert status == "asymmetric"
        assert len(notes) == 1
        assert "dwarf_advanced channel" in notes[0]
        assert "the old side" in notes[0]

    def test_failed_advanced_extraction_is_named_per_side_not_as_no_debug_info(
        self,
    ) -> None:
        # A FAILED advanced read is incomplete coverage on the side that
        # failed; it is reported even when the other side has no DWARF at all.
        failed = AdvancedDwarfMetadata(
            has_dwarf=True, extraction=AdvancedExtractionStatus.FAILED
        )
        old = AbiSnapshot(
            version="1.0", library="libfoo.so.1", functions=[], dwarf_advanced=failed
        )
        new = _snap()
        status, notes = _dwarf_context_status(old, new)
        assert status == "asymmetric"
        assert notes == [
            "DWARF advanced extraction failed on the old side: its facts are "
            "unknown, so dwarf_advanced findings for that side cannot be "
            "established"
        ]

    def test_each_channel_asymmetric_reports_both_notes(self, channels) -> None:
        # Basic only on old and advanced only on new: each detector family
        # skips on opposite sides, so both gaps must be reported.
        old, new = _snap(), _snap()
        channels[id(old)] = DebugInfoEvidence(True, False, True)
        channels[id(new)] = DebugInfoEvidence(False, True, False)
        status, notes = _dwarf_context_status(old, new)
        assert status == "asymmetric"
        assert len(notes) == 2

    def test_both_sides_with_both_channels_is_clean(self, channels) -> None:
        old, new = _snap(), _snap()
        channels[id(old)] = DebugInfoEvidence(True, True, True)
        channels[id(new)] = DebugInfoEvidence(True, True, True)
        assert _dwarf_context_status(old, new) == ("clean", [])


class TestL3ContextStatus:
    def test_neither_side_has_build_evidence_is_not_evaluated(self) -> None:
        assert _l3_context_status(None, None) == ("not_evaluated", [])

    @pytest.mark.parametrize(
        ("old_has", "new_has", "missing"),
        [(True, False, "new"), (False, True, "old")],
    )
    def test_one_sided_build_evidence_names_the_missing_side(
        self, old_has: bool, new_has: bool, missing: str
    ) -> None:
        old = SimpleNamespace(build_evidence=object() if old_has else None)
        new = SimpleNamespace(build_evidence=object() if new_has else None)
        status, notes = _l3_context_status(old, new)
        assert status == "asymmetric"
        assert len(notes) == 1
        assert f"the {missing} side carries no BuildEvidence" in notes[0]

    def test_build_evidence_on_both_sides_is_clean(self) -> None:
        pack = SimpleNamespace(build_evidence=object())
        assert _l3_context_status(pack, pack) == ("clean", [])
