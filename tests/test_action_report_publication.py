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

"""``actions/report``'s rendering, bounding and publication decisions.

These are the parts of the publisher that decide something, and they are
tested here with no credentials, no network and no runner -- which is the
whole reason they live in :mod:`abicheck.frontends.action.report_publication`
rather than in the Action's shell (ADR-073).

**Bug class:** ``report.unestablished_result_reads_as_success``. Two of its
members show up in a publisher specifically: a body that was silently
shortened reading as a smaller finding set, and a failed *publication*
reading as a clean *compatibility* result. Both are stated as invariants
below rather than as single reproducers.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from click.testing import CliRunner

from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.report_publication import (
    DEFAULT_MAX_COMMENT_BYTES,
    GITHUB_COMMENT_CHAR_LIMIT,
    GITHUB_SUMMARY_BYTE_LIMIT,
    ExistingComment,
    PublicationError,
    PublicationIdentity,
    bound_to_bytes,
    decide,
    find_existing,
    read_comments,
    render_report,
    render_resolution,
    render_summary,
    request_payload,
    summary_for_plan,
)

IDENTITY = PublicationIdentity(
    identity="abicheck:linux-gcc", run_id="1000", run_attempt=1, head_sha="a" * 40
)


def compare_report(
    *, changes: list[dict[str, object]], verdict: str
) -> dict[str, object]:
    return {
        "library": "libthing.so",
        "old_version": "1.0",
        "new_version": "1.1",
        "verdict": verdict,
        "changes": changes,
    }


BREAK = compare_report(
    changes=[
        {
            "kind": "func_removed",
            "symbol": "thing_open",
            "severity": "breaking",
            "description": "Function removed",
        }
    ],
    verdict="BREAKING",
)
CLEAN = compare_report(changes=[], verdict="NO_CHANGE")


# ---------------------------------------------------------------------------
# Identity and the ordering guard
# ---------------------------------------------------------------------------


class TestIdentityMarker:
    @pytest.mark.parametrize(
        "identity",
        [
            "abicheck:default",
            "abicheck:linux-gcc",
            "with spaces and · punctuation",
            "a}brace",
            "an --> html comment terminator",
            "unicode: 關數 ünïcödé",
            "",
        ],
    )
    def test_round_trips_through_a_rendered_body(self, identity: str) -> None:
        """The marker is the sticky key. Anything it cannot round-trip
        becomes a duplicate comment on every run, so the property is stated
        over adversarial identity text rather than the two names the Action
        happens to generate."""
        stamp = PublicationIdentity(
            identity=identity, run_id="42", run_attempt=3, head_sha="f" * 40
        )
        body = f"{stamp.marker()}\n\n## a body\n\nwith --> text in it\n"
        assert PublicationIdentity.parse(body) == stamp

    def test_marker_never_closes_its_own_html_comment_early(self) -> None:
        stamp = PublicationIdentity(identity="x --> y", run_id="1")
        marker = stamp.marker()
        assert marker.endswith("-->")
        assert "-->" not in marker[:-3]

    def test_a_body_with_no_marker_has_no_identity(self) -> None:
        assert PublicationIdentity.parse("just a comment from a human") is None

    def test_an_unparseable_marker_is_still_recognised_as_ours(self) -> None:
        """Returning ``None`` here would make the publisher post a second
        comment beside its own broken one, which is the exact outcome a
        sticky comment exists to prevent."""
        body = "<!-- abicheck-report-identity: {not json -->\n## body"
        parsed = PublicationIdentity.parse(body)
        assert parsed is not None
        assert parsed.identity == ""

    @pytest.mark.parametrize(
        "mine,theirs,expected",
        [
            (("100", 1), ("99", 1), True),
            (("99", 1), ("100", 1), False),
            (("100", 2), ("100", 1), True),
            (("100", 1), ("100", 2), False),
            (("100", 1), ("100", 1), False),
            (("", 1), ("100", 1), None),
            (("100", 1), ("", 1), None),
            (("not-a-number", 1), ("100", 1), None),
        ],
    )
    def test_ordering_is_by_run_then_attempt(
        self,
        mine: tuple[str, int],
        theirs: tuple[str, int],
        expected: bool | None,
    ) -> None:
        a = PublicationIdentity(identity="x", run_id=mine[0], run_attempt=mine[1])
        b = PublicationIdentity(identity="x", run_id=theirs[0], run_attempt=theirs[1])
        assert a.supersedes(b) is expected


# ---------------------------------------------------------------------------
# The publication decision
# ---------------------------------------------------------------------------


def _comment(identity: PublicationIdentity, comment_id: int = 7) -> ExistingComment:
    return ExistingComment(
        comment_id=comment_id, body=f"{identity.marker()}\n## previous body"
    )


class TestPublicationDecision:
    def test_first_publication_creates(self) -> None:
        rendered = render_report(BREAK, identity=IDENTITY)
        plan = decide(rendered, identity=IDENTITY, comments=[])
        assert plan.action == "create"
        assert IDENTITY.marker() in plan.body

    def test_a_rerun_updates_in_place_and_never_duplicates(self) -> None:
        previous = replace(IDENTITY, run_id="999")
        rendered = render_report(BREAK, identity=IDENTITY)
        plan = decide(
            rendered, identity=IDENTITY, comments=[_comment(previous, comment_id=31)]
        )
        assert plan.action == "update"
        assert plan.comment_id == 31

    def test_an_older_producer_run_never_overwrites_a_newer_result(self) -> None:
        """The ordering guard. A late-finishing older run silently replacing
        a newer result is misinformation, not merely churn -- and it happens
        routinely when an old commit's workflow is re-run."""
        newer = replace(IDENTITY, run_id="2000")
        older = replace(IDENTITY, run_id="1000")
        rendered = render_report(CLEAN, identity=older)
        plan = decide(rendered, identity=older, comments=[_comment(newer)])
        assert plan.action == "skip"
        assert plan.skipped_reason == "stale"
        assert not plan.posts

    def test_staleness_outranks_having_something_to_say(self) -> None:
        newer = replace(IDENTITY, run_id="2000")
        older = replace(IDENTITY, run_id="1000")
        rendered = render_report(BREAK, identity=older)
        assert rendered.has_content
        plan = decide(rendered, identity=older, comments=[_comment(newer)])
        assert plan.action == "skip"
        assert plan.skipped_reason == "stale"

    def test_an_unorderable_marker_does_not_freeze_the_comment(self) -> None:
        """ "Cannot tell" is not staleness. A publisher frozen forever by one
        malformed marker is a worse failure than an extra update."""
        unorderable = ExistingComment(
            comment_id=5,
            body=(
                "<!-- abicheck-report-identity: "
                '{"identity": "abicheck:linux-gcc", "run_id": "nope"} -->'
            ),
        )
        rendered = render_report(BREAK, identity=IDENTITY)
        plan = decide(rendered, identity=IDENTITY, comments=[unorderable])
        assert plan.action == "update"

    def test_resolution_clears_a_stale_comment_even_under_on_changes(self) -> None:
        rendered = render_report(CLEAN, identity=IDENTITY, on="changes")
        assert not rendered.has_content
        plan = decide(rendered, identity=IDENTITY, comments=[_comment(IDENTITY)])
        assert plan.action == "clear"
        assert "resolved" in plan.body
        assert IDENTITY.marker() in plan.body

    def test_nothing_to_say_and_nothing_said_stays_quiet(self) -> None:
        rendered = render_report(CLEAN, identity=IDENTITY, on="changes")
        plan = decide(rendered, identity=IDENTITY, comments=[])
        assert plan.action == "skip"
        assert plan.skipped_reason == "no-changes"
        assert not plan.posts

    def test_on_never_makes_no_write_of_any_kind(self) -> None:
        """`never` is the kill switch, and it must actually kill writes.

        This asserted `clear` until review, on the reading that `never`
        meant "do not report findings" rather than "leave an obsolete
        report standing". That reading does not survive looking at what the
        clear would *say*: `render_report` short-circuits on `never`
        **before** `build_model`, so the resolution body is written without
        the report having been read at all. Under `on: never` a BREAKING
        report would have had its comment replaced by "previously reported
        findings are resolved" -- an affirmative all-clear the Action never
        established. Not reporting and asserting the opposite of the truth
        are not the same thing, and only one of them is what `never` asks
        for.
        """
        rendered = render_report(BREAK, identity=IDENTITY, on="never")
        assert not rendered.has_content
        assert rendered.reason == "never"
        for comments in ([], [_comment(IDENTITY)]):
            plan = decide(rendered, identity=IDENTITY, comments=comments)
            assert plan.action == "skip"
            assert plan.skipped_reason == "never"
            assert not plan.posts
            assert not plan.body

    def test_on_never_is_the_only_setting_that_suppresses_a_clear(self) -> None:
        """The positive control: clearing still happens where it is honest.

        Under `changes`, a report that no longer shows anything *has* been
        read, so "resolved" is a claim the run can make.
        """
        rendered = render_report(CLEAN, identity=IDENTITY, on="changes")
        plan = decide(rendered, identity=IDENTITY, comments=[_comment(IDENTITY)])
        assert plan.action == "clear"
        assert "resolved" in (plan.body or "")

    def test_on_always_publishes_a_clean_result(self) -> None:
        rendered = render_report(CLEAN, identity=IDENTITY, on="always")
        assert rendered.has_content
        assert decide(rendered, identity=IDENTITY, comments=[]).action == "create"

    def test_another_profiles_comment_is_not_ours_to_overwrite(self) -> None:
        other = PublicationIdentity(identity="abicheck:windows-msvc", run_id="9")
        rendered = render_report(BREAK, identity=IDENTITY)
        plan = decide(rendered, identity=IDENTITY, comments=[_comment(other)])
        assert plan.action == "create"
        assert plan.comment_id is None

    def test_a_human_comment_is_never_matched(self) -> None:
        human = ExistingComment(comment_id=3, body="LGTM, but check the ABI")
        assert find_existing([human], IDENTITY) is None

    def test_an_invalid_post_mode_is_a_publication_error(self) -> None:
        with pytest.raises(PublicationError):
            render_report(BREAK, identity=IDENTITY, on="sometimes")


class TestExistingCommentParsing:
    def test_malformed_records_are_skipped_not_fatal(self) -> None:
        raw = "\n".join(
            [
                json.dumps({"id": 1, "body": "first"}),
                "not json at all",
                json.dumps([1, 2, 3]),
                json.dumps({"body": "no id"}),
                json.dumps({"id": "7", "body": "string id"}),
                json.dumps({"id": True, "body": "bool id"}),
                "",
                json.dumps({"id": 2, "body": "second"}),
            ]
        )
        parsed = read_comments(raw)
        assert [c.comment_id for c in parsed] == [1, 2]

    def test_a_body_with_embedded_newlines_survives_the_framing(self) -> None:
        body = "line one\nline two\n<!-- marker -->"
        raw = json.dumps({"id": 9, "body": body})
        assert read_comments(raw)[0].body == body


# ---------------------------------------------------------------------------
# Bounding
# ---------------------------------------------------------------------------


class TestByteBounding:
    """The bound is in bytes and holds for any content.

    Stated over generated inputs rather than one oversized fixture: the
    failure this guards is a body that *almost* fits, which a single
    hand-sized example never explores.
    """

    @staticmethod
    def _body(n_lines: int, filler: str) -> str:
        return "\n".join(
            f"| `kind_{i}` | `{filler}{i}` | detail |" for i in range(n_lines)
        )

    @pytest.mark.parametrize(
        "filler", ["ascii_symbol_", "關數_ünïcödé_λ_", "😀_emoji_"]
    )
    @pytest.mark.parametrize("budget", [200, 1000, 5000, 20000])
    def test_result_is_within_budget_and_valid_utf8(
        self, filler: str, budget: int
    ) -> None:
        body = self._body(4000, filler)
        assert len(body.encode("utf-8")) > budget, "premise: the body must overflow"
        out, truncated = bound_to_bytes(body, budget, "\n\n<sub>cut</sub>")
        assert truncated
        assert len(out.encode("utf-8")) <= budget
        # A byte-wise slice would leave a partial sequence here; a
        # character-wise one at a line boundary cannot.
        assert out.encode("utf-8").decode("utf-8") == out
        assert "�" not in out

    def test_a_body_within_budget_is_returned_unchanged(self) -> None:
        body = "## short\n\nnothing to cut"
        out, truncated = bound_to_bytes(body, 10_000, "\n\nnote")
        assert out == body
        assert truncated is False

    def test_truncation_is_always_disclosed(self) -> None:
        body = self._body(2000, "sym_")
        out, truncated = bound_to_bytes(body, 500, "\n\n<sub>TRUNCATED-HERE</sub>")
        assert truncated
        assert "TRUNCATED-HERE" in out

    def test_an_open_details_block_is_closed_before_the_note(self) -> None:
        body = "<details><summary>x</summary>\n" + self._body(2000, "sym_")
        out, _ = bound_to_bytes(body, 600, "\n\n<sub>cut</sub>")
        assert out.count("<details") == out.count("</details>")
        assert out.rstrip().endswith("</sub>")

    def test_the_note_itself_is_inside_the_budget(self) -> None:
        note = "\n\n" + "N" * 300
        out, _ = bound_to_bytes("x\n" * 5000, 400, note)
        assert len(out.encode("utf-8")) <= 400
        assert note.strip() in out

    def test_a_zero_budget_yields_nothing_rather_than_an_oversized_body(self) -> None:
        out, truncated = bound_to_bytes("something", 0, "note")
        assert out == ""
        assert truncated is True


class TestRenderedOutputsRespectPlatformLimits:
    @staticmethod
    def _huge_report(n: int) -> dict[str, object]:
        return compare_report(
            changes=[
                {
                    "kind": "func_removed",
                    "symbol": f"ns::Widget::method_{i}",
                    "severity": "breaking",
                    "description": f"Function removed ({i})",
                    "source_location": f"include/widget.h:{i}",
                }
                for i in range(n)
            ],
            verdict="BREAKING",
        )

    def test_comment_body_fits_both_of_githubs_comment_limits(self) -> None:
        rendered = render_report(
            self._huge_report(6000), identity=IDENTITY, detail="full"
        )
        assert rendered.body_bytes <= DEFAULT_MAX_COMMENT_BYTES
        # Bounding bytes bounds characters too, which is the limit GitHub
        # actually states for a comment body.
        assert len(rendered.body) <= GITHUB_COMMENT_CHAR_LIMIT

    def test_summary_is_bounded_independently_of_the_comment(self) -> None:
        rendered = render_report(
            self._huge_report(6000), identity=IDENTITY, detail="full"
        )
        summary, _ = render_summary(rendered.body)
        assert len(summary.encode("utf-8")) <= GITHUB_SUMMARY_BYTE_LIMIT
        # The summary's budget is the larger one, so a body that fit the
        # comment budget is never cut again on its way to the summary.
        assert summary == rendered.body

    def test_a_truncated_comment_never_reads_as_a_smaller_finding_set(self) -> None:
        rendered = render_report(
            self._huge_report(6000),
            identity=IDENTITY,
            detail="full",
            report_url="https://example.invalid/run/1",
        )
        assert "6000 breaking" in rendered.body
        if rendered.truncated:
            assert "truncated" in rendered.body

    def test_identity_survives_truncation(self) -> None:
        """The marker is written first precisely so the sticky key cannot be
        the thing a size cut removes."""
        rendered = render_report(
            self._huge_report(6000),
            identity=IDENTITY,
            detail="full",
            max_comment_bytes=4000,
        )
        assert rendered.truncated
        assert PublicationIdentity.parse(rendered.body) == IDENTITY


class TestRequestPayload:
    @pytest.mark.parametrize(
        "body",
        [
            'a "quoted" body',
            "a\nmultiline\nbody",
            "backticks ` and $(command substitution) and ${VAR}",
            "unicode 關數 😀",
            "a\\backslash and a \x07 control character",
        ],
    )
    def test_arbitrary_body_text_round_trips_as_json(self, body: str) -> None:
        """The API document is built by a JSON serializer, never by string
        formatting, so report text cannot break out of it."""
        assert json.loads(request_payload(body)) == {"body": body}


class TestResolutionBody:
    def test_states_that_findings_are_resolved_not_that_none_existed(self) -> None:
        body = render_resolution(IDENTITY, sha="abc123def456")
        assert "resolved" in body
        assert "abc123def456"[:12] in body
        assert PublicationIdentity.parse(body) == IDENTITY


# ---------------------------------------------------------------------------
# The Action-only CLI, which is what the shell actually calls
# ---------------------------------------------------------------------------


class TestActionCommentCommand:
    """`python -m abicheck.frontends.action.cli comment`, end to end.

    The shell's whole contribution is passing these paths and posting what
    lands in them, so the files this command writes *are* the Action's
    behaviour: a wrong one here is a wrong comment on a real pull request.
    """

    @staticmethod
    def _invoke(tmp_path, report_payload, *extra, existing=None):
        report = tmp_path / "report.json"
        report.write_text(json.dumps(report_payload), encoding="utf-8")
        args = [
            "comment",
            str(report),
            "--identity",
            "abicheck:linux-gcc",
            "--run-id",
            "1000",
            "--body-out",
            str(tmp_path / "body.md"),
            "--request-out",
            str(tmp_path / "request.json"),
            "--summary-out",
            str(tmp_path / "summary.md"),
            "--plan-out",
            str(tmp_path / "plan.json"),
            *extra,
        ]
        if existing is not None:
            path = tmp_path / "existing.ndjson"
            path.write_text(existing, encoding="utf-8")
            args += ["--existing-comments", str(path)]
        result = CliRunner().invoke(action_cli, args)
        assert result.exit_code == 0, result.output
        return json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))

    def test_a_first_publication_writes_body_request_and_summary(self, tmp_path):
        plan = self._invoke(tmp_path, BREAK)
        assert plan["action"] == "create"
        body = (tmp_path / "body.md").read_text(encoding="utf-8")
        assert json.loads((tmp_path / "request.json").read_text("utf-8")) == {
            "body": body
        }
        assert (tmp_path / "summary.md").read_text(encoding="utf-8") == body
        assert plan["body_bytes"] == len(body.encode("utf-8"))

    def test_a_stale_skip_publishes_on_no_channel_at_all(self, tmp_path):
        """The ordering guard covers the comment. The job summary is a second
        publication channel it does not cover, so a superseded body must not
        be written there either."""
        newer = PublicationIdentity(
            identity="abicheck:linux-gcc", run_id="9999", run_attempt=1
        )
        existing = json.dumps({"id": 5, "body": newer.marker() + "\n## older"})
        plan = self._invoke(tmp_path, BREAK, existing=existing)
        assert plan["action"] == "skip"
        assert plan["skipped_reason"] == "stale"
        assert not (tmp_path / "summary.md").exists()
        assert not (tmp_path / "request.json").exists()

    def test_nothing_to_say_writes_no_request(self, tmp_path):
        plan = self._invoke(tmp_path, CLEAN)
        assert plan["action"] == "skip"
        assert (tmp_path / "body.md").read_text(encoding="utf-8") == ""
        assert not (tmp_path / "request.json").exists()

    def test_a_clear_writes_the_resolution_body_not_the_rendered_one(self, tmp_path):
        previous = PublicationIdentity(
            identity="abicheck:linux-gcc", run_id="1", run_attempt=1
        )
        existing = json.dumps({"id": 5, "body": previous.marker() + "\n## break"})
        plan = self._invoke(tmp_path, CLEAN, existing=existing)
        assert plan["action"] == "clear"
        body = (tmp_path / "body.md").read_text(encoding="utf-8")
        assert "resolved" in body
        assert (
            json.loads((tmp_path / "request.json").read_text("utf-8"))["body"] == body
        )

    def test_on_never_writes_nothing_and_says_why(self, tmp_path):
        plan = self._invoke(tmp_path, BREAK, "--on", "never")
        assert plan["action"] == "skip"
        assert plan["skipped_reason"] == "never"

    @pytest.mark.parametrize("budget", [600, 3000, 20000, 60000])
    def test_the_body_always_fits_its_budget_and_truncation_is_recorded(
        self, tmp_path, budget
    ):
        """Two layers shrink a body and they must agree.

        The renderer tightens its own row budget first, and only what
        survives that is byte-bounded here -- so at a generous budget
        nothing is cut and `comment_truncated` is false, while at a tight
        one the cut happens and is both recorded in the plan and disclosed
        in the body. Asserting the relationship rather than a fixed expected
        value is what keeps this true when either layer changes.
        """
        wide = compare_report(
            changes=[
                {
                    "kind": "func_removed",
                    "symbol": f"entry_{i}",
                    "severity": "breaking",
                    "description": "Function removed",
                }
                for i in range(4000)
            ],
            verdict="BREAKING",
        )
        plan = self._invoke(tmp_path, wide, "--max-comment-bytes", str(budget))
        body = (tmp_path / "body.md").read_text(encoding="utf-8")
        assert plan["body_bytes"] <= budget
        assert plan["comment_truncated"] is (
            "truncated to fit GitHub's comment size limit" in body
        )
        # Whatever was cut, the sticky identity is never what goes.
        assert PublicationIdentity.parse(body) is not None


class TestTheSummaryGetsItsOwnBudget:
    """The two destinations are bounded from the same *unbounded* render.

    Review finding: the summary was bounded from `rendered.body`, which
    `render_report` had already cut to the comment budget. The ~15x larger
    summary limit therefore bought nothing -- the summary could only ever
    be the comment, and inherited the comment's own truncation notice, so
    the job summary told a reader content had been dropped for a limit that
    was not the one it was subject to.
    """

    @staticmethod
    def _big_report(rows: int = 4000) -> dict[str, object]:
        return {
            "library": "libthing.so",
            "old_version": "1.0",
            "new_version": "1.1",
            "verdict": "BREAKING",
            "changes": [
                {
                    "kind": "func_removed",
                    "symbol": f"thing_symbol_with_a_long_name_{i:05d}",
                    "severity": "breaking",
                    "description": "Function removed from the public surface",
                }
                for i in range(rows)
            ],
        }

    def test_the_summary_keeps_content_the_comment_had_to_drop(self) -> None:
        rendered = render_report(
            self._big_report(),
            identity=IDENTITY,
            on="always",
            detail="full",
            max_comment_bytes=20_000,
        )
        assert rendered.truncated, "the fixture must actually exceed the comment budget"
        plan = decide(rendered, identity=IDENTITY, comments=[])
        summary, summary_truncated = summary_for_plan(
            rendered, plan, max_summary_bytes=900_000
        )
        assert not summary_truncated
        assert len(summary.encode("utf-8")) > len(rendered.body.encode("utf-8")), (
            "the summary is still bounded by the comment's budget"
        )
        # The specific tell: the comment's own truncation notice must not be
        # carried into a destination that did not truncate.
        assert "truncated" not in summary.lower()

    def test_both_budgets_are_still_honoured_independently(self) -> None:
        rendered = render_report(
            self._big_report(),
            identity=IDENTITY,
            on="always",
            detail="full",
            max_comment_bytes=20_000,
        )
        plan = decide(rendered, identity=IDENTITY, comments=[])
        summary, summary_truncated = summary_for_plan(
            rendered, plan, max_summary_bytes=30_000
        )
        assert len(rendered.body.encode("utf-8")) <= 20_000
        assert len(summary.encode("utf-8")) <= 30_000
        assert summary_truncated, "a summary over its own budget must say so"

    def test_a_clear_summarises_the_resolution_not_the_report(self) -> None:
        """`plan.body` wins when the plan wrote one: a cleared comment
        states a resolution, and the summary must agree with the comment
        rather than re-publishing the findings it just retracted."""
        rendered = render_report(CLEAN, identity=IDENTITY, on="changes")
        plan = decide(rendered, identity=IDENTITY, comments=[_comment(IDENTITY)])
        assert plan.action == "clear"
        summary, _ = summary_for_plan(rendered, plan)
        assert "resolved" in summary

    def test_an_unbounded_render_carries_full_body_equal_to_body(self) -> None:
        """Vacuity guard: when nothing was cut the two are the same text, so
        the tests above cannot be passing on an accidental difference."""
        rendered = render_report(BREAK, identity=IDENTITY, on="always")
        assert not rendered.truncated
        assert rendered.full_body == rendered.body
