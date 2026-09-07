# SPDX-License-Identifier: Apache-2.0
"""F-8/F-9 (plan §7): the ``FindingEvolution`` axis, landed (plan §5 P2 /
ADR-068 D3, this PR).

F-8: a private-header leak present in **both** OLD and NEW, with OLD's
evidence too weak to have proven the leak at the time, must read as
``not_evaluated`` -- never as ``introduced`` (that would be a manufactured
finding, ADR-068's "second risk"). F-9: a leak present in OLD and fixed in
NEW must read as ``resolved``, and that must be visible on a passing run.

**The correctness crux** (ADR-068 D3's own words: "the single largest
correctness risk in the migration"): ``not_evaluated`` is mandatory, not a
convenience. This module proves it two ways, per AGENTS.md's "bug-class
regression testing" / "Primitive-level property tests" guidance --
not one fixed fixture:

1. :func:`test_evolve_check_findings_exhaustive_matrix` -- an exhaustive,
   small-domain enumeration of the generic matcher
   (:func:`abicheck.compare.finding_evolution.evolve_check_findings`)
   itself, over every valid ``(has_old, has_new, old_evaluated,
   new_evaluated)`` combination, oracled against ADR-068 D3's own four-case
   table restated independently in this test's parametrize data -- not
   re-derived from the implementation.
2. :func:`test_private_header_leak_evolution_matrix` -- the concrete,
   end-to-end wiring (:mod:`abicheck.workflows.crosscheck_evolution`) over
   the full 3x3 evidence-state matrix (no evidence / evidence-clean /
   evidence-leaked, on each side) for the one migrated check, proving the
   real ``AbiSnapshot`` -> ``run_crosschecks`` -> evolution pipeline gets
   every cell right, not just the two named F-8/F-9 cells.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker_policy import BREAKING_KINDS, RISK_KINDS, ChangeKind
from abicheck.checker_types import Change
from abicheck.cli import main as abicheck_main
from abicheck.compare.finding_evolution import evolve_check_findings
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin
from abicheck.model.finding_evolution import FindingEvolution
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.crosscheck_evolution import compute_crosscheck_evolution

# --------------------------------------------------------------------------- #
# 0. The gap is closed -- Change carries a real evolution field.
# --------------------------------------------------------------------------- #


def test_change_carries_an_evolution_field() -> None:
    field_names = {f.name for f in dataclasses.fields(Change)}
    assert "evolution" in field_names


def test_change_evolution_defaults_to_none() -> None:
    """Every pre-existing, non-migrated finding kind must be unaffected --
    the default is `None`, not some evolution value that would silently
    appear on every existing detector's output."""
    c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")
    assert c.evolution is None


# --------------------------------------------------------------------------- #
# 1. The generic matcher, exhaustively -- the crux, stated abstractly.
# --------------------------------------------------------------------------- #


def _change(symbol: str = "s", new_value: str | None = "Impl") -> Change:
    return Change(
        kind=ChangeKind.PRIVATE_HEADER_LEAK,
        symbol=symbol,
        description="d",
        new_value=new_value,
    )


#: Every valid (has_old, has_new, old_evaluated, new_evaluated) combination,
#: restricted to the correlation every real check upholds (a check never
#: emits a finding without sufficient evidence -- has_old => old_evaluated,
#: has_new => new_evaluated), with the expected evolution independently
#: restated from ADR-068 D3's own table -- not derived from
#: evolve_check_findings's own source. `None` means "no finding emitted at
#: all" (absent on both sides).
_MATCHER_MATRIX: list[tuple[bool, bool, bool, bool, FindingEvolution | None]] = [
    # Absent on both sides -- never a finding, regardless of either side's
    # own evidence sufficiency.
    (False, False, True, True, None),
    (False, False, True, False, None),
    (False, False, False, True, None),
    (False, False, False, False, None),
    # Present on NEW only.
    (False, True, True, True, FindingEvolution.INTRODUCED),
    # THE CRUX: OLD's evidence was insufficient -- must not read as new.
    (False, True, False, True, FindingEvolution.NOT_EVALUATED),
    # Present on OLD only.
    (True, False, True, True, FindingEvolution.RESOLVED),
    # THE CRUX's sibling: NEW's evidence was insufficient -- must not read
    # as silently fixed.
    (True, False, True, False, FindingEvolution.NOT_EVALUATED),
    # Present on both sides.
    (True, True, True, True, FindingEvolution.PERSISTENT),
]


@pytest.mark.parametrize(
    "has_old, has_new, old_evaluated, new_evaluated, expected",
    _MATCHER_MATRIX,
    ids=[
        f"old={ho},new={hn},old_eval={oe},new_eval={ne}"
        for ho, hn, oe, ne, _ in _MATCHER_MATRIX
    ],
)
def test_evolve_check_findings_exhaustive_matrix(
    has_old: bool,
    has_new: bool,
    old_evaluated: bool,
    new_evaluated: bool,
    expected: FindingEvolution | None,
) -> None:
    change = _change()
    old_findings = [change] if has_old else []
    new_findings = [change] if has_new else []
    result = evolve_check_findings(
        old_evaluated=old_evaluated,
        old_findings=old_findings,
        new_evaluated=new_evaluated,
        new_findings=new_findings,
    )
    if expected is None:
        assert result == []
    else:
        assert len(result) == 1
        assert result[0].evolution == expected
        # Authority is never touched by this module (ADR-028 D3/ADR-035 D1).
        assert result[0].kind == ChangeKind.PRIVATE_HEADER_LEAK


def test_evolve_check_findings_never_conflates_not_evaluated_with_no_finding() -> None:
    """A dedicated, explicit restatement of the crux (beyond the matrix
    above): a finding present on exactly one side with the OTHER side
    unevaluated is never simply dropped -- it survives as a real
    `not_evaluated` `Change`, not silence."""
    change = _change()
    result = evolve_check_findings(
        old_evaluated=False,
        old_findings=[],
        new_evaluated=True,
        new_findings=[change],
    )
    assert len(result) == 1
    assert result[0].evolution == FindingEvolution.NOT_EVALUATED
    assert result[0].evolution != FindingEvolution.INTRODUCED


def test_evolve_check_findings_identity_distinguishes_findings() -> None:
    """Two findings with different identities (different leaked types) on
    the two sides must NOT be paired as one persistent finding -- each
    keeps its own evolution."""
    old_finding = _change(symbol="use", new_value="OldImpl")
    new_finding = _change(symbol="use", new_value="NewImpl")
    result = evolve_check_findings(
        old_evaluated=True,
        old_findings=[old_finding],
        new_evaluated=True,
        new_findings=[new_finding],
    )
    by_value = {c.new_value: c.evolution for c in result}
    assert by_value == {
        "OldImpl": FindingEvolution.RESOLVED,
        "NewImpl": FindingEvolution.INTRODUCED,
    }


# --------------------------------------------------------------------------- #
# 2. The concrete migrated check, over the full evidence/finding matrix.
# --------------------------------------------------------------------------- #

_NONE = "no_evidence"
_CLEAN = "evidence_clean"
_LEAK = "evidence_leaked"


def _snapshot_for_state(state: str) -> AbiSnapshot:
    """A minimal, compiler-free ``AbiSnapshot`` for one evidence/finding
    state of ``private_header_leak`` (mirrors ``tests/test_crosscheck.py``'s
    own ``test_private_header_leak_flags_public_api_exposing_private_type``
    fixture shape)."""
    if state == _NONE:
        # No header evidence at all -- crosscheck._origin_resolvable is
        # False regardless of any decl present (a stripped/no-headers dump).
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=False,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]),
        )
    has_leak = state == _LEAK
    if state not in (_CLEAN, _LEAK):
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
_EVOLUTION_MATRIX: dict[tuple[str, str], FindingEvolution | None] = {
    (_NONE, _NONE): None,
    (_NONE, _CLEAN): None,
    # F-8: pre-existing leak, baseline lacking evidence -- not_evaluated.
    (_NONE, _LEAK): FindingEvolution.NOT_EVALUATED,
    (_CLEAN, _NONE): None,
    (_CLEAN, _CLEAN): None,
    (_CLEAN, _LEAK): FindingEvolution.INTRODUCED,
    # Sibling of F-9: candidate lacks evidence -- can't confirm "resolved".
    (_LEAK, _NONE): FindingEvolution.NOT_EVALUATED,
    # F-9: leak present in OLD, fixed in NEW.
    (_LEAK, _CLEAN): FindingEvolution.RESOLVED,
    (_LEAK, _LEAK): FindingEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_EVOLUTION_MATRIX))
def test_private_header_leak_evolution_matrix(old_state: str, new_state: str) -> None:
    old = _snapshot_for_state(old_state)
    new = _snapshot_for_state(new_state)
    changes = compute_crosscheck_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    expected = _EVOLUTION_MATRIX[(old_state, new_state)]
    if expected is None:
        assert leaks == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(leaks) == 1, (
            f"expected exactly one finding for {old_state, new_state}"
        )
        assert leaks[0].evolution == expected


def test_f8_pre_existing_leak_on_evidence_less_baseline_is_not_evaluated() -> None:
    """F-8, named directly: the leak exists on BOTH sides, but OLD's
    snapshot has no header evidence at all (a stripped baseline). It must
    read as `not_evaluated` -- never `introduced`, which would manufacture
    a "new" problem out of one that may have existed all along."""
    old = _snapshot_for_state(_NONE)
    new = _snapshot_for_state(_LEAK)
    changes = compute_crosscheck_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].evolution == FindingEvolution.NOT_EVALUATED
    assert leaks[0].evolution != FindingEvolution.INTRODUCED


def test_f9_leak_fixed_in_new_reads_as_resolved() -> None:
    """F-9, named directly: leak present in OLD, fixed in NEW, both sides
    with sufficient evidence -- must read as `resolved`, visible on a
    passing run (never simply dropped)."""
    old = _snapshot_for_state(_LEAK)
    new = _snapshot_for_state(_CLEAN)
    changes = compute_crosscheck_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].evolution == FindingEvolution.RESOLVED


# --------------------------------------------------------------------------- #
# 3. Wired into compare()'s own pipeline end to end.
# --------------------------------------------------------------------------- #


def test_wired_into_compare_pipeline_end_to_end() -> None:
    """Not just the ``workflows`` helper -- ``checker.compare()`` itself
    must surface an evolution-stated finding (plan item 2's "wired into the
    comparison pipeline" requirement)."""
    from abicheck.checker import compare

    old = _snapshot_for_state(_NONE)
    new = _snapshot_for_state(_LEAK)
    result = compare(old, new, scope_to_public_surface=False)
    leaks = [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].evolution == FindingEvolution.NOT_EVALUATED


def test_compare_with_no_header_evidence_at_all_emits_nothing_new() -> None:
    """No regression for the overwhelmingly common case: a plain
    binary-only comparison with no header evidence on either side must not
    gain any new finding just because this stage now runs unconditionally."""
    from abicheck.checker import compare

    old = _snapshot_for_state(_NONE)
    new = _snapshot_for_state(_NONE)
    result = compare(old, new, scope_to_public_surface=False)
    assert not [c for c in result.changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]


# --------------------------------------------------------------------------- #
# 4. Authority is unchanged (ADR-028 D3 / ADR-035 D1).
# --------------------------------------------------------------------------- #


def test_private_header_leak_stays_risk_never_breaking() -> None:
    assert ChangeKind.PRIVATE_HEADER_LEAK in RISK_KINDS
    assert ChangeKind.PRIVATE_HEADER_LEAK not in BREAKING_KINDS


@pytest.mark.parametrize("evolution", list(FindingEvolution))
def test_authority_unchanged_for_every_evolution_state(
    evolution: FindingEvolution,
) -> None:
    """Evolution is additive metadata, never a promotion: every one of the
    four states leaves the finding's own category exactly where it always
    was."""
    c = Change(
        kind=ChangeKind.PRIVATE_HEADER_LEAK,
        symbol="s",
        description="d",
        evolution=evolution,
    )
    assert c.kind in RISK_KINDS
    assert c.kind not in BREAKING_KINDS


# --------------------------------------------------------------------------- #
# 5. Presentation never changes analysis (ADR-068 D4, plan row F-19).
# --------------------------------------------------------------------------- #


def test_evolution_is_identical_across_format_and_view_permutations(
    tmp_path: Path,
) -> None:
    """The canonical result -- here, this finding's own `evolution` value
    and the overall `private_header_leak` finding count -- must be
    byte-identical regardless of `--format`/`--report-mode`/`--demangle`,
    per ADR-068 D4 / plan row F-19. A real end-to-end CLI invocation (not a
    call to `compare()` directly), since presentation flags are parsed at
    the CLI boundary."""
    old_path = tmp_path / "old.abi.json"
    new_path = tmp_path / "new.abi.json"
    old_path.write_text(snapshot_to_json(_snapshot_for_state(_NONE)), encoding="utf-8")
    new_path.write_text(snapshot_to_json(_snapshot_for_state(_LEAK)), encoding="utf-8")

    def _leak_evolution(*extra_args: str) -> tuple[int, str | None, int]:
        result = CliRunner().invoke(
            abicheck_main,
            [
                "compare",
                str(old_path),
                str(new_path),
                "--no-scope-public-headers",
                "--format",
                "json",
                *extra_args,
            ],
        )
        report = json.loads(result.stdout)
        leaks = [c for c in report["changes"] if c["kind"] == "private_header_leak"]
        evolution = leaks[0].get("evolution") if leaks else None
        return result.exit_code, evolution, len(leaks)

    baseline = _leak_evolution()
    with_demangle = _leak_evolution("--demangle")
    with_report_mode = _leak_evolution("--report-mode", "leaf")

    assert baseline == with_demangle == with_report_mode
    assert baseline[1] == FindingEvolution.NOT_EVALUATED.value
    assert baseline[2] == 1
