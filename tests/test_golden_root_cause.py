"""Golden regression tests for `to_markdown(..., report_mode="root-cause")`.

Reuses `test_golden_output.py`'s own fixture-building helpers so the golden
suites can't independently drift in how a case is constructed, but pins
`--report-mode root-cause`'s output. Added alongside the ADR-061 Phase 2
structural rewrite of `reporter_markdown.py`'s root-cause grouping (see
`abicheck/report/render_markdown.py`'s `RootCauseSectionData`/
`render_root_cause_section`) -- `--report-mode root-cause` had no golden
coverage before this file, so this is the pre-refactor baseline the rewrite
is checked against (verified byte-identical against the pre-refactor code
path before being captured), not a regression test for a reported bug.

Usage:
  pytest tests/test_golden_root_cause.py
  pytest tests/test_golden_root_cause.py --update-goldens
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.reporter_markdown import to_markdown
from tests.test_golden_output import _fn, _snap

TESTS_DIR = Path(__file__).parent
GOLDEN_DIR = TESTS_DIR / "golden" / "root_cause"


def _run_golden(case_id: str, old, new, update: bool) -> None:
    golden_path = GOLDEN_DIR / f"{case_id}.md"
    result = compare(old, new)
    actual = to_markdown(result, report_mode="root-cause")

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
def test_golden_root_cause_no_change(update_goldens: bool) -> None:
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("no_change", old, new, update_goldens)


@pytest.mark.golden
def test_golden_root_cause_func_removed(update_goldens: bool) -> None:
    old = _snap(
        ver="1.0",
        funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")],
    )
    new = _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")])
    _run_golden("func_removed", old, new, update_goldens)


@pytest.mark.golden
def test_golden_root_cause_compatible_addition(update_goldens: bool) -> None:
    old = _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")])
    new = _snap(
        ver="2.0",
        funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")],
    )
    _run_golden("compatible_addition", old, new, update_goldens)


@pytest.mark.golden
def test_golden_root_cause_struct_size_change(update_goldens: bool) -> None:
    from abicheck.model import RecordType, TypeField

    old = _snap(
        ver="1.0",
        types=[
            RecordType(
                name="Point",
                kind="struct",
                size_bits=64,
                fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)],
            )
        ],
    )
    new = _snap(
        ver="2.0",
        types=[
            RecordType(
                name="Point",
                kind="struct",
                size_bits=96,
                fields=[
                    TypeField("x", "int", 0),
                    TypeField("y", "int", 32),
                    TypeField("z", "int", 64),
                ],
            )
        ],
    )
    _run_golden("struct_size_change", old, new, update_goldens)
