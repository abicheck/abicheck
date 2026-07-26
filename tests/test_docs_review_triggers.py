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

"""Unit tests for scripts/check_docs_review_triggers.py.

Mirrors the tests/test_*_gate.py pattern used for the other CI-gating
scripts/check_*.py scripts (see scripts/CLAUDE.md's inventory table), except
this one is advisory-only (never fails), so the tests assert *content*
(which pages/paths are reported), not exit codes.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_docs_review_triggers.py"
)
_spec = importlib.util.spec_from_file_location(
    "check_docs_review_triggers", _GATE_PATH
)
assert _spec and _spec.loader
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


# --- _matches ----------------------------------------------------------------


@pytest.mark.parametrize(
    "dep, changed, expected",
    [
        ("abicheck/model.py", "abicheck/model.py", True),
        ("abicheck/model.py", "abicheck/model2.py", False),
        ("abicheck/buildsource", "abicheck/buildsource/inline.py", True),
        ("abicheck/buildsource/", "abicheck/buildsource/inline.py", True),
        ("abicheck/buildsource", "abicheck/buildsource.py", False),
        ("abicheck/model.py", "abicheck/model.pyc", False),
    ],
)
def test_matches(dep: str, changed: str, expected: bool) -> None:
    assert rt._matches(dep, changed) is expected


# --- find_triggers -------------------------------------------------------


def test_find_triggers_flags_page_on_dependency_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text(
        "---\ndepends_on:\n  - abicheck/model.py\nlifecycle: active\n---\n\n# Page\n",
        encoding="utf-8",
    )
    triggers = rt.find_triggers(["abicheck/model.py", "tests/test_model.py"])
    assert len(triggers) == 1
    (key, hits), = triggers.items()
    assert key.endswith("page.md")
    assert hits == ["abicheck/model.py"]


def test_find_triggers_matches_directory_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text(
        "---\ndepends_on:\n  - abicheck/buildsource\n---\n\n# Page\n",
        encoding="utf-8",
    )
    triggers = rt.find_triggers(["abicheck/buildsource/inline.py"])
    assert len(triggers) == 1
    (key, hits), = triggers.items()
    assert key.endswith("page.md")
    assert hits == ["abicheck/buildsource/inline.py"]


def test_find_triggers_ignores_page_without_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text(
        "---\ndepends_on:\n  - abicheck/model.py\n---\n\n# Page\n",
        encoding="utf-8",
    )
    assert rt.find_triggers(["abicheck/unrelated.py"]) == {}


def test_find_triggers_ignores_page_without_depends_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text("# Page\n\nNo front matter.\n", encoding="utf-8")
    assert rt.find_triggers(["abicheck/model.py"]) == {}


def test_find_triggers_ignores_malformed_front_matter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text(
        "---\n[this is not a mapping]\n---\n\n# Page\n", encoding="utf-8"
    )
    assert rt.find_triggers(["abicheck/model.py"]) == {}


def test_find_triggers_ignores_non_list_depends_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rt, "DOCS", tmp_path)
    (tmp_path / "page.md").write_text(
        "---\ndepends_on: abicheck/model.py\n---\n\n# Page\n", encoding="utf-8"
    )
    assert rt.find_triggers(["abicheck/model.py"]) == {}


# --- render_summary --------------------------------------------------------


def test_render_summary_empty() -> None:
    assert "No docs page" in rt.render_summary({})


def test_render_summary_lists_pages_and_hits() -> None:
    summary = rt.render_summary({"docs/learn/verdicts.md": ["abicheck/checker_policy.py"]})
    assert "docs/learn/verdicts.md" in summary
    assert "abicheck/checker_policy.py" in summary


# --- changed_files -----------------------------------------------------------


def test_changed_files_parses_git_diff_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, cwd, capture_output, text, check):  # noqa: ANN001
        assert cmd[:3] == ["git", "diff", "--no-renames"]
        return subprocess.CompletedProcess(
            cmd, 0, stdout="abicheck/model.py\ndocs/learn/verdicts.md\n", stderr=""
        )

    monkeypatch.setattr(rt.subprocess, "run", fake_run)
    assert rt.changed_files("origin/main", "HEAD") == [
        "abicheck/model.py",
        "docs/learn/verdicts.md",
    ]


def test_main_never_fails_on_diff_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate is advisory-only: even a git-diff failure (e.g. no
    origin/main available locally) must exit 0, not break a caller's build."""

    def fake_run(cmd, cwd, capture_output, text, check):  # noqa: ANN001
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(rt.subprocess, "run", fake_run)
    monkeypatch.setattr(rt.sys, "argv", ["check_docs_review_triggers.py"])
    assert rt.main() == 0
    assert "skipping" in capsys.readouterr().out
