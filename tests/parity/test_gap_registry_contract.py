# SPDX-License-Identifier: Apache-2.0
"""Structural checks on the expected-gap registry itself.

The registry (``tests/parity/gaps.py``) *is* the migration's definition of
done (plan §6 Phase 0): it must name exactly the fourteen remaining
capabilities ADR-068 §1 and the plan's requirements enumerate, each with a
real reason and a real plan-phase reference -- never an empty placeholder,
and never silently missing an entry. ``private_header_leak`` and
``finding_evolution`` were the first two entries closed (plan §5 P2 / ADR-068
D3, this PR) and are asserted absent, not merely renamed.
"""

from __future__ import annotations

from .gaps import ALL_EXPECTED_GAPS, EXPECTED_GAPS, NOT_YET_IMPLEMENTED_ANYWHERE

#: The ten remaining cross-source checks (crosscheck.ALL_CHECKS minus the
#: one migrated so far) + pattern_scan + preprocessor_scan +
#: changed_path_localization + abi3_audit -- exactly the list this task's
#: own requirements name as the still-red set.
_REQUIRED_SCAN_ONLY_KEYS = {
    "exported_not_public",
    "public_not_exported",
    "header_build_context_mismatch",
    "odr_type_variant",
    "public_to_internal_dependency",
    "unversioned_exported_symbol",
    "rtti_for_internal_type",
    "identity_collision_detected",
    "compile_context_conflict",
    "source_surface_dso_mismatch",
    "pattern_scan",
    "preprocessor_scan",
    "changed_path_localization",
    "abi3_audit",
}

#: Checks already migrated onto the FindingEvolution model (plan §6 Phase
#: 2a) -- kept in lockstep with
#: ``abicheck.workflows.crosscheck_evolution.MIGRATED_CROSSCHECKS`` by
#: ``test_crosscheck_keys_match_all_checks`` below, so the two registries
#: (this one and the real migration switch) cannot silently drift apart.
_MIGRATED_CROSSCHECKS = {"private_header_leak"}


def test_registry_names_exactly_the_required_scan_only_capabilities() -> None:
    assert set(EXPECTED_GAPS) == _REQUIRED_SCAN_ONLY_KEYS


def test_every_gap_has_a_non_empty_reason_and_phase() -> None:
    for key, gap in ALL_EXPECTED_GAPS.items():
        assert gap.reason.strip(), f"{key}: empty reason"
        assert gap.plan_phase.strip(), f"{key}: empty plan_phase"
        assert "Phase" in gap.plan_phase, f"{key}: plan_phase must name a phase"


def test_finding_evolution_gap_is_closed() -> None:
    """Plan §5 P2 / ADR-068 D3 landed ``FindingEvolution`` (this PR) -- the
    "not implemented anywhere" tracking entry is gone, and it must never
    reappear in ``EXPECTED_GAPS`` either (it was never a scan-vs-compare
    capability gap to begin with -- see ``gaps.py``'s own docstring)."""
    assert "finding_evolution" not in EXPECTED_GAPS
    assert "finding_evolution" not in NOT_YET_IMPLEMENTED_ANYWHERE
    assert NOT_YET_IMPLEMENTED_ANYWHERE == {}
    assert ALL_EXPECTED_GAPS == EXPECTED_GAPS


def test_private_header_leak_gap_is_closed() -> None:
    """The first cross-source check migrated onto FindingEvolution (this
    PR) -- its EXPECTED_GAPS row must be deleted, not merely marked closed,
    per ``gaps.py``'s own contract."""
    assert "private_header_leak" not in EXPECTED_GAPS
    assert "private_header_leak" not in ALL_EXPECTED_GAPS


def test_crosscheck_keys_match_all_checks() -> None:
    from abicheck.buildsource.crosscheck import ALL_CHECKS
    from abicheck.workflows.crosscheck_evolution import MIGRATED_CROSSCHECKS

    assert set(MIGRATED_CROSSCHECKS) == _MIGRATED_CROSSCHECKS

    crosscheck_keys = {
        k
        for k in EXPECTED_GAPS
        if k
        not in {
            "pattern_scan",
            "preprocessor_scan",
            "changed_path_localization",
            "abi3_audit",
        }
    }
    assert crosscheck_keys | _MIGRATED_CROSSCHECKS == set(ALL_CHECKS)
