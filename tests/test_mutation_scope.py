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


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 30])
def test_shards_partition_only_mutate(tmp_path: Path, n: int) -> None:
    rng = random.Random(n)
    modules = [f"abicheck/m{i}.py" for i in range(22)]
    (tmp_path / "abicheck").mkdir()
    for m in modules:
        (tmp_path / m).write_text("x" * rng.randint(1, 5000))
    shards = [scope.shard_modules(modules, k, n, tmp_path) for k in range(1, n + 1)]
    flat = list(itertools.chain.from_iterable(shards))
    assert sorted(flat) == sorted(modules)
    assert len(flat) == len(set(flat))
    # Deterministic: an order-shuffled input yields the same assignment.
    shuffled = modules[:]
    rng.shuffle(shuffled)
    assert shards == [
        scope.shard_modules(shuffled, k, n, tmp_path) for k in range(1, n + 1)
    ]


def test_shards_are_balanced_by_size(tmp_path: Path) -> None:
    (tmp_path / "abicheck").mkdir()
    sizes = {"abicheck/big.py": 1000, "abicheck/s1.py": 400, "abicheck/s2.py": 400}
    for m, size in sizes.items():
        (tmp_path / m).write_text("x" * size)
    assert scope.shard_modules(sizes, 1, 2, tmp_path) == ["abicheck/big.py"]
    assert scope.shard_modules(sizes, 2, 2, tmp_path) == [
        "abicheck/s1.py",
        "abicheck/s2.py",
    ]


@pytest.mark.parametrize("spec", ["0/4", "5/4", "1", "a/b", "1/0", ""])
def test_parse_shard_rejects_malformed_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        scope.parse_shard(spec)


def _part(measured: list[str], survivors: dict[str, int]) -> dict[str, object]:
    return {
        "_comment": "c",
        "measured_modules": measured,
        "modules": {m: {"survivors": n, "keys": []} for m, n in survivors.items()},
    }


def test_merge_combines_a_partition() -> None:
    doc = scope.merge_baseline_parts(
        [_part(["a.py"], {"a.py": 2}), _part(["b.py", "c.py"], {"c.py": 3})],
        ["a.py", "b.py", "c.py"],
    )
    assert doc["total_survivors"] == 5
    assert set(doc["modules"]) == {"a.py", "c.py"}


@pytest.mark.parametrize(
    "parts",
    [
        [_part(["a.py"], {})],  # missing shard
        [_part(["a.py", "b.py"], {}), _part(["b.py"], {})],  # overlap
        [_part(["a.py"], {"b.py": 1}), _part(["b.py"], {})],  # unmeasured entry
        [{"modules": {}}, _part(["b.py"], {})],  # no measured_modules
    ],
)
def test_merge_refuses_anything_but_a_partition(parts) -> None:
    with pytest.raises(ValueError):
        scope.merge_baseline_parts(parts, ["a.py", "b.py"])


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
        "abicheck.diff_symbols.*",
        "abicheck.diff_types.*",
    ]
    seen.clear()
    args = ["--run", "--diff-scoped", "--scope-run-to-functions", "--diff-file",
            str(repo / "d.diff"), "--baseline-file", str(repo / "n")]  # fmt: skip
    assert gate.main([*args, "--shard", "2/2"]) == 0
    assert seen == []


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
    doc = scope.merge_baseline_parts(parts, only)
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
