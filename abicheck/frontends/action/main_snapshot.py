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

"""Which default-branch snapshot a pull request's delta is measured against.

Plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md``. Two decisions,
both pure functions over facts the Action's shell already fetched (git
parents/merge-bases, the artifacts and workflow-runs API documents), so every
rule is testable with no credentials -- the same split as
:mod:`~abicheck.frontends.action.baseline_source`:

1. :func:`select_base_commit` -- *which commit* the PR build sits on. The
   delta is only this PR's changes if the base is **exactly** the code the
   build was produced on top of; an older snapshot leaks other commits into
   the delta, a newer one hides changes. So the answer is an exact commit or
   a named refusal, never "the nearest one".
2. :func:`resolve_snapshot` -- *which stored snapshot* describes that commit,
   and, when none does, which of the distinct reasons applies. Every reason is
   its own :class:`DeltaOutcome`; none of them reads as "no changes"
   (ADR-065: absent is not removed).
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .run_selection import (
    RunExpectation,
    SourceRun,
    SourceRunRejected,
    verify_source_run,
)

__all__ = [
    "BaseSelection",
    "COMMENT_ONLY_OUTCOMES",
    "delta_notes",
    "OPERAND_MATCH_NOTES",
    "DeltaOutcome",
    "SNAPSHOT_ARTIFACT_PREFIX",
    "SnapshotExpectation",
    "SnapshotResolution",
    "outcome_message",
    "resolve_snapshot",
    "select_base_commit",
    "snapshot_artifact_name",
]

#: Every snapshot artifact is ``<prefix><full sha>[-<profile>]``.
SNAPSHOT_ARTIFACT_PREFIX = "abicheck-main-"

#: Events that may produce a default-branch snapshot. ``pull_request`` and
#: ``pull_request_target`` are deliberately absent (ADR-047 section 12): a
#: pull request must never be able to write what other PRs are measured
#: against.
SNAPSHOT_PRODUCER_EVENTS = ("push", "workflow_dispatch", "schedule")


class DeltaOutcome(enum.StrEnum):
    """One answer to "what is this PR's delta measured against?"."""

    RESOLVED = "resolved"
    #: Base not resolvable from the checkout: not a merge commit, or the
    #: history needed for it was not fetched.
    BASE_UNKNOWN = "base_unknown"
    BASE_AMBIGUOUS = "base_ambiguous"
    BASE_PENDING = "base_pending"
    BASE_FAILED = "base_failed"
    BASE_NOT_SNAPSHOTTED = "base_not_snapshotted"
    BASE_EXPIRED = "base_expired"
    #: A trusted run succeeded for the base, but no artifact is listed: it
    #: expired (the API no longer lists expired artifacts), was deleted, or
    #: was never uploaded. Which one is not knowable from the API.
    SNAPSHOT_MISSING = "snapshot_missing"
    UNTRUSTED_SOURCE = "untrusted_source"
    LOOKUP_FAILED = "lookup_failed"


_MESSAGES: dict[DeltaOutcome, str] = {
    DeltaOutcome.RESOLVED: "Changes in this {subject} (vs `{branch}@{sha7}`).",
    DeltaOutcome.BASE_UNKNOWN: (
        "Delta unavailable: the base commit could not be determined from this "
        "checkout. Check out the pull request merge commit with `fetch-depth: 2` "
        "(or `0` in head mode)."
    ),
    DeltaOutcome.BASE_AMBIGUOUS: (
        "Delta unavailable: this branch has several merge-bases with "
        "`{branch}`. Rebase onto or merge `{branch}`."
    ),
    DeltaOutcome.BASE_PENDING: (
        "Delta unavailable: the `{branch}@{sha7}` snapshot is still being "
        "built. Re-run this job when it finishes."
    ),
    DeltaOutcome.BASE_FAILED: (
        "Delta unavailable: the snapshot run for `{branch}@{sha7}` did not succeed."
    ),
    DeltaOutcome.BASE_NOT_SNAPSHOTTED: (
        "Delta unavailable: no snapshot was produced for `{branch}@{sha7}` "
        "(the snapshot workflow did not run for it, or the cache has no entry)."
    ),
    DeltaOutcome.BASE_EXPIRED: (
        "Delta unavailable: the `{branch}@{sha7}` snapshot expired. Push to this "
        "branch or use *Update branch* (a re-run reuses the old merge commit)."
    ),
    DeltaOutcome.SNAPSHOT_MISSING: (
        "Delta unavailable: the snapshot run for `{branch}@{sha7}` succeeded but "
        "no snapshot artifact is listed (expired, deleted, or never uploaded). "
        "Push to this branch or use *Update branch* to measure against a newer base."
    ),
    DeltaOutcome.UNTRUSTED_SOURCE: (
        "Delta unavailable: a snapshot for `{branch}@{sha7}` exists but was not "
        "produced by the configured snapshot workflow on `{branch}`."
    ),
    DeltaOutcome.LOOKUP_FAILED: (
        "Delta unavailable: the snapshot lookup failed (API error)."
    ),
}


def outcome_message(
    outcome: DeltaOutcome, *, sha: str = "", branch: str = "main", subject: str = "PR"
) -> str:
    """The one-line comment/summary text for *outcome*."""
    return _MESSAGES[outcome].format(
        sha7=sha[:7] or "?", branch=branch, subject=subject
    )


def snapshot_artifact_name(sha: str, profile: str = "") -> str:
    """The artifact name a snapshot of commit *sha* is stored under."""
    sha = sha.strip().lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError(f"not a full commit SHA: {sha!r}")
    suffix = f"-{profile}" if profile else ""
    if profile and not all(
        (ch.isascii() and ch.isalnum()) or ch in "._-" for ch in profile
    ):
        raise ValueError(f"profile {profile!r} has characters outside [A-Za-z0-9._-]")
    return f"{SNAPSHOT_ARTIFACT_PREFIX}{sha}{suffix}"


# ── 1. base commit ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BaseSelection:
    outcome: DeltaOutcome
    sha: str = ""
    #: ``merge-commit`` | ``head`` | ``merge-group`` -- what rule chose it.
    rule: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is DeltaOutcome.RESOLVED


def select_base_commit(
    mode: str,
    *,
    head_parents: Sequence[str] = (),
    merge_bases: Sequence[str] = (),
    merge_group_base: str = "",
) -> BaseSelection:
    """Choose the commit the PR build sits on.

    *mode*: ``merge-commit`` (the checkout is the PR merge commit -- base is
    its **first parent**, the base-branch tip GitHub merged onto), ``head``
    (the checkout is the PR head -- base is its unique merge-base with the
    default branch) or ``merge-group`` (base is the queue entry's
    ``base_sha``).

    *head_parents* is ``git rev-list --parents -n1 HEAD`` minus HEAD itself;
    *merge_bases* is ``git merge-base --all <default> HEAD``.
    """
    if mode == "merge-group":
        if merge_group_base:
            return BaseSelection(DeltaOutcome.RESOLVED, merge_group_base, mode)
        return BaseSelection(
            DeltaOutcome.BASE_UNKNOWN,
            rule=mode,
            message="the merge_group event has no base_sha",
        )
    if mode == "merge-commit":
        if len(head_parents) == 2:
            return BaseSelection(DeltaOutcome.RESOLVED, head_parents[0], mode)
        if not head_parents:
            reason = "the parent of HEAD is not in the local history (shallow checkout)"
        else:
            reason = (
                f"HEAD has {len(head_parents)} parent(s), so it is not a pull "
                "request merge commit -- use mode 'head' for a head checkout"
            )
        return BaseSelection(DeltaOutcome.BASE_UNKNOWN, rule=mode, message=reason)
    if mode == "head":
        unique = list(dict.fromkeys(merge_bases))
        if len(unique) == 1:
            return BaseSelection(DeltaOutcome.RESOLVED, unique[0], mode)
        if not unique:
            return BaseSelection(
                DeltaOutcome.BASE_UNKNOWN,
                rule=mode,
                message="no merge-base in the local history (fetch the default branch with full depth)",
            )
        return BaseSelection(
            DeltaOutcome.BASE_AMBIGUOUS,
            rule=mode,
            message=f"{len(unique)} merge-bases: {', '.join(b[:7] for b in unique)}",
        )
    raise ValueError(
        f"unknown base mode {mode!r}; expected merge-commit, head or merge-group"
    )


# ── 2. stored snapshot ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotExpectation:
    repository: str
    #: The snapshot workflow's file path or name.
    workflow: str
    #: The default branch the producer must have run on.
    branch: str
    base_sha: str
    artifact_name: str


@dataclass(frozen=True)
class SnapshotResolution:
    outcome: DeltaOutcome
    artifact_id: str = ""
    run_id: str = ""
    message: str = ""
    rejected: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.outcome is DeltaOutcome.RESOLVED


def _runs_by_id(runs: Sequence[Any]) -> dict[str, tuple[Any, SourceRun]]:
    out: dict[str, tuple[Any, SourceRun]] = {}
    for raw in runs:
        try:
            run = SourceRun.from_api(raw)
        except SourceRunRejected:
            continue
        if run.run_id:
            out[run.run_id] = (raw, run)
    return out


def _producer_problem(run: SourceRun, expected: SnapshotExpectation) -> str:
    """Why *run* may not supply the snapshot, or ``""`` when it may."""
    try:
        verify_source_run(
            run,
            RunExpectation(
                repository=expected.repository,
                workflow=expected.workflow,
                allowed_conclusions=(),
            ),
        )
    except SourceRunRejected as exc:
        return f"{exc.code}: {exc.message}"
    if run.event not in SNAPSHOT_PRODUCER_EVENTS:
        return f"wrong-event: triggered by {run.event or '(unknown)'!r}"
    if run.head_branch != expected.branch:
        return f"branch-mismatch: ran on {run.head_branch or '(none)'!r}, needed {expected.branch!r}"
    if run.head_sha != expected.base_sha:
        return f"head-sha-mismatch: built {run.head_sha or '(none)'}, needed {expected.base_sha}"
    return ""


def resolve_snapshot(
    artifacts: Sequence[Any],
    runs: Sequence[Any],
    expected: SnapshotExpectation,
    *,
    lookup_failed: bool = False,
) -> SnapshotResolution:
    """Pick the snapshot artifact for ``expected.base_sha``.

    *artifacts* are ``GET .../actions/artifacts?name=<name>`` rows. *runs* are
    the snapshot workflow's runs at ``head_sha=<base>`` in **every** status,
    so a run still in progress can be told apart from one that never ran.

    An artifact is eligible only when its name matches exactly, it has not
    expired, and the run that uploaded it is one of *runs* and passes
    :func:`_producer_problem` with conclusion ``success``. Several eligible
    artifacts (a re-run of the snapshot workflow for the same commit) were all
    built from the base by the trusted workflow; the newest is taken and every
    other one is listed in ``rejected`` as superseded. Their archive digests
    cannot be compared instead: an artifact zip records upload timestamps, so
    two uploads of identical content never share one.
    """
    if lookup_failed:
        return SnapshotResolution(
            DeltaOutcome.LOOKUP_FAILED, message="artifact or run lookup failed"
        )
    by_id = _runs_by_id(runs)
    rejected: list[str] = []
    eligible: list[Mapping[str, Any]] = []
    saw_expired = saw_untrusted = False
    for row in artifacts:
        if not isinstance(row, Mapping) or row.get("name") != expected.artifact_name:
            continue
        label = f"artifact {row.get('id', '?')}"
        run_ref = row.get("workflow_run")
        run_id = str(run_ref.get("id", "")) if isinstance(run_ref, Mapping) else ""
        entry = by_id.get(run_id)
        if entry is None:
            saw_untrusted = True
            rejected.append(
                f"{label}: unknown-producer: run {run_id or '?'} is not a run of {expected.workflow} at {expected.base_sha[:7]}"
            )
            continue
        problem = _producer_problem(entry[1], expected)
        if problem:
            saw_untrusted = True
            rejected.append(f"{label}: {problem}")
            continue
        if entry[1].conclusion != "success":
            rejected.append(
                f"{label}: run {run_id} concluded {entry[1].conclusion or '(none yet)'!r}"
            )
            continue
        if row.get("expired"):
            saw_expired = True
            rejected.append(f"{label}: expired")
            continue
        eligible.append(row)

    if eligible:
        chosen = max(eligible, key=lambda row: int(row.get("id", 0) or 0))
        rejected.extend(
            f"artifact {row.get('id', '?')}: superseded by newer upload {chosen.get('id')}"
            for row in eligible
            if row is not chosen
        )
        run_ref = chosen.get("workflow_run")
        return SnapshotResolution(
            DeltaOutcome.RESOLVED,
            artifact_id=str(chosen.get("id", "")),
            run_id=str(run_ref.get("id", "")) if isinstance(run_ref, Mapping) else "",
            message=f"artifact {chosen.get('id')} from run {run_ref.get('id') if isinstance(run_ref, Mapping) else '?'}",
            rejected=tuple(rejected),
        )

    trusted_runs = [
        run for _raw, run in by_id.values() if not _producer_problem(run, expected)
    ]
    if any(run.status and run.status != "completed" for run in trusted_runs):
        outcome = DeltaOutcome.BASE_PENDING
    elif saw_expired:
        outcome = DeltaOutcome.BASE_EXPIRED
    elif trusted_runs and not any(run.conclusion == "success" for run in trusted_runs):
        outcome = DeltaOutcome.BASE_FAILED
    elif trusted_runs and not saw_untrusted:
        # A trusted run succeeded for this exact commit, yet no artifact is
        # listed. Expired artifacts are no longer listed (2026-09), but a
        # deleted or never-uploaded one looks the same, so this is not
        # reported as `base_expired`.
        outcome = DeltaOutcome.SNAPSHOT_MISSING
    elif saw_untrusted:
        outcome = DeltaOutcome.UNTRUSTED_SOURCE
    else:
        outcome = DeltaOutcome.BASE_NOT_SNAPSHOTTED
    return SnapshotResolution(outcome, message=outcome.value, rejected=tuple(rejected))


# ── the PR comment's notes ──────────────────────────────────────────────────

#: Decided by the main Action after the lookup, not by it: the snapshot
#: resolved but the comparison is not one a single-library delta can describe,
#: or the delta rerun produced no report.
COMMENT_ONLY_OUTCOMES = {
    "unsupported_operand": (
        "Delta unavailable: delta-baseline compares one library; this is a "
        "directory/package comparison."
    ),
    "rerun_failed": (
        "Delta unavailable: the comparison against the default-branch snapshot "
        "produced no report."
    ),
    # Operand identity (abicheck.workflows.delta_operand): the snapshot is not
    # shown to be the same library as this PR's. Never compared -- an
    # unrelated snapshot would read as every symbol removed (ADR-065).
    "operand_unmatched": (
        "Delta unavailable: the default-branch snapshot is of a different "
        "library than this PR's (no shared SONAME/install name, path or file "
        "name). If it was renamed, pair the two with `delta-operand-map`."
    ),
    "operand_ambiguous": (
        "Delta unavailable: the default-branch snapshot has this PR's library "
        "file name but an unrelated SONAME/install name. Declare the pairing "
        "with `delta-operand-map` if they are the same library."
    ),
    "operand_unreadable": (
        "Delta unavailable: the identity of the default-branch snapshot or of "
        "this PR's library could not be read."
    ),
}

#: A matched pairing worth stating in the comment (the trivial ones -- same
#: loader name, same file name -- need no note). Keyed by
#: :class:`abicheck.workflows.delta_operand.MatchRule` values.
OPERAND_MATCH_NOTES = {
    "declared": (
        "The snapshot's library is paired with this PR's by `delta-operand-map`."
    ),
    "loader_name_version": (
        "The snapshot's library and this PR's differ only by SONAME/install-name "
        "version; the delta reports that version change."
    ),
    "file_name_version": (
        "The snapshot's library and this PR's differ only by file-name version."
    ),
}
_KNOWN_MATCH_RULES = {"", "loader_name", "file_name", *OPERAND_MATCH_NOTES}

_GATE_VERDICT = re.compile(r"^[A-Z][A-Z_]{0,39}$")


def delta_notes(
    outcome: str,
    *,
    base_sha: str = "",
    branch: str = "main",
    gate_verdict: str = "",
    merge_group: bool = False,
    operand_match: str = "",
) -> list[str]:
    """The quoted lines a delta-baseline comment carries.

    The outcome line, then -- for a ``resolved`` delta whose operand pairing
    is not trivial (*operand_match*, a ``MatchRule`` value) -- how the
    snapshot's library was paired with this PR's, then the gate line.

    The one place their wording lives: the main Action's direct comment and
    the trusted fork publisher both call this, and the publisher calls it
    from *structured* fields of an analysis context a fork wrote -- never
    from text the fork chose. An unknown outcome is refused, not rendered.
    """
    if outcome in COMMENT_ONLY_OUTCOMES:
        first = COMMENT_ONLY_OUTCOMES[outcome]
    else:
        try:
            parsed = DeltaOutcome(outcome)
        except ValueError as exc:
            raise ValueError(f"unknown delta outcome {outcome!r}") from exc
        first = outcome_message(
            parsed,
            sha=base_sha,
            branch=branch or "main",
            subject="merge-queue entry" if merge_group else "PR",
        )
    if operand_match not in _KNOWN_MATCH_RULES:
        raise ValueError(f"unknown operand match rule {operand_match!r}")
    if first.startswith("Delta unavailable"):
        first += " Showing the release-baseline comparison."
    lines = [first]
    if outcome == DeltaOutcome.RESOLVED.value and operand_match in OPERAND_MATCH_NOTES:
        lines.append(OPERAND_MATCH_NOTES[operand_match])
    verdict = gate_verdict if _GATE_VERDICT.match(gate_verdict or "") else "unknown"
    gate = (
        f"Release gate: {verdict} (the comparison against the configured "
        "baseline); this check's status comes from the gate."
    )
    return [*lines, gate]
