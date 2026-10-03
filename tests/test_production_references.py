"""``scripts/production_references.py``: unreached code with no production
reference (``usecase_paths.py dead``).

Bug class: calling code dead that a production path still uses, or live
that only other dead code uses. The hand-made list behind
``docs/contribute/plans/dead-code-and-single-owner.md`` was built by
re-checking references "to a fixpoint" by hand; done by hand, a helper that
only a dead function calls is easily kept, and a re-export is easily read as
a use. The invariant: a function is dead exactly when no live root reaches
it through the reference graph. The oracle is a breadth-first search over
the *generated* call graph, never the fixpoint loop under test.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import production_references as pr  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fid(i: int, modules: int) -> str:
    return f"abicheck/m{i % modules}.py::fn_{i}"


def _generate(rng: random.Random, n: int, modules: int, root: Path):
    """A package of *n* uniquely named functions over *modules* files. Each
    function calls a random subset of the others (plain name, attribute or
    a string handed to ``getattr``); some are referenced from module level,
    from ``scripts/`` or from a workflow; some are "reached" by a use case;
    tests reference a random subset, which must change nothing."""
    calls = {i: rng.sample(range(n), rng.randint(0, 3)) for i in range(n)}
    module_refs = set(rng.sample(range(n), rng.randint(0, 2)))
    script_refs = set(rng.sample(range(n), rng.randint(0, 2)))
    workflow_refs = set(rng.sample(range(n), rng.randint(0, 1)))
    reached = set(rng.sample(range(n), rng.randint(0, 3)))
    bodies: dict[int, list[str]] = {
        m: ["import importlib", "from . import *"] for m in range(modules)
    }
    for i in range(n):
        lines = [f"def fn_{i}():"]
        for j in calls[i]:
            form = rng.randrange(3)
            if form == 0:
                lines.append(f"    fn_{j}()")
            elif form == 1:
                lines.append(
                    f"    importlib.import_module('abicheck.m{j % modules}').fn_{j}()"
                )
            else:
                lines.append(
                    f"    getattr(importlib.import_module('abicheck.m{j % modules}'), 'fn_{j}')()"
                )
        lines.append("    return None")
        bodies[i % modules] += lines
    for i in module_refs:
        bodies[i % modules].append(f"REGISTRY_{i} = [fn_{i}]")
    for m, lines in bodies.items():
        _write(root, f"abicheck/m{m}.py", "\n".join(lines) + "\n")
    _write(
        root,
        "scripts/tool.py",
        "from abicheck import m0\n"
        + "".join(f"def main_{i}():\n    m0.fn_{i}()\n" for i in script_refs),
    )
    _write(
        root,
        ".github/workflows/w.yml",
        "".join(
            f"run: python -c 'from abicheck.m0 import fn_{i}; fn_{i}()'\n"
            for i in workflow_refs
        ),
    )
    tested = rng.sample(range(n), rng.randint(0, n))
    _write(
        root,
        "tests/test_gen.py",
        "".join(f"def test_{i}():\n    fn_{i}()\n" for i in tested),
    )
    roots = module_refs | script_refs | workflow_refs | reached
    return calls, roots, reached


def _bfs_dead(n: int, calls: dict[int, list[int]], roots: set[int]) -> set[int]:
    seen, queue = set(roots), deque(roots)
    while queue:
        for j in calls[queue.popleft()]:
            if j not in seen:
                seen.add(j)
                queue.append(j)
    return set(range(n)) - seen


@pytest.mark.parametrize("seed", range(25))
def test_dead_is_exactly_what_no_live_root_reaches(tmp_path, seed) -> None:
    rng = random.Random(seed)
    n, modules = rng.randint(4, 18), rng.randint(1, 4)
    calls, roots, reached = _generate(rng, n, modules, tmp_path)
    unreached = {_fid(i, modules) for i in range(n) if i not in reached}
    report = pr.dead_report(tmp_path, unreached)
    expected = {_fid(i, modules) for i in _bfs_dead(n, calls, roots)}
    assert set(report.dead) == expected
    # Every unreached function lands in exactly one class.
    classes = [
        set(report.dead),
        set(report.documented),
        set(report.decided),
        set(report.live),
        set(report.unverifiable),
    ]
    assert sum(len(c) for c in classes) == len(unreached)
    assert set().union(*classes) == unreached
    # A live verdict names a reference that is not inside dead code.
    dead = set(report.dead)
    for site in report.live.values():
        assert site.enclosing is None or pr._outer(site.enclosing) not in dead


def test_fixpoint_result_does_not_depend_on_candidate_order() -> None:
    rng = random.Random(7)
    names = {f"f{i}": f"n{i}" for i in range(30)}
    sites: dict[str, list[pr.Site]] = {}
    for fid, name in names.items():
        sites[name] = [
            pr.Site("x", 1, rng.choice([None, *names, "abicheck/live.py::kept"]))
            for _ in range(rng.randint(0, 2))
        ]
    first, _ = pr.dead_fixpoint(set(names), names, sites)
    shuffled = dict(sorted(names.items(), key=lambda _: rng.random()))
    second, _ = pr.dead_fixpoint(set(shuffled), shuffled, sites)
    assert first == second


def _report_for(
    tmp_path: Path, files: dict[str, str], unreached: set[str]
) -> pr.DeadReport:
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    return pr.dead_report(tmp_path, unreached)


def test_a_re_export_is_not_a_use_but_a_rename_is(tmp_path) -> None:
    report = _report_for(
        tmp_path,
        {
            "abicheck/a.py": "def exported():\n    return 1\ndef renamed():\n    return 2\n",
            "abicheck/b.py": '"""Mentions exported() in a docstring."""\n'
            "from .a import exported\n"
            "from .a import renamed as other\n"
            "__all__ = ['exported']\n"
            "VALUE = other()\n",
        },
        {"abicheck/a.py::exported", "abicheck/a.py::renamed"},
    )
    assert report.dead == ["abicheck/a.py::exported"]
    assert "abicheck/a.py::renamed" in report.live


def test_a_workflow_comment_is_not_a_reference(tmp_path) -> None:
    report = _report_for(
        tmp_path,
        {
            "abicheck/a.py": "def commented():\n    return 1\ndef run_by_ci():\n    return 2\n",
            ".github/workflows/w.yml": "# mirrors commented()\nrun: python -c 'import abicheck.a as a; a.run_by_ci()'\n",
        },
        {"abicheck/a.py::commented", "abicheck/a.py::run_by_ci"},
    )
    assert report.dead == ["abicheck/a.py::commented"]
    assert report.live["abicheck/a.py::run_by_ci"].path == ".github/workflows/w.yml"


def test_functions_not_called_by_name_are_unverifiable(tmp_path) -> None:
    report = _report_for(
        tmp_path,
        {
            "abicheck/a.py": "import ast, click\n"
            "@click.command('x')\n"
            "def command():\n    return 1\n"
            "class V(ast.NodeVisitor):\n"
            "    def visit_Name(self, node):\n        return node\n"
            "class Own:\n"
            "    def __eq__(self, other):\n        return True\n"
            "    @property\n"
            "    def prop(self):\n        return 1\n"
            "class Sub(Own):\n"
            "    def plain(self):\n        return 2\n",
        },
        {
            "abicheck/a.py::command",
            "abicheck/a.py::V.visit_Name",
            "abicheck/a.py::Own.__eq__",
            "abicheck/a.py::Own.prop",
            "abicheck/a.py::Sub.plain",
        },
    )
    assert set(report.unverifiable) == {
        "abicheck/a.py::command",
        "abicheck/a.py::V.visit_Name",
        "abicheck/a.py::Own.__eq__",
    }
    # A transparent decorator and a package-defined base leave the method
    # judgeable by name.
    assert set(report.dead) == {"abicheck/a.py::Own.prop", "abicheck/a.py::Sub.plain"}


def test_a_nested_function_goes_with_its_container(tmp_path) -> None:
    report = _report_for(
        tmp_path,
        {
            "abicheck/a.py": "def outer():\n"
            "    def inner():\n        return helper()\n"
            "    return inner()\n"
            "def helper():\n    return 1\n",
        },
        {
            "abicheck/a.py::outer",
            "abicheck/a.py::outer.<locals>.inner",
            "abicheck/a.py::helper",
        },
    )
    assert set(report.dead) == {"abicheck/a.py::outer", "abicheck/a.py::helper"}


def test_documented_and_decided_are_kept_apart_from_dead(tmp_path) -> None:
    report = _report_for(
        tmp_path,
        {
            "abicheck/a.py": "def documented():\n    return only_for_documented()\n"
            "def only_for_documented():\n    return 0\n"
            "def decided():\n    return 2\n"
            "def tested():\n    return 3\n",
            "docs/use/api.md": "Call `documented()` from a release script.\n",
            "docs/contribute/plans/p.md": "`decided()` is planned for phase 2.\n",
            "tests/test_a.py": "def test_t():\n    tested()\n",
        },
        {
            "abicheck/a.py::documented",
            "abicheck/a.py::only_for_documented",
            "abicheck/a.py::decided",
            "abicheck/a.py::tested",
        },
    )
    # Kept API is a root: its helper is kept with it, not reported dead.
    assert (
        report.live["abicheck/a.py::only_for_documented"].enclosing
        == "abicheck/a.py::documented"
    )
    assert report.documented == {"abicheck/a.py::documented": ["docs/use/api.md"]}
    assert report.decided == {"abicheck/a.py::decided": ["docs/contribute/plans/p.md"]}
    assert report.dead == ["abicheck/a.py::tested"]
    assert report.tests == {"abicheck/a.py::tested": ["tests/test_a.py"]}
    text = pr.render_markdown(report)
    assert "test-only (1 test files)" in text
    assert "docs/use/api.md" in text


def test_dead_subcommand_classifies_a_recordings_unreached_functions(
    tmp_path, capsys
) -> None:
    """``usecase_paths.py dead`` end to end: a recording's reached functions
    are roots, its unreached ones are classified, and the JSON names each."""
    import json

    import usecase_paths as up

    _write(
        tmp_path,
        "abicheck/a.py",
        "def ran():\n    return helper()\n"
        "def helper():\n    return 1\n"
        "def orphan():\n    return chained()\n"
        "def chained():\n    return 2\n",
    )
    recording = tmp_path / "rec.json"
    recording.write_text(
        json.dumps(
            {
                "schema_version": up.SCHEMA_VERSION,
                "runs": {
                    "r": {"use_case": "UC-X", "functions": ["abicheck/a.py::ran"]}
                },
                "inventory": up.inventory(tmp_path),
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "dead.json"
    assert (
        up.main(["dead", str(recording), "--root", str(tmp_path), "--json", str(out)])
        == 0
    )
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["dead"] == ["abicheck/a.py::chained", "abicheck/a.py::orphan"]
    assert doc["live"] == {"abicheck/a.py::helper": "abicheck/a.py:2"}
    assert (
        "| dead (no production reference, undocumented) | 2 |"
        in capsys.readouterr().out
    )
