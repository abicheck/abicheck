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


def test_pull_request_paths_match_mutated_source_paths() -> None:
    triggers = _on_block(_workflow())["pull_request"]["paths"]
    assert sorted(triggers) == sorted(_source_paths()), (
        "mutation.yml's pull_request paths and [tool.mutmut].source_paths have "
        "drifted — a mutated module with no trigger is only checked weekly."
    )


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


def test_mutmut_is_version_pinned() -> None:
    """An unpinned install is how this lane silently changed behaviour before."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "mutmut>=3.7,<4" in text
