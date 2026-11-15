"""Determinism of abiforge's generation and expectations (plan §6.5).

Two runs of one cell with one input give the same expectation and the same
rendered seed, and cell generation order does not change any expectation.
The in-process checks are fast and in the default lane. The cross-process
check runs the same computation under several ``PYTHONHASHSEED`` values, the
way a set-ordering leak would surface, so it is marked ``slow``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from abiforge.model.config import Config, default_config
from abiforge.model.expect import Expectation
from abiforge.model.opkit import EditOp
from abiforge.model.seeds import SEEDS, Seed
from abiforge.operators import OPERATORS, applicable, matrix
from abiforge.run.toolchains import Toolchain

GCC = Toolchain("gcc", 0, "gcc", "g++")
REPO = Path(__file__).resolve().parent.parent


def _configs() -> list[Config]:
    return [default_config("c", GCC), default_config("c++", GCC)]


def _expectation_key(exp: Expectation) -> str:
    """A canonical text for an Expectation: set members sorted, so the key is order-free."""
    return json.dumps(
        {
            "expected": sorted(exp.expected),
            "any_of": sorted(sorted(group) for group in exp.any_of),
            "verdict_min": exp.verdict_min,
            "verdict_max": exp.verdict_max,
        },
        sort_keys=True,
    )


def test_matrix_is_stable_across_calls() -> None:
    first = [(op.name, seed.name) for op, seed in matrix()]
    second = [(op.name, seed.name) for op, seed in matrix()]
    assert first == second
    assert len(first) == len(set(first)), "a pair appears twice in the matrix"


@pytest.mark.parametrize("seed", SEEDS, ids=lambda s: s.name)
def test_render_is_stable(seed: Seed) -> None:
    assert seed.render() == seed.render()
    assert seed.render_extra() == seed.render_extra()


EDIT_PAIRS = [(op, seed) for op in OPERATORS for seed in SEEDS if op.applies_to(seed)]


@pytest.mark.parametrize(("op", "seed"), EDIT_PAIRS, ids=lambda p: p.name)
def test_edit_is_stable(op: EditOp, seed: Seed) -> None:
    assert op.edit(dict(seed.roles)) == op.edit(dict(seed.roles))


def test_expectations_are_stable_across_calls() -> None:
    for config in _configs():
        for op in OPERATORS:
            keys = {_expectation_key(op.expect(config)) for _ in range(3)}
            assert len(keys) == 1, (op.name, config.std.name)


def test_expectations_do_not_depend_on_generation_order() -> None:
    """The same expectation for each operator whether it is evaluated first or last."""
    for config in _configs():
        forward = {op.name: _expectation_key(op.expect(config)) for op in OPERATORS}
        backward = {
            op.name: _expectation_key(op.expect(config)) for op in reversed(OPERATORS)
        }
        assert forward == backward, config.std.name


def test_applicable_order_is_the_table_order() -> None:
    for seed in SEEDS:
        names = [op.name for op in applicable(seed)]
        table = [op.name for op in OPERATORS if op in applicable(seed)]
        assert names == table, seed.name


_CHILD = """
import json, sys
from abiforge.model.config import default_config
from abiforge.model.seeds import SEEDS
from abiforge.operators import OPERATORS, matrix
from abiforge.run.toolchains import Toolchain
from tests.test_abiforge_determinism import _expectation_key
gcc = Toolchain("gcc", 0, "gcc", "g++")
out = {
    "matrix": [[op.name, seed.name] for op, seed in matrix()],
    "renders": {seed.name: seed.render() for seed in SEEDS},
    "expectations": {
        cfg.std.name: {op.name: _expectation_key(op.expect(cfg)) for op in OPERATORS}
        for cfg in (default_config("c", gcc), default_config("c++", gcc))
    },
}
json.dump(out, sys.stdout, sort_keys=True)
"""


@pytest.mark.slow
def test_generation_is_identical_across_hash_seeds() -> None:
    """The matrix, the rendered seeds and the expectations under three hash seeds."""
    outputs = []
    for hash_seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(REPO)}
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO,
            check=False,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2], "output depends on PYTHONHASHSEED"
    assert json.loads(outputs[0])["matrix"], "the child produced an empty matrix"
