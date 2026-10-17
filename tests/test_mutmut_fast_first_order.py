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
    # Ordering alone: the cutoff (tested below) would drop the 1.0 s and 5.0 s items.
    monkeypatch.setenv("MUTMUT_SLOW_TEST_CUTOFF", "0")
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


class _Hook:
    def __init__(self) -> None:
        self.deselected: list[FakeItem] = []

    def pytest_deselected(self, items: list[FakeItem]) -> None:
        self.deselected.extend(items)


class FakeConfig:
    def __init__(self) -> None:
        self.hook = _Hook()


def _run_mutant(
    monkeypatch: pytest.MonkeyPatch, items: list[FakeItem]
) -> tuple[list[str], list[str]]:
    config = FakeConfig()
    _plugin().pytest_collection_modifyitems(config, items)
    return [i.nodeid for i in items], [i.nodeid for i in config.hook.deselected]


def test_cutoff_drops_slow_known_items_and_reports_them(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(
        mutants_dir,
        {"t/a.py::fast": 0.2, "t/a.py::edge": 1.0, "t/a.py::slow": 4.0},
    )
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_2")
    monkeypatch.delenv("MUTMUT_SLOW_TEST_CUTOFF", raising=False)
    items = _items("t/a.py::slow", "t/a.py::edge", "t/a.py::fast")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::fast"]
    assert dropped == ["t/a.py::edge", "t/a.py::slow"]


def test_unknown_duration_is_never_deselected(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(mutants_dir, {"t/a.py::slow": 9.0, "t/a.py::fast": 0.1})
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_2")
    items = _items("t/a.py::new_unknown", "t/a.py::slow", "t/a.py::fast")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::fast", "t/a.py::new_unknown"]
    assert dropped == ["t/a.py::slow"]


def test_all_slow_falls_back_to_the_full_sorted_set(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(mutants_dir, {"t/a.py::b": 3.0, "t/a.py::a": 2.0})
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_2")
    items = _items("t/a.py::b", "t/a.py::a")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::a", "t/a.py::b"]
    assert dropped == []


def test_unknown_survives_when_the_only_recorded_test_is_slow(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_stats(mutants_dir, {"t/a.py::slow": 3.0})
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_2")
    items = _items("t/a.py::slow", "t/a.py::unknown")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::unknown"]
    assert dropped == ["t/a.py::slow"]


@pytest.mark.parametrize(
    ("value", "kept", "dropped"),
    [
        ("0.5", ["t/a.py::fast"], ["t/a.py::mid", "t/a.py::slow"]),
        ("2", ["t/a.py::fast", "t/a.py::mid"], ["t/a.py::slow"]),
        ("0", ["t/a.py::fast", "t/a.py::mid", "t/a.py::slow"], []),
        ("-1", ["t/a.py::fast", "t/a.py::mid", "t/a.py::slow"], []),
        ("  ", ["t/a.py::fast", "t/a.py::mid"], ["t/a.py::slow"]),
        ("not-a-number", ["t/a.py::fast", "t/a.py::mid"], ["t/a.py::slow"]),
    ],
)
def test_env_override_sets_or_disables_the_cutoff(
    mutants_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    kept: list[str],
    dropped: list[str],
) -> None:
    _write_stats(
        mutants_dir,
        {"t/a.py::fast": 0.1, "t/a.py::mid": 0.7, "t/a.py::slow": 3.0},
    )
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_2")
    monkeypatch.setenv("MUTMUT_SLOW_TEST_CUTOFF", value)
    items = _items("t/a.py::slow", "t/a.py::mid", "t/a.py::fast")
    assert _run_mutant(monkeypatch, items) == (kept, dropped)


def test_default_cutoff_constant_is_one_second() -> None:
    plugin = _plugin()
    assert plugin.SLOW_TEST_CUTOFF_SECONDS == 1.0


@pytest.mark.parametrize(
    "sentinel", ["", "fail", "stats", "list_all_tests", "mutant_generation"]
)
def test_sentinel_runs_are_not_cut(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch, sentinel: str
) -> None:
    _write_stats(mutants_dir, {"t/a.py::b": 9.0, "t/a.py::a": 0.1})
    monkeypatch.setenv("MUTANT_UNDER_TEST", sentinel)
    items = _items("t/a.py::b", "t/a.py::a")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::b", "t/a.py::a"]
    assert dropped == []


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]"])
def test_missing_or_unreadable_stats_are_not_cut(
    mutants_dir: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    if content is not None:
        (mutants_dir / "mutmut-stats.json").write_text(content, encoding="utf-8")
    monkeypatch.setenv("MUTANT_UNDER_TEST", "pkg.mod.func__mutmut_1")
    items = _items("t/a.py::b", "t/a.py::a")
    kept, dropped = _run_mutant(monkeypatch, items)
    assert kept == ["t/a.py::b", "t/a.py::a"]
    assert dropped == []
