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

"""Direct tests for tests/_canonical_lane.py, split out of
test_ai_readiness.py (its only consumer today) rather than kept inline
there, to keep that already-large module under the file-size hard cap.

canonical_python() is evaluated at *module collection* time by any consumer
using it in a module-level pytestmark — an uncaught exception there breaks
collecting that whole module on every lane, not just the one skip decision
— so the defensive-degradation behavior below is the primitive's actual
contract, not an edge case.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from _canonical_lane import canonical_python, is_canonical_lane


@pytest.mark.parametrize(
    "content",
    [
        "[]",  # a JSON array — raw["canonical_python"] raises TypeError
        "42",  # a bare scalar — same
        '"a string"',  # same
        "null",
        "{}",  # a dict missing the key — KeyError
        '{"canonical_python": {}}',  # present but not str()-able into "N.N"
        "not json at all",  # json.JSONDecodeError
    ],
)
def test_canonical_python_degrades_on_malformed_repo_facts(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "repo_facts.json").write_text(content, encoding="utf-8")
    assert canonical_python(tmp_path) == (3, 13)


def test_canonical_python_degrades_when_the_file_is_absent(tmp_path: Path) -> None:
    assert canonical_python(tmp_path) == (3, 13)


def test_canonical_python_reads_a_well_formed_repo_facts(tmp_path: Path) -> None:
    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"canonical_python": "3.14"}), encoding="utf-8"
    )
    assert canonical_python(tmp_path) == (3, 14)


def test_canonical_python_reads_the_real_repo_facts_json() -> None:
    """Sanity: the real committed repo_facts.json's canonical_python value."""
    assert canonical_python() == (3, 13)


def test_is_canonical_lane_agrees_with_canonical_python(tmp_path: Path) -> None:
    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"canonical_python": "3.14"}), encoding="utf-8"
    )
    expected = sys.platform.startswith("linux") and sys.version_info[:2] == (3, 14)
    assert is_canonical_lane(tmp_path) == expected
