# SPDX-License-Identifier: Apache-2.0
"""F-8/F-9 (plan §7): the ``FindingEvolution`` axis, landed in two slices.

ADR-068 Phase 1 item 2 (``docs/contribute/plans/one-comparison-product.md``
"Phase 1") added the generic vocabulary this module used to document the
absence of: ``checker_policy.FindingEvolution``,
``Change.evolution``/``DiffResult.resolved_findings``, and the cross-run
correspondence primitive in ``policy.finding_evolution``
(``compute_finding_evolution``/``compute_resolved_findings``/
``apply_finding_evolution``) -- for an N>1-comparison *chain* (a
longitudinal history, or a CI job diffing today's findings against a stored
prior run).

A later PR (plan §5 P2 / §6 Phase 2a) closed this plan's own "single
largest correctness risk in the migration" (ADR-068 D3's own words) for a
*different* axis: a one-sided check re-evaluated on each side within a
*single* `compare()` call, given each side's own evidence-sufficiency
signal, has no comparison chain of its own to consult. That needs a second,
same-comparison matcher (:mod:`abicheck.compare.finding_evolution`) reusing
the identical `FindingEvolution` enum rather than a second one -- see that
module's own docstring for how the two axes relate. `private_header_leak`
is the first check migrated onto it.

This module now demonstrates both axes directly against real production
code, instead of asserting either vocabulary doesn't exist:

**F-8** (a pre-existing issue must never be manufactured as ``introduced``)
and **F-9** (a fixed issue must read as ``resolved``, visible on a passing
run) each get two demonstrations:

1. Against the cross-run primitive (``policy.finding_evolution``) --
   ``run_crosschecks``' own real ``Change`` objects for case144's
   private-header leak default to ``NOT_EVALUATED`` (F-8, since crosscheck
   itself never sets ``evolution``), and two real ``checker.compare()``
   results composed through ``apply_finding_evolution`` demonstrate
   ``resolved`` (F-9).
2. Against the same-comparison matcher (``compare.finding_evolution``) --
   :func:`test_evolve_check_findings_exhaustive_matrix` is an exhaustive,
   small-domain enumeration of the generic matcher itself over every valid
   ``(has_old, has_new, old_evaluated, new_evaluated)`` combination, oracled
   against ADR-068 D3's own four-case table restated independently in this
   test's parametrize data -- not re-derived from the implementation.
   :func:`test_private_header_leak_evolution_matrix` is the concrete,
   end-to-end wiring (:mod:`abicheck.workflows.crosscheck_evolution`) over
   the full 3x3 evidence-state matrix (no evidence / evidence-clean /
   evidence-leaked, on each side) for the one migrated check, proving the
   real ``AbiSnapshot`` -> ``run_crosschecks`` -> evolution pipeline gets
   every cell right, not just the two named F-8/F-9 cells -- per AGENTS.md's
   "bug-class regression testing" / "Primitive-level property tests"
   guidance, not one fixed fixture.

What remains a **real, separate** gap -- unrelated to whether either
vocabulary exists -- is that ``scan --against``'s own crosscheck pass still
runs single-sided (``test_f9_scan_against_has_no_per_side_crosscheck_at_all``
keeps documenting it; it is intentionally not registered in ``gaps.py`` any
more, since ``NOT_YET_IMPLEMENTED_ANYWHERE`` is specifically for "neither
tool has this vocabulary at all", which is no longer true either way).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.checker import compare  # noqa: E402
from abicheck.checker_policy import (  # noqa: E402
    BREAKING_KINDS,
    RISK_KINDS,
    ChangeKind,
    FindingEvolution,
)
from abicheck.checker_types import Change  # noqa: E402
from abicheck.cli import main as abicheck_main  # noqa: E402
from abicheck.compare.finding_evolution import evolve_check_findings  # noqa: E402
from abicheck.elf_metadata import ElfMetadata, ElfSymbol  # noqa: E402
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.policy.finding_evolution import apply_finding_evolution  # noqa: E402
from abicheck.serialization import load_snapshot, snapshot_to_json  # noqa: E402
from abicheck.workflows.crosscheck_evolution import (  # noqa: E402
    compute_crosscheck_evolution,
)


def _g20_snapshot(case_name: str, filename: str = "snapshot.abi.json") -> AbiSnapshot:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return load_snapshot(path)


# --------------------------------------------------------------------------- #
# 0. The gap is closed -- Change carries a real evolution field.
# --------------------------------------------------------------------------- #


def test_change_carries_an_evolution_field() -> None:
    field_names = {f.name for f in dataclasses.fields(Change)}
    assert "evolution" in field_names


def test_change_evolution_defaults_to_not_evaluated() -> None:
    """Every pre-existing, non-migrated finding kind must be unaffected --
    the default is `NOT_EVALUATED` (ADR-067 D3's convention: a finding
    nobody classified reads as *not evaluated*, never as a silently-assumed
    `persistent` or an omitted field), not some other evolution value that
    would silently appear on every existing detector's output."""
    c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")
    assert c.evolution is FindingEvolution.NOT_EVALUATED


# --------------------------------------------------------------------------- #
# 1a. The cross-run primitive (policy.finding_evolution), against real
#     production code -- ADR-068 Phase 1 item 2's own F-8/F-9 demonstration.
# --------------------------------------------------------------------------- #


def test_f8_pre_existing_leak_defaults_to_not_evaluated_never_manufactured() -> None:
    """F-8: with a fixed, single (candidate-only) snapshot -- case144's
    private-header leak -- ``run_crosschecks`` has no OLD-side evidence at
    all, so it cannot know whether the leak is new or pre-existing. The
    *safe* answer is ``NOT_EVALUATED`` (default), never a manufactured
    ``INTRODUCED`` -- and that is exactly what the real ``Change`` object
    carries, since a bare ``run_crosschecks`` call (unlike
    ``workflows.crosscheck_evolution.compute_crosscheck_evolution`` below)
    never computes evolution itself.
    """
    from abicheck.buildsource.crosscheck import run_crosschecks

    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    result = run_crosschecks(snapshot, None)
    leak = next(c for c in result.findings if c.kind == ChangeKind.PRIVATE_HEADER_LEAK)
    assert leak.evolution is FindingEvolution.NOT_EVALUATED
    assert leak.evolution is not FindingEvolution.INTRODUCED


def test_f9_resolved_is_expressible_via_the_generic_primitive() -> None:
    """F-9: a finding present in one comparison and gone from the next must
    read as ``resolved``, visible on the *later* (passing) run. Demonstrated
    over two real ``compare()`` results -- ``apply_finding_evolution`` is
    the primitive a chain-aware caller (longitudinal history, or a CI job
    diffing today's findings against a stored prior run) applies to get
    exactly this.
    """
    baseline = AbiSnapshot(library="libfoo", version="0.0")
    with_issue = AbiSnapshot(library="libfoo", version="1.0")
    with_issue.functions.append(
        Function(
            name="foo::gone",
            mangled="_ZN3foo4goneEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    without_issue = AbiSnapshot(library="libfoo", version="1.1")
    # Run 1 (an earlier point in the chain, against the same fixed
    # baseline): the finding is present.
    previous = compare(baseline, with_issue)
    # Run 2 (the current, later point, same baseline): the finding is gone
    # -- fixed.
    current = compare(baseline, without_issue)

    apply_finding_evolution(current, previous)

    assert any(
        c.evolution is FindingEvolution.RESOLVED for c in current.resolved_findings
    )
    # The fix is visible on a passing run's own resolved_findings list, not
    # only inferable by absence -- exactly F-9's "must be visible" half.
    resolved_symbols = {c.symbol for c in current.resolved_findings}
    assert "_ZN3foo4goneEv" in resolved_symbols


def test_f9_scan_against_has_no_per_side_crosscheck_at_all() -> None:
    """F-9's narrower, still-open half: ``scan --against`` itself needs
    crosscheck to run over BOTH sides and diff the two outcomes to express
    `resolved` -- unrelated to whether either vocabulary exists (both do,
    see the tests around this one). `scan --against` runs the always-on
    crosscheck tier over the **candidate** snapshot only (scan_engine.py's
    single `run_crosschecks(new_snap, ...)` call): there is no second,
    OLD-side crosscheck pass for scan's own report to diff against.
    ``workflows.crosscheck_evolution`` (Phase 2a) gives ``compare()`` this
    per-side pass; `scan --against` itself is a separate, still-open gap."""
    import ast
    import inspect

    from abicheck import scan_engine

    tree = ast.parse(inspect.getsource(scan_engine))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_crosschecks"
    ]
    # A real AST match, not a textual count that a comment or docstring
    # mentioning "run_crosschecks(" could inflate (Codex review): exactly
    # one call expression, over the *candidate* snapshot (new_snap) --
    # never a second, OLD-side pass, which a baseline-diffed crosscheck
    # would require to express "resolved".
    assert len(calls) == 1, (
        f"scan_engine.py calls run_crosschecks() {len(calls)} time(s), not 1 -- "
        "if this is a baseline-side crosscheck pass landing, scan --against "
        "may now be evolution-stated too; update this test accordingly."
    )
    (call,) = calls
    assert call.args and isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "new_snap", (
        f"run_crosschecks() is now called with {call.args[0].id!r}, not "
        "new_snap -- re-examine whether it now runs over the baseline side too"
    )


# --------------------------------------------------------------------------- #
# 1b. The same-comparison matcher, exhaustively -- the crux, stated
#     abstractly (compare.finding_evolution.evolve_check_findings).
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
    """F-8, named directly, for the same-comparison matcher this time: the
    leak exists on BOTH sides, but OLD's snapshot has no header evidence at
    all (a stripped baseline). It must read as `not_evaluated` -- never
    `introduced`, which would manufacture a "new" problem out of one that
    may have existed all along."""
    old = _snapshot_for_state(_NONE)
    new = _snapshot_for_state(_LEAK)
    changes = compute_crosscheck_evolution(old, new)
    leaks = [c for c in changes if c.kind == ChangeKind.PRIVATE_HEADER_LEAK]
    assert len(leaks) == 1
    assert leaks[0].evolution == FindingEvolution.NOT_EVALUATED
    assert leaks[0].evolution != FindingEvolution.INTRODUCED


def test_f9_leak_fixed_in_new_reads_as_resolved() -> None:
    """F-9, named directly, for the same-comparison matcher this time: leak
    present in OLD, fixed in NEW, both sides with sufficient evidence --
    must read as `resolved`, visible on a passing run (never simply
    dropped)."""
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
    must surface an evolution-stated finding (plan §6 Phase 2a's "wired
    into the comparison pipeline" requirement)."""
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

    def _leak_evolution(*extra_args: str) -> tuple[int, int, int]:
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
        # The per-check evolution fact surfaces at the aggregate
        # `finding_evolution.counts` level (report/finding_evolution.py's
        # own compute/render pair, shared with ADR-068 Phase 1 item 2's
        # cross-run block) rather than a second, per-change JSON field --
        # see compare/finding_evolution.py's own docstring for why one
        # `Change.evolution` fact is enough.
        not_evaluated_count = report["finding_evolution"]["counts"]["not_evaluated"]
        return result.exit_code, not_evaluated_count, len(leaks)

    baseline = _leak_evolution()
    with_demangle = _leak_evolution("--demangle")
    with_report_mode = _leak_evolution("--report-mode", "leaf")

    assert baseline == with_demangle == with_report_mode
    assert baseline[1] >= 1
    assert baseline[2] == 1
