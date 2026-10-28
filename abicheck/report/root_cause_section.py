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

"""The structured intermediate for ``--report-mode root-cause``'s root-cause section.

Moved out of ``reporter_markdown.py`` (which holds the legacy Markdown helpers)
so that module stays under its size baseline. The function is unchanged except
for its *non_gating* budget: a gating finding is always itemised, and a
non-gating one is itemised up to the shared Markdown budget and counted beyond
it (``report/gating_budget.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..model.evidence_status import EvidenceStatus
from ..reporter_markdown import _format_change_md
from .cross_source_evolution import cross_source_evolution_md_suffix
from .markdown_budget import ITEM_LIMIT
from .markdown_text import md_text
from .policy_facts import evidence_status_of, impact_text_for
from .render_markdown import RootCauseGroupData, RootCauseSectionData
from .view import finding_id, group_changes_by_root_cause, root_cause_key_and_display

if TYPE_CHECKING:
    from ..model.change import Change

__all__ = ["compute_root_cause_section"]


def compute_root_cause_section(
    changes: list[Change],
    scoped_only: list[Change],
    missing_labels: list[str],
    blocks: bool,
    missing_kind: str,
    *,
    contract_evaluation: bool,
    evidence_tiers: Sequence[str] = (),
    non_gating: frozenset[int] = frozenset(),
) -> RootCauseSectionData | None:
    """The structured intermediate for ``--report-mode root-cause``'s
    "## Root Causes" section.

    *non_gating* is the ``id()`` set of the changes the gate does not count
    (:func:`~abicheck.report.gating_budget.non_gating_change_ids`). A gating
    finding is always itemised; a non-gating one is itemised up to the shared
    budget (:data:`~abicheck.report.markdown_budget.ITEM_LIMIT` lines across the
    section) and counted beyond it, so a flood of additions cannot make this
    view unbounded. The default, empty set, itemises every finding.

    Groups *changes* + *scoped_only* by root cause (:func:`group_changes_by_root_cause`)
    and merges each *missing_labels* entry into a matching group -- or starts
    a new singleton group -- by the identical key
    :func:`root_cause_key_and_display` computes. Returns ``None`` (no
    section at all) only when there is neither a real group nor a missing
    label to show.

    *evidence_tiers* (``DiffResult.evidence_tiers``), when given, lets this
    function qualify an ``UNATTRIBUTED`` finding's impact text here too
    (Codex review, fresh evidence: this root-cause path kept the
    unconditional text even after the full/leaf Markdown views were
    fixed). The resolved impact string -- not just the evidence status --
    is computed here and handed to ``_format_change_md`` as a plain
    value: the earlier fix passed a resolved ``EvidenceStatus`` in and let
    the renderer call ``impact_for()`` itself, which still left that
    registry lookup on the render side (Codex review, fresh evidence --
    ``report/AGENTS.md``'s compute/render split treats a per-change
    ``impact_for()`` call as a report decision the compute half owes the
    renderer, not a formatting choice).

    A *scoped_only* member keeps its ``EvidenceStatus.CONSUMER_PROVEN``
    override rather than being re-scored from *evidence_tiers* like an
    ordinary comparison finding -- JSON (``reporter.to_json``'s
    ``_scoped_only_change_dict``) and SARIF already stamp scoped_only
    findings this way (they are proven by the supplied consumer's own
    import table, independent of what evidence the library-to-library
    comparison itself carries), so recomputing via
    ``evidence_status_for_result`` here would contradict those two formats
    and could demote a consumer-proven finding to "plausible, not
    confirmed" (Codex review, fresh evidence).
    """
    groups = group_changes_by_root_cause(changes + scoped_only)
    if not groups and not missing_labels:
        return None

    scoped_only_ids = {finding_id(c) for c in scoped_only}

    order: list[str] = []
    root_by_key: dict[str, str] = {}
    finding_lines_by_key: dict[str, list[str]] = {}
    count_by_key: dict[str, int] = {}

    def _resolved_impact(c: Change) -> str | None:
        kind = getattr(c, "kind", None)
        if kind is None:
            return None
        evidence_status = (
            EvidenceStatus.CONSUMER_PROVEN
            if finding_id(c) in scoped_only_ids
            else evidence_status_of(c, evidence_tiers)
        )
        return impact_text_for(kind, evidence_status)

    # Parallel to `finding_lines_by_key`: True where the line gates the run
    # (always itemised). A missing contract label gates exactly when the
    # scoped gate blocks.
    gating_by_key: dict[str, list[bool]] = {}
    for key, root_display, group_changes in groups:
        order.append(key)
        root_by_key[key] = root_display
        finding_lines_by_key[key] = [
            _format_change_md(c, _resolved_impact(c))
            + cross_source_evolution_md_suffix(c)
            for c in group_changes
        ]
        gating_by_key[key] = [id(c) not in non_gating for c in group_changes]
        count_by_key[key] = len(group_changes)

    if missing_labels:
        referenced_causes = frozenset(
            c.caused_by_type for c in changes + scoped_only if c.caused_by_type
        )
        severity_tag = "breaking" if blocks else "compatible"
        for label in missing_labels:
            key, root_display = root_cause_key_and_display(
                None,
                label,
                missing_kind,
                label,
                referenced_causes=referenced_causes,
            )
            line = (
                f"- {md_text(label, code=True)} is required but missing from the new "
                f"library ({severity_tag})"
            )
            # ADR-049 Phase 3 (Codex review, fresh evidence): the
            # non-root-cause markdown/text/review fold-in
            # (cli_compare_fold._fold_scoped_compat_into_text) already
            # tags a missing-contract label with its stamped decision;
            # this root-cause path builds the identical label shape
            # independently and was missing the same treatment, so
            # --report-mode root-cause silently dropped the contract
            # decision for this one finding shape. A missing-contract
            # label has no Change object of its own to read an
            # already-stamped decision off of (unlike scoped_only,
            # rendered via _format_change_md above), so unlike every
            # other contract-rendering site in this fix, this one
            # genuinely needs the caller's own --contract
            # intent threaded through explicitly.
            if contract_evaluation:
                from ..contract_scoped_promotion import (
                    stamp_explicit_scope_contract_evaluation,
                )

                label_decision: dict[str, object] = {}
                stamp_explicit_scope_contract_evaluation(label_decision)
                line += (
                    f" [contract: {label_decision['contract_relevance']} "
                    f"({label_decision['contract_reason_code']}), "
                    f"assurance: {label_decision['contract_assurance']}]"
                )
            if key in finding_lines_by_key:
                finding_lines_by_key[key].append(line)
                gating_by_key[key].append(blocks)
                count_by_key[key] += 1
            else:
                order.append(key)
                root_by_key[key] = root_display
                finding_lines_by_key[key] = [line]
                gating_by_key[key] = [blocks]
                count_by_key[key] = 1

    # The shared budget: every gating line, and the first ITEM_LIMIT
    # non-gating lines across the whole section, in group order. A group with
    # nothing left to show is counted, not listed.
    budget = ITEM_LIMIT
    groups_out: list[RootCauseGroupData] = []
    omitted_groups = 0
    omitted_findings = 0
    for key in order:
        shown: list[str] = []
        hidden = 0
        for line, gating in zip(finding_lines_by_key[key], gating_by_key[key]):
            if gating:
                shown.append(line)
            elif budget > 0:
                budget -= 1
                shown.append(line)
            else:
                hidden += 1
        if not shown:
            omitted_groups += 1
            omitted_findings += hidden
            continue
        groups_out.append(
            RootCauseGroupData(
                root_display=root_by_key[key],
                count=count_by_key[key],
                finding_lines=tuple(shown),
                omitted=hidden,
            )
        )
    return RootCauseSectionData(
        groups=tuple(groups_out),
        omitted_groups=omitted_groups,
        omitted_findings=omitted_findings,
    )
