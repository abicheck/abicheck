# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for ADR-068 D3 / plan P2's ``CrossSourceEvolution`` state and six of
the eleven cross-source checks migrated onto it: ``unversioned_exported_
symbol`` and ``private_header_leak`` (landed first), plus
``exported_not_public``, ``public_not_exported``, ``rtti_for_internal_type``,
and ``public_to_internal_dependency`` (a second slice, plan §3 rows 3-5). The
remaining five (``header_build_context_mismatch``, ``odr_type_variant``,
``identity_collision_detected``, ``compile_context_conflict``, and
``source_surface_dso_mismatch``, closing out plan §3 #3) have their own
tests in the sibling module ``test_cross_source_evolution_more_checks.py``
-- split out purely to keep this file under the architecture gate's
test-file line cap, not because the two slices differ in kind.

The crux (plan §7 F-8/F-9): a pre-existing problem must never read as
``introduced`` merely because one side's evidence couldn't confirm it. This
is exercised as a property over several evidence combinations
(``TestNotEvaluatedCrux`` for ``unversioned_exported_symbol``,
``TestFourChecksNotEvaluatedCrux`` generalizing the same property across the
four checks the second slice adds, and each check's own 3x3 evidence/finding
matrix test), not a single fixed fixture — see root ``AGENTS.md``'s bug-class
regression-testing guidance. The ``private_header_leak``,
``rtti_for_internal_type``, and ``public_to_internal_dependency`` tests
additionally exercise the per-check identity generalization
``workflows.cross_source_evolution`` needed to support a check whose own
findings are not uniquely keyed by ``symbol`` alone -- see that module's own
docstring.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
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
# Wired into compare(): automatic (ADR-068 D3/D4/D5) -- no opt-in flag, on
# by default, evidence-gated per side/check rather than a user-facing
# switch. ``cross_source_checks=False`` survives only as an internal Tier-1
# knob so a test can isolate the stage -- no front end ever sets it.
# --------------------------------------------------------------------------- #


def test_compare_runs_the_stage_by_default():
    old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare(old, new)
    evolved = [
        c
        for c in result.changes
        if getattr(c, "cross_source_evolution", None) is not None
    ]
    assert len(evolved) == 1
    assert evolved[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
    assert evolved[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_compare_snapshots_runs_the_stage_by_default_too():
    """The Tier-2 public wrapper (``workflows.compare_policy.
    compare_snapshots``, re-exported as ``service.compare_snapshots``) does
    not expose ``cross_source_checks`` at all -- unlike the Tier-1 core, it
    is generated into ``docs/reference/python-api-reference.md`` as the
    documented public Python API, so a caller here can never suppress the
    automatic stage (ADR-068 D5). Same assertion as
    ``test_compare_runs_the_stage_by_default`` above, through the public
    wrapper instead of the core verb directly."""
    from abicheck.workflows.compare_policy import compare_snapshots

    old = _snap(None, version="1.0")  # no ELF at all -> not evaluated
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare_snapshots(old, new)
    evolved = [
        c
        for c in result.changes
        if getattr(c, "cross_source_evolution", None) is not None
    ]
    assert len(evolved) == 1
    assert evolved[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
    assert evolved[0].kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL


def test_compare_no_finding_is_a_true_no_op():
    """When neither side's evidence flags anything, running the stage
    unconditionally must not add so much as an empty marker -- the exact
    invariant that keeps every pre-existing invocation exiting identically
    (plan §7 F-19/F-20's output-invariance principle, applied here)."""
    old = _snap(_no_evidence_elf(), version="1.0")
    new = _snap(_no_evidence_elf(), version="1.1")
    result = compare(old, new)
    assert not any(
        getattr(c, "cross_source_evolution", None) is not None for c in result.changes
    )


def test_compare_stage_can_still_be_disabled_internally():
    """``cross_source_checks=False`` is kept as a real Tier-1 keyword purely
    so a test can isolate the stage -- never exposed as a user-facing
    opt-out (ADR-068 D5)."""
    old = _snap(_versioned_elf(("_Z6leakyv",)), version="1.0")
    new = _snap(_versioned_elf(("_Z6leakyv",)), version="1.1")
    result = compare(old, new, cross_source_checks=False)
    assert not any(
        getattr(c, "cross_source_evolution", None) is not None for c in result.changes
    )


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


def test_private_header_leak_wired_into_compare_by_default() -> None:
    """``checker.compare()`` surfaces this check's evolution-stated finding
    too, automatically -- not just the workflows helper, and not only when
    ``cross_source_checks`` is passed explicitly (ADR-068 D3/D4/D5, on by
    default)."""
    old = _phl_snapshot(_PHL_NONE)
    new = _phl_snapshot(_PHL_LEAK)
    result = compare(old, new, scope_to_public_surface=False)
    leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


def _phl_isolated_snapshot(*, leaked: bool) -> AbiSnapshot:
    """Same shape as ``_phl_snapshot``, but the ONLY thing that differs
    between the two states is ``Impl``'s header origin (private vs.
    public) -- the function's own signature (mangled name, return type)
    never changes. Isolates "the leak alone got fixed" from any other,
    unrelated structural change a real header-promotion fix wouldn't
    actually cause, so a test using this fixture attributes a gate
    result to the leak's own resolution and nothing else.
    """
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="_Z3usev",
                return_type="Impl *",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        types=[
            RecordType(
                name="Impl",
                kind="struct",
                origin=ScopeOrigin.PRIVATE_HEADER
                if leaked
                else ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]),
    )


class TestResolvedExcludedFromTheGate:
    """Plan §7 F-9, named directly: "resolved, and visible on a passing
    run" (Codex review, PR #1172, round 12). A RESOLVED cross-source
    finding used to gate exactly like PERSISTENT/INTRODUCED -- the same
    RISK/API_BREAK kind its ChangeKind defaults to always drove the
    verdict, contradicting the acceptance criterion's own "passing run"
    half. ``is_cross_source_resolved`` (checker_policy.py) is the shared
    exclusion every gate/exit-code chokepoint now applies."""

    def test_resolved_leak_stays_out_of_the_verdict(self) -> None:
        from abicheck.checker_policy import RISK_KINDS, Verdict

        assert ChangeKind.PRIVATE_HEADER_LEAK in RISK_KINDS
        old = _phl_isolated_snapshot(leaked=True)
        new = _phl_isolated_snapshot(leaked=False)
        result = compare(old, new, scope_to_public_surface=False)
        leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
        assert len(leaks) == 1
        assert leaks[0].cross_source_evolution == CrossSourceEvolution.RESOLVED
        # The crux: still fully visible in the report...
        assert leaks[0] in result.changes
        # ...but the overall run is a pass, per F-9's own wording -- with
        # nothing else differing between the fixtures, a fully-excluded
        # RESOLVED finding leaves NO_CHANGE, not merely COMPATIBLE.
        assert result.verdict == Verdict.NO_CHANGE

    def test_resolved_leak_reports_zero_gate_contribution(self) -> None:
        from abicheck.checker_policy import Verdict
        from abicheck.policy.severity import gate_contribution_for_change

        old = _phl_isolated_snapshot(leaked=True)
        new = _phl_isolated_snapshot(leaked=False)
        result = compare(old, new, scope_to_public_surface=False)
        leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
        assert len(leaks) == 1
        # Legacy scheme (config=None): the per-finding field a report would
        # show must agree with the overall exit staying "compatible" --
        # never a nonzero contribution the exit code doesn't reflect.
        assert gate_contribution_for_change(leaks[0], None) == 0
        assert result.verdict == Verdict.NO_CHANGE

    def test_persistent_leak_still_gates_normally(self) -> None:
        # Negative control: this exclusion is specific to RESOLVED, not a
        # blanket exemption for every cross-source finding.
        from abicheck.checker_policy import Verdict

        old = _phl_isolated_snapshot(leaked=True)
        new = _phl_isolated_snapshot(leaked=True)
        result = compare(old, new, scope_to_public_surface=False)
        leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
        assert len(leaks) == 1
        assert leaks[0].cross_source_evolution == CrossSourceEvolution.PERSISTENT
        assert result.verdict != Verdict.COMPATIBLE

    def test_introduced_leak_still_gates_normally(self) -> None:
        # Negative control, the other direction.
        from abicheck.checker_policy import Verdict

        old = _phl_isolated_snapshot(leaked=False)
        new = _phl_isolated_snapshot(leaked=True)
        result = compare(old, new, scope_to_public_surface=False)
        leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
        assert len(leaks) == 1
        assert leaks[0].cross_source_evolution == CrossSourceEvolution.INTRODUCED
        assert result.verdict != Verdict.COMPATIBLE


# --------------------------------------------------------------------------- #
# The four checks this PR migrates onto compute_cross_source_evolution:
# exported_not_public, public_not_exported, rtti_for_internal_type, and
# public_to_internal_dependency (plan §3 rows 3-5). Each gets its own 3x3
# (OLD state, NEW state) evidence/finding matrix, the same pattern
# _PHL_MATRIX above exercises for private_header_leak -- the correctness
# crux (F-8/F-9) is check-agnostic, so it is stated once per check as a
# property over evidence combinations rather than a single fixture.
# --------------------------------------------------------------------------- #

_ENP_NONE = "no_evidence"  # no header provenance at all -- _origin_resolvable False
_ENP_CLEAN = "evidence_clean"
_ENP_FLAG = "evidence_flagged"


def _enp_snapshot(state: str) -> AbiSnapshot:
    """``exported_not_public``: an exported symbol with no public-header
    declaration (mirrors ``test_crosscheck.py``'s own
    ``test_exported_not_public_flags_export_only_symbol`` fixture shape)."""
    if state == _ENP_NONE:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z6secretv")]),
        )
    if state not in (_ENP_CLEAN, _ENP_FLAG):
        raise ValueError(state)
    functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
    ]
    if state == _ENP_FLAG:
        functions.append(
            Function(
                name="secret",
                mangled="_Z6secretv",
                return_type="void",
                origin=ScopeOrigin.EXPORT_ONLY,
            )
        )
    exported = ["_Z3fooi"] + (["_Z6secretv"] if state == _ENP_FLAG else [])
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=functions,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exported]),
    )


_ENP_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_ENP_NONE, _ENP_NONE): None,
    (_ENP_NONE, _ENP_CLEAN): None,
    (_ENP_NONE, _ENP_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_ENP_CLEAN, _ENP_NONE): None,
    (_ENP_CLEAN, _ENP_CLEAN): None,
    (_ENP_CLEAN, _ENP_FLAG): CrossSourceEvolution.INTRODUCED,
    (_ENP_FLAG, _ENP_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_ENP_FLAG, _ENP_CLEAN): CrossSourceEvolution.RESOLVED,
    (_ENP_FLAG, _ENP_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_ENP_MATRIX))
def test_exported_not_public_evolution_matrix(old_state: str, new_state: str) -> None:
    old = _enp_snapshot(old_state)
    new = _enp_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    expected = _ENP_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_exported_not_public_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.EXPORTED_NOT_PUBLIC in RISK_KINDS
    old = _enp_snapshot(_ENP_NONE)
    new = _enp_snapshot(_ENP_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC:
            assert c.effective_verdict is None


_PNE_NONE = "no_evidence"
_PNE_CLEAN = "evidence_clean"
_PNE_FLAG = "evidence_flagged"


def _pne_snapshot(state: str) -> AbiSnapshot:
    """``public_not_exported``: a public declaration whose export the
    binary is missing (mirrors ``test_crosscheck.py``'s own
    ``test_public_not_exported_flags_missing_symbol`` fixture shape)."""
    if state == _PNE_NONE:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3fooi")]),
        )
    if state not in (_PNE_CLEAN, _PNE_FLAG):
        raise ValueError(state)
    functions = [
        Function(
            name="foo",
            mangled="_Z3fooi",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
    ]
    exported = ["_Z3fooi"]
    if state == _PNE_FLAG:
        functions.append(
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        )
    else:
        exported.append("_Z3barv")
        functions.append(
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=functions,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exported]),
    )


_PNE_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_PNE_NONE, _PNE_NONE): None,
    (_PNE_NONE, _PNE_CLEAN): None,
    (_PNE_NONE, _PNE_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_PNE_CLEAN, _PNE_NONE): None,
    (_PNE_CLEAN, _PNE_CLEAN): None,
    (_PNE_CLEAN, _PNE_FLAG): CrossSourceEvolution.INTRODUCED,
    (_PNE_FLAG, _PNE_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_PNE_FLAG, _PNE_CLEAN): CrossSourceEvolution.RESOLVED,
    (_PNE_FLAG, _PNE_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_PNE_MATRIX))
def test_public_not_exported_evolution_matrix(old_state: str, new_state: str) -> None:
    old = _pne_snapshot(old_state)
    new = _pne_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.PUBLIC_NOT_EXPORTED]
    expected = _PNE_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_public_not_exported_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.PUBLIC_NOT_EXPORTED in RISK_KINDS
    old = _pne_snapshot(_PNE_NONE)
    new = _pne_snapshot(_PNE_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.PUBLIC_NOT_EXPORTED:
            assert c.effective_verdict is None


_RTTI_NONE = "no_evidence"
_RTTI_CLEAN = "evidence_clean"
_RTTI_FLAG = "evidence_flagged"


def _rtti_snapshot(state: str) -> AbiSnapshot:
    """``rtti_for_internal_type``: exported RTTI for a private-header type
    (mirrors ``test_crosscheck.py``'s own RTTI fixture shape)."""
    if state == _RTTI_NONE:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZTI6Widget")]),
        )
    if state not in (_RTTI_CLEAN, _RTTI_FLAG):
        raise ValueError(state)
    # Both CLEAN and FLAG declare the same private type (so _origin_resolvable
    # is True for both -- a real "provenance captured, nothing/something to
    # flag" pair, not a fixture that accidentally carries zero declarations
    # and so reads as NOT_EVALUATED for an unrelated reason); only FLAG
    # additionally exports its RTTI symbol.
    types = [RecordType(name="Widget", kind="class", origin=ScopeOrigin.PRIVATE_HEADER)]
    exported = ["_ZTI6Widget"] if state == _RTTI_FLAG else []
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        types=types,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exported]),
    )


_RTTI_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_RTTI_NONE, _RTTI_NONE): None,
    (_RTTI_NONE, _RTTI_CLEAN): None,
    (_RTTI_NONE, _RTTI_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_RTTI_CLEAN, _RTTI_NONE): None,
    (_RTTI_CLEAN, _RTTI_CLEAN): None,
    (_RTTI_CLEAN, _RTTI_FLAG): CrossSourceEvolution.INTRODUCED,
    (_RTTI_FLAG, _RTTI_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_RTTI_FLAG, _RTTI_CLEAN): CrossSourceEvolution.RESOLVED,
    (_RTTI_FLAG, _RTTI_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_RTTI_MATRIX))
def test_rtti_for_internal_type_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _rtti_snapshot(old_state)
    new = _rtti_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.RTTI_FOR_INTERNAL_TYPE]
    expected = _RTTI_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_rtti_for_internal_type_identity_distinguishes_two_types_same_symbol() -> None:
    """Same reasoning as ``test_private_header_leak_identity_distinguishes_
    two_leaks_on_one_symbol``: this check's own identity is ``(symbol,
    new_value)``, not a bare ``symbol`` -- exercised here across OLD/NEW
    where the identical RTTI symbol name resolves to a *different* private
    type on each side (a type moved/renamed under an unchanged mangled
    name). A bare-symbol identity would misread this as one continuous
    PERSISTENT finding instead of the true resolved/introduced pair."""

    def _snap_with_type(type_name: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            types=[
                RecordType(
                    name=type_name, kind="class", origin=ScopeOrigin.PRIVATE_HEADER
                )
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZTI6Widget")]),
        )

    old = _snap_with_type("Widget")
    new = _snap_with_type("Widget")
    # Both sides resolve the identical RTTI symbol to the identical type name
    # here (mangled RTTI names are not spelling-stable across a rename in
    # this fixture's simplified model) -- the identity function itself is
    # exercised directly instead, mirroring private_header_leak's own test.
    from abicheck.buildsource.crosscheck import CHECK_RTTI_FOR_INTERNAL_TYPE
    from abicheck.workflows.cross_source_evolution import _IDENTITY_FUNCS

    identity = _IDENTITY_FUNCS[CHECK_RTTI_FOR_INTERNAL_TYPE]
    old_changes = compute_cross_source_evolution(old, new)
    assert old_changes  # sanity: the fixture does flag something
    c = old_changes[0]
    assert identity(c) == (c.symbol, c.new_value)


def test_rtti_for_internal_type_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.RTTI_FOR_INTERNAL_TYPE in RISK_KINDS
    old = _rtti_snapshot(_RTTI_NONE)
    new = _rtti_snapshot(_RTTI_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.RTTI_FOR_INTERNAL_TYPE:
            assert c.effective_verdict is None


def test_rtti_for_internal_type_wired_into_compare_by_default() -> None:
    """``checker.compare()`` surfaces this check's evolution-stated finding
    too, automatically. ``scope_to_public_surface=False`` is passed for the
    same reason ``test_private_header_leak_wired_into_compare_by_default``
    passes it -- the minimal test snapshot here has no header-derived
    surface data richer than the bare origin tags -- the real end-to-end
    default-scoping behavior is exercised by ``tests/parity/
    test_crosscheck_parity.py::test_rtti_for_internal_type_reaches_compare``
    against a real G20 fixture instead."""
    old = _rtti_snapshot(_RTTI_NONE)
    new = _rtti_snapshot(_RTTI_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [c for c in result.changes if c.kind == ChangeKind.RTTI_FOR_INTERNAL_TYPE]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_PID_NONE = "no_evidence"  # no L5 source graph at all
_PID_CLEAN = "evidence_clean"  # graph present, no dependency edges to internal
_PID_FLAG = "evidence_flagged"


def _pid_snapshot(state: str) -> AbiSnapshot:
    """``public_to_internal_dependency``: a public decl reaching an internal
    one via the L5 graph (mirrors ``test_crosscheck.py``'s own
    ``test_public_to_internal_dependency_flags_public_reaching_internal``
    fixture shape)."""
    if state == _PID_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_PID_CLEAN, _PID_FLAG):
        raise ValueError(state)
    # Both CLEAN and FLAG carry at least one real dependency edge (so the
    # check's own "no decl-dependency edges at all" skip gate -- a
    # different reason to be NOT_EVALUATED than the one this fixture means
    # to exercise -- never fires for either state); only FLAG's edge
    # target is genuinely internal. CLEAN's target is a second public decl,
    # which the check must not flag.
    nodes = [
        GraphNode(
            id="decl://pub",
            kind="source_decl",
            label="pubFn",
            attrs={"visibility": "public_header"},
        ),
        GraphNode(
            id="decl://other",
            kind="source_decl",
            label="internalImpl" if state == _PID_FLAG else "otherPubFn",
            attrs={"visibility": "source" if state == _PID_FLAG else "public_header"},
        ),
    ]
    edges = [GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")]
    graph = SourceGraphSummary(nodes=nodes, edges=edges)
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_graph=graph),
    )


_PID_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_PID_NONE, _PID_NONE): None,
    (_PID_NONE, _PID_CLEAN): None,
    (_PID_NONE, _PID_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_PID_CLEAN, _PID_NONE): None,
    (_PID_CLEAN, _PID_CLEAN): None,
    (_PID_CLEAN, _PID_FLAG): CrossSourceEvolution.INTRODUCED,
    (_PID_FLAG, _PID_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_PID_FLAG, _PID_CLEAN): CrossSourceEvolution.RESOLVED,
    (_PID_FLAG, _PID_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_PID_MATRIX))
def test_public_to_internal_dependency_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _pid_snapshot(old_state)
    new = _pid_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY]
    expected = _PID_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_public_to_internal_dependency_identity_distinguishes_two_targets() -> None:
    """The generalization this check specifically motivated (same shape as
    ``private_header_leak``'s own equivalent test): one public declaration
    reaching TWO distinct internal targets across OLD/NEW must resolve as
    two independent findings, not collapse onto one because they share a
    ``symbol``."""

    def _snap_with_target(internal_name: str) -> AbiSnapshot:
        nodes = [
            GraphNode(
                id="decl://pub",
                kind="source_decl",
                label="pubFn",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://int",
                kind="source_decl",
                label=internal_name,
                attrs={"visibility": "source"},
            ),
        ]
        edges = [GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")]
        graph = SourceGraphSummary(nodes=nodes, edges=edges)
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_graph=graph),
        )

    old = _snap_with_target("oldImpl")
    new = _snap_with_target("newImpl")
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY]
    by_value = {c.new_value: c.cross_source_evolution for c in hits}
    assert by_value == {
        "oldImpl": CrossSourceEvolution.RESOLVED,
        "newImpl": CrossSourceEvolution.INTRODUCED,
    }


def test_public_to_internal_dependency_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY in RISK_KINDS
    old = _pid_snapshot(_PID_NONE)
    new = _pid_snapshot(_PID_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY:
            assert c.effective_verdict is None


def test_public_to_internal_dependency_wired_into_compare_by_default() -> None:
    old = _pid_snapshot(_PID_NONE)
    new = _pid_snapshot(_PID_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [
        c for c in result.changes if c.kind == ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY
    ]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


class TestFourChecksNotEvaluatedCrux:
    """The correctness crux (plan §7 F-8/F-9), generalized across all four
    checks landed in this PR via ``itertools.product``, mirroring
    ``TestNotEvaluatedCrux`` above for ``unversioned_exported_symbol``: a
    finding flagged on an evaluated side must never read
    INTRODUCED/RESOLVED when its sibling side's own evidence could not
    confirm or deny it."""

    _CASES: tuple[tuple[ChangeKind, object, str, str], ...] = (
        (ChangeKind.EXPORTED_NOT_PUBLIC, _enp_snapshot, _ENP_NONE, _ENP_FLAG),
        (ChangeKind.PUBLIC_NOT_EXPORTED, _pne_snapshot, _PNE_NONE, _PNE_FLAG),
        (ChangeKind.RTTI_FOR_INTERNAL_TYPE, _rtti_snapshot, _RTTI_NONE, _RTTI_FLAG),
        (
            ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY,
            _pid_snapshot,
            _PID_NONE,
            _PID_FLAG,
        ),
    )

    @pytest.mark.parametrize(
        "kind,builder,none_state,flag_state",
        _CASES,
        ids=lambda v: getattr(v, "value", v),
    )
    @pytest.mark.parametrize("swap_sides", [False, True])
    def test_never_introduced_or_resolved_when_a_side_lacks_evidence(
        self, kind, builder, none_state: str, flag_state: str, swap_sides: bool
    ) -> None:
        no_evidence = builder(none_state)
        flagged = builder(flag_state)
        old, new = (flagged, no_evidence) if swap_sides else (no_evidence, flagged)
        changes = compute_cross_source_evolution(old, new)
        hits = [c for c in changes if c.kind == kind]
        assert len(hits) == 1, f"{kind.value}: expected exactly one finding"
        assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
        assert hits[0].cross_source_evolution not in (
            CrossSourceEvolution.INTRODUCED,
            CrossSourceEvolution.RESOLVED,
        )
