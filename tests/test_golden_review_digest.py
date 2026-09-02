"""Golden regression tests for `to_review_digest` (the `--format review` path).

Reuses `test_golden_output.py`'s own fixture-building helpers so the two
golden suites can't independently drift in how a case is constructed, but
pins `reporter_markdown.to_review_digest`'s output rather than
`to_markdown`'s. Added alongside the ADR-061 Phase 2 structural rewrite of
`reporter_markdown.py` (see `abicheck/report/render_markdown.py`) --
`to_review_digest` had no golden coverage before this file, so this is the
pre-refactor baseline the rewrite is checked against, not a regression test
for a reported bug.

Usage:
  pytest tests/test_golden_review_digest.py
  pytest tests/test_golden_review_digest.py --update-goldens
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.reporter_markdown import to_review_digest
from tests.test_golden_output import _fn, _snap

TESTS_DIR = Path(__file__).parent
GOLDEN_DIR = TESTS_DIR / "golden" / "review"


def _run_golden(case_id: str, old, new, update: bool) -> None:
    golden_path = GOLDEN_DIR / f"{case_id}.md"
    result = compare(old, new)
    actual = to_review_digest(result)

    if update:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual, encoding="utf-8")
        pytest.skip(f"Updated golden: {golden_path.name}")
        return

    if not golden_path.exists():
        pytest.fail(
            f"Golden file missing: {golden_path}\n"
            f"Run with --update-goldens to create it.\n"
            f"Current output:\n{actual}"
        )

    expected = golden_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"{case_id}.expected",
                tofile=f"{case_id}.actual",
                n=3,
            )
        )
        pytest.fail(f"Golden mismatch for {case_id}:\n{diff}")


@pytest.mark.golden
def test_golden_review_no_change(update_goldens: bool) -> None:
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("no_change", old, new, update_goldens)


@pytest.mark.golden
def test_golden_review_func_removed(update_goldens: bool) -> None:
    old = _snap(
        ver="1.0",
        funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")],
    )
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("func_removed", old, new, update_goldens)


@pytest.mark.golden
def test_golden_review_compatible_addition(update_goldens: bool) -> None:
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(
        ver="2.0",
        funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")],
    )
    _run_golden("compatible_addition", old, new, update_goldens)
