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

"""ADR-070 D6's report section: a release's ``analysis_assurance``.

The ``compute_*``/``render_*`` split this package's ``AGENTS.md`` asks for,
and the same shape :mod:`abicheck.report.comparison_scope` already uses for
ADR-065's axis: the assurance axis is *decided* by
``policy.release_assurance.resolve_release_assurance_decision`` (the members,
the setting, the aggregate status, the one ``0``/``1``);
:func:`build_release_assurance_section` projects that already-made decision
into one JSON-shaped mapping, and :func:`release_assurance_notice` formats
it. Nothing here computes a status or a contribution -- a caller that needs
the exit value reads it off the decision it resolved, never off this module.

``section`` is ``None`` unless ``assurance.require_complete`` is in effect,
which is what keeps every pre-existing release report byte-identical
(ADR-070 D4) -- the same rule under which the release report gains its
contract-coverage fields only under ``--contract``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..policy.release_assurance import (
    ReleaseAssuranceDecision,
    release_assurance_diagnostic,
)

__all__ = [
    # Re-exported so a `frontends` module can *name* the decision type it is
    # handed without importing `policy` directly (forbidden by
    # `architecture/modules.yaml`; `frontends -> report` is the sanctioned
    # seam). This module already depends on it for every signature below, so
    # the re-export adds no new edge -- see `release_exit.py`'s own use.
    "ReleaseAssuranceDecision",
    "ReleaseAssuranceTerms",
    "build_release_assurance_section",
    "release_assurance_notice",
    "release_assurance_terms",
]

#: Schema version for the release-level ``analysis_assurance`` block,
#: versioned independently of ``REPORT_SCHEMA_VERSION`` for the same reason
#: ``analysis_assurance.ANALYSIS_ASSURANCE_SCHEMA_VERSION`` is: a consumer
#: can version-check this sub-object without caring about the report's own
#: MAJOR.MINOR. Deliberately its *own* counter and not that one's: this is
#: the release fold, not one pair's ``AnalysisAssurance``, and the two can
#: gain fields independently.
RELEASE_ASSURANCE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ReleaseAssuranceTerms:
    """One resolved :class:`ReleaseAssuranceDecision` plus its JSON
    projection, built exactly once so the summary block, the stderr notice,
    and the number the exit fold read cannot disagree.

    ``section`` is ``None`` when ``assurance.require_complete`` was not in
    effect. The exit contribution is deliberately *not* mirrored here: read
    it off ``decision``, the policy-owned object.
    """

    decision: ReleaseAssuranceDecision
    section: dict[str, Any] | None

    @property
    def status(self) -> str:
        """The decided aggregate status."""
        return self.decision.status

    @property
    def require_complete(self) -> bool:
        """Whether ``assurance.require_complete`` was in effect."""
        return self.decision.require_complete


def release_assurance_terms(
    decision: ReleaseAssuranceDecision,
) -> ReleaseAssuranceTerms:
    """Project an already-resolved *decision* (never members + a setting: the
    deciding happened in ``policy``) into :class:`ReleaseAssuranceTerms`."""
    return ReleaseAssuranceTerms(
        decision=decision,
        section=(
            build_release_assurance_section(decision)
            if decision.require_complete
            else None
        ),
    )


def build_release_assurance_section(
    decision: ReleaseAssuranceDecision,
) -> dict[str, Any]:
    """The release's ``analysis_assurance`` mapping (ADR-070 D6).

    ``incomplete_members`` names the members that fell short and why rather
    than only counting them -- a count is not actionable. ``member_count`` is
    every member the fold saw, so a reader can tell "1 of 9" from "1 of 1".
    Every key is always present once the setting is on, ``0``/``[]``
    included: an omitted key reads as "not asked", which is exactly what this
    block is not.
    """
    return {
        "schema_version": RELEASE_ASSURANCE_SCHEMA_VERSION,
        "status": decision.status,
        "require_complete": decision.require_complete,
        "member_count": len(decision.members),
        "incomplete_member_count": decision.incomplete_member_count,
        "exit_contribution": decision.exit_contribution,
        "incomplete_members": [m.to_dict() for m in decision.incomplete_members],
    }


def release_assurance_notice(
    decision: ReleaseAssuranceDecision, *, base_exit: int = 0
) -> str | None:
    """The one stderr line for a release's incomplete assurance, or ``None``.

    A thin pass-through to the policy-owned
    ``release_assurance_diagnostic`` -- present so every release consumer
    reaches the axis's three views (section, notice, contribution) through
    this one module, the way ``comparison_scope`` already works, rather than
    some importing ``report`` and others ``policy``.
    """
    return release_assurance_diagnostic(decision, base_exit=base_exit)
