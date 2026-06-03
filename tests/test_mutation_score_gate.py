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

"""Unit tests for the mutation-score gate's parser and drift logic.

mutmut itself is slow and not installed in the default lane, so the gate's
*logic* is unit-tested here against representative output. This keeps the gate
trustworthy independent of whether mutmut is available.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_score.py"
_spec = importlib.util.spec_from_file_location("check_mutation_score", _GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "text, expected",
    [
        # mutmut emoji summary line (2.x / 3.x).
        ("⠋ 120/120  🎉 100  🫥 0  ⏰ 0  🤔 0  🙁 20  🔇 0", 20),
        ("🎉 100 🙁 0", 0),
        # plain-text "<n> survived" form.
        ("7 survived", 7),
        # per-mutant listing.
        ("abicheck.diff_symbols.x_1: survived\nabicheck.diff_types.y_2: survived", 2),
    ],
)
def test_parse_survivors_recognizes_formats(text: str, expected: int) -> None:
    assert gate.parse_survivors(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "no useful signal here", "Killed all"])
def test_parse_survivors_returns_none_when_unmeasurable(text: str) -> None:
    """'could not measure' must be distinguishable from 'zero survivors'."""
    assert gate.parse_survivors(text) is None


def test_gate_skips_when_unmeasurable(tmp_path: Path) -> None:
    """Empty / unparseable results are non-fatal (matches the mypy-skip pattern)."""
    results = tmp_path / "empty.txt"
    results.write_text("", encoding="utf-8")
    rc = gate.main(["--results-file", str(results), "--baseline", "5"])
    assert rc == 0


def test_gate_fails_when_survivors_exceed_baseline(tmp_path: Path) -> None:
    results = tmp_path / "results.txt"
    results.write_text("🙁 9", encoding="utf-8")
    rc = gate.main(["--results-file", str(results), "--baseline", "3"])
    assert rc == 1


def test_gate_reports_only_when_baseline_unset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results.txt"
    results.write_text("🙁 42", encoding="utf-8")
    # No --baseline and module default is None -> report-only, never fails.
    rc = gate.main(["--results-file", str(results)])
    assert rc == 0
    assert "42 surviving mutant" in capsys.readouterr().out


def test_gate_at_baseline_is_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results.txt"
    results.write_text("🙁 3", encoding="utf-8")
    rc = gate.main(["--results-file", str(results), "--baseline", "3"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


# --- --run strict mode: a run that produces no measurement must FAIL ----------


def test_run_mode_fails_when_mutmut_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """--run with mutmut absent must fail, not silently skip (no-op gate guard)."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: None)
    assert gate.main(["--run"]) == 1


def test_run_mode_fails_when_run_aborts_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    """--run where the run aborts (no parseable count) must fail — never an
    inferred zero."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "config error: nothing to mutate")
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_run_mode_fails_on_interrupted_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted run that printed only progress ("309/464") with no explicit
    survivor count must NOT be mistaken for a clean zero-survivor run."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "309/464  🎉 300")  # no 🙁 count
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_run_mode_counts_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    """--run with an explicit survivor count is gated normally."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "🙁 2")
    assert gate.main(["--run", "--baseline", "5"]) == 0   # within baseline
    assert gate.main(["--run", "--baseline", "1"]) == 1   # exceeds baseline


def test_run_mode_clean_run_zero_survivors_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean run prints an explicit '🙁 0' in its summary → parsed as 0 → passes
    baseline 0. Zero is detected, never inferred."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "12/12  🎉 12  🙁 0  ⏰ 0  🤔 0")
    assert gate.main(["--run", "--baseline", "0"]) == 0


def test_run_mode_fails_on_unresolved_mutants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero survivors but unresolved (timeout/suspicious) mutants is an
    incomplete measurement — it must not pass a zero baseline."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "🙁 0  ⏰ 2  🤔 1")
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_unresolved_does_not_fail_report_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """In report-only mode (no baseline) unresolved mutants are surfaced but the
    gate does not fail — it is only reporting."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run", lambda cmd: "🙁 0  ⏰ 2")
    assert gate.main(["--run"]) == 0  # SURVIVOR_BASELINE is None → report-only


@pytest.mark.parametrize(
    "text, expected",
    [
        ("🙁 0  ⏰ 2  🤔 1", 3),
        ("🙁 5", 0),
        ("⏰ 4", 4),
        ("🫥 2", 2),          # no-tests (uncovered) counts as unresolved
        ("🔇 2", 0),          # skipped is intentional → NOT unresolved
        ("🙁 0  🫥 5  🔇 3", 5),  # 5 uncovered count; 3 skipped do not
        ("no markers", 0),
    ],
)
def test_count_unresolved(text: str, expected: int) -> None:
    assert gate.count_unresolved(text) == expected


def test_no_run_unparseable_is_still_a_skip(tmp_path: Path) -> None:
    """Without --run, an unparseable/empty result stays a graceful skip."""
    results = tmp_path / "garbage.txt"
    results.write_text("nothing useful", encoding="utf-8")
    assert gate.main(["--results-file", str(results), "--baseline", "0"]) == 0
