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
