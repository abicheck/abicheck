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

"""The lint toolchain must be the same one at every entry point.

`ruff` reaches this repository two ways — CI and `pip install -e ".[dev]"` get
it from `pyproject.toml`'s dependency, contributors running `pre-commit` get it
from `.pre-commit-config.yaml`'s `rev:`. When those disagree, the same code
lints differently depending on which entry point ran.

That was the state before this file existed: `ruff>=0.3` against `rev: v0.9.0`.
The consequence was not theoretical — 0.9.0 reports an `F811` in
`abicheck/cli_buildsource_helpers.py` that current ruff does not, so a
contributor's pre-commit run and CI reached different verdicts on unmodified
code. Pinned forward rather than back for exactly that reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

_RUFF_REPO = "https://github.com/astral-sh/ruff-pre-commit"
#: `ruff==X.Y.Z` — an exact pin, not a floor.
_EXACT_PIN = re.compile(r"^ruff==(?P<version>\d+\.\d+\.\d+)$")


def _pyproject_ruff_requirement() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    dev = data["project"]["optional-dependencies"]["dev"]
    matches = [d for d in dev if d.replace(" ", "").startswith("ruff")]
    assert len(matches) == 1, f"expected exactly one ruff dependency, got {matches}"
    return matches[0].replace(" ", "")


def _pre_commit_ruff_rev() -> str:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    repos = [r for r in config["repos"] if r["repo"] == _RUFF_REPO]
    assert len(repos) == 1, f"expected exactly one ruff-pre-commit repo, got {repos}"
    return repos[0]["rev"]


def test_pyproject_pins_ruff_exactly() -> None:
    """A floor (`ruff>=0.3`) means the gate's rules depend on the day it ran."""
    requirement = _pyproject_ruff_requirement()
    assert _EXACT_PIN.match(requirement), (
        f"ruff must be pinned exactly, got {requirement!r} — an unpinned lint "
        "tool silently changes what CI enforces"
    )


def test_pre_commit_rev_is_a_version_tag() -> None:
    rev = _pre_commit_ruff_rev()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", rev), (
        f"ruff-pre-commit rev {rev!r} should be a vX.Y.Z tag"
    )


def test_the_two_ruff_pins_agree() -> None:
    """The invariant this file exists for."""
    pyproject_version = _EXACT_PIN.match(_pyproject_ruff_requirement()).group("version")
    pre_commit_version = _pre_commit_ruff_rev().lstrip("v")
    assert pyproject_version == pre_commit_version, (
        f"ruff is pinned to {pyproject_version} in pyproject.toml but "
        f"{pre_commit_version} in .pre-commit-config.yaml — the same code will "
        "lint differently in CI and in pre-commit. Update both together."
    )
