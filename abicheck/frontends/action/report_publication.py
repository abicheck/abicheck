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

"""Render, bound and decide — the whole of ``actions/report`` except the API call.

ADR-073. The publisher Action exists because a fork PR's analysis job must
stay unprivileged: it can build and compare, but it must not hold a token
that can write to the PR. Publication therefore happens in a separate,
trusted ``workflow_run`` job, which holds the token and does no analysis at
all. Everything that Action *decides* is here rather than in its shell, for
three reasons:

* it is testable with no credentials, no network and no runner;
* untrusted report text never reaches a shell word at all -- it is read in
  Python, escaped by the Markdown renderer, and handed to the API as a JSON
  document this module writes;
* the shell that remains is short enough to read, which is the only way the
  "this Action runs no analysis" property stays checkable by eye as well as
  by test.

**Publication failure is not a compatibility result.** The two are reported
on separate channels and never substituted for each other: a failed post
fails the step with its own message and ``posted=false``, and a non-clean
compatibility verdict never fails this Action, because this Action is a
reporter and not a gate.

**Platform limits.** A comment body is capped by GitHub at 65,536 characters
(the API rejects a longer one with ``body is too long``); the underlying
column is 262,144 bytes. A job summary is capped at 1 MiB per step, and
exceeding it drops the summary with an error annotation. This module bounds
both in *bytes*, with headroom, which bounds characters too (a UTF-8 string
never has more characters than bytes) -- so one measurement satisfies both
of a comment's two limits, and the byte one is the one a summary actually
has.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...pr_comment import build_model, render_comment, should_post
from ...pr_comment_sections import _close_open_details

#: GitHub's documented comment-body limit, in characters.
GITHUB_COMMENT_CHAR_LIMIT = 65_536
#: The byte limit behind it (the column is a 262,144-byte ``mediumblob``).
GITHUB_COMMENT_BYTE_LIMIT = 262_144
#: GitHub's documented per-step job-summary limit, in bytes (1 MiB). Over it,
#: the summary upload fails and the content is dropped entirely.
GITHUB_SUMMARY_BYTE_LIMIT = 1024 * 1024

#: Default byte budget for a comment body. Under the character limit even if
#: every byte were its own character, and far under the byte limit -- the
#: headroom absorbs the request envelope and the fact that GitHub's own check
#: has been observed to apply to a compressed size rather than the raw one.
DEFAULT_MAX_COMMENT_BYTES = 60_000
#: Default byte budget for the job summary, with ~12% headroom under 1 MiB.
DEFAULT_MAX_SUMMARY_BYTES = 900_000

#: Opening token of the identity marker. A comment whose body contains it is
#: one of ours *for some identity*; the payload says which.
IDENTITY_MARKER_PREFIX = "<!-- abicheck-report-identity:"
#: Everything between the prefix and the first `-->`. Deliberately not
#: `\{.*?\}`: an identity string containing a closing brace would end that
#: match early, yielding invalid JSON and -- since a body carrying the
#: prefix is ours either way -- an identity of `""` that matches no sticky
#: comment, so the publisher would post a duplicate beside its own previous
#: one. `marker()` escapes `-->` out of the payload, so the first one is
#: always the real terminator.
_IDENTITY_MARKER_RE = re.compile(
    re.escape(IDENTITY_MARKER_PREFIX) + r"\s*(.*?)\s*-->", re.DOTALL
)

POST_MODES = ("always", "changes", "never")


class PublicationError(Exception):
    """A publication-side failure, never a compatibility outcome."""


# ---------------------------------------------------------------------------
# Sticky identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationIdentity:
    """Who this comment belongs to, and which run produced it.

    *identity* is the sticky key: one comment per identity per PR. It
    defaults to the profile name so one PR can carry one comment per
    profile (``linux-gcc``, ``windows-msvc``, ...) without them overwriting
    each other.

    *run_id* and *run_attempt* are the **ordering guard**. Two producer runs
    for the same PR can finish out of order -- a re-run of an older commit,
    a slow matrix leg, a retried publisher -- and without an ordering record
    the last writer wins, which means the *oldest* result can be the one a
    reviewer is left looking at. Recording them in the marker makes the
    comparison possible at all; :func:`decide` is where it is made.

    *head_sha* is recorded for the reader's benefit and for audit. It is
    deliberately **not** part of the ordering comparison: SHAs have no order.
    """

    identity: str
    run_id: str = ""
    run_attempt: int = 1
    head_sha: str = ""

    def marker(self) -> str:
        """The hidden HTML comment carrying this identity.

        ``ensure_ascii=True`` keeps the payload pure ASCII, so the marker
        renders identically whatever the identity string contains, and
        ``sort_keys`` keeps it byte-stable across runs -- a marker that
        reordered itself would make every republish look like a content
        change.
        """
        payload = json.dumps(
            {
                "identity": self.identity,
                "run_id": self.run_id,
                "run_attempt": self.run_attempt,
                "head_sha": self.head_sha,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        # JSON does not escape `>`, so a field containing `-->` would close
        # the HTML comment early and spill the rest of the payload into the
        # rendered body as markup. `\u003e` is a legal JSON escape for the
        # same character, so this round-trips exactly through
        # :meth:`parse` while being inert inside an HTML comment.
        payload = payload.replace("-->", "--\\u003e")
        return f"{IDENTITY_MARKER_PREFIX} {payload} -->"

    @classmethod
    def parse(cls, body: str) -> PublicationIdentity | None:
        """Recover an identity from an existing comment body, or ``None``.

        Returns ``None`` for a body with no marker at all. A body that *has*
        the marker but whose payload does not parse yields an identity with
        empty ordering fields rather than ``None``: the comment is
        demonstrably ours, and treating it as a stranger's would make the
        publisher post a duplicate beside it, which is the one outcome a
        sticky comment exists to prevent.
        """
        match = _IDENTITY_MARKER_RE.search(body)
        if match is None:
            return None
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return cls(identity="")
        if not isinstance(payload, dict):
            return cls(identity="")
        attempt = payload.get("run_attempt")
        return cls(
            identity=str(payload.get("identity", "") or ""),
            run_id=str(payload.get("run_id", "") or ""),
            run_attempt=attempt if isinstance(attempt, int) and attempt > 0 else 1,
            head_sha=str(payload.get("head_sha", "") or ""),
        )

    def order_key(self) -> tuple[int, int] | None:
        """``(run_id, attempt)`` for ordering, or ``None`` when unorderable.

        ``None`` for a non-numeric or absent run id. A missing order is not
        "older" and not "newer": the caller must fall through to publishing
        rather than skipping, because skipping on an unknown order would let
        one malformed marker freeze the comment permanently.
        """
        if not self.run_id.isdigit():
            return None
        return int(self.run_id), self.run_attempt

    def supersedes(self, other: PublicationIdentity) -> bool | None:
        """Whether *self* is a strictly newer run than *other*.

        ``None`` means "cannot tell" -- either side unorderable.
        """
        mine, theirs = self.order_key(), other.order_key()
        if mine is None or theirs is None:
            return None
        return mine > theirs


# ---------------------------------------------------------------------------
# Byte bounding
# ---------------------------------------------------------------------------


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def bound_to_bytes(text: str, max_bytes: int, note: str) -> tuple[str, bool]:
    """*text* cut to *max_bytes* on a line boundary, with *note* appended.

    Returns ``(text, truncated)``. Three properties, each of which a naive
    byte slice gets wrong:

    * **No split codepoint.** The cut lands on a line boundary chosen by
      measuring whole lines, so a multibyte character is never bisected --
      a body cut mid-sequence is rejected or rendered as replacement
      characters, and either way the reader loses the content *and* the
      explanation.
    * **No dangling block.** Any ``<details>`` the cut left open is closed,
      or it swallows the truncation note that follows it.
    * **The note is inside the budget.** Its own bytes are reserved first
      and the head is shrunk until the assembled result fits, rather than a
      fixed reserve being guessed at.

    Truncation is always *disclosed*: the note is appended unconditionally
    when anything was dropped, so a shortened body can never read as a
    complete one with fewer findings.
    """
    if max_bytes <= 0:
        return "", bool(text)
    if _byte_len(text) <= max_bytes:
        return text, False
    budget = max_bytes - _byte_len(note)
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = _byte_len(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    head = "\n".join(kept)
    # Closing the blocks the cut left open costs bytes of its own, and a
    # deeply nested body can need more than any fixed reserve -- shrink
    # until the whole assembled result fits.
    while kept and _byte_len(_close_open_details(head) + note) > max_bytes:
        kept.pop()
        head = "\n".join(kept)
    return _close_open_details(head) + note, True


def _comment_truncation_note(report_url: str | None) -> str:
    where = f" — see the [full report]({report_url})" if report_url else ""
    return (
        "\n\n<sub>⚠️ This comment was truncated to fit GitHub's comment size "
        f"limit; findings below the cut are not shown{where}. The complete "
        "machine-readable report is unaffected.</sub>"
    )


def _summary_truncation_note(report_url: str | None) -> str:
    where = f" — see the [full report]({report_url})" if report_url else ""
    return (
        "\n\n<sub>⚠️ This job summary was truncated to fit GitHub's 1 MiB "
        f"per-step limit; content below the cut is not shown{where}.</sub>"
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedReport:
    """One rendered body and everything the caller needs to decide with it."""

    body: str
    #: Whether the report said anything this ``on`` policy wants published.
    has_content: bool
    truncated: bool
    #: Why nothing was rendered, when ``has_content`` is false.
    reason: str = ""
    #: The same body *before* the comment budget was applied. The job
    #: summary's limit is ~15x the comment's, so bounding the summary from
    #: the already-cut comment would silently give it the comment's budget
    #: and hand it the comment's own truncation notice. Empty when nothing
    #: was rendered; equal to ``body`` whenever no cut was needed.
    full_body: str = ""

    @property
    def body_bytes(self) -> int:
        """The rendered body's size in UTF-8 bytes -- the unit both
        platform limits are bounded against."""
        return _byte_len(self.body)


def render_report(
    report: Mapping[str, Any],
    *,
    identity: PublicationIdentity,
    report_dir: Path | None = None,
    detail: str = "standard",
    on: str = "changes",
    sha: str = "",
    run_label: str | None = None,
    report_url: str | None = None,
    report_artifact_url: str | None = None,
    path_prefix: str = "",
    gate_api_break: bool = False,
    gate_breaking: bool = True,
    max_comment_bytes: int = DEFAULT_MAX_COMMENT_BYTES,
) -> RenderedReport:
    """Render *report* to a bounded, identity-stamped comment body.

    This is the whole of the Action's "analysis": reading an
    already-produced JSON document and formatting it. It builds no snapshot,
    invokes no compiler, and consults nothing outside the report and the
    member reports the report itself names.

    *on* is applied here rather than by the caller so the reason a body is
    empty is recorded alongside it (``never``/``no-changes``) instead of
    being re-derived from an empty string.
    """
    if on not in POST_MODES:
        raise PublicationError(
            f"`on` must be one of {', '.join(POST_MODES)}, not {on!r}"
        )
    if on == "never":
        return RenderedReport(
            body="", has_content=False, truncated=False, reason="never"
        )
    model = build_model(
        dict(report),
        gate_api_break=gate_api_break,
        gate_breaking=gate_breaking,
        path_prefix=path_prefix,
        report_dir=report_dir,
    )
    if not should_post(model, on):
        return RenderedReport(
            body="", has_content=False, truncated=False, reason="no-changes"
        )
    body = render_comment(
        model,
        sha=sha,
        detail=detail,
        run_label=run_label,
        report_url=report_url,
        report_artifact_url=report_artifact_url,
    )
    body = f"{identity.marker()}\n{body}"
    full_body = body
    body, truncated = bound_to_bytes(
        body, max_comment_bytes, _comment_truncation_note(report_url)
    )
    return RenderedReport(
        body=body, has_content=True, truncated=truncated, full_body=full_body
    )


def render_resolution(identity: PublicationIdentity, *, sha: str = "") -> str:
    """The body that *replaces* a stale comment once the report is clean.

    A previously-reported break that has since been fixed must not be left
    on the PR. Deleting the comment would also work, but loses the ordering
    marker -- and with it the ability of a later, slower producer run to
    know it has been superseded -- so the comment is rewritten in place
    instead, keeping its identity and its run record.
    """
    where = f" as of `{sha[:12]}`" if sha else ""
    return (
        f"{identity.marker()}\n"
        f"## ✅ abicheck — previously reported findings are resolved\n\n"
        f"This check has nothing to report{where}. The findings this comment "
        f"previously showed are no longer present in the analysed result.\n"
    )


def render_summary(
    body: str,
    *,
    max_summary_bytes: int = DEFAULT_MAX_SUMMARY_BYTES,
    report_url: str | None = None,
) -> tuple[str, bool]:
    """The job-summary rendering of a rendered body.

    Bounded **independently** of the comment: the two destinations have
    different limits (1 MiB versus 64 KiB), so folding them into one budget
    would either waste the summary's room or overrun the comment's.

    That independence is a property of what the caller passes, not of this
    function -- and it was not true when this docstring first claimed it.
    Callers must pass :attr:`RenderedReport.full_body`, the body *before*
    the comment budget was applied. Handed the bounded ``body`` instead,
    this returns the comment's own truncation, notice and all, and the
    larger budget buys exactly nothing. ``summary_for_plan`` is the
    pairing that gets this right; prefer it to calling this directly.
    Returns ``(text, truncated)``.
    """
    return bound_to_bytes(body, max_summary_bytes, _summary_truncation_note(report_url))


def summary_for_plan(
    rendered: RenderedReport,
    plan: PublicationPlan,
    *,
    max_summary_bytes: int = DEFAULT_MAX_SUMMARY_BYTES,
    report_url: str | None = None,
) -> tuple[str, bool]:
    """The job summary for what *plan* decided to publish.

    The pairing :func:`render_summary` asks for. It picks the right source
    body so the summary is bounded once, from full content, against its own
    budget.

    Note which body that is. ``plan.body`` is *not* the general answer:
    for ``create``/``update`` it holds the comment body, already cut to the
    comment budget, so reading it here reproduces the very defect this
    helper exists to prevent (it did, until the test below caught it). Only
    a ``clear`` authors text of its own -- a resolution notice, which is
    what the comment says and so what the summary must say too.
    """
    source = plan.body if plan.action == "clear" else ""
    if not source:
        source = rendered.full_body or rendered.body
    return render_summary(
        source, max_summary_bytes=max_summary_bytes, report_url=report_url
    )


# ---------------------------------------------------------------------------
# Deciding what to do with it
# ---------------------------------------------------------------------------

#: What the publisher should do. ``skip`` never writes anything.
ACTIONS = ("create", "update", "clear", "skip")


@dataclass(frozen=True)
class ExistingComment:
    """One comment already on the PR, as the API reported it."""

    comment_id: int
    body: str

    @property
    def identity(self) -> PublicationIdentity | None:
        """This comment's recorded identity, or ``None`` if it is not ours."""
        return PublicationIdentity.parse(self.body)


@dataclass(frozen=True)
class PublicationPlan:
    """What to do, to which comment, and why -- decided without a network."""

    action: str
    body: str = ""
    comment_id: int | None = None
    skipped_reason: str = ""

    @property
    def posts(self) -> bool:
        """Whether this plan writes anything to the pull request."""
        return self.action in ("create", "update", "clear")

    def to_dict(self) -> dict[str, Any]:
        """The plan document the Action's shell reads back."""
        return {
            "action": self.action,
            "comment_id": self.comment_id,
            "skipped_reason": self.skipped_reason,
        }


def find_existing(
    comments: list[ExistingComment], identity: PublicationIdentity
) -> ExistingComment | None:
    """Our own previous comment for *identity*, or ``None``.

    Matches on the identity **payload**, not merely on the marker prefix, so
    two profiles publishing to one PR keep two separate sticky comments
    instead of overwriting each other. The *last* match wins, matching the
    root Action's own long-standing behaviour for a PR that somehow carries
    more than one.
    """
    found: ExistingComment | None = None
    for comment in comments:
        parsed = comment.identity
        if parsed is not None and parsed.identity == identity.identity:
            found = comment
    return found


def decide(
    rendered: RenderedReport,
    *,
    identity: PublicationIdentity,
    comments: list[ExistingComment],
    sha: str = "",
) -> PublicationPlan:
    """Turn a rendered body plus the PR's current comments into one action.

    The rules, in the order they apply:

    1. **Staleness wins over everything.** If the existing comment records a
       strictly newer producer run than this one, do nothing -- including
       when this run has content and that one is clean. A late-finishing
       older run overwriting a newer result is silent misinformation, and it
       is the failure mode a rerun of an old commit produces routinely.
       "Cannot tell" (an unorderable marker on either side) is not
       staleness: it falls through and publishes, because a publisher frozen
       by one malformed marker is worse than a duplicate update.
    2. **Nothing to say, and something already said** -> clear it in place.
       This applies under ``on: changes`` too: "the report no longer shows
       this" is itself the news, and leaving an obsolete break on the PR is
       the one thing a sticky comment must not do.
    3. **Nothing to say, and nothing already said** -> stay quiet.
    4. Otherwise create or update.
    """
    existing = find_existing(comments, identity)
    if existing is not None:
        # `find_existing` only ever returns a comment whose marker parsed,
        # so this cannot be `None` -- re-read rather than asserting it,
        # because an `assert` disappears under `-O` and this branch decides
        # whether a newer result gets overwritten.
        previous = existing.identity or PublicationIdentity(identity="")
        if previous.supersedes(identity) is True:
            return PublicationPlan(
                action="skip",
                comment_id=existing.comment_id,
                skipped_reason="stale",
            )
    if rendered.reason == "never":
        # `on: never` means this Action does not write to the pull request,
        # full stop. Falling through to the clear branch below would PATCH
        # an existing comment into a resolution notice -- a write, on the
        # one setting whose entire meaning is "do not write". Clearing is
        # for a report that no longer shows what it used to; `never` is a
        # statement about the channel, not about the findings, and under it
        # this Action has no opinion to publish either way.
        return PublicationPlan(
            action="skip",
            comment_id=existing.comment_id if existing is not None else None,
            skipped_reason="never",
        )
    if not rendered.has_content:
        if existing is None:
            return PublicationPlan(
                action="skip",
                skipped_reason=rendered.reason or "no-changes",
            )
        return PublicationPlan(
            action="clear",
            body=render_resolution(identity, sha=sha),
            comment_id=existing.comment_id,
        )
    if existing is None:
        return PublicationPlan(action="create", body=rendered.body)
    return PublicationPlan(
        action="update", body=rendered.body, comment_id=existing.comment_id
    )


def read_comments(raw: str) -> list[ExistingComment]:
    """Parse the publisher shell's newline-delimited ``{id, body}`` records.

    Hand-parsed rather than trusted: a record that is not an object, or
    carries no integer ``id``, is skipped rather than raising, because one
    unexpected record from the API must not cost the whole publication. The
    bodies inside are untrusted text and are only ever *matched against*,
    never executed, formatted, or used to choose a path or an endpoint.
    """
    out: list[ExistingComment] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        comment_id = record.get("id")
        body = record.get("body")
        if not isinstance(comment_id, int) or isinstance(comment_id, bool):
            continue
        out.append(ExistingComment(comment_id=comment_id, body=str(body or "")))
    return out


def request_payload(body: str) -> str:
    """The API request document for a create/update, as JSON text.

    The body is serialized here, in Python, and handed to the shell as a
    file. Nothing about the report's content ever becomes a shell word, an
    argument, or part of a URL.
    """
    return json.dumps({"body": body}, ensure_ascii=False)


__all__ = [
    "ACTIONS",
    "DEFAULT_MAX_COMMENT_BYTES",
    "DEFAULT_MAX_SUMMARY_BYTES",
    "GITHUB_COMMENT_BYTE_LIMIT",
    "GITHUB_COMMENT_CHAR_LIMIT",
    "GITHUB_SUMMARY_BYTE_LIMIT",
    "IDENTITY_MARKER_PREFIX",
    "POST_MODES",
    "ExistingComment",
    "PublicationError",
    "PublicationIdentity",
    "PublicationPlan",
    "RenderedReport",
    "bound_to_bytes",
    "decide",
    "find_existing",
    "read_comments",
    "render_report",
    "render_resolution",
    "render_summary",
    "request_payload",
]
