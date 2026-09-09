# SPDX-License-Identifier: Apache-2.0
"""Structural checks on the expected-gap registry itself.

The registry (``tests/parity/gaps.py``) *is* the migration's definition of
done (plan §6 Phase 0): it must name exactly the capabilities ADR-068 §1 and
the plan's requirements enumerate *that are still open*, each with a real
reason and a real plan-phase reference -- never an empty placeholder, never
silently missing an entry, and never still listing one a phase has closed.
Four of the original fifteen scan-only capabilities were closed first
(changed-path localization and the abi3 audit, Phase 2c/2d), then all eleven
cross-source checks closed across two PRs (Phase 2a): ``unversioned_
exported_symbol``, ``private_header_leak``, ``exported_not_public``,
``public_not_exported``, ``rtti_for_internal_type``, and ``public_to_
internal_dependency`` first, then ``header_build_context_mismatch``,
``odr_type_variant``, ``identity_collision_detected``, ``compile_context_
conflict``, and ``source_surface_dso_mismatch``. The sixteenth,
differently-shaped ``finding_evolution`` entry that used to live in
``NOT_YET_IMPLEMENTED_ANYWHERE`` is also closed -- ADR-068 Phase 1 item 2
landed the vocabulary (see ``test_evolution_state_gap.py``), so that registry
is empty today.
"""

from __future__ import annotations

from .gaps import ALL_EXPECTED_GAPS, EXPECTED_GAPS, NOT_YET_IMPLEMENTED_ANYWHERE

#: The original fifteen-capability red set is now fully closed: all eleven
#: cross-source checks (Phase 2a) and pattern_scan/preprocessor_scan
#: (Phase 2b), alongside changed_path_localization and abi3_audit (Phase
#: 2c/2d), were all deleted from the registry by the PRs that closed them
#: (`compare --since/--changed-path`, `compare --abi3`, `checker.compare`'s
#: automatic `cross_source_checks` stage, `checker.compare`'s automatic
#: `pattern_preprocessor_scan` stage): a closed gap is removed, never left
#: listed, which is what makes this registry the migration's own definition
#: of done (plan §6 Phase 0/3).
_REQUIRED_SCAN_ONLY_KEYS: set[str] = set()


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
    """All eleven ``crosscheck.ALL_CHECKS`` entries are closed now (ADR-068
    §3): ``unversioned_exported_symbol`` and ``private_header_leak`` landed
    first, then ``exported_not_public``, ``public_not_exported``,
    ``rtti_for_internal_type``, and ``public_to_internal_dependency`` (plan
    §3 rows 3-5), then the remaining five -- ``header_build_context_
    mismatch``, ``odr_type_variant``, ``identity_collision_detected``,
    ``compile_context_conflict``, and ``source_surface_dso_mismatch``. None
    of the eleven is a registered scan-only gap any more, so no
    ``crosscheck.ALL_CHECKS`` name survives in ``EXPECTED_GAPS`` at all --
    which, alongside pattern_scan/preprocessor_scan's own closure (Phase
    2b), is exactly why ``EXPECTED_GAPS`` is empty."""
    from abicheck.buildsource.cross_source_checks import ALL_CHECKS

    assert set(EXPECTED_GAPS) == set()
    assert set(ALL_CHECKS) & set(EXPECTED_GAPS) == set()
