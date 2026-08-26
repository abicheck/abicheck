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

"""Unit tests for scripts/check_mutation_score.py's run-scoping
(``--scope-run-to-diff``).

Split out of tests/test_mutation_score_gate.py (which was already past the
file-size soft limit) rather than grown further — see that file for the
gate's general parsing/drift-logic tests; this file is scoped to one
feature.

`mutmut run` always *generates* mutants for the whole `only_mutate` set —
only the test-execution phase can be scoped, via its own `MUTANT_NAMES`
positional argument (verified directly against a real, installed mutmut
3.7.0: `collect_source_file_mutation_data` fnmatches each given pattern
against every mutant key and filters `tests_for_mutant_names` to the
matches). These tests cover the pure pattern-building helpers and the
scope-aware unresolved-gating main() needs so an out-of-scope module's
deliberately-untested ("not checked") mutants never fail a scoped run.
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
        f"[tool.mutmut]\nonly_mutate = [\n{items}\n]\n", encoding="utf-8"
    )


def test_mutant_scope_pattern_matches_mutmuts_dotted_key_format() -> None:
    assert gate.mutant_scope_pattern("abicheck/diff_symbols.py") == (
        "abicheck.diff_symbols.*"
    )
    assert gate.mutant_scope_pattern("abicheck/buildsource/inline.py") == (
        "abicheck.buildsource.inline.*"
    )


def test_load_only_mutate_globs_reads_the_real_pyproject_toml() -> None:
    """Sanity: the real config this repo ships parses and names real modules."""
    only_mutate = gate.load_only_mutate_globs()
    assert only_mutate is not None
    assert "abicheck/diff_symbols.py" in only_mutate
    assert "abicheck/checker_policy.py" in only_mutate


def test_load_only_mutate_globs_returns_none_when_unreadable(tmp_path: Path) -> None:
    assert gate.load_only_mutate_globs(tmp_path / "does-not-exist.toml") is None


def test_diff_touched_only_mutate_modules_uses_added_and_removed_lines() -> None:
    only_mutate = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    # diff_types.py: pure modification. diff_symbols.py: pure deletion (no
    # new-side hunk at all) — the case the module docstring calls out.
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n"
        "+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    pass\n"
        "diff --git a/abicheck/diff_symbols.py b/abicheck/diff_symbols.py\n"
        "--- a/abicheck/diff_symbols.py\n"
        "+++ b/abicheck/diff_symbols.py\n"
        "@@ -5,1 +4,0 @@\n"
        "-    pass\n"
        "diff --git a/abicheck/service.py b/abicheck/service.py\n"
        "--- a/abicheck/service.py\n"
        "+++ b/abicheck/service.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    pass\n"
    )
    touched = gate.diff_touched_only_mutate_modules(diff, only_mutate)
    # service.py is real but not in only_mutate, and must not appear.
    assert touched == {"abicheck/diff_types.py", "abicheck/diff_symbols.py"}


def test_mutant_run_scope_is_none_without_a_diff_or_config() -> None:
    assert gate.mutant_run_scope(None, ["abicheck/diff_types.py"]) is None
    assert gate.mutant_run_scope("some diff", None) is None
    assert gate.mutant_run_scope("some diff", []) is None


def test_mutant_run_scope_is_none_when_nothing_in_scope_is_touched() -> None:
    diff = (
        "diff --git a/abicheck/service.py b/abicheck/service.py\n"
        "--- a/abicheck/service.py\n+++ b/abicheck/service.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.mutant_run_scope(diff, ["abicheck/diff_types.py"]) is None


def test_mutant_run_scope_is_none_when_every_module_is_touched() -> None:
    """Scoping would filter nothing, so it's not worth the extra invocation shape."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.mutant_run_scope(diff, ["abicheck/diff_types.py"]) is None


def test_mutant_run_scope_narrows_to_the_touched_module() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    only_mutate = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    assert gate.mutant_run_scope(diff, only_mutate) == ["abicheck.diff_types.*"]


def test_run_mode_passes_the_scope_patterns_to_mutmut_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--scope-run-to-diff`` reaches the actual `mutmut run` argv."""
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
            return "1/1  🎉 1  🙁 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 0
    run_cmd = seen_cmds[0]
    assert run_cmd[:2] == ["mutmut", "run"]
    assert run_cmd[2:] == ["abicheck.diff_types.*"]


def test_run_mode_scoped_run_does_not_fail_on_out_of_scope_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dominant case this feature exists for: everything outside the
    touched module reads "not checked" (never test-executed), which must not
    be gated as an unresolved measurement — only what was actually in scope."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return "2/2  🎉 1  🙁 0  🫥 0  ⏰ 0  🤔 0", 0
        # In scope: killed. Out of scope (diff_symbols.py, never touched by
        # this diff): "not checked" — the real status an untested mutant gets.
        return (
            "    abicheck.diff_types.x_alpha__mutmut_1: killed\n"
            "    abicheck.diff_symbols.x_beta__mutmut_1: not checked\n"
        ), 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate,
        "load_cicd_stats",
        lambda _dir: {"total": 2, "survived": 0, "killed": 1, "not_checked": 1},
    )
    rc = gate.main(
        ["--run", "--diff-scoped", "--scope-run-to-diff", "--diff-file", diff]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "did not resolve" not in out


def test_run_mode_unscoped_run_still_fails_on_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the test above: without --scope-run-to-diff, an
    unresolved mutant anywhere still fails a diff-scoped run, exactly as
    before this feature existed."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("2/2  🎉 1  🙁 0  🫥 0  ⏰ 1  🤔 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else (
                "    abicheck.diff_types.x_alpha__mutmut_1: killed\n"
                "    abicheck.diff_symbols.x_beta__mutmut_1: timeout\n",
                0,
            )
        ),
    )
    rc = gate.main(["--run", "--diff-scoped", "--diff-file", diff])
    assert rc == 1


def test_write_baseline_never_scopes_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--write-baseline must always see the full population, never a subset."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--write-baseline",
            "--baseline-file",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert rc == 0
    assert seen_cmds[0] == ["mutmut", "run"]


def test_receipt_records_run_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("1/1  🎉 1  🙁 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else ("    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0)
        ),
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0}
    )
    receipt = tmp_path / "receipt.json"
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--json",
            str(receipt),
        ]
    )
    assert rc == 0
    doc = json.loads(receipt.read_text())
    assert doc["run_scope"]["mode"] == "diff"
    assert doc["run_scope"]["modules"] == ["abicheck/diff_types.py"]
    assert doc["run_scope"]["requested"] is True
