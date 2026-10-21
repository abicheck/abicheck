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

"""``RootCauseCorrelator`` (G29 Phase 6) evidence helpers shared by
``reporter.py``/``cli_compare_fold.py``'s JSON serialization.

Split out of ``reporter.py`` (AI-readiness file-size cap -- both it and
``reporter_markdown.py``, the more obvious home, were already at or near
the 2000-line hard cap when this Phase 6 follow-up landed) rather than
grown in place. Leaf module: only reaches ``reporter_markdown.py`` (the
``--used-by``/``--required-symbol`` show-only filter and the per-finding
root-cause-evidence lookup) and ``impact.correlation`` (the evidence rank
order), so it introduces no new import-cycle risk.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, cast

from .policy.evaluate import effective_kind_sets
from .report.view import apply_show_only, finding_id
from .reporter_markdown import (
    root_cause_evidence_lookup_for_changes,
    root_cause_lookup_for_changes,
)

if TYPE_CHECKING:
    from .checker_types import DiffResult
    from .model.change import Change


def scoped_only_changes_filtered(
    result: DiffResult, show_only: str | None
) -> list[Change]:
    """``result.scoped_only_changes``, filtered by ``--show-only`` the same
    way the caller's own ``changes`` list already is -- shared so callers
    can never filter the two lists differently (Codex review)."""
    scoped_only = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only:
        scoped_only = apply_show_only(
            scoped_only,
            show_only,
            policy=result.policy,
            kind_sets=effective_kind_sets(result),
            policy_file=result.policy_file,
        )
    return scoped_only


def scoped_only_evidence_lookup(
    result: DiffResult, changes: list[Change], show_only: str | None
) -> dict[str, dict[str, object]]:
    """``finding_id -> root_cause_evidence`` for *changes* plus *result*'s
    filtered ``scoped_only_changes`` (G29 Phase 6, Codex review).

    A finding correlating only via a scoped-only (``--used-by``/
    ``--required-symbol``) sibling needs that sibling in the same
    :func:`~abicheck.impact.correlation.correlate_root_causes` call to be
    recognized -- a lookup over *changes* alone under-correlates, unlike
    ``sarif.to_sarif``'s identical combined computation. Shared by a caller
    rendering only ``changes`` and ``cli_compare_fold.py``'s later
    scoped-only fold-in.
    """
    scoped_only = scoped_only_changes_filtered(result, show_only)
    return root_cause_evidence_lookup_for_changes(changes + scoped_only)


def impact_group_lookup_for_changes(changes: list[Change]) -> dict[str, str]:
    """``finding_id -> impact_group_id`` for every change that is a member of
    a multi-piece ``RootCauseCorrelator`` group (G29 Phase 6 follow-up).

    The value is the correlator group's own ``root_cause_id``, which hashes the
    same ``caused_by_type``-else-``symbol`` key as the report-level grouping, so
    it agrees with ``root_cause_id`` whenever both are set. It differs by
    presence: a bare-symbol ``--used-by`` pair with no ``caused_by_type`` is one
    correlator group that :func:`~abicheck.reporter_markdown.root_cause_for_change`
    leaves ungrouped. Built over the real ``Change`` objects, so a scoped-only
    sibling must be included in *changes* by the caller.
    """
    from .impact.correlation import correlate_root_causes

    lookup: dict[str, str] = {}
    for group in correlate_root_causes(changes):
        for member_change, _level in group.members:
            lookup[finding_id(member_change)] = group.root_cause_id
    return lookup


def impact_assessment_lookups(
    changes: list[Change],
) -> tuple[dict[str, tuple[str, str]], dict[str, dict[str, object]], dict[str, str]]:
    """The three per-finding correlation lookups an ``impactAssessment`` reads,
    over one *changes* list: ``(root_cause, root_cause_evidence, impact_group_id)``.
    Callers pass the combined primary + scoped-only list so a scoped-only
    sibling correlates the same way in every format.
    """
    return (
        root_cause_lookup_for_changes(changes),
        root_cause_evidence_lookup_for_changes(changes),
        impact_group_lookup_for_changes(changes),
    )


def scoped_only_impact_group_lookup(
    result: DiffResult, changes: list[Change], show_only: str | None
) -> dict[str, str]:
    """``finding_id -> impact_group_id`` for *changes* plus *result*'s filtered
    ``scoped_only_changes`` -- the same combined correlator pass
    :func:`scoped_only_evidence_lookup` runs, so a scoped-only sibling that
    joins a finding's correlator group is seen here too (G29 Phase 6
    follow-up: ``impact_group_id`` must reflect the real sibling ``Change``
    objects, not just their ``caused_by_type`` strings).
    """
    scoped_only = scoped_only_changes_filtered(result, show_only)
    return impact_group_lookup_for_changes(changes + scoped_only)


def fold_evidence_summaries(
    evidences: Iterable[dict[str, object] | None],
) -> dict[str, object] | None:
    """Fold several per-finding ``root_cause_evidence`` dicts (each already
    correct on its own) into one group-level summary (G29 Phase 6, Codex
    review): the strongest ``strongest_evidence_level`` seen, and the union
    of every ``evidence_levels`` list, ranked.

    Deliberately not a re-derivation via ``correlate_root_causes`` + a
    ``root_cause_id``-equality lookup -- a report-level group and a
    correlator group can genuinely disagree on membership (a bare-symbol
    pair sharing no ``caused_by_type``: report grouping keeps each its own
    singleton, the correlator's ``caused_by_type or symbol`` key merges
    them -- reproduced by ``--used-by``'s
    ``FUNC_REMOVED``/``CONSUMER_REQUIRED_SYMBOL_REMOVED`` pair), so matching by
    id silently misses group-level evidence for exactly that shape even
    though each finding's own per-finding evidence already shows it.
    Folding already-correct member evidence instead sidesteps that mismatch
    entirely: every member of one real correlator group carries an
    identical summary by construction, so this is a no-op re-derivation in
    the common case. ``None`` when nothing in *evidences* has any.
    """
    from .impact.correlation import EVIDENCE_RANK

    strongest: str | None = None
    levels: set[str] = set()
    found = False
    for member_evidence in evidences:
        if member_evidence is None:
            continue
        found = True
        member_levels = member_evidence.get("evidence_levels") or ()
        levels.update(str(level) for level in cast(Sequence[object], member_levels))
        member_strongest = member_evidence.get("strongest_evidence_level")
        if member_strongest is not None and (
            strongest is None
            or EVIDENCE_RANK.get(str(member_strongest), -1)
            > EVIDENCE_RANK.get(strongest, -1)
        ):
            strongest = str(member_strongest)
    if not found:
        return None
    return {
        "strongest_evidence_level": strongest,
        "evidence_levels": sorted(
            levels, key=lambda level: EVIDENCE_RANK.get(level, -1)
        ),
    }


def root_cause_group_evidence(
    group_changes: list[Change], rc_evidence: dict[str, dict[str, object]]
) -> dict[str, object] | None:
    """A ``root_causes[]`` group's evidence summary, folded from its own
    members' already-correct per-finding ``root_cause_evidence`` (see
    :func:`fold_evidence_summaries` for why this isn't a
    ``root_cause_id``-matched re-derivation)."""
    return fold_evidence_summaries(
        rc_evidence.get(finding_id(c)) for c in group_changes
    )


def entry_root_cause_evidence(entry: dict[str, object]) -> dict[str, object] | None:
    """A serialized finding dict's own ``impact_assessment.root_cause_evidence``,
    or ``None`` when absent."""
    assessment = entry.get("impact_assessment")
    if not isinstance(assessment, dict):
        return None
    evidence = assessment.get("root_cause_evidence")
    return evidence if isinstance(evidence, dict) else None
