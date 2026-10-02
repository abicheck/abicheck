"""``scripts/usecase_paths.py``: which code each use case runs.

Bug class: attributing an executed line to the wrong function. The first
draft owned a function's ``def`` line by that function, and ``def`` lines
run at import time -- so every function of every imported module read as
reached by every use case, and the importance ranking put 7,862 functions
in the top tier. The invariant: a function is reached by a context exactly
when its body ran under that context. The oracle is the generated code
itself -- each function records its own name when it runs -- never the
span arithmetic under test.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import usecase_paths as up  # noqa: E402

coverage = pytest.importorskip("coverage")


# ── generated package: every function logs itself when it runs ─────────────


def _module_source(n: int) -> tuple[str, list[str]]:
    """A module whose functions take every shape the span logic must handle:
    decorated, nested, methods, async, default arguments evaluated at import,
    docstrings, one-line bodies. Each appends its qualname to ``CALLS`` as its
    first body statement."""
    lines = [
        "import asyncio, functools",
        "CALLS = []",
        "def deco(f):",
        "    CALLS.append('deco')  # runs at import: not a call of f",
        "    return f",
        "def default_value():",
        "    CALLS.append('default_value')",
        "    return 1",
    ]
    # A one-line body shares the def line, which runs at import: it is
    # deliberately left unattributed (no span), never reported as reached.
    lines.append("def oneline(): CALLS.append('oneline')")
    names = ["deco", "default_value"]
    for i in range(n):
        shape = i % 5
        if shape == 0:
            lines += [
                f"def f{i}(x=default_value()):",
                f"    CALLS.append('f{i}')",
                "    return x",
            ]
            names.append(f"f{i}")
        elif shape == 1:
            lines += [
                "@deco",
                f"def f{i}():",
                f'    """doc {i}"""',
                f"    CALLS.append('f{i}')",
            ]
            names.append(f"f{i}")
        elif shape == 2:
            lines += [
                f"def f{i}():",
                f"    CALLS.append('f{i}')",
                "    def inner():",
                f"        CALLS.append('f{i}.<locals>.inner')",
                "    inner()",
            ]
            names += [f"f{i}", f"f{i}.<locals>.inner"]
        elif shape == 3:
            lines += [
                f"class C{i}:",
                "    @functools.lru_cache(maxsize=None)",
                "    def m(self):",
                f"        CALLS.append('C{i}.m')",
                f"def f{i}():",
                f"    CALLS.append('f{i}')",
                f"    C{i}().m()",
            ]
            names += [f"C{i}.m", f"f{i}"]
        else:
            lines += [
                f"async def a{i}():",
                f"    CALLS.append('a{i}')",
                f"def f{i}():",
                f"    CALLS.append('f{i}')",
                f"    asyncio.run(a{i}())",
            ]
            names += [f"a{i}", f"f{i}"]
    return "\n".join(lines) + "\n", names


_RUNNER = """
import importlib.util, json, sys
import coverage
pkg, data_file, plan = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
cov = coverage.Coverage(data_file=data_file, source=[pkg])
cov.start()
mod = None
logged = {}
for ctx, fns in plan.items():
    cov.switch_context(ctx)
    if mod is not None:
        del mod.CALLS[:]
    if mod is None:
        # Imported lazily inside the first context, as a real run imports
        # abicheck modules while a test or flow is running: the def lines,
        # decorators and default arguments execute under that context.
        spec = importlib.util.spec_from_file_location("gen_mod", pkg + "/mod.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    for fn in fns:
        getattr(mod, fn)()
    logged[ctx] = sorted(set(mod.CALLS))
cov.stop()
cov.save()
print(json.dumps(logged))
"""


def _record(tmp_path: Path, n: int, calls: dict[str, list[str]]):
    """Import the generated module under coverage, then call each context's
    top-level functions under that context. Returns what the module logged
    per context and what the tool attributed per context.

    Runs in a child interpreter: a second ``Coverage`` started inside a
    pytest session that is itself under coverage would disturb the outer
    measurement.
    """
    pkg = tmp_path / up.PACKAGE
    pkg.mkdir()
    source, _ = _module_source(n)
    (pkg / "mod.py").write_text(source, encoding="utf-8")
    data_file = tmp_path / ".coverage"
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(pkg), str(data_file), json.dumps(calls)],
        capture_output=True, text=True, check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("COV")},
    )  # fmt: skip
    logged = {ctx: set(fns) for ctx, fns in json.loads(proc.stdout).items()}
    got = up.functions_by_context(data_file, root=tmp_path)
    attributed = {
        ctx: {f.split("::", 1)[1] for f in got.get(ctx, set())} for ctx in calls
    }
    return logged, attributed


#: Ran, but deliberately unattributable (see ``_body_start``).
_UNATTRIBUTABLE = {"oneline"}


@pytest.mark.parametrize("seed", range(6))
def test_a_function_is_reached_exactly_when_its_body_ran(tmp_path, seed) -> None:
    rng = random.Random(seed)
    n = 15
    top = [f"f{i}" for i in range(n)] + ["oneline"]
    calls = {f"ctx{k}": rng.sample(top, rng.randint(0, 6)) for k in range(4)}
    logged, attributed = _record(tmp_path, n, calls)
    for ctx in calls:
        assert attributed[ctx] == logged[ctx] - _UNATTRIBUTABLE, ctx


def test_importing_a_module_reaches_only_what_ran_at_import(tmp_path) -> None:
    """The regression itself: def lines, decorators and default arguments
    run at import and must not count as calls of the functions they define.
    The decorator and the default-argument function really ran, so they
    are reached; nothing else is."""
    logged, attributed = _record(tmp_path, 10, {"idle": []})
    assert logged["idle"] == {"deco", "default_value"}
    assert attributed["idle"] == logged["idle"]


def test_inventory_names_every_generated_function(tmp_path) -> None:
    pkg = tmp_path / up.PACKAGE
    pkg.mkdir()
    source, names = _module_source(10)
    (pkg / "mod.py").write_text(source, encoding="utf-8")
    assert set(up.inventory(tmp_path)[f"{up.PACKAGE}/mod.py"]) == set(names)


def test_a_docstring_only_body_has_no_span() -> None:
    spans = up.function_spans('def f():\n    """only a docstring"""\n')
    assert spans == []


# ── scenario context names ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("tests/test_scenarios.py::test_sc_x|run", "test_sc_x"),
        ("tests/test_scenarios.py::test_sc_x[a-b]|run", "test_sc_x"),
        ("tests/test_scenarios.py::Cls::test_sc_x|run", "test_sc_x"),
        ("tests/test_scenarios.py::test_sc_x|setup", None),
        ("tests/test_scenarios.py::test_sc_x|teardown", None),
        ("", None),
        ("compare-headers/case01", None),
    ],
)
def test_scenario_context_key(context, expected) -> None:
    assert up.scenario_context_key(context) == expected


# ── rank and diff over hand-built recordings ────────────────────────────────


def _doc(runs: dict[str, tuple[str | None, list[str]]], inventory: dict) -> dict:
    return {
        "schema_version": up.SCHEMA_VERSION,
        "runs": {
            rid: {"use_case": uc, "functions": fns} for rid, (uc, fns) in runs.items()
        },
        "inventory": inventory,
        "failures": [],
    }


_INV = {"abicheck/a.py": ["one", "two", "three", "four"]}


def test_tiers_count_distinct_use_cases_not_runs() -> None:
    doc = _doc(
        {
            "r1": ("UC-A", ["abicheck/a.py::one", "abicheck/a.py::two"]),
            "r2": ("UC-A", ["abicheck/a.py::two"]),  # same use case again
            "r3": ("UC-B", ["abicheck/a.py::one"]),
            "r4": ("UC-C", ["abicheck/a.py::one"]),
        },
        _INV,
    )
    ranked = up.rank(doc, shared_at=3)
    assert ranked["abicheck/a.py::one"]["tier"] == up.TIER_SHARED
    assert ranked["abicheck/a.py::two"]["tier"] == up.TIER_USECASE
    assert ranked["abicheck/a.py::two"]["use_cases"] == ["UC-A"]
    assert ranked["abicheck/a.py::three"]["tier"] == up.TIER_UNREACHED


@pytest.mark.parametrize("n", range(0, 7))
@pytest.mark.parametrize("shared_at", [1, 2, 3, 5])
def test_tier_boundaries(n, shared_at) -> None:
    expected = (
        up.TIER_SHARED if n >= shared_at
        else up.TIER_USECASE if n >= 1
        else up.TIER_UNREACHED
    )  # fmt: skip
    assert up.tier_of(n, shared_at) == expected


def test_diff_reports_path_changes_and_relevance_drops() -> None:
    base = _doc(
        {
            "r1": ("UC-A", ["abicheck/a.py::one", "abicheck/a.py::two"]),
            "r2": ("UC-B", ["abicheck/a.py::three", "abicheck/a.py::gone"]),
            "old": ("UC-C", ["abicheck/a.py::one"]),
        },
        {"abicheck/a.py": ["one", "two", "three", "four", "gone"]},
    )
    head = _doc(
        {
            "r1": ("UC-A", ["abicheck/a.py::one", "abicheck/a.py::four"]),
            "r2": ("UC-B", ["abicheck/a.py::three"]),
            "new": ("UC-D", ["abicheck/a.py::one"]),
        },
        _INV,
    )
    d = up.diff_recordings(base, head)
    assert d["path_changes"] == {
        "r1": {"entered": ["abicheck/a.py::four"], "left": ["abicheck/a.py::two"]},
        "r2": {"entered": [], "left": ["abicheck/a.py::gone"]},
    }
    dropped = {r["function"]: r for r in d["relevance_dropped"]}
    assert dropped["abicheck/a.py::two"]["still_defined"] is True
    assert dropped["abicheck/a.py::two"]["use_cases"] == ["UC-A"]
    assert dropped["abicheck/a.py::gone"]["still_defined"] is False
    assert d["newly_reached"] == ["abicheck/a.py::four"]
    assert d["runs_only_in_base"] == ["old"]
    assert d["runs_only_in_head"] == ["new"]


def test_a_function_still_reached_by_another_use_case_did_not_drop() -> None:
    base = _doc(
        {
            "r1": ("UC-A", ["abicheck/a.py::one"]),
            "r2": ("UC-B", ["abicheck/a.py::one"]),
        },
        _INV,
    )
    head = _doc({"r1": ("UC-A", []), "r2": ("UC-B", ["abicheck/a.py::one"])}, _INV)
    d = up.diff_recordings(base, head)
    assert d["relevance_dropped"] == []
    assert d["path_changes"] == {"r1": {"entered": [], "left": ["abicheck/a.py::one"]}}


def test_identical_recordings_diff_to_nothing() -> None:
    doc = _doc({"r1": ("UC-A", ["abicheck/a.py::one"])}, _INV)
    d = up.diff_recordings(doc, doc)
    assert not any(d[k] for k in d)
    assert "No use case changed" in up.render_diff_markdown(d)


@pytest.mark.parametrize(
    ("fail_on", "expected"),
    [([], 0), (["dropped"], 1), (["changed"], 1), (["failed"], 0)],
)
def test_diff_exit_code_only_on_requested_kinds(tmp_path, fail_on, expected) -> None:
    base = _doc({"r1": ("UC-A", ["abicheck/a.py::one", "abicheck/a.py::two"])}, _INV)
    head = _doc({"r1": ("UC-A", ["abicheck/a.py::one"])}, _INV)
    (tmp_path / "b.json").write_text(json.dumps(base))
    (tmp_path / "h.json").write_text(json.dumps(head))
    argv = ["diff", str(tmp_path / "b.json"), str(tmp_path / "h.json")]
    for kind in fail_on:
        argv += ["--fail-on", kind]
    assert up.main(argv) == expected


# ── the flows file stays consistent with the catalog and registry ───────────


def _registry_ids() -> set[str]:
    import yaml

    doc = yaml.safe_load((ROOT / "docs/contribute/usecase-registry.yaml").read_text(encoding="utf-8"))
    return {uc["id"] for uc in doc["use_cases"]}


def test_every_flow_names_a_registered_use_case_and_real_cases() -> None:
    spec = up.load_flows(up.DEFAULT_FLOWS)
    ids = _registry_ids()
    for flow in spec["flows"]:
        assert flow["use_case"] in ids, flow["id"]
        assert ("argv" in flow) != ("steps" in flow), flow["id"]
    for case in spec["cases"]:
        assert (ROOT / "catalog" / "cases" / case).is_dir(), case
    assert len({f["id"] for f in spec["flows"]}) == len(spec["flows"])


def test_every_flow_placeholder_is_one_the_recorder_provides() -> None:
    import string

    known = {"v1", "v2", "h1", "h2", "app", "d1", "d2", "out"}
    spec = up.load_flows(up.DEFAULT_FLOWS)
    for flow in spec["flows"]:
        for step in flow.get("steps") or [flow["argv"]]:
            for arg in step:
                fields = {f for _, f, _, _ in string.Formatter().parse(str(arg)) if f}
                assert fields <= known, (flow["id"], arg)
                # A placeholder the flow uses but does not declare it needs
                # would be skipped silently for a case without it.
                optional = fields & {"h1", "h2", "app", "d1", "d2"}
                needs = set(flow.get("needs", []))
                implied = needs | ({"d1", "d2"} if {"h1", "h2"} <= needs else set())
                assert optional <= implied, (flow["id"], arg)


def test_every_scenario_test_named_in_the_catalog_exists() -> None:
    import test_scenarios

    for sc in up.load_scenarios():
        assert hasattr(test_scenarios, sc["test"]), sc["id"]


# ── changed functions ───────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True,
    )  # fmt: skip


def test_changed_functions_maps_every_kind_of_edit_to_its_function(tmp_path) -> None:
    """Body edit, decorator edit, signature edit, a pure deletion and a new
    function each mark exactly their own function; an untouched neighbour and
    a module-level edit mark nothing."""
    pkg = tmp_path / up.PACKAGE
    pkg.mkdir()
    before = [
        "X = 1",
        "def untouched():",
        "    return 1",
        "def body_edit():",
        "    return 1",
        "@staticmethod",
        "def decorator_edit():",
        "    return 1",
        "def signature_edit(a):",
        "    return a",
        "def deletion():",
        "    a = 1",
        "    return a",
        "class K:",
        "    def method(self):",
        "        return 1",
    ]
    (pkg / "m.py").write_text("\n".join(before) + "\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    after = list(before)
    after[0] = "X = 2"  # module level
    after[4] = "    return 2"
    after[5] = "@classmethod"
    after[8] = "def signature_edit(a, b=0):"
    del after[11]  # deletion(): drop `a = 1`
    after[11] = "    return 1"
    after += ["def added():", "    return 3"]
    (pkg / "m.py").write_text("\n".join(after) + "\n")
    got = {f.split("::", 1)[1] for f in up.changed_functions("HEAD", tmp_path)}
    assert got == {"body_edit", "decorator_edit", "signature_edit", "deletion", "added"}


def test_changed_functions_are_listed_most_relied_on_first() -> None:
    ranked = {
        "abicheck/a.py::low": {"tier": up.TIER_USECASE, "use_cases": ["UC-A"]},
        "abicheck/a.py::high": {
            "tier": up.TIER_SHARED,
            "use_cases": ["UC-A", "UC-B", "UC-C"],
        },
    }
    text = up.render_changed_by_importance(
        {"abicheck/a.py::low", "abicheck/a.py::high", "abicheck/a.py::new"}, ranked
    )
    assert text.index("::high") < text.index("::low") < text.index("::new")
    assert "1 shared, 1 use-case, 1 unreached" in text
