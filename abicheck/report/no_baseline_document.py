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

"""The ``compare --no-baseline`` audit document, and what it may be rendered as.

A leaf holding only the *contract* the audit's compute half
(:mod:`abicheck.report.no_baseline`) produces and its render halves consume
-- the same shape this package's own ``document.py`` has relative to its
``render_*`` siblings.

It exists as a third module rather than living beside the compute half
because the machine-format renderers moved out to
:mod:`abicheck.report.no_baseline_render` when that file crossed the
800-line cap, and a renderer needs the document's type. Importing it back
from ``no_baseline`` formed a real cycle
(``no_baseline -> no_baseline_render -> no_baseline``) that the
``import-cycle-growth`` gate rejects -- correctly, since the dependency
direction is what tells a reader which half owns the shape. Both halves now
depend on this leaf, and nothing depends on both of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cross_source_evolution import CrossSourceEvolutionSummary
    from .finding import ReportFinding

__all__ = [
    "NO_BASELINE_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_SUPPORTED_FORMATS",
    "NO_BASELINE_UNSUPPORTED_FORMATS",
    "NoBaselineDocument",
]


#: Independent of ``reporter.REPORT_SCHEMA_VERSION`` (the two-sided report's
#: own schema) -- this report carries a different shape (no ``old_version``,
#: no ``verdict``, no addition/removal summary), so it gets its own counter
#: rather than borrowing one that promises a shape this report doesn't have.
#:
#: ``2.0``: the audit's cross-source/candidate-side findings reach the
#: document (``findings``, ``suppressed_findings``, ``suppressed_count``,
#: ``cross_source_evolution``, ``pattern_preprocessor_scan``). ``1.0``
#: always emitted ``"changes": []``, which was the gap, not the schema's
#: intent. ``suppressed_findings`` landed in the same unreleased ``2.0``
#: rather than as a ``2.1``: a suppressed finding disappearing entirely was
#: a defect in this shape, not a later addition to a shipped one, and
#: version numbers exist to warn consumers of a *published* change.
NO_BASELINE_REPORT_SCHEMA_VERSION = "2.1"

#: Formats a ``--no-baseline`` audit renders one-sided.
#:
#: ``json``/``markdown`` are the audit's own native shapes. ``sarif`` and
#: ``junit`` were added once the audit had a real finding set to carry:
#: both are *findings* formats with no verdict slot to leave empty (a SARIF
#: run is a list of results with rule ids and levels; a JUnit suite is a
#: list of test cases), which is exactly what a single-build audit
#: produces, and both are how a CI job consumes one -- code scanning upload
#: and test-report annotation respectively. ``oneline`` is the "just tell
#: me" flow and needs one sentence.
NO_BASELINE_SUPPORTED_FORMATS = frozenset(
    {"json", "markdown", "sarif", "junit", "oneline"}
)

#: Formats that stay a declared usage error, and why (ADR-068 D2 ruling,
#: 2026-09-09 -- recorded here rather than left undated in a plan).
#:
#: Both are *narrative* renderings built around a compatibility comparison,
#: not projections of a finding list:
#:
#: * ``html`` -- the HTML report's whole information architecture is a
#:   verdict badge, an OLD -> NEW version headline, and
#:   addition/removal/modification tables. A single-build audit has no
#:   verdict (D2 forbids one), no OLD version, and no additions or
#:   removals, so a one-sided HTML page is a *new page design* rather than
#:   a projection of the document below -- genuinely different work from
#:   the four formats above, none of which needed a layout decision.
#: * ``review`` -- a compact GitHub-facing digest whose content is
#:   literally "what changed between these two releases, and should you
#:   ship it": verdict, counts by direction, release recommendation,
#:   manual-review banner. With no baseline there is no change to review
#:   and no release to recommend; the honest digest is ``oneline``, which
#:   is supported.
#:
#: Tracked in ``docs/contribute/plans/one-comparison-product.md`` (Phase 2e
#: follow-up) and ``docs/contribute/known-gaps.md``. Not a silent omission:
#: the CLI's usage error names this ruling.
NO_BASELINE_UNSUPPORTED_FORMATS = frozenset({"html", "review"})


@dataclass(frozen=True)
class NoBaselineDocument:
    """The one frozen, plain-value audit document every format projects.

    Holds resolved values only -- no ``DiffResult``, no live policy
    objects -- so a renderer cannot re-derive a verdict or reach past what
    the compute half decided.
    """

    library: str
    new_version: str
    #: OLD's acquisition state, always ``"declared_absent"`` today.
    old_acquisition_state: str
    evidence_tiers: tuple[str, ...]
    #: One entry per candidate-side finding, in emission order.
    findings: tuple[ReportFinding, ...]
    #: The findings a ``--suppress`` rule matched, same resolution, kept
    #: alongside rather than dropped: ``vision.md``'s "Record before
    #: disposing" rule requires "detected, then suppressed by rule X" to
    #: stay visible on a passing run, never to read as "nothing found".
    suppressed: tuple[ReportFinding, ...]
    evolution: CrossSourceEvolutionSummary | None
    pattern_preprocessor_scan: dict[str, Any] | None
    run_outcome: dict[str, Any]
    comparison_scope: dict[str, Any]
    #: ADR-049 Phase 7's orthogonal coverage contribution, and the total
    #: exit code this run reports. Both resolved compute-side so no
    #: renderer computes an exit code of its own.
    coverage_exit_contribution: int
    exit_code: int
    #: Every orthogonal axis's own contribution, keyed as in
    #: ``no_baseline.NO_BASELINE_EXIT_AXIS_NOTICES`` and resolved from the
    #: same function ``exit_code`` above is folded from. Carried so a
    #: renderer can *explain* a nonzero exit instead of only reporting one:
    #: the Markdown projection stated the coverage axis alone, so a missed
    #: evidence contract exited 7 beside a report that said nothing about
    #: it (Codex review, P2). Default ``()``-equivalent empty mapping keeps
    #: a hand-constructed document (tests) valid.
    exit_axes: Mapping[str, int] = field(default_factory=dict)
