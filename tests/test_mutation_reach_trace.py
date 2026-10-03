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

"""`scripts/mutation_reach_trace.py`: finding ``only_mutate`` functions safely.

A heap census (``gc.get_objects()``) beside a live thread can break that
thread's ``tuple(...)`` construction (``memory_trace.gc_census_is_safe``), so
the tracer censuses only when it is the sole thread and otherwise walks the
modules' namespaces, retrying the census at a later test. Contract:

* with another thread alive the heap is never enumerated;
* the namespace walk finds every function the module keeps anywhere reachable
  from its namespace, checked against an independent oracle -- a real heap
  census, taken while this test is single-threaded, filtered to functions
  compiled from the module's file;
* a walk never marks the modules scanned, so the next safe test censuses;
  only a census does.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import threading
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

_MODULE_SOURCE = textwrap.dedent(
    """
    import functools

    def top():
        def nested():
            return 1
        return nested

    def _decorate(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            return fn(*a, **k)
        return wrapper

    @_decorate
    def decorated():
        return 2

    class Outer:
        def method(self):
            return 3

        @staticmethod
        def static():
            return 4

        @classmethod
        def klass(cls):
            return 5

        @property
        def prop(self):
            return 6

        @prop.setter
        def prop(self, value):
            pass

        class Inner:
            def inner_method(self):
                return 7

    HANDLERS = {"a": lambda: 8, "b": [lambda: 9, (lambda: 10,)]}
    """
)


@pytest.fixture(scope="module")
def trace() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mutation_reach_trace", _REPO / "scripts" / "mutation_reach_trace.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def synthetic(tmp_path: Path) -> types.ModuleType:
    path = tmp_path / "reach_trace_synthetic.py"
    path.write_text(_MODULE_SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("reach_trace_synthetic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _oracle(module: types.ModuleType) -> set[types.FunctionType]:
    """Every live function compiled from *module*'s file: an independent
    census, only ever taken while this test is the sole thread."""
    import gc

    assert threading.active_count() == 1, "oracle census needs a single thread"
    path = module.__file__
    return {
        o
        for o in gc.get_objects()
        if isinstance(o, types.FunctionType) and o.__code__.co_filename == path
    }


def test_namespace_walk_finds_every_function_the_heap_census_finds(
    trace: types.ModuleType, synthetic: types.ModuleType
) -> None:
    expected = _oracle(synthetic)
    # Vacuity guard: the oracle must see the shapes this test is about.
    names = {f.__code__.co_name for f in expected}
    assert {
        "top",
        "wrapper",
        "decorated",
        "method",
        "static",
        "klass",
        "prop",
        "inner_method",
        "<lambda>",
    } <= names
    found = set(trace.function_candidates([synthetic.__name__], heap_census=False))
    missing = {f.__qualname__ for f in expected - found}
    assert not missing, missing


def test_walk_never_enumerates_the_heap(
    trace: types.ModuleType,
    synthetic: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> list[object]:
        raise AssertionError("heap census while another thread may be alive")

    monkeypatch.setattr(trace.gc, "get_objects", forbidden)
    assert trace.function_candidates([synthetic.__name__], heap_census=False)


class _FakeMonitoring:
    class events:
        PY_START = 1

    def __init__(self) -> None:
        self.armed: list[types.CodeType] = []

    def set_local_events(self, _tool: int, code: types.CodeType, _ev: int) -> None:
        self.armed.append(code)


@pytest.mark.parametrize(
    "safe_sequence", [(False, True), (False, False, True), (True,)]
)
def test_only_a_census_marks_the_modules_scanned(
    trace: types.ModuleType,
    synthetic: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    safe_sequence: tuple[bool, ...],
) -> None:
    fake = _FakeMonitoring()
    monkeypatch.setattr(trace, "_MON", fake)
    calls: list[bool] = []

    def candidates(modules: list[str], *, heap_census: bool) -> list[object]:
        calls.append(heap_census)
        return [synthetic.top]

    monkeypatch.setattr(trace, "function_candidates", candidates)
    mon = object.__new__(trace.Monitor)
    mon.paths = frozenset({synthetic.__file__})
    mon.modules = [synthetic.__name__]
    mon.armed = set()
    mon.module_ids = ()
    for safe in safe_sequence:
        monkeypatch.setattr(trace, "gc_census_is_safe", lambda safe=safe: safe)
        mon.arm()
    # Every unsafe arm walked; the first safe one censused and nothing after.
    assert calls == list(safe_sequence)
    assert mon.module_ids != ()
    monkeypatch.setattr(trace, "gc_census_is_safe", lambda: True)
    mon.arm()
    assert calls == list(safe_sequence), "a scanned module set was rescanned"
    assert synthetic.top.__code__ in mon.armed


def test_a_walk_alone_leaves_the_modules_unscanned(
    trace: types.ModuleType,
    synthetic: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trace, "_MON", _FakeMonitoring())
    monkeypatch.setattr(trace, "gc_census_is_safe", lambda: False)
    mon = object.__new__(trace.Monitor)
    mon.paths = frozenset({synthetic.__file__})
    mon.modules = [synthetic.__name__]
    mon.armed = set()
    mon.module_ids = ()
    mon.arm()
    assert mon.module_ids == ()
    # What the walk found is armed regardless.
    assert synthetic.top.__code__ in mon.armed
