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

"""Primitive-level tests for `abicheck.config_paths.find_config_in_dir`."""

from __future__ import annotations

from abicheck.config_paths import CONFIG_RELATIVE_CANDIDATES, find_config_in_dir


def test_no_candidate_present_returns_none(tmp_path):
    assert find_config_in_dir(tmp_path) is None


def test_finds_root_level_config(tmp_path):
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("severity: {}\n", encoding="utf-8")
    assert find_config_in_dir(tmp_path) == cfg


def test_finds_dot_github_config(tmp_path):
    (tmp_path / ".github").mkdir()
    cfg = tmp_path / ".github" / ".abicheck.yml"
    cfg.write_text("severity: {}\n", encoding="utf-8")
    assert find_config_in_dir(tmp_path) == cfg


def test_finds_dot_github_abicheck_subdir_config(tmp_path):
    (tmp_path / ".github" / "abicheck").mkdir(parents=True)
    cfg = tmp_path / ".github" / "abicheck" / ".abicheck.yml"
    cfg.write_text("severity: {}\n", encoding="utf-8")
    assert find_config_in_dir(tmp_path) == cfg


def test_precedence_root_beats_dot_github_beats_subdir(tmp_path):
    """First candidate present wins, in `CONFIG_RELATIVE_CANDIDATES` order."""
    (tmp_path / ".github" / "abicheck").mkdir(parents=True)
    for rel in CONFIG_RELATIVE_CANDIDATES:
        (tmp_path / rel).write_text("severity: {}\n", encoding="utf-8")

    # All three exist; the root-level file must win.
    assert find_config_in_dir(tmp_path) == tmp_path / ".abicheck.yml"

    # Remove the root file: .github/.abicheck.yml must win over the subdir.
    (tmp_path / ".abicheck.yml").unlink()
    assert find_config_in_dir(tmp_path) == tmp_path / ".github" / ".abicheck.yml"

    # Remove that too: only the subdir copy remains.
    (tmp_path / ".github" / ".abicheck.yml").unlink()
    assert (
        find_config_in_dir(tmp_path)
        == tmp_path / ".github" / "abicheck" / ".abicheck.yml"
    )


def test_a_directory_named_like_the_config_is_not_a_match(tmp_path):
    """`find_config_in_dir` only ever returns a regular file."""
    (tmp_path / ".abicheck.yml").mkdir()
    assert find_config_in_dir(tmp_path) is None
