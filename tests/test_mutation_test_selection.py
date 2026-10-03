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

"""The narrowed mutmut stats selection: the reach trace, its generator, and
the PR-side widening.

mutmut can only kill a mutant with a test it associated in its stats pass,
and it associates a test only when the test calls the mutated function
in-process. ``tests/mutation_test_selection.txt`` drops the files with no
such test from that pass. The property that makes this safe is that the
trace is a *superset* of mutmut's association -- these tests pin it against
a real pytest, through every way a test can reach code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import gen_mutation_test_selection as gen  # noqa: E402
import mutation_scope as scope  # noqa: E402

_MUTATED = """
def helper(x):
    def inner(y):
        return y + 1
    return inner(x)

class Thing:
    def method(self):
        return (lambda: 3)()
"""
_OTHER = "def untouched():\n    return 0\n"
_TESTS = """
import threading
import pytest
import pkg.other

@pytest.fixture
def via_fixture():
    import pkg.mutated
    return pkg.mutated.helper(1)

def test_direct():
    import pkg.mutated
    assert pkg.mutated.helper(1) == 2

def test_through_a_fixture(via_fixture):
    assert via_fixture == 2

def test_lazy_import_inside_the_test():
    from pkg.mutated import Thing
    assert Thing().method() == 3

def test_in_a_worker_thread():
    out = []
    def work():
        import pkg.mutated
        out.append(pkg.mutated.helper(5))
    t = threading.Thread(target=work)
    t.start(); t.join()
    assert out == [6]

def test_never_reaches_mutated_code():
    assert pkg.other.untouched() == 0
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "mutated.py").write_text(_MUTATED)
    (tmp_path / "pkg" / "other.py").write_text(_OTHER)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_reach.py").write_text(_TESTS)
    (tmp_path / "tests" / "test_unrelated.py").write_text(
        "def test_x():\n    assert 1\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mutmut]\nonly_mutate = ["pkg/mutated.py"]\n'
        '[tool.pytest.ini_options]\npythonpath = ["."]\n'
    )
    return tmp_path


def _trace(project: Path, extra: list[str]) -> set[str]:
    out = project / "reach"
    env = {
        **os.environ,
        "MUTATION_REACH_OUT": str(out),
        "PYTHONPATH": os.pathsep.join([str(REPO / "scripts"), str(project)]),
    }
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "mutation_reach_trace", *extra, "tests"],
        cwd=project, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    hits: set[str] = set()
    for part in out.glob("*.json"):
        hits.update(json.loads(part.read_text()))
    return hits


_REACHING = {
    "tests/test_reach.py::test_direct",
    "tests/test_reach.py::test_through_a_fixture",
    "tests/test_reach.py::test_lazy_import_inside_the_test",
    "tests/test_reach.py::test_in_a_worker_thread",
}


@pytest.mark.parametrize("workers", [[], ["-n", "2"], ["-p", "no:randomly", "-n", "0"]])
def test_the_trace_records_exactly_the_reaching_tests(
    tmp_path: Path, workers: list[str]
) -> None:
    """Every reach path is recorded and nothing else is, serial or under
    xdist. The oracle is the fixture's own construction, not the plugin."""
    assert _trace(_project(tmp_path), workers) == _REACHING


_IDLE_THREAD_CONFTEST = """
import threading

_stop = threading.Event()
_idle = threading.Thread(target=_stop.wait, daemon=True)
_idle.start()


def pytest_sessionfinish(session):
    # The trace plugin armed while this thread was alive, so it never took a
    # heap census (bug class gc-census-concurrent-thread).
    assert threading.active_count() > 1
    _stop.set()
"""


@pytest.mark.parametrize("workers", [[], ["-n", "2"]])
def test_a_live_thread_gets_the_same_trace_without_a_heap_census(
    tmp_path: Path, workers: list[str]
) -> None:
    """With another thread alive the plugin must not enumerate the GC heap,
    and its namespace walk must still credit every reaching test: the same
    oracle as the census path."""
    project = _project(tmp_path)
    (project / "tests" / "conftest.py").write_text(_IDLE_THREAD_CONFTEST)
    assert _trace(project, workers) == _REACHING


def test_the_namespace_walk_finds_methods_wrappers_and_accessors() -> None:
    import types as _types

    import mutation_reach_trace as trace

    mod = _types.ModuleType("_reach_probe")
    exec(  # noqa: S102 - fixed source
        "import functools\n"
        "def plain(): pass\n"
        "def _deco(f):\n"
        "    @functools.wraps(f)\n"
        "    def w(*a): return f(*a)\n"
        "    return w\n"
        "@_deco\n"
        "def wrapped(): pass\n"
        "class C:\n"
        "    def m(self): pass\n"
        "    @staticmethod\n"
        "    def s(): pass\n"
        "    @classmethod\n"
        "    def c(cls): pass\n"
        "    @property\n"
        "    def p(self): return 1\n"
        "    @p.setter\n"
        "    def p(self, v): pass\n",
        mod.__dict__,
    )
    sys.modules["_reach_probe"] = mod
    try:
        names = {
            f.__code__.co_name
            for f in trace.namespace_functions(["_reach_probe", "_absent_module"])
            if isinstance(f, _types.FunctionType)
        }
    finally:
        del sys.modules["_reach_probe"]
    # ``wrapped`` appears twice under one name: the wrapper ``w`` and the
    # original via ``__wrapped__``.
    assert {"plain", "w", "wrapped", "m", "s", "c", "p", "_deco"} <= names


def test_the_namespace_walk_never_runs_a_globals_getattr() -> None:
    """A module global whose ``__getattr__`` raises (a lazy proxy, a mock)
    must neither abort the walk nor hide the functions next to it."""
    import functools
    import types as _types

    import mutation_reach_trace as trace

    class Exploding:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError(f"__getattr__({name!r}) must not be called")

    def original() -> None:
        pass

    mod = _types.ModuleType("_reach_probe_proxy")
    mod.proxy = Exploding()
    mod.wrapped = functools.wraps(original)(lambda: original())
    mod.cached = functools.lru_cache(maxsize=None)(original)
    sys.modules["_reach_probe_proxy"] = mod
    try:
        found = trace.namespace_functions(["_reach_probe_proxy"])
    finally:
        del sys.modules["_reach_probe_proxy"]
    assert mod.proxy in found
    # Both wrapper kinds are still followed to the function they wrap.
    assert sum(f is original for f in found) == 2


def test_the_trace_is_independent_of_test_order(tmp_path: Path) -> None:
    """A test that is first to import a mutated module must not be the only
    one credited (the plugin imports them before any test)."""
    project = _project(tmp_path)
    single = _trace(project, ["-k", "lazy_import"])
    assert single == {"tests/test_reach.py::test_lazy_import_inside_the_test"}


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


def test_lane_args_keep_selection_flags_and_drop_run_flags() -> None:
    args = gen.lane_pytest_args()
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["mutmut"]
    assert "-x" not in args and "-p" not in args
    assert not any(a.startswith("scripts.") for a in args)
    marker = cfg["pytest_add_cli_args"][cfg["pytest_add_cli_args"].index("-m") + 1]
    assert args[args.index("-m") + 1] == marker
    for arg in cfg["pytest_add_cli_args"]:
        if arg.startswith(("--deselect=", "--ignore=")):
            assert arg in args


def test_files_of_is_the_sorted_set_of_files() -> None:
    ids = [
        "tests/b.py::t[x::y]",
        "tests/a.py::t",
        "tests/b.py::u",
        "tests/sub/c.py::C::t",
    ]
    assert gen.files_of(ids) == ["tests/a.py", "tests/b.py", "tests/sub/c.py"]


def test_the_mutmut_lane_reads_the_committed_selection() -> None:
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["mutmut"]
    assert cfg["pytest_add_cli_args_test_selection"] == [
        "@tests/mutation_test_selection.txt"
    ]
    assert gen.FULL_SELECTION == ["tests/"]


def test_the_committed_selection_is_well_formed() -> None:
    """Sorted, unique, and every entry an existing test file -- a cheap
    always-on check; completeness itself is the weekly --check."""
    lines = gen.read_selection()
    assert lines, "an empty selection would make the stats pass run nothing"
    assert lines == sorted(set(lines))
    missing = [p for p in lines if not (REPO / p).is_file()]
    assert not missing, f"selection names files that do not exist: {missing}"
    assert all(
        Path(p).name.startswith("test_") and p.startswith("tests/") for p in lines
    )


# --------------------------------------------------------------------------
# PR-side widening
# --------------------------------------------------------------------------

_SEL = ["tests/test_a.py", "tests/test_b.py"]


def _exists(paths: set[str]):
    return lambda p: p in paths


@pytest.mark.parametrize(
    ("changed", "present", "expected"),
    [
        ([], set(), _SEL),
        (["abicheck/diff_types.py", "README.md"], {"abicheck/diff_types.py"}, _SEL),
        (["tests/test_new.py"], {"tests/test_new.py"}, [*_SEL, "tests/test_new.py"]),
        (["tests/sub/test_deep.py"], {"tests/sub/test_deep.py"}, ["tests/sub/test_deep.py", *_SEL]),
        (["tests/test_0_first.py"], {"tests/test_0_first.py"}, ["tests/test_0_first.py", *_SEL]),
        (["tests/test_a.py"], {"tests/test_a.py"}, _SEL),
        (["tests/test_gone.py"], set(), _SEL),
        (["tests/conftest.py"], {"tests/conftest.py"}, ["tests/"]),
        (["tests/_helpers.py", "tests/test_new.py"], {"tests/_helpers.py", "tests/test_new.py"}, ["tests/"]),
        (["tests/data/x.json"], {"tests/data/x.json"}, _SEL),
    ],
)  # fmt: skip
def test_extend_selection(
    changed: list[str], present: set[str], expected: list[str]
) -> None:
    assert scope.extend_selection(_SEL, changed, _exists(present)) == expected


def test_extend_selection_never_narrows() -> None:
    """Exhaustive over every subset of a small path universe: the result
    always contains the committed selection, or is the whole suite."""
    universe = ["tests/test_a.py", "tests/test_new.py", "tests/conftest.py",
                "tests/_h.py", "abicheck/x.py", "tests/d.json"]  # fmt: skip
    for mask in range(1 << len(universe)):
        changed = [p for i, p in enumerate(universe) if mask >> i & 1]
        out = scope.extend_selection(_SEL, changed, _exists(set(universe)))
        assert out == ["tests/"] or set(_SEL) <= set(out)
        for path in changed:
            if path.startswith("tests/test_") and out != ["tests/"]:
                assert path in out


def test_extended_selection_keeps_the_committed_file_contract() -> None:
    """Whatever the diff, the extended file is sorted and unique -- the same
    invariant ``test_the_committed_selection_is_well_formed`` asserts, which
    the stats pass runs against the *extended* file. Exhaustive over every
    ordered subset of new test paths that sort before, between and after the
    committed entries; the oracle is ``sorted(set(...))`` of what must be
    present, not the function's own construction."""
    import itertools

    committed = ["tests/test_b.py", "tests/test_d.py"]
    pool = ["tests/test_a.py", "tests/test_c.py", "tests/test_e.py",
            "tests/test_b.py", "tests/sub/test_z.py"]  # fmt: skip
    for r in range(len(pool) + 1):
        for changed in itertools.permutations(pool, r):
            out = scope.extend_selection(committed, list(changed), _exists(set(pool)))
            assert out == sorted(set(committed) | set(changed)), changed
