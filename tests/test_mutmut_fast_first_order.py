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
"""Fast-first ordering in `scripts/mutmut_stable_param_ids.py`: during a mutant
run, items are stably sorted by mutmut's recorded durations (unknown last);
the clean and sentinel runs keep pytest's order, and so does a run with no
readable stats file."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@dataclass
class FakeItem:
    nodeid: str


def _plugin():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import mutmut_stable_param_ids
    finally:
        sys.path.remove(str(REPO / "scripts"))
    return mutmut_stable_param_ids


def _items(*ids: str) -> list[FakeItem]:
    return [FakeItem(i) for i in ids]


def _write_stats(directory: Path, durations: dict[str, float]) -> None:
    (directory / "mutmut-stats.json").write_text(
        json.dumps({"duration_by_test": durations}), encoding="utf-8"
    )


@pytest.fixture
def mutants_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_mutant_run_orders_by_duration_unknowns_last_ties_stable(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(
        mutants_dir,
        {
            "t/a.py::slow": 5.0,
            "t/a.py::fast": 0.001,
            "t/a.py::tie1": 1.0,
            "t/a.py::tie2": 1.0,
        },
    )
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_3")
    items = _items(
        "t/a.py::unknown_b",
        "t/a.py::tie2",
        "t/a.py::slow",
        "t/a.py::unknown_a",
        "t/a.py::fast",
        "t/a.py::tie1",
    )
    _plugin().pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == [
        "t/a.py::fast",
        "t/a.py::tie2",
        "t/a.py::tie1",
        "t/a.py::slow",
        "t/a.py::unknown_b",
        "t/a.py::unknown_a",
    ]


@pytest.mark.parametrize(
    "sentinel", ["", "fail", "stats", "list_all_tests", "mutant_generation"]
)
def test_clean_and_sentinel_runs_keep_pytests_order(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch, sentinel: str
) -> None:
    _write_stats(mutants_dir, {"t/a.py::b": 9.0, "t/a.py::a": 0.1})
    monkeypatch.setenv("MUTANT_UNDER_TEST", sentinel)
    items = _items("t/a.py::b", "t/a.py::a")
    _plugin().pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == ["t/a.py::b", "t/a.py::a"]


def test_unset_mutant_variable_keeps_pytests_order(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(mutants_dir, {"t/a.py::b": 9.0, "t/a.py::a": 0.1})
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    items = _items("t/a.py::b", "t/a.py::a")
    _plugin().pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == ["t/a.py::b", "t/a.py::a"]


def test_missing_stats_file_keeps_order(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_1")
    items = _items("t/a.py::b", "t/a.py::a")
    _plugin().pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == ["t/a.py::b", "t/a.py::a"]


@pytest.mark.parametrize(
    "content", ["not json", "[1, 2]", '{"other": {}}', '{"duration_by_test": 3}']
)
def test_unreadable_stats_file_keeps_order(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    (mutants_dir / "mutmut-stats.json").write_text(content, encoding="utf-8")
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_1")
    items = _items("t/a.py::b", "t/a.py::a")
    _plugin().pytest_collection_modifyitems(None, items)
    assert [i.nodeid for i in items] == ["t/a.py::b", "t/a.py::a"]
