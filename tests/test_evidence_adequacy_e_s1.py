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

"""E-S1 (docs/contribute/plans/vision-api-abi-evolution.md section E /
cli-cleanup-phase-two.md Block 5): compiler-probe-failure toolchain identity
and per-detector layout-unverified rows.

Two independent primitives, each tested against its own small, exhaustive
catalog of inputs rather than a single hand-picked example -- per this
repo's own "a bug fix's regression test targets the bug *class*" convention
(root AGENTS.md):

- ``dumper_toolchain._compiler_identity_status`` /
  ``comparability_profile._toolchain_identity_status`` /
  ``comparability.check_contracts_comparable``: a compiler-probe failure
  must produce ``FactStatus.FAILED``, never collapse into the same "absent"
  shape a probe that was simply never attempted produces -- and the
  comparability gate must never let a FAILED toolchain identity compare
  silently, regardless of whether the two sides' opaque profile fields
  happen to still agree.
- ``analysis_assurance._layout_unverified_detectors``: a snapshot pair with
  no DWARF/DWARF-advanced evidence on either side must report a genuine,
  non-empty "layout unverified" row -- never the same empty tuple a pair
  with real DWARF evidence gets.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.analysis_assurance import (
    AnalysisAssurance,
    _layout_unverified_detectors,
)
from abicheck.checker import compare
from abicheck.comparability import check_contracts_comparable
from abicheck.comparability_profile import _toolchain_identity_status
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.errors import ProfileMismatchError
from abicheck.extract.toolchain_identity import (
    _compiler_family_from_toolchain,
    compiler_identity_status as _compiler_identity_status,
)
from abicheck.model import (
    AbiSnapshot,
    ExtractionContract,
    FactStatus,
    Function,
    Visibility,
)

# The three toolchain-probe states named in the task catalog.
_TOOLCHAIN_PRESENT = "present"
_TOOLCHAIN_FAILED = "failed"
_TOOLCHAIN_ABSENT = "absent"

_TOOLCHAIN_STATES = (_TOOLCHAIN_PRESENT, _TOOLCHAIN_FAILED, _TOOLCHAIN_ABSENT)


def _fn() -> Function:
    return Function(
        name="pub_a",
        mangled="_Z5pub_av",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _ast_toolchain_for(state: str) -> dict[str, str]:
    """The raw ``ast_toolchain`` dict ``dumper_toolchain`` would have stamped
    for one of the three catalog states."""
    if state == _TOOLCHAIN_PRESENT:
        return {
            "producer": "castxml",
            "selected": "/usr/bin/castxml",
            "compiler_selected": "/usr/bin/gcc",
        }
    if state == _TOOLCHAIN_FAILED:
        return {
            "producer": "castxml",
            "selected": "/usr/bin/castxml",
            "compiler_error": "resolution failed",
        }
    assert state == _TOOLCHAIN_ABSENT
    return {}


def _snapshot_for_toolchain_state(state: str, *, fingerprint: str) -> AbiSnapshot:
    """One side of a compare, carrying an ``ExtractionContract`` built the
    same way ``dumper_contract._attach_extraction_contract`` would build it
    from *state*'s ``ast_toolchain`` -- real production wiring, not a
    hand-rolled shortcut, so this test exercises the same seam a real dump
    goes through.
    """
    ast_toolchain = _ast_toolchain_for(state)
    status = _compiler_identity_status(ast_toolchain)
    family = _compiler_family_from_toolchain(ast_toolchain)
    snap = AbiSnapshot(version="1.0", library="libfoo.so.1", functions=[_fn()])
    if state == _TOOLCHAIN_ABSENT:
        # No L2 frontend ran at all -- no contract attached, matching
        # compute_extraction_contract's own "nothing to fingerprint"
        # convention for a plain binary/symbols-only dump.
        snap.contract = None
        return snap
    snap.contract = ExtractionContract(
        profile_fingerprint=fingerprint,
        profile_fields={"compiler_family": family or ""},
        compiler_identity_status=status.value if status is not None else None,
    )
    return snap


class TestCompilerProbeFailureStatus:
    """``_compiler_identity_status``/``_compiler_family_from_toolchain``
    over the full three-state catalog."""

    @pytest.mark.parametrize("state", _TOOLCHAIN_STATES)
    def test_status_matches_catalog_state(self, state: str) -> None:
        ast_toolchain = _ast_toolchain_for(state)
        status = _compiler_identity_status(ast_toolchain)
        if state == _TOOLCHAIN_PRESENT:
            assert status is FactStatus.PRESENT
        elif state == _TOOLCHAIN_FAILED:
            assert status is FactStatus.FAILED
        else:
            assert status is None

    def test_failed_probe_never_reports_a_guessed_family(self) -> None:
        """The specific regression this slice closes: a castxml dump whose
        host-compiler resolution failed must not guess a family from
        castxml's own executable name (previously ``"castxml"``, identical
        on both sides of a compare regardless of the real, unresolved host
        compiler)."""
        family = _compiler_family_from_toolchain(_ast_toolchain_for(_TOOLCHAIN_FAILED))
        assert family is None


class TestComparabilityGateNeverSilentlyComparesOnFailure:
    """For every (toolchain-present, toolchain-failed, toolchain-absent) x
    itself combination, the comparability gate must never silently treat a
    FAILED side as comparable -- exhaustive over the 3x3 catalog."""

    @pytest.mark.parametrize(
        ("old_state", "new_state"), list(itertools.product(_TOOLCHAIN_STATES, repeat=2))
    )
    def test_matching_fingerprint_never_silently_compares_when_either_side_failed(
        self, old_state: str, new_state: str
    ) -> None:
        # Both sides share the identical (opaque) profile_fingerprint/
        # compiler_family fields -- the adversarial case: if FAILED were
        # not checked independently, an identical fingerprint alone would
        # read as "comparable".
        old = _snapshot_for_toolchain_state(old_state, fingerprint="same")
        new = _snapshot_for_toolchain_state(new_state, fingerprint="same")

        mismatch = check_contracts_comparable(old, new, diagnostic=True)
        either_failed = _TOOLCHAIN_FAILED in (old_state, new_state)
        if either_failed:
            assert mismatch is not None, (
                f"old={old_state!r} new={new_state!r}: a FAILED toolchain "
                "probe on either side must never compare silently"
            )
            assert mismatch.kind == "profile"
            assert "layout" in mismatch.dimensions
            # And the raising (non-diagnostic) path must refuse too.
            with pytest.raises(ProfileMismatchError):
                check_contracts_comparable(old, new)
        else:
            # Neither side failed -- an identical fingerprint is genuinely
            # comparable on this axis (present/present, present/absent,
            # absent/absent all fall through to "nothing to disprove").
            assert mismatch is None

    def test_toolchain_identity_status_helper_is_none_safe(self) -> None:
        assert _toolchain_identity_status(None) is None
        assert _toolchain_identity_status(ExtractionContract()) is None
        assert (
            _toolchain_identity_status(
                ExtractionContract(compiler_identity_status="not-a-real-status")
            )
            is None
        )
        assert (
            _toolchain_identity_status(
                ExtractionContract(compiler_identity_status=FactStatus.FAILED.value)
            )
            is FactStatus.FAILED
        )


# The two DWARF-presence states named in the task catalog.
_DWARF_PRESENT = "present"
_DWARF_ABSENT = "absent"
_DWARF_STATES = (_DWARF_PRESENT, _DWARF_ABSENT)


def _snapshot_for_dwarf_state(state: str) -> AbiSnapshot:
    has_dwarf = state == _DWARF_PRESENT
    return AbiSnapshot(
        version="1.0",
        library="libfoo.so.1",
        functions=[_fn()],
        dwarf=DwarfMetadata(has_dwarf=has_dwarf),
        dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=has_dwarf),
    )


class TestLayoutUnverifiedRows:
    """``_layout_unverified_detectors`` over the full 2x2 DWARF-presence
    catalog: only "absent x absent" may ever report a non-empty row, and
    that row must never collapse silently into "checked, found nothing".
    """

    @pytest.mark.parametrize(
        ("old_state", "new_state"), list(itertools.product(_DWARF_STATES, repeat=2))
    )
    def test_only_both_absent_reports_unverified_layout(
        self, old_state: str, new_state: str
    ) -> None:
        old = _snapshot_for_dwarf_state(old_state)
        new = _snapshot_for_dwarf_state(new_state)
        detectors = _layout_unverified_detectors(old, new)
        both_absent = old_state == new_state == _DWARF_ABSENT
        if both_absent:
            assert detectors, "both sides lack DWARF -- must report a genuine row"
            assert "dwarf" in detectors
            assert "layout_descriptor" in detectors
        else:
            assert detectors == ()

    def test_end_to_end_through_compare_reports_layout_unverified(self) -> None:
        old = _snapshot_for_dwarf_state(_DWARF_ABSENT)
        new = _snapshot_for_dwarf_state(_DWARF_ABSENT)
        result = compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.layout_unverified_detectors
        assert any("layout unverified" in n for n in aa.notes)

    def test_end_to_end_through_compare_is_empty_when_dwarf_present(self) -> None:
        old = _snapshot_for_dwarf_state(_DWARF_PRESENT)
        new = _snapshot_for_dwarf_state(_DWARF_PRESENT)
        result = compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.layout_unverified_detectors == ()
