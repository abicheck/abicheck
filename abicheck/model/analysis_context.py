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

"""What the producer run recorded about *which* revision it analysed.

A trusted ``workflow_run`` publisher re-derives the repository, the run and
the pull request from the GitHub API, because the artifact it is handed was
produced by an unprivileged job (ADR-073). There is exactly one fact the API
cannot supply: on a ``pull_request`` event the producer checks out an
**ephemeral merge commit**, so what was analysed is neither the PR head the
API reports nor anything the API names. Only the producer knows it.

That single missing fact is why a downstream project ended up writing a
``tested-sha.txt`` beside the report, parsing it in privileged shell, and
then running the whole verifier a second time to check it. This module is
the canonical place for it instead: an ``analysis_context`` block inside the
aggregate document the producer already publishes, carried through the same
artifact, read through the same bounded reader.

**Everything here is untrusted input.** Its fields are *claims* by a job
that may be a fork's. The block exists so a publisher has something specific
to verify against the API -- ``verify_tested_sha`` establishes that the
recorded commit really is the pull request's head or a merge of it -- never
so a publisher can skip verifying. Accordingly:

* every value is shape-validated on the way in (a SHA is full hex, a run id
  is digits, a ref is bounded and free of control characters), because these
  values reach step outputs and comment bodies in a privileged job, where a
  newline forges an output and a control character corrupts a log;
* the identities stay **separate**. PR head, PR base, the commit actually
  built, the producer's run and attempt, and the revision of the
  orchestration that ran are five different things, and the reporting errors
  this module exists to prevent are all of the form "one was displayed as
  another";
* an absent field stays absent rather than being defaulted to a plausible
  sibling. "The producer did not record which commit it analysed" and "the
  producer analysed the PR head" are different states, and collapsing them
  is how a comment comes to claim analysis of a tree nothing looked at.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

__all__ = [
    "ANALYSIS_CONTEXT_KEY",
    "ANALYSIS_CONTEXT_SCHEMA",
    "AnalysisContext",
    "AnalysisContextError",
]

#: The document key the block is published under, and the schema it declares.
#: Versioned because a publisher reading an artifact from an older producer is
#: the ordinary case, not an error.
ANALYSIS_CONTEXT_KEY = "analysis_context"
ANALYSIS_CONTEXT_SCHEMA = "abicheck.analysis-context/1"

_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
_DIGITS = re.compile(r"^[0-9]{1,20}$")
#: Deliberately permissive about *content* and strict about *class*: a git
#: ref or a workflow ref may hold almost any printable character, but never a
#: control character, and never at unbounded length.
_SAFE_TEXT = re.compile(r"^[\x20-\x7e]{1,300}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class AnalysisContextError(ValueError):
    """A recorded analysis context could not be read as one.

    Raised for a *shape* failure -- a malformed SHA, an unreadable document,
    an unknown schema. It never means "this claim is false"; establishing
    that is :func:`~abicheck.frontends.action.run_selection.verify_tested_sha`'s
    job, against the API.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _validated(name: str, value: Any, pattern: re.Pattern[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AnalysisContextError(
            "analysis-context-malformed",
            f"{name} must be a string, got {type(value).__name__}",
        )
    if value == "":
        return ""
    if not pattern.match(value):
        # The value is echoed back with !r so a stray newline or control
        # character is visible in the log rather than acting in it.
        raise AnalysisContextError(
            "analysis-context-malformed",
            f"{name} is not in the form this field accepts: {value!r}",
        )
    return value


@dataclass(frozen=True)
class AnalysisContext:
    """One producer run's own account of what it analysed.

    Every field is optional and every field defaults to ``""``. A producer
    that cannot state a value must leave it empty rather than guessing, and a
    consumer reading an empty value learns "not recorded" -- which is a real
    answer, and a different one from any particular commit.
    """

    #: The revision actually checked out, built and captured. On a
    #: ``pull_request`` run this is the ephemeral merge commit; it is NOT the
    #: PR head, and displaying one as the other is the whole defect.
    tested_sha: str = ""
    #: The pull request's own head, as the producer saw it. Kept so a
    #: publisher can report a disagreement with the API rather than silently
    #: preferring one; never used *as* the head.
    pr_head_sha: str = ""
    #: The base commit the merge was made against, and the branch it names.
    pr_base_sha: str = ""
    pr_base_ref: str = ""
    #: A claimed pull request number. Only ever cross-checked against the
    #: API's answer -- never published to.
    pr_number: str = ""
    #: Which run produced this, in the producer's own words. The publisher
    #: re-derives all of it; a disagreement is worth reporting.
    producer_repository: str = ""
    producer_run_id: str = ""
    producer_run_attempt: str = ""
    producer_workflow_ref: str = ""
    producer_event: str = ""
    #: The revision of the *orchestration* -- the abicheck Action/workflow
    #: ref the producer ran. Distinct from every commit above: a historical
    #: bootstrap builds an old captured revision with a current orchestration,
    #: and flattening the two makes that look like a tag push of the old one.
    orchestration_ref: str = ""
    #: The contract profile this analysis was performed under.
    profile: str = ""

    _PATTERNS = {
        "tested_sha": _FULL_SHA,
        "pr_head_sha": _FULL_SHA,
        "pr_base_sha": _FULL_SHA,
        "pr_number": _DIGITS,
        "producer_repository": _REPOSITORY,
        "producer_run_id": _DIGITS,
        "producer_run_attempt": _DIGITS,
        "pr_base_ref": _SAFE_TEXT,
        "producer_workflow_ref": _SAFE_TEXT,
        "producer_event": _SAFE_TEXT,
        "orchestration_ref": _SAFE_TEXT,
        "profile": _SAFE_TEXT,
    }

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_mapping(cls, data: Any) -> AnalysisContext:
        """Read a recorded block, validating every field's shape.

        An unknown key is refused rather than ignored: this document travels
        from an untrusted job into a privileged one, and a key nothing reads
        is either a producer's misspelling of a field that *does* matter --
        silently leaving the real one empty -- or an attempt to reach a field
        a later version will add. Both are worth a message.
        """
        if not isinstance(data, Mapping):
            raise AnalysisContextError(
                "analysis-context-malformed",
                f"the analysis context is not an object (got {type(data).__name__})",
            )
        schema = data.get("schema", ANALYSIS_CONTEXT_SCHEMA)
        if schema != ANALYSIS_CONTEXT_SCHEMA:
            raise AnalysisContextError(
                "analysis-context-unsupported-schema",
                f"the analysis context declares schema {schema!r}; this build "
                f"reads {ANALYSIS_CONTEXT_SCHEMA}",
            )
        known = set(cls.field_names()) | {"schema", "note"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise AnalysisContextError(
                "analysis-context-malformed",
                f"unrecognized field(s) {', '.join(unknown)} -- a misspelled "
                "field leaves the one it was meant to be silently empty",
            )
        return cls(
            **{
                name: _validated(name, data.get(name), cls._PATTERNS[name])
                for name in cls.field_names()
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """The published block, schema and untrustedness stated in it.

        ``note`` is part of the document rather than only of this docstring
        because the reader of a published ``aggregate.json`` is often a
        person deciding whether to trust what they are looking at, and the
        answer should travel with the data.
        """
        document: dict[str, Any] = {
            "schema": ANALYSIS_CONTEXT_SCHEMA,
            "note": (
                "Recorded by the analysis job, which may be unprivileged or a "
                "fork's. Every claim here is verified against the GitHub API "
                "before a trusted publisher displays it."
            ),
        }
        document.update({name: getattr(self, name) for name in self.field_names()})
        return document

    @property
    def records_tested_sha(self) -> bool:
        """Whether the producer stated which commit it analysed.

        A property rather than a ``bool(...)`` at each call site: "did the
        producer say?" is the question a publisher's missing-provenance
        policy branches on, and it must not drift into "is this SHA truthy"
        somewhere and "is it different from the head" somewhere else.
        """
        return bool(self.tested_sha)
