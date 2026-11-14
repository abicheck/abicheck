"""Run one corpus cell end to end, minimize a failing one, print it as a test.

``run_cell(op, seed, config, ctx)`` returns a ``CellResult``:

- ``status="not-applicable"``: ``op.requires(config)`` gave a reason;
- ``status="unavailable"``: the host cannot build ``config`` (toolchain
  registry); reported as a skip, never as a pass;
- ``status="infeasible"``: every consumer of the build is a mix that cannot
  work whatever the edit (``pair_blocked``), so nothing could be executed;
  reported as a skip with each pair's reason, never as a pass;
- ``status="ran"``: built once, compared once, executed per consumer and
  reconciled. ``pairs`` reports each (build, consumer) pair, blocked ones
  included with their reasons.

``minimize`` shrinks a failing cell's config one axis at a time towards
``default_config`` (and the evidence towards ``headers``), keeping every step
that reproduces the same class and problems, until no axis can move.
``regression_test`` renders the result as a pytest function ready to paste.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from abiforge.model.config import (
    Config,
    Consumer,
    Flags,
    ResolvedConsumer,
    Toolchain,
    default_config,
    has_feature,
)
from abiforge.model.expect import (
    ExecTruth,
    Expectation,
    PairTruth,
    Reconciled,
    reconcile,
)
from abiforge.model.seeds import Seed
from abiforge.model.space import seed_constructs, seed_flags_conflict
from abiforge.operators import EditOp
from abiforge.run.exec import Blocked, ExecutionOracle
from abiforge.run.harness import BaselineCache, NewBuildCache, abicheck_answer
from abiforge.run.toolchains import (
    capability_gap,
    consumer_unavailable,
    default_cxx11_abi,
    elf_link_unavailable,
    execution_unavailable,
    unavailable,
    uses_libstdcxx,
)


@dataclass
class CellContext:
    root: Path
    cache: BaselineCache
    new_cache: NewBuildCache
    oracle: ExecutionOracle
    execute: bool = True

    @classmethod
    def at(cls, root: Path, execute: bool = True) -> CellContext:
        return cls(
            root=root,
            cache=BaselineCache(root / "old"),
            new_cache=NewBuildCache(root / "new"),
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

    @property
    def pairs(self) -> tuple[PairTruth, ...]:
        return self.exec_truth.pairs if self.exec_truth else ()

    def pair_rows(self) -> list[dict[str, str]]:
        """One row per (build, consumer) pair, for the per-pair report."""
        return [
            {
                "op": self.op,
                "seed": self.seed,
                "build": self.config.build_id,
                "producer": self.config.producer.id,
                "consumer": p.consumer,
                "outcome": p.outcome,
                "cell": self.cls,
                "detail": p.reason or "; ".join(p.signals),
            }
            for p in self.pairs
        ]

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
            + "".join(
                f"    [{self.config.build_id} x {p.consumer}] {p.outcome}"
                f"{': ' + (p.reason or '; '.join(p.signals)) if p.reason or p.signals else ''}\n"
                for p in self.pairs
            )
            + f"  problems: {list(self.reconciled.problems)} {self.reconciled.detail}"
        )


def seed_conflict(seed: Seed, config: Config, op: EditOp | None = None) -> str | None:
    """A seed x build pair (with *op*'s edit, when given) that is ill-formed.

    The rule lives with the language-space model (``abiforge.model.space``), so
    the coverage report counts as infeasible exactly what the generator refuses.
    """
    return _std_flag_conflict(seed, config.flags, op)


def _std_flag_conflict(
    seed: Seed, flags: Flags, op: EditOp | None = None
) -> str | None:
    """A cell built (or consumed) under *flags* that cannot work: the space model's rule."""
    return seed_flags_conflict(seed, flags, op)


def _has_std_string(seed: Seed, op: EditOp | None = None) -> bool:
    """The cell passes ``std::string`` across its boundary, in the baseline
    (cxx_stdlib, cxx_alt) or through *op*'s edit."""
    return "std_string" in seed_constructs(seed, op)


def stdlib_mismatch(
    seed: Seed, config: Config, consumer: ResolvedConsumer, op: EditOp | None = None
) -> str | None:
    """A ``std::`` seed whose library and consumer use different C++ libraries.

    A consumer built against libc++ cannot link a libstdc++ ``std::string``
    API (or the reverse), whatever the edit; that pairing is not buildable.
    """
    tc = consumer.toolchain
    if not _has_std_string(seed, op) or tc == config.producer:
        return None
    if uses_libstdcxx(config.producer) != uses_libstdcxx(tc):
        return (
            f"{config.producer.id} and {tc.id} use different C++ standard "
            "libraries; the std::string seed cannot link across them"
        )
    return None


def _effective_cxx11_abi(tc: Toolchain, flags: Flags) -> int | None:
    return flags.cxx11_abi if flags.cxx11_abi is not None else default_cxx11_abi(tc)


def dual_abi_mismatch(
    seed: Seed, config: Config, consumer: ResolvedConsumer, op: EditOp | None = None
) -> str | None:
    """A ``std::string`` consumer on the other side of libstdc++'s dual ABI.

    ``_GLIBCXX_USE_CXX11_ABI=0`` and ``=1`` are two different ``std::string``
    types with different mangled names: the consumer cannot link the old
    library at all, so the pair says nothing about the edit.
    """
    if not _has_std_string(seed, op):
        return None
    if not (uses_libstdcxx(config.producer) and uses_libstdcxx(consumer.toolchain)):
        return None
    lib = _effective_cxx11_abi(config.producer, config.flags)
    use = _effective_cxx11_abi(consumer.toolchain, consumer.flags)
    if lib != use:
        return (
            f"the library's std::string is _GLIBCXX_USE_CXX11_ABI={lib}, the "
            f"consumer's is {use}: the dual ABI cannot link across them"
        )
    return None


def pair_blocked(
    op: EditOp, seed: Seed, config: Config, consumer: ResolvedConsumer
) -> tuple[str, str] | None:
    """``("infeasible" | "unavailable", reason)`` for a pair that cannot run, else None.

    *Infeasible* mixes cannot work whatever the edit, so their consumers would
    break (or fail to build) against the old library already; *unavailable*
    ones are host gaps. Neither is a pass: both are reported with the reason.
    """
    if consumer.std.lang != config.lang:
        return (
            "infeasible",
            f"a {consumer.std.lang} consumer cannot use the {config.lang} seed "
            '(the C header has no extern "C" guards)',
        )
    for feat in op.features:
        if not has_feature(consumer.std, feat):
            return (
                "infeasible",
                f"the edited header needs {feat}, which the consumer's "
                f"{consumer.std.name} lacks",
            )
    if consumer.flags.bits != config.flags.bits:
        return (
            "infeasible",
            "consumer and library word sizes differ; nothing can link",
        )
    reason = _std_flag_conflict(seed, consumer.flags, op)
    if reason:
        return ("infeasible", f"consumer: {reason}")
    reason = consumer_unavailable(config, consumer, op.features)
    if reason:
        return ("unavailable", reason)
    reason = stdlib_mismatch(seed, config, consumer, op) or dual_abi_mismatch(
        seed, config, consumer, op
    )
    if reason:
        return ("infeasible", reason)
    return None


def run_cell(op: EditOp, seed: Seed, config: Config, ctx: CellContext) -> CellResult:
    reason = op.requires(config) or seed_conflict(seed, config, op)
    if reason or not op.applies_to(seed) or seed.lang != config.lang:
        return CellResult(
            op.name,
            seed.name,
            config,
            "not-applicable",
            reason or "seed/language mismatch",
        )
    reason = (
        unavailable(config)
        or elf_link_unavailable(seed, config)
        or capability_gap(op, config)
    )
    if reason is None and op.rebuild is not None:
        reason = unavailable(replace(config, flags=op.new_flags(config.flags)))
    if reason:
        return CellResult(op.name, seed.name, config, "unavailable", reason)
    executes = ctx.execute and execution_unavailable(config) is None
    blocked: list[Blocked] = []
    if executes:
        for consumer in config.consumer_set:
            why = pair_blocked(op, seed, config, consumer)
            if why is not None:
                blocked.append((consumer, *why))
        if len(blocked) == len(config.consumer_set):
            # Nothing can execute: the oracle would add no signal, and running
            # abicheck alone would pass a cell its consumers never checked.
            status = (
                "unavailable"
                if all(b[1] == "unavailable" for b in blocked)
                else "infeasible"
            )
            why_all = "; ".join(f"{c.id}: {r}" for c, _, r in blocked)
            return CellResult(op.name, seed.name, config, status, why_all)
    old = ctx.cache.get(seed, config)
    new = ctx.new_cache.get(op, seed, config)
    # The verdict depends on the library pair alone, never on the consumers.
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
            blocked,
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
    if config.consumers:
        steps.append(("consumers", replace(config, consumers=())))
        if len(config.consumers) > 1:
            for i in range(len(config.consumers)):
                rest = config.consumers[:i] + config.consumers[i + 1 :]
                steps.append((f"consumers[{i}]", replace(config, consumers=rest)))
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


def _consumer_expr(u: Consumer) -> str:
    args = []
    if u.toolchain is not None:
        args.append(f"toolchain={_tc_expr(u.toolchain)}")
    if u.std is not None:
        args.append(f'std=STANDARD_BY_NAME["{u.std.name}"]')
    if u.flags is not None:
        args.append(f"flags={_flags_expr(u.flags)}")
    return f"Consumer({', '.join(args)})"


def config_expr(c: Config) -> str:
    parts = [
        f'std=STANDARD_BY_NAME["{c.std.name}"]',
        f"producer={_tc_expr(c.producer)}",
    ]
    if c.flags != Flags():
        parts.append(f"flags={_flags_expr(c.flags)}")
    if c.consumers:
        parts.append(
            "consumers=(" + "".join(_consumer_expr(u) + ", " for u in c.consumers) + ")"
        )
    if c.evidence != "headers":
        parts.append(f'evidence="{c.evidence}"')
    return "Config(" + ", ".join(parts) + ")"


REGRESSION_IMPORTS = """from pathlib import Path

import pytest

from abiforge.run.cells import CellContext, run_cell
from abiforge.model.config import STANDARD_BY_NAME, Config, Consumer, Flags
from abiforge.model.expect import PASSING_CLASSES
from abiforge.operators import OP_BY_NAME
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.run.toolchains import toolchain"""


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
    if cell.status in ("unavailable", "infeasible"):
        pytest.skip(cell.reason)
    assert cell.cls in PASSING_CLASSES, cell.describe()
'''
