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

"""Tests for scripts/gen_cjm_stage_table.py.

The generated counts are checked against counts computed here directly from
the registry YAML (not through the generator's own helpers), the committed
page must be in sync, and an edited table is drift.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import _yaml_fast

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "gen_cjm_stage_table", _SCRIPTS / "gen_cjm_stage_table.py"
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_cjm_stage_table"] = gen
_spec.loader.exec_module(gen)

_CJM = _ROOT / "docs" / "contribute" / "cjm.md"
_REGISTRY = _ROOT / "docs" / "contribute" / "usecase-registry.yaml"
_STATUSES = ("complete", "partial", "modeled", "planned", "by_design_excluded")


def _independent_counts() -> dict[str, dict[str, int]]:
    data = _yaml_fast.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    counts: dict[str, dict[str, int]] = {}
    for case in data["use_cases"]:
        for stage in case["journey_stages"]:
            row = counts.setdefault(stage, dict.fromkeys(_STATUSES, 0))
            row[case["status"]] += 1
    return counts


def _table_rows(text: str) -> dict[str, list[int]]:
    rows: dict[str, list[int]] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"\| `([a-z_]+)` \|(.*)\|", line)
        if m:
            cells = [int(c.strip()) for c in m.group(2).split("|")]
            rows[m.group(1)] = cells
    return rows


def test_committed_table_is_in_sync() -> None:
    assert gen.main(["--check"]) == 0


def test_table_counts_match_registry() -> None:
    expected = _independent_counts()
    text = _CJM.read_text(encoding="utf-8")
    rows = _table_rows(text)
    assert set(rows) == set(expected)
    for stage, counts in expected.items():
        assert rows[stage] == [counts[s] for s in _STATUSES], stage


def test_rows_follow_heading_order() -> None:
    text = _CJM.read_text(encoding="utf-8")
    headings = re.findall(r"^### `([a-z_]+)`\s*$", text, re.MULTILINE)
    table = re.findall(r"^\| `([a-z_]+)` \|", text, re.MULTILINE)
    assert table == headings


def test_edited_table_is_drift(tmp_path: Path) -> None:
    cjm = tmp_path / "cjm.md"
    text = _CJM.read_text(encoding="utf-8")
    cjm.write_text(
        text.replace("| `trust` | 10 |", "| `trust` | 99 |", 1), encoding="utf-8"
    )
    assert gen.main(["--check", "--cjm", str(cjm)]) == 1


def test_missing_sentinels_are_a_hard_error(tmp_path: Path) -> None:
    cjm = tmp_path / "cjm.md"
    cjm.write_text(
        _CJM.read_text(encoding="utf-8").replace(
            "<!-- BEGIN GENERATED: cjm-stage-table -->", ""
        ),
        encoding="utf-8",
    )
    try:
        gen.main(["--check", "--cjm", str(cjm)])
    except ValueError as exc:
        assert "cjm-stage-table" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing sentinels")
