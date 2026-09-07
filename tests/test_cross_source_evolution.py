# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for ADR-068 D3 / plan P2's ``FindingEvolution`` state and the first
cross-source check (``unversioned_exported_symbol``) migrated onto it.

The crux (plan §7 F-8/F-9): a pre-existing problem must never read as
``introduced`` merely because one side's evidence couldn't confirm it. This
is exercised as a property over several evidence combinations
(``TestNotEvaluatedCrux``), not a single fixed fixture — see root
``AGENTS.md``'s bug-class regression-testing guidance.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, FindingEvolution
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.workflows.cross_source_evolution import compute_cross_source_evolution


def _snap(elf: ElfMetadata | None, *, version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version=version, from_headers=True, elf=elf)


def _versioned_elf(unversioned_names: tuple[str, ...]) -> ElfMetadata:
    """An ELF snapshot with a real versioning scheme and *unversioned_names*
    exported with no version -- each flags ``unversioned_exported_symbol``.
    """
    symbols = [ElfSymbol(name="_Z3okv", version="FOO_1.0", visibility="default")]
    symbols += [
        ElfSymbol(name=n, version="", visibility="default") for n in unversioned_names
    ]
    return ElfMetadata(symbols=symbols, versions_defined=["FOO_1.0"])


def _no_evidence_elf() -> ElfMetadata:
    """ELF present but with no versioning scheme -- the check runs (status
    'present') but finds nothing to flag, distinct from not running at all."""
    return ElfMetadata(
        symbols=[ElfSymbol(name="_Z3okv", version="", visibility="default")],
        versions_defined=[],
    )


# --------------------------------------------------------------------------- #
# compute_cross_source_evolution: the four states, directly
# --------------------------------------------------------------------------- #


def test_persistent_when_flagged_on_both_evaluated_sides():
    old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 1
    assert results[0].symbol == "_Z6leakyv"
    assert results[0].finding_evolution == FindingEvolution.PERSISTENT
    assert results[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_introduced_when_flagged_only_on_new_both_evaluated():
    old = _snap(_no_evidence_elf(), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 1
    assert results[0].symbol == "_Z6leakyv"
    assert results[0].finding_evolution == FindingEvolution.INTRODUCED


def test_resolved_when_flagged_only_on_old_both_evaluated():
    old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
    new = _snap(_no_evidence_elf(), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 1
    assert results[0].symbol == "_Z6leakyv"
    assert results[0].finding_evolution == FindingEvolution.RESOLVED


def test_no_finding_when_neither_side_flags_anything():
    old = _snap(_no_evidence_elf(), version="1.0")
    new = _snap(_no_evidence_elf(), version="1.1")
    assert compute_cross_source_evolution(old, new) == []


def test_capped_default_finding_count_never_reads_as_not_evaluated():
    """CodeRabbit review, fresh evidence: ``run_crosschecks``'s default
    ``max_per_check=200`` cap truncates a check's own ``findings`` list once
    exceeded, but still records a ``providers`` entry (evaluated) for it --
    the crux (plan §7 F-8/F-9) also has a *volume* shape, not just a
    *missing-evidence* one. A side whose real finding count exceeds the
    default cap must still resolve every one of them as PERSISTENT/
    INTRODUCED/RESOLVED, never NOT_EVALUATED purely because the count is
    large -- ``_run_one_side`` disables the cap (``max_per_check=0``) for
    exactly this reason. 250 unversioned exports on both sides comfortably
    exceeds the 200 default.
    """
    names = tuple(f"_Z{i}leakyv" for i in range(250))
    old = _snap(_versioned_elf(names), version="1.0")
    new = _snap(_versioned_elf(names), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 250
    assert {r.symbol for r in results} == set(names)
    assert all(r.finding_evolution == FindingEvolution.PERSISTENT for r in results)


# --------------------------------------------------------------------------- #
# TestNotEvaluatedCrux -- the correctness crux (plan §7 F-8/F-9), stated as a
# property over several evidence combinations, not one fixed fixture.
# --------------------------------------------------------------------------- #


class TestNotEvaluatedCrux:
    """A finding present on an evaluated side must read ``not_evaluated`` --
    never ``introduced``/``resolved`` -- whenever its sibling side's own
    evidence could not confirm or deny it (a stripped/non-ELF snapshot).
    """

    #: (old_has_elf, new_has_elf) -- every combination where exactly one
    #: side lacks ELF evidence entirely (no evidence at all -> not
    #: evaluated) while the other side genuinely flags the symbol.
    #: `itertools.product` generates the matrix rather than hand-listing
    #: each row, so extending either axis (e.g. a third evidence shape)
    #: doesn't require rewriting the case list by hand; the
    #: both-True/both-False corners are filtered out below since they carry
    #: no "one side missing" evidence gap to assert about.
    _MISSING_SIDES = [
        (old_has_elf, new_has_elf)
        for old_has_elf, new_has_elf in itertools.product([True, False], repeat=2)
        if old_has_elf != new_has_elf
    ]

    @pytest.mark.parametrize("old_has_elf,new_has_elf", _MISSING_SIDES)
    def test_never_introduced_or_resolved_when_a_side_lacks_evidence(
        self, old_has_elf: bool, new_has_elf: bool
    ):
        old_elf = _versioned_elf(("_Z6leakyv",)) if old_has_elf else None
        new_elf = _versioned_elf(("_Z6leakyv",)) if new_has_elf else None
        old = _snap(old_elf, version="1.0")
        new = _snap(new_elf, version="1.1")

        results = compute_cross_source_evolution(old, new)

        # The symbol is flagged on at least one evaluated side (both cases
        # here have ELF+scheme+unversioned symbol on whichever side has
        # ELF), so it must be reported -- but only ever as NOT_EVALUATED.
        assert len(results) == 1
        finding = results[0]
        assert finding.symbol == "_Z6leakyv"
        assert finding.finding_evolution == FindingEvolution.NOT_EVALUATED
        assert finding.finding_evolution not in (
            FindingEvolution.INTRODUCED,
            FindingEvolution.RESOLVED,
        )

    def test_present_on_both_but_only_one_side_evaluated_is_not_evaluated(self):
        """The literal plan F-8 scenario: leak present in BOTH real releases,
        but OLD is a stripped/ELF-only baseline with no evidence to confirm
        it. Must never read as INTRODUCED."""
        old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
        new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
        results = compute_cross_source_evolution(old, new)
        assert len(results) == 1
        assert results[0].finding_evolution == FindingEvolution.NOT_EVALUATED

    def test_resolved_scenario_f9_visible_when_both_evaluated(self):
        """Plan F-9: leak present in OLD, fixed in NEW -- RESOLVED, and
        distinct from NOT_EVALUATED (both sides *did* have evidence)."""
        old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
        new = _snap(_no_evidence_elf(), version="1.1")
        results = compute_cross_source_evolution(old, new)
        assert len(results) == 1
        assert results[0].finding_evolution == FindingEvolution.RESOLVED


# --------------------------------------------------------------------------- #
# Authority (ADR-028 D3 / ADR-035 D1): migrating onto compare() never
# promotes the finding's own severity.
# --------------------------------------------------------------------------- #


def test_authority_unchanged_finding_stays_risk_regardless_of_evolution():
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.UNVERSIONED_EXPORTED_SYMBOL in RISK_KINDS
    old = _snap(_no_evidence_elf(), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    for c in compute_cross_source_evolution(old, new):
        assert c.effective_verdict is None  # never overridden toward BREAKING


# --------------------------------------------------------------------------- #
# Wired into compare(): off by default, opt-in via cross_source_checks=True.
# --------------------------------------------------------------------------- #


def test_compare_off_by_default():
    old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare(old, new)
    assert not any(
        getattr(c, "finding_evolution", None) is not None for c in result.changes
    )


def test_compare_opt_in_merges_evolution_stated_finding():
    old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare(old, new, cross_source_checks=True)
    evolved = [
        c for c in result.changes if getattr(c, "finding_evolution", None) is not None
    ]
    assert len(evolved) == 1
    assert evolved[0].finding_evolution == FindingEvolution.NOT_EVALUATED
    assert evolved[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_compare_result_finding_evolution_default_none() -> None:
    """A ``Change`` never touched by this module carries ``None`` -- keeps
    every pre-existing producer's output unchanged."""
    c = Change(kind=ChangeKind.FUNC_ADDED, symbol="foo", description="added")
    assert c.finding_evolution is None
