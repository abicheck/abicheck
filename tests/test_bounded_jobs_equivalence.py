"""Worker-count equivalence for the three per-unit pools (plan Phase 7).

``extract.tu_jobs._tu_jobs``, ``buildsource.source_replay._l4_jobs`` and
``buildsource.call_graph._call_graph_jobs`` all size a pool of child-process
units through :func:`abicheck.process_resources.bounded_jobs`. The table below
is hand-derived from the sizing rule each caller had *before* the convergence,
not from the shared implementation, so it fails if the convergence moved a
result. Each row is one (caller, unit count, CPUs, available RAM, env override)
combination and the worker count that rule gives.

The arithmetic behind every expected value:

* ceiling   = ``max(8, 2 * cpus)``       (oversubscription ceiling)
* auto      = ``min(n_units, cpus, 8)``  (``max(1, ...)``)
* RAM cap   = ``max(1, int(avail / 3.0))`` (3.0 GiB default per-worker budget,
  shared by all three pools; ``None`` when RAM is unknown)
* explicit override: ``max(1, int(env))``, clamped to the ceiling, then to the
  RAM cap; an unparsable value gives 1. ``_call_graph_jobs`` also clamps an
  explicit value to ``n_units``; the other two do not.
* ``_call_graph_jobs`` returns ``n_units`` unchanged for ``n_units <= 1``.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.call_graph import _call_graph_jobs
from abicheck.buildsource.source_replay import _l4_jobs
from abicheck.extract.tu_jobs import _tu_jobs

_CALLERS = {"tu": _tu_jobs, "l4": _l4_jobs, "cg": _call_graph_jobs}

_ENV_VARS = {
    "tu": "ABICHECK_TU_JOBS",
    "l4": "ABICHECK_L4_JOBS",
    "cg": "ABICHECK_CALL_GRAPH_JOBS",
}

_MEM_BUDGET_VARS = ("ABICHECK_TU_JOB_MEM_GIB", "ABICHECK_L4_JOB_MEM_GIB")

# (caller, n_units, cpus, avail_gib, env, expected)
ORACLE = [
    # -- auto: min(n, cpus, 8), no RAM pressure -------------------------------
    ("tu", 1000, 4, None, None, 4),  # min(1000, 4, 8)
    ("l4", 1000, 4, None, None, 4),
    ("cg", 1000, 4, None, None, 4),
    ("tu", 1000, 16, None, None, 8),  # min(1000, 16, 8): the eight cap
    ("l4", 1000, 16, None, None, 8),
    ("cg", 1000, 16, None, None, 8),
    ("tu", 2, 16, None, None, 2),  # fewer units than CPUs
    ("l4", 2, 16, None, None, 2),
    ("cg", 2, 16, None, None, 2),
    ("tu", 0, 4, None, None, 1),  # max(1, min(0, 4, 8))
    ("l4", 0, 4, None, None, 1),
    ("cg", 0, 4, None, None, 0),  # call graph returns n_units for n <= 1
    ("tu", 1, 4, None, None, 1),
    ("cg", 1, 4, None, None, 1),
    ("tu", 1000, None, None, None, 1),  # os.cpu_count() None -> 1 CPU
    # -- auto: RAM reduces the count -------------------------------------------
    ("tu", 1000, 8, 6.0, None, 2),  # auto 8, cap int(6/3) = 2
    ("l4", 1000, 8, 6.0, None, 2),
    ("cg", 1000, 8, 6.0, None, 2),
    ("tu", 1000, 4, 6.0, None, 2),  # auto 4, cap 2
    ("tu", 3, 8, 6.0, None, 2),  # auto 3, cap 2
    ("cg", 3, 8, 6.0, None, 2),
    ("tu", 2, 8, 6.0, None, 2),  # auto 2, cap 2 is not < 2
    ("tu", 1000, 8, 1.0, None, 1),  # cap max(1, int(1/3)) = 1
    ("l4", 1000, 8, 1.0, None, 1),
    ("cg", 1000, 8, 1.0, None, 1),
    ("tu", 1000, 8, 100.0, None, 8),  # cap 33 does not bind
    ("cg", 1000, 8, 100.0, None, 8),
    ("tu", 1000, 4, 100.0, None, 4),  # large RAM, CPU still binds
    # -- explicit override: serial, bounded, unparsable ------------------------
    ("tu", 100, 4, None, "1", 1),
    ("l4", 100, 4, None, "1", 1),
    ("cg", 100, 4, None, "1", 1),
    ("cg", 100, 8, 6.0, "1", 1),
    ("tu", 100, 4, None, "0", 1),  # max(1, 0)
    ("cg", 100, 4, None, "0", 1),
    ("tu", 100, 4, None, "abc", 1),  # unparsable -> serial
    ("l4", 100, 4, None, "abc", 1),
    ("cg", 100, 4, None, "abc", 1),
    ("tu", 1, 4, None, "8", 8),  # explicit is not bounded by n for tu
    ("cg", 1, 4, None, "8", 1),  # call graph short-circuits at n <= 1
    ("cg", 0, 4, None, "8", 0),
    # -- explicit override: oversubscription ceiling ---------------------------
    ("tu", 100, 4, None, "64", 8),  # ceiling max(8, 2*4) = 8
    ("l4", 100, 4, None, "64", 8),
    ("cg", 100, 4, None, "64", 8),
    ("tu", 100, 16, None, "20", 20),  # ceiling 32, 20 fits
    ("cg", 100, 16, None, "20", 20),
    ("tu", 5, 16, None, "20", 20),  # tu: explicit not bounded by n
    ("cg", 5, 16, None, "20", 5),  # cg: explicit bounded by n
    ("tu", 100, 8, None, "50", 16),  # ceiling max(8, 16) = 16
    ("l4", 100, 8, None, "50", 16),
    ("cg", 100, 8, None, "50", 16),
    ("tu", 100, 8, 100.0, "16", 16),  # cap 33, ceiling 16: ceiling binds
    # -- explicit override: RAM cap ---------------------------------------------
    ("tu", 100, 8, 6.0, "5", 2),  # cap 2 < 5
    ("l4", 100, 8, 6.0, "5", 2),
    ("cg", 100, 8, 6.0, "5", 2),
    ("tu", 100, 8, 6.0, "2", 2),  # 2 == cap, kept
    ("cg", 100, 8, 6.0, "2", 2),
    ("tu", 100, 8, 1.0, "4", 1),  # cap 1
]


@pytest.mark.parametrize(
    ("caller", "n_units", "cpus", "avail_gib", "env", "expected"),
    ORACLE,
    ids=[f"{c}-n{n}-cpu{cpu}-ram{ram}-env{env}" for c, n, cpu, ram, env, _ in ORACLE],
)
def test_pool_sizing_matches_oracle(
    monkeypatch,
    caller: str,
    n_units: int,
    cpus: int | None,
    avail_gib: float | None,
    env: str | None,
    expected: int,
) -> None:
    # Another test can re-import abicheck.process_resources after collection,
    # leaving two module objects: each caller reads the one it bound (the L4
    # and call-graph pools via source_replay, the manifest pool at call time),
    # so the RAM probe is patched on every one of them.
    from abicheck import process_resources
    from abicheck.buildsource import source_replay

    for var in (*_ENV_VARS.values(), *_MEM_BUDGET_VARS):
        monkeypatch.delenv(var, raising=False)
    if env is not None:
        monkeypatch.setenv(_ENV_VARS[caller], env)
    monkeypatch.setattr(process_resources.os, "cpu_count", lambda: cpus)
    for module in {process_resources, source_replay.process_resources}:
        monkeypatch.setattr(module, "available_mem_gib", lambda: avail_gib)
    assert _CALLERS[caller](n_units) == expected
