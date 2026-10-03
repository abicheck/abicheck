# SPDX-License-Identifier: Apache-2.0
"""``mutation_reach_trace`` finds the same code without a heap census.

The tracer arms ``sys.monitoring`` on every code object compiled from an
``only_mutate`` file, found from live functions. A whole-heap ``gc`` walk
beside another live thread breaks that thread's ``tuple(...)`` construction
(``test_gc_census_thread_safety.py``), so with other threads alive it walks
every loaded module's references instead. That fallback is only sound
if it reaches the same code: the oracle is the heap walk itself, over the
real ``only_mutate`` modules.
"""

from __future__ import annotations

import functools
import importlib
import sys
import threading
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import mutation_reach_trace as mrt  # noqa: E402


def _armable(functions, paths) -> set[types.CodeType]:
    return {
        nested
        for f in functions
        if f.__code__.co_filename in paths
        for nested in mrt.code_objects(f.__code__)
        if nested.co_name != "<module>"
    }


@pytest.fixture(scope="module")
def mutated():
    paths = mrt.only_mutate_paths(REPO)
    names = mrt.module_names(REPO, paths)
    for n in names:
        importlib.import_module(n)
    return paths, list(sys.modules.values())


_COMPARE_IN_A_FRESH_INTERPRETER = """
import gc, importlib, json, sys
sys.path.insert(0, sys.argv[2])
import mutation_reach_trace as mrt
from pathlib import Path
repo = Path(sys.argv[1])
paths = mrt.only_mutate_paths(repo)
for name in mrt.module_names(repo, paths):
    importlib.import_module(name)
gc.collect()
modules = list(sys.modules.values())
assert mrt._census_is_safe()
def armable(functions):
    return {
        f"{c.co_filename}:{c.co_firstlineno}:{c.co_qualname}"
        for f in functions
        if f.__code__.co_filename in paths
        for c in mrt.code_objects(f.__code__)
        if c.co_name != "<module>"
    }
print(json.dumps({
    "heap": sorted(armable(mrt.live_functions(modules))),
    "namespace": sorted(armable(mrt.namespace_functions(modules))),
}))
"""


def test_the_namespace_walk_reaches_what_the_heap_walk_reaches() -> None:
    """In a fresh interpreter, as the tracer meets them: a test process's heap
    also holds functions other tests left behind (uncollected garbage, a
    reloaded module's previous functions), which no walk from a module can
    reach and which the tracer never needs to arm."""
    import json
    import subprocess

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            _COMPARE_IN_A_FRESH_INTERPRETER,
            str(REPO),
            str(REPO / "scripts"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    found = json.loads(out.stdout)
    assert found["heap"], "the oracle found nothing to arm"
    assert sorted(set(found["heap"]) - set(found["namespace"])) == []
    assert found["namespace"] == found["heap"]


def test_with_another_thread_alive_no_heap_census_runs(
    mutated, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, modules = mutated

    def census_forbidden():
        raise AssertionError("heap census beside a live thread")

    monkeypatch.setattr(mrt.gc, "get_objects", census_forbidden)
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait)
    worker.start()
    try:
        found = _armable(mrt.live_functions(modules), paths)
    finally:
        stop.set()
        worker.join()
    assert found


def test_wrappers_are_unwrapped() -> None:
    module = types.ModuleType("m")

    def plain():
        return 1

    def wrapped():
        return 2

    @functools.wraps(wrapped)
    def outer():
        return wrapped()

    class C:
        @staticmethod
        def s():
            return 3

        @classmethod
        def c(cls):
            return 4

        @property
        def p(self):
            return 5

    module.plain = plain
    module.outer = outer
    module.cached = functools.lru_cache(maxsize=None)(lambda: 6)
    module.part = functools.partial(plain)

    class Slotted:
        __slots__ = ("held", "__private")

        def __init__(self) -> None:
            self.held = lambda: 7
            self.__private = lambda: 8

    module.C = C
    module.registry = [Slotted()]
    found = mrt.namespace_functions([module])
    names = {f.__name__ for f in found}
    # functools.wraps renames `outer` to `wrapped`; both objects are found.
    assert outer in found and wrapped in found
    assert {"plain", "<lambda>", "s", "c", "p"} <= names
    slotted = module.registry[0]
    assert slotted.held in found and slotted._Slotted__private in found
