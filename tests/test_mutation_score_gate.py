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
import json
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


def test_mutmut_subprocess_timeout_matches_the_workflow_ceiling() -> None:
    """The Python cap must stay close to, but strictly below, the GitHub
    Actions job deadline.

    Not equal to it: the job's checkout/dependency-install/parser-
    verification steps run *before* this subprocess call and its own "Save
    mutmut results"/"Upload mutmut results" steps run *after* -- a
    subprocess timeout equal to the full job budget leaves no room for any
    of that, so GitHub kills the whole job mid-subprocess before those
    steps can produce a receipt or upload anything (Codex review). Not too
    far below it either, or the cap wastes job budget the run could have
    used.
    """
    workflow = (
        Path(__file__).resolve().parent.parent
        / ".github"
        / "workflows"
        / "mutation.yml"
    ).read_text(encoding="utf-8")
    assert "timeout-minutes: 355" in workflow
    job_timeout_seconds = 355 * 60
    headroom_seconds = job_timeout_seconds - gate.MUTMUT_RUN_TIMEOUT_SECONDS
    assert 5 * 60 <= headroom_seconds <= 15 * 60


def test_stats_without_a_survivor_count_are_not_a_completion_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`total` alone is not proof the run finished.

    The parsed-vs-exported cross-check is conditional on `survived`, so a
    receipt carrying only `total` skipped it while still counting as proof —
    and with an unrecognised results format alongside, the survivor count
    defaulted to zero and passed a zero baseline unvalidated (Codex review).
    """
    results = _write(tmp_path, "r.txt", "nothing this parser understands")
    monkeypatch.setattr(gate, "load_cicd_stats", lambda _dir: {"total": 100})
    assert gate.main(["--results-file", results, "--baseline", "0"]) == 1
    assert "unmeasurable" in capsys.readouterr().out


def test_complete_stats_are_still_a_completion_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a receipt with both keys still proves the run ran, so
    an empty per-mutant listing really does mean every mutant was killed."""
    results = _write(tmp_path, "r.txt", "")
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 100, "survived": 0}
    )
    assert gate.main(["--results-file", results, "--baseline", "0"]) == 0


def test_an_explicit_but_unmeasurable_results_file_fails(tmp_path: Path) -> None:
    """Empty results named explicitly are a failure, not a graceful skip.

    This test previously asserted 0, on the analogy of the mypy-skip pattern.
    The analogy does not hold: a missing *tool* is a reason to skip, but a
    caller who named an input and asked for a verdict on it has been told
    "cannot tell" — and a gate reporting success for that is the false green
    this script exists to remove (Codex review).
    """
    results = tmp_path / "empty.txt"
    results.write_text("", encoding="utf-8")
    assert gate.main(["--results-file", str(results), "--baseline", "5"]) == 1


def test_gate_fails_when_survivors_exceed_baseline(tmp_path: Path) -> None:
    results = tmp_path / "results.txt"
    results.write_text("20/20  🎉 11  🙁 9", encoding="utf-8")
    rc = gate.main(["--results-file", str(results), "--baseline", "3"])
    assert rc == 1


def test_gate_reports_only_when_baseline_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = tmp_path / "results.txt"
    results.write_text("50/50  🎉 8  🙁 42", encoding="utf-8")
    # No --baseline and module default is None -> report-only, never fails.
    rc = gate.main(["--results-file", str(results)])
    assert rc == 0
    assert "42 surviving mutant" in capsys.readouterr().out


def test_gate_at_baseline_is_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = tmp_path / "results.txt"
    results.write_text("10/10  🎉 7  🙁 3", encoding="utf-8")
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
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: ("10/10  🎉 8  🙁 2", 0))
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
    monkeypatch.setattr(
        gate, "_run_mutmut", lambda cmd: ("10/10  🎉 7  🙁 0  ⏰ 2  🤔 1", 0)
    )
    assert gate.main(["--run", "--baseline", "0"]) == 1


def test_unresolved_does_not_fail_report_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """In report-only mode (no baseline) unresolved mutants are surfaced but the
    gate does not fail — it is only reporting."""
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(gate, "_run_mutmut", lambda cmd: ("10/10  🎉 8  🙁 0  ⏰ 2", 0))
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


def test_no_run_unparseable_still_fails(tmp_path: Path) -> None:
    """Unparseable content is the same situation as empty content: the caller
    supplied it and the gate cannot say anything about it."""
    results = tmp_path / "garbage.txt"
    results.write_text("nothing useful", encoding="utf-8")
    assert gate.main(["--results-file", str(results), "--baseline", "0"]) == 1


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


def test_write_baseline_records_per_module_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    results = _write(tmp_path, "r.txt", _TWO_MODULES)
    out_file = str(tmp_path / "baseline.json")
    # A real recording run exports stats; this fixture is a bare listing, so
    # the completion witness has to come from somewhere (see
    # test_write_baseline_refuses_a_capture_that_cannot_prove_completion).
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 10, "survived": 3}
    )
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


def test_write_baseline_refuses_a_capture_that_cannot_prove_completion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`mutmut results` prints no progress counter, so a listing truncated
    after a few lines reads exactly like a whole one.

    Tolerable for gating — a short capture can only under-report, and the
    caller produced it — but not for *recording*: a partial survivor set
    written as the baseline becomes what every later run is scored against,
    silently and permanently (Codex review).
    """
    results = _write(tmp_path, "r.txt", _TWO_MODULES)
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
    assert "cannot prove it is complete" in capsys.readouterr().out
    assert not out_file.exists()


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


def test_diff_scoped_allows_recorded_legacy_survivors_but_rejects_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A baseline makes a legacy function a delta gate, not a permanent ban."""
    diff, baseline = _diff_scoped_env(tmp_path, monkeypatch)
    Path(baseline).write_text(
        json.dumps(
            {
                "modules": {
                    "abicheck/diff_types.py": {
                        "survivors": 10,
                        "keys": [],
                        "functions": {"alpha": 1},
                    }
                }
            },
        ),
        encoding="utf-8",
    )
    one = _write(
        tmp_path, "one.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
    )
    two = _write(
        tmp_path,
        "two.txt",
        "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        "    abicheck.diff_types.x_alpha__mutmut_2: survived\n",
    )
    common = ["--baseline-file", baseline, "--diff-scoped", "--diff-file", diff]
    assert gate.main(["--results-file", one, *common]) == 0
    assert gate.main(["--results-file", two, *common]) == 1
    assert "alpha: 1 -> 2" in capsys.readouterr().out


def test_an_unreadable_results_file_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicitly named input that cannot be read is a failed invocation.

    It shared the "no tool, nothing to do" exit, so a scripted offline check
    reported success having processed no results at all (Codex review) — the
    same can't-fail shape as a --run that produced nothing, reached from the
    other direction.
    """
    rc = gate.main(["--results-file", str(tmp_path / "nope.txt")])
    assert rc == 1
    assert "could not be read" in capsys.readouterr().out


def test_no_results_file_and_no_run_is_still_a_clean_skip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Negative control: the optional path stays optional. Without --run and
    without an explicit input there is genuinely nothing to do."""
    assert gate.main([]) == 0


@pytest.mark.parametrize(
    "text",
    [
        # The opening render of a run that was then interrupted. Identical in
        # shape to a clean finish, and it parsed as "zero survivors".
        "0/100  🎉 0 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0",
        "309/464  🎉 300  🙁 0",
        # No counter at all: a bare count proves nothing about completion.
        "🙁 0",
    ],
)
def test_an_incomplete_summary_render_is_not_a_measurement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    """A survivor count is not a completion witness (Codex review).

    mutmut prints `{total - not_checked}/{total}`, so the halves match exactly
    when nothing is left unchecked. Without that, an interrupted run's own
    `🙁 0` passed a zero baseline — the false green this gate exists to remove.
    """
    results = _write(tmp_path, "r.txt", text)
    # Exit 1, not 0: the caller named an input and asked for a verdict on it.
    # The first version of this test asserted 0 and so encoded the very
    # false-green it was written to prevent (Codex review).
    assert gate.main(["--results-file", results, "--baseline", "0"]) == 1
    assert "unmeasurable" in capsys.readouterr().out


def test_a_completed_summary_render_still_measures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control: the witness must not reject a real finished run —
    N == N is exactly what mutmut prints when nothing is left unchecked."""
    results = _write(tmp_path, "r.txt", "12/12  🎉 12 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0")
    assert gate.main(["--results-file", results, "--baseline", "0"]) == 0
    assert "unmeasurable" not in capsys.readouterr().out


def test_a_deleted_file_does_not_leak_into_the_previous_file() -> None:
    """`+++ /dev/null` must end the previous file's attribution.

    It does not match `+++ b/`, so the deleted file's hunk was recorded
    against whichever file preceded it — and a pure deletion takes the
    count == 0 path, which invents two line numbers. The diff-scoped gate
    could then fail on a function the branch never touched (CodeRabbit
    review). The existing deletion fixture missed it by having no preceding
    file, leaving `current` None.
    """
    diff = (
        "--- a/keep.py\n+++ b/keep.py\n@@ -1,0 +2,1 @@\n+x = 1\n"
        "--- a/gone.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n-y = 2\n"
    )
    assert gate.parse_changed_lines(diff) == {"keep.py": {2}}


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
        # A pure deletion contributes no new-side line at all. It is not
        # skipped — `parse_removed_lines` answers it against the base, where
        # those lines actually exist (see the pair below). Attributing it here
        # meant inventing a new-side number, and every such guess named the
        # wrong function for at least one real diff shape.
        ("@@ -3,1 +3,0 @@", set()),
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
    assert "no baseline is available" in capsys.readouterr().out


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


#: A base revision with a function between two others — the shape that tells
#: "deleted a line *inside* a function" apart from "deleted a whole function".
_BASE_THREE = """\
def before():
    return 1


def removed():
    return 2


def after():
    return 3
"""

#: The same file after `removed()` is deleted outright (base lines 5-8).
_NEW_TWO = """\
def before():
    return 1


def after():
    return 3
"""


def _with_base(
    monkeypatch: pytest.MonkeyPatch, sources: dict[str, str]
) -> list[str | None]:
    """Give `main()` a base revision to resolve removed lines against.

    `_base_reader` shells out to `git merge-base`/`git show`, which cannot
    work in a `tmp_path` that is not a repository — so the reader is injected
    here. `TestAgainstRealGit` covers the git plumbing itself.
    """
    asked: list[str | None] = []

    def _reader(base_ref: str) -> object:
        def _read(path: str) -> str | None:
            asked.append(path)
            return sources.get(path)

        return _read

    monkeypatch.setattr(gate, "_base_reader", _reader)
    return asked


def test_a_pure_deletion_still_gates_the_function_it_was_deleted_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting a condition is the most direct way to weaken a detector, and it
    produces a hunk with no added lines at all. Base line 2 is inside
    `alpha()`, so `alpha` is what the branch touched."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    _with_base(monkeypatch, {"abicheck/diff_types.py": _SOURCE})
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


def test_deleting_a_whole_function_does_not_gate_its_neighbour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The false positive the previous new-side guess produced (Codex review).

    Deleting `removed()` leaves a hunk anchored at the blank line before
    `after()`, so attributing "the anchor and its successor" marked `after()`
    as edited — and any *pre-existing* survivor in `after()` then failed the
    branch that never touched it. Resolved against the base, those lines
    belong to `removed()`, which no longer exists and therefore matches no
    mutant.
    """
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_NEW_TWO, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    _with_base(monkeypatch, {"abicheck/diff_types.py": _BASE_THREE})
    deletion_diff = _write(
        tmp_path,
        "d.diff",
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -5,4 +4,0 @@\n-def removed():\n-    return 2\n-\n-\n",
    )
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_types.x_after__mutmut_1: survived\n"
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
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "diff_types.py::after" not in out


def test_removed_lines_are_read_from_the_merge_base_not_the_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving base-side numbers against the *new* file is the same class of
    error as guessing a new-side number: line 6 of the new file is inside
    `after()`, line 6 of the base is inside `removed()`."""
    asked = _with_base(monkeypatch, {"abicheck/diff_types.py": _BASE_THREE})
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_NEW_TWO, encoding="utf-8")
    touched = gate.changed_functions(
        {},
        tmp_path,
        {"abicheck/diff_types.py": ("abicheck/diff_types.py", {5, 6})},
        gate._base_reader("origin/main"),
    )
    assert touched == {"abicheck/diff_types.py": {"removed"}}
    assert asked == ["abicheck/diff_types.py"]


def test_an_unreadable_base_fails_closed_when_a_survivor_could_be_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch whose only edit is a deletion has all of its evidence on the
    removed side, so an unresolvable base must not print the same OK line as a
    fully resolved run — and must not exit 0 either.

    Warning and exiting 0 was the first version of this, and it was the same
    can't-fail shape the whole gate exists to remove: the survivor in the
    function the branch gutted goes unreported, and the run is green (Codex
    review).
    """
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_reader", lambda base_ref: None)
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
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "WARNING" in out
    assert "abicheck/diff_types.py" in out


def test_an_unreadable_base_only_warns_when_nothing_could_be_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control, and the reason the failure is conditional: an
    unattributed removal in a module with no surviving mutant has nothing to
    hide — there is no survivor to attribute — so failing there would block
    branches on an absence of evidence that cannot matter."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_reader", lambda base_ref: None)
    deletion_diff = _write(
        tmp_path,
        "d.diff",
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n@@ -2,1 +1,0 @@\n-    return 1\n",
    )
    results = _write(
        tmp_path, "r.txt", "    abicheck.diff_platform.x_other__mutmut_1: survived\n"
    )
    rc = gate.main(
        [
            "--results-file",
            results,
            "--baseline-file",
            _baseline(tmp_path, {"abicheck/diff_platform.py": 10}),
            "--diff-scoped",
            "--diff-file",
            deletion_diff,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "WARNING" in out


def test_a_modification_is_not_treated_as_an_unattributable_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Second negative control: every modified line is removed *and* added, so
    keying the failure on removals alone would fail closed on an ordinary edit
    whose new side is already attributed."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_reader", lambda base_ref: None)
    edit_diff = _write(
        tmp_path,
        "d.diff",
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -2,1 +2,1 @@\n-    return 1\n+    return 2\n",
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
            edit_diff,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "unattributed removals" not in out


def test_a_reader_that_answers_none_for_one_path_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader can exist and still not answer for one path.

    Keying the risk check on `read_base is None` asked "is there a reader",
    not "was this path resolved" — so a base that simply does not carry the
    file (a rename, an unreadable blob) came back `None`, the removal was
    skipped, and the survivor in the gutted function passed `--diff-scoped`
    (Codex and CodeRabbit, independently).
    """
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    # Callable, and unhelpful — the shape the `is None` guard could not see.
    _with_base(monkeypatch, {})
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
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "abicheck/diff_types.py" in out


def test_a_renamed_module_reads_its_removals_from_the_old_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same conflation, and why it is not just a
    fail-closed: a rename's removed lines *are* readable — under the old
    name — and belong to the new one, which is what a mutant key carries."""
    asked = _with_base(monkeypatch, {"abicheck/old_name.py": _SOURCE})
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    removed = gate.parse_removed_lines(
        "diff --git a/abicheck/old_name.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/old_name.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -2,1 +1,0 @@\n-    return 1\n"
    )
    assert removed == {"abicheck/diff_types.py": ("abicheck/old_name.py", {2})}
    touched = gate.changed_functions(
        {}, tmp_path, removed, gate._base_reader("origin/main")
    )
    # Read from the old name, filed under the new one.
    assert touched == {"abicheck/diff_types.py": {"alpha"}}
    assert asked == ["abicheck/old_name.py"]
    assert gate.unresolved_removals(removed, gate._base_reader("origin/main")) == set()


def test_a_whole_file_deletion_is_still_a_pure_deletion(tmp_path: Path) -> None:
    """`+++ /dev/null` has no new path at all, so keying on the new side alone
    dropped a deleted module from the risk set entirely — the one shape with
    nothing downstream to notice it (CodeRabbit)."""
    diff = (
        "diff --git a/abicheck/gone.py b/abicheck/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/abicheck/gone.py\n+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n-def f():\n-    return 1\n-\n"
    )
    assert gate.pure_deletion_paths(diff) == {"abicheck/gone.py"}
    assert gate.parse_removed_lines(diff) == {
        "abicheck/gone.py": ("abicheck/gone.py", {1, 2, 3})
    }
    # And it must not leak onto a neighbouring file in the same diff.
    assert gate.parse_changed_lines(diff) == {}


def test_pure_deletion_paths_separates_deletions_from_edits() -> None:
    """The predicate itself: only a hunk with no new side has its evidence
    exclusively in the base."""
    diff = (
        "--- a/gone.py\n+++ b/gone.py\n@@ -5,4 +4,0 @@\n-a\n-b\n-c\n-d\n"
        "--- a/edited.py\n+++ b/edited.py\n@@ -20,1 +20,1 @@\n-old\n+new\n"
        "--- a/added.py\n+++ b/added.py\n@@ -3,0 +4,1 @@\n+x\n"
    )
    assert gate.pure_deletion_paths(diff) == {"gone.py"}


def test_a_deleted_module_level_constant_keeps_module_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a global is a module-scope edit, and only the base says so.

    The new-side guess resolved such a deletion against the *post*-change
    file, where the anchor can land on the following `def` — so a deleted
    global reported `function` scope and the gate then matched only that one
    function instead of every mutant in the module (Codex review).
    """
    base = "X = 1\n\n\ndef f():\n    return X\n"
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    _with_base(monkeypatch, {"abicheck/diff_types.py": base})
    touched = gate.changed_functions(
        {},
        tmp_path,
        {"abicheck/diff_types.py": ("abicheck/diff_types.py", {1})},
        gate._base_reader("origin/main"),
    )
    assert touched == {"abicheck/diff_types.py": {gate.MODULE_SCOPE}}


def test_parse_removed_lines_reads_the_base_side_of_every_hunk() -> None:
    """Modified lines appear on both sides; only the base side can name the
    function a *removed* line lived in."""
    diff = (
        "--- a/x.py\n+++ b/x.py\n"
        "@@ -5,4 +4,0 @@\n-a\n-b\n-c\n-d\n"
        "@@ -20,1 +17,1 @@\n-old\n+new\n"
    )
    assert gate.parse_removed_lines(diff) == {"x.py": ("x.py", {5, 6, 7, 8, 20})}


def test_parse_removed_lines_ignores_pure_additions() -> None:
    """Negative control: an added line has no base-side existence at all."""
    assert (
        gate.parse_removed_lines("--- a/x.py\n+++ b/x.py\n@@ -3,0 +4,2 @@\n+a\n+b\n")
        == {}
    )


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
    results = _write(tmp_path, "r.txt", "10/10  🎉 7  🙁 3")
    baseline = _baseline(tmp_path, {"abicheck/diff_types.py": 5})
    rc = gate.main(["--results-file", results, "--baseline-file", baseline])
    assert rc == 1
    assert "no per-mutant listing" in capsys.readouterr().out


def test_summary_only_survivors_cannot_write_a_baseline(tmp_path: Path) -> None:
    """It would record `total_survivors: 0` and gate every later run on that."""
    results = _write(tmp_path, "r.txt", "10/10  🎉 7  🙁 3")
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
    results = _write(tmp_path, "r.txt", "10/10  🎉 7  🙁 3")
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
    results = _write(tmp_path, "r.txt", "10/10  🎉 7  🙁 3")
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
    results = _write(tmp_path, "r.txt", "40/40  🎉 40  🙁 0")
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


def test_a_module_scope_change_uses_per_module_drift_not_absolute_debt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Module scope has no precise mutant attribution, so baseline drift gates it.

    Historical survivors must not permanently prohibit a constant/table edit;
    a raised module total is still rejected by the ordinary baseline gate.
    """
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
    assert rc == 0
    assert "per-module baseline OK" in capsys.readouterr().out
