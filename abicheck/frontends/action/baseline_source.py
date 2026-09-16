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

"""Decide *which* producer run a baseline may be taken from, and whether a
name really is a release tag.

``actions/resolve-baseline`` resolves a baseline **within** an
already-staged set. Getting that set staged in the first place is the other
half, and it is a trust decision rather than a lookup: a baseline is what a
pull request's changes are measured against, so a run that failed, a run of a
different workflow, a run from a fork, or a "tag" that is really a branch
must not become one.

This module is that decision, expressed the same way
:mod:`~abicheck.frontends.action.run_selection` expresses the publication
boundary -- pure functions over already-fetched GitHub API documents, so the
Action's shell does the network calls and every rule here is testable with no
credentials. It reuses that module's :class:`~run_selection.SourceRun` and
:func:`~run_selection.verify_source_run` rather than restating what a run must
be; what it adds is the three questions those do not answer:

* **Is this name a tag at all?** Resolved through ``git/ref/tags/<name>``
  explicitly. A generic revision endpoint (``commits/<name>``) accepts any
  revision name and will happily resolve a *branch*, which is how a
  branch-named build can publish itself as a release baseline. An annotated
  tag is peeled to the commit it points at; the name is never required to look
  like a version, because plenty of projects tag ``1.5.2`` with no ``v``.
* **Does the capture belong to the intended commit?** The tag's peeled commit
  must be the one the producing run actually built.
* **Is the producer eligible?** Exact head SHA alone is not eligibility: a run
  at that SHA can come from any branch, any event, and can have failed --
  and a capture step that deliberately runs after a test failure means a
  failed run still has an artifact to offer.

Every refusal carries a code, and "the lookup itself failed" is deliberately a
different code from "there is no baseline": the first is an operational
failure to retry or report, the second is a real, expected lifecycle state
(the first release, a repository that has not published one yet). Collapsing
them is how a transient API error becomes a silent "no baseline, so
compatible".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .run_selection import SourceRun, SourceRunRejected, verify_source_run

__all__ = [
    "BaselineProducerExpectation",
    "ProducerSelection",
    "TagResolution",
    "required_job_failures",
    "resolve_tag",
    "select_producer_run",
    "verify_tag_commit",
]


# ── tags ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TagResolution:
    """What ``git/ref/tags/<name>`` said about a name."""

    #: ``tag`` when the name is a real tag of the repository, ``not_a_tag``
    #: when the ref does not exist, ``lookup_failed`` when the question could
    #: not be asked.
    outcome: str
    name: str = ""
    #: The commit the tag ultimately points at -- peeled, for an annotated tag.
    commit_sha: str = ""
    #: ``True`` when peeling was needed, purely so a caller can say so.
    annotated: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "tag"


def resolve_tag(
    name: str,
    ref_document: Any,
    *,
    tag_object_document: Any = None,
    lookup_failed: bool = False,
) -> TagResolution:
    """Interpret a ``GET /repos/{o}/{r}/git/ref/tags/{name}`` response.

    *ref_document* is that response, or ``None`` when the ref does not exist.
    *tag_object_document* is the ``git/tags/{sha}`` response, required only
    when the ref points at a tag **object** (an annotated tag) -- the caller
    fetches it because only the first response says whether it is needed.

    *lookup_failed* is the caller's own signal that the request errored rather
    than 404'd. It is a separate parameter rather than something inferred from
    a missing document precisely because the two are indistinguishable at the
    shell level unless the caller keeps them apart deliberately.
    """
    if lookup_failed:
        return TagResolution(
            outcome="lookup_failed",
            name=name,
            message=(
                f"could not determine whether {name!r} is a tag (the API lookup "
                "failed). This is an operational failure, not a missing baseline."
            ),
        )
    if ref_document is None:
        return TagResolution(
            outcome="not_a_tag",
            name=name,
            message=(
                f"{name!r} is not a tag of this repository. A branch, a bare "
                "revision, or a tag that was deleted all land here -- none of "
                "them may publish a release-contract baseline."
            ),
        )
    if not isinstance(ref_document, Mapping):
        return TagResolution(
            outcome="lookup_failed",
            name=name,
            message="the git-ref response is not a JSON object",
        )
    obj = ref_document.get("object")
    if not isinstance(obj, Mapping):
        return TagResolution(
            outcome="lookup_failed",
            name=name,
            message="the git-ref response carries no object block",
        )
    sha = str(obj.get("sha", "") or "")
    kind = str(obj.get("type", "") or "")
    if not sha:
        return TagResolution(
            outcome="lookup_failed", name=name, message="the git ref names no sha"
        )
    if kind != "tag":
        # A lightweight tag points straight at the commit.
        return TagResolution(
            outcome="tag", name=name, commit_sha=sha, message=f"{name} -> {sha}"
        )
    if not isinstance(tag_object_document, Mapping):
        return TagResolution(
            outcome="lookup_failed",
            name=name,
            message=(
                f"{name!r} is an annotated tag, whose ref points at a tag object "
                f"({sha}) rather than a commit. Fetch git/tags/{sha} and pass it "
                "as tag_object_document -- treating the tag-object sha as the "
                "commit would compare against an object that is not a commit at all."
            ),
        )
    peeled = tag_object_document.get("object")
    peeled_sha = str(peeled.get("sha", "") or "") if isinstance(peeled, Mapping) else ""
    if not peeled_sha:
        return TagResolution(
            outcome="lookup_failed",
            name=name,
            message=f"the tag object {sha} names no target commit",
        )
    return TagResolution(
        outcome="tag",
        name=name,
        commit_sha=peeled_sha,
        annotated=True,
        message=f"{name} -> (annotated {sha}) -> {peeled_sha}",
    )


def verify_tag_commit(resolution: TagResolution, built_sha: str) -> None:
    """Refuse unless the capture was taken from the commit *resolution* names.

    Raises :class:`SourceRunRejected` -- the same exception the publication
    boundary raises, so a caller has one refusal vocabulary for "this evidence
    is not what it claims to be", whichever boundary noticed.
    """
    if not resolution.ok:
        raise SourceRunRejected(resolution.outcome, resolution.message)
    if not built_sha:
        raise SourceRunRejected(
            "tag-commit-unknown",
            f"tag {resolution.name!r} resolves to {resolution.commit_sha}, but the "
            "run being published names no commit it built.",
        )
    if resolution.commit_sha != built_sha:
        raise SourceRunRejected(
            "tag-commit-mismatch",
            f"tag {resolution.name!r} points at {resolution.commit_sha}, but the "
            f"capture being published was taken from {built_sha}. Publishing it "
            "would label one commit's ABI as another's for every future "
            "comparison against this release.",
        )


# ── producer runs ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BaselineProducerExpectation:
    """What a run must be before its capture may become a baseline."""

    repository: str = ""
    workflow: str = ""
    event: str = ""
    #: The commit the baseline must describe. For an ``accepted-main``
    #: channel this is the pull request's own base SHA -- comparing against a
    #: baseline built from a *later* default-branch commit compares across two
    #: histories, not against the PR's base.
    head_sha: str = ""
    #: The branch the run must have been triggered on. A push run at the right
    #: SHA can still come from an unrelated branch.
    head_branch: str = ""
    allowed_conclusions: tuple[str, ...] = ("success",)
    #: Job names that must individually have concluded ``success``. Empty
    #: means the run-level conclusion is the only requirement.
    required_jobs: tuple[str, ...] = ()
    #: When ``True``, a run whose overall conclusion is not allowed may still
    #: be selected as long as every :attr:`required_jobs` entry succeeded --
    #: the explicit policy for "unrelated matrix legs may fail". Without it,
    #: an unrelated failure is never silently accepted.
    allow_unrelated_job_failures: bool = False


@dataclass(frozen=True)
class ProducerSelection:
    """Which run was chosen, or why none was."""

    #: ``resolved`` | ``not_found`` | ``lookup_failed`` | ``ineligible``
    outcome: str
    run: SourceRun | None = None
    message: str = ""
    #: One line per candidate that was considered and rejected, so a caller
    #: reporting "no eligible baseline" can say what it *did* see. Silence
    #: here is what makes a mis-scoped query look like an empty history.
    rejected: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome == "resolved"


def required_job_failures(
    jobs_document: Any, required: Sequence[str]
) -> tuple[list[str], list[str]]:
    """``(failed, absent)`` among *required* job names in *jobs_document*.

    *jobs_document* is a ``GET .../runs/{id}/jobs`` response (or its
    ``jobs`` array). A required job that is **absent** is reported separately
    from one that failed: "the capture job did not run" and "the capture job
    ran and failed" are different facts, and an absent job silently treated as
    passing is how a run with no capture at all becomes a baseline.
    """
    if isinstance(jobs_document, Mapping):
        rows = jobs_document.get("jobs")
    else:
        rows = jobs_document
    by_name: dict[str, str] = {}
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        for row in rows:
            if isinstance(row, Mapping):
                name = str(row.get("name", "") or "")
                if name:
                    by_name[name] = str(row.get("conclusion", "") or "")
    failed = [name for name in required if by_name.get(name, "") not in ("", "success")]
    absent = [name for name in required if name not in by_name]
    return failed, absent


def select_producer_run(
    candidates: Sequence[Any],
    expected: BaselineProducerExpectation,
    *,
    lookup_failed: bool = False,
    jobs_by_run: Mapping[str, Any] | None = None,
) -> ProducerSelection:
    """Choose the newest eligible run among *candidates*.

    *candidates* are ``GET .../workflows/{file}/runs`` rows. *jobs_by_run*
    supplies each run's jobs document, needed only when
    :attr:`~BaselineProducerExpectation.required_jobs` is set.

    "Newest" is by run number, descending, so a re-run of an older commit
    cannot displace the current one. A run that fails any check is skipped
    *with its reason recorded* rather than dropped.
    """
    if lookup_failed:
        return ProducerSelection(
            outcome="lookup_failed",
            message=(
                "the producer-run lookup failed (API error). Reporting an "
                "unavailable baseline, not a clean result."
            ),
        )

    rejected: list[str] = []
    eligible: list[tuple[int, SourceRun]] = []
    for index, raw in enumerate(candidates):
        try:
            run = SourceRun.from_api(raw)
        except SourceRunRejected as exc:
            rejected.append(f"candidate {index}: {exc.code}: {exc.message}")
            continue
        label = f"run {run.run_id or index}"

        # The run-level conclusion is checked separately from the rest so an
        # explicit "unrelated matrix legs may fail" policy can override it --
        # and only it. Every identity check below stays mandatory.
        conclusion_expectation = expected.allowed_conclusions
        if expected.allow_unrelated_job_failures and expected.required_jobs:
            conclusion_expectation = ()

        try:
            verify_source_run(
                run,
                _run_expectation(expected, conclusion_expectation),
            )
        except SourceRunRejected as exc:
            rejected.append(f"{label}: {exc.code}: {exc.message}")
            continue

        if expected.head_sha and run.head_sha != expected.head_sha:
            rejected.append(
                f"{label}: head-sha-mismatch: built {run.head_sha or '<none>'}, "
                f"needed {expected.head_sha}"
            )
            continue
        if expected.head_branch and run.head_branch != expected.head_branch:
            rejected.append(
                f"{label}: branch-mismatch: triggered on "
                f"{run.head_branch or '<none>'}, needed {expected.head_branch}"
            )
            continue
        if run.status and run.status != "completed":
            rejected.append(f"{label}: not-completed: status {run.status!r}")
            continue
        if expected.required_jobs:
            jobs = (jobs_by_run or {}).get(run.run_id)
            if jobs is None:
                rejected.append(
                    f"{label}: jobs-unavailable: required jobs were declared but "
                    "this run's job list was not supplied, so they cannot be "
                    "checked. An unchecked requirement is not a satisfied one."
                )
                continue
            failed, absent = required_job_failures(jobs, expected.required_jobs)
            if failed or absent:
                detail = []
                if failed:
                    detail.append(f"failed: {', '.join(failed)}")
                if absent:
                    detail.append(f"never ran: {', '.join(absent)}")
                rejected.append(f"{label}: required-job: {'; '.join(detail)}")
                continue

        number = raw.get("run_number") if isinstance(raw, Mapping) else None
        eligible.append((number if isinstance(number, int) else 0, run))

    if not eligible:
        return ProducerSelection(
            outcome="not_found",
            message=(
                "no eligible producer run was found. This is a real lifecycle "
                "state (nothing has been published for this revision yet), not an "
                "error -- and not a clean comparison either."
            ),
            rejected=tuple(rejected),
        )
    eligible.sort(key=lambda pair: pair[0])
    chosen = eligible[-1][1]
    return ProducerSelection(
        outcome="resolved",
        run=chosen,
        message=(
            f"selected run {chosen.run_id} (attempt {chosen.run_attempt}) of "
            f"{chosen.workflow_path or chosen.workflow_name} at {chosen.head_sha}"
        ),
        rejected=tuple(rejected),
    )


def _run_expectation(
    expected: BaselineProducerExpectation, conclusions: tuple[str, ...]
) -> Any:
    from .run_selection import RunExpectation

    return RunExpectation(
        repository=expected.repository,
        workflow=expected.workflow,
        event=expected.event,
        allowed_conclusions=conclusions,
    )
