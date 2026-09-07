# SPDX-License-Identifier: Apache-2.0
"""Structural checks on the expected-gap registry itself.

The registry (``tests/parity/gaps.py``) *is* the migration's definition of
done (plan §6 Phase 0): it must name exactly the capabilities ADR-068 §1 and
the plan's requirements enumerate *that are still open*, each with a real
reason and a real plan-phase reference -- never an empty placeholder, never
silently missing an entry, and never still listing one a phase has closed.
Two of the original fifteen scan-only capabilities are already closed
(changed-path localization and the abi3 audit, Phase 2c/2d), and so is the
sixteenth, differently-shaped ``finding_evolution`` entry that used to live
in ``NOT_YET_IMPLEMENTED_ANYWHERE`` -- ADR-068 Phase 1 item 2 landed the
vocabulary (see ``test_evolution_state_gap.py``), so that registry is empty
today.
"""

from __future__ import annotations

from .gaps import ALL_EXPECTED_GAPS, EXPECTED_GAPS, NOT_YET_IMPLEMENTED_ANYWHERE

#: The eleven cross-source checks (crosscheck.ALL_CHECKS) + pattern_scan +
#: preprocessor_scan -- what is left of the red set. changed_path_localization
#: and abi3_audit were deleted from the registry by the PR that landed Phase
#: 2c/2d (`compare --since/--changed-path`, `compare --abi3`): a closed gap is
#: removed, never left listed, which is what makes this registry the
#: migration's own definition of done (plan §6 Phase 0/3).
_REQUIRED_SCAN_ONLY_KEYS = {
    "exported_not_public",
    "public_not_exported",
    "header_build_context_mismatch",
    "private_header_leak",
    "odr_type_variant",
    "public_to_internal_dependency",
    "unversioned_exported_symbol",
    "rtti_for_internal_type",
    "identity_collision_detected",
    "compile_context_conflict",
    "source_surface_dso_mismatch",
    "pattern_scan",
    "preprocessor_scan",
}


def test_registry_names_exactly_the_required_scan_only_capabilities() -> None:
    assert set(EXPECTED_GAPS) == _REQUIRED_SCAN_ONLY_KEYS


def test_every_gap_has_a_non_empty_reason_and_phase() -> None:
    for key, gap in ALL_EXPECTED_GAPS.items():
        assert gap.reason.strip(), f"{key}: empty reason"
        assert gap.plan_phase.strip(), f"{key}: empty plan_phase"
        assert "Phase" in gap.plan_phase, f"{key}: plan_phase must name a phase"


def test_finding_evolution_gap_has_closed() -> None:
    """ADR-068 Phase 1 item 2 landed the generic `FindingEvolution`
    primitive (`checker_policy.FindingEvolution`, `policy.finding_evolution`),
    so `finding_evolution` is no longer tracked anywhere in this registry --
    see `test_evolution_state_gap.py` for the real demonstration that
    replaced the old absence-of-the-vocabulary test."""
    assert "finding_evolution" not in EXPECTED_GAPS
    assert "finding_evolution" not in NOT_YET_IMPLEMENTED_ANYWHERE
    assert NOT_YET_IMPLEMENTED_ANYWHERE == {}
    assert ALL_EXPECTED_GAPS == EXPECTED_GAPS


def test_crosscheck_keys_match_all_checks() -> None:
    from abicheck.buildsource.crosscheck import ALL_CHECKS

    crosscheck_keys = {
        k for k in EXPECTED_GAPS if k not in {"pattern_scan", "preprocessor_scan"}
    }
    assert crosscheck_keys == set(ALL_CHECKS)
