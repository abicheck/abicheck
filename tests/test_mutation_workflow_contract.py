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

"""The mutation lane's trigger must match what it actually mutates.

`.github/workflows/mutation.yml` filters `pull_request` on a path list, and
`[tool.mutmut].source_paths` decides what gets mutated. Two hand-maintained
copies of the same list is precisely the drift shape AGENTS.md warns about — a
module added to `source_paths` but not to the workflow is mutated only on the
weekly run, so a PR can weaken its tests and see a green board.

Same enforcement pattern as `tests/test_verify_profiles.py`, which pins that
pixi/pre-commit/CI all route through `scripts/verify.py`'s step catalog.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
import tomllib

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mutation.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_block(wf: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True (YAML 1.1).
    return wf.get("on", wf.get(True))


def _mutmut_config() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["mutmut"]


def _source_paths() -> list[str]:
    """What mutmut **copies** into `mutants/` — the whole importable package.

    Not the mutation scope; see `_only_mutate()`. The two were the same list
    once, and that is exactly what broke the lane: mutmut runs the suite from
    inside `mutants/`, so a copy holding 14 of 272 modules and no
    `__init__.py` shadowed the real package as a namespace package and every
    run died at "failed to collect stats".
    """
    return _mutmut_config()["source_paths"]


def _only_mutate() -> list[str]:
    """The modules actually mutated — the detector core."""
    return _mutmut_config()["only_mutate"]


def _filters() -> dict[str, list[str]]:
    """Every dorny/paths-filter list in the `resolve` job."""
    steps = _workflow()["jobs"]["resolve"]["steps"]
    step = next(s for s in steps if "paths-filter" in str(s.get("uses", "")))
    return yaml.safe_load(step["with"]["filters"])


def _paths_filter() -> list[str]:
    return _filters()["mutated"]


def _test_filter() -> list[str]:
    return _filters()["mutated_tests"]


#: The lane's own infrastructure. A change to any of these must start the
#: lane, because the real-mutmut test is `slow` and runs in no other selector.
_INFRASTRUCTURE_PATHS = {
    "scripts/mutation_results.py",
    "scripts/check_mutation_score.py",
    "tests/test_mutation_results.py",
    ".github/workflows/mutation.yml",
    "pyproject.toml",
}


def _test_globs() -> set[str]:
    """One `tests/test_<stem>*.py` glob per mutated module.

    A PR that only weakens a detector test changes no production file, so the
    source-path entries alone never start the lane and the resulting survivors
    wait for the weekly run (Codex review). Derived from `source_paths` rather
    than listed, so a module added there without its tests fails the contract
    below instead of silently losing its trigger.
    """
    return {f"tests/test_{PurePosixPath(p).stem}*.py" for p in _only_mutate()}


def test_paths_filter_covers_every_mutated_modules_tests() -> None:
    missing = _test_globs() - set(_test_filter())
    assert not missing, (
        f"mutated module(s) whose tests have no trigger: {sorted(missing)} — a "
        "PR weakening only those tests would not start this lane."
    )


def test_paths_filter_covers_every_mutated_source_path() -> None:
    missing = set(_only_mutate()) - set(_paths_filter())
    assert not missing, (
        f"mutated module(s) {sorted(missing)} have no trigger — they would be "
        "checked only on the weekly run."
    )


def test_paths_filter_covers_the_lanes_own_infrastructure() -> None:
    """Otherwise a change to the parser, its config or this workflow does not
    start the lane, and the real-mutmut test runs nowhere at all."""
    missing = _INFRASTRUCTURE_PATHS - set(_paths_filter())
    assert not missing, f"untriggered mutation-lane infrastructure: {sorted(missing)}"


def test_the_filter_is_exactly_sources_plus_infrastructure() -> None:
    """No stray entries — the filter is a contract, not a wishlist."""
    assert set(_paths_filter()) == set(_only_mutate()) | _INFRASTRUCTURE_PATHS
    assert set(_test_filter()) == _test_globs()


def test_the_lane_does_not_cache_mutmut_results() -> None:
    """Removed deliberately, and pinned so it cannot return unnoticed.

    mutmut skips a mutant whose *source function hash* is unchanged, so reuse
    is sound only if nothing outside those functions changed. Three review
    rounds found ways that breaks — a test-only commit, a module-level
    constant, a deletion-only hunk — and the conservative rule covering the
    last one (any hunk containing a removal) matches essentially every edit,
    since a modification *is* a removal plus an addition. At that point the
    cache never hits and only carries risk, so the lane does a full run.
    Reinstating it needs a design that consults the removed side of the diff,
    not another new-side heuristic.
    """
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    caches = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache")]
    assert not caches, (
        "a mutmut cache was reintroduced — see this test's docstring for why "
        "new-side heuristics cannot make it safe"
    )


def test_the_trigger_has_no_workflow_level_paths_filter() -> None:
    """A workflow-level `paths:` is ANDed with `types:`, so labelling a PR that
    touches none of those files would not start the workflow — the documented
    `mutation` label override could never fire. The decision is made at job
    level instead, so path-match and label can be ORed."""
    pull_request = _on_block(_workflow())["pull_request"]
    assert "paths" not in pull_request
    assert "labeled" in pull_request["types"]


def test_the_run_decision_is_path_match_or_label() -> None:
    steps = _workflow()["jobs"]["resolve"]["steps"]
    decide = next(s for s in steps if s.get("id") == "decide")
    script = decide["run"]
    assert (
        '"$MATCHED" = "true" ] || [ "$MATCHED_TESTS" = "true" ] '
        '|| [ "$LABELLED" = "true"'
    ) in script, (
        "the run decision must OR the production path match, the detector-test "
        "match and the label"
    )
    # A non-PR event (schedule / dispatch) must always run.
    assert 'if [ "$GITHUB_EVENT_NAME" != "pull_request" ]' in script


def test_a_detector_test_change_makes_the_pr_lane_fail_closed() -> None:
    """A test-only diff has no changed production function to scope to, so
    --diff-scoped alone reports "gated nothing" and exits 0 — green for a run
    that checked nothing. That case, and only that case, requires a baseline
    (Codex review)."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    pr_step = next(
        s for s in steps if "--diff-scoped" in str(s.get("run", "")) and "run" in s
    )
    assert "--require-baseline" in pr_step["run"]
    assert pr_step["env"]["DETECTOR_TESTS"].strip().startswith("${{")
    assert "detector_tests" in pr_step["env"]["DETECTOR_TESTS"]


def test_the_detector_test_signal_is_published_by_resolve() -> None:
    """Otherwise the fail-closed branch above reads an always-empty variable
    and silently never fires."""
    outputs = _workflow()["jobs"]["resolve"]["outputs"]
    assert "detector_tests" in outputs
    steps = _workflow()["jobs"]["resolve"]["steps"]
    decide = next(s for s in steps if s.get("id") == "decide")
    assert 'echo "detector_tests=$MATCHED_TESTS"' in decide["run"]


def test_the_mutmut_job_is_gated_on_that_decision() -> None:
    job = _workflow()["jobs"]["mutmut"]
    assert job["needs"] == "resolve"
    assert "needs.resolve.outputs.run == 'true'" in job["if"]


def test_the_scheduled_lane_requires_a_baseline() -> None:
    """Otherwise a completed weekly run returns 0 however many mutants survive."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    scheduled = next(
        s for s in steps if "schedule" in str(s.get("if", "")) and "run" in s
    )
    assert "--require-baseline" in scheduled["run"]


def test_every_mutated_module_exists() -> None:
    missing = [p for p in _only_mutate() if not (REPO_ROOT / p).is_file()]
    assert not missing, f"[tool.mutmut].only_mutate names missing files: {missing}"


def test_source_paths_is_the_importable_package_not_a_file_list() -> None:
    """The regression that made this lane fail on its first real run.

    mutmut copies `source_paths` into `mutants/` and runs the suite from
    there. With individual files listed, `mutants/abicheck/` held 14 of 272
    modules and no `__init__.py`, so `import abicheck` resolved to that
    partial copy as a namespace package — `abicheck.__version__` and
    `abicheck.compat` were absent and mutmut aborted at "failed to collect
    stats" before generating a single result. The editable install does not
    save it: its finder is appended to `sys.meta_path`, so the path finder
    reaches `mutants/` first. Reproduced locally and in CI.
    """
    for path in _source_paths():
        assert (REPO_ROOT / path).is_dir(), (
            f"[tool.mutmut].source_paths entry {path!r} is not a directory — "
            "mutmut would copy an incomplete package into mutants/"
        )
        assert (REPO_ROOT / path / "__init__.py").is_file(), (
            f"{path} has no __init__.py, so the copy under mutants/ would "
            "shadow the real package as a namespace package"
        )


def test_the_repository_files_tests_read_are_copied_into_mutants() -> None:
    """mutmut runs the suite from inside `mutants/`, so a test that reads a
    repository file resolves it relative to that copy.

    Without these, collection died with 37 `FileNotFoundError`s — and any one
    of them aborts the lane before a single mutant is measured. Pinned by
    directory rather than by the individual files, since the failure mode of a
    missing entry is a dead lane, not a wrong number.
    """
    also_copy = set(_mutmut_config().get("also_copy", []))
    required = {".github", "docs", "examples", "scripts", "skills-src", "agent-evals"}
    missing = required - also_copy
    assert not missing, (
        f"tests read these repository paths but mutmut would not copy them: "
        f"{sorted(missing)}"
    )


def test_every_copied_path_that_exists_is_a_real_repository_path() -> None:
    """A typo in `also_copy` is silent — mutmut skips a path that does not
    exist — so the entry would simply never arrive under `mutants/`."""
    unknown = [
        p for p in _mutmut_config().get("also_copy", []) if not (REPO_ROOT / p).exists()
    ]
    assert not unknown, f"[tool.mutmut].also_copy names nonexistent paths: {unknown}"


def test_every_mutated_module_lives_under_a_copied_source_path() -> None:
    """`only_mutate` narrows what is mutated; it cannot reach outside what is
    copied, so a module named there but not under `source_paths` is mutated
    nowhere."""
    roots = tuple(f"{p.rstrip('/')}/" for p in _source_paths())
    outside = [p for p in _only_mutate() if not p.startswith(roots)]
    assert not outside, f"only_mutate names paths outside source_paths: {outside}"


def test_mutmut_config_uses_v3_key_names() -> None:
    """The v2 spellings do not merely warn — they break the run.

    `tests_dir` as a string makes mutmut 3.x abort with a TypeError before
    generating a mutant, and `runner` is not a recognised 3.x key at all, so
    the marker exclusion would be silently dropped and every mutant would
    re-run the integration and slow lanes. Both confirmed against 3.7.0.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        cfg = tomllib.load(fh)["tool"]["mutmut"]
    assert "source_paths" in cfg
    for dead_key in ("paths_to_mutate", "tests_dir", "runner"):
        assert dead_key not in cfg, (
            f"[tool.mutmut].{dead_key} is a mutmut 2.x key; 3.x either ignores "
            "it or aborts on it"
        )
    assert isinstance(cfg.get("pytest_add_cli_args_test_selection"), list)


def test_marker_exclusion_still_reaches_pytest() -> None:
    """The slow/integration lanes must stay out of every mutant's test run."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        args = tomllib.load(fh)["tool"]["mutmut"]["pytest_add_cli_args"]
    assert "-m" in args
    expr = args[args.index("-m") + 1]
    for marker in ("integration", "libabigail", "abicc", "slow"):
        assert f"not {marker}" in expr, f"marker {marker} no longer excluded"


def test_checkout_is_unshallow_for_the_diff_scoped_lane() -> None:
    """`--diff-scoped` needs the merge-base; a shallow clone reports every
    function as changed and the gate becomes noise."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    checkout = next(
        s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0


def test_the_real_mutmut_parser_test_runs_in_this_lane() -> None:
    """This is the only CI lane with mutmut installed, and every pytest
    selector in the repo excludes `slow` — so without an explicit step the one
    test that validates the parser against mutmut's *actual* output runs
    nowhere, and the parser falls back to being checked only against
    hand-written fixtures (Codex review)."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    step = next(
        (s for s in steps if "test_mutation_results.py" in str(s.get("run", ""))), None
    )
    assert step is not None, (
        "no step runs tests/test_mutation_results.py — the real-tool contract "
        "would never execute in CI"
    )
    assert "-m slow" in step["run"], "the real-run test carries the slow marker"
    assert step.get("env", {}).get("ABICHECK_MIN_EXECUTED") == "1", (
        "without conftest.py's silent-skip guard, a skipif that starts "
        "matching turns this step green with zero tests run"
    )


def test_that_step_precedes_the_mutation_run() -> None:
    """Cheap check first: no point spending the mutation budget when the
    parser that reads its output is already known to be wrong."""
    names = [s.get("name", "") for s in _workflow()["jobs"]["mutmut"]["steps"]]
    verify_idx = next(i for i, n in enumerate(names) if "parser against a real" in n)
    run_idx = next(i for i, n in enumerate(names) if "Run mutation testing" in n)
    assert verify_idx < run_idx


def test_mutmut_is_version_pinned() -> None:
    """An unpinned install is how this lane silently changed behaviour before."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "mutmut>=3.7,<4" in text
