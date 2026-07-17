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

"""Unit tests for the changelog-fragment gate's git-diff and gating logic.

Mirrors the tests/test_*_gate.py pattern used for the other CI-gating
scripts/check_*.py scripts (see scripts/CLAUDE.md's inventory table).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_changelog_fragment.py"
)
_spec = importlib.util.spec_from_file_location("check_changelog_fragment", _GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


# --- needs_changelog_entry -------------------------------------------------


@pytest.mark.parametrize(
    "changed, expected",
    [
        ([("M", "abicheck/checker.py")], True),
        ([("D", "abicheck/old_module.py")], True),
        ([("A", "abicheck/new_module.py")], True),
        ([("M", "abicheck/buildsource/inline.py")], True),
        ([("M", "tests/test_checker.py")], False),
        ([("M", "docs/index.md")], False),
        ([("M", "scripts/check_fp_rate.py")], False),
        ([("M", "abicheck/schemas/report.json")], False),
        ([], False),
    ],
)
def test_needs_changelog_entry(changed: list[tuple[str, str]], expected: bool) -> None:
    assert gate.needs_changelog_entry(changed) is expected


# --- has_changelog_fragment --------------------------------------------------


@pytest.mark.parametrize(
    "changed, expected",
    [
        ([("A", "changelog.d/20260101_me.md")], True),
        ([("M", "changelog.d/existing_fragment.md")], True),
        # A deleted fragment leaves nothing for `scriv collect` to include.
        ([("D", "changelog.d/20260101_me.md")], False),
        # README.md / the .j2 template are infrastructure, not entries.
        ([("M", "changelog.d/README.md")], False),
        ([("M", "changelog.d/fragment_template.md.j2")], False),
        # scriv only reads *.md (per [tool.scriv] format = "md") — a
        # non-Markdown file under changelog.d/ satisfies nothing.
        ([("A", "changelog.d/placeholder.txt")], False),
        # scriv's collector globs changelog.d/*.md non-recursively, so a
        # nested fragment would never actually be collected.
        ([("A", "changelog.d/subdir/nested.md")], False),
        ([("M", "abicheck/checker.py")], False),
        ([], False),
    ],
)
def test_has_changelog_fragment(changed: list[tuple[str, str]], expected: bool) -> None:
    assert gate.has_changelog_fragment(changed) is expected


def test_combined_gate_requires_added_or_modified_md_fragment() -> None:
    """A PR that both touches source and deletes its old fragment must fail."""
    changed = [("M", "abicheck/checker.py"), ("D", "changelog.d/old.md")]
    assert gate.needs_changelog_entry(changed) is True
    assert gate.has_changelog_fragment(changed) is False


# --- changed_files (real git integration) -----------------------------------


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repo, with the gate's ROOT pointed at it."""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    _init_repo(tmp_path)
    return tmp_path


def test_changed_files_reports_move_out_of_abicheck_as_delete_and_add(
    repo: Path,
) -> None:
    """A rename must not hide the old, now-deleted abicheck/ path (--no-renames)."""
    (repo / "abicheck").mkdir()
    (repo / "abicheck" / "foo.py").write_text("def f():\n    pass\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base = _git("rev-parse", "HEAD", cwd=repo)

    (repo / "tools").mkdir()
    _git("mv", "abicheck/foo.py", "tools/foo.py", cwd=repo)
    _git("commit", "-q", "-am", "move out of abicheck", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)

    changed = gate.changed_files(base, head)
    assert ("D", "abicheck/foo.py") in changed
    assert ("A", "tools/foo.py") in changed
    # With default rename detection this move would show up only as the new
    # path and the gate would miss it entirely — the whole point of the fix.
    assert gate.needs_changelog_entry(changed) is True


def test_changed_files_includes_pure_deletion(repo: Path) -> None:
    (repo / "abicheck").mkdir()
    (repo / "abicheck" / "gone.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base = _git("rev-parse", "HEAD", cwd=repo)

    (repo / "abicheck" / "gone.py").unlink()
    _git("commit", "-q", "-am", "remove module", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)

    changed = gate.changed_files(base, head)
    assert ("D", "abicheck/gone.py") in changed
    assert gate.needs_changelog_entry(changed) is True


def test_changed_files_no_source_change(repo: Path) -> None:
    (repo / "abicheck").mkdir()
    (repo / "abicheck" / "mod.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base = _git("rev-parse", "HEAD", cwd=repo)

    (repo / "README.md").write_text("docs only\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "docs", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)

    changed = gate.changed_files(base, head)
    assert gate.needs_changelog_entry(changed) is False


# --- main() end-to-end --------------------------------------------------


def test_main_skip_label_bypasses_regardless_of_diff(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["check_changelog_fragment.py", "--skip-label"])
    assert gate.main() == 0
    assert "skip-changelog" in capsys.readouterr().out


def test_main_fails_when_source_changed_without_fragment(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "abicheck").mkdir()
    (repo / "abicheck" / "mod.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base = _git("rev-parse", "HEAD", cwd=repo)

    (repo / "abicheck" / "mod.py").write_text("x = 2\n")
    _git("commit", "-q", "-am", "change behavior", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)

    monkeypatch.setattr(
        "sys.argv", ["check_changelog_fragment.py", "--base", base, "--head", head]
    )
    assert gate.main() == 1
    assert "no changelog" in capsys.readouterr().err


def test_main_passes_when_fragment_present(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "abicheck").mkdir()
    (repo / "abicheck" / "mod.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    base = _git("rev-parse", "HEAD", cwd=repo)

    (repo / "abicheck" / "mod.py").write_text("x = 2\n")
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / "20260101_me.md").write_text("### Changed\n\n- Bumped x.\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "change behavior + fragment", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)

    monkeypatch.setattr(
        "sys.argv", ["check_changelog_fragment.py", "--base", base, "--head", head]
    )
    assert gate.main() == 0
    assert "OK" in capsys.readouterr().out
