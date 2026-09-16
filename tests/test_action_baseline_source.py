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

"""Which producer run may supply a baseline, and whether a name is a tag.

Every case here is a way a *wrong* baseline becomes the thing a pull request is
measured against: a branch published as a release, an annotated tag read as its
own tag object, a failed run's capture, a run from another branch at the same
SHA, and a transient API error read as "no baseline, therefore compatible".
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _workflow_exec import bash_executable, require_bash

from abicheck.frontends.action.baseline_source import (
    BaselineProducerExpectation,
    required_job_failures,
    resolve_tag,
    select_producer_run,
    verify_tag_commit,
)
from abicheck.frontends.action.run_selection import SourceRunRejected

_WORKFLOW = ".github/workflows/ci.yml"
_RUN_SH = (
    Path(__file__).resolve().parents[1]
    / "actions"
    / "verify-baseline-source"
    / "run.sh"
)


def _run(
    run_id: int,
    *,
    number: int = 1,
    conclusion: str = "success",
    sha: str = "BASE",
    branch: str = "main",
    repo: str = "o/r",
    event: str = "push",
    path: str = _WORKFLOW,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "run_number": number,
        "run_attempt": 1,
        "repository": {"full_name": repo},
        "path": path,
        "name": "CI",
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": branch,
    }


_EXPECT = BaselineProducerExpectation(
    repository="o/r",
    workflow=_WORKFLOW,
    event="push",
    head_sha="BASE",
    head_branch="main",
)


class TestTags:
    def test_a_lightweight_tag_resolves_to_its_commit(self) -> None:
        resolution = resolve_tag("1.5.2", {"object": {"sha": "C1", "type": "commit"}})
        assert (resolution.outcome, resolution.commit_sha, resolution.annotated) == (
            "tag",
            "C1",
            False,
        )

    def test_an_annotated_tag_is_peeled(self) -> None:
        resolution = resolve_tag(
            "1.5.2",
            {"object": {"sha": "TAGOBJ", "type": "tag"}},
            tag_object_document={"object": {"sha": "C1", "type": "commit"}},
        )
        assert (resolution.outcome, resolution.commit_sha, resolution.annotated) == (
            "tag",
            "C1",
            True,
        )

    def test_an_annotated_tag_is_never_read_as_its_own_tag_object(self) -> None:
        """Using the tag-object sha as the commit compares against an object
        that is not a commit at all -- and it silently 'works'."""
        resolution = resolve_tag("1.5.2", {"object": {"sha": "TAGOBJ", "type": "tag"}})
        assert resolution.outcome == "lookup_failed"
        assert resolution.commit_sha == ""

    @pytest.mark.parametrize(
        "name", ["1.5.2", "v1.5.2", "release-2026.01", "2026.1.0rc1"]
    )
    def test_a_tag_name_is_never_required_to_look_like_a_version(
        self, name: str
    ) -> None:
        """A ``v``-prefix guard excludes every release of a project that
        doesn't use one, which is most of them."""
        assert resolve_tag(name, {"object": {"sha": "C1", "type": "commit"}}).ok

    def test_a_branch_is_not_a_tag(self) -> None:
        resolution = resolve_tag("main", None)
        assert resolution.outcome == "not_a_tag"

    def test_a_failed_lookup_is_not_a_missing_tag(self) -> None:
        """Collapsing these is how a transient API error publishes nothing
        silently, or worse, publishes a branch."""
        assert resolve_tag("1.5.2", None, lookup_failed=True).outcome == "lookup_failed"

    @pytest.mark.parametrize(
        "document",
        [
            "a string",
            {"no_object": True},
            {"object": {"type": "commit"}},
            {"object": "not-a-mapping"},
            {"object": {"sha": "TAGOBJ", "type": "tag"}},
        ],
    )
    def test_a_malformed_ref_response_never_resolves(self, document: Any) -> None:
        resolution = resolve_tag("1.5.2", document)
        assert not resolution.ok

    def test_an_annotated_tag_object_naming_no_commit_is_refused(self) -> None:
        resolution = resolve_tag(
            "1.5.2",
            {"object": {"sha": "TAGOBJ", "type": "tag"}},
            tag_object_document={"object": {}},
        )
        assert resolution.outcome == "lookup_failed"

    def test_the_capture_must_belong_to_the_tagged_commit(self) -> None:
        resolution = resolve_tag("1.5.2", {"object": {"sha": "C1", "type": "commit"}})
        verify_tag_commit(resolution, "C1")
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tag_commit(resolution, "C2")
        assert excinfo.value.code == "tag-commit-mismatch"

    def test_a_capture_naming_no_commit_is_refused(self) -> None:
        resolution = resolve_tag("1.5.2", {"object": {"sha": "C1", "type": "commit"}})
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tag_commit(resolution, "")
        assert excinfo.value.code == "tag-commit-unknown"

    def test_a_non_tag_never_passes_the_commit_check(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tag_commit(resolve_tag("main", None), "C1")
        assert excinfo.value.code == "not_a_tag"


class TestProducerSelection:
    def test_the_newest_eligible_run_wins(self) -> None:
        selection = select_producer_run(
            [_run(1, number=1), _run(2, number=7), _run(3, number=3)], _EXPECT
        )
        assert selection.ok
        assert selection.run is not None and selection.run.run_id == "2"

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"conclusion": "failure"}, "wrong-conclusion"),
            ({"branch": "topic"}, "branch-mismatch"),
            ({"sha": "OTHER"}, "head-sha-mismatch"),
            ({"repo": "other/repo"}, "wrong-repository"),
            ({"event": "pull_request"}, "wrong-event"),
            ({"path": ".github/workflows/other.yml"}, "wrong-workflow"),
            ({"status": "in_progress", "conclusion": ""}, "conclusion"),
        ],
    )
    def test_exact_sha_alone_is_never_eligibility(
        self, kwargs: dict[str, Any], reason: str
    ) -> None:
        """Each of these runs sits at the right commit and must still be refused."""
        selection = select_producer_run([_run(1, **kwargs)], _EXPECT)
        assert selection.outcome == "not_found"
        assert any(reason in line for line in selection.rejected), selection.rejected

    def test_every_rejection_is_reported(self) -> None:
        """A caller saying "no eligible baseline" must be able to say what it
        saw; silence makes a mis-scoped query look like an empty history."""
        selection = select_producer_run(
            [_run(1, conclusion="failure"), _run(2, branch="topic")], _EXPECT
        )
        assert len(selection.rejected) == 2

    def test_not_found_and_lookup_failed_are_different_outcomes(self) -> None:
        assert select_producer_run([], _EXPECT).outcome == "not_found"
        assert (
            select_producer_run([_run(1)], _EXPECT, lookup_failed=True).outcome
            == "lookup_failed"
        )

    def test_a_malformed_candidate_is_skipped_not_fatal(self) -> None:
        selection = select_producer_run(["not a run", _run(9, number=2)], _EXPECT)
        assert selection.ok
        assert selection.run is not None and selection.run.run_id == "9"
        assert any("candidate 0" in line for line in selection.rejected)


class TestRequiredJobs:
    _JOBS = {
        "1": {
            "jobs": [
                {"name": "build", "conclusion": "success"},
                {"name": "capture", "conclusion": "success"},
                {"name": "flaky", "conclusion": "failure"},
            ]
        }
    }

    def test_unrelated_failures_are_only_accepted_under_explicit_policy(self) -> None:
        expectation = BaselineProducerExpectation(
            repository="o/r",
            workflow=_WORKFLOW,
            event="push",
            head_sha="BASE",
            head_branch="main",
            required_jobs=("build", "capture"),
        )
        failed_run = [_run(1, conclusion="failure")]
        # Without the policy, a failed run is simply not eligible.
        assert (
            select_producer_run(failed_run, expectation, jobs_by_run=self._JOBS).outcome
            == "not_found"
        )
        # With it, the required jobs are what decides.
        opted_in = BaselineProducerExpectation(
            **{**expectation.__dict__, "allow_unrelated_job_failures": True}
        )
        assert select_producer_run(failed_run, opted_in, jobs_by_run=self._JOBS).ok

    def test_a_required_job_that_failed_is_ineligible(self) -> None:
        expectation = BaselineProducerExpectation(
            repository="o/r",
            workflow=_WORKFLOW,
            event="push",
            head_sha="BASE",
            head_branch="main",
            required_jobs=("build", "flaky"),
            allow_unrelated_job_failures=True,
        )
        selection = select_producer_run(
            [_run(1, conclusion="failure")], expectation, jobs_by_run=self._JOBS
        )
        assert selection.outcome == "not_found"
        assert any("failed: flaky" in line for line in selection.rejected)

    def test_a_required_job_that_never_ran_is_not_a_passing_one(self) -> None:
        """An absent capture job treated as success is how a run with no
        capture at all becomes a baseline."""
        expectation = BaselineProducerExpectation(
            repository="o/r",
            workflow=_WORKFLOW,
            event="push",
            head_sha="BASE",
            head_branch="main",
            required_jobs=("build", "never-declared"),
        )
        selection = select_producer_run([_run(1)], expectation, jobs_by_run=self._JOBS)
        assert selection.outcome == "not_found"
        assert any("never ran: never-declared" in line for line in selection.rejected)

    def test_an_unchecked_requirement_is_not_a_satisfied_one(self) -> None:
        expectation = BaselineProducerExpectation(
            repository="o/r",
            workflow=_WORKFLOW,
            event="push",
            head_sha="BASE",
            head_branch="main",
            required_jobs=("build",),
        )
        selection = select_producer_run([_run(1)], expectation, jobs_by_run={})
        assert selection.outcome == "not_found"
        assert any("jobs-unavailable" in line for line in selection.rejected)

    @pytest.mark.parametrize(
        "document",
        [
            {"jobs": [{"name": "build", "conclusion": "success"}]},
            [{"name": "build", "conclusion": "success"}],
        ],
    )
    def test_either_jobs_document_shape_is_read(self, document: Any) -> None:
        assert required_job_failures(document, ["build"]) == ([], [], [])

    def test_a_required_job_with_no_conclusion_yet_is_not_satisfied(self) -> None:
        """A row exists but records ``null`` -- neither failed nor absent.

        The membership test used to exclude ``""``, which was meant to cover
        the *absent* default of the lookup while absence was computed
        separately -- so this case was silently satisfied. With
        ``allow_unrelated_job_failures`` dropping the run-level conclusion
        check too, a run whose capture job never concluded became an eligible
        baseline producer. An unchecked requirement is not a satisfied one.
        """
        document = {
            "jobs": [
                {"name": "build", "conclusion": "success"},
                {"name": "capture", "conclusion": None},
            ]
        }
        failed, absent, unfinished = required_job_failures(
            document, ["build", "capture"]
        )
        assert failed == []
        assert absent == []
        assert unfinished == ["capture"]

    @pytest.mark.parametrize(
        "conclusion", ["failure", "cancelled", "skipped", "timed_out"]
    )
    def test_only_success_satisfies_a_requirement(self, conclusion: str) -> None:
        """``skipped`` included deliberately: a skipped capture produced none."""
        document = {"jobs": [{"name": "capture", "conclusion": conclusion}]}
        failed, absent, unfinished = required_job_failures(document, ["capture"])
        assert failed == ["capture"]
        assert (absent, unfinished) == ([], [])

    def test_an_unconcluded_required_job_blocks_selection(self) -> None:
        """The end-to-end consequence, through the real selector."""
        expectation = BaselineProducerExpectation(
            repository="o/r",
            workflow=_WORKFLOW,
            event="push",
            head_sha="BASE",
            head_branch="main",
            required_jobs=("build", "capture"),
            allow_unrelated_job_failures=True,
        )
        jobs = {
            "1": {
                "jobs": [
                    {"name": "build", "conclusion": "success"},
                    {"name": "capture", "conclusion": None},
                ]
            }
        }
        selection = select_producer_run(
            [_run(1, conclusion="failure")], expectation, jobs_by_run=jobs
        )
        assert selection.outcome == "not_found"
        assert any("no conclusion yet: capture" in line for line in selection.rejected)


class TestRecencyOrdering:
    """ "Newest" must not depend on the order the API happened to return.

    The runs endpoint returns newest first, so a stable sort over candidates
    all keyed the same made ``[-1]`` pick the *oldest* of that group.
    """

    def test_a_row_without_a_run_number_never_outranks_one_with_it(self) -> None:
        numbered = _run(1, number=5)
        unnumbered = _run(2)
        del unnumbered["run_number"]
        for candidates in ([numbered, unnumbered], [unnumbered, numbered]):
            selection = select_producer_run(candidates, _EXPECT)
            assert selection.run is not None
            assert selection.run.run_id == "1", candidates

    def test_ties_break_on_the_monotonic_run_id(self) -> None:
        older = _run(100)
        newer = _run(200)
        for candidate in (older, newer):
            del candidate["run_number"]
        for candidates in ([older, newer], [newer, older]):
            selection = select_producer_run(candidates, _EXPECT)
            assert selection.run is not None
            assert selection.run.run_id == "200", candidates

    def test_selection_is_independent_of_input_order(self) -> None:
        """Stated as a property over every permutation, not one arrangement."""
        import itertools

        candidates = [_run(1, number=3), _run(2, number=9), _run(3, number=7)]
        winners = {
            (selection.run.run_id if selection.run else None)
            for permutation in itertools.permutations(candidates)
            for selection in [select_producer_run(list(permutation), _EXPECT)]
        }
        assert winners == {"2"}

    def test_a_non_integer_run_number_is_not_treated_as_one(self) -> None:
        bogus = _run(1)
        bogus["run_number"] = "12"
        real = _run(2, number=4)
        selection = select_producer_run([bogus, real], _EXPECT)
        assert selection.run is not None and selection.run.run_id == "2"


class TestMalformedJobs:
    """A jobs document that cannot be read satisfies nothing."""

    @pytest.mark.parametrize("document", [None, "text", {"jobs": "text"}, 7])
    def test_a_malformed_jobs_document_fails_every_requirement(
        self, document: Any
    ) -> None:
        failed, absent, unfinished = required_job_failures(document, ["build"])
        assert absent == ["build"]
        assert unfinished == []


class TestVerifyBaselineSourceShell:
    """``actions/verify-baseline-source/run.sh`` against a scripted ``gh``.

    The decisions above are pure and tested directly; what this class covers is
    the shell that *feeds* them, and specifically the one distinction it is
    responsible for keeping: a 404 (this ref does not exist) is a normal answer,
    while a network/auth failure is an operational one. ``gh`` exits 1 for both.

    It also pins that neither case crashes the step. The first version of this
    script read the status with ``$(gh ... ; echo $?)``, and command
    substitution inherits ``set -e`` -- so a failing ``gh`` aborted the subshell
    before the status was echoed and took the whole script with it, turning
    "this is a branch, not a tag" into a hard failure.
    """

    @staticmethod
    def _gh_stub(directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stub = directory / "gh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'case "$2" in\n'
            '  */git/ref/tags/1.5.2) echo \'{"object":{"sha":"TAGOBJ","type":"tag"}}\'; exit 0 ;;\n'
            '  */git/tags/TAGOBJ) echo \'{"object":{"sha":"COMMITX","type":"commit"}}\'; exit 0 ;;\n'
            '  */git/ref/tags/light) echo \'{"object":{"sha":"COMMITY","type":"commit"}}\'; exit 0 ;;\n'
            '  */git/ref/tags/main) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;\n'
            '  */git/ref/tags/boom) echo "gh: connection reset by peer" >&2; exit 1 ;;\n'
            '  *) echo "unexpected: $*" >&2; exit 1 ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return directory

    def _run(self, tmp_path: Path, tag: str, built_sha: str = "") -> dict[str, str]:
        require_bash()
        stub_dir = self._gh_stub(tmp_path / "bin")
        github_output = tmp_path / "gh_output"
        github_output.write_text("")
        env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env.update(
            {
                "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(github_output),
                "INPUT_MODE": "tag",
                "INPUT_TAG": tag,
                "INPUT_BUILT_SHA": built_sha,
                "INPUT_EXPECT_REPOSITORY": "o/r",
            }
        )
        result = subprocess.run(
            [bash_executable(), str(_RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=False,
        )
        outputs = {"__rc__": str(result.returncode)}
        for line in github_output.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                outputs[key] = value
        return outputs

    @pytest.mark.skipif(not _RUN_SH.is_file(), reason="run.sh not found")
    def test_an_annotated_tag_is_fetched_and_peeled(self, tmp_path: Path) -> None:
        outputs = self._run(tmp_path, "1.5.2", "COMMITX")
        assert outputs["__rc__"] == "0"
        assert outputs["outcome"] == "tag"
        assert outputs["eligible"] == "true"
        assert outputs["commit-sha"] == "COMMITX"
        assert outputs["annotated"] == "true"

    @pytest.mark.skipif(not _RUN_SH.is_file(), reason="run.sh not found")
    def test_a_lightweight_tag_needs_no_second_call(self, tmp_path: Path) -> None:
        outputs = self._run(tmp_path, "light", "COMMITY")
        assert outputs["outcome"] == "tag"
        assert outputs["annotated"] == "false"

    @pytest.mark.skipif(not _RUN_SH.is_file(), reason="run.sh not found")
    def test_a_wrong_commit_is_ineligible_not_a_crash(self, tmp_path: Path) -> None:
        outputs = self._run(tmp_path, "1.5.2", "SOMETHINGELSE")
        assert outputs["__rc__"] == "0"
        assert outputs["outcome"] == "tag-commit-mismatch"
        assert outputs["eligible"] == "false"

    @pytest.mark.skipif(not _RUN_SH.is_file(), reason="run.sh not found")
    def test_a_404_reads_as_not_a_tag(self, tmp_path: Path) -> None:
        outputs = self._run(tmp_path, "main")
        assert outputs["__rc__"] == "0"
        assert outputs["outcome"] == "not_a_tag"
        assert outputs["eligible"] == "false"

    @pytest.mark.skipif(not _RUN_SH.is_file(), reason="run.sh not found")
    def test_a_transport_failure_reads_as_lookup_failed(self, tmp_path: Path) -> None:
        """Same ``gh`` exit code as the 404 above, different meaning.

        Collapsing them is how a transient API error becomes a silent "no
        baseline, therefore compatible".
        """
        outputs = self._run(tmp_path, "boom")
        assert outputs["__rc__"] == "0"
        assert outputs["outcome"] == "lookup_failed"
        assert outputs["eligible"] == "false"
