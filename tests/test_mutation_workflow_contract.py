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


def test_paths_filter_matches_mutated_source_paths() -> None:
    assert sorted(_paths_filter()) == sorted(_source_paths()), (
        "mutation.yml's paths filter and [tool.mutmut].source_paths have "
        "drifted — a mutated module with no trigger is only checked weekly."
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


def test_the_cache_is_restored_only_on_the_pr_lane() -> None:
    """mutmut skips a mutant whose source function hash is unchanged, so a warm
    cache reuses verdicts computed against the *old* tests. That is sound for
    `--diff-scoped` (changed functions re-run) and unsound for the scheduled
    drift lane, where weakening only assertions would leave every source hash
    untouched and the stale "killed" outcomes would be reused."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    cache = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache"))
    # Asserted as a clause rather than by exact string: the condition gained a
    # second guard (module-scope changes also skip the cache), and pinning the
    # whole expression would make this test fail on any further, correct
    # tightening.
    assert "github.event_name == 'pull_request'" in cache.get("if", "")


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


def test_the_cache_is_skipped_for_a_module_scope_change() -> None:
    """The module-scope attribution added for the diff-scoped gate is defeated
    by a warm cache: mutmut skips a mutant whose *function* hash is unchanged,
    and a module-level constant belongs to no function's hash, so the gate
    would compare against verdicts computed before the constant changed
    (Codex review)."""
    steps = _workflow()["jobs"]["mutmut"]["steps"]
    scope = next(s for s in steps if s.get("id") == "scope")
    assert "--print-changed-scope" in scope["run"]

    cache = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache"))
    condition = cache["if"]
    assert "steps.scope.outputs.changed != 'module'" in condition
    assert "github.event_name == 'pull_request'" in condition

    names = [s.get("name", "") for s in steps]
    assert names.index("Decide whether the cache is safe to reuse") < names.index(
        "Restore mutmut cache"
    )
