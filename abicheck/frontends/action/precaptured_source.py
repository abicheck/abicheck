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

"""May *this* run's captures be published as release-contract baselines?

``publish-baseline.yml``'s pre-captured mode publishes a baseline-set the
workflow did not build. Until now it could only take one from its **own**
run, which is the easy half: a `workflow_call` job and the job that uploaded
the artifact share a run, so the artifact's provenance is the caller's own.

The case that matters is the other one. A project whose release baselines are
published by an automatic ``workflow_run`` job takes the capture from a
*different*, already-completed producer run -- and at that point the artifact
stops being the publisher's own evidence and becomes an input with an origin
that has to be established. The downstream project that hit this wrote its
own publisher rather than go without.

This module is that establishment, and it is deliberately the same shape as
:mod:`~abicheck.frontends.action.run_selection`'s publication boundary: pure
functions over already-fetched API documents, so the workflow's shell makes
the requests and every rule here is reachable by a test with no credentials.
It reuses ``SourceRun``/``verify_source_run`` for what a run must be, and adds
the two questions those do not answer.

**One: is this producer allowed to mint a release baseline at all?** A
published release-contract baseline is what every future comparison in the
project is measured against, on an immutable channel. ADR-047 section 12
already forbids a baseline-publishing workflow from triggering on
``pull_request``/``pull_request_target``; the same rule has to hold for the
run the capture is *taken from*, or the trigger restriction is decorative --
a contributor's own pull-request build would supply the bytes while a
push-triggered publisher supplied the write token. So a pull-request-origin
producer is refused **unconditionally**, not by a configurable default that a
caller can widen.

**Two: which artifacts, exactly?** Discovery and download have to agree on
*identity*, not on a name pattern. Re-matching the pattern at download time
means the publication can pick up whichever artifact matched most recently --
an artifact can be added to a run between the two requests -- so this returns
the artifacts' own ids and the workflow carries those through its matrix.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .run_selection import (
    RunExpectation,
    SourceRun,
    SourceRunRejected,
    verify_source_run,
)

__all__ = [
    "FORBIDDEN_PRODUCER_EVENTS",
    "PrecapturedArtifact",
    "select_precaptured_artifacts",
    "verify_precaptured_producer",
]

#: Events whose runs may never supply a release-contract baseline, whatever
#: the caller asks for. These are the two triggers that run with a
#: contributor's tree; ADR-047 section 12 forbids them for the publishing
#: workflow, and a capture taken from one reaches the same channel by a
#: longer route.
FORBIDDEN_PRODUCER_EVENTS = frozenset({"pull_request", "pull_request_target"})

#: A GitHub artifact id, which is a positive integer. Zero is excluded
#: deliberately: it is what `int(entry.get("id") or 0)` produces for an
#: entry that states no id at all, so accepting it would let exactly the
#: unidentified entry this check exists to catch through.
_ARTIFACT_ID = re.compile(r"^[1-9][0-9]*$")


@dataclass(frozen=True)
class PrecapturedArtifact:
    """One baseline-set artifact, identified by id rather than by name.

    ``artifact_id`` is what a download must use. The ``name`` is kept for
    messages and for the profile-suffix the caller may want to report, never
    as the thing looked up a second time.
    """

    artifact_id: str
    name: str
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "size_bytes": self.size_bytes,
        }


def verify_precaptured_producer(
    run_document: Mapping[str, Any],
    expectation: RunExpectation,
) -> SourceRun:
    """Establish that *run_document* may supply a release-contract baseline.

    Everything :func:`~run_selection.verify_source_run` checks -- repository,
    workflow identity, event, run id, attempt, conclusion -- plus the one
    rule that is not the caller's to relax.

    The unconditional check runs **after** the declared expectation, so a
    caller that stated the wrong repository hears about that first; it is the
    more actionable message, and the trust rule refuses either way.
    """
    run = SourceRun.from_api(run_document)
    verify_source_run(run, expectation)
    if run.event in FORBIDDEN_PRODUCER_EVENTS:
        raise SourceRunRejected(
            "producer-event-forbidden",
            f"run {run.run_id} was triggered by {run.event!r}, which runs with "
            "a contributor's own tree. A release-contract baseline is what "
            "every future comparison is measured against, on an immutable "
            "channel, so it may never be minted from a pull-request build -- "
            "this is not configurable, and widening it would make ADR-047 "
            "section 12's trigger restriction decorative.",
        )
    return run


def _artifact_owner(artifact: Mapping[str, Any]) -> str:
    owner = artifact.get("workflow_run")
    return str(owner.get("id", "")) if isinstance(owner, Mapping) else ""


def select_precaptured_artifacts(
    artifacts: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
    run_id: str,
) -> list[PrecapturedArtifact]:
    """Every baseline-set artifact of *run_id*, bound to its own id.

    *artifacts* is ``GET /repos/{repo}/actions/runs/{id}/artifacts``'s
    ``artifacts`` array. Like :func:`~run_selection.select_artifact`, the
    scoping to *run_id* is re-established from each entry's own
    ``workflow_run.id`` rather than trusted from the URL the shell chose, and
    an entry that states no owner is refused exactly like one stating the
    wrong owner -- "the entry did not say" is the case an attacker controls.

    An empty *prefix* is refused rather than treated as "match everything":
    publishing every artifact a run happens to carry as a baseline-set is
    never what a caller meant, and it is the reading an accidentally-empty
    input would get.
    """
    if not prefix:
        raise SourceRunRejected(
            "artifact-prefix-missing",
            "no baseline-set artifact prefix was given; an empty prefix would "
            "match every artifact the run published",
        )
    selected: list[PrecapturedArtifact] = []
    seen_names: set[str] = set()
    for entry in artifacts:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name", ""))
        if not name.startswith(prefix):
            continue
        if _artifact_owner(entry) != str(run_id):
            continue
        if entry.get("expired"):
            raise SourceRunRejected(
                "artifact-expired",
                f"the baseline-set artifact {name!r} from run {run_id} has "
                "expired; its bytes are gone, so nothing can be published "
                "from it",
            )
        artifact_id = str(entry.get("id", ""))
        if not _ARTIFACT_ID.match(artifact_id):
            raise SourceRunRejected(
                "artifact-unidentified",
                f"the baseline-set artifact {name!r} from run {run_id} has no "
                f"usable id ({artifact_id!r}); a download bound to a name "
                "instead could fetch a different artifact than the one "
                "discovery looked at",
            )
        if name in seen_names:
            raise SourceRunRejected(
                "ambiguous-artifact",
                f"run {run_id} published more than one artifact named "
                f"{name!r}; which one is the baseline-set is not a question "
                "this can answer by picking",
            )
        seen_names.add(name)
        selected.append(
            PrecapturedArtifact(
                artifact_id=artifact_id,
                name=name,
                size_bytes=int(entry.get("size_in_bytes") or 0),
            )
        )
    if not selected:
        raise SourceRunRejected(
            "artifact-not-found",
            f"run {run_id} published no artifact whose name starts with "
            f"{prefix!r}. Publishing nothing while reporting success is the "
            "outcome this refusal exists to prevent.",
        )
    # Sorted by name so the matrix a workflow builds is deterministic; the
    # id is what identifies each entry, so the order is presentation only.
    return sorted(selected, key=lambda a: a.name)
