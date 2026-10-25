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

"""Retired release-compare module names must not reappear in live code.

``cli_compare_release_helpers`` and ``cli_compare_release_matrix`` were
deleted; their owners are named in the modules that replaced them. A
reference left behind in a comment or docstring points a reader at code that
no longer exists, so this scan fails on any mention outside the changelog,
which is history by design.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RETIRED = (
    "cli_compare_release_helpers",
    "cli_compare_release_matrix",
    "cli_compare_release_pairwise",
)
_SCANNED = ("abicheck", "action", "scripts", "tests", "architecture")
_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md"}
# A mention is history only when it is labelled ``retired`` right before the
# name, optionally inside a code span; any other mention is a live reference.
_LIVE_RE = {
    name: re.compile(rf"(?<!retired )(?<!retired `)(?<!retired ``){re.escape(name)}")
    for name in _RETIRED
}


def _live_files() -> list[Path]:
    found: list[Path] = []
    for top in _SCANNED:
        for path in (_ROOT / top).rglob("*"):
            if (
                path.is_file()
                and path.suffix in _SUFFIXES
                and "__pycache__" not in path.parts
                and "changelog.d" not in path.parts
                # This file names the retired modules to forbid them.
                and path.name != Path(__file__).name
                # Generated test-selection data keyed by test-file names.
                and path.name != "shard_weights.json"
            ):
                found.append(path)
    return found


@pytest.mark.parametrize("name", _RETIRED)
def test_no_live_file_names_a_retired_module(name: str) -> None:
    offenders = [
        p.relative_to(_ROOT).as_posix()
        for p in _live_files()
        if _LIVE_RE[name].search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert offenders == [], f"{name} is retired; name its current owner instead"
