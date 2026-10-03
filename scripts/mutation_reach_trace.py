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
live function (``gc``) and their nested ``co_consts``. A closure created at
run time reuses a nested code object that is already armed, so new code only
appears when an ``only_mutate`` module is (re)imported: the scan is repeated
only when those module objects change, not per test.

The heap census runs only while this is the process's sole Python thread
(``memory_trace.gc_census_is_safe``): a census beside a live thread can break
that thread's ``tuple(...)`` construction. With another thread alive the scan
walks the ``only_mutate`` modules' namespaces instead (functions, classes,
descriptors, ``__wrapped__`` chains and containers), arms what it finds, and
leaves the modules marked unscanned so the next test retries the full census.


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
import tomllib
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from abicheck.workflows.memory_trace import gc_census_is_safe

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


def _namespace_functions(modules: list[str]) -> list[object]:
    """Every function reachable from the named modules' namespaces.

    The fallback when a heap census is unsafe. Follows class bodies (nested
    classes too), ``staticmethod``/``classmethod``/``property`` wrappers,
    ``functools.wraps`` ``__wrapped__`` chains and plain containers, so a
    handler kept in a module-level registry is still found. Only a function
    created inside another call and kept nowhere in a namespace is missed,
    and :meth:`Monitor.arm` retries the full census for that.
    """
    found: list[object] = []
    seen: set[int] = set()
    stack: list[object] = []
    for name in modules:
        module = sys.modules.get(name)
        if module is not None:
            stack.extend(list(vars(module).values()))
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, types.FunctionType):
            found.append(obj)
            wrapped = getattr(obj, "__wrapped__", None)
            if wrapped is not None:
                stack.append(wrapped)
        elif isinstance(obj, (staticmethod, classmethod)):
            stack.append(obj.__func__)
        elif isinstance(obj, property):
            stack.extend(f for f in (obj.fget, obj.fset, obj.fdel) if f is not None)
        elif isinstance(obj, type):
            stack.extend(list(vars(obj).values()))
        elif isinstance(obj, dict):
            stack.extend(list(obj.values()))
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(list(obj))
    return found


def function_candidates(modules: list[str], *, heap_census: bool) -> list[object]:
    """Objects that may be ``only_mutate`` functions: the whole heap when a
    census is safe, else :func:`_namespace_functions`."""
    if heap_census:
        return gc.get_objects()
    return _namespace_functions(modules)


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

    def _live_functions(
        self, census: bool | None = None
    ) -> Generator[types.FunctionType]:
        """Every live function, or -- when another thread exists -- every
        function reachable from the ``only_mutate`` modules' namespaces.

        A heap census while another thread runs can hand out (and so break)
        a tuple that thread is still building (bug class
        ``gc-census-concurrent-thread``), so it is taken only when
        ``gc_census_is_safe()``; the namespace walk is the fallback.

        The walk follows namespaces, classes, containers, closures, partials,
        bound methods, defaults and ``__wrapped__`` from every module whose
        file is an only_mutate file. Its one structural limit: a function of
        an only_mutate file loaded *without* registering a module in
        ``sys.modules`` (a bare ``exec_module``) has no root it can start
        from, so a test reaching only that copy is under-reported. No
        thread-safe enumeration of the heap exists to close that; the
        mutation lane's ``selection-check`` job, which compares the
        selection with mutmut's own association, is the backstop.
        """
        if census is None:
            census = gc_census_is_safe()
        for obj in function_candidates(self.modules, heap_census=census):
            if isinstance(obj, types.FunctionType):
                yield obj
        if census:
            return
        seen: set[int] = set()
        # By file, not by name: the same only_mutate file can be imported
        # under a name `module_names` did not predict (another sys.path
        # entry, a test's own importlib load), and the census found those.
        stack: list[object] = [
            vars(m) for m in list(sys.modules.values()) if self._is_only_mutate(m)
        ]
        while stack:
            obj = stack.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if isinstance(obj, dict):
                stack.extend(obj.values())
            elif isinstance(obj, (list, tuple, set, frozenset)):
                # A callback table or registry held only in a container.
                stack.extend(obj)
            elif isinstance(obj, functools.partial):
                stack.extend((obj.func, *obj.args, *obj.keywords.values()))
            elif isinstance(obj, types.MethodType):
                stack.append(obj.__func__)
            elif isinstance(obj, type):
                # The class's values directly: a temporary copy pushed here
                # would be freed after its turn, and a later copy reusing
                # its id() would be skipped as already seen.
                stack.extend(vars(obj).values())
            elif isinstance(obj, (staticmethod, classmethod, property)):
                stack.extend(
                    f
                    for f in (
                        getattr(obj, a, None)
                        for a in ("__func__", "fget", "fset", "fdel")
                    )
                    if f
                )
            elif isinstance(obj, types.FunctionType):
                yield obj
                if (wrapped := getattr(obj, "__wrapped__", None)) is not None:
                    stack.append(wrapped)
                # A decorator's wrapper reaches the decorated function only
                # through its closure (no functools.wraps, no __wrapped__).
                for cell in obj.__closure__ or ():
                    try:
                        stack.append(cell.cell_contents)
                    except ValueError:  # an empty cell
                        pass
                stack.extend(obj.__defaults__ or ())
                stack.extend((obj.__kwdefaults__ or {}).values())

    @functools.cached_property
    def _basenames(self) -> frozenset[str]:
        return frozenset(os.path.basename(p) for p in self.paths)

    def _is_only_mutate(self, module: object) -> bool:
        path = getattr(module, "__file__", None)
        if not isinstance(path, str):
            return False
        # Called for every loaded module before every test: rule out the
        # common case by basename before paying for a filesystem resolve.
        if os.path.basename(path) not in self._basenames:
            return False
        try:
            return str(Path(path).resolve()) in self.paths
        except OSError:
            return False

    def arm(self) -> None:
        """Enable PY_START on every only_mutate code object not yet armed,
        rescanning only when an only_mutate module was (re)imported."""
        # Every loaded module of an only_mutate file, not only the predicted
        # names: one imported later under another name (a test's own
        # importlib load) must trigger a rescan too.
        ids = tuple(id(sys.modules.get(name)) for name in self.modules) + tuple(
            sorted(id(m) for m in list(sys.modules.values()) if self._is_only_mutate(m))
        )
        if ids == self.module_ids:
            return
        census = gc_census_is_safe()
        if census:
            # Only a full census marks this module set done: after a partial
            # namespace walk (another thread alive) the next test retries,
            # so the census runs once this is the sole thread again.
            self.module_ids = ids
        for obj in self._live_functions(census):
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
    # xdist's own record of this process's role, never the environment: a
    # trace run launched from inside another xdist worker inherits that
    # worker's PYTEST_XDIST_WORKER, so its controller would take a worker's
    # name and overwrite that worker's hits with its own empty set.
    workerinput = getattr(session.config, "workerinput", None)
    worker = workerinput["workerid"] if workerinput else "main"
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{worker}.json").write_text(
        json.dumps(sorted(mon.hits)), encoding="utf-8"
    )
    mon.close()
