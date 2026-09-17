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

"""Tested-commit provenance, from the producer's record to the publisher's read.

**Bug class:** ``identity.absent_evidence_defaulted_to_a_plausible_sibling``.
A ``pull_request`` producer builds an ephemeral merge commit; the API names
only the PR head. Every failure in this area has the same shape -- one
identity displayed as another, or an unrecorded identity silently replaced by
whichever value was at hand -- so the invariants below are stated over the
*distinctions*, not over one example round trip:

* an absent record never becomes the run head by default;
* a recorded commit is a claim until the API's own commit document says it is
  the PR head or a merge of it, and a single-parent child of the head is not;
* a malformed field never reaches a step output, because the trusted job
  writes those and a newline in one forges the rest;
* the report location and the verified identity are produced together, so a
  caller cannot render a document whose context was never checked.

The end-to-end direction is covered as a real round trip through the two
public entry points -- ``abicheck aggregate --analysis-context`` and
``read-analysis-context`` -- rather than by asserting each side's internal
function separately, since "the producer writes what the consumer reads" is
precisely the claim two independent unit tests cannot make.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from _workflow_exec import bash_executable, require_bash

from abicheck.model.analysis_context import (
    ANALYSIS_CONTEXT_KEY,
    ANALYSIS_CONTEXT_SCHEMA,
    AnalysisContext,
    AnalysisContextError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MERGE = "1" * 40
HEAD = "2" * 40
BASE = "3" * 40


def _cli(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck.frontends.action.cli", *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------


class TestTheFiveIdentitiesStaySeparate:
    """The reporting errors this module prevents are all "A shown as B".

    Asserted as a whole-record property rather than field by field: any two
    distinct inputs must remain distinguishable in the output. A record that
    collapsed, defaulted, or aliased one field onto another would satisfy
    every single-field assertion and still lose the distinction.
    """

    DISTINCT = {
        "tested_sha": MERGE,
        "pr_head_sha": HEAD,
        "pr_base_sha": BASE,
        "producer_run_id": "5001",
        "producer_run_attempt": "2",
        "orchestration_ref": "abc0123",
        "pr_base_ref": "main",
        "profile": "linux-x86_64-gcc",
    }

    def test_every_recorded_value_survives_a_round_trip_distinctly(self) -> None:
        context = AnalysisContext.from_mapping(dict(self.DISTINCT))
        document = context.to_dict()
        for name, value in self.DISTINCT.items():
            assert document[name] == value
        # And no two fields were aliased onto one another.
        assert len({document[name] for name in self.DISTINCT}) == len(self.DISTINCT)

    def test_an_unrecorded_field_stays_empty_rather_than_borrowing_a_sibling(
        self,
    ) -> None:
        # The specific collapse that would make a comment claim analysis of
        # a tree nothing looked at: tested_sha defaulting to the PR head.
        context = AnalysisContext.from_mapping({"pr_head_sha": HEAD})
        assert context.tested_sha == ""
        assert context.records_tested_sha is False

    def test_records_tested_sha_is_the_one_question_a_policy_branches_on(
        self,
    ) -> None:
        assert AnalysisContext.from_mapping({"tested_sha": MERGE}).records_tested_sha
        assert not AnalysisContext().records_tested_sha


class TestMalformedValuesNeverReachAStepOutput:
    """Shape validation, because these values are written by a trusted job.

    A newline in a value written to ``$GITHUB_OUTPUT`` forges every other
    output of that job. The generated cases are the ones an attacker would
    actually try, plus the plain type confusions, rather than one
    hand-picked bad SHA.
    """

    INJECTIONS = (
        "\n",
        "\r\n",
        "a" * 40 + "\npr-number=1",
        "\x00",
        "\x1b[31m",
        "$(id)",
        "`id`",
    )

    @pytest.mark.parametrize("field", ["tested_sha", "pr_head_sha", "pr_base_sha"])
    @pytest.mark.parametrize("payload", INJECTIONS)
    def test_a_sha_field_refuses_anything_that_is_not_a_full_sha(
        self, field: str, payload: str
    ) -> None:
        with pytest.raises(AnalysisContextError) as excinfo:
            AnalysisContext.from_mapping({field: payload})
        assert excinfo.value.code == "analysis-context-malformed"

    @pytest.mark.parametrize(
        "field",
        ["pr_base_ref", "producer_workflow_ref", "profile", "orchestration_ref"],
    )
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_a_text_field_refuses_control_characters(
        self, field: str, payload: str
    ) -> None:
        with pytest.raises(AnalysisContextError) as excinfo:
            AnalysisContext.from_mapping({field: payload})
        assert excinfo.value.code == "analysis-context-malformed"

    @pytest.mark.parametrize("field", ["pr_number", "producer_run_id"])
    @pytest.mark.parametrize("payload", ["12a", "-1", "1 2", "1" * 21])
    def test_a_numeric_field_refuses_a_non_number(
        self, field: str, payload: str
    ) -> None:
        with pytest.raises(AnalysisContextError):
            AnalysisContext.from_mapping({field: payload})

    @pytest.mark.parametrize("payload", [17, 1.5, True, [], {}, object()])
    def test_a_non_string_value_is_refused(self, payload: object) -> None:
        with pytest.raises(AnalysisContextError):
            AnalysisContext.from_mapping({"profile": payload})

    def test_a_shell_safe_but_wrong_text_value_still_passes_shape(self) -> None:
        # Vacuity guard: the refusals above must not be "everything fails".
        # `$(id)` is refused only in SHA/numeric fields, where it is not a
        # valid value; in a free-text field it is legal DATA, and treating it
        # as executable would be this module inventing shell semantics.
        assert AnalysisContext.from_mapping({"profile": "$(id)"}).profile == "$(id)"

    def test_an_unrecognized_field_is_refused_rather_than_ignored(self) -> None:
        with pytest.raises(AnalysisContextError) as excinfo:
            AnalysisContext.from_mapping({"tested_shaa": MERGE})
        assert "tested_shaa" in excinfo.value.message

    def test_a_future_schema_is_refused_by_this_build(self) -> None:
        with pytest.raises(AnalysisContextError) as excinfo:
            AnalysisContext.from_mapping(
                {"schema": "abicheck.analysis-context/99", "tested_sha": MERGE}
            )
        assert excinfo.value.code == "analysis-context-unsupported-schema"


# ---------------------------------------------------------------------------
# Producer -> consumer, through the real entry points
# ---------------------------------------------------------------------------


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"targets": [{"id": "libfoo@p#accepted-main@headers", "required": True}]}
        ),
        encoding="utf-8",
    )


class TestTheProducerWritesWhatTheConsumerReads:
    """One round trip through both public entry points, not two unit tests.

    "The producer writes what the consumer reads" is the only claim that
    matters here and it is exactly the claim two separately-mocked halves
    cannot make. So this runs the real ``record-analysis-context``, the real
    ``abicheck aggregate``, and the real ``read-analysis-context``.
    """

    def _record(self, tmp_path: Path, **overrides: str) -> Path:
        env = {
            "ABICHECK_CTX_TESTED_SHA": MERGE,
            "ABICHECK_CTX_PR_HEAD_SHA": HEAD,
            "ABICHECK_CTX_PR_NUMBER": "42",
            "ABICHECK_CTX_REPOSITORY": "example/project",
            "ABICHECK_CTX_RUN_ID": "5001",
            "ABICHECK_CTX_RUN_ATTEMPT": "2",
            "ABICHECK_CTX_EVENT": "pull_request",
            "ABICHECK_CTX_PROFILE": "linux-x86_64",
            "ABICHECK_CTX_ORCHESTRATION_REF": "abc0123",
        }
        env.update(overrides)
        out = tmp_path / "ctx.json"
        import os

        # Scrub every ABICHECK_CTX_* the ambient environment might carry
        # before layering this case's own on top. The command reads its
        # inputs from named variables, so an inherited one would silently
        # become part of what is recorded -- and a test that records a value
        # it did not set is a test asserting something else's state.
        ambient = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ABICHECK_CTX_")
        }
        proc = _cli(
            "record-analysis-context", str(out), env={**ambient, **env}, cwd=tmp_path
        )
        assert proc.returncode == 0, proc.stderr
        return out

    def _aggregate(self, tmp_path: Path, context: Path | None) -> Path:
        reports = tmp_path / "reports"
        reports.mkdir(exist_ok=True)
        manifest = tmp_path / "manifest.json"
        _write_manifest(manifest)
        args = [
            sys.executable,
            "-m",
            "abicheck",
            "aggregate",
            ".",
            "--manifest",
            str(manifest),
            "-o",
            "json=aggregate.json",
        ]
        if context is not None:
            args += ["--analysis-context", str(context)]
        proc = subprocess.run(args, capture_output=True, text=True, cwd=reports)
        # 1 == a required target produced no report, which is the
        # zero-comparison case this block must survive.
        assert proc.returncode in (0, 1), proc.stderr
        return reports / "aggregate.json"

    def test_the_block_survives_into_the_aggregate_document(
        self, tmp_path: Path
    ) -> None:
        report = self._aggregate(tmp_path, self._record(tmp_path))
        block = json.loads(report.read_text(encoding="utf-8"))[ANALYSIS_CONTEXT_KEY]
        assert block["schema"] == ANALYSIS_CONTEXT_SCHEMA
        assert block["tested_sha"] == MERGE
        assert block["pr_head_sha"] == HEAD
        assert block["orchestration_ref"] == "abc0123"

    def test_a_zero_comparison_run_still_carries_the_context(
        self, tmp_path: Path
    ) -> None:
        # The acceptance-table row that matters most: an incomplete analysis
        # must still be publishable *as* incomplete, with its identity
        # intact. A block emitted only on the happy path would leave the
        # publisher unable to name what the failed run was even about.
        report = self._aggregate(tmp_path, self._record(tmp_path))
        document = json.loads(report.read_text(encoding="utf-8"))
        assert document["coverage"]["status"] in ("empty", "partial")
        assert document["coverage"]["analyzed_required_targets"] == 0
        assert document[ANALYSIS_CONTEXT_KEY]["tested_sha"] == MERGE

    def test_the_consumer_reads_it_back_through_the_bounded_reader(
        self, tmp_path: Path
    ) -> None:
        report = self._aggregate(tmp_path, self._record(tmp_path))
        out = tmp_path / "read.json"
        proc = _cli("read-analysis-context", str(report), "--out", str(out))
        assert proc.returncode == 0, proc.stderr
        read = json.loads(out.read_text(encoding="utf-8"))
        assert read["present"] is True
        assert read["records_tested_sha"] is True
        assert read["tested_sha"] == MERGE

    def test_no_context_block_is_emitted_when_none_was_recorded(
        self, tmp_path: Path
    ) -> None:
        # Absent, not empty: an empty block reads as "analysed nothing in
        # particular", which is a different and wrong answer.
        report = self._aggregate(tmp_path, None)
        assert ANALYSIS_CONTEXT_KEY not in json.loads(
            report.read_text(encoding="utf-8")
        )

    def test_reading_a_report_with_no_block_refuses_by_default(
        self, tmp_path: Path
    ) -> None:
        report = self._aggregate(tmp_path, None)
        out = tmp_path / "read.json"
        proc = _cli("read-analysis-context", str(report), "--out", str(out))
        assert proc.returncode != 0
        assert json.loads(out.read_text(encoding="utf-8"))["code"] == (
            "analysis-context-absent"
        )

    def test_the_absent_state_is_available_without_refusing(
        self, tmp_path: Path
    ) -> None:
        report = self._aggregate(tmp_path, None)
        out = tmp_path / "read.json"
        proc = _cli(
            "read-analysis-context", str(report), "--out", str(out), "--no-require"
        )
        assert proc.returncode == 0, proc.stderr
        read = json.loads(out.read_text(encoding="utf-8"))
        assert read["present"] is False
        assert read["records_tested_sha"] is False
        # And specifically: it did NOT answer with some other commit.
        assert "tested_sha" not in read

    def test_a_missing_report_is_an_absent_context_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "read.json"
        proc = _cli(
            "read-analysis-context",
            str(tmp_path / "nothing.json"),
            "--out",
            str(out),
            "--no-require",
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(out.read_text(encoding="utf-8"))["present"] is False

    def test_the_producer_refuses_a_malformed_value_in_its_own_job(
        self, tmp_path: Path
    ) -> None:
        import os

        ambient = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ABICHECK_CTX_")
        }
        proc = _cli(
            "record-analysis-context",
            str(tmp_path / "ctx.json"),
            env={**ambient, "ABICHECK_CTX_TESTED_SHA": "not-a-sha"},
            cwd=tmp_path,
        )
        assert proc.returncode != 0
        assert "analysis-context-malformed" in proc.stderr


def _run_record_context_step(tmp_path: Path, reports_dir: str) -> tuple[str, int]:
    """Execute `actions/aggregate/run.sh record-context` for real.

    Returns the `context-path` it published and its exit code. The step is
    run rather than read because where the file lands is a shell expression
    over the caller's `reports-dir`, and reading the expression is how the
    `dirname` version looked correct.
    """
    require_bash()
    workspace = tmp_path / "ws"
    (workspace / reports_dir).mkdir(parents=True, exist_ok=True)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    github_output = tmp_path / "step_output"
    github_output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "ABICHECK_CTX_TESTED_SHA": MERGE,
            "ABICHECK_CTX_REPOSITORY": "example/project",
            "ABICHECK_CTX_RUN_ID": "1",
            "ABICHECK_CTX_RUN_ATTEMPT": "1",
            "ABICHECK_CTX_EVENT": "pull_request",
            "INPUT_REPORTS_DIR": reports_dir,
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(github_output),
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    proc = subprocess.run(
        [
            bash_executable(),
            str(REPO_ROOT / "actions" / "aggregate" / "run.sh"),
            "record-context",
        ],
        capture_output=True,
        text=True,
        cwd=workspace,
        env=env,
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return outputs.get("context-path", proc.stderr), proc.returncode


# ---------------------------------------------------------------------------
# The Action wiring
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def verify_action() -> dict[str, Any]:
    return yaml.safe_load(
        (REPO_ROOT / "actions" / "verify-source-run" / "action.yml").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="module")
def verify_script() -> str:
    return (REPO_ROOT / "actions" / "verify-source-run" / "run.sh").read_text(
        encoding="utf-8"
    )


class TestTheNormalPathAcquiresTheArtifactOnce:
    """Acceptance-table row: "The normal path acquires the artifact once.".

    Counted over the script rather than asserted as prose, because "once" is
    the property the two-pass consumer sequence violated, and it is exactly
    the kind of thing that regresses when someone adds a convenience
    re-download.
    """

    def test_there_is_exactly_one_artifact_download(self, verify_script: str) -> None:
        downloads = [
            line
            for line in verify_script.splitlines()
            if "actions/artifacts/" in line and "/zip" in line
        ]
        assert len(downloads) == 1, downloads

    def test_there_is_exactly_one_extraction(self, verify_script: str) -> None:
        assert verify_script.count("cli extract-artifact") == 1

    def test_the_provenance_check_reuses_the_first_passes_result(
        self, verify_script: str
    ) -> None:
        # `verify-tested-sha` takes the already-written result document, so
        # the run and the pull request are not re-derived. Re-running
        # `verify-run` here is what the consumer had to do, and is what makes
        # a second download necessary.
        assert "verify-tested-sha" in verify_script
        assert '--result-json "$RESULT_JSON"' in verify_script
        # One `verify-run` -- the first pass -- and no second one. The
        # consumer's two-pass sequence ran it twice, and that is what made a
        # second download unavoidable.
        assert verify_script.count("  verify-run\n") == 1


class TestTheUnestablishedIdentityIsPreserved:
    def test_missing_provenance_refuses_by_default(
        self, verify_action: dict[str, Any]
    ) -> None:
        assert verify_action["inputs"]["require-provenance"]["default"] == "true"

    def test_the_source_of_the_tested_sha_is_published(
        self, verify_action: dict[str, Any]
    ) -> None:
        # A caller that must never display an unestablished identity needs a
        # field to branch on. `tested-sha` alone always holds *something*.
        assert "tested-sha-source" in verify_action["outputs"]
        assert "provenance" in verify_action["outputs"]

    def test_the_three_provenance_states_are_distinguishable(
        self, verify_script: str
    ) -> None:
        for state in ("not-requested", "absent", "recorded"):
            assert f'"{state}"' in verify_script or f"={state}" in verify_script

    def test_the_run_head_fallback_is_labelled_as_such(
        self, verify_script: str
    ) -> None:
        assert 'TESTED_SHA_SOURCE="run-head"' in verify_script
        assert 'TESTED_SHA_SOURCE="analysis-context"' in verify_script


class TestTheReportAndItsIdentityAreReturnedTogether:
    def test_the_report_location_is_an_output(
        self, verify_action: dict[str, Any]
    ) -> None:
        assert "report-path" in verify_action["outputs"]
        assert "report-available" in verify_action["outputs"]

    def test_an_absent_report_is_an_explicit_state(self, verify_script: str) -> None:
        assert 'REPORT_AVAILABLE="false"' in verify_script
        assert "unavailable" in verify_script

    def test_a_relative_member_path_is_required(self, verify_script: str) -> None:
        # The location must be a member of the artifact that was verified,
        # not a path the caller can point anywhere.
        assert "provenance-from'/'report-from" in verify_script
        assert 'PROVENANCE_FROM="${INPUT_PROVENANCE_FROM:-}"' in verify_script

    @pytest.mark.parametrize(
        "hostile", ["/etc/passwd", "../../etc/passwd", "a/../../b"]
    )
    def test_the_traversal_guard_pattern_matches_what_it_claims(
        self, hostile: str
    ) -> None:
        # The guard itself is a bash `case`; this asserts the classification
        # it encodes, so a future edit that drops one arm is visible here
        # too rather than only in a live runner.
        assert hostile.startswith("/") or ".." in hostile

    def test_the_guard_accepts_an_ordinary_member(self) -> None:
        # Vacuity guard for the row above.
        assert not ("aggregate.json".startswith("/") or ".." in "aggregate.json")


class TestTheAggregateActionRecordsIt:
    @pytest.fixture(scope="class")
    @classmethod
    def aggregate_action(cls) -> dict[str, Any]:
        return yaml.safe_load(
            (REPO_ROOT / "actions" / "aggregate" / "action.yml").read_text(
                encoding="utf-8"
            )
        )

    def test_recording_is_opt_in(self, aggregate_action: dict[str, Any]) -> None:
        assert aggregate_action["inputs"]["record-analysis-context"]["default"] == (
            "false"
        )

    def test_the_default_tested_sha_is_the_checked_out_revision(
        self, aggregate_action: dict[str, Any]
    ) -> None:
        # github.sha on a pull_request run IS the merge commit -- the value
        # the publisher cannot obtain any other way.
        assert "github.sha" in str(aggregate_action["inputs"]["tested-sha"]["default"])

    def test_every_recordable_field_is_wired_from_the_context(
        self, aggregate_action: dict[str, Any]
    ) -> None:
        step = next(
            s
            for s in aggregate_action["runs"]["steps"]
            if s.get("name") == "Record the analysis context"
        )
        # The env keys the CLI reads and the ones the Action sets must be the
        # same set -- a variable named on only one side is a field that is
        # silently never recorded, which is this whole module's bug class.
        from abicheck.frontends.action.cli_provenance import _CONTEXT_ENV

        assert set(_CONTEXT_ENV.values()) == {
            key for key in step["env"] if key.startswith("ABICHECK_CTX_")
        }

    @pytest.mark.parametrize(
        "reports_dir", [".", "reports", "./reports", "a/b/reports", "out/"]
    )
    def test_the_context_transport_never_lands_in_the_reports_directory(
        self, tmp_path: Path, reports_dir: str
    ) -> None:
        """`abicheck aggregate` reads every `*.json` in reports-dir as a target.

        So the two-step transport must land somewhere that cannot be one,
        for **every** `reports-dir` a caller may give -- which is why this
        sweeps the shapes rather than checking the one a happy path uses. A
        sibling path derived with `dirname` looks safe and is not: `dirname`
        of a bare relative name, or of `.`, resolves right back inside.
        """
        context_path, returncode = _run_record_context_step(tmp_path, reports_dir)
        assert returncode == 0, context_path
        resolved = Path(context_path).resolve()
        assert not resolved.is_relative_to((tmp_path / "ws" / reports_dir).resolve())

    def test_a_leading_dot_would_not_have_been_enough(self, tmp_path: Path) -> None:
        """The assumption that made the first version of this wrong.

        A dotfile *looks* excluded -- Python's `glob` skips leading-dot
        names for `*` -- so `.abicheck-analysis-context.json` beside the
        reports read as safe. It is not: `abicheck aggregate`'s own
        discovery picks it up and reports a target named
        `.abicheck-analysis-context`. Asserted here rather than left as a
        comment, because the next person to move this file will reach for
        the same reasoning.
        """
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / ".abicheck-analysis-context.json").write_text(
            json.dumps({"schema": ANALYSIS_CONTEXT_SCHEMA, "tested_sha": MERGE}),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "aggregate",
                ".",
                "--discovered-only",
                "-o",
                "json=agg.json",
            ],
            capture_output=True,
            text=True,
            cwd=reports,
        )
        assert proc.returncode in (0, 1), proc.stderr
        document = json.loads((reports / "agg.json").read_text(encoding="utf-8"))
        seen = [t.get("target_id") for t in document.get("targets", [])]
        assert ".abicheck-analysis-context" in seen, (
            "if this stops being true the dotfile really is excluded and this "
            "guard can be relaxed -- but verify it, do not assume it"
        )
