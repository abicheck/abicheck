"""Run one corpus cell end to end, minimize a failing one, print it as a test.

``run_cell(op, seed, config, ctx)`` returns a ``CellResult``:

- ``status="not-applicable"``: ``op.requires(config)`` gave a reason;
- ``status="unavailable"``: the host cannot build ``config`` (toolchain
  registry); reported as a skip, never as a pass;
- ``status="ran"``: built, compared, executed and reconciled.

``minimize`` shrinks a failing cell's config one axis at a time towards
``default_config`` (and the evidence towards ``headers``), keeping every step
that reproduces the same class and problems, until no axis can move.
``regression_test`` renders the result as a pytest function ready to paste.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from tests._edit_corpus_config import Config, Flags, Toolchain, default_config
from tests._edit_corpus_exec import ExecutionOracle
from tests._edit_corpus_expect import ExecTruth, Expectation, Reconciled, reconcile
from tests._edit_corpus_harness import BaselineCache, abicheck_answer, build_new
from tests._edit_corpus_operators import EditOp
from tests._edit_corpus_seeds import Seed
from tests._edit_corpus_toolchains import unavailable


@dataclass
class CellContext:
    root: Path
    cache: BaselineCache
    oracle: ExecutionOracle
    execute: bool = True

    @classmethod
    def at(cls, root: Path, execute: bool = True) -> CellContext:
        return cls(
            root=root,
            cache=BaselineCache(root / "old"),
            oracle=ExecutionOracle(root / "exec"),
            execute=execute,
        )


@dataclass(frozen=True)
class CellResult:
    op: str
    seed: str
    config: Config
    status: str
    reason: str = ""
    verdict: str = ""
    kinds: frozenset[str] = frozenset()
    expectation: Expectation | None = None
    exec_truth: ExecTruth | None = None
    reconciled: Reconciled | None = None

    @property
    def cls(self) -> str:
        return self.reconciled.cls if self.reconciled else self.status

    def signature(self) -> tuple[str, tuple[str, ...]]:
        """What must reproduce for a minimized cell to count as the same finding."""
        return (self.cls, self.reconciled.problems if self.reconciled else ())

    def describe(self) -> str:
        if self.status != "ran":
            return f"{self.op} on {self.seed} [{self.config.id}]: {self.status} ({self.reason})"
        assert self.reconciled is not None and self.exec_truth is not None
        ex = self.exec_truth
        exec_txt = (
            f"binary_break={ex.binary_break} source_break={ex.source_break} signals={list(ex.signals)}"
            if ex.available
            else f"unavailable ({ex.reason})"
        )
        return (
            f"{self.op} on {self.seed} [{self.config.id}] -> {self.cls}\n"
            f"  abicheck: verdict={self.verdict} kinds={sorted(self.kinds)}\n"
            f"  expect:   {self.expectation}\n"
            f"  execute:  {exec_txt}\n"
            f"  problems: {list(self.reconciled.problems)} {self.reconciled.detail}"
        )


def seed_conflict(seed: Seed, config: Config) -> str | None:
    """A seed x config pair that is ill-formed on its own, whatever the edit."""
    if seed.name == "cxx_stdlib" and any(
        f.pack is not None or f.short_enums for f in (config.flags, config.consumer_fl)
    ):
        # gcc documents both flags as making code binary-incompatible with
        # code built without them, and the installed libstdc++ is built without.
        return "-fpack-struct/-fshort-enums make std::string incompatible with the installed libstdc++"
    return None


def run_cell(op: EditOp, seed: Seed, config: Config, ctx: CellContext) -> CellResult:
    reason = op.requires(config) or seed_conflict(seed, config)
    if reason or not op.applies_to(seed) or seed.lang != config.lang:
        return CellResult(
            op.name,
            seed.name,
            config,
            "not-applicable",
            reason or "seed/language mismatch",
        )
    reason = unavailable(config)
    if reason is None and op.rebuild is not None:
        reason = unavailable(replace(config, flags=op.new_flags(config.flags)))
    if reason:
        return CellResult(op.name, seed.name, config, "unavailable", reason)
    old = ctx.cache.get(seed, config)
    new = build_new(op, seed, config, ctx.root / "new")
    verdict, kinds = abicheck_answer(old, new)
    exp = op.expect(config)
    if ctx.execute:
        truth = ctx.oracle.truth(
            seed,
            config,
            old,
            new,
            op.edit(seed.roles),
            op.new_flags(config.flags),
            op.name,
        )
    else:
        truth = ExecTruth(available=False, reason="execution disabled for this run")
    return CellResult(
        op.name,
        seed.name,
        config,
        "ran",
        verdict=verdict,
        kinds=kinds,
        expectation=exp,
        exec_truth=truth,
        reconciled=reconcile(truth, exp, verdict, kinds),
    )


# --- minimization ----------------------------------------------------------


def _shrink_steps(config: Config) -> list[tuple[str, Config]]:
    """Every one-axis move of *config* towards the default config."""
    base = default_config(config.lang, config.producer)
    f, d = config.flags, Flags()
    steps: list[tuple[str, Config]] = []
    for attr in (
        "opt",
        "visibility",
        "bits",
        "short_enums",
        "pack",
        "cxx11_abi",
        "abi_version",
        "vtordisp",
    ):
        if getattr(f, attr) != getattr(d, attr):
            steps.append(
                (
                    f"flags.{attr}",
                    replace(config, flags=replace(f, **{attr: getattr(d, attr)})),
                )
            )
    if config.consumer is not None:
        steps.append(("consumer", replace(config, consumer=None)))
    if config.consumer_flags is not None:
        steps.append(("consumer_flags", replace(config, consumer_flags=None)))
    if config.std != base.std:
        steps.append(("std", replace(config, std=base.std)))
    if config.evidence != "headers":
        steps.append(("evidence", replace(config, evidence="headers")))
    return steps


def minimize(
    op: EditOp, seed: Seed, failing: CellResult, ctx: CellContext, budget: int = 24
) -> CellResult:
    """Greedy one-axis-at-a-time shrink of *failing* that keeps its signature."""
    current = failing
    want = failing.signature()
    tries = 0
    progress = True
    while progress and tries < budget:
        progress = False
        for _axis, candidate in _shrink_steps(current.config):
            tries += 1
            got = run_cell(op, seed, candidate, ctx)
            if got.status == "ran" and got.signature() == want:
                current = got
                progress = True
                break
            if tries >= budget:
                break
    return current


# --- printing --------------------------------------------------------------


def _tc_expr(tc: Toolchain) -> str:
    return f'toolchain("{tc.family}", {tc.version})'


def _flags_expr(f: Flags) -> str:
    d = Flags()
    args = [
        f"{a}={getattr(f, a)!r}"
        for a in f.__dataclass_fields__
        if getattr(f, a) != getattr(d, a)
    ]
    return f"Flags({', '.join(args)})"


def config_expr(c: Config) -> str:
    parts = [
        f'std=STANDARD_BY_NAME["{c.std.name}"]',
        f"producer={_tc_expr(c.producer)}",
    ]
    if c.flags != Flags():
        parts.append(f"flags={_flags_expr(c.flags)}")
    if c.consumer is not None:
        parts.append(f"consumer={_tc_expr(c.consumer)}")
    if c.consumer_flags is not None:
        parts.append(f"consumer_flags={_flags_expr(c.consumer_flags)}")
    if c.evidence != "headers":
        parts.append(f'evidence="{c.evidence}"')
    return "Config(" + ", ".join(parts) + ")"


REGRESSION_IMPORTS = """from pathlib import Path

import pytest

from tests._edit_corpus_cells import CellContext, run_cell
from tests._edit_corpus_config import STANDARD_BY_NAME, Config, Flags
from tests._edit_corpus_expect import PASSING_CLASSES
from tests._edit_corpus_operators import OP_BY_NAME
from tests._edit_corpus_seeds import SEED_BY_NAME
from tests._edit_corpus_toolchains import toolchain"""


def regression_test(cell: CellResult) -> str:
    """A ready-to-paste pytest regression for a (minimized) failing cell."""
    ident = f"{cell.op}_{cell.seed}_{cell.config.id}"
    safe = "".join(ch if ch.isalnum() else "_" for ch in ident).lower()
    problems = list(cell.reconciled.problems) if cell.reconciled else []
    return f'''{REGRESSION_IMPORTS}

@pytest.mark.integration
def test_edit_corpus_regression_{safe}(tmp_path: Path) -> None:
    # Found by the generated edit corpus: {cell.cls}.
    # abicheck said {cell.verdict} {sorted(cell.kinds)}; problems: {problems}
    config = {config_expr(cell.config)}
    cell = run_cell(
        OP_BY_NAME["{cell.op}"], SEED_BY_NAME["{cell.seed}"], config, CellContext.at(tmp_path)
    )
    if cell.status == "unavailable":
        pytest.skip(cell.reason)
    assert cell.cls in PASSING_CLASSES, cell.describe()
'''
