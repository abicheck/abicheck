# Copyright 2026 Nikolay Petrov
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

"""``RootCauseCorrelator`` (G29 Phase 6) — a composer, not a detector.

The plan's Phase 6 explicitly rejects a new ``ChangeKind`` per graph edge;
instead the four existing kinds that each independently answer "does a load
against the new library fail because of *this* symbol" —
:data:`~abicheck.checker_policy.ChangeKind.FUNC_REMOVED` (artifact-level: the
symbol vanished from the export table), :data:`~abicheck.checker_policy.
ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API` (call-graph-level: an
internal dependency of a public entry point changed/vanished — ``internal_
leak.py``), :data:`~abicheck.checker_policy.ChangeKind.
CONSUMER_REQUIRED_SYMBOL_REMOVED` (consumer-level: a real ``--used-by``
binary's own undefined symbol no longer resolves — ``appcompat.py``), and
:data:`~abicheck.checker_policy.ChangeKind.CONSUMER_RUNTIME_LOAD_FAILED`
(runtime-level: the same consumer binary was actually dynamically loaded
against the new library and failed to resolve — ``cli_helpers_compare.py``)
— get folded into one :class:`RootCauseGroup` per underlying symbol, each
member tagged with its own evidence level, rather than reported as four
independent findings a reader has to manually notice share a cause.

This module is deliberately narrower than ``reporter_markdown``'s existing
``--report-mode root-cause`` grouping
(:func:`~abicheck.reporter_markdown._group_changes_by_root_cause`): that
grouping buckets *every* finding by ``caused_by_type`` (any redundant-change
family, not just this one), is report-mode-only, and keeps every group
including size-1 singletons since its whole point is showing full report
structure. ``correlate_root_causes`` only ever groups the four kinds above,
lives independently of any report renderer (so ``impact.engine`` can use it
without a ``reporter_markdown`` import — the cross-package direction that
kind of dependency would need to run and doesn't exist today), and drops a
size-1 "group" (a piece with no correlated sibling carries no root-cause
information beyond what its own finding already states).

Correlation key: this finding's own ``caused_by_type`` when set (the
call-graph/consumer overlay kinds set it to the *symbol they are actually
about* — ``dname``/``sym`` in their respective producers — which is the
identity a sibling finding's own ``symbol`` field carries), else the
finding's own ``symbol`` (the direct-removal kind has no ``caused_by_type``
of its own; its ``symbol`` *is* the shared identity other findings key
against). This mirrors ``_root_cause_key_and_display``'s
``caused_by_type``-else-``symbol`` precedence, applied here to a fixed,
four-kind family instead of every ``ChangeKind`` in the report.

Evidence level is derived from :data:`EVIDENCE_LEVEL_BY_KIND` (a per-``kind``
default), then promoted when ``Change.reachability_kind`` states a stronger
tier the kind alone wouldn't reveal (see :func:`_evidence_level_for`) — this
covers ``appcompat._attach_consumer_impact``, which enriches an *existing*
``FUNC_REMOVED`` in place with ``reachability_kind="consumer_proven"``
instead of emitting a separate ``CONSUMER_REQUIRED_SYMBOL_REMOVED`` overlay
whenever ``uncovered_missing_symbols`` finds the symbol already covered
(Codex review, fresh evidence: without this promotion, a shared Change
carrying real consumer proof read back as the weaker kind-only
``artifact_proven``, silently understating a group's
``strongest_evidence_level``). What this promotion does **not** attempt: a
``FUNC_REMOVED`` from a header-only comparison (no export table ever
consulted) still reads as ``artifact_proven`` by default, since nothing in
:class:`~abicheck.checker_types.Change` records *which* evidence tier
actually produced a given finding — the same per-finding-provenance gap
``AGENTS.md``'s "Evidence-provider model" entry documents as a deliberately
deferred, multi-day project of its own, not a drive-by fix here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..checker_policy import ChangeKind
from ..checker_types import Change

#: Evidence tier label per correlated finding kind, weakest to strongest
#: proof that a real consumer is actually affected — an export-table removal
#: alone is real but says nothing about who depends on it; a runtime load
#: failure against an actual consumer binary is the strongest evidence this
#: codebase can produce.
EVIDENCE_LEVEL_BY_KIND: dict[ChangeKind, str] = {
    ChangeKind.FUNC_REMOVED: "artifact_proven",
    ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API: "call_graph_proven",
    ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED: "consumer_proven",
    ChangeKind.CONSUMER_RUNTIME_LOAD_FAILED: "runtime_proven",
}

#: Rank order for :data:`EVIDENCE_LEVEL_BY_KIND`'s values, weakest first —
#: used only to pick a group's :attr:`RootCauseGroup.strongest_evidence_level`
#: and to order :attr:`RootCauseGroup.evidence_levels`; not exposed as a
#: standalone numeric score since the levels themselves are the stable,
#: documented vocabulary.
_EVIDENCE_RANK = {
    level: rank
    for rank, level in enumerate(dict.fromkeys(EVIDENCE_LEVEL_BY_KIND.values()))
}


@dataclass(frozen=True)
class RootCauseGroup:
    """One symbol's correlated evidence across the four load-failure kinds.

    ``members`` is ``(change, evidence_level)`` pairs, in the order the
    members were encountered in the input list — never re-sorted by evidence
    strength, so a caller rendering "as detected" order gets it, while
    :attr:`strongest_evidence_level` answers the "how sure are we" question
    directly.
    """

    root_cause_id: str
    root_display: str
    members: tuple[tuple[Change, str], ...]

    @property
    def evidence_levels(self) -> tuple[str, ...]:
        """This group's distinct evidence levels, weakest first."""
        seen = {level for _, level in self.members}
        return tuple(sorted(seen, key=lambda level: _EVIDENCE_RANK.get(level, -1)))

    @property
    def strongest_evidence_level(self) -> str | None:
        """The single most-trustworthy evidence level in this group, or
        ``None`` for an (unreachable in practice) empty group."""
        if not self.members:
            return None
        return max(
            (level for _, level in self.members),
            key=lambda level: _EVIDENCE_RANK.get(level, -1),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "root_cause_id": self.root_cause_id,
            "root_display": self.root_display,
            "strongest_evidence_level": self.strongest_evidence_level,
            "evidence_levels": list(self.evidence_levels),
            "members": [
                {
                    "finding_symbol": c.symbol,
                    "kind": c.kind.value,
                    "evidence_level": level,
                }
                for c, level in self.members
            ],
        }


def _evidence_level_for(change: Change) -> str:
    """*change*'s evidence level: its kind's default from
    :data:`EVIDENCE_LEVEL_BY_KIND`, promoted to ``"consumer_proven"`` when
    ``reachability_kind`` says so and that outranks the kind default.

    Only ever called for a *change* whose ``kind`` is already a key of
    :data:`EVIDENCE_LEVEL_BY_KIND` (:func:`correlate_root_causes` filters to
    that set before this runs), so the base lookup is total, not a
    ``.get()`` with a ``None`` fallback.
    """
    base = EVIDENCE_LEVEL_BY_KIND[change.kind]
    if (
        change.reachability_kind == "consumer_proven"
        and _EVIDENCE_RANK["consumer_proven"] > _EVIDENCE_RANK[base]
    ):
        return "consumer_proven"
    return base


def _correlation_key(change: Change) -> str | None:
    """The shared symbol identity *change*'s evidence is actually about, or
    ``None`` when it carries neither — never reachable for the four kinds
    this module correlates in practice (each always sets at least one), but
    checked defensively since :class:`Change` itself allows both to be
    unset."""
    return change.caused_by_type or change.symbol or None


def correlate_root_causes(changes: list[Change]) -> list[RootCauseGroup]:
    """Group *changes* whose kind is one of :data:`EVIDENCE_LEVEL_BY_KIND`'s
    four families by shared symbol identity, in first-seen order.

    A symbol with only one correlated piece is not returned as a group (see
    the module docstring) — call this to find *multi-piece* root causes,
    not as a substitute for iterating ``changes`` directly. Every other
    ``ChangeKind`` is ignored entirely; this is intentionally not a general
    ``caused_by_type`` grouping (``reporter_markdown``'s ``--report-mode
    root-cause`` machinery already does that, for every kind).
    """
    by_key: dict[str, list[Change]] = {}
    order: list[str] = []
    for change in changes:
        level = EVIDENCE_LEVEL_BY_KIND.get(change.kind)
        if level is None:
            continue
        key = _correlation_key(change)
        if not key:
            continue
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(change)

    groups: list[RootCauseGroup] = []
    for key in order:
        members = by_key[key]
        if len(members) < 2:
            continue
        root_cause_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        member_pairs = tuple((c, _evidence_level_for(c)) for c in members)
        groups.append(
            RootCauseGroup(
                root_cause_id=root_cause_id, root_display=key, members=member_pairs
            )
        )
    return groups
