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

"""``scripts/test_refactor_churn.py``: which tests refactor commits had to edit.

Each test builds a throwaway git repository with scripted commits and asserts
exact counts. The oracle is the history we wrote, not the script's own
helpers: every expected number below is derived by hand from the commit list.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import test_refactor_churn as churn  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

MOD = "tests/test_mod.py"
OTHER = "tests/test_other.py"
RENAMED = "tests/test_renamed.py"
OLD = "tests/test_old.py"

MOD_V1 = textwrap.dedent(
    """\
    import re


    def helper():
        return 1


    def test_add():
        value = helper()
        assert value == 1


    def test_sub():
        value = helper() - 0
        assert value == 1


    class TestGroup:
        def test_nested(self):
            total = 1 + 2
            assert total == 3
    """
)
OTHER_V1 = "def test_x():\n    x = 5\n    assert x == 5\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")


def _commit(
    repo: Path,
    subject: str,
    *,
    files: dict[str, str] | None = None,
    renames: tuple[tuple[str, str], ...] = (),
    date: str | None = None,
) -> None:
    for rel, text in (files or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not write_text: the repo must hold "\n" on every platform.
        path.write_bytes(text.encode("utf-8"))
    for old, new in renames:
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        os.replace(repo / old, repo / new)
    _git(repo, "add", "-A")
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Churn Test",
            "-c",
            "user.email=churn@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            subject,
        ],
        check=True,
        capture_output=True,
        env=env,
    )


def _edit(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"expected exactly one match for {old!r}"
    return text.replace(old, new)


def _build_churn_repo(repo: Path) -> None:
    """Ten commits, oldest first. Every expected count below comes from this list."""
    _init(repo)
    mod = MOD_V1
    other = OTHER_V1
    # 1. feat: creates both files. Creating a test is not an edit.
    _commit(repo, "feat: add mirror tests", files={MOD: mod, OTHER: other})
    # 2. refactor: setup-only edit to test_add; helper edit is not a test.
    mod = _edit(mod, "    return 1\n", "    return 1  # one\n")
    mod = _edit(mod, "    value = helper()\n", "    value = helper() + 0\n")
    _commit(repo, "refactor: extract constant", files={MOD: mod})
    # 3. refactor(core): assertion edit to test_add.
    mod = _edit(
        mod,
        "    assert value == 1\n\n\ndef test_sub",
        "    assert value == 2\n\n\ndef test_sub",
    )
    _commit(repo, "refactor(core): tighten expected value", files={MOD: mod})
    # 4. fix: assertion edit to test_sub (context only, not refactor).
    mod = _edit(
        mod, "    assert value == 1\n\n\nclass", "    assert value == 0\n\n\nclass"
    )
    _commit(repo, "fix: handle zero divisor", files={MOD: mod})
    # 5. refactor: pure rename. No hunks, so nothing is counted.
    _commit(
        repo, "refactor: move test_x to renamed module", renames=((OTHER, RENAMED),)
    )
    # 6. refactor: import-only edits, one inside test_sub. Neutral, not counted.
    mod = _edit(mod, "import re\n", "import os\nimport re\n")
    mod = _edit(mod, "def test_sub():\n", "def test_sub():\n    import math\n")
    _commit(repo, "refactor: sort test imports", files={MOD: mod})
    # 7. refactor: assertion edit to test_x, reached through the rename.
    other = _edit(other, "    assert x == 5\n", "    assert x == 6\n")
    _commit(repo, "refactor: change expected in renamed test", files={RENAMED: other})
    # 8. style: setup-only edit to the nested test.
    mod = _edit(mod, "        total = 1 + 2\n", "        total = sum([1, 2])\n")
    _commit(repo, "style: reflow nested test setup", files={MOD: mod})
    # 9. test: assertion edit to test_add. Not a refactor, but it is an edit.
    mod = _edit(
        mod,
        "    assert value == 2\n\n\ndef test_sub",
        '    assert value == 2, "tweaked"\n\n\ndef test_sub',
    )
    _commit(repo, "test: tweak test_add expectation", files={MOD: mod})
    # 10. refactor: removes test_sub, including its assertion.
    mod = _edit(
        mod,
        "def test_sub():\n    import math\n    value = helper() - 0\n    assert value == 0\n\n\n",
        "",
    )
    _commit(repo, "refactor: drop test_sub", files={MOD: mod})


@pytest.fixture(scope="module")
def churn_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("churn") / "repo"
    _build_churn_repo(repo)
    return repo


def _test_ids(entries: list[dict[str, object]]) -> list[str]:
    return [str(e["test"]) for e in entries]


def _counts(entry: dict[str, object]) -> tuple[int, int, int, int]:
    return (
        int(entry["refactor_edits"]),  # type: ignore[call-overload]
        int(entry["refactor_assertion_edits"]),  # type: ignore[call-overload]
        int(entry["featfix_edits"]),  # type: ignore[call-overload]
        int(entry["other_edits"]),  # type: ignore[call-overload]
    )


def _commit_flags(entry: dict[str, object]) -> list[tuple[str, bool, bool]]:
    return [
        (c["kind"], c["assertion"], c["setup"])  # type: ignore[index]
        for c in entry["commits"]  # type: ignore[attr-defined]
    ]


def test_ranks_live_tests_by_refactor_assertion_edits(churn_repo: Path) -> None:
    data = churn.to_json(churn.analyse(churn_repo, "1 year ago"))

    assert data["commits_scanned"] == 10
    assert data["commits_touching_tests"] == 10
    assert data["refactor_commits"] == 7
    assert data["unparsed_files"] == 0
    assert _test_ids(data["tests"]) == [  # type: ignore[arg-type]
        f"{MOD}::test_add",
        f"{RENAMED}::test_x",
        f"{MOD}::TestGroup::test_nested",
    ]
    add, renamed, nested = data["tests"]  # type: ignore[misc]
    # test_add: setup (2), assertion in a refactor (3), assertion in a test: commit.
    assert _counts(add) == (2, 1, 0, 1)
    assert _commit_flags(add) == [
        ("refactor", False, True),
        ("refactor", True, False),
        ("other", True, False),
    ]
    # test_x is found through the rename; the pure rename counted nothing.
    assert _counts(renamed) == (1, 1, 0, 0)
    assert _commit_flags(renamed) == [("refactor", True, False)]
    assert _counts(nested) == (1, 0, 0, 0)


def test_removed_test_is_reported_separately(churn_repo: Path) -> None:
    data = churn.to_json(churn.analyse(churn_repo, "1 year ago"))

    assert _test_ids(data["gone"]) == [f"{MOD}::test_sub"]  # type: ignore[arg-type]
    sub = data["gone"][0]  # type: ignore[index]
    assert sub["gone"] is True
    # The feat/fix assertion (context) and the refactor deletion, which removed
    # the assertion. Its import-only hunk in commit 6 counted nothing.
    assert _counts(sub) == (1, 1, 1, 0)
    assert _commit_flags(sub) == [("featfix", True, False), ("refactor", True, True)]


def test_history_is_complete_for_a_full_clone(churn_repo: Path) -> None:
    data = churn.to_json(churn.analyse(churn_repo, "1 year ago"))

    assert data["history"] == {"shallow": False, "unshallowed": False, "note": None}


def test_markdown_ranks_then_lists_gone_tests_separately(churn_repo: Path) -> None:
    text = churn.render_markdown(churn.analyse(churn_repo, "1 year ago"), top=20)

    assert "| 1 | `tests/test_mod.py::test_add` | 2 | 1 | 0 |" in text
    assert "| 2 | `tests/test_renamed.py::test_x` | 1 | 1 | 0 |" in text
    assert "| 3 | `tests/test_mod.py::TestGroup::test_nested` | 1 | 0 | 0 |" in text
    heading = text.index("## Tests no longer at HEAD")
    assert text.index("test_sub") > heading
    assert text.index("test_add") < heading


def test_since_window_limits_the_history(tmp_path: Path) -> None:
    repo = tmp_path / "window"
    _init(repo)
    _commit(
        repo,
        "feat: add old test",
        files={OLD: "def test_a():\n    value = 1\n    assert value == 1\n"},
        date="2024-01-01T00:00:00+0000",
    )
    _commit(
        repo,
        "refactor: tighten old expectation",
        files={OLD: "def test_a():\n    value = 1\n    assert value == 2\n"},
        date="2024-06-01T00:00:00+0000",
    )
    _commit(
        repo,
        "refactor: reflow old setup",
        files={OLD: "def test_a():\n    value = 1 + 0\n    assert value == 2\n"},
    )

    recent = churn.to_json(churn.analyse(repo, "1 year ago"))
    assert recent["commits_scanned"] == 1
    (only,) = recent["tests"]  # type: ignore[misc]
    assert only["test"] == f"{OLD}::test_a"
    assert _counts(only) == (1, 0, 0, 0)
    assert _commit_flags(only) == [("refactor", False, True)]

    wide = churn.to_json(churn.analyse(repo, "2023-01-01"))
    assert wide["commits_scanned"] == 3
    (both,) = wide["tests"]  # type: ignore[misc]
    assert _counts(both) == (2, 1, 0, 0)
    assert _commit_flags(both) == [
        ("refactor", True, False),
        ("refactor", False, True),
    ]


def test_shallow_clone_is_reported_and_then_fetched(
    churn_repo: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", churn_repo.as_uri(), str(clone)],
        check=True,
        capture_output=True,
    )

    partial = churn.to_json(churn.analyse(clone, "1 year ago", unshallow=False))
    assert partial["history"]["shallow"] is True  # type: ignore[index]
    assert partial["history"]["unshallowed"] is False  # type: ignore[index]
    assert "--no-unshallow" in partial["history"]["note"]  # type: ignore[index]
    assert partial["commits_scanned"] == 1

    full = churn.to_json(churn.analyse(clone, "1 year ago"))
    assert full["history"]["unshallowed"] is True  # type: ignore[index]
    assert full["commits_scanned"] == 10
    assert _test_ids(full["tests"]) == _test_ids(  # type: ignore[arg-type]
        churn.to_json(churn.analyse(churn_repo, "1 year ago"))["tests"]  # type: ignore[index]
    )


def test_missing_repository_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert churn.main(["--repo", str(tmp_path / "missing")]) == 1
    assert "error:" in capsys.readouterr().err


def test_main_writes_json_and_prints_markdown(
    churn_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "churn.json"
    assert churn.main(["--repo", str(churn_repo), "--json", str(out)]) == 0
    assert "# Test refactor churn" in capsys.readouterr().out
    assert '"test": "tests/test_mod.py::test_add"' in out.read_text(encoding="utf-8")


SNIPPET = textwrap.dedent(
    """\
    import os


    def test_one():
        value = 1
        assert value == 1
        with pytest.raises(ValueError, match="x"):
            boom()
        expected = 2
        self.assertEqual(value, expected)
        value += 0
        # note
    """
)


@pytest.mark.parametrize(
    ("line", "label"),
    [
        (1, None),  # import
        (2, None),  # blank
        (4, "setup"),  # def line
        (5, "setup"),  # plain assignment
        (6, "assertion"),  # assert statement
        (7, "assertion"),  # pytest.raises header
        (8, "setup"),  # body of the raises block is setup
        (9, "assertion"),  # expected-value assignment
        (10, "assertion"),  # assert* method call
        (11, "setup"),
        (12, None),  # comment
    ],
)
def test_line_labels_separate_assertions_from_setup(
    line: int, label: str | None
) -> None:
    src = churn._parse_source(SNIPPET)
    assert src.label(line) == label


def test_innermost_test_is_chosen_for_an_enclosed_line() -> None:
    src = churn._parse_source(SNIPPET)
    assert src.test_at(8) == "test_one"
    assert src.test_at(1) is None


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("refactor: x", "refactor"),
        ("refactor(core): x", "refactor"),
        ("perf: x", "refactor"),
        ("style!: x", "refactor"),
        ("feat: x", "featfix"),
        ("fix(api): x", "featfix"),
        ("test: x", "other"),
        ("chore: x", "other"),
    ],
)
def test_commit_kind_follows_conventional_prefix(subject: str, kind: str) -> None:
    assert churn._kind(subject) == kind


def test_verbatim_move_to_another_file_is_not_an_edit(tmp_path: Path) -> None:
    repo = tmp_path / "split"
    _init(repo)
    moved = "def test_moved():\n    value = 1\n    assert value == 1\n"
    kept = "def test_kept():\n    value = 2\n    assert value == 2\n"
    _commit(
        repo,
        "feat: one module",
        files={"tests/test_split.py": kept + "\n\n" + moved},
    )
    # A refactor that splits the module: test_moved changes file, body unchanged.
    _commit(
        repo,
        "refactor: split test module",
        files={
            "tests/test_split.py": kept,
            "tests/test_split_part.py": moved,
        },
    )

    data = churn.to_json(churn.analyse(repo, "1 year ago"))

    assert data["tests"] == []
    assert data["gone"] == []
