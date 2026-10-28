"""A WEAK-dominated removal list says so, without changing its verdict.

**Bug class:** evidence the report holds but does not surface. oneDAL
2026.0→2026.1 binary-only reported ~1,744 ``core`` removals as breaking;
every one was WEAK (vague linkage), which the per-finding JSON carried as
``symbol_binding`` but the Markdown never summarised. Demoting them is not
the fix -- it was tried twice and reverted (``extern template`` consumers
do break) -- so the invariant is disclosure only.

**General invariant:** over every mix of binding (weak / global / unique /
unobserved) and kind (removal / non-removal), the note counts exactly the
removals with an observed WEAK binding against all removals with an
observed binding, appears iff that weak count is nonzero, and never
changes the verdict or the breaking count. The oracle is a direct count
over the inputs' own fields.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.report.weak_removals import weak_removal_counts, weak_removal_note_lines
from abicheck.reporter import to_markdown

_BINDINGS = ("weak", "global", "unique", None)
_KINDS = (
    (ChangeKind.FUNC_REMOVED_ELF_ONLY, True),
    (ChangeKind.FUNC_REMOVED, True),
    (ChangeKind.VAR_REMOVED, True),
    (ChangeKind.FUNC_RETURN_CHANGED, False),
)
_CASES = list(
    itertools.combinations_with_replacement(itertools.product(_BINDINGS, _KINDS), 3)
)


def _changes(case) -> list[Change]:
    return [
        Change(kind=kind, symbol=f"s{i}", description="d", symbol_binding=binding)
        for i, (binding, (kind, _)) in enumerate(case)
    ]


@pytest.mark.parametrize("case", _CASES)
def test_counts_match_a_direct_count(case) -> None:
    observed = [b for b, (_, removal) in case if removal and b]
    assert weak_removal_counts(_changes(case)) == (
        observed.count("weak"),
        len(observed),
    )
    assert bool(weak_removal_note_lines(_changes(case))) == ("weak" in observed)


@pytest.mark.parametrize("weak", [0, 1, 5])
def test_markdown_discloses_without_reclassifying(weak: int) -> None:
    changes = [
        Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol=f"w{i}",
            description="d",
            symbol_binding="weak",
        )
        for i in range(weak)
    ] + [
        Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol="g",
            description="d",
            symbol_binding="global",
        )
    ]
    md = to_markdown(
        DiffResult(
            old_version="1",
            new_version="2",
            library="l",
            changes=changes,
            verdict=Verdict.BREAKING,
        )
    )
    assert f"| Breaking changes | {weak + 1} |" in md
    note = (
        f"**{weak} of {weak + 1} removed export(s) with an observed binding were WEAK**"
    )
    assert (note in md) == bool(weak)
