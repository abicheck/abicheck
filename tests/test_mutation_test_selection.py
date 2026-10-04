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
import random
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

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


def _trace(
    project: Path, extra: list[str], inherited_worker: str | None = None
) -> set[str]:
    out = project / "reach"
    # The nested run is its own session: an outer xdist worker's identity
    # (PYTEST_XDIST_WORKER=gwN) would otherwise be inherited by the nested
    # *controller*, whose output file then collides with -- and overwrites --
    # the nested worker of the same name, dropping that worker's hits.
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("PYTEST_XDIST_")},
        "MUTATION_REACH_OUT": str(out),
        "PYTHONPATH": os.pathsep.join([str(REPO / "scripts"), str(project)]),
    }
    env.pop("PYTEST_XDIST_WORKER", None)
    if inherited_worker is not None:
        env["PYTEST_XDIST_WORKER"] = inherited_worker
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


#: A conftest that keeps a second Python thread alive for the whole session
#: and makes any heap census taken beside it fail loudly -- the condition
#: under which ``gc.get_objects()`` corrupts another thread's ``tuple(...)``
#: (``memory_trace.gc_census_is_safe``).
_LINGERING_THREAD_CONFTEST = """
import gc
import threading

threading.Thread(target=threading.Event().wait, daemon=True).start()
_census = gc.get_objects


def _guarded_census(*args, **kwargs):
    assert threading.active_count() == 1, "heap census beside a live thread"
    return _census(*args, **kwargs)


gc.get_objects = _guarded_census
"""


@pytest.mark.parametrize("workers", [[], ["-n", "2"]])
def test_the_trace_never_takes_a_census_beside_another_thread(
    tmp_path: Path, workers: list[str]
) -> None:
    """With a second thread alive throughout, the plugin must not enumerate
    the heap, and its namespace walk must still find every reaching test --
    nested functions, methods and lambdas included. Same oracle as below."""
    project = _project(tmp_path)
    (project / "tests" / "conftest.py").write_text(_LINGERING_THREAD_CONFTEST)
    assert _trace(project, workers) == _REACHING


@pytest.mark.parametrize("workers", [[], ["-n", "2"], ["-p", "no:randomly", "-n", "0"]])
def test_the_trace_records_exactly_the_reaching_tests(
    tmp_path: Path, workers: list[str]
) -> None:
    """Every reach path is recorded and nothing else is, serial or under
    xdist. The oracle is the fixture's own construction, not the plugin."""
    assert _trace(_project(tmp_path), workers) == _REACHING


@pytest.mark.parametrize("inherited", ["gw0", "gw1", "main"])
def test_a_worker_name_inherited_from_an_enclosing_run_loses_nothing(
    tmp_path: Path, inherited: str
) -> None:
    """A trace run launched from inside an xdist worker inherits that
    worker's ``PYTEST_XDIST_WORKER``. The inner controller must not take the
    name and overwrite the inner worker of the same name with its own empty
    set -- which is how this test file failed in CI's sharded unit lane,
    whenever it landed on outer worker gw0 or gw1."""
    assert _trace(_project(tmp_path), ["-n", "2"], inherited) == _REACHING


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
    """Sorted, unique, and every entry an existing test file -- or the
    whole-suite sentinel a PR touching a shared test module widens it to
    (this check runs inside mutmut's stats pass, against the widened file).
    A cheap always-on check; completeness itself is the weekly --check."""
    assert not gen.selection_problems(
        gen.read_selection(), lambda p: (REPO / p).is_file()
    )


def test_selection_rule_accepts_both_legitimate_shapes_and_nothing_else() -> None:
    every = lambda p: True  # noqa: E731
    assert gen.FULL_SELECTION == ["tests/"]
    assert not gen.selection_problems(list(gen.FULL_SELECTION), every)
    assert not gen.selection_problems(["tests/a/test_x.py", "tests/test_y.py"], every)
    assert gen.selection_problems([], every)
    assert gen.selection_problems(["tests/test_b.py", "tests/test_a.py"], every)
    assert gen.selection_problems(["tests/test_a.py", "tests/test_a.py"], every)
    assert gen.selection_problems(["tests/conftest.py"], every)
    assert gen.selection_problems(["tests/", "tests/test_a.py"], every)
    assert gen.selection_problems(["tests/test_a.py"], lambda p: False)


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
        (["tests/test_a.py"], {"tests/test_a.py"}, _SEL),
        (["tests/test_gone.py"], set(), _SEL),
        (["tests/conftest.py"], {"tests/conftest.py"}, _SEL),
        (["tests/_helpers.py", "tests/test_new.py"], {"tests/_helpers.py", "tests/test_new.py"}, [*_SEL, "tests/test_new.py"]),
        (["tests/data/x.json"], {"tests/data/x.json"}, _SEL),
    ],
)  # fmt: skip
def test_extend_selection(
    changed: list[str], present: set[str], expected: list[str]
) -> None:
    assert scope.extend_selection(_SEL, changed, _exists(present)) == expected


@pytest.mark.parametrize("seed", range(25))
def test_the_widened_selection_passes_the_committed_files_own_check(seed: int) -> None:
    """Whatever a branch changes, the file ``extend-selection`` writes must
    satisfy the invariant the suite asserts about the committed file
    (sorted, unique, ``tests/**/test_*.py``) -- the stats pass runs that
    assertion against the widened file, inside mutmut. Inputs are random
    names in random order, including ones sorting before every committed
    entry; the oracle is the well-formedness rule, not extend_selection."""
    rng = random.Random(seed)
    pool = [f"tests/{d}test_{rng.choice('abcxyz')}{i}.py"
            for i, d in enumerate(rng.choices(["", "sub/", "unit/a/"], k=12))]  # fmt: skip
    committed = sorted(set(rng.sample(pool, 4)))
    changed = rng.sample(pool, rng.randint(0, len(pool)))
    out = scope.extend_selection(committed, changed, lambda p: True)
    assert not gen.selection_problems(out, lambda p: True)
    assert out == sorted(set(out))
    assert set(committed) | set(changed) == set(out)
    assert all(PurePosixPath(p).name.startswith("test_") for p in out)


def test_extend_selection_never_narrows() -> None:
    """Exhaustive over every subset of a small path universe: the result
    always contains the committed selection, and never widens to the whole
    suite (that made every helper-touching PR run ~50k tests per mutant)."""
    universe = ["tests/test_a.py", "tests/test_new.py", "tests/conftest.py",
                "tests/_h.py", "abicheck/x.py", "tests/d.json"]  # fmt: skip
    for mask in range(1 << len(universe)):
        changed = [p for i, p in enumerate(universe) if mask >> i & 1]
        out = scope.extend_selection(_SEL, changed, _exists(set(universe)), {})
        assert set(_SEL) <= set(out) and "tests/" not in out
        # The widened file is checked by the suite it feeds, under the same
        # rule as the committed one (test_the_committed_selection_is_well_formed).
        assert not gen.selection_problems(out, lambda p: True), out
        for path in changed:
            if path.startswith("tests/test_"):
                assert path in out


# --------------------------------------------------------------------------
# The census-free fallback finds what the census finds
# --------------------------------------------------------------------------


def test_namespace_walk_reaches_every_code_object_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With another thread alive the plugin may not take a heap census
    (bug class gc-census-concurrent-thread) and walks the only_mutate
    modules instead; it must still arm every code object the file defines.

    Oracle: the file's own compiled code tree (``compile`` + nested
    ``co_consts``), matched by qualified name -- independent of both the walk
    and the census, and taking no census itself.
    """
    import importlib.util

    import mutation_reach_trace as trace

    src = tmp_path / "walked.py"
    text = (
        _MUTATED
        + "import functools\n"
        + "def _deco(f):\n    @functools.wraps(f)\n    def w(*a):\n        return f(*a)\n    return w\n"
        + "@_deco\ndef wrapped(x):\n    return x\n"
        + "class Props:\n"
        + "    @staticmethod\n    def s():\n        return 1\n"
        + "    @classmethod\n    def c(cls):\n        return 2\n"
        + "    @property\n    def p(self):\n        return 3\n"
        + "    class Nested:\n        def deep(self):\n            return 4\n"
        # Reachable only through a container, a closure without
        # functools.wraps, a partial, a bound method or a default.
        + "def _in_list():\n    return 5\n"
        + "CALLBACKS = [(_in_list,)]\n"
        + "def _plain(f):\n    def g():\n        return f()\n    return g\n"
        + "def _hidden():\n    return 6\n"
        + "exposed = _plain(_hidden)\n"
        + "def _partial_target(a, b):\n    return a + b\n"
        + "PARTIAL = functools.partial(_partial_target, 1)\n"
        + "def _bound_target(self):\n    return 7\n"
        + "BOUND = _bound_target.__get__(object())\n"
        + "def _default_target():\n    return 8\n"
        + "def uses_default(cb=_default_target):\n    return cb()\n"
        + "del _in_list, _hidden, _partial_target, _bound_target, _default_target\n"
    )
    src.write_text(text)
    # Imported under a name module_names() would not predict, the way a
    # test's own importlib load can.
    spec = importlib.util.spec_from_file_location("unpredicted_name", src)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "unpredicted_name", module)
    spec.loader.exec_module(module)

    def is_function_code(code: object) -> bool:
        # Class bodies are code objects too, but no function object owns
        # one; arm() only needs the code that functions run.
        return code.co_name != "<module>" and bool(code.co_flags & 0x2)  # CO_NEWLOCALS

    expected = {
        c.co_qualname
        for c in trace.code_objects(compile(text, str(src), "exec"))
        if is_function_code(c)
    }
    assert {
        "helper",
        "helper.<locals>.inner",
        "Thing.method",
        "Props.Nested.deep",
        "_in_list",
        "_hidden",
        "_partial_target",
        "_bound_target",
        "_default_target",
    } <= expected

    mon = object.__new__(trace.Monitor)
    mon.paths = frozenset({str(src.resolve())})
    mon.modules = ["pkg.never_imported"]
    monkeypatch.setattr(trace, "gc_census_is_safe", lambda: False)
    reached = {
        c.co_qualname
        for f in mon._live_functions()
        if f.__code__.co_filename == str(src.resolve())
        for c in trace.code_objects(f.__code__)
    }
    assert reached == expected


def test_arm_rescans_when_a_matching_module_appears_under_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``arm()`` runs before every test and skips the walk while nothing it
    tracks changed. A module of an only_mutate file imported *later* under a
    name ``module_names`` did not predict must count as a change -- else its
    functions are never armed and the tests reaching them go unrecorded."""
    import importlib.util

    import mutation_reach_trace as trace

    src = tmp_path / "late.py"
    src.write_text("def f():\n    return 1\n")
    mon = object.__new__(trace.Monitor)
    mon.paths = frozenset({str(src.resolve())})
    mon.modules = ["pkg.never_imported"]
    mon.module_ids = ()
    mon.armed = set()
    scans: list[int] = []
    monkeypatch.setattr(
        mon, "_live_functions", lambda census=None: scans.append(1) or iter(())
    )
    # A partial (threaded) walk deliberately rescans on the next test; this
    # test is about the module set, so it must not depend on whether an
    # earlier test in this worker left a thread alive.
    monkeypatch.setattr(trace, "gc_census_is_safe", lambda: True)

    mon.arm()
    mon.arm()
    assert len(scans) == 1, "an unchanged module set must not rescan"

    spec = importlib.util.spec_from_file_location("loaded_late_under_new_name", src)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "loaded_late_under_new_name", module)
    spec.loader.exec_module(module)
    mon.arm()
    assert len(scans) == 2, "a newly loaded only_mutate module must trigger a rescan"
    mon.arm()
    assert len(scans) == 2


# --------------------------------------------------------------------------
# Helper changes add their importers, not the whole suite
# --------------------------------------------------------------------------

_SOURCES = {
    "tests/_util.py": "import os\n",
    "tests/_mid.py": "from tests._util import thing\n",
    "tests/regressions/manifest.py": "X = 1\n",
    "tests/regressions/__init__.py": "",
    "tests/test_direct.py": "from ._util import thing\n",
    "tests/test_via_mid.py": "import tests._mid as m\n",
    "tests/test_pkg_form.py": "from tests.regressions import manifest\n",
    "tests/test_dotted.py": "from regressions.manifest import BUG_CLASSES\n",
    "tests/test_unrelated.py": "import json\nfrom abicheck import util\n",
    "tests/test_mentions_in_text.py": "# _util is mentioned, never imported\nX = '_util'\n",
}


@pytest.mark.parametrize(
    ("helper", "expected"),
    [
        ("tests/_util.py", {"tests/test_direct.py", "tests/test_via_mid.py"}),
        ("tests/_mid.py", {"tests/test_via_mid.py"}),
        ("tests/regressions/manifest.py", {"tests/test_pkg_form.py", "tests/test_dotted.py"}),
    ],
)  # fmt: skip
def test_helper_importers_follows_every_import_spelling(
    helper: str, expected: set[str]
) -> None:
    """Independent oracle: the expected sets are written by hand from the
    fixture's import lines, covering relative, aliased, package-attribute
    and dotted spellings, and a transitive helper chain."""
    assert scope.helper_importers([helper], _SOURCES) == expected


def test_a_helper_change_adds_its_importers_and_nothing_else() -> None:
    """A helper change used to widen the stats pass to the whole suite,
    which made the run outlast mutmut's timeout. It now adds exactly the
    files that import the helper, and the result passes the well-formed
    check the stats pass runs against it."""
    present = set(_SOURCES)
    out = scope.extend_selection(_SEL, ["tests/_util.py"], _exists(present), _SOURCES)
    assert out == sorted({*_SEL, "tests/test_direct.py", "tests/test_via_mid.py"})
    assert out != gen.FULL_SELECTION
    assert not gen.selection_problems(out, lambda p: True)


@pytest.mark.parametrize("seed", range(20))
def test_helper_importers_is_the_reachability_closure(seed: int) -> None:
    """Random import graphs over helpers and tests: the answer equals a
    plain BFS over the generated edge list (the oracle never parses text)."""
    rng = random.Random(seed)
    helpers = [f"tests/_h{i}.py" for i in range(6)]
    tests_ = [f"tests/test_t{i}.py" for i in range(8)]
    edges = {p: rng.sample(helpers, rng.randint(0, 2)) for p in helpers + tests_}
    sources = {
        p: "".join(
            f"from tests.{PurePosixPath(d).stem} import x\n" for d in deps if d != p
        )
        for p, deps in edges.items()
    }
    changed = rng.sample(helpers, rng.randint(1, 2))
    reached, todo = set(changed), list(changed)
    while todo:
        cur = todo.pop()
        for p, deps in edges.items():
            if cur in deps and p != cur and p not in reached:
                reached.add(p)
                todo.append(p)
    expected = {p for p in reached if p in tests_}
    assert scope.helper_importers(changed, sources) == expected
