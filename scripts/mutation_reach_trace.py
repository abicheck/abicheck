# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""pytest plugin: which tests execute ``[tool.mutmut].only_mutate`` code in-process.

mutmut's stats pass associates a test with a mutant only when the test calls
the mutated function in-process; a mutant can only be killed by an associated
test. A test file none of whose tests reach any ``only_mutate`` function
therefore cannot kill anything, and running it in the stats pass only costs
time. This plugin records the tests that do reach that code, so
``scripts/gen_mutation_test_selection.py`` can derive the selection mutmut's
stats pass runs.

Unlike mutmut's own tracing it runs under plain pytest, so it parallelises
with xdist. It is deliberately a superset of mutmut's association: any code
object compiled from an ``only_mutate`` file counts (functions, methods,
lambdas, comprehensions, nested functions -- everything but the module
body), over the whole test protocol (fixture setup and teardown included),
on every thread. A forked child is invisible to it, exactly as to mutmut.

Mechanism: ``sys.monitoring`` (3.12+) ``PY_START`` events are enabled *only*
on those code objects; each reports once and is disabled, and
``restart_events()`` re-arms them per test, so every other call in the suite
costs nothing. (A global ``sys.setprofile`` hook was measured covering about
2% of the suite in several minutes.) The code objects are found from every
live function and their nested ``co_consts``. Live functions come from a
whole-heap ``gc`` walk only when no other thread is alive -- a heap census
beside a live thread breaks that thread's ``tuple(...)`` construction
(``abicheck.workflows.memory_trace.gc_census_is_safe``) -- and otherwise
by following references from every loaded module
(:func:`namespace_functions`). A closure created at
run time reuses a nested code object that is already armed, so new code only
appears when an ``only_mutate`` module is (re)imported: the scan is repeated
only when those module objects change, not per test.

Activated by ``MUTATION_REACH_OUT=<dir>``: each worker writes the node ids it
saw reach mutated code to ``<dir>/<worker>.json``.
"""

from __future__ import annotations

import functools
import gc
import importlib
import json
import os
import sys
import threading
import tomllib
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

#: ``sys.monitoring`` (3.12+), typed loosely: mypy here targets 3.11.
_MON: Any = getattr(sys, "monitoring", None)

#: sys.monitoring tool slot. 0-2 are reserved for debuggers, coverage and
#: profilers; 3-5 are free for other tools.
_TOOL_ID = 4


def module_names(rootdir: Path, paths: frozenset[str]) -> list[str]:
    root = rootdir.resolve()
    return [
        ".".join(Path(p).relative_to(root).with_suffix("").parts) for p in sorted(paths)
    ]


def only_mutate_paths(rootdir: Path) -> frozenset[str]:
    config = tomllib.loads((rootdir / "pyproject.toml").read_text(encoding="utf-8"))
    return frozenset(
        str((rootdir / m).resolve()) for m in config["tool"]["mutmut"]["only_mutate"]
    )


def code_objects(code: types.CodeType) -> Generator[types.CodeType]:
    """*code* and every code object nested in it (closures, lambdas, ...)."""
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from code_objects(const)


def _census_is_safe() -> bool:
    """``abicheck.workflows.memory_trace.gc_census_is_safe``, restated (as
    ``perf_cache_reset`` does) so this plugin imports nothing it traces."""
    return threading.active_count() == 1


def _slot_values(value: object) -> list[object]:
    """The values *value* keeps in ``__slots__``, which have no ``__dict__``
    (the detector registry's entries hold their ``requires_support`` lambda
    this way)."""
    if isinstance(value, type):
        return []
    out: list[object] = []
    for klass in type(value).__mro__:
        slots = klass.__dict__.get("__slots__", ())
        for name in (slots,) if isinstance(slots, str) else slots:
            if name.startswith("__") and not name.endswith("__"):
                name = f"_{klass.__name__.lstrip('_')}{name}"  # mangled
            if name not in ("__dict__", "__weakref__") and hasattr(value, name):
                out.append(getattr(value, name))
    return out


def namespace_functions(modules: list[types.ModuleType]) -> list[types.FunctionType]:
    """Every function reachable from *modules* by following references:
    module and class namespaces, instance attributes and slots, containers, method
    descriptors, properties, ``functools.partial`` and ``__wrapped__``
    (``lru_cache`` included), and closure cells. Pass every loaded module
    to reach functions stored in another module's object, such as a lambda
    handed to a registering decorator. Unlike ``gc.get_objects()`` it never
    holds a reference to an object only another thread can reach, such as a
    tuple that thread is still building. A container another thread resizes
    mid-copy is skipped rather than retried."""
    found: list[types.FunctionType] = []
    seen: set[int] = set()
    stack: list[object] = list(modules)
    while stack:
        value = stack.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        try:
            if isinstance(value, types.FunctionType):
                found.append(value)
                stack.extend(c.cell_contents for c in value.__closure__ or ())
            elif isinstance(value, staticmethod | classmethod):
                stack.append(value.__func__)
            elif isinstance(value, property):
                stack.extend(f for f in (value.fget, value.fset, value.fdel) if f)
            elif isinstance(value, functools.partial):
                stack.append(value.func)
            elif isinstance(value, dict):
                stack.extend(list(value.values()))
            elif isinstance(value, list | tuple | set | frozenset):
                stack.extend(list(value))
            if not isinstance(value, dict | types.FunctionType):
                attrs = getattr(value, "__dict__", None)
                if isinstance(attrs, dict | types.MappingProxyType):
                    stack.extend(list(attrs.values()))
                stack.extend(_slot_values(value))
        except (RuntimeError, ValueError):  # resized mid-copy; empty cell
            continue
    return found


def live_functions(modules: list[types.ModuleType]) -> list[types.FunctionType]:
    """Every live function when a heap walk is safe, else every function
    reachable from *modules*."""
    if _census_is_safe():
        return [o for o in gc.get_objects() if isinstance(o, types.FunctionType)]
    return namespace_functions(modules)


class Monitor:
    def __init__(self, paths: frozenset[str], modules: list[str]) -> None:
        if _MON is None:
            raise pytest.UsageError(
                "mutation_reach_trace needs Python 3.12+ (sys.monitoring)"
            )
        self.paths = paths
        self.modules = modules
        self.armed: set[types.CodeType] = set()
        self.module_ids: tuple[int, ...] = ()
        self.hit = False
        self.hits: set[str] = set()
        _MON.use_tool_id(_TOOL_ID, "mutation-reach-trace")
        _MON.register_callback(_TOOL_ID, _MON.events.PY_START, self._on_start)

    def _on_start(self, code: types.CodeType, offset: int) -> object:
        self.hit = True
        return _MON.DISABLE

    def arm(self) -> None:
        """Enable PY_START on every only_mutate code object not yet armed,
        rescanning only when an only_mutate module was (re)imported."""
        ids = tuple(id(sys.modules.get(name)) for name in self.modules)
        if ids == self.module_ids:
            return
        self.module_ids = ids
        for obj in live_functions(list(sys.modules.values())):
            code = obj.__code__
            if code.co_filename not in self.paths or code in self.armed:
                continue
            for nested in code_objects(code):
                if nested.co_name != "<module>" and nested not in self.armed:
                    _MON.set_local_events(_TOOL_ID, nested, _MON.events.PY_START)
                    self.armed.add(nested)

    def close(self) -> None:
        _MON.register_callback(_TOOL_ID, _MON.events.PY_START, None)
        _MON.free_tool_id(_TOOL_ID)


_STATE = pytest.StashKey[Monitor]()


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get("MUTATION_REACH_OUT"):
        root = Path(str(config.rootpath))
        paths = only_mutate_paths(root)
        names = module_names(root, paths)
        # Imported up front, so a test that imports one lazily inside its own
        # body still finds its code armed before it runs.
        for name in names:
            importlib.import_module(name)
        config.stash[_STATE] = Monitor(paths, names)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: object) -> Generator[None]:
    mon = item.config.stash.get(_STATE, None)
    if mon is None:
        yield
        return
    mon.arm()
    mon.hit = False
    _MON.restart_events()
    try:
        yield
    finally:
        if mon.hit:
            mon.hits.add(item.nodeid)


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = os.environ.get("MUTATION_REACH_OUT")
    mon = session.config.stash.get(_STATE, None)
    if not out or mon is None:
        return
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{worker}.json").write_text(
        json.dumps(sorted(mon.hits)), encoding="utf-8"
    )
    mon.close()
