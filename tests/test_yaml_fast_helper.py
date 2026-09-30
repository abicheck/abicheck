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

"""``tests/_yaml_fast.safe_load`` must be a drop-in for ``yaml.safe_load``.

The oracle is PyYAML's own pure-Python ``yaml.safe_load``, applied to every
committed YAML document the suite reads (workflows, actions, docs data) and
to a set of adversarial snippets: the same document or the same error class.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import _yaml_fast
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _committed_yaml() -> list[Path]:
    """Every committed ``*.yml``/``*.yaml`` (git's index, not a tree walk)."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "*.yml", "*.yaml"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in sorted(filter(None, listed.split("\0")))]


@pytest.mark.repo_scan
def test_every_committed_yaml_document_loads_identically() -> None:
    paths = _committed_yaml()
    assert len(paths) > 40  # vacuity guard: the sweep really covers the tree
    mismatched = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        try:
            expected = yaml.safe_load(text)
        except yaml.YAMLError:
            with pytest.raises(yaml.YAMLError):
                _yaml_fast.safe_load(text)
            continue
        if _yaml_fast.safe_load(text) != expected:
            mismatched.append(str(path.relative_to(ROOT)))
    assert not mismatched, mismatched


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2\n",  # duplicate key: last value wins in both
        "on: [push]\nyes: no\n",  # YAML 1.1 booleans
        "x: 0o17\ny: 017\nz: 1_000\n",  # octal / underscore ints
        "t: 2026-09-30\n",  # timestamp resolution
        "- &a {k: v}\n- *a\n",  # anchors and aliases
        "s: 'it''s'\nd: \"tab\\t\"\n",  # quoting and escapes
        "",  # empty document -> None
        "u: é中\n",  # non-ASCII
    ],
)
def test_snippets_load_identically(text: str) -> None:
    assert _yaml_fast.safe_load(text) == yaml.safe_load(text)


@pytest.mark.parametrize(
    "text", ["a: [1, 2\n", "a: b: c\n", "\t- x\n", "!!python/object:os.system x\n"]
)
def test_invalid_or_unsafe_input_raises_the_same_error_family(text: str) -> None:
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)
    with pytest.raises(yaml.YAMLError):
        _yaml_fast.safe_load(text)
