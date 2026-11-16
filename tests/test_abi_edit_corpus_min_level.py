"""Minimum-level cells: each edit is caught at the lowest evidence that can show it (plan B2).

For every (operator, seed) of the default matrix, the minimal evidence sets come
from the kinds' declared ``observable_at`` (``abiforge.model.observable``). Two
cell kinds run against the gcc producer:

- *at-minimum*: the edit must be caught at exactly its minimal evidence;
- *below-minimum*: at evidence that cannot show it, abicheck must not claim a
  break the evidence cannot see (the reverse check).

A cell that disagrees with its ground truth is a detector finding: it is
recorded in ``abiforge/known.py`` (a strict xfail with the exact issue set),
as the matrix tests do. A minimal set the corpus cannot build, and an
UNCLASSIFIED kind, are reported as skips with their reason, never silently
dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abiforge.known import known_mismatch
from abiforge.model.config import default_config
from abiforge.model.expect import PASSING_CLASSES, issue_keys
from abiforge.model.observable import min_level_cells, unclassified_kinds
from abiforge.model.seeds import SEEDS
from abiforge.operators import OPERATORS, matrix
from abiforge.run.cells import CellContext, run_cell
from abiforge.run.toolchains import toolchain

# Nightly, non-gating (plan B2): the PR tier never runs these. Their red set is the
# committed baseline in tests/data/min_level_red_cells.json (a ratchet).
pytestmark = pytest.mark.edit_corpus_nightly

_BASELINE = Path(__file__).resolve().parent / "data" / "min_level_red_cells.json"
_RED = json.loads(_BASELINE.read_text(encoding="utf-8"))["cells"]

PRODUCER = toolchain("gcc", 13)


def _base(seed):
    return default_config(seed.lang, PRODUCER)


def _applies(op, seed) -> bool:
    return op.requires(_base(seed)) is None and op.applies_to(seed)


def _params():
    params = []
    for op, seed in matrix():
        if not _applies(op, seed):
            continue
        cells, unsupported = min_level_cells(op, _base(seed))
        unclassified = sorted(unclassified_kinds(op))
        for atoms in unsupported:
            params.append(
                pytest.param(
                    op,
                    seed,
                    None,
                    "unsupported",
                    id=f"{op.name}-{seed.name}-{''.join(sorted(atoms))}-unsupported",
                )
            )
        for cell in cells:
            params.append(
                pytest.param(
                    op,
                    seed,
                    cell,
                    cell.role,
                    id=f"{op.name}-{seed.name}-{''.join(sorted(cell.atoms))}-{cell.role}",
                )
            )
        if unclassified:
            params.append(
                pytest.param(
                    op,
                    seed,
                    None,
                    "unclassified",
                    id=f"{op.name}-{seed.name}-unclassified",
                )
            )
    return params


_PARAMS = _params()


@pytest.mark.parametrize("op,seed,cell,role", _PARAMS)
def test_min_level_cell(op, seed, cell, role, tmp_path, request) -> None:
    if role == "unsupported":
        pytest.skip(
            f"{op.name}: a minimal evidence set the corpus does not build (reported)"
        )
    if role == "unclassified":
        pytest.skip(
            f"{op.name}: unclassified kinds {sorted(unclassified_kinds(op))} "
            "get no floor cell (reported)"
        )
    result = run_cell(op, seed, cell.config, CellContext.at(tmp_path))
    if result.status != "ran":
        pytest.skip(f"{result.status}: {result.reason}")
    known = known_mismatch(op.name, seed.name, cell.config)
    if known is not None:
        issues = issue_keys(result.reconciled.problems if result.reconciled else ())
        executed = bool(result.exec_truth and result.exec_truth.available)
        assert known.matches(result.cls, issues, result.verdict, executed), (
            f"recorded {known.cls} no longer reproduces: got {result.cls}"
        )
        pytest.xfail(f"{known.cls}: {known.reason}")
    cell_id = request.node.callspec.id
    if result.cls in PASSING_CLASSES:
        assert cell_id not in _RED, (
            f"{cell_id} passes now: remove it from tests/data/min_level_red_cells.json"
        )
        return
    assert cell_id in _RED, (
        f"new red cell {cell_id}: {result.cls}. Triage it (E or D) and record it in "
        "tests/data/min_level_red_cells.json\n" + result.describe()
    )
    # A recorded red cell: the ratchet holds, the cell stays in the baseline.


def test_every_operator_has_a_minimal_cell_or_a_reported_reason() -> None:
    """Coverage of the plan's B2 rule: no operator is silently without a floor cell."""
    for op in OPERATORS:
        seeds = [s for s in SEEDS if op.applies_to(s)]
        if not seeds:
            continue
        cells, unsupported = min_level_cells(op, _base(seeds[0]))
        reported = bool(unsupported) or bool(unclassified_kinds(op))
        assert cells or reported, op.name
