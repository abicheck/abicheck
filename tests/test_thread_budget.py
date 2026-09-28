"""`ABICHECK_MAX_THREADS`: one process-wide budget for every abicheck pool.

Stated as invariants over nested pool shapes (the real process nests members
x sides x probes), not one example: the total never exceeds the cap, the
results never depend on it, and a spent budget degrades to inline execution
instead of deadlocking.
"""

from __future__ import annotations

import ast
import itertools
import threading
import time
from pathlib import Path

import pytest

from abicheck import process_resources as pr
from abicheck.buildsource import include_graph_workers as igw

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "THREAD_BUDGET", pr._ThreadBudget())


def _nested(widths: tuple[int, ...], leaf: list[str], lock: threading.Lock) -> list:
    """Nested pools `widths[0]` wide, each task opening the next level."""

    def run(depth: int, label: str) -> object:
        if depth == len(widths):
            time.sleep(0.002)
            with lock:
                leaf.append(label)
            return label
        with pr.BudgetedExecutor(widths[depth]) as pool:
            futures = [
                pool.submit(run, depth + 1, f"{label}.{i}")
                for i in range(widths[depth])
            ]
            return [f.result() for f in futures]

    return run(0, "r")  # type: ignore[return-value]


@pytest.mark.parametrize("cap", [None, 1, 2, 3, 7])
@pytest.mark.parametrize("widths", [(2,), (3, 2), (2, 2, 3), (4, 1, 2)])
def test_total_threads_never_exceed_the_cap_and_results_do_not_change(
    monkeypatch: pytest.MonkeyPatch, cap: int | None, widths: tuple[int, ...]
) -> None:
    if cap is None:
        monkeypatch.delenv(pr.MAX_THREADS_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(pr.MAX_THREADS_ENV_VAR, str(cap))
    leaf: list[str] = []
    result = _nested(widths, leaf, threading.Lock())

    # Oracle computed independently of the implementation: the full tree of
    # labels, in submission order.
    def expected(depth: int, label: str) -> object:
        if depth == len(widths):
            return label
        return [expected(depth + 1, f"{label}.{i}") for i in range(widths[depth])]

    assert result == expected(0, "r")
    assert sorted(leaf) == sorted(
        "r." + ".".join(map(str, p))
        for p in itertools.product(*(range(w) for w in widths))
    )
    if cap is not None:
        assert pr.THREAD_BUDGET.peak <= cap
    assert pr.THREAD_BUDGET.in_use == 0, "every grant is returned"


def test_a_spent_budget_runs_inline_in_the_submitting_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(pr.MAX_THREADS_ENV_VAR, "1")
    with pr.BudgetedExecutor(4) as outer:
        assert outer.granted_threads == 1
        with pr.BudgetedExecutor(4) as inner:
            assert inner.granted_threads == 0
            me = threading.get_ident()
            assert inner.submit(threading.get_ident).result() == me
            with pytest.raises(ValueError, match="boom"):
                inner.submit(_raise).result()
    assert pr.THREAD_BUDGET.in_use == 0


def _raise() -> None:
    raise ValueError("boom")


@pytest.mark.parametrize("raw", ["", "0", "-2", "many"])
def test_unset_zero_or_bad_cap_means_unlimited(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(pr.MAX_THREADS_ENV_VAR, raw)
    assert pr.max_threads() is None
    with pr.BudgetedExecutor(5) as pool:
        assert pool.granted_threads == 5


def test_shutdown_twice_returns_the_grant_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(pr.MAX_THREADS_ENV_VAR, "4")
    pool = pr.BudgetedExecutor(3)
    pool.shutdown(wait=False)
    pool.shutdown(wait=True)
    assert pr.THREAD_BUDGET.in_use == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3", 3), ("1", 1), ("", None), ("0", None), ("junk", None)],
)
def test_probe_pool_size_follows_include_map_jobs(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int | None
) -> None:
    monkeypatch.setenv("ABICHECK_INCLUDE_MAP_JOBS", raw)
    default = pr.jobs_ceiling(floor=4, cpu_multiplier=1)
    assert igw._shared_pool_size() == (default if expected is None else expected)


def test_no_module_builds_a_thread_pool_outside_the_budget() -> None:
    """Every abicheck thread pool goes through `BudgetedExecutor`.

    A raw `ThreadPoolExecutor(...)` anywhere else is a pool the total cap
    cannot see -- the gap this budget exists to close.
    """
    offenders = []
    for path in sorted((_ROOT / "abicheck").rglob("*.py")):
        if path.name == "process_resources.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (
                    fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                )
                if name == "ThreadPoolExecutor":
                    offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    assert offenders == []
