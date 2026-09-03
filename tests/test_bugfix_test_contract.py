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

"""Mirrors `scripts/check_bugfix_test_contract.py`, the way
`test_fp_rate_gate.py` mirrors `check_fp_rate.py`.

Paired throughout: the gate must accept a properly-answered fix *and* reject
the specific omission each requirement exists for. A gate that only accepts
would be a box to tick.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_bugfix_test_contract.py"
)
_spec = importlib.util.spec_from_file_location("check_bugfix_test_contract", _PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_bugfix_test_contract"] = gate
_spec.loader.exec_module(gate)


COMPLETE_BODY = """\
## Bug-fix test contract

- Bug class: window size computed in KiB where zstd wants bytes
- Publicly observable failure: written .json.zst could not be read back
- Regression test fails on base: yes, test_zstd_round_trip_at_production_scale
- Negative control: gzip round-trip at the same scale is unaffected
- Public-surface test: through write_snapshot_bytes / read_snapshot_bytes
- Axes covered: zstd and gzip, both compression levels, Linux + macOS
- General invariant: every supported algorithm round-trips at production scale
"""


def _answers(body: str) -> dict[str, str]:
    return gate.parse_answers(body)


def _git_repo_with(tmp_path: Path, *, second_commit: dict[str, str]) -> Path:
    """A real repository: one base commit, then *second_commit*'s edits."""
    repo = tmp_path / "repo"
    (repo / "tests" / "golden").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n", encoding="utf-8"
    )
    (repo / "scripts" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "golden" / "report.md").write_text(
        "line one\nspurious line\nline three\n", encoding="utf-8"
    )
    (repo / "tests" / "data").mkdir(parents=True)
    (repo / "tests" / "data" / "policy.yml").write_text(
        "# tighten this later\noverrides:\n  func_removed: ok\n", encoding="utf-8"
    )

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _git("init", "-q", ".")
    _git("config", "user.email", "t@example.invalid")
    _git("config", "user.name", "t")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    for rel, content in second_commit.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content, encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "fix: thing")
    return repo


class TestAgainstRealGit:
    """The evidence rules, exercised through real `git diff` output.

    The fixtures elsewhere in this file are transcribed from real git, but a
    transcription cannot catch the flags being wrong — and the copy case was
    exactly that: the parsing was already correct, the diff simply never
    reported a copy (Codex review).
    """

    def test_an_unchanged_copy_of_a_test_does_not_satisfy_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                # Byte-identical copy of tests/test_a.py.
                "tests/test_b.py": (
                    "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n"
                ),
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == 1

    def test_an_added_symlink_to_an_existing_test_is_not_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ln -s test_a.py tests/test_regression.py` writes no assertion.

        Git renders a symlink's *target path* as the added line, so it looked
        like a test file with content — a duplicate of an existing test
        accepted as new evidence (Codex review).
        """
        repo = _git_repo_with(tmp_path, second_commit={"scripts/thing.py": "x = 2\n"})
        (repo / "tests" / "test_regression.py").symlink_to("test_a.py")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "fix: link"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~2", "--head", "HEAD"]) == 1

    def test_a_deletion_from_a_golden_snapshot_is_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fix that stops emitting spurious output proves itself by deleting
        the expected line — that snapshot fails against the base behaviour.

        An added-lines-only scan rejected such a PR as having no test change
        at all, which is the gate blocking legitimate work rather than
        catching an escape (Codex review).
        """
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/golden/report.md": "line one\nline three\n",
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == (
            gate.EXIT_STRUCTURAL_ONLY
        )

    def test_deleting_only_a_fixture_comment_is_not_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The removal path had no comment rule at all, so the added-line
        filter could be sidestepped by *deleting* a comment instead of adding
        one — a shipped-code fix then passed the structural gate with no
        assertion and no fixture value changed (Codex review)."""
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/data/policy.yml": "overrides:\n  func_removed: ok\n",
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == 1

    def test_deleting_a_real_fixture_value_is_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control for the pair: the rule must reject the comment,
        not the removal path — dropping an expected value is exactly how a
        fixture proves a fix."""
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/data/policy.yml": "# tighten this later\noverrides:\n",
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == (
            gate.EXIT_STRUCTURAL_ONLY
        )

    def test_a_deletion_from_a_python_test_is_not_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control, and the reason the rule is fixture-only: deleting
        lines from a `.py` test module is how a test gets weakened."""
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/test_a.py": "def test_a():\n    assert 1 == 1\n",
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == 1

    def test_a_docstring_only_edit_is_not_a_regression_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A docstring cannot fail, so editing one is not evidence.

        Through real git and a real file so the line numbers are real: a
        docstring's middle lines are arbitrary prose, indistinguishable from
        code by pattern alone (Codex review).
        """
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/test_a.py": (
                    "def test_a():\n"
                    '    """Now with an explanation.\n'
                    "\n"
                    "    Several lines of it, none executable.\n"
                    '    """\n'
                    "    assert 1 == 1\n    assert 2 == 2\n"
                ),
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == 1

    def test_a_new_assertion_beside_a_new_docstring_is_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: the docstring rule drops docstring *lines*, not
        the whole edit that happens to contain one."""
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/test_a.py": (
                    "def test_a():\n"
                    '    """Now with an explanation."""\n'
                    "    assert 1 == 1\n    assert 2 == 2\n    assert 3 == 3\n"
                ),
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == (
            gate.EXIT_STRUCTURAL_ONLY
        )

    def test_a_real_new_assertion_does_satisfy_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control, through the same path: a copy that actually adds
        an assertion is evidence."""
        repo = _git_repo_with(
            tmp_path,
            second_commit={
                "scripts/thing.py": "x = 2\n",
                "tests/test_b.py": (
                    "def test_a():\n    assert 1 == 1\n"
                    "    assert 2 == 2\n    assert 3 == 3\n"
                ),
            },
        )
        monkeypatch.setattr(gate, "ROOT", repo)
        assert gate.main(["--base", "HEAD~1", "--head", "HEAD"]) == (
            gate.EXIT_STRUCTURAL_ONLY
        )


class TestContentEvidence:
    """The status alone is not evidence — the added lines have to say something.

    `changed_files()` runs with `--no-renames`, so `git mv` arrives as one `D`
    and one `A`; a status-only predicate read that `A` as a regression test
    (Codex review). These pin the content half.
    """

    def test_a_pure_rename_is_not_a_regression_test(self) -> None:
        assert not gate.adds_or_modifies_a_test(
            [("D", "tests/test_old.py"), ("A", "tests/test_new.py")],
            _RENAME_DIFF,
        )

    def test_an_unchanged_copy_is_not_a_regression_test(self) -> None:
        """Duplicating a test is not writing one.

        Real `git diff -C --find-copies-harder` output for
        `cp tests/test_a.py tests/test_b.py`: no `+++` header at all, so the
        copy contributes no added lines. Without copy detection git reported
        it as a new file with every line added, and it passed (Codex review).
        """
        assert not gate.adds_or_modifies_a_test([("A", "tests/test_b.py")], _COPY_DIFF)

    def test_a_copy_carrying_a_real_edit_is_evidence(self) -> None:
        """Negative control: starting from an existing test and adding a real
        assertion is legitimate — copy detection must not reject that."""
        assert gate.adds_or_modifies_a_test(
            [("A", "tests/test_b.py")], _COPY_WITH_EDIT_DIFF
        )

    def test_a_rename_carrying_a_real_edit_is_evidence(self) -> None:
        """Negative control: moving a test *and* strengthening it still counts."""
        assert gate.adds_or_modifies_a_test(
            [("D", "tests/test_old.py"), ("A", "tests/test_new.py")],
            _RENAME_WITH_EDIT_DIFF,
        )

    @pytest.mark.parametrize(
        "added",
        [
            "",
            "   ",
            "# a note about the fix",
            "    # TODO: come back to this",
        ],
    )
    def test_blank_or_comment_only_python_lines_are_not_evidence(
        self, added: str
    ) -> None:
        diff = _content_diff("tests/test_x.py", added)
        assert not gate.adds_or_modifies_a_test([("M", "tests/test_x.py")], diff)

    @pytest.mark.parametrize(
        "path, added",
        [
            # `#` is a heading here, not a comment.
            ("tests/golden/report.md", "# Summary"),
            # JSON has no comment syntax at all, so this is content (or a
            # syntax error) — either way not something to discard.
            ("tests/data/expected.json", '{"//": 1}'),
            # C fixtures: `#include` is a directive, and `#` is not a comment
            # marker in C at all.
            ("tests/data/widget.h", "#include <stddef.h>"),
        ],
    )
    def test_a_hash_line_in_test_data_is_still_evidence(
        self, path: str, added: str
    ) -> None:
        """The comment rule is per format. `#` starts a heading in a golden
        Markdown snapshot and a preprocessor directive in a C fixture, so
        applying Python's comment syntax everywhere would discard real
        test-data changes."""
        assert gate.adds_or_modifies_a_test([("M", path)], _content_diff(path, added))

    @pytest.mark.parametrize(
        "path, added",
        [
            ("tests/data/policy.yml", "# tighten this later"),
            ("tests/data/policy.yaml", "  # tighten this later"),
            ("tests/data/config.toml", "# see AGENTS.md"),
            ("tests/data/run.sh", "# shellcheck disable=SC2086"),
            ("tests/data/widget.h", "// a note about the fix"),
        ],
    )
    def test_comment_only_fixture_lines_are_not_evidence(
        self, path: str, added: str
    ) -> None:
        """The other half of the same rule, and the reason it is per format
        rather than `.py`-only: a comment asserts nothing in *any* language,
        so a comment-only edit to a YAML or shell fixture is no more evidence
        of a regression test than the Python case above (Codex review)."""
        assert not gate.adds_or_modifies_a_test(
            [("M", path)], _content_diff(path, added)
        )

    def test_a_real_line_in_the_same_fixture_is_evidence(self) -> None:
        """Negative control for the pair above: the comment rule must reject
        the comment, not the file type."""
        diff = _content_diff("tests/data/policy.yml", "  overrides: {func_removed: ok}")
        assert gate.adds_or_modifies_a_test([("M", "tests/data/policy.yml")], diff)

    def test_the_content_diff_is_taken_with_rename_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two diffs want opposite things: the status listing splits a
        rename into `D`+`A` so a deleted test stays visible, the content scan
        pairs them so a pure rename adds nothing. Every fixture in this class
        assumes the paired shape, so the flag is pinned rather than assumed."""
        seen: dict[str, list[str]] = {}

        def _spy(args: list[str]) -> str:
            seen["args"] = args
            return ""

        monkeypatch.setattr(gate, "_git", _spy)
        gate.unified_diff("A", "B")
        assert "-M" in seen["args"]
        # Copies as well: `cp test_a.py test_b.py` is a rename's twin, and
        # without `-C --find-copies-harder` git reports it as a new file with
        # every line added (Codex review).
        assert "-C" in seen["args"]
        assert "--find-copies-harder" in seen["args"]
        assert "--no-renames" not in seen["args"]

    def test_added_lines_are_attributed_to_their_own_file(self) -> None:
        """A rename entry carries no `+++` header, so the previous file's
        attribution must not leak into it."""
        diff = _content_diff("abicheck/checker.py") + _RENAME_DIFF
        assert gate.added_content_paths(diff) == {"abicheck/checker.py"}

    def test_the_diff_filter_keeps_type_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replacing a shipped script with a symlink is a real behavioural
        change; excluding `T` made the path vanish from the diff entirely.

        Executed rather than grepped for in the source: asserting the literal
        flag text is the same shape this whole gate exists to reject (#705
        asserted workflow text, #758 had to execute it), and it would stay
        green if the argument list were built differently (CodeRabbit review).
        """
        seen: dict[str, list[str]] = {}

        def _spy(args: list[str]) -> str:
            seen["args"] = args
            return "T\tscripts/x.py\n"

        monkeypatch.setattr(gate, "_git", _spy)
        assert gate.changed_files("A", "B") == [("T", "scripts/x.py")]
        assert [a for a in seen["args"] if a.startswith("--diff-filter=")] == [
            "--diff-filter=ACDMT"
        ]

    def test_a_plugin_subtree_test_counts(self) -> None:
        assert gate.touches_tests(["contrib/abicheck-clang-plugin/tests/test_x.py"])

    @pytest.mark.parametrize(
        "path",
        [
            # Golden snapshots really are test data — they are compared
            # byte-for-byte, so changing one is a genuine test change.
            "tests/golden/func_removed.md",
            "tests/golden/report.txt",
            "tests/fixtures/snapshot.json",
            "tests/test_x.py",
            "tests/conftest.py",
            "tests/canonical_identity_contract.py",
            "contrib/abicheck-clang-plugin/tests/test_x.py",
        ],
    )
    def test_real_test_paths_are_recognised(self, path: str) -> None:
        assert gate.is_test_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            # `testpaths = ["tests"]` and the canonical command is `pytest
            # tests/`, so a test-shaped basename outside a `tests` tree is
            # never collected by any lane — crediting it accepted a file
            # nothing runs (Codex review).
            "foo/bar_test.py",
            "scripts/test_helper.py",
            # The one real file the fallback matched, and the clearest case:
            # an agent-eval fixture the suite deliberately does not execute.
            "agent-evals/tasks/add-change-kind-small/hidden_tests/test_x.py",
        ],
    )
    def test_a_test_basename_outside_a_test_tree_is_not_collected(
        self, path: str
    ) -> None:
        assert not gate.is_test_path(path)

    def test_the_uncollected_fixture_is_really_uncollected(self) -> None:
        """The rule above rests on a claim about this repository's own
        configuration, so read it rather than restate it: anything outside
        `testpaths` is not collected by the canonical command."""
        config = tomllib.loads(
            (Path(gate.__file__).resolve().parent.parent / "pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]

    @pytest.mark.parametrize(
        "path",
        [
            # A shipped script whose *name* merely contains `test_`. The
            # substring form matched these, so a fix editing only one of them
            # satisfied the "you must change a test" requirement with no test —
            # passing exactly the case the gate rejects (Codex review). The
            # second entry is this checker itself, which makes the hole
            # self-referential.
            "scripts/summarize_test_durations.py",
            "scripts/check_bugfix_test_contract.py",
            "abicheck/latest_test_helper.py",
            "abicheck/diff_types.py",
            # Prose whose *basename* starts with `test_`. Outside a test tree
            # the basename forms must be real Python modules, or a fix could
            # satisfy the structural requirement by editing a planning doc
            # (Codex review).
            "docs/test_plan.md",
            "examples/test_notes.txt",
            # Prose under tests/ is documentation, not an executable test — a
            # fix that changes shipped code and only edits tests/CLAUDE.md
            # must not satisfy the structural gate (Codex review).
            "tests/CLAUDE.md",
            "tests/scenarios/README.md",
            "tests/fixtures/g32/README.md",
        ],
    )
    def test_shipped_files_whose_names_contain_test_are_not_tests(
        self, path: str
    ) -> None:
        assert not gate.is_test_path(path)

    def test_a_fix_to_the_checker_itself_still_requires_a_test(self) -> None:
        """End-to-end form of the hole: shipped code, no test, must not pass."""
        paths = ["scripts/check_bugfix_test_contract.py"]
        assert gate.touches_shipped_code(paths)
        assert not gate.touches_tests(paths)

    def test_docs_only_change_is_not_shipped_code(self) -> None:
        assert not gate.touches_shipped_code(["docs/x.md", "README.md"])

    @pytest.mark.parametrize(
        "path", ["action/run.sh", "action/validate-inputs.sh", "action/install-deps.sh"]
    )
    def test_the_actions_shell_layer_counts_as_shipped_code(self, path: str) -> None:
        """`action/` is shell, not Python.

        Requiring `.py` everywhere let a fix to the composite Action's runtime
        behaviour ship with no test at all and still pass the objective half of
        this gate (Codex review). That layer has real coverage — the
        `test_action_run_sh_*` suites execute these scripts — so there is
        always a test to add.
        """
        assert gate.touches_shipped_code([path])

    def test_shell_scripts_outside_the_action_tree_are_not_shipped_code(self) -> None:
        """Per-prefix, not a blanket `.sh` rule: a helper script elsewhere is
        not the Action's runtime."""
        assert not gate.touches_shipped_code(["abicheck/x.sh", "tools/helper.sh"])

    def test_non_code_files_in_the_action_tree_are_not_shipped_code(self) -> None:
        assert not gate.touches_shipped_code(["action/README.md", "action/AGENTS.md"])

    @pytest.mark.parametrize(
        "path",
        [
            "abicheck/policies/security.yaml",
            "abicheck/policies/glibc_symbol_versioned.yaml",
            "abicheck/schemas/compare_report.schema.json",
        ],
    )
    def test_packaged_runtime_data_is_shipped_code(self, path: str) -> None:
        """The policy profiles are resolved by bare name through
        `policy_file.builtin_policy_path()` and the schemas are the published
        report contracts — a correctness fix to either changes shipped
        behaviour with no `.py` touched (Codex review)."""
        assert gate.touches_shipped_code([path])

    def test_docs_inside_the_package_are_not_shipped_code(self) -> None:
        """The four CLAUDE.md files under abicheck/ are documentation."""
        assert not gate.touches_shipped_code(
            ["abicheck/CLAUDE.md", "abicheck/buildsource/CLAUDE.md"]
        )

    def test_the_root_action_manifest_is_shipped_code(self) -> None:
        """`action.yml` *is* the published composite Action — it declares the
        inputs and the executable steps — and has dedicated coverage in
        `test_action_reference.py` / `test_action_run_contract.py`, so a fix to
        it can carry a test (Codex review)."""
        assert gate.touches_shipped_code(["action.yml"])

    def test_an_unrelated_root_yaml_is_not_shipped_code(self) -> None:
        """Named files, not a blanket root-YAML rule."""
        assert not gate.touches_shipped_code(["mkdocs.yml", "codecov.yml"])


class TestAnswerParsing:
    def test_parses_plain_bullets(self) -> None:
        assert _answers("- Negative control: gzip is unaffected")[
            "negative-control"
        ] == ("gzip is unaffected")

    @pytest.mark.parametrize(
        "line",
        [
            "- **Negative control**: gzip",
            "* Negative control: gzip",
            "Negative control: gzip",
            "- [x] Negative control: gzip",
        ],
    )
    def test_tolerates_common_markdown_shapes(self, line: str) -> None:
        assert _answers(line).get("negative-control") == "gzip"

    @pytest.mark.parametrize(
        "value",
        ["", "-", "n/a", "N/A", "TBD", "todo", "...", "<describe>", "yes/no", "___"],
    )
    def test_placeholders_do_not_count_as_answers(self, value: str) -> None:
        """The template ships these; leaving them in must not satisfy the gate."""
        body = f"- Negative control: {value}\n"
        missing = {r.key for r in gate.missing_requirements(body, ["abicheck/x.py"])}
        assert "negative-control" in missing


class TestAlwaysRequiredAnswers:
    def test_a_complete_body_satisfies_the_core_requirements(self) -> None:
        missing = gate.missing_requirements(COMPLETE_BODY, ["abicheck/checker.py"])
        assert missing == []

    @pytest.mark.parametrize(
        "prompt, key",
        [
            ("Bug class", "bug-class"),
            ("Publicly observable failure", "publicly-observable-failure"),
            ("Regression test fails on base", "regression-test-fails-on-base"),
            ("Negative control", "negative-control"),
            ("Public-surface test", "public-surface"),
            ("Axes covered", "axes-covered"),
            ("General invariant", "general-invariant"),
        ],
    )
    def test_each_core_answer_is_individually_required(
        self, prompt: str, key: str
    ) -> None:
        body = "\n".join(
            line
            for line in COMPLETE_BODY.splitlines()
            if not line.startswith(f"- {prompt}:")
        )
        missing = {
            r.key for r in gate.missing_requirements(body, ["abicheck/checker.py"])
        }
        assert key in missing


def _content_diff(path: str, added: str = "    assert f(2) == 4") -> str:
    """One file with one added line, in `git diff --unified=0 -M` shape."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,0 +2 @@\n"
        f"+{added}\n"
    )


def _deletion_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-    assert f(2) == 4\n"
    )


#: Real `git diff -M` output shape for `git mv` with no edit: no `+++` header
#: at all, so nothing is added anywhere.
_RENAME_DIFF = (
    "diff --git a/tests/test_old.py b/tests/test_new.py\n"
    "similarity index 100%\n"
    "rename from tests/test_old.py\n"
    "rename to tests/test_new.py\n"
)

#: Real `git diff -C --find-copies-harder` output for an unchanged copy.
_COPY_DIFF = (
    "diff --git a/tests/test_a.py b/tests/test_b.py\n"
    "similarity index 100%\n"
    "copy from tests/test_a.py\n"
    "copy to tests/test_b.py\n"
)

_COPY_WITH_EDIT_DIFF = (
    "diff --git a/tests/test_a.py b/tests/test_b.py\n"
    "similarity index 87%\n"
    "copy from tests/test_a.py\n"
    "copy to tests/test_b.py\n"
    "--- a/tests/test_a.py\n"
    "+++ b/tests/test_b.py\n"
    "@@ -3,0 +4 @@\n"
    "+    assert f(0) == 0\n"
)

_RENAME_WITH_EDIT_DIFF = (
    "diff --git a/tests/test_old.py b/tests/test_new.py\n"
    "similarity index 92%\n"
    "rename from tests/test_old.py\n"
    "rename to tests/test_new.py\n"
    "--- a/tests/test_old.py\n"
    "+++ b/tests/test_new.py\n"
    "@@ -3,0 +4 @@\n"
    "+    assert f(-1) == 1\n"
)


class TestGapAdmission:
    """The template advertised a row nothing could ask for (Codex review).

    AGENTS.md's rule is "generalize, or record the gap". An author who says
    the fix does not close the class has followed half of that; the other half
    is naming what is still unsupported.
    """

    @pytest.mark.parametrize(
        "invariant",
        [
            "None — documented as a known gap",
            "No general invariant; instance only",
            "Could not generalise this one",
        ],
    )
    def test_admitting_a_gap_asks_what_is_unsupported(self, invariant: str) -> None:
        body = COMPLETE_BODY.replace(
            "- General invariant: every supported algorithm round-trips at production scale",
            f"- General invariant: {invariant}",
        )
        assert f"- General invariant: {invariant}" in body, "fixture must be edited"
        missing = {r.key for r in gate.missing_requirements(body, ["abicheck/x.py"])}
        assert "known-unsupported-cases" in missing

    def test_a_real_invariant_does_not(self) -> None:
        """Negative control: the row is not boilerplate on every fix."""
        missing = {
            r.key for r in gate.missing_requirements(COMPLETE_BODY, ["abicheck/x.py"])
        }
        assert "known-unsupported-cases" not in missing

    @pytest.mark.parametrize(
        "invariant",
        [
            # The reported false positive: an ordinary universal claim that
            # happens to open with the same word a refusal does. Read as a
            # substring, "none" turned a fully general fix into a required
            # "Known unsupported cases" row its author had nothing true to
            # write in (Codex review).
            "None of the malformed archive members escape the root",
            "Every reader normalises the path, so none of the callers can bypass it",
            # "no general" as a substring: this says the opposite of the
            # phrase it used to match.
            "No generalization was needed — the helper already covers every backend",
            "Nothing that reaches the parser can escape the sandbox",
        ],
    )
    def test_a_universal_invariant_is_not_an_admission(self, invariant: str) -> None:
        """Negative control against the vocabulary itself, not just the gate:
        these are the shapes a real, general invariant takes, and every one of
        them contains a gap-vocabulary word somewhere inside it."""
        body = COMPLETE_BODY.replace(
            "- General invariant: every supported algorithm round-trips at production scale",
            f"- General invariant: {invariant}",
        )
        assert f"- General invariant: {invariant}" in body, "fixture must be edited"
        missing = {r.key for r in gate.missing_requirements(body, ["abicheck/x.py"])}
        assert "known-unsupported-cases" not in missing

    @pytest.mark.parametrize(
        "answer, admits",
        [
            # Anchored: only the *complete* answer means the bare refusal.
            ("None.", True),
            ("**none**", True),
            ("n/a", True),
            ("None of the callers can reach it", False),
            # Whole-phrase matches, anywhere in the sentence.
            ("The fix is not generalisable beyond ELF", True),
            ("There is no general invariant here", True),
            ("Known gap: Mach-O export tries are untouched", True),
            ("no generalization was needed", False),
        ],
    )
    def test_the_vocabulary_is_anchored_or_whole_phrase(
        self, answer: str, admits: bool
    ) -> None:
        assert gate._admits_a_gap(answer) is admits

    def test_answering_it_satisfies_the_gate(self) -> None:
        body = (
            COMPLETE_BODY.replace(
                "- General invariant: every supported algorithm round-trips at production scale",
                "- General invariant: none — documented as a known gap",
            )
            + "\n- Known unsupported cases: multi-document SYCL output\n"
        )
        missing = {r.key for r in gate.missing_requirements(body, ["abicheck/x.py"])}
        assert "known-unsupported-cases" not in missing


class TestConditionalRequirements:
    """Each conditional exists because a real escape went through that surface."""

    @pytest.mark.parametrize(
        "path, key",
        [
            ("abicheck/snapshot_io.py", "real-dependency-test"),
            ("abicheck/serialization.py", "real-dependency-test"),
            ("action/entrypoint.sh", "malicious-fixture"),
            (".github/workflows/publish-baseline.yml", "malicious-fixture"),
            ("abicheck/finding_identity.py", "merge-pair"),
            ("abicheck/diff_filtering.py", "fp-fn-pair"),
            ("abicheck/checker_policy.py", "verdict-gate-exit"),
            ("abicheck/severity.py", "verdict-gate-exit"),
            # The root manifest is the published Action; it shares the
            # action/ trust boundary and must ask the same question.
            ("action.yml", "malicious-fixture"),
        ],
    )
    def test_touching_the_surface_asks_its_question(self, path: str, key: str) -> None:
        missing = {r.key for r in gate.missing_requirements(COMPLETE_BODY, [path])}
        assert key in missing, f"{path} should trigger {key}"

    def test_a_test_only_change_does_not_trigger_conditionals(self) -> None:
        """A test file named after its subject must not fire the conditional —
        a question that fires on everything is boilerplate, not a signal."""
        missing = gate.missing_requirements(
            COMPLETE_BODY,
            ["tests/test_finding_identity.py", "tests/canonical_identity_contract.py"],
        )
        assert missing == []

    @pytest.mark.parametrize(
        "path, key",
        [
            # `actions/` does not contain the substring `action/` — an `s`
            # follows `action` — so the plural tree was recognised as shipped
            # code without ever being asked for hostile-input evidence
            # (Codex review).
            ("actions/check-target/run.sh", "malicious-fixture"),
            ("actions/resolve-baseline/resolve_baseline.py", "malicious-fixture"),
        ],
    )
    def test_the_plural_actions_tree_shares_the_trust_boundary(
        self, path: str, key: str
    ) -> None:
        requirement = next(r for r in gate.REQUIREMENTS if r.key == key)
        assert requirement.applies_to([path])

    @pytest.mark.parametrize(
        "path",
        [
            ".github/actions/setup-castxml/action.yml",
            ".github/actions/cache-ast-dumps/action.yml",
        ],
    )
    def test_local_composite_actions_share_the_trust_boundary(self, path: str) -> None:
        """A local composite action has no `on:` trigger, so neither the
        shipped-code map nor the `.github/workflows/` prefix matched it — yet
        its steps run inside every caller with that caller's permissions, and
        `setup-castxml` alone is used by six workflows. Its blast radius is
        wider than a single workflow file's, not narrower (Codex review)."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == "malicious-fixture")
        assert requirement.applies_to([path])

    def test_pyproject_is_shipped_runtime_config(self) -> None:
        """Build config, but three of its sections are installed behaviour:
        `[project].dependencies`, `[project.scripts]` and
        `[tool.setuptools.package-data]`. A fix to any of them changes what
        users run with no `.py` touched, and the structural gate let it
        through (Codex review)."""
        assert gate.touches_shipped_code(["pyproject.toml"])

    def test_an_unterminated_comment_hides_answers_from_the_parser_too(
        self,
    ) -> None:
        """GitHub hides everything after an unclosed `<!--` from the rendered
        description, so answers behind one are invisible to reviewers while a
        closing-marker-only strip still read them (CodeRabbit review)."""
        body = (
            "Bug class: real\n<!-- oops, no closing marker\nNegative control: hidden\n"
        )
        answers = gate.parse_answers(body)
        assert "bug-class" in answers
        assert "negative-control" not in answers

    def test_a_closed_comment_still_strips_exactly_its_own_region(self) -> None:
        """Negative control: the unterminated case must not swallow the rest
        of a body that closes its comment properly."""
        body = "Bug class: real\n<!-- hidden -->\nNegative control: visible\n"
        answers = gate.parse_answers(body)
        assert "bug-class" in answers
        assert "negative-control" in answers

    def test_other_root_toml_is_not_shipped(self) -> None:
        """Negative control: the rule names one file, it is not "any TOML".

        The first version of this asserted two `.yml` paths, so it tested no
        TOML at all and duplicated the YAML control (CodeRabbit review).
        """
        assert not gate.touches_shipped_code(["ruff.toml", "pixi.toml"])

    def test_a_packaged_policy_named_security_is_not_a_trust_boundary(self) -> None:
        """`abicheck/policies/security.yaml` is a packaged runtime policy
        profile, not an Action or a workflow. A bare `security` substring
        trigger demanded hostile-input evidence for it — and `N/A` cannot
        satisfy that, since the answer parser rejects placeholders (Codex
        review)."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == "malicious-fixture")
        assert not requirement.applies_to(["abicheck/policies/security.yaml"])

    def test_the_security_workflow_itself_still_asks_the_question(self) -> None:
        """Negative control: narrowing must not switch the real boundary off —
        it is covered by the prefix, not by the word."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == "malicious-fixture")
        assert requirement.applies_to([".github/workflows/security.yml"])

    def test_prose_under_a_trust_boundary_prefix_asks_nothing(self) -> None:
        """Negative control: the prefixes are *directory* prefixes, so the
        docs exclusion has to apply on the trust-boundary branch too, or a
        composite action's README would be asked for hostile-input evidence."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == "malicious-fixture")
        assert not requirement.applies_to([".github/actions/setup-castxml/README.md"])

    @pytest.mark.parametrize(
        "path, key",
        [
            ("docs/reference/snapshot_io.md", "real-dependency-test"),
            ("docs/guide/suppression.md", "fp-fn-pair"),
            ("docs/contribute/finding_identity.md", "merge-pair"),
        ],
    )
    def test_documentation_does_not_trigger_runtime_questions(
        self, path: str, key: str
    ) -> None:
        """A docs-only fix was being asked for real-dependency or FP/FN
        evidence because the path merely contained the module's name. A
        conditional that fires on prose is boilerplate, not a signal
        (Codex review)."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == key)
        assert not requirement.applies_to([path])

    @pytest.mark.parametrize(
        "path, key",
        [
            ("abicheck/snapshot_io.py", "real-dependency-test"),
            ("abicheck/suppression.py", "fp-fn-pair"),
            (".github/workflows/publish.yml", "malicious-fixture"),
        ],
    )
    def test_the_real_runtime_surfaces_still_trigger(self, path: str, key: str) -> None:
        """Negative control: narrowing must not switch the conditionals off.

        `.github/workflows/` is not shipped code by the suffix map, but it is
        exactly the surface the malicious-fixture question exists for, so it is
        an explicit trust-boundary exception."""
        requirement = next(r for r in gate.REQUIREMENTS if r.key == key)
        assert requirement.applies_to([path])

    def test_an_unrelated_diff_does_not_ask_conditional_questions(self) -> None:
        """Conditionals must not degrade into always-on boilerplate."""
        missing = gate.missing_requirements(COMPLETE_BODY, ["abicheck/cli_deps.py"])
        assert missing == []

    def test_answering_the_conditional_satisfies_it(self) -> None:
        body = COMPLETE_BODY + "- Real-dependency test: real zstd at 8 MiB\n"
        missing = gate.missing_requirements(body, ["abicheck/snapshot_io.py"])
        assert missing == []

    def test_one_diff_can_trigger_several_conditionals(self) -> None:
        missing = {
            r.key
            for r in gate.missing_requirements(
                COMPLETE_BODY, ["abicheck/diff_filtering.py"]
            )
        }
        # diff_filtering is both identity/dedup logic and filtering logic.
        assert {"merge-pair", "fp-fn-pair"} <= missing


class TestRequirementCatalogue:
    def test_every_requirement_explains_why_it_exists(self) -> None:
        """An unexplained gate becomes a box to tick."""
        for req in gate.REQUIREMENTS:
            assert req.why.strip(), f"{req.key} has no rationale"
            assert len(req.why) > 40, f"{req.key}'s rationale is too thin to act on"

    def test_requirement_keys_are_unique(self) -> None:
        keys = [r.key for r in gate.REQUIREMENTS]
        assert len(keys) == len(set(keys))

    def test_prompts_round_trip_through_the_answer_parser(self) -> None:
        """Every prompt must be answerable in the exact form the template ships."""
        body = "\n".join(f"- {r.prompt}: something real" for r in gate.REQUIREMENTS)
        assert gate.missing_requirements(body, ["abicheck/snapshot_io.py"]) == []

    def test_every_required_template_row_is_actually_enforced(self) -> None:
        """The reverse of the check below, and the direction that was missing.

        `Bug class` and `Publicly observable failure` were presented to
        contributors as required rows while no `Requirement` backed them, so a
        fix PR could leave both blank and still pass the declared contract
        (Codex review). Telling someone a field is mandatory and not checking
        it is worse than not asking.
        """
        template = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "PULL_REQUEST_TEMPLATE.md"
        ).read_text(encoding="utf-8")
        # Only the required block — the conditional rows ship inside an HTML
        # comment and are asked for by the checker, not by the template.
        required_block = template.split("<!-- Conditional")[0]
        listed = {
            gate._normalize(line.split(":", 1)[0].lstrip("- ").strip())
            for line in required_block.splitlines()
            if line.startswith("- ") and ":" in line
        }
        enforced = {gate._normalize(r.prompt) for r in gate.REQUIREMENTS}
        unenforced = listed - enforced
        assert not unenforced, (
            f"PR template lists {sorted(unenforced)} as required, but no "
            "Requirement enforces them"
        )

    def test_the_shipped_pr_template_answers_every_prompt_it_lists(self) -> None:
        """The template's own labels must match the checker's prompts, or a
        contributor fills in a form the gate then says is empty."""
        template = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "PULL_REQUEST_TEMPLATE.md"
        ).read_text(encoding="utf-8")
        for req in gate.REQUIREMENTS:
            assert f"- {req.prompt}:" in template, (
                f"PR template is missing a row for {req.prompt!r}"
            )


class TestEmptyPrBody:
    """An empty description must not be treated like a local run.

    CI always passes `--body-file`, so collapsing "flag absent" and "file
    empty" into one branch let a contributor clear the PR description and
    bypass every declared requirement while still passing (Codex review).
    """

    def test_an_empty_body_file_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/x.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        empty = tmp_path / "body.md"
        empty.write_text("   \n\n", encoding="utf-8")
        rc = gate.main(["--base", "A", "--head", "B", "--body-file", str(empty)])
        assert rc == 1
        assert "PR description is empty" in capsys.readouterr().out

    def test_an_unresolvable_local_base_is_partial_not_a_pass(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No diff means not even the structural half ran.

        Returning 0 there let `verify.py` record the step as passed and the pr
        profile as complete — the same over-claim as a missing PR body, from
        an input missing for a different reason (Codex review).
        """

        def _boom(base: str, head: str) -> list[tuple[str, str]]:
            raise subprocess.CalledProcessError(128, ["git", "diff"])

        monkeypatch.setattr(gate, "changed_files", _boom)
        # A distinct code from the missing-body case: nothing ran here, so
        # reporting that reason would send a reader to the wrong fix.
        assert gate.main([]) == gate.EXIT_NO_DIFF
        assert "nothing was checked" in capsys.readouterr().out

    def test_an_unresolvable_ref_named_by_ci_is_still_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: when CI names the refs, an unresolvable one is a
        real failure, not a partial run — a gate that cannot run in CI has
        nothing to be partial about."""

        def _boom(base: str, head: str) -> list[tuple[str, str]]:
            raise subprocess.CalledProcessError(128, ["git", "diff"])

        monkeypatch.setattr(gate, "changed_files", _boom)
        assert gate.main(["--base", "A", "--head", "B"]) == 1

    def test_a_structural_failure_outranks_the_partial_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A real finding must not be laundered into "partial".

        Without a body file the run is partial, but a fix that changes shipped
        code with no test is a genuine failure the structural half found on
        its own — it exits 1, which no caller maps to a skip.
        """
        monkeypatch.setattr(
            gate, "changed_files", lambda b, h: [("M", "abicheck/x.py")]
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(gate, "unified_diff", lambda b, h: "")
        assert gate.main(["--base", "A", "--head", "B"]) == 1
        assert "changes shipped code but no test" in capsys.readouterr().out

    def test_an_absent_body_file_is_structural_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Negative control: the local path must still work."""
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/x.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        rc = gate.main(["--base", "A", "--head", "B"])
        # Exit 2, not 0: the structural half ran and passed, the declared half
        # could not run at all. `verify.py` maps this to a skip so a local
        # `--profile pr` cannot claim parity over half a gate (Codex review).
        assert rc == gate.EXIT_STRUCTURAL_ONLY
        out = capsys.readouterr().out
        assert "no --body-file given" in out
        assert "the declared half did not run" in out

    def test_a_populated_body_is_still_evaluated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/cli_deps.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        body = tmp_path / "body.md"
        body.write_text(COMPLETE_BODY, encoding="utf-8")
        assert gate.main(["--base", "A", "--head", "B", "--body-file", str(body)]) == 0


class TestSkipLabel:
    def test_skip_label_short_circuits(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = gate.main(["--base", "HEAD", "--head", "HEAD", "--skip-label"])
        assert rc == 0
        assert "skipped via the skip-test-contract label" in capsys.readouterr().out


class TestHiddenAnswersDoNotCount:
    """The declared half exists to put evidence in front of a reviewer.

    GitHub hides `<!-- ... -->` regions from the rendered description, so an
    answer written inside the template's own conditional block satisfied the
    parser while being invisible to every human who opened the PR (Codex
    review).
    """

    def test_an_answer_inside_a_comment_is_ignored(self) -> None:
        body = "- Bug class: x\n<!--\n- Real-dependency test: yes\n-->\n"
        assert "real-dependency-test" not in gate.parse_answers(body)
        missing = {
            r.key for r in gate.missing_requirements(body, ["abicheck/snapshot_io.py"])
        }
        assert "real-dependency-test" in missing

    def test_the_same_answer_outside_the_comment_counts(self) -> None:
        body = COMPLETE_BODY + "- Real-dependency test: real zstd at 8 MiB\n"
        assert gate.missing_requirements(body, ["abicheck/snapshot_io.py"]) == []

    def test_a_multiline_comment_region_is_stripped_whole(self) -> None:
        body = "<!--\n- Bug class: hidden\n- Negative control: hidden\n-->\n"
        assert gate.parse_answers(body) == {}

    def test_text_after_a_comment_still_parses(self) -> None:
        body = "<!-- hint -->\n- Bug class: visible\n"
        assert gate.parse_answers(body)["bug-class"] == "visible"


class TestPublishedActionsTree:
    """`actions/` (plural) holds five composite actions consumed directly as
    `uses: abicheck/abicheck/actions/...`; the predicate only knew `action/`."""

    @pytest.mark.parametrize(
        "path",
        [
            "actions/check-target/run.sh",
            "actions/resolve-baseline/resolve_baseline.py",
            "actions/baseline/action.yml",
            "actions/collect-facts/run.sh",
        ],
    )
    def test_published_actions_are_shipped_code(self, path: str) -> None:
        assert gate.touches_shipped_code([path])

    def test_prose_in_that_tree_is_not_shipped_code(self) -> None:
        assert not gate.touches_shipped_code(["actions/check-target/README.md"])
