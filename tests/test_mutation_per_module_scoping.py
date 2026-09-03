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

"""Unit tests for scripts/check_mutation_score.py's `check_per_module` vs. a
scoped run's incomplete population.

Split out of tests/test_mutation_run_scoping.py (which was already past the
architecture gate's 1200-line test-file cap) rather than grown further — see
that file for the general run-scoping tests; this file is scoped to one gap
in how the per-module baseline gate reads a scoped measurement.

A scoped run (``--scope-run-to-diff``) never test-executes a mutant outside
``scope_modules`` — every such mutant reads ``not checked``, so
`survivors_by_module` reports zero survivors for it regardless of the
module's true state. Comparing that unconditionally against a per-module
baseline reads as "still within baseline" for a module this run never
measured at all — `only_mutate` modules can import each other (e.g.
``diff_types.py`` imports ``diff_symbols``), so a diff touching only one can
leave a real regression in the other's mutants completely unreported (Codex
review, PR #877). `check_per_module` now takes the same `scope_modules` a
scoped run already threads through `unresolved_for_gate`, restricts its
comparison to it, and reports every excluded baseline module as *skipped*
rather than silently folding it into "no failures".
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


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


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


def _pyproject_with_only_mutate(tmp_path: Path, only_mutate: list[str]) -> None:
    # A minimal but real TOML document — hand-written rather than via
    # tomllib's write-side (stdlib has none), since only_mutate's own strings
    # are plain module paths with no character needing escaping.
    items = ",\n".join(f'  "{m}"' for m in only_mutate)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mutmut]\nonly_mutate = [\n{items}\n]\n",
        encoding="utf-8",
    )


def test_check_per_module_skips_modules_outside_scope() -> None:
    records = [
        gate.MutantRecord(
            key="abicheck.diff_types.x_alpha__mutmut_1",
            module="abicheck.diff_types",
            function="alpha",
            status="survived",
        ),
    ]
    baseline = {"abicheck/diff_types.py": 0, "abicheck/diff_symbols.py": 3}
    failures, skipped = gate.check_per_module(
        records, baseline, {"abicheck/diff_types.py"}
    )
    # A real regression *within* scope is still caught.
    assert any("diff_types.py" in f for f in failures)
    # The untested module is reported as skipped, never silently passed —
    # its baseline count (3) was not re-verified this run, whatever the
    # true current count actually is.
    assert skipped == ["abicheck/diff_symbols.py"]
    assert not any("diff_symbols.py" in f for f in failures)


def test_check_per_module_without_scope_compares_everything() -> None:
    """Backward-compat control: an unscoped call (scope_modules=None, the
    weekly/dispatch lanes' shape) behaves exactly as before this fix —
    every baseline module is compared and none are reported as skipped."""
    records = [
        gate.MutantRecord(
            key="abicheck.diff_symbols.x_beta__mutmut_1",
            module="abicheck.diff_symbols",
            function="beta",
            status="survived",
        ),
    ]
    baseline = {"abicheck/diff_types.py": 0, "abicheck/diff_symbols.py": 0}
    failures, skipped = gate.check_per_module(records, baseline, None)
    assert skipped == []
    assert any("diff_symbols.py" in f for f in failures)


def test_check_per_module_empty_scope_compares_everything() -> None:
    """An empty `scope_modules` set means "this call was not scoped" (the
    same convention `main()` already uses for `unresolved_for_gate`), not
    "scope to nothing" — an empty set must not vacuously skip every module."""
    records = [
        gate.MutantRecord(
            key="abicheck.diff_symbols.x_beta__mutmut_1",
            module="abicheck.diff_symbols",
            function="beta",
            status="survived",
        ),
    ]
    baseline = {"abicheck/diff_symbols.py": 0}
    failures, skipped = gate.check_per_module(records, baseline, set())
    assert skipped == []
    assert any("diff_symbols.py" in f for f in failures)


def test_run_mode_scoped_run_does_not_silently_pass_an_out_of_scope_baseline_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: a real regression in a module the diff never touched must
    not be reported as "per-module baseline OK" just because the scoped run
    never re-tested it."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    baseline_file = tmp_path / "mutation-baseline.json"
    baseline_file.write_text(
        json.dumps(
            {
                "modules": {
                    "abicheck/diff_types.py": {"survivors": 0, "functions": {}},
                    "abicheck/diff_symbols.py": {"survivors": 1, "functions": {}},
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0  🫥 0  ⏰ 0  🤔 0", 0
        # In scope: killed. diff_symbols.py is out of scope and never
        # re-tested this run — its baseline (1 survivor) cannot be
        # confirmed or refuted by this measurement.
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--baseline-file",
            str(baseline_file),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "per-module baseline check skipped" in out
    assert "abicheck/diff_symbols.py" in out
    # The scoped module is reported OK on its own terms, not folded into an
    # unqualified "per-module baseline OK" that would imply diff_symbols.py
    # was re-verified too.
    assert (
        "per-module baseline OK for the scoped module(s): abicheck/diff_types.py" in out
    )


def test_run_mode_scoped_run_still_catches_a_regression_in_the_scoped_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a real regression *inside* the scoped module must
    still fail the run — scoping narrows what is measured, not what is
    enforced within that narrowed set."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    baseline_file = tmp_path / "mutation-baseline.json"
    baseline_file.write_text(
        json.dumps(
            {"modules": {"abicheck/diff_types.py": {"survivors": 0, "functions": {}}}}
        ),
        encoding="utf-8",
    )

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 0  🙁 1  🫥 0  ⏰ 0  🤔 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: survived\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 1, "killed": 0}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--baseline-file",
            str(baseline_file),
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# The identical gap one level flatter (Codex review, PR #877, fifteenth
# round): the *global-total* baseline (`--baseline`/`SURVIVOR_BASELINE`)
# compares `survivors` — the whole-records survivor count — against a total
# established from a full run. A scoped run's `survivors` is really only the
# scoped module(s)' own count (every out-of-scope mutant reads "not
# checked", never "survived"), so comparing it against the recorded total
# is meaningless — and, worse, a real out-of-scope regression would read as
# "improved, please lower the baseline". Unlike the per-module gate, there
# is no narrower population to restrict a *total* to, so the only sound fix
# is to skip the comparison outright for a scoped run.
# ---------------------------------------------------------------------------


def test_scoping_disabled_when_only_a_global_baseline_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A module-scope edit (one outside every function) has no mutant of its
    own for check_diff_scoped() to attribute — by design it can only be
    caught by check_per_module(), which needs a per-module baseline. With
    only the legacy global total configured (no per-module baseline file),
    scoping would leave *no* gate standing for such an edit at all, so it
    must fall back to a full run instead (Codex review, PR #877, sixteenth
    round)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0  🫥 0  ⏰ 0  🤔 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--baseline",
            "5",
            # No --baseline-file pointed at a real per-module baseline; the
            # default path doesn't exist in tmp_path, so baseline_modules
            # loads as None — exactly the "only the global fallback is
            # configured" shape this fix targets.
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    # A real, unscoped `mutmut run` — no MUTANT_NAMES restricting it.
    assert seen_cmds[0] == ["mutmut", "run"]
    assert "mutation-score: scoping this run to" not in out
    assert "global-total baseline check skipped" not in out
    assert "please lower SURVIVOR_BASELINE" in out


def test_run_mode_scoped_run_skips_the_global_total_baseline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With a real per-module baseline also present, scoping proceeds
    normally, and the global-total gate still declines to score a scoped
    (necessarily partial) survivor count against a whole-repository total —
    the fifteenth-round fix, now verified to survive the sixteenth-round
    scoping-disable check landing right next to it."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    baseline_file = tmp_path / "mutation-baseline.json"
    baseline_file.write_text(
        json.dumps(
            {
                "modules": {
                    "abicheck/diff_types.py": {"survivors": 0, "functions": {}},
                    "abicheck/diff_symbols.py": {"survivors": 0, "functions": {}},
                }
            }
        ),
        encoding="utf-8",
    )

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0  🫥 0  ⏰ 0  🤔 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--baseline",
            "5",
            "--baseline-file",
            str(baseline_file),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    # A real per-module baseline is present, so scoping proceeds for real.
    assert any(cmd[:2] == ["mutmut", "run"] and len(cmd) > 2 for cmd in seen_cmds), out
    assert "global-total baseline check skipped" in out
    assert "please lower SURVIVOR_BASELINE" not in out
    assert "OK (0 == baseline 5)" not in out
    # And check_per_module() itself still gates for real.
    assert "per-module baseline OK for the scoped module(s)" in out


def test_run_mode_unscoped_run_still_gates_the_global_total_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: without --scope-run-to-diff, a real regression
    against the global-total baseline still fails the run exactly as before
    this fix — nothing here weakens the unscoped case."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("1/1  🎉 0  🙁 1  🫥 0  ⏰ 0  🤔 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else ("    abicheck.diff_types.x_alpha__mutmut_1: survived\n", 0)
        ),
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 1, "killed": 0}
    )
    rc = gate.main(["--run", "--baseline", "0"])
    assert rc == 1
