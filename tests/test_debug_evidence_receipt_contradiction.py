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

"""P1 review finding, split out of ``test_analysis_assurance.py`` (``_extra``-
style sibling, matching e.g. ``test_ctf_metadata_evidence.py``) purely to
stay under that file's AI-readiness no-growth debt baseline -- it already
sits exactly at its 1993-line baseline, close to the 2000-line hard cap.

Finding: ``_debug_evidence_receipt``'s ``basic_state``/``advanced_state``
each resolve via ``getattr(channel, "evidence_state", None) or (...)``,
where the ``(...)`` fallback derives a state from ``has_dwarf`` -- but that
fallback can never actually fire against a *real* ``DwarfMetadata``/
``AdvancedDwarfMetadata`` instance, because ``evidence_state`` is a
dataclass field whose own default is the non-empty string ``"not_available"``
(not ``None``). So a legacy caller still constructing
``DwarfMetadata(has_dwarf=True)``/``AdvancedDwarfMetadata(has_dwarf=True)``
without the newer ``evidence_state`` kwarg reads back with ``evidence_state
== "not_available"`` -- a state ``debug_parse_incomplete`` treats as
legitimately absent evidence, not incomplete, even though ``has_dwarf``
says data was actually found. Fixed by normalizing the ``has_dwarf=True`` /
``evidence_state="not_available"`` contradiction to ``"presence_only"`` (the
cheapest real tier), mirroring the identical degrade already applied to a
legacy pre-v44 serialized block in
``snapshot_platform_blocks.dwarf_from_dict``/``dwarf_advanced_from_dict``.
"""

from __future__ import annotations

from abicheck.analysis_assurance import _debug_evidence_receipt
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.model import AbiSnapshot


def _snapshot(
    dwarf: DwarfMetadata, dwarf_advanced: AdvancedDwarfMetadata
) -> AbiSnapshot:
    return AbiSnapshot(
        version="1.0",
        library="libfoo.so.1",
        dwarf=dwarf,
        dwarf_advanced=dwarf_advanced,
    )


class TestBasicChannelContradiction:
    def test_legacy_has_dwarf_true_with_default_evidence_state_is_normalized(
        self,
    ) -> None:
        """The reviewer's exact reported shape: a legacy in-memory caller
        constructs ``DwarfMetadata(has_dwarf=True)`` without the newer
        ``evidence_state`` kwarg, so the field reads back as its own
        dataclass default (``"not_available"``) rather than ``None`` --
        the ``or`` fallback in ``_debug_evidence_receipt`` can never see
        this case. Must be normalized to ``"presence_only"``, not silently
        trusted as ``"not_available"``."""
        snap = _snapshot(
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=False),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["basic"] == "presence_only"

    def test_explicit_not_available_with_has_dwarf_true_is_also_normalized(
        self,
    ) -> None:
        """The same contradiction, stated explicitly rather than via the
        dataclass default -- the fix cannot (and should not attempt to)
        distinguish "explicitly not_available" from "left at default",
        since both are equally contradictory against has_dwarf=True."""
        snap = _snapshot(
            DwarfMetadata(has_dwarf=True, evidence_state="not_available"),
            AdvancedDwarfMetadata(has_dwarf=False),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["basic"] == "presence_only"

    def test_has_dwarf_false_with_default_evidence_state_is_not_normalized(
        self,
    ) -> None:
        """Positive control: has_dwarf=False with the default
        evidence_state is NOT a contradiction -- both agree nothing was
        found, so this must stay "not_available"."""
        snap = _snapshot(
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["basic"] == "not_available"

    def test_explicit_parsed_state_is_left_untouched(self) -> None:
        """Positive control: a real, non-contradictory evidence_state must
        pass through unchanged."""
        snap = _snapshot(
            DwarfMetadata(has_dwarf=True, evidence_state="parsed"),
            AdvancedDwarfMetadata(has_dwarf=False),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["basic"] == "parsed"


class TestAdvancedChannelContradiction:
    def test_legacy_has_dwarf_true_with_default_evidence_state_is_normalized(
        self,
    ) -> None:
        """The advanced-channel sibling of the basic-channel case above."""
        snap = _snapshot(
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=True),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["advanced"] == "presence_only"

    def test_has_dwarf_false_with_default_evidence_state_is_not_normalized(
        self,
    ) -> None:
        snap = _snapshot(
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        )
        receipt = _debug_evidence_receipt(snap)
        assert receipt["advanced"] == "not_available"


class TestDebugParseIncompleteReflectsNormalization:
    """The whole point of the normalization: a legacy has_dwarf=True/
    default-evidence_state snapshot must now actually be flagged as
    incomplete evidence by compute_analysis_assurance's own
    debug_parse_incomplete predicate, not silently pass as
    "not_available" (a state debug_parse_incomplete treats as legitimately
    absent, not incomplete)."""

    def test_legacy_snapshot_is_flagged_incomplete(self) -> None:
        from abicheck import checker
        from abicheck.analysis_assurance import AnalysisAssurance
        from abicheck.model import Function, Visibility

        fn = Function(
            name="pub_a",
            mangled="_Z5pub_av",
            visibility=Visibility.PUBLIC,
            return_type="void",
        )
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=[fn],
            dwarf=DwarfMetadata(has_dwarf=True),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=False),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=[fn],
            dwarf=DwarfMetadata(has_dwarf=True),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=False),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert any("presence-probed or failed to parse" in n for n in aa.notes), (
            aa.notes
        )
