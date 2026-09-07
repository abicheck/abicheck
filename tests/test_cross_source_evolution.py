# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for ADR-068 D3 / plan P2's ``CrossSourceEvolution`` state and the
two cross-source checks migrated onto it so far:
``unversioned_exported_symbol`` and ``private_header_leak``.

The crux (plan §7 F-8/F-9): a pre-existing problem must never read as
``introduced`` merely because one side's evidence couldn't confirm it. This
is exercised as a property over several evidence combinations
(``TestNotEvaluatedCrux``, and ``test_private_header_leak_evolution_matrix``'s
own 3x3 evidence/finding matrix), not a single fixed fixture — see root
``AGENTS.md``'s bug-class regression-testing guidance. The
``private_header_leak`` tests additionally exercise the per-check identity
generalization ``workflows.cross_source_evolution`` needed to support it --
see that module's own docstring.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, CrossSourceEvolution
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin
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
    assert results[0].cross_source_evolution == CrossSourceEvolution.PERSISTENT
    assert results[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_introduced_when_flagged_only_on_new_both_evaluated():
    old = _snap(_no_evidence_elf(), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 1
    assert results[0].symbol == "_Z6leakyv"
    assert results[0].cross_source_evolution == CrossSourceEvolution.INTRODUCED


def test_resolved_when_flagged_only_on_old_both_evaluated():
    old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
    new = _snap(_no_evidence_elf(), version="1.1")
    results = compute_cross_source_evolution(old, new)
    assert len(results) == 1
    assert results[0].symbol == "_Z6leakyv"
    assert results[0].cross_source_evolution == CrossSourceEvolution.RESOLVED


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
    assert all(
        r.cross_source_evolution == CrossSourceEvolution.PERSISTENT for r in results
    )


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
        assert finding.cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
        assert finding.cross_source_evolution not in (
            CrossSourceEvolution.INTRODUCED,
            CrossSourceEvolution.RESOLVED,
        )

    def test_present_on_both_but_only_one_side_evaluated_is_not_evaluated(self):
        """The literal plan F-8 scenario: leak present in BOTH real releases,
        but OLD is a stripped/ELF-only baseline with no evidence to confirm
        it. Must never read as INTRODUCED."""
        old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
        new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
        results = compute_cross_source_evolution(old, new)
        assert len(results) == 1
        assert results[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED

    def test_resolved_scenario_f9_visible_when_both_evaluated(self):
        """Plan F-9: leak present in OLD, fixed in NEW -- RESOLVED, and
        distinct from NOT_EVALUATED (both sides *did* have evidence)."""
        old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
        new = _snap(_no_evidence_elf(), version="1.1")
        results = compute_cross_source_evolution(old, new)
        assert len(results) == 1
        assert results[0].cross_source_evolution == CrossSourceEvolution.RESOLVED


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
        getattr(c, "cross_source_evolution", None) is not None for c in result.changes
    )


def test_compare_opt_in_merges_evolution_stated_finding():
    old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare(old, new, cross_source_checks=True)
    evolved = [
        c
        for c in result.changes
        if getattr(c, "cross_source_evolution", None) is not None
    ]
    assert len(evolved) == 1
    assert evolved[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
    assert evolved[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_compare_result_cross_source_evolution_default_none() -> None:
    """A ``Change`` never touched by this module carries ``None`` -- keeps
    every pre-existing producer's output unchanged."""
    c = Change(kind=ChangeKind.FUNC_ADDED, symbol="foo", description="added")
    assert c.cross_source_evolution is None


# --------------------------------------------------------------------------- #
# private_header_leak -- the second migrated check (plan §3 #3-#4). Exercises
# the per-check identity generalization: unlike unversioned_exported_symbol,
# a single symbol can carry more than one finding here (one function leaking
# two distinct private types), distinguished only by ``new_value``.
# --------------------------------------------------------------------------- #

_PHL_NONE = "no_evidence"
_PHL_CLEAN = "evidence_clean"
_PHL_LEAK = "evidence_leaked"


def _phl_snapshot(state: str) -> AbiSnapshot:
    """A minimal, compiler-free ``AbiSnapshot`` for one evidence/finding
    state of ``private_header_leak`` (mirrors ``tests/test_crosscheck.py``'s
    own ``test_private_header_leak_flags_public_api_exposing_private_type``
    fixture shape)."""
    if state == _PHL_NONE:
        # No header evidence at all -- crosscheck._origin_resolvable is
        # False regardless of any decl present (a stripped/no-headers dump).
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]),
        )
    has_leak = state == _PHL_LEAK
    if state not in (_PHL_CLEAN, _PHL_LEAK):
        raise ValueError(state)
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="_Z3usev",
                return_type="Impl *" if has_leak else "int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        types=(
            [RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER)]
            if has_leak
            else []
        ),
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]),
    )


#: The full 3x3 (OLD state, NEW state) evidence/finding matrix, independently
#: restated from ADR-068 D3's table -- `None` means "no private_header_leak
#: finding at all".
_PHL_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_PHL_NONE, _PHL_NONE): None,
    (_PHL_NONE, _PHL_CLEAN): None,
    # F-8: pre-existing leak, baseline lacking evidence -- not_evaluated.
    (_PHL_NONE, _PHL_LEAK): CrossSourceEvolution.NOT_EVALUATED,
    (_PHL_CLEAN, _PHL_NONE): None,
    (_PHL_CLEAN, _PHL_CLEAN): None,
    (_PHL_CLEAN, _PHL_LEAK): CrossSourceEvolution.INTRODUCED,
    # Sibling of F-9: candidate lacks evidence -- can't confirm "resolved".
    (_PHL_LEAK, _PHL_NONE): CrossSourceEvolution.NOT_EVALUATED,
    # F-9: leak present in OLD, fixed in NEW.
    (_PHL_LEAK, _PHL_CLEAN): CrossSourceEvolution.RESOLVED,
    (_PHL_LEAK, _PHL_LEAK): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_PHL_MATRIX))
def test_private_header_leak_evolution_matrix(old_state: str, new_state: str) -> None:
    old = _phl_snapshot(old_state)
    new = _phl_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    expected = _PHL_MATRIX[(old_state, new_state)]
    if expected is None:
        assert leaks == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(leaks) == 1, (
            f"expected exactly one finding for {old_state, new_state}"
        )
        assert leaks[0].cross_source_evolution == expected


def test_private_header_leak_f8_pre_existing_on_evidenceless_baseline() -> None:
    """F-8, named directly: the leak exists on BOTH sides, but OLD's
    snapshot has no header evidence at all (a stripped baseline). It must
    read as `not_evaluated` -- never `introduced`."""
    old = _phl_snapshot(_PHL_NONE)
    new = _phl_snapshot(_PHL_LEAK)
    changes = compute_cross_source_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
    assert leaks[0].cross_source_evolution != CrossSourceEvolution.INTRODUCED


def test_private_header_leak_f9_fixed_reads_as_resolved() -> None:
    """F-9, named directly: leak present in OLD, fixed in NEW, both sides
    with sufficient evidence -- must read as `resolved`."""
    old = _phl_snapshot(_PHL_LEAK)
    new = _phl_snapshot(_PHL_CLEAN)
    changes = compute_cross_source_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].cross_source_evolution == CrossSourceEvolution.RESOLVED


def test_private_header_leak_identity_distinguishes_two_leaks_on_one_symbol() -> None:
    """The generalization this check specifically motivated: one function
    (``use``) referencing TWO distinct private types across OLD/NEW must
    resolve as two independent findings, not collapse onto one because they
    share a ``symbol`` -- a bare symbol-keyed identity would silently drop
    one of the two (the bug this module's per-check identity fixes)."""

    def _snap_with_leak(leaked_type: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                Function(
                    name="use",
                    mangled="_Z3usev",
                    return_type=f"{leaked_type} *",
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name=leaked_type, kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]),
        )

    old = _snap_with_leak("OldImpl")
    new = _snap_with_leak("NewImpl")
    changes = compute_cross_source_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    by_value = {c.new_value: c.cross_source_evolution for c in leaks}
    assert by_value == {
        "OldImpl": CrossSourceEvolution.RESOLVED,
        "NewImpl": CrossSourceEvolution.INTRODUCED,
    }


def test_private_header_leak_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.PRIVATE_HEADER_LEAK in RISK_KINDS
    old = _phl_snapshot(_PHL_NONE)
    new = _phl_snapshot(_PHL_LEAK)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.PRIVATE_HEADER_LEAK:
            assert c.effective_verdict is None


def test_private_header_leak_wired_into_compare_opt_in() -> None:
    """``checker.compare(..., cross_source_checks=True)`` surfaces this
    check's evolution-stated finding too, not just the workflows helper."""
    old = _phl_snapshot(_PHL_NONE)
    new = _phl_snapshot(_PHL_LEAK)
    result = compare(old, new, cross_source_checks=True, scope_to_public_surface=False)
    leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
