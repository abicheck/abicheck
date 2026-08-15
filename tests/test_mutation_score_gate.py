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

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_score.py"
)
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
        # Per-mutant listing, in mutmut 3.7.0's *real* key format. The
        # previous fixture here ("x_1: survived") had no `__mutmut_<n>`
        # suffix, which mutmut never emits — so it pinned a format belief
        # rather than the format, and passed against a parser that could not
        # read a real run. Captured from an actual `mutmut results`.
        (
            "    abicheck.diff_symbols.x_diff__mutmut_1: survived\n"
            "    abicheck.diff_types.xǁTypeMapǁget__mutmut_2: survived",
            2,
        ),
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


def test_gate_reports_only_when_baseline_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = tmp_path / "results.txt"
    results.write_text("🙁 42", encoding="utf-8")
    # No --baseline and module default is None -> report-only, never fails.
    rc = gate.main(["--results-file", str(results)])
    assert rc == 0
    assert "42 surviving mutant" in capsys.readouterr().out


def test_gate_at_baseline_is_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_run_mode_fails_when_run_aborts_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--run where the run aborts (no parseable count) must fail — never an
    inferred zero."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate, "_run_mutmut", lambda cmd: ("config error: nothing to mutate", 0)
    )
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_run_mode_fails_on_interrupted_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted run that printed only progress ("309/464") with no explicit
    survivor count must NOT be mistaken for a clean zero-survivor run."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate, "_run_mutmut", lambda cmd: ("309/464  🎉 300", 0)
    )  # no 🙁 count
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_run_mode_counts_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    """--run with an explicit survivor count is gated normally."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: ("🙁 2", 0))
    assert gate.main(["--run", "--baseline", "5"]) == 0  # within baseline
    assert gate.main(["--run", "--baseline", "1"]) == 1  # exceeds baseline


def test_run_mode_clean_run_zero_survivors_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean run prints an explicit '🙁 0' in its summary → parsed as 0 → passes
    baseline 0. Zero is detected, never inferred."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate, "_run_mutmut", lambda cmd: ("12/12  🎉 12  🙁 0  ⏰ 0  🤔 0", 0)
    )
    assert gate.main(["--run", "--baseline", "0"]) == 0


def test_run_mode_fails_on_unresolved_mutants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero survivors but unresolved (timeout/suspicious) mutants is an
    incomplete measurement — it must not pass a zero baseline."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: ("🙁 0  ⏰ 2  🤔 1", 0))
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_unresolved_does_not_fail_report_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """In report-only mode (no baseline) unresolved mutants are surfaced but the
    gate does not fail — it is only reporting."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: ("🙁 0  ⏰ 2", 0))
    assert gate.main(["--run"]) == 0  # SURVIVOR_BASELINE is None → report-only


@pytest.mark.parametrize(
    "text, expected",
    [
        ("🙁 0  ⏰ 2  🤔 1", 3),
        ("🙁 5", 0),
        ("⏰ 4", 4),
        ("🫥 2", 2),  # no-tests (uncovered) counts as unresolved
        ("🔇 2", 0),  # skipped is intentional → NOT unresolved
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


# --- Per-module baseline -----------------------------------------------------
#
# The gate this repository had could only compare one whole-repository number.
# That is exactly the shape that lets a real regression hide: one module gets
# worse while another improves, the total stays flat, and the gate says OK.

_TWO_MODULES = (
    "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
    "    abicheck.diff_types.x_alpha__mutmut_2: survived\n"
    "    abicheck.diff_symbols.x_beta__mutmut_1: survived\n"
)


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _baseline(tmp_path: Path, modules: dict[str, int]) -> str:
    import json

    return _write(
        tmp_path,
        "mutation-baseline.json",
        json.dumps(
            {"modules": {m: {"survivors": n, "keys": []} for m, n in modules.items()}}
        ),
    )


def test_per_module_regression_fails_even_when_the_total_falls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The invariant a global-total gate cannot express.

    Baseline: diff_types 1, diff_symbols 5 (total 6). Now: diff_types 2,
    diff_symbols 1 (total 3). The repository total *improved* by half, and a
    total-only gate passes — but diff_types genuinely regressed, which is the
    signal that matters.
    """
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    baseline = _baseline(
        tmp_path, {"abicheck/diff_types.py": 1, "abicheck/diff_symbols.py": 5}
    )
    rc = gate.main(
        ["--results-file", results, "--baseline-file", baseline, "--baseline", "99"]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "abicheck/diff_types.py: 1 -> 2" in out
    # ...and the total-only gate on the same run is perfectly happy.
    assert "exceed baseline" not in out


def test_per_module_at_baseline_passes(tmp_path: Path) -> None:
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    baseline = _baseline(
        tmp_path, {"abicheck/diff_types.py": 2, "abicheck/diff_symbols.py": 1}
    )
    assert gate.main(["--results-file", results, "--baseline-file", baseline]) == 0


def test_a_module_absent_from_the_baseline_is_treated_as_zero(tmp_path: Path) -> None:
    """A newly-mutated module with survivors must fail, not pass by omission."""
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    baseline = _baseline(tmp_path, {"abicheck/diff_types.py": 2})
    assert gate.main(["--results-file", results, "--baseline-file", baseline]) == 1


def test_write_baseline_records_per_module_counts(tmp_path: Path) -> None:
    import json

    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    out_file = str(tmp_path / "baseline.json")
    assert (
        gate.main(
            ["--results-file", results, "--baseline-file", out_file, "--write-baseline"]
        )
        == 0
    )
    doc = json.loads(Path(out_file).read_text(encoding="utf-8"))
    assert doc["total_survivors"] == 3
    assert doc["modules"]["abicheck/diff_types.py"]["survivors"] == 2
    assert doc["modules"]["abicheck/diff_symbols.py"]["keys"] == [
        "abicheck.diff_symbols.x_beta__mutmut_1"
    ]


def test_write_baseline_refuses_an_unresolved_run(tmp_path: Path) -> None:
    """Recording a baseline from an incomplete run bakes in a fiction."""
    results = _write(
        tmp_path,
        "r.txt",
        _TWO_MODULES + "    abicheck.diff_types.x_alpha__mutmut_3: timeout\n",
    )
    out_file = str(tmp_path / "baseline.json")
    rc = gate.main(
        ["--results-file", results, "--baseline-file", out_file, "--write-baseline"]
    )
    assert rc == 1
    assert not Path(out_file).exists()


# --- Diff-scoped gate --------------------------------------------------------

_DIFF = """\
diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py
--- a/abicheck/diff_types.py
+++ b/abicheck/diff_types.py
@@ -1,0 +2,1 @@
+    pass
"""

_SOURCE = """\
def alpha():
    return 1


def untouched():
    return 2
"""


def _diff_scoped_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Set up a diff-scoped run whose *only* possible failure is diff-scoped.

    The per-module baseline is deliberately set high enough to be satisfied, so
    each of these tests isolates one axis instead of passing/failing for two
    reasons at once.
    """
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    return (
        _write(tmp_path, "d.diff", _DIFF),
        _baseline(tmp_path, {"abicheck/diff_types.py": 10}),
    )


def test_diff_scoped_fails_on_a_survivor_in_a_changed_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No baseline is consulted: editing a function whose mutants still pass
    means the edit is executed but unverified."""
    diff, baseline = _diff_scoped_env(tmp_path, monkeypatch)
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
    )
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            baseline,
            "--diff-scoped",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 1
    assert "abicheck/diff_types.py::alpha" in capsys.readouterr().out


def test_diff_scoped_ignores_survivors_in_untouched_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing debt elsewhere must not block an unrelated PR."""
    diff, baseline = _diff_scoped_env(tmp_path, monkeypatch)
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_untouched__mutmut_1: survived\n"
    )
    assert (
        gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                baseline,
                "--diff-scoped",
                "--diff-file",
                diff,
            ]
        )
        == 0
    )


@pytest.mark.parametrize(
    "hunk, expected",
    [
        ("@@ -1,0 +2,1 @@", {2}),
        ("@@ -5,2 +5,3 @@", {5, 6, 7}),
        ("@@ -1 +1 @@", {1}),  # no count => 1 line
        # A pure deletion contributes no new-side line, but deleting a guard
        # is a real way to weaken a detector, so it is attributed to the
        # surrounding function rather than skipped. This fixture previously
        # asserted `set()`, which pinned the hole rather than the behaviour.
        ("@@ -3,1 +3,0 @@", {3, 4}),
    ],
)
def test_parse_changed_lines_hunk_shapes(hunk: str, expected: set[int]) -> None:
    diff = f"--- a/f.py\n+++ b/f.py\n{hunk}\n+x\n"
    assert gate.parse_changed_lines(diff).get("f.py", set()) == expected


def test_parse_changed_lines_ignores_deleted_files() -> None:
    assert (
        gate.parse_changed_lines("--- a/f.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n") == {}
    )


# --- Codex review: the gate must not pass on a run that did not happen --------


def test_an_aborted_mutmut_run_fails_even_with_readable_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stale-cache hole introduced by caching `mutants/` in CI.

    `mutmut run` exits 0 even when mutants survive (verified against 3.7.0), so
    a nonzero exit means the run aborted — and with a restored cache the
    results left on disk are the *previous* commit's, complete-looking and
    wrong. The gate must refuse to read them.
    """
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")

    def fake(cmd):
        if cmd[1] == "run":
            return ("config error: nothing to mutate", 1)
        return (_TWO_MODULES, 0)  # a perfectly readable stale database

    monkeypatch.setattr(gate, "_run_mutmut", fake)
    assert gate.main(["--run", "--baseline", "99"]) == 1
    assert "the run aborted" in capsys.readouterr().out


def test_a_successful_run_with_survivors_is_still_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for the check above: exit 0 with survivors is normal,
    and must not be mistaken for an abort."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: (_TWO_MODULES, 0))
    assert gate.main(["--run", "--baseline", "99"]) == 0
    assert gate.main(["--run", "--baseline", "1"]) == 1


# --- Codex review: a drift lane with no baseline is not a drift lane ----------


def test_a_failing_results_command_is_not_read_as_zero_survivors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`mutmut run` can succeed while `mutmut results` fails.

    Its stderr parses as "no per-mutant lines", and if exported stats happen to
    exist with total > 0 the completeness check passes and the unparseable
    output becomes zero survivors — passing even an explicit zero baseline
    while the stats report survivors (Codex review).
    """
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")

    def fake(cmd):
        if cmd[1] == "results":
            return ("Traceback: cache is corrupt", 1)
        return ("", 0)

    monkeypatch.setattr(gate, "_run_mutmut", fake)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda d: {"total": 40, "survived": 12}
    )
    assert gate.main(["--run", "--baseline", "0"]) == 1
    assert "cannot read the per-mutant statuses" in capsys.readouterr().out


def test_require_baseline_fails_when_no_baseline_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scheduled lane advertises baseline-drift gating. With no baseline
    file and SURVIVOR_BASELINE unset it would return 0 regardless of how many
    mutants survived — a can't-fail gate, which is what this whole script
    exists to remove."""
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            str(tmp_path / "absent.json"),
            "--require-baseline",
        ]
    )
    assert rc == 1
    assert "could only ever report, never gate" in capsys.readouterr().out


def test_require_baseline_passes_once_a_baseline_exists(tmp_path: Path) -> None:
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    baseline = _baseline(
        tmp_path, {"abicheck/diff_types.py": 2, "abicheck/diff_symbols.py": 1}
    )
    assert (
        gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                baseline,
                "--require-baseline",
            ]
        )
        == 0
    )


def test_require_baseline_is_satisfied_by_the_global_baseline_too(
    tmp_path: Path,
) -> None:
    """`--baseline N` is a real baseline, even without the per-module file."""
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    assert (
        gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--baseline",
                "99",
                "--require-baseline",
            ]
        )
        == 0
    )


# --- Codex review: deleting a guard must be attributed to its function --------


def test_a_pure_deletion_still_gates_the_function_it_was_deleted_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting a condition is the most direct way to weaken a detector, and it
    produces a hunk with no added lines at all."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    deletion_diff = _write(
        tmp_path,
        "d.diff",
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n@@ -2,1 +1,0 @@\n-    return 1\n",
    )
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
    )
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            _baseline(tmp_path, {"abicheck/diff_types.py": 10}),
            "--diff-scoped",
            "--diff-file",
            deletion_diff,
        ]
    )
    assert rc == 1
    assert "abicheck/diff_types.py::alpha" in capsys.readouterr().out


# --- Codex review: two independent sources must agree ------------------------


def test_a_parser_stats_disagreement_fails_instead_of_gating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure mode a permitted `mutmut>=3.7,<4` update can cause.

    If mutmut changes its `results` format, the listing parses to nothing, the
    summary fallback yields 0, and the exported stats still prove mutants ran —
    so `_measurement_is_complete` is satisfied and every gate passes while real
    survivors exist. Cross-checking the two sources is what makes that
    detectable rather than silent.
    """
    results = _write(tmp_path, "r.txt", "some future format nobody parses\n")
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda d: {"total": 40, "survived": 7, "killed": 33}
    )
    rc = gate.main(["--results-file", results, "--baseline", "0"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "parsed 0 surviving mutant(s)" in out
    assert "reports 7" in out


def test_agreeing_sources_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative control — the check must not fire on a healthy run."""
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda d: {"total": 40, "survived": 3, "killed": 37}
    )
    assert gate.main(["--results-file", results, "--baseline", "99"]) == 0


def test_absent_stats_do_not_trigger_the_cross_check(tmp_path: Path) -> None:
    """`--results-file` without a mutants/ directory is a legitimate mode."""
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    assert gate.main(["--results-file", results, "--baseline", "99"]) == 0


# --- Codex review: a count without attribution cannot drive per-module gates --


def test_summary_only_survivors_cannot_pass_the_per_module_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`🙁 3` gives a count but no attribution, so `by_module` is empty — the
    per-module comparison then finds nothing to compare and reports success."""
    results = _write(tmp_path, "r.txt", "🙁 3")
    baseline = _baseline(tmp_path, {"abicheck/diff_types.py": 5})
    rc = gate.main(["--results-file", results, "--baseline-file", baseline])
    assert rc == 1
    assert "no per-mutant listing" in capsys.readouterr().out


def test_summary_only_survivors_cannot_write_a_baseline(tmp_path: Path) -> None:
    """It would record `total_survivors: 0` and gate every later run on that."""
    results = _write(tmp_path, "r.txt", "🙁 3")
    out_file = tmp_path / "baseline.json"
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            str(out_file),
            "--write-baseline",
        ]
    )
    assert rc == 1
    assert not out_file.exists()


def test_summary_only_survivors_cannot_pass_the_diff_scoped_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff, _ = _diff_scoped_env(tmp_path, monkeypatch)
    results = _write(tmp_path, "r.txt", "🙁 3")
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            str(tmp_path / "absent.json"),
            "--diff-scoped",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 1


def test_summary_only_survivors_still_serve_the_global_total_gate(
    tmp_path: Path,
) -> None:
    """Negative control: counting needs no attribution, so the legacy
    whole-repository check must keep working on a summary."""
    results = _write(tmp_path, "r.txt", "🙁 3")
    assert (
        gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--baseline",
                "5",
            ]
        )
        == 0
    )


def test_zero_survivors_from_a_summary_is_not_blocked(tmp_path: Path) -> None:
    """A clean summary carries no attribution either, but has nothing to lose."""
    results = _write(tmp_path, "r.txt", "🎉 40  🙁 0")
    baseline = _baseline(tmp_path, {"abicheck/diff_types.py": 0})
    assert gate.main(["--results-file", results, "--baseline-file", baseline]) == 0


def test_an_unresolvable_base_ref_fails_the_diff_scoped_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`git diff` against a bad ref exits nonzero and prints to stderr. Reading
    that as a diff yields zero changed functions, so the gate reported OK with
    survivors present — a typo in --base-ref silently disabled it."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
    )
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            _baseline(tmp_path, {"abicheck/diff_types.py": 10}),
            "--diff-scoped",
            "--base-ref",
            "definitely-not-a-ref",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "would pass vacuously" in out
    assert "diff-scoped OK" not in out


def test_a_module_scope_change_gates_every_survivor_in_that_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A top-level policy table can change what any function in the module
    does, so a module-scope edit gates the module's survivors rather than
    matching none of them."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(
        "_KINDS = frozenset({'a'})\n\n\ndef untouched():\n    return _KINDS\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    diff = _write(
        tmp_path,
        "d.diff",
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n@@ -1,1 +1,1 @@\n+_KINDS = frozenset({'a', 'b'})\n",
    )
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_untouched__mutmut_1: survived\n"
    )
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            _baseline(tmp_path, {"abicheck/diff_types.py": 10}),
            "--diff-scoped",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 1
    assert "module-scope change" in capsys.readouterr().out


class TestChangedScopeProbe:
    """`--print-changed-scope` decides whether mutation.yml may reuse the
    mutmut cache. It fails safe: anything it cannot determine answers
    "module", i.e. do a full run."""

    def _probe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diff: str) -> str:
        (tmp_path / "abicheck").mkdir(exist_ok=True)
        (tmp_path / "abicheck" / "diff_types.py").write_text(
            "_KINDS = frozenset({'a'})\n\n\ndef f():\n    return _KINDS\n",
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text(
            '[tool.mutmut]\nsource_paths = ["abicheck/diff_types.py"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
        path = _write(tmp_path, "d.diff", diff)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            gate.main(["--print-changed-scope", "--diff-file", path])
        return buf.getvalue().strip()

    def test_a_module_level_edit_answers_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        diff = "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n@@ -1,1 +1,1 @@\n+_KINDS = frozenset({'a','b'})\n"
        assert self._probe(tmp_path, monkeypatch, diff) == "module"

    def test_a_function_body_edit_answers_function(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        diff = "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n@@ -5,1 +5,1 @@\n+    return None\n"
        assert self._probe(tmp_path, monkeypatch, diff) == "function"

    def test_a_change_outside_the_mutated_modules_answers_function(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the mutated modules' staleness matters."""
        diff = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1,1 +1,1 @@\n+text\n"
        assert self._probe(tmp_path, monkeypatch, diff) == "function"

    def test_an_unresolvable_base_ref_fails_safe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            gate.main(["--print-changed-scope", "--base-ref", "definitely-not-a-ref"])
        assert buf.getvalue().strip() == "module"
