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

"""``cli_provenance``'s command bodies, in process.

The sibling of ``test_action_cli_integration_commands.py``, for the same
reason that module states and for a gap this one shipped with.

``tests/test_action_analysis_context.py`` drives these commands as real
**subprocesses**, which is the right shape for its own claim — that the
producer writes what the consumer reads, end to end through two public entry
points. What a subprocess cannot do is report coverage back, so every command
body here read as entirely unexercised, and `codecov/patch` said so.

That is not only a coverage number. The thing a subprocess round-trip cannot
catch is precisely what these commands *owe their caller*: the refusal exit
code a CI step branches on, and the exact key names and value shapes written
into the result documents a shell then reads back field by field. A command
that emitted ``records_tested_sha`` under a different key, or exited 1 instead
of :data:`EXIT_REFUSED`, would pass every end-to-end assertion in that module
— the round trip would still carry the block, and the shell's
``emit-fields`` would simply answer empty.

So the contract asserted here is the boundary one:

* a refusal exits :data:`EXIT_REFUSED` (3) — never Click's usage exit (2),
  and never 0, because a caller must be able to tell "this input is wrong"
  from "I invoked the tool wrong" from "it worked";
* a refusal still **writes its result document**, with a machine-readable
  ``code``, because the Action's shell reads that code to report why;
* every emitted key is named exactly as the shells read it back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

# Imported for its registration side effect, exactly as the sibling module
# does: `cli_base` owns the bare group and `cli` is what imports the modules
# that decorate commands onto it. Importing `cli_base` alone yields a group
# with no commands, so every invocation below would exit 2 and the
# refusal-exit assertions would be testing Click's usage path, not ours.
from abicheck.frontends.action import cli as _cli  # noqa: F401
from abicheck.frontends.action.cli_base import EXIT_REFUSED, action_cli

MERGE = "1" * 40
HEAD = "2" * 40
BASE = "3" * 40
UNRELATED = "4" * 40
TAG_OBJECT = "c" * 40


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _env(**overrides: str) -> dict[str, str]:
    """A complete ``ABICHECK_CTX_*`` environment, with overrides applied.

    Built from the command's own variable table rather than a hand-written
    list: a field added to the record without a variable here would otherwise
    be recorded as empty by every case below and nothing would notice.
    """
    from abicheck.frontends.action.cli_provenance import _CONTEXT_ENV

    env = dict.fromkeys(_CONTEXT_ENV.values(), "")
    env.update(
        {
            "ABICHECK_CTX_TESTED_SHA": MERGE,
            "ABICHECK_CTX_PR_HEAD_SHA": HEAD,
            "ABICHECK_CTX_PR_NUMBER": "42",
            "ABICHECK_CTX_REPOSITORY": "example/project",
            "ABICHECK_CTX_RUN_ID": "5001",
            "ABICHECK_CTX_RUN_ATTEMPT": "2",
            "ABICHECK_CTX_EVENT": "pull_request",
            "ABICHECK_CTX_PROFILE": "linux-x86_64",
        }
    )
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# record-analysis-context
# ---------------------------------------------------------------------------


class TestRecordAnalysisContext:
    def test_it_writes_every_declared_field(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        out = tmp_path / "ctx.json"
        result = runner.invoke(
            action_cli, ["record-analysis-context", str(out)], env=_env()
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["tested_sha"] == MERGE
        assert document["pr_head_sha"] == HEAD
        assert document["pr_number"] == "42"
        assert document["producer_run_attempt"] == "2"
        assert document["schema"] == "abicheck.analysis-context/1"

    def test_the_recorded_commit_is_echoed_when_there_is_one(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            action_cli,
            ["record-analysis-context", str(tmp_path / "ctx.json")],
            env=_env(),
        )
        assert MERGE in result.output

    def test_a_run_that_recorded_no_commit_still_writes_a_block(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # "Not recorded" is a state the producer is allowed to be in; it is
        # the consumer's policy, not this command's, that decides what to do
        # about it.
        out = tmp_path / "ctx.json"
        result = runner.invoke(
            action_cli,
            ["record-analysis-context", str(out)],
            env=_env(ABICHECK_CTX_TESTED_SHA=""),
        )
        assert result.exit_code == 0, result.output
        assert _read(out)["tested_sha"] == ""
        assert MERGE not in result.output

    @pytest.mark.parametrize(
        ("variable", "value"),
        [
            ("ABICHECK_CTX_TESTED_SHA", "not-a-sha"),
            ("ABICHECK_CTX_PR_HEAD_SHA", "abc123"),
            ("ABICHECK_CTX_PR_NUMBER", "twelve"),
            ("ABICHECK_CTX_REPOSITORY", "no-slash"),
            ("ABICHECK_CTX_RUN_ID", "-1"),
            ("ABICHECK_CTX_PROFILE", "has\na newline"),
        ],
    )
    def test_a_malformed_value_refuses_with_the_refusal_exit(
        self, runner: CliRunner, tmp_path: Path, variable: str, value: str
    ) -> None:
        out = tmp_path / "ctx.json"
        result = runner.invoke(
            action_cli,
            ["record-analysis-context", str(out)],
            env=_env(**{variable: value}),
        )
        # EXIT_REFUSED, not Click's 2 and not 1: the producer's own job needs
        # to report "a value you gave me is malformed", distinctly.
        assert result.exit_code == EXIT_REFUSED, result.output
        assert not out.exists(), "a refused record must not be written"

    def test_surrounding_whitespace_is_stripped_not_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # A workflow expression routinely yields a trailing newline; that is
        # not a malformed value, it is the same value.
        out = tmp_path / "ctx.json"
        result = runner.invoke(
            action_cli,
            ["record-analysis-context", str(out)],
            env=_env(ABICHECK_CTX_TESTED_SHA=f"  {MERGE}\n"),
        )
        assert result.exit_code == 0, result.output
        assert _read(out)["tested_sha"] == MERGE


# ---------------------------------------------------------------------------
# read-analysis-context
# ---------------------------------------------------------------------------


class TestReadAnalysisContext:
    def _report(self, path: Path, **context: str) -> Path:
        block = {"schema": "abicheck.analysis-context/1", **context}
        return _write(path, {"status": "pass", "analysis_context": block})

    def test_it_emits_the_keys_the_shell_reads_back(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        report = self._report(tmp_path / "aggregate.json", tested_sha=MERGE)
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli, ["read-analysis-context", str(report), "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        # These three key names are what `run.sh` reads with `emit-fields`;
        # a rename here is silently an empty value there.
        assert document["present"] is True
        assert document["records_tested_sha"] is True
        assert document["tested_sha"] == MERGE

    def test_a_block_recording_no_commit_is_present_but_not_records(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        report = self._report(tmp_path / "aggregate.json", pr_head_sha=HEAD)
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli, ["read-analysis-context", str(report), "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["present"] is True
        assert document["records_tested_sha"] is False

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("no block at all", {"status": "pass"}),
            ("not an object", ["a", "list"]),
            ("block is not an object", {"analysis_context": "a string"}),
        ],
    )
    def test_an_absent_block_refuses_by_default_and_says_why(
        self, runner: CliRunner, tmp_path: Path, label: str, payload: object
    ) -> None:
        report = _write(tmp_path / "aggregate.json", payload)
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli, ["read-analysis-context", str(report), "--out", str(out)]
        )
        assert result.exit_code == EXIT_REFUSED, f"{label}: {result.output}"
        # The document is written even on refusal: the shell reads `code`
        # off it to report the reason.
        document = _read(out)
        assert document["present"] is False
        assert document["code"]
        assert document["reason"]

    def test_a_missing_file_is_an_absent_context_not_a_crash(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli,
            [
                "read-analysis-context",
                str(tmp_path / "nothing.json"),
                "--out",
                str(out),
                "--no-require",
            ],
        )
        assert result.exit_code == 0, result.output
        assert _read(out)["code"] == "analysis-context-absent"

    def test_no_require_reports_the_absence_without_refusing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        report = _write(tmp_path / "aggregate.json", {"status": "pass"})
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli,
            ["read-analysis-context", str(report), "--out", str(out), "--no-require"],
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["present"] is False
        # Specifically: it did not answer with some other commit.
        assert "tested_sha" not in document

    def test_a_malformed_field_is_refused_with_its_own_code(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        report = self._report(tmp_path / "aggregate.json", tested_sha="not-a-sha")
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli, ["read-analysis-context", str(report), "--out", str(out)]
        )
        assert result.exit_code == EXIT_REFUSED
        assert _read(out)["code"] == "analysis-context-malformed"

    def test_a_document_past_the_byte_cap_is_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # It came out of a hostile archive, so the reader is bounded.
        report = self._report(tmp_path / "aggregate.json", tested_sha=MERGE)
        out = tmp_path / "read.json"
        result = runner.invoke(
            action_cli,
            [
                "read-analysis-context",
                str(report),
                "--out",
                str(out),
                "--max-bytes",
                "8",
            ],
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        assert _read(out)["present"] is False


# ---------------------------------------------------------------------------
# verify-tested-sha
# ---------------------------------------------------------------------------


def _run_document(head: str = HEAD) -> dict[str, Any]:
    return {
        "id": 5001,
        "run_attempt": 1,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": head,
        "path": ".github/workflows/ci.yml",
        "name": "CI",
        "repository": {"full_name": "example/project"},
        "head_repository": {"full_name": "example/project"},
    }


def _verified_result() -> dict[str, Any]:
    return {"verified": True, "pr_number": 42, "pr_head_sha": HEAD}


class TestVerifyTestedSha:
    def _invoke(
        self,
        runner: CliRunner,
        tmp_path: Path,
        *,
        tested_sha: str,
        result_document: dict[str, Any] | None = None,
        commit: dict[str, Any] | None = None,
    ) -> tuple[Any, Path]:
        run_json = _write(tmp_path / "run.json", _run_document())
        result_json = _write(
            tmp_path / "result.json",
            _verified_result() if result_document is None else result_document,
        )
        out = tmp_path / "tested.json"
        args = [
            "verify-tested-sha",
            "--run-json",
            str(run_json),
            "--result-json",
            str(result_json),
            "--tested-sha",
            tested_sha,
            "--out",
            str(out),
        ]
        if commit is not None:
            args += ["--tested-commit-json", str(_write(tmp_path / "c.json", commit))]
        return runner.invoke(action_cli, args), out

    def test_the_pull_request_head_itself_is_accepted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(runner, tmp_path, tested_sha=HEAD)
        assert result.exit_code == 0, result.output
        assert _read(out) == {"verified": True, "tested_sha": HEAD}

    def test_a_real_merge_of_the_head_is_accepted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(
            runner,
            tmp_path,
            tested_sha=MERGE,
            commit={"sha": MERGE, "parents": [{"sha": HEAD}, {"sha": BASE}]},
        )
        assert result.exit_code == 0, result.output
        assert _read(out)["tested_sha"] == MERGE

    def test_a_single_parent_child_of_the_head_is_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # A commit built ON TOP of the pull request is not the pull request's
        # tree, and a contributor can produce one and name it.
        result, out = self._invoke(
            runner,
            tmp_path,
            tested_sha=MERGE,
            commit={"sha": MERGE, "parents": [{"sha": HEAD}]},
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        assert _read(out)["code"] == "unassociated-tested-sha"

    def test_an_unrelated_commit_is_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(
            runner,
            tmp_path,
            tested_sha=UNRELATED,
            commit={"sha": UNRELATED, "parents": [{"sha": BASE}, {"sha": BASE}]},
        )
        assert result.exit_code == EXIT_REFUSED
        assert _read(out)["code"] == "unassociated-tested-sha"

    def test_a_non_head_commit_with_no_commit_document_is_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(runner, tmp_path, tested_sha=MERGE)
        assert result.exit_code == EXIT_REFUSED
        assert _read(out)["code"] == "unverified-tested-sha"

    @pytest.mark.parametrize(
        "document",
        [
            {"verified": False, "code": "wrong-repository"},
            {"pr_number": 42, "pr_head_sha": HEAD},
            ["not", "an", "object"],
        ],
    )
    def test_a_result_that_does_not_record_a_verified_run_is_refused(
        self, runner: CliRunner, tmp_path: Path, document: object
    ) -> None:
        # This command trusts the first pass's answer about which pull
        # request this is, so it must refuse anything that is not one.
        result, _out = self._invoke(
            runner,
            tmp_path,
            tested_sha=HEAD,
            result_document=document,  # type: ignore[arg-type]
        )
        assert result.exit_code == EXIT_REFUSED, result.output


# ---------------------------------------------------------------------------
# tag-peel / resolve-tag
# ---------------------------------------------------------------------------


def _ref(name: str, sha: str, kind: str = "commit") -> dict[str, Any]:
    return {"ref": f"refs/tags/{name}", "object": {"sha": sha, "type": kind}}


class TestTagPeel:
    def test_a_lightweight_tag_names_nothing_to_fetch(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", MERGE))
        result = runner.invoke(action_cli, ["tag-peel", "1.5.2", str(ref)])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == ""

    def test_an_annotated_tag_names_its_object(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", TAG_OBJECT, "tag"))
        result = runner.invoke(action_cli, ["tag-peel", "1.5.2", str(ref)])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == TAG_OBJECT

    def test_a_ref_that_is_not_this_tag_refuses(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(
            tmp_path / "ref.json",
            {"ref": "refs/heads/1.5.2", "object": {"sha": MERGE, "type": "commit"}},
        )
        result = runner.invoke(action_cli, ["tag-peel", "1.5.2", str(ref)])
        assert result.exit_code == EXIT_REFUSED, result.output
        assert "tag-not-found" in result.output


class TestResolveTag:
    def test_it_emits_the_commit_and_the_expected_revision_apart(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", MERGE))
        out = tmp_path / "resolved.json"
        result = runner.invoke(
            action_cli, ["resolve-tag", "1.5.2", str(ref), "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        # The two key names `run.sh` reads back, and the fact that they are
        # two: conflating them is the defect this command exists to prevent.
        assert document["commit_sha"] == MERGE
        assert document["expected_project_ref"] == MERGE
        assert document["annotated"] is False
        assert document["resolved"] is True

    def test_the_tag_mode_expects_the_tag_string_not_the_commit(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", MERGE))
        out = tmp_path / "resolved.json"
        result = runner.invoke(
            action_cli,
            [
                "resolve-tag",
                "1.5.2",
                str(ref),
                "--expected-project-ref",
                "tag",
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["commit_sha"] == MERGE
        assert document["expected_project_ref"] == "1.5.2"

    def test_an_annotated_tag_is_peeled_through_its_object(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", TAG_OBJECT, "tag"))
        tag_object = _write(
            tmp_path / "tag.json",
            {"sha": TAG_OBJECT, "object": {"sha": MERGE, "type": "commit"}},
        )
        out = tmp_path / "resolved.json"
        result = runner.invoke(
            action_cli,
            [
                "resolve-tag",
                "1.5.2",
                str(ref),
                "--tag-object-json",
                str(tag_object),
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["commit_sha"] == MERGE
        assert document["annotated"] is True

    def test_a_refusal_writes_its_code_and_exits_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", TAG_OBJECT, "tag"))
        out = tmp_path / "resolved.json"
        result = runner.invoke(
            action_cli, ["resolve-tag", "1.5.2", str(ref), "--out", str(out)]
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        document = _read(out)
        assert document["resolved"] is False
        assert document["code"] == "tag-object-missing"

    def test_an_unsupported_expectation_is_refused(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", MERGE))
        result = runner.invoke(
            action_cli,
            ["resolve-tag", "1.5.2", str(ref), "--expected-project-ref", "HEAD"],
        )
        assert result.exit_code == EXIT_REFUSED
        assert "expected-project-ref" in result.output

    def test_it_prints_the_document_even_without_out(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # `--out` is optional; the shell may read stdout instead.
        ref = _write(tmp_path / "ref.json", _ref("1.5.2", MERGE))
        result = runner.invoke(action_cli, ["resolve-tag", "1.5.2", str(ref)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["commit_sha"] == MERGE


# ---------------------------------------------------------------------------
# select-precaptured-source
# ---------------------------------------------------------------------------


def _producer_run(**overrides: Any) -> dict[str, Any]:
    document = {
        "id": 5001,
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": MERGE,
        "path": ".github/workflows/release.yml",
        "name": "Release",
        "repository": {"full_name": "example/project"},
        "head_repository": {"full_name": "example/project"},
    }
    document.update(overrides)
    return document


def _artifact(name: str, artifact_id: int = 901, **overrides: Any) -> dict[str, Any]:
    entry = {
        "id": artifact_id,
        "name": name,
        "size_in_bytes": 1024,
        "expired": False,
        "workflow_run": {"id": 5001},
    }
    entry.update(overrides)
    return entry


class TestSelectPrecapturedSource:
    def _invoke(
        self,
        runner: CliRunner,
        tmp_path: Path,
        *,
        run: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        extra: list[str] | None = None,
    ) -> tuple[Any, Path]:
        run_json = _write(tmp_path / "run.json", run or _producer_run())
        artifacts_json = _write(
            tmp_path / "artifacts.json",
            artifacts if artifacts is not None else [_artifact("baseline-set-linux")],
        )
        out = tmp_path / "source.json"
        args = [
            "select-precaptured-source",
            "--run-json",
            str(run_json),
            "--artifacts-json",
            str(artifacts_json),
            "--artifact-prefix",
            "baseline-set-",
            "--expect-repository",
            "example/project",
            "--expect-run-id",
            "5001",
            "--out",
            str(out),
        ]
        return runner.invoke(action_cli, args + (extra or [])), out

    def test_an_eligible_run_returns_its_artifacts_by_id(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(runner, tmp_path)
        assert result.exit_code == 0, result.output
        document = _read(out)
        assert document["eligible"] is True
        assert document["run_id"] == "5001"
        # `artifact_id` and `name` are the two keys the workflow's own
        # `while read` loop consumes.
        assert document["artifacts"] == [
            {
                "artifact_id": "901",
                "name": "baseline-set-linux",
                "size_bytes": 1024,
            }
        ]

    def test_a_pull_request_producer_is_refused_whatever_is_declared(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result, out = self._invoke(
            runner,
            tmp_path,
            run=_producer_run(event="pull_request"),
            extra=["--expect-event", "pull_request"],
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        document = _read(out)
        assert document["eligible"] is False
        assert document["code"] == "producer-event-forbidden"

    def test_an_artifacts_listing_in_envelope_form_is_accepted(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # The API's own shape is `{"artifacts": [...]}`; the workflow
        # flattens it, but the command accepts either rather than depending
        # on which flattener ran.
        run_json = _write(tmp_path / "run.json", _producer_run())
        artifacts_json = _write(
            tmp_path / "artifacts.json",
            {"artifacts": [_artifact("baseline-set-linux")]},
        )
        out = tmp_path / "source.json"
        result = runner.invoke(
            action_cli,
            [
                "select-precaptured-source",
                "--run-json",
                str(run_json),
                "--artifacts-json",
                str(artifacts_json),
                "--artifact-prefix",
                "baseline-set-",
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert _read(out)["artifacts"][0]["artifact_id"] == "901"

    @pytest.mark.parametrize(
        ("label", "kwargs", "code"),
        [
            (
                "wrong workflow",
                {"extra": ["--expect-workflow", ".github/workflows/other.yml"]},
                "wrong-workflow",
            ),
            (
                "wrong attempt",
                {"extra": ["--expect-run-attempt", "7"]},
                "wrong-attempt",
            ),
            (
                "disallowed conclusion",
                {"run": _producer_run(conclusion="failure")},
                "wrong-conclusion",
            ),
            (
                "no matching artifact",
                {"artifacts": [_artifact("build-logs")]},
                "artifact-not-found",
            ),
            (
                "expired artifact",
                {"artifacts": [_artifact("baseline-set-linux", expired=True)]},
                "artifact-expired",
            ),
        ],
    )
    def test_each_refusal_writes_its_own_code(
        self,
        runner: CliRunner,
        tmp_path: Path,
        label: str,
        kwargs: dict[str, Any],
        code: str,
    ) -> None:
        result, out = self._invoke(runner, tmp_path, **kwargs)
        assert result.exit_code == EXIT_REFUSED, f"{label}: {result.output}"
        assert _read(out)["code"] == code, label

    def test_an_empty_allowed_conclusion_permits_a_failed_run(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # The documented "empty allows any" choice, at the command boundary.
        result, _out = self._invoke(
            runner,
            tmp_path,
            run=_producer_run(conclusion="failure"),
            extra=["--allow-conclusion", ""],
        )
        assert result.exit_code == 0, result.output


class TestEveryProvenanceCommandIsReachable:
    """The registration side effect this module depends on actually happened.

    Without the `cli` import above, the group has no commands and every
    invocation exits 2 — which several assertions here would otherwise read
    as an ordinary refusal-adjacent failure rather than as "the test file is
    testing nothing".
    """

    COMMANDS = (
        "record-analysis-context",
        "read-analysis-context",
        "verify-tested-sha",
        "tag-peel",
        "resolve-tag",
        "select-precaptured-source",
    )

    @pytest.mark.parametrize("name", COMMANDS)
    def test_the_command_is_registered(self, name: str) -> None:
        assert name in action_cli.commands

    @pytest.mark.parametrize("name", COMMANDS)
    def test_the_command_has_help_text(self, runner: CliRunner, name: str) -> None:
        result = runner.invoke(action_cli, [name, "--help"])
        assert result.exit_code == 0
        assert result.output.strip()
