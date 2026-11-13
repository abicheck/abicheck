"""`added_lines_by_path` numbers each added line by its new-side position.

Bug class: a line scanner whose position counter ignores one line kind. The
hunk walker advanced only on `+` lines, so a diff with context lines numbered
every later addition too low and the docstring check read the wrong lines.
Oracle: diffs generated from known old/new files, where an added line's
number is its index in the *new file* -- computed without the gate.
"""

from __future__ import annotations

import random

import pytest

from tests._bugfix_contract_gate import gate


def _diff_for(path: str, new: list[str], kinds: list[str]) -> str:
    """One hunk turning *kinds* ('ctx'|'add'|'del') into a unified diff."""
    old_n = sum(k != "add" for k in kinds)
    new_n = sum(k != "del" for k in kinds)
    body: list[str] = []
    it = iter(new)
    for k in kinds:
        if k == "del":
            body.append("-removed")
        else:
            body.append(("+" if k == "add" else " ") + next(it))
    head = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -1,{old_n} +1,{new_n} @@",
    ]
    return "\n".join(head + body) + "\n"


@pytest.mark.parametrize("seed", range(40))
def test_added_line_numbers_match_the_new_file(seed: int) -> None:
    rng = random.Random(seed)
    kinds = [rng.choice(["ctx", "add", "del"]) for _ in range(rng.randint(1, 30))]
    new = [f"line{i}" for i in range(sum(k != "del" for k in kinds))]
    expected = [
        (i + 1, text)
        for i, (text, k) in enumerate(zip(new, [k for k in kinds if k != "del"]))
        if k == "add"
    ]
    got = gate.added_lines_by_path(_diff_for("tests/test_x.py", new, kinds))
    assert got.get("tests/test_x.py", []) == expected


def test_zero_context_diff_is_unchanged() -> None:
    diff = _diff_for("tests/test_x.py", ["a", "b"], ["add", "add"])
    assert gate.added_lines_by_path(diff) == {"tests/test_x.py": [(1, "a"), (2, "b")]}
