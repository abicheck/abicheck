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

from pathlib import Path

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


def _source_paths() -> list[str]:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["mutmut"]["source_paths"]


def _paths_filter() -> list[str]:
    """The dorny/paths-filter list in the `resolve` job."""
    steps = _workflow()["jobs"]["resolve"]["steps"]
    step = next(s for s in steps if "paths-filter" in str(s.get("uses", "")))
    return yaml.safe_load(step["with"]["filters"])["mutated"]


#: The lane's own infrastructure. A change to any of these must start the
#: lane, because the real-mutmut test is `slow` and runs in no other selector.
_INFRASTRUCTURE_PATHS = {
    "scripts/mutation_results.py",
    "scripts/check_mutation_score.py",
    "tests/test_mutation_results.py",
    ".github/workflows/mutation.yml",
    "pyproject.toml",
}


def test_paths_filter_covers_every_mutated_source_path() -> None:
    missing = set(_source_paths()) - set(_paths_filter())
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
    assert set(_paths_filter()) == set(_source_paths()) | _INFRASTRUCTURE_PATHS


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
    assert '"$MATCHED" = "true" ] || [ "$LABELLED" = "true"' in script, (
        "the run decision must OR the path match with the label"
    )
    # A non-PR event (schedule / dispatch) must always run.
    assert 'if [ "$GITHUB_EVENT_NAME" != "pull_request" ]' in script


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
    missing = [p for p in _source_paths() if not (REPO_ROOT / p).is_file()]
    assert not missing, f"[tool.mutmut].source_paths names missing files: {missing}"


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
