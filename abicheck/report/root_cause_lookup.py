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

"""Per-finding root-cause ids and correlator evidence, keyed by ``finding_id``.

The JSON/SARIF ``impact_assessment`` surface and the leaf-change views look a
finding's root cause up here; ``--report-mode root-cause`` groups by the same
key (:func:`~abicheck.report.view.root_cause_key_and_display`).
"""

from __future__ import annotations

from ..checker import Change
from .view import (
    finding_id,
    root_cause_id_for_key,
    root_cause_key_and_display,
)

# ---------------------------------------------------------------------------
# Leaf-change mode helpers
# ---------------------------------------------------------------------------


def root_cause_for_change(
    c: Change, *, referenced_causes: frozenset[str] = frozenset()
) -> tuple[str, str] | None:
    """This change's ``(root_cause_id, root_display)``, or ``None`` when it
    has no real correlation signal (G29 Phase 3 follow-up, ADR-052).

    Uses the exact same grouping key ``--report-mode root-cause`` computes
    (:func:`root_cause_key_and_display`), hashed the same way
    :func:`~abicheck.report.diff_report._to_json_root_cause`/``sarif._root_cause_for``
    already do — so a finding's ``root_cause_id`` here is always identical to
    its ``root_causes[].root_cause_id`` in JSON root-cause mode, or its
    ``properties.rootCauseId`` in SARIF root-cause mode, for the same report.

    Deliberately returns ``None`` for the trivial self-referencing singleton
    case (no ``caused_by_type``, and *c*'s own ``symbol`` isn't referenced by
    any other finding's ``caused_by_type``) — unlike ``--report-mode
    root-cause``'s own grouping (which buckets *every* finding, including
    singletons, since that mode's whole point is showing the full grouping
    structure), a per-finding ``ImpactAssessment.root_cause_id`` naming
    nothing but the finding's own identity is not real information; see
    :func:`root_cause_lookup_for_changes`.
    """
    fid = finding_id(c)
    key, root_display = root_cause_key_and_display(
        c.caused_by_type,
        c.symbol,
        c.kind.value,
        fid,
        referenced_causes=referenced_causes,
    )
    if key == f"finding:{fid}":
        return None
    return root_cause_id_for_key(key), root_display


def root_cause_lookup_for_changes(
    changes: list[Change], *, extra_causes: frozenset[str] = frozenset()
) -> dict[str, tuple[str, str]]:
    """``finding_id -> (root_cause_id, root_display)`` for every change in
    *changes* that has one (G29 Phase 3 follow-up, ADR-052).

    Built once per report (or per self-contained scope, e.g. a suppressed-
    changes list or a scoped-only fold-in) so per-change lookup during
    serialization is O(1) rather than re-deriving ``referenced_causes`` per
    finding. Feeds ``impact.engine.assess_change``'s ``root_cause`` parameter
    — see :func:`root_cause_for_change` for why a finding with no real
    correlation signal is simply absent from the returned dict rather than
    mapped to a self-referencing singleton id.
    """
    referenced_causes = (
        frozenset(c.caused_by_type for c in changes if c.caused_by_type) | extra_causes
    )
    lookup: dict[str, tuple[str, str]] = {}
    for c in changes:
        rc = root_cause_for_change(c, referenced_causes=referenced_causes)
        if rc is not None:
            lookup[finding_id(c)] = rc
    return lookup


def root_cause_evidence_lookup_for_changes(
    changes: list[Change],
) -> dict[str, dict[str, object]]:
    """``finding_id -> root_cause_evidence`` for every change that is a
    member of a G29 Phase 6 ``RootCauseCorrelator`` group (G29 Phase 6
    follow-up: wiring the correlator's output into the JSON/SARIF
    ``impact_assessment`` surface).

    Deliberately independent of :func:`root_cause_lookup_for_changes` above:
    that function's ``root_cause_id``/``root_cause_display`` grouping covers
    *any* two findings sharing a ``caused_by_type``/``symbol``, for every
    ``ChangeKind``; :func:`~abicheck.impact.correlation.correlate_root_causes`
    covers only the load-failure kinds its own module docstring names
    (``FUNC_REMOVED``/``INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API``/
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED``),
    ranked by evidence strength, and drops a symbol with only one correlated
    piece. Built directly from the actual ``Change`` objects the correlator
    grouped — not by matching root-cause ids after the fact — so this stays
    correct independent of whether the two functions' grouping keys happen
    to agree for a given finding.

    Scoped to *changes* only: unlike ``root_cause_lookup_for_changes``, this
    has no ``extra_causes`` parameter, so a correlated sibling that exists
    only as a scoped-only (``--used-by``/``--required-symbol``) finding
    appended to a report after this runs is not seen — deliberately left for
    a follow-up, mirroring this module's own "Deliberately not implemented
    this slice" precedent rather than growing this helper's contract
    unverified.
    """
    from ..impact.correlation import correlate_root_causes

    lookup: dict[str, dict[str, object]] = {}
    for group in correlate_root_causes(changes):
        for member_change, level in group.members:
            lookup[finding_id(member_change)] = {
                "evidence_level": level,
                "strongest_evidence_level": group.strongest_evidence_level,
                "evidence_levels": list(group.evidence_levels),
            }
    return lookup
