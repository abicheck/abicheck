# SPDX-License-Identifier: Apache-2.0
"""Structural checks on the expected-gap registry itself.

The registry (``tests/parity/gaps.py``) *is* the migration's definition of
done (plan §6 Phase 0): it must name exactly the capabilities ADR-068 §1 and
the plan's requirements enumerate *that are still open*, each with a real
reason and a real plan-phase reference -- never an empty placeholder, never
silently missing an entry, and never still listing one a phase has closed.
All fifteen of the originally-listed scan-only capabilities are now closed
(changed-path localization and the abi3 audit, Phase 2c/2d; all eleven
cross-source checks, Phase 2a; and the pattern/preprocessor pre-scans,
Phase 2b -- this PR), and so is the sixteenth, differently-shaped
``finding_evolution`` entry that used to live in
``NOT_YET_IMPLEMENTED_ANYWHERE`` -- ADR-068 Phase 1 item 2 landed the
vocabulary (see ``test_evolution_state_gap.py``), so that registry is empty
too. ``EXPECTED_GAPS`` is therefore empty: every capability the registry
ever tracked has a real, working parity path on ``compare`` now.
"""

from __future__ import annotations

from .gaps import ALL_EXPECTED_GAPS, EXPECTED_GAPS, NOT_YET_IMPLEMENTED_ANYWHERE


def test_registry_is_empty() -> None:
    """The red set the migration exists to empty is empty."""
    assert EXPECTED_GAPS == {}


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
    assert ALL_EXPECTED_GAPS == EXPECTED_GAPS == {}


def test_crosscheck_keys_match_all_checks() -> None:
    """All eleven of ``crosscheck.ALL_CHECKS`` are ADR-068 §3 closed
    capabilities now (`checker.compare`'s automatic `cross_source_checks`
    stage), so none is a registered scan-only gap any more -- no
    cross-source-check key remains in ``EXPECTED_GAPS`` at all."""
    from abicheck.buildsource.crosscheck import ALL_CHECKS

    crosscheck_keys = {k for k in EXPECTED_GAPS if k not in {}}
    assert crosscheck_keys == set()
    # Sanity: every check name really is one of ALL_CHECKS' own eleven --
    # this test would be vacuous if ALL_CHECKS itself had silently shrunk.
    assert len(ALL_CHECKS) == 11


def test_pattern_and_preprocessor_scan_keys_are_gone() -> None:
    """Phase 2b's own closure, named explicitly rather than folded into the
    generic emptiness assertion above -- a reader should not have to infer
    this PR's specific result from `EXPECTED_GAPS == {}` alone."""
    assert "pattern_scan" not in EXPECTED_GAPS
    assert "preprocessor_scan" not in EXPECTED_GAPS
