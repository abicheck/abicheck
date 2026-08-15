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
import sys
from pathlib import Path

import pytest

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


class TestApplicability:
    @pytest.mark.parametrize(
        "subject",
        ["fix: thing", "fix(cli): thing", "fix!: thing", "perf: thing", "security: x"],
    )
    def test_fix_shaped_subjects_are_in_scope(self, subject: str) -> None:
        assert gate.is_bugfix([subject], None)

    @pytest.mark.parametrize(
        "subject", ["feat: thing", "docs: thing", "refactor: thing", "test: thing"]
    )
    def test_other_conventional_types_are_out_of_scope(self, subject: str) -> None:
        """The contract is about a bug that escaped, not about all change."""
        assert not gate.is_bugfix([subject], None)

    def test_the_pr_title_alone_can_bring_it_into_scope(self) -> None:
        assert gate.is_bugfix(["chore: wip"], "fix: the real subject")

    def test_no_signal_anywhere_is_out_of_scope(self) -> None:
        assert not gate.is_bugfix(["chore: wip"], "Update docs")


class TestStructuralRequirement:
    def test_shipped_code_without_a_test_is_detected(self) -> None:
        paths = ["abicheck/diff_types.py"]
        assert gate.touches_shipped_code(paths)
        assert not gate.touches_tests(paths)

    def test_a_test_change_satisfies_it(self) -> None:
        assert gate.touches_tests(["abicheck/diff_types.py", "tests/test_diff.py"])

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
            "foo/bar_test.py",
        ],
    )
    def test_real_test_paths_are_recognised(self, path: str) -> None:
        assert gate.is_test_path(path)

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
            gate, "changed_paths", lambda b, h: ["abicheck/x.py", "tests/test_x.py"]
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        empty = tmp_path / "body.md"
        empty.write_text("   \n\n", encoding="utf-8")
        rc = gate.main(["--base", "A", "--head", "B", "--body-file", str(empty)])
        assert rc == 1
        assert "PR description is empty" in capsys.readouterr().out

    def test_an_absent_body_file_is_structural_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Negative control: the local path must still work."""
        monkeypatch.setattr(
            gate, "changed_paths", lambda b, h: ["abicheck/x.py", "tests/test_x.py"]
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        rc = gate.main(["--base", "A", "--head", "B"])
        assert rc == 0
        assert "no --body-file given" in capsys.readouterr().out

    def test_a_populated_body_is_still_evaluated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gate,
            "changed_paths",
            lambda b, h: ["abicheck/cli_deps.py", "tests/test_x.py"],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
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
