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

"""Cross-run baseline acquisition: may this run's captures be published?

**Bug class:** ``trust.evidence_identified_by_a_name_that_can_be_rebound``.
A release-contract baseline lands on an immutable channel and is what every
future comparison in a project is measured against, so two things about the
bytes have to be established before they get there: *whose run produced
them*, and *which artifact exactly*. Both fail the same way -- something
selected by a mutable handle (a name pattern, "the latest attempt") resolving
to different bytes than the ones that were checked.

The invariants, stated over the primitive rather than over one repro:

* a pull-request-origin producer is refused **whatever** the caller declares,
  because a configurable version of that rule is not a rule;
* selection answers with artifact *ids*, and an entry that does not
  unambiguously identify itself is refused rather than picked;
* an entry's ownership is re-established from its own ``workflow_run.id``,
  never trusted from the URL the shell chose, and an entry that states no
  owner is refused exactly like one stating the wrong owner;
* publishing nothing is never reported as success.

Eligibility is checked by sweeping *every* event name GitHub can deliver
rather than by naming the two that are refused: "the event nobody thought
of" is precisely how a trust rule acquires a hole.
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.frontends.action.precaptured_source import (
    FORBIDDEN_PRODUCER_EVENTS,
    PrecapturedArtifact,
    select_precaptured_artifacts,
    verify_precaptured_producer,
)
from abicheck.frontends.action.run_selection import RunExpectation, SourceRunRejected

RUN_ID = "5001"
HEAD = "a" * 40
WORKFLOW = ".github/workflows/release.yml"

#: Every event that can trigger a workflow run, as GitHub names them. The
#: two refused ones are not listed separately -- the sweep below derives
#: which are refused from the module's own exported set, and separately
#: asserts that set is exactly the pull-request pair, so a silent widening
#: fails rather than quietly re-deriving itself.
ALL_EVENTS = (
    "push",
    "release",
    "workflow_dispatch",
    "schedule",
    "workflow_run",
    "repository_dispatch",
    "merge_group",
    "pull_request",
    "pull_request_target",
    "issue_comment",
    "fork",
)


def run_document(
    *,
    event: str = "push",
    run_id: str = RUN_ID,
    attempt: int = 1,
    conclusion: str = "success",
    repository: str = "example/project",
) -> dict[str, Any]:
    return {
        "id": int(run_id),
        "run_attempt": attempt,
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": HEAD,
        "path": WORKFLOW,
        "name": "Release",
        "repository": {"full_name": repository},
        "head_repository": {"full_name": repository},
    }


def artifact(
    name: str,
    artifact_id: int = 900,
    *,
    owner: str | None = RUN_ID,
    expired: bool = False,
    size: int = 1024,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": artifact_id,
        "name": name,
        "size_in_bytes": size,
        "expired": expired,
    }
    if owner is not None:
        entry["workflow_run"] = {"id": int(owner)}
    return entry


def expectation(**overrides: Any) -> RunExpectation:
    base: dict[str, Any] = {
        "repository": "example/project",
        "workflow": WORKFLOW,
        "run_id": RUN_ID,
        "allowed_conclusions": ("success",),
    }
    base.update(overrides)
    return RunExpectation(**base)


# ---------------------------------------------------------------------------
# Producer eligibility
# ---------------------------------------------------------------------------


class TestAPullRequestProducerCanNeverMintAReleaseBaseline:
    """The rule that is not the caller's to relax.

    ADR-047 section 12 forbids a baseline-*publishing* workflow from
    triggering on a pull request. A capture taken from one reaches the same
    immutable channel by a longer route -- a contributor's build supplies
    the bytes while a push-triggered publisher supplies the write token --
    so the restriction has to hold for the producer too or it is decorative.
    """

    @pytest.mark.parametrize("event", ALL_EVENTS)
    def test_every_event_is_swept_not_just_the_two_that_are_refused(
        self, event: str
    ) -> None:
        document = run_document(event=event)
        if event in FORBIDDEN_PRODUCER_EVENTS:
            with pytest.raises(SourceRunRejected) as excinfo:
                verify_precaptured_producer(document, expectation())
            assert excinfo.value.code == "producer-event-forbidden"
            return
        assert verify_precaptured_producer(document, expectation()).event == event

    def test_the_forbidden_set_is_exactly_the_pull_request_pair(self) -> None:
        # Derived-from-the-module sweeps can pass against an empty set; this
        # is the assertion that stops a silent widening from being invisible.
        assert FORBIDDEN_PRODUCER_EVENTS == {"pull_request", "pull_request_target"}

    @pytest.mark.parametrize("event", sorted(FORBIDDEN_PRODUCER_EVENTS))
    def test_declaring_the_event_as_expected_does_not_make_it_allowed(
        self, event: str
    ) -> None:
        # The configurable-version-of-the-rule test: a caller that names
        # `pull_request` as its expected event satisfies `verify_source_run`
        # and must still be refused.
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_precaptured_producer(
                run_document(event=event), expectation(event=event)
            )
        assert excinfo.value.code == "producer-event-forbidden"


class TestTheDeclaredExpectationIsStillChecked:
    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"repository": "someone/else"}, "wrong-repository"),
            ({"workflow": ".github/workflows/other.yml"}, "wrong-workflow"),
            ({"run_id": "9999"}, "wrong-run"),
            ({"run_attempt": 7}, "wrong-attempt"),
            ({"allowed_conclusions": ("failure",)}, "wrong-conclusion"),
        ],
    )
    def test_a_mismatch_is_refused_with_its_own_code(
        self, overrides: dict[str, Any], code: str
    ) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_precaptured_producer(run_document(), expectation(**overrides))
        assert excinfo.value.code == code

    def test_the_declared_mismatch_is_reported_before_the_trust_rule(self) -> None:
        # Both refuse, but the actionable message is the one about what the
        # caller configured.
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_precaptured_producer(
                run_document(event="pull_request"),
                expectation(repository="someone/else"),
            )
        assert excinfo.value.code == "wrong-repository"

    def test_a_specific_attempt_binds(self) -> None:
        assert (
            verify_precaptured_producer(
                run_document(attempt=3), expectation(run_attempt=3)
            ).run_attempt
            == 3
        )


# ---------------------------------------------------------------------------
# Artifact selection
# ---------------------------------------------------------------------------


class TestSelectionAnswersWithIdentitiesNotNames:
    PREFIX = "abicheck-baseline-set-"

    def _select(self, entries: list[dict[str, Any]]) -> list[PrecapturedArtifact]:
        return select_precaptured_artifacts(entries, prefix=self.PREFIX, run_id=RUN_ID)

    def test_every_matching_artifact_is_returned_with_its_id(self) -> None:
        selected = self._select(
            [
                artifact(f"{self.PREFIX}linux", 901),
                artifact(f"{self.PREFIX}macos", 902),
                artifact("unrelated-logs", 903),
            ]
        )
        assert [(a.name, a.artifact_id) for a in selected] == [
            (f"{self.PREFIX}linux", "901"),
            (f"{self.PREFIX}macos", "902"),
        ]

    def test_the_order_is_deterministic_regardless_of_listing_order(self) -> None:
        # The id identifies each entry, so order is presentation -- but a
        # matrix built from an order that varies per request is a matrix
        # whose legs renumber between runs.
        entries = [
            artifact(f"{self.PREFIX}macos", 902),
            artifact(f"{self.PREFIX}linux", 901),
        ]
        assert [a.name for a in self._select(entries)] == [
            f"{self.PREFIX}linux",
            f"{self.PREFIX}macos",
        ]
        assert [a.name for a in self._select(list(reversed(entries)))] == [
            f"{self.PREFIX}linux",
            f"{self.PREFIX}macos",
        ]

    @pytest.mark.parametrize("owner", [None, "9999", "0"])
    def test_an_entry_not_owned_by_this_run_is_skipped(self, owner: str | None) -> None:
        # Including the unstated case: "the entry did not say" is the one an
        # attacker controls, so it fails closed exactly like a wrong owner.
        with pytest.raises(SourceRunRejected) as excinfo:
            self._select([artifact(f"{self.PREFIX}linux", 901, owner=owner)])
        assert excinfo.value.code == "artifact-not-found"

    @pytest.mark.parametrize("bad_id", [0, -1])
    def test_an_entry_with_no_usable_id_is_refused_not_name_matched(
        self, bad_id: int
    ) -> None:
        entries = [artifact(f"{self.PREFIX}linux", bad_id)]
        with pytest.raises(SourceRunRejected) as excinfo:
            self._select(entries)
        assert excinfo.value.code == "artifact-unidentified"

    def test_two_artifacts_of_the_same_name_are_refused_not_picked(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            self._select(
                [
                    artifact(f"{self.PREFIX}linux", 901),
                    artifact(f"{self.PREFIX}linux", 902),
                ]
            )
        assert excinfo.value.code == "ambiguous-artifact"

    def test_an_expired_artifact_is_refused_explicitly(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            self._select([artifact(f"{self.PREFIX}linux", 901, expired=True)])
        assert excinfo.value.code == "artifact-expired"

    def test_publishing_nothing_is_never_success(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            self._select([artifact("something-else", 901)])
        assert excinfo.value.code == "artifact-not-found"

    def test_an_empty_prefix_is_refused_rather_than_matching_everything(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            select_precaptured_artifacts(
                [artifact("anything", 901)], prefix="", run_id=RUN_ID
            )
        assert excinfo.value.code == "artifact-prefix-missing"

    @pytest.mark.parametrize("junk", [None, 17, "a string", []])
    def test_a_malformed_listing_entry_is_skipped_not_crashed_on(
        self, junk: object
    ) -> None:
        selected = select_precaptured_artifacts(
            [junk, artifact(f"{self.PREFIX}linux", 901)],  # type: ignore[list-item]
            prefix=self.PREFIX,
            run_id=RUN_ID,
        )
        assert [a.artifact_id for a in selected] == ["901"]

    def test_the_prefix_is_a_prefix_not_a_substring(self) -> None:
        with pytest.raises(SourceRunRejected):
            self._select([artifact(f"x-{self.PREFIX}linux", 901)])
