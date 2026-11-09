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

"""The report's ``delta_baseline`` block: which comparison this report is.

With ``delta-baseline: main`` the root GitHub Action runs two comparisons of
the same new side (plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md``):

* the **gate** -- against the configured release baseline. Its verdict is
  the step's verdict and exit code, always;
* the **delta** -- against the default-branch snapshot of the exact commit the
  pull request was built on, so the PR comment lists only this PR's changes.

Both reports look alike, so each one says which it is. The Action writes a
small JSON file and passes ``--set report.delta_baseline=@FILE``; the CLI
checks its shape (exit 64 if unusable) and copies it into the compare report
(schema 7.3) and the release report (release schema 3.1) as a top-level
``delta_baseline`` block, marked ``asserted_by: "action"`` because the CLI did
not look the snapshot up itself.

The block is informational. It never changes ``verdict``, ``severity``, the
exit code, or which comparison gates: a report with ``comparison_role:
"delta"`` is advisory by definition, and its ``gate_verdict`` records the
gate's answer so a reader of the delta report alone is not misled into
reading its own (usually narrower) verdict as the check's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..workflows.delta_operand import MatchRule

__all__ = [
    "DELTA_BASELINE_PATH",
    "DELTA_COMPARISON_ROLES",
    "DELTA_GATE_OUTCOMES",
    "DELTA_LOOKUP_OUTCOMES",
    "DELTA_MEASURED_OUTCOMES",
    "DELTA_OPERAND_MATCHES",
    "DELTA_POST_GATE_OUTCOMES",
    "DELTA_REPORT_OUTCOMES",
    "DeltaBaseline",
    "DeltaBaselineError",
]

#: The ``.abicheck.yml`` / ``--set`` path that names the record file.
DELTA_BASELINE_PATH = "report.delta_baseline"

#: ``gate``: this report's verdict decides the check. ``delta``: this report
#: is the PR-only comparison against the default-branch snapshot; advisory.
DELTA_COMPARISON_ROLES: tuple[str, ...] = ("gate", "delta")

#: The base-snapshot lookup's outcomes -- the values of
#: ``frontends.action.main_snapshot.DeltaOutcome`` (a test keeps the two equal;
#: this layer may not import the Action frontend).
DELTA_LOOKUP_OUTCOMES: tuple[str, ...] = (
    "resolved",
    "base_unknown",
    "base_ambiguous",
    "base_pending",
    "base_failed",
    "base_not_snapshotted",
    "base_expired",
    "snapshot_missing",
    "untrusted_source",
    "lookup_failed",
)

#: The outcomes of a measured delta -- the only ones a ``delta`` report
#: carries: ``resolved`` (a stored snapshot) and ``base_built`` (no stored
#: snapshot was usable, so ``delta-on-missing-base: build`` built the base
#: commit in the PR job, after the gate).
DELTA_MEASURED_OUTCOMES: tuple[str, ...] = ("resolved", "base_built")

#: Every outcome a report may carry: the lookup's, ``unsupported_operand``
#: (the snapshot resolved, but a directory/package comparison is not one a
#: single-snapshot delta can describe -- known before the gate runs), and
#: ``base_built`` (a ``delta`` report only: the base was built after the gate,
#: so no ``gate`` report can record it).
DELTA_REPORT_OUTCOMES: tuple[str, ...] = (
    *DELTA_LOOKUP_OUTCOMES,
    "unsupported_operand",
    "base_built",
)

#: The outcomes a ``gate`` report may carry: every report outcome except
#: ``base_built``, which is decided after the gate ran.
DELTA_GATE_OUTCOMES: tuple[str, ...] = tuple(
    o for o in DELTA_REPORT_OUTCOMES if o != "base_built"
)

#: Outcomes the comment can carry that no report records. Each is decided
#: after the gate has run, and in each case no delta comparison runs:
#: ``rerun_failed`` (the delta rerun produced no report), a failed base
#: build (``base_build_failed``/``base_dump_failed``) and the operand
#: identity refusals of :mod:`abicheck.workflows.delta_operand` (the snapshot
#: is not shown to be this PR's library, so it is never compared). The gate
#: report's own block keeps the lookup's outcome; the analysis context's
#: ``delta_outcome`` carries the comment's final one.
DELTA_POST_GATE_OUTCOMES: tuple[str, ...] = (
    "rerun_failed",
    # delta-on-missing-base: build, after the gate: the base commit could not
    # be checked out or built, or its build could not be dumped.
    "base_build_failed",
    "base_dump_failed",
    "operand_unmatched",
    "operand_ambiguous",
    "operand_map_invalid",
    "operand_unreadable",
)

#: How a delta report's snapshot library was paired with the PR's library
#: (:class:`~abicheck.workflows.delta_operand.MatchRule`, without its empty
#: "no match" member: a delta report exists only for a matched pair).
DELTA_OPERAND_MATCHES: tuple[str, ...] = tuple(r.value for r in MatchRule if r.value)

_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "comparison_role",
        "outcome",
        "base_sha",
        "branch",
        "gate_verdict",
        "operand_match",
        "snapshot_sha",
    }
)
_FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
#: A git ref name with nothing a Markdown renderer or a shell would act on.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_VERDICT = re.compile(r"^[A-Z][A-Z_]{0,39}$")


class DeltaBaselineError(ValueError):
    """The record is not the shape :class:`DeltaBaseline` reads."""


def _optional(
    data: Mapping[str, object], name: str, pattern: re.Pattern[str]
) -> str | None:
    value = data.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DeltaBaselineError(f"{name} {value!r} does not match {pattern.pattern}")
    return value


def _operand_match(data: Mapping[str, object]) -> str | None:
    value = data.get("operand_match")
    if value is None or value == "":
        return None
    if value not in DELTA_OPERAND_MATCHES:
        raise DeltaBaselineError(
            f"operand_match must be one of {', '.join(DELTA_OPERAND_MATCHES)}, got {value!r}"
        )
    return str(value)


@dataclass(frozen=True)
class DeltaBaseline:
    """One report's asserted place in a gate + delta pair."""

    comparison_role: str
    outcome: str
    base_sha: str | None = None
    branch: str | None = None
    gate_verdict: str | None = None
    #: Delta reports only: the operand-identity rule that paired the
    #: snapshot's library with the PR's (one of :data:`DELTA_OPERAND_MATCHES`);
    #: ``None`` when not recorded.
    operand_match: str | None = None
    #: ``resolved`` only: the commit whose stored snapshot was used -- the
    #: base itself, or under the Action's ``delta-base-equivalent-paths`` its
    #: path-equivalent first-parent ancestor. ``None`` when not recorded and
    #: for every other outcome (no stored snapshot was used).
    snapshot_sha: str | None = None

    @classmethod
    def from_mapping(cls, data: object) -> DeltaBaseline:
        """Read one record; :class:`DeltaBaselineError` if malformed.

        Unknown keys are refused, not dropped, so a misspelled field never
        reads as an absent one; an empty string reads as absent. The two
        roles' invariants are enforced here, not left to the writer: a delta
        report exists only for a resolved or built base of a named commit, and only
        a delta report records the gate's verdict (a gate report's own
        ``verdict`` *is* the gate's).
        """
        if not isinstance(data, Mapping):
            raise DeltaBaselineError(
                f"must be a JSON object, got {type(data).__name__}"
            )
        unknown = sorted(str(k) for k in data if k not in _KNOWN_FIELDS)
        if unknown:
            raise DeltaBaselineError(
                f"unknown key(s) {', '.join(unknown)}; expected "
                f"{', '.join(sorted(_KNOWN_FIELDS))}"
            )
        role = data.get("comparison_role")
        if role not in DELTA_COMPARISON_ROLES:
            raise DeltaBaselineError(
                f"comparison_role must be one of {', '.join(DELTA_COMPARISON_ROLES)}, got {role!r}"
            )
        outcome = data.get("outcome")
        if outcome not in DELTA_REPORT_OUTCOMES:
            raise DeltaBaselineError(
                f"outcome must be one of {', '.join(DELTA_REPORT_OUTCOMES)}, got {outcome!r}"
            )
        record = cls(
            comparison_role=str(role),
            outcome=str(outcome),
            base_sha=_optional(data, "base_sha", _FULL_SHA),
            branch=_optional(data, "branch", _BRANCH),
            gate_verdict=_optional(data, "gate_verdict", _VERDICT),
            operand_match=_operand_match(data),
            snapshot_sha=_optional(data, "snapshot_sha", _FULL_SHA),
        )
        if record.snapshot_sha is not None:
            if record.outcome != "resolved":
                raise DeltaBaselineError(
                    f"snapshot_sha is recorded only for outcome 'resolved', got "
                    f"{record.outcome!r}: no stored snapshot was used"
                )
            if record.base_sha is None:
                raise DeltaBaselineError(
                    "snapshot_sha needs base_sha: a snapshot stands in for a named base"
                )
        if record.comparison_role == "delta":
            if record.outcome not in DELTA_MEASURED_OUTCOMES:
                raise DeltaBaselineError(
                    f"a delta report needs outcome {' or '.join(DELTA_MEASURED_OUTCOMES)}, "
                    f"got {record.outcome!r}: without a resolved or built base "
                    "there is no delta comparison"
                )
            if record.base_sha is None or record.gate_verdict is None:
                raise DeltaBaselineError(
                    "a delta report needs base_sha and gate_verdict: it must say "
                    "what it was measured against and what the gate decided"
                )
        elif record.outcome not in DELTA_GATE_OUTCOMES:
            raise DeltaBaselineError(
                "base_built is recorded on the delta report only; the base is "
                "built after the gate ran"
            )
        elif record.gate_verdict is not None:
            raise DeltaBaselineError(
                "gate_verdict is recorded on the delta report only; a gate "
                "report's own verdict is the gate's"
            )
        elif record.operand_match is not None:
            raise DeltaBaselineError(
                "operand_match is recorded on the delta report only; the gate "
                "runs before the operands are paired"
            )
        return record

    @classmethod
    def from_report_block(cls, block: object) -> DeltaBaseline:
        """Read a report's ``delta_baseline`` block back (a stored report).

        The inverse of :meth:`to_report_dict`, with the same validation plus
        the two derived keys checked against the record they derive from.
        """
        if not isinstance(block, Mapping):
            raise DeltaBaselineError(
                f"must be a JSON object, got {type(block).__name__}"
            )
        rest = dict(block)
        asserted_by = rest.pop("asserted_by", None)
        decides = rest.pop("decides_exit_code", None)
        if asserted_by != "action":
            raise DeltaBaselineError(
                f"asserted_by must be 'action', got {asserted_by!r}"
            )
        record = cls.from_mapping(rest)
        if decides is not (record.comparison_role == "gate"):
            raise DeltaBaselineError(
                f"decides_exit_code {decides!r} contradicts comparison_role "
                f"{record.comparison_role!r}"
            )
        return record

    def to_report_dict(self) -> dict[str, object]:
        """The report's ``delta_baseline`` block; every key is always present."""
        return {
            "asserted_by": "action",
            "comparison_role": self.comparison_role,
            "decides_exit_code": self.comparison_role == "gate",
            "outcome": self.outcome,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "gate_verdict": self.gate_verdict,
            "operand_match": self.operand_match,
            "snapshot_sha": self.snapshot_sha,
        }
