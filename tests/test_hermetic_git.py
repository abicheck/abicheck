# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""A test's git commits must not depend on the developer's signing setup.

Oracle: a real `git commit` under a global config that *requires* signing
through a program that does not exist. Without conftest's overrides git
refuses to write the commit; with them the commit succeeds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _commit(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, capture_output=True, text=True
        )

    run("init", "-q")
    (repo / "f").write_text("x", encoding="utf-8")
    run("add", "f")
    return run("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "m")


def _signing_global_config(tmp_path: Path) -> Path:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n"
        '[gpg]\n\tformat = ssh\n[gpg "ssh"]\n\tprogram = /nonexistent/signer\n'
        "[user]\n\tsigningkey = /nonexistent/key.pub\n",
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize("scope", ["commit", "tag"])
def test_signing_is_disabled_for_every_git_the_suite_starts(
    tmp_path: Path, scope: str
) -> None:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=str(_signing_global_config(tmp_path)))
    out = subprocess.run(
        ["git", "config", f"{scope}.gpgsign"], env=env, capture_output=True, text=True
    )
    assert out.stdout.strip() == "false"


def test_a_real_commit_succeeds_under_a_broken_global_signer(tmp_path: Path) -> None:
    config = _signing_global_config(tmp_path)
    repo = tmp_path / "r"
    repo.mkdir()
    hermetic = _commit(repo, dict(os.environ, GIT_CONFIG_GLOBAL=str(config)))
    assert hermetic.returncode == 0, hermetic.stderr

    # Control: the same commit without the overrides really does fail, so
    # the assertion above is not vacuous.
    bare = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    repo2 = tmp_path / "r2"
    repo2.mkdir()
    failed = _commit(repo2, dict(bare, GIT_CONFIG_GLOBAL=str(config)))
    assert failed.returncode != 0
