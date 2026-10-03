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


def _trace(
    project: Path, extra: list[str], inherited: dict[str, str] | None = None
) -> set[str]:
    out = project / "reach"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("PYTEST_XDIST")},
        **(inherited or {}),
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


@pytest.mark.parametrize("workers", [[], ["-n", "2"]])
def test_a_run_nested_in_an_xdist_worker_keeps_every_worker_file(
    tmp_path: Path, workers: list[str]
) -> None:
    """A trace launched from inside an xdist worker inherits that worker's
    ``PYTEST_XDIST_*`` variables; its controller must not write the file its
    own ``gw0`` writes (which dropped every hit ``gw0`` recorded)."""
    inherited = {"PYTEST_XDIST_WORKER": "gw0", "PYTEST_XDIST_WORKER_COUNT": "4"}
    assert _trace(_project(tmp_path), workers, inherited) == _REACHING


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


def _well_formed_problems(lines: list[str], is_file) -> list[str]:  # type: ignore[no-untyped-def]
    """Why *lines* is not a selection the stats pass can run, or ``[]``.

    Either the whole suite, spelled ``gen.FULL_SELECTION`` (what a PR's
    ``extend-selection`` widens to when it changes a shared test module), or
    a sorted, unique list of existing ``tests/**/test_*.py`` files."""
    if not lines:
        return ["an empty selection would make the stats pass run nothing"]
    if lines == gen.FULL_SELECTION:
        return []
    problems = []
    if lines != sorted(set(lines)):
        problems.append("not sorted and unique")
    missing = [p for p in lines if not is_file(p)]
    if missing:
        problems.append(f"selection names files that do not exist: {missing}")
    if not all(
        Path(p).name.startswith("test_") and p.startswith("tests/") for p in lines
    ):
        problems.append("an entry is not a tests/**/test_*.py file")
    return problems


def test_the_committed_selection_is_well_formed() -> None:
    """A cheap always-on check; completeness itself is the weekly --check.
    The mutation lane rewrites this file in place (``extend-selection``)
    before its stats pass, which may run this very test against the result."""
    problems = _well_formed_problems(
        gen.read_selection(), lambda p: (REPO / p).is_file()
    )
    assert not problems, problems


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


def test_every_extended_selection_is_one_the_lane_accepts() -> None:
    """Exhaustive over a small universe: whatever `extend-selection` writes
    over the committed file must pass the well-formedness check, since the
    widened stats pass runs that check against it (a PR touching a test
    helper once failed the mutation lane on its own `tests/` sentinel)."""
    universe = ["tests/test_new.py", "tests/sub/test_deep.py", "tests/test_0.py",
                "tests/conftest.py", "tests/_h.py", "abicheck/x.py"]  # fmt: skip
    for mask in range(1 << len(universe)):
        changed = [p for i, p in enumerate(universe) if mask >> i & 1]
        out = scope.extend_selection(_SEL, changed, _exists(set(universe)))
        exists = set(universe) | set(_SEL)
        assert not _well_formed_problems(out, exists.__contains__), (changed, out)


# ── census safety: the namespace fallback (gc-census-concurrent-thread) ─────


def _armed(monkeypatch: pytest.MonkeyPatch, *, safe: bool):  # type: ignore[no-untyped-def]
    import importlib

    import mutation_reach_trace as trace

    paths = trace.only_mutate_paths(REPO)
    names = trace.module_names(REPO, paths)
    for name in names:
        importlib.import_module(name)
    monkeypatch.setattr(trace, "gc_census_is_safe", lambda: safe)
    mon = trace.Monitor(paths, names)
    try:
        mon.arm()
        return paths, set(mon.armed), mon.module_ids
    finally:
        mon.close()


@pytest.mark.skipif(
    not hasattr(sys, "monitoring"), reason="the plugin needs sys.monitoring (3.12+)"
)
def test_fallback_arms_every_def_without_a_heap_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With another thread alive the plugin must not enumerate the heap, and
    the namespace walk it uses instead must still arm every function and
    method not nested inside another function, in every only_mutate file.

    The oracle is the files' own AST, not the walker: one expected entry per
    such ``def``, matched by file, name and first line (a decorator's line
    when decorated). Nested defs, lambdas and comprehensions come from
    ``co_consts`` either way, so they are not what the fallback risks
    missing."""
    import ast

    import mutation_reach_trace as trace

    def census_forbidden() -> list[object]:
        raise AssertionError("heap census taken while unsafe")

    monkeypatch.setattr(trace.gc, "get_objects", census_forbidden)
    paths, fallback, module_ids = _armed(monkeypatch, safe=False)
    monkeypatch.undo()
    _, census, _ = _armed(monkeypatch, safe=True)

    assert module_ids == (), "an unsafe arm must leave the census to retry later"
    assert fallback <= census

    missing = []
    for path in sorted(paths):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        todo: list[tuple[ast.AST, bool]] = [(tree, False)]
        while todo:
            node, nested = todo.pop()
            for child in ast.iter_child_nodes(node):
                is_def = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                if is_def and not nested:
                    lines = {child.lineno, *(d.lineno for d in child.decorator_list)}
                    if not any(
                        c.co_filename == path
                        and c.co_name == child.name
                        and c.co_firstlineno in lines
                        for c in fallback
                    ):
                        missing.append(f"{path}:{child.lineno} {child.name}")
                todo.append((child, nested or is_def or isinstance(child, ast.Lambda)))
    assert not missing, missing
    assert len(fallback) > 100  # vacuity guard: the walk found the real tree
