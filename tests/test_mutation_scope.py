"""Tests for scripts/mutation_scope.py and the gate paths that use it.

Two run-cost reductions, each stated as the invariant it must keep:

* function scoping executes a mutant set that *contains every mutant the
  diff-scoped gate reads* — checked against mutmut's real key mangling via
  `mutation_results.split_mutant_key`, an independent decoder, not the
  encoder under test;
* sharding is a partition — every module in exactly one shard, for every
  shard count — and merging per-shard baselines refuses anything else.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import itertools
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scope = _load("mutation_scope")
gate = _load("check_mutation_score")
results = _load("mutation_results")


# --------------------------------------------------------------------------
# Pattern encoding vs mutmut's key decoding
# --------------------------------------------------------------------------

_NAMES = ["f", "foo", "foobar", "foo_bar", "_private", "__init__", "x_y"]


def _mutmut_key(module_path: str, qualname: str, n: int) -> str:
    """mutmut 3.x's own key shape (see mutation_results.split_mutant_key)."""
    dotted = module_path[:-3].replace("/", ".")
    parts = qualname.split(".")
    mangled = f"x_{parts[0]}" if len(parts) == 1 else "xǁ" + "ǁ".join(parts)
    return f"{dotted}.{mangled}__mutmut_{n}"


@pytest.mark.parametrize(
    "qualname",
    [*_NAMES, *(f"C.{n}" for n in _NAMES), "Outer.Inner.meth"],
)
def test_pattern_matches_exactly_its_own_functions_mutants(qualname: str) -> None:
    module = "abicheck/pkg/mod.py"
    pattern = scope.function_scope_pattern(module, qualname)
    universe = [
        _mutmut_key(m, q, n)
        for m in (module, "abicheck/pkg/other.py")
        for q in [*_NAMES, *(f"C.{x}" for x in _NAMES), "Outer.Inner.meth"]
        for n in (1, 2, 17)
    ]
    for key in universe:
        decoded = results.split_mutant_key(key)
        assert decoded is not None
        expected = decoded == ("abicheck.pkg.mod", qualname)
        assert fnmatch.fnmatch(key, pattern) is expected, (key, pattern)


def test_function_run_scope_covers_everything_the_diff_gate_reads() -> None:
    """Property: for random touched sets, a survivor that check_diff_scoped
    would report is always inside the executed scope."""
    rng = random.Random(1234)
    only_mutate = ["abicheck/a.py", "abicheck/b.py", "abicheck/c.py"]
    qualnames = ["f", "g", "C.m", "C.n", "h"]
    for _ in range(200):
        touched = {
            path: set(rng.sample(qualnames, rng.randint(0, 3)))
            | ({"<module>"} if rng.random() < 0.2 else set())
            for path in rng.sample([*only_mutate, "abicheck/helper.py"], 2)
        }
        unattributable = set(rng.sample(only_mutate, rng.randint(0, 1)))
        patterns, whole = scope.function_run_scope(touched, only_mutate, unattributable)
        assert whole == unattributable
        records = [
            results.MutantRecord(
                key=_mutmut_key(m, q, 1),
                module=m[:-3].replace("/", "."),
                function=q,
                status="survived",
            )
            for m in only_mutate
            for q in qualnames
        ]
        read_by_gate = gate.check_diff_scoped(records, touched)
        executed = {
            r.key for r in records if any(fnmatch.fnmatch(r.key, p) for p in patterns)
        }
        for line in read_by_gate:
            key = line.rsplit("[", 1)[1].rstrip("]")
            assert key in executed, (key, patterns)


def test_function_run_scope_ignores_paths_outside_only_mutate() -> None:
    patterns, whole = scope.function_run_scope(
        {"abicheck/helper.py": {"f"}, "tests/test_x.py": {"test_y"}},
        ["abicheck/a.py"],
    )
    assert (patterns, whole) == ([], set())


def test_module_scope_edit_alone_contributes_no_pattern() -> None:
    patterns, _ = scope.function_run_scope(
        {"abicheck/a.py": {"<module>"}}, ["abicheck/a.py"]
    )
    assert patterns == []


# --------------------------------------------------------------------------
# Sharding
# --------------------------------------------------------------------------


def _write_modules(root: Path, rng: random.Random, count: int) -> list[str]:
    """Modules of random top-level functions and class methods."""
    (root / "abicheck").mkdir(exist_ok=True)
    modules = []
    for i in range(count):
        lines = []
        for j in range(rng.randint(0, 6)):
            body = "\n".join("    x = 1" for _ in range(rng.randint(1, 40)))
            lines.append(f"def f{j}():\n{body}\n")
        if rng.random() < 0.5:
            lines.append("class C:\n    def m(self):\n        return 1\n")
        path = f"abicheck/m{i}.py"
        (root / path).write_text("\n".join(lines))
        modules.append(path)
    return modules


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 30])
def test_shards_partition_every_function(tmp_path: Path, n: int) -> None:
    """Disjoint, exhaustive and order-independent over random modules. The
    oracle for "every function" is a plain AST walk of each file, not
    `mutation_units`."""
    import ast

    rng = random.Random(n)
    modules = _write_modules(tmp_path, rng, 22)
    expected = set()
    for m in modules:
        for node in ast.parse((tmp_path / m).read_text()).body:
            if isinstance(node, ast.FunctionDef):
                expected.add((m, node.name))
            elif isinstance(node, ast.ClassDef):
                expected |= {(m, f"{node.name}.{f.name}") for f in node.body}
    shards = [scope.shard_assignment(modules, k, n, tmp_path) for k in range(1, n + 1)]
    flat = list(itertools.chain.from_iterable(shards))
    assert set(flat) == expected
    assert len(flat) == len(set(flat))
    shuffled = modules[:]
    rng.shuffle(shuffled)
    assert shards == [
        scope.shard_assignment(shuffled, k, n, tmp_path) for k in range(1, n + 1)
    ]


def test_a_large_module_is_split_across_shards(tmp_path: Path) -> None:
    """The reason the unit is a function: one module holding most of the
    population no longer pins one shard at its whole weight."""
    (tmp_path / "abicheck").mkdir()
    body = "\n".join("    x = 1" for _ in range(50))
    (tmp_path / "abicheck/big.py").write_text(
        "\n".join(f"def f{i}():\n{body}\n" for i in range(8))
    )
    (tmp_path / "abicheck/small.py").write_text("def g():\n    return 1\n")
    only = ["abicheck/big.py", "abicheck/small.py"]
    shards = [scope.shard_assignment(only, k, 4, tmp_path) for k in range(1, 5)]
    assert all(any(m == "abicheck/big.py" for m, _ in s) for s in shards)
    weights = [sum(51 for m, _ in s if m == "abicheck/big.py") for s in shards]
    assert max(weights) - min(weights) <= 51


def test_mutable_functions_follows_mutmuts_trampoline_rules() -> None:
    src = (
        "def top():\n    def nested():\n        pass\n    return 1\n"
        "async def atop():\n    return 1\n"
        "class C:\n    def m(self):\n        return 1\n"
        "    class Inner:\n        def hidden(self):\n            return 1\n"
        "    @property\n    def p(self):\n        return 1\n"
        "    @p.setter\n    def p(self, v):\n        pass\n"
    )
    units = scope.mutable_functions(src)
    assert set(units) == {"top", "atop", "C.m", "C.p"}
    assert units["C.p"] == 4  # both 2-line definitions of one name, summed
    assert scope.mutable_functions("def broken(:\n") == {}


def test_unassigned_records_names_mutants_no_shard_covers() -> None:
    units = [("a.py", "f"), ("a.py", "C.m")]
    assert scope.unassigned_records([("a.py", "f"), ("a.py", "C.m")], units) == []
    assert scope.unassigned_records(
        [("a.py", "f"), ("a.py", "C.Inner.x"), ("b.py", "g")], units
    ) == [("a.py", "C.Inner.x"), ("b.py", "g")]


@pytest.mark.parametrize(
    ("records", "failures"),
    [
        ([("a.py", "f", True)], []),  # at baseline (1)
        ([("a.py", "f", True), ("a.py", "f", True)], ["  a.py: 1 -> 2 (+1)"]),
        # a new survivor in a function with no recorded survivors still adds
        # to the module total this shard owns: 1 recorded, 2 now
        ([("a.py", "f", True), ("a.py", "g", True)], ["  a.py: 1 -> 2 (+1)"]),
        # a survivor in another shard's function is not this shard's to score
        ([("a.py", "other", True), ("a.py", "other", True)], []),
    ],
)
def test_shard_drift_compares_only_this_shards_functions(records, failures) -> None:
    baseline = {("a.py", "f"): 1, ("a.py", "other"): 0}
    units = [("a.py", "f"), ("a.py", "g")]
    assert scope.check_shard_drift(records, baseline, units) == failures


@pytest.mark.parametrize("spec", ["0/4", "5/4", "1", "a/b", "1/0", ""])
def test_parse_shard_rejects_malformed_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        scope.parse_shard(spec)


def _part(units: list[str], survivors: dict[str, dict[str, int]]) -> dict[str, object]:
    return {
        "_comment": "c",
        "measured_units": units,
        "modules": {
            m: {"survivors": sum(f.values()), "keys": [], "functions": f}
            for m, f in survivors.items()
        },
    }


_UNITS = [("a.py", "f"), ("a.py", "g"), ("b.py", "h")]


def test_merge_sums_a_module_split_across_parts() -> None:
    doc = scope.merge_baseline_parts(
        [
            _part(["a.py::f"], {"a.py": {"f": 2}}),
            _part(["a.py::g", "b.py::h"], {"a.py": {"g": 1}, "b.py": {"h": 3}}),
        ],
        _UNITS,
    )
    assert doc["total_survivors"] == 6
    assert doc["modules"]["a.py"]["survivors"] == 3
    assert doc["modules"]["a.py"]["functions"] == {"f": 2, "g": 1}


@pytest.mark.parametrize(
    "parts",
    [
        [_part(["a.py::f", "a.py::g"], {})],  # missing shard
        [
            _part(["a.py::f", "a.py::g"], {}),
            _part(["a.py::g", "b.py::h"], {}),
        ],  # overlap
        [_part(["a.py::f"], {"b.py": {"h": 1}}), _part(["a.py::g", "b.py::h"], {})],
        [{"modules": {}}, _part(["a.py::f", "a.py::g", "b.py::h"], {})],
    ],
)
def test_merge_refuses_anything_but_a_partition(parts) -> None:
    with pytest.raises(ValueError):
        scope.merge_baseline_parts(parts, _UNITS)


# --------------------------------------------------------------------------
# pyproject relevance
# --------------------------------------------------------------------------

_BASE = '[project]\ndependencies = ["a"]\n[tool.mutmut]\nonly_mutate = ["x.py"]\n[tool.pytest.ini_options]\nmarkers = []\n[tool.ruff]\nx = 1\n'


@pytest.mark.parametrize(
    ("new", "relevant"),
    [
        (_BASE, False),
        (_BASE.replace('["a"]', '["a", "b"]'), False),
        (_BASE.replace("x = 1", "x = 2"), False),
        (_BASE.replace('["x.py"]', '["y.py"]'), True),
        (_BASE.replace("markers = []", 'markers = ["slow"]'), True),
        ("not toml [", True),
    ],
)
def test_pyproject_relevance(new: str, relevant: bool) -> None:
    assert scope.pyproject_mutation_config_changed(_BASE, new) is relevant


def test_pyproject_absent_at_base_counts_as_changed() -> None:
    assert scope.pyproject_mutation_config_changed(None, _BASE) is True


# --------------------------------------------------------------------------
# Through the gate's main()
# --------------------------------------------------------------------------

_SOURCE = "def alpha():\n    return 1\n\n\ndef untouched():\n    return 2\n"
_DIFF = (
    "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
    "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
    "@@ -1,0 +2,1 @@\n+    return 1\n"
    "diff --git a/changelog.d/x.md b/changelog.d/x.md\n"
    "--- a/changelog.d/x.md\n+++ b/changelog.d/x.md\n"
    "@@ -0,0 +1,1 @@\n+fix\n"
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE)
    (tmp_path / "abicheck" / "diff_symbols.py").write_text(_SOURCE)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mutmut]\nonly_mutate = ["abicheck/diff_types.py", '
        '"abicheck/diff_symbols.py"]\n'
    )
    (tmp_path / "d.diff").write_text(_DIFF)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_base_reader", lambda ref: lambda path: None)
    monkeypatch.setattr(gate, "load_cicd_stats", lambda _d: {"total": 4, "survived": 1})
    return tmp_path


def _fake_mutmut(seen: list[list[str]], run_out=("4/4", 0)):
    def run(cmd: list[str]) -> tuple[str, int]:
        seen.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return run_out
        return (
            "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
            "    abicheck.diff_types.x_untouched__mutmut_1: not checked\n"
            "    abicheck.diff_symbols.x_alpha__mutmut_1: timeout\n",
            0,
        )

    return run


def test_function_scope_runs_only_changed_functions_despite_outside_paths(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A changelog fragment no longer forces a full run, an out-of-scope
    timeout no longer fails it, and the changed function's survivor still
    fails the gate."""
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen))
    receipt = repo / "r.json"
    rc = gate.main(
        [
            "--run", "--diff-scoped", "--scope-run-to-diff",
            "--scope-run-to-functions", "--diff-file", str(repo / "d.diff"),
            "--baseline-file", str(repo / "none.json"), "--json", str(receipt),
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert seen[0] == ["mutmut", "run", "abicheck.diff_types.x_alpha__mutmut_*"]
    assert rc == 1 and "x_alpha__mutmut_1" in out
    assert "did not resolve" not in out
    doc = json.loads(receipt.read_text())
    assert doc["run_scope"]["mode"] == "functions"


def test_function_scope_is_not_used_when_a_baseline_exists(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a baseline the out-of-scope population feeds the drift gate, so
    the conservative module-level rules apply (outside path -> full run)."""
    baseline = repo / "b.json"
    baseline.write_text(json.dumps({"modules": {"abicheck/diff_types.py": 5}}))
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen))
    gate.main(
        [
            "--run", "--diff-scoped", "--scope-run-to-diff",
            "--scope-run-to-functions", "--diff-file", str(repo / "d.diff"),
            "--baseline-file", str(baseline),
        ]
    )  # fmt: skip
    assert seen[0] == ["mutmut", "run"]


def test_no_mutant_in_scoped_functions_is_a_clean_answer(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        _fake_mutmut(seen, (f"AssertionError: {scope.NOTHING_MATCHES_MARKER}", 1)),
    )
    monkeypatch.setattr(
        gate,
        "check_diff_scoped",
        lambda records, touched, baseline=None: [],
    )
    rc = gate.main(
        [
            "--run", "--diff-scoped", "--scope-run-to-functions",
            "--diff-file", str(repo / "d.diff"),
            "--baseline-file", str(repo / "none.json"),
        ]
    )  # fmt: skip
    assert rc == 0
    assert ["mutmut", "results"] in seen


def test_an_unrelated_abort_still_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen, ("boom", 1)))
    rc = gate.main(
        [
            "--run", "--diff-scoped", "--scope-run-to-functions",
            "--diff-file", str(repo / "d.diff"),
            "--baseline-file", str(repo / "none.json"),
        ]
    )  # fmt: skip
    assert rc == 1
    assert ["mutmut", "results"] not in seen


def test_require_baseline_fails_before_running_mutmut(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen))
    rc = gate.main(
        ["--run", "--require-baseline", "--baseline-file", str(repo / "none.json")]
    )
    assert rc == 1
    assert seen == []


def test_full_run_is_sharded_and_later_shards_skip_a_scoped_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen))
    patterns = []
    for k in (1, 2):
        seen.clear()
        gate.main(["--run", "--shard", f"{k}/2", "--baseline-file", str(repo / "n")])
        patterns.append(seen[0][2:])
    assert sorted(itertools.chain(*patterns)) == [
        "abicheck.diff_symbols.x_alpha__mutmut_*",
        "abicheck.diff_symbols.x_untouched__mutmut_*",
        "abicheck.diff_types.x_alpha__mutmut_*",
        "abicheck.diff_types.x_untouched__mutmut_*",
    ]
    seen.clear()
    args = ["--run", "--diff-scoped", "--scope-run-to-functions", "--diff-file",
            str(repo / "d.diff"), "--baseline-file", str(repo / "n")]  # fmt: skip
    assert gate.main([*args, "--shard", "2/2"]) == 0
    assert seen == []


_PLAN_MODES = {
    "full": ["--run"],
    "require-baseline": ["--run", "--require-baseline"],
    "write-baseline": ["--run", "--write-baseline"],
    "diff-scoped": ["--run", "--diff-scoped", "--scope-run-to-diff",
                    "--scope-run-to-functions"],
    "diff-scoped-required": ["--run", "--diff-scoped", "--scope-run-to-diff",
                             "--scope-run-to-functions", "--require-baseline"],
}  # fmt: skip


@pytest.mark.parametrize("n", [1, 2, 3, 4])
@pytest.mark.parametrize("mode", sorted(_PLAN_MODES))
def test_planned_shards_are_exactly_the_shards_that_run_mutmut(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    n: int,
) -> None:
    """`--plan-shards N` is what the workflow starts runners from, so it must
    name every shard with work (a missing one silently drops modules from the
    measurement) and no shard without (a runner started only to skip). The
    oracle is independent of the planner: run each of the N shards for real
    (mutmut faked) and record which ones invoked it. A run every shard fails
    before reaching mutmut still needs one runner to report the failure."""
    args = [*_PLAN_MODES[mode], "--baseline-file", str(repo / "none.json")]
    if "--diff-scoped" in args:
        args += ["--diff-file", str(repo / "d.diff")]
    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut(seen))
    monkeypatch.setattr(gate, "load_cicd_stats", lambda _d: {"total": 4, "survived": 0})
    working, failed, patterns = [], [], []
    for k in range(1, n + 1):
        seen.clear()
        rc = gate.main([*args, "--shard", f"{k}/{n}"])
        runs = [c for c in seen if c[:2] == ["mutmut", "run"]]
        if runs:
            working.append(k)
            patterns.append(runs[0][2:])
        elif rc != 0:
            failed.append(k)
    if not working and failed:
        working = [1]
    # No mutant population is measured twice: patterns of the shards that ran
    # are disjoint (an unscoped `mutmut run` would overlap every other shard).
    if len(patterns) > 1:
        assert all(patterns), "an unscoped shard measures every other shard's mutants"
        flat = list(itertools.chain(*patterns))
        assert len(flat) == len(set(flat))
    capsys.readouterr()
    assert gate.main([*args, "--plan-shards", str(n)]) == 0
    planned = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert planned == working
    assert planned, "a plan with no shard would start no job and gate nothing"


def test_sharded_baseline_parts_merge_into_a_full_baseline(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut([]))
    monkeypatch.setattr(gate, "count_unresolved", lambda text: 0)
    monkeypatch.setattr(
        gate.MutantRecord, "is_unresolved", property(lambda self: False)
    )
    monkeypatch.setattr(gate, "load_cicd_stats", lambda _d: {"total": 4, "survived": 1})
    parts = []
    for k in (1, 2):
        part = repo / f"p{k}.json"
        assert gate.main(
            ["--run", "--write-baseline", "--shard", f"{k}/2",
             "--baseline-file", str(part)]
        ) == 0  # fmt: skip
        parts.append(json.loads(part.read_text()))
    only = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    doc = scope.merge_baseline_parts(parts, scope.mutation_units(only, repo))
    assert doc["modules"]["abicheck/diff_types.py"]["survivors"] == 1


# --------------------------------------------------------------------------
# Against a real mutmut (third-party boundary: run in the mutation lane)
# --------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("mutmut") is None, reason="mutmut not installed")
def test_function_patterns_scope_a_real_mutmut_run(tmp_path: Path) -> None:
    """The encoder's patterns select exactly the intended functions' mutants
    in real mutmut output, and a function with no mutants produces the exact
    abort marker the gate treats as an empty scope, after which the database
    is still readable as this run's own."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "alpha.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def scale(a):\n    return a * 2\n\n"
        "def noop():\n    pass\n\n"
        "class C:\n    def m(self, a):\n        return a - 1\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_all.py").write_text(
        "from pkg.alpha import add, scale, C, noop\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
        "def test_rest():\n    scale(3)\n    C().m(2)\n    noop()\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["pkg/alpha.py"]\n'
        'pytest_add_cli_args_test_selection = ["tests/"]\n'
        'pytest_add_cli_args = ["-q", "--tb=no"]\n'
        "use_git_change_detection = false\n"
    )
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}

    def mutmut(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mutmut", *args],
            cwd=tmp_path, capture_output=True, text=True, timeout=900, env=env,
        )  # fmt: skip

    patterns = [
        scope.function_scope_pattern("pkg/alpha.py", "scale"),
        scope.function_scope_pattern("pkg/alpha.py", "C.m"),
    ]
    run = mutmut("run", *patterns)
    assert run.returncode == 0, run.stdout + run.stderr
    records = results.parse_mutant_records(mutmut("results", "--all", "true").stdout)
    checked = {r.function for r in records if r.status != "not checked"}
    assert checked == {"scale", "C.m"}, records
    assert any(r.function == "add" and r.status == "not checked" for r in records)

    empty = mutmut("run", scope.function_scope_pattern("pkg/alpha.py", "noop"))
    assert empty.returncode != 0
    assert scope.NOTHING_MATCHES_MARKER in empty.stdout + empty.stderr
    assert mutmut("export-cicd-stats").returncode == 0
    stats = results.load_cicd_stats(tmp_path / "mutants")
    assert stats is not None and stats["total"] > 0


def _baseline(repo: Path, modules: dict[str, object]) -> Path:
    path = repo / "baseline.json"
    path.write_text(json.dumps({"modules": modules}))
    return path


@pytest.mark.parametrize(
    ("functions", "expected_rc"),
    [
        ({"alpha": 1}, 0),  # the one survivor is recorded
        ({"alpha": 0}, 1),  # it is new: drift
        # recorded against another function of the same module in this shard:
        # the module total is unchanged, the same answer the unsharded
        # per-module gate gives
        ({"untouched": 1}, 0),
    ],
)
def test_a_function_shard_gates_drift_per_function(
    repo: Path, monkeypatch: pytest.MonkeyPatch, functions, expected_rc
) -> None:
    """End to end through main(): the fake listing has one survivor in
    diff_types.alpha; the oracle is the hand-written baseline, not the
    gate's own counting."""

    def run(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return ("2/2", 0)
        return (
            "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
            "    abicheck.diff_types.x_untouched__mutmut_1: killed\n",
            0,
        )

    monkeypatch.setattr(gate, "_run_mutmut", run)
    baseline = _baseline(
        repo,
        {
            "abicheck/diff_types.py": {
                "survivors": sum(functions.values()),
                "functions": functions,
            }
        },
    )
    rc = gate.main(["--run", "--shard", "1/1", "--baseline-file", str(baseline)])
    assert rc == expected_rc


def test_a_function_shard_refuses_a_baseline_without_function_counts(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gate, "_run_mutmut", _fake_mutmut([]))
    baseline = _baseline(repo, {"abicheck/diff_types.py": {"survivors": 5}})
    assert gate.main(["--run", "--shard", "1/1", "--baseline-file", str(baseline)]) == 1
    assert "no per-function counts" in capsys.readouterr().out


def test_a_mutant_no_shard_covers_fails_the_shard(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If mutmut ever mutates a function `mutable_functions` does not list,
    no shard would measure it; the run must say so instead of merging a
    baseline that silently omits it."""

    def run(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return ("4/4", 0)
        return ("    abicheck.diff_types.xǁGhostǁm__mutmut_1: killed\n", 0)

    monkeypatch.setattr(gate, "_run_mutmut", run)
    monkeypatch.setattr(gate, "load_cicd_stats", lambda _d: {"total": 1, "survived": 0})
    assert (
        gate.main(["--run", "--shard", "1/2", "--baseline-file", str(repo / "n")]) == 1
    )
    assert "abicheck/diff_types.py::Ghost.m" in capsys.readouterr().out
