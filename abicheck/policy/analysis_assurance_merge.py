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

"""Roll several libraries' :class:`AnalysisAssurance` blocks into one.

A multi-library comparison (``compat check`` over a descriptor whose
``<libs>`` names more than one library) produces one assurance block per
library and must publish one for the release. Dropping them instead is not
neutral: every reporter then omits the block entirely, so a member whose
evidence was ``partial``, ``failed`` or ``not_comparable`` contributes no
release-wide signal at all and ``--require-complete-analysis`` stops gating
on it -- silently upgrading incomplete evidence to no stated concern, which
is the inversion of ``vision.md``'s "weaker evidence narrows conclusions".

**What is and is not aggregated.** The *judgement* fields roll up to their
weakest member, because a release is only as well-evidenced as its least
well-evidenced library. The per-library *accounting* blocks
(``target_accounting``, ``translation_units``, ``export_accounting``) do
not: summing one library's translation units with another's answers no
question anyone asked, and there is no release-level object for them to
describe. They are left at their own documented "nothing was requested /
nothing was evaluated" defaults, which states the absence rather than
fabricating a zero -- the same reasoning
:data:`abicheck.compat.multi_library._FIELD_POLICY` applies to the other
per-library objects it drops. A note on the merged block says so, so a
reader is never left inferring it from an empty accounting.

This lives beside :mod:`abicheck.analysis_assurance` rather than inside it
for the reason ``analysis_assurance_layout.py`` does: that module sits at a
``no_growth`` baseline in ``architecture/debt.yaml`` and the way to respect
one is to give new responsibility its own owner, never to grow the file.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..analysis_assurance import AnalysisAssurance
from ..evidence_depth import DEPTH_RANK

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["FIELD_MERGE_POLICY", "merge_analysis_assurance"]

#: How each :class:`AnalysisAssurance` field is combined, so a field added
#: later cannot be silently reset to its constructor default.
#:
#: That is not hypothetical: ``l3_context_status`` was missed when this
#: module was written -- the axes were enumerated by reading the dataclass,
#: which is exactly the review that misses one -- so every multi-library
#: result published ``"not_evaluated"`` for build evidence regardless of what
#: its members observed (Codex review). ``tests/
#: test_multi_library_assurance.py`` fails on any field absent from this
#: table, the same way ``multi_library._FIELD_POLICY`` does for
#: ``DiffResult``.
#:
#: * ``"weakest"``  -- an axis; the merged value is the least reassuring one.
#: * ``"derived"``  -- computed by :func:`merge_analysis_assurance` itself
#:   (status, the depth trio, notes, the detector union).
#: * ``"per_library"`` -- accounting that describes one library and has no
#:   release-level meaning; deliberately left at its default, which states
#:   "nothing requested / nothing evaluated" rather than fabricating a zero.
FIELD_MERGE_POLICY: dict[str, str] = {
    "schema_version": "derived",
    "status": "derived",
    "requested_depth": "derived",
    "requested_depth_source": "derived",
    "effective_depth": "derived",
    "depth_satisfied": "derived",
    "notes": "derived",
    "layout_unverified_detectors": "derived",
    "l0_context_status": "weakest",
    "header_context_status": "weakest",
    "dwarf_context_status": "weakest",
    "l3_context_status": "weakest",
    "fact_set_comparability": "weakest",
    "graph_completeness": "weakest",
    "schema_staleness_status": "weakest",
    "target_accounting": "per_library",
    "translation_units": "per_library",
    "export_accounting": "per_library",
}

#: ``status`` worst-last, so :func:`max` over the index picks the weakest.
#:
#: ``not_requested`` deliberately ranks *below* ``complete``: it is the
#: absence of a claim, not a good one, and a release reported ``complete``
#: because one member never had its assurance computed would overstate what
#: was checked. An unrecognised value ranks worst -- for the same reason
#: ``multi_library._WORST_SCALES`` does it, an unknown assurance level is
#: not evidence of a good one.
_STATUS_WORST_LAST: tuple[str, ...] = (
    "complete",
    "not_requested",
    "partial",
    "not_comparable",
    "failed",
)


def _worst_status(values: Sequence[str]) -> str:
    def rank(value: str) -> int:
        try:
            return _STATUS_WORST_LAST.index(value)
        except ValueError:
            return len(_STATUS_WORST_LAST)

    return max(values, key=rank)


#: One explicit worst-last scale per axis field, replacing a lexicographic
#: ``sorted(...)[0]`` pick over the non-best values.
#:
#: The ordering rule is a single question asked of each label: **how much
#: assurance does this label assert?** Weakest -- the label asserting the
#: least -- goes last, so :func:`max` over the index picks it. That is the
#: direction ``AGENTS.md``'s "weaker evidence narrows conclusions" requires
#: of a release-wide roll-up: the merged label may understate what some
#: member achieved, never overstate what every member supports.
#:
#: This is why a *no-evidence* label ranks below a *detected-defect* one on
#: every axis that has both (``not_collected`` below ``degraded``,
#: ``not_evaluated`` below ``asymmetric``): "degraded" asserts a graph was
#: collected release-wide, which is false the moment one member collected
#: none -- the exact overstatement Codex's review named. Nothing is lost by
#: ranking it that way, because :func:`merge_analysis_assurance` unions every
#: member's own ``notes``, so the specific detected defect is still reported;
#: only the one-word release-wide label defers to the least-claiming member.
#:
#: The lexicographic pick this replaces got that backwards by accident rather
#: than by design: it returned ``"degraded"`` for {``degraded``,
#: ``not_collected``} and ``"asymmetric"`` for {``asymmetric``,
#: ``not_evaluated``} purely because ``d`` < ``n`` and ``a`` < ``n``.
_AXIS_WORST_LAST: dict[str, tuple[str, ...]] = {
    "l0_context_status": ("clean", "asymmetric", "not_evaluated"),
    "header_context_status": (
        "clean",
        "drift_detected",
        "asymmetric",
        "not_evaluated",
    ),
    "dwarf_context_status": ("clean", "asymmetric", "not_evaluated"),
    "l3_context_status": ("clean", "asymmetric", "not_evaluated"),
    "fact_set_comparability": (
        "comparable",
        "inconsistent",
        "unknown",
        "not_applicable",
    ),
    "graph_completeness": (
        "complete",
        "degraded",
        "narrowed",
        "unknown",
        "not_collected",
    ),
    "schema_staleness_status": ("clean", "degraded", "not_evaluated"),
}


def _weakest(values: Sequence[str], *, axis: str) -> str:
    """The least-claiming of *values* on *axis*'s :data:`_AXIS_WORST_LAST` scale.

    An unrecognised value wins outright, for the same reason
    :func:`_worst_status` ranks one worst: a label this build cannot place
    is not evidence of a good one. Deterministic for two equally-unplaceable
    values by sorting them, so the merged block never depends on which
    library was read first.
    """
    scale = _AXIS_WORST_LAST[axis]
    unranked = sorted({v for v in values if v not in scale})
    if unranked:
        return unranked[0]
    return max(values, key=scale.index)


def _shallowest_depth(values: Sequence[str]) -> str | None:
    """The least-deep depth among *values* on ``DEPTH_RANK``'s ladder.

    An unranked value wins outright: like the status scale above, evidence
    this build cannot place is not evidence of depth.
    """
    known = [v for v in values if v in DEPTH_RANK]
    if len(known) != len(values):
        return sorted(set(values) - set(known))[0]
    return min(known, key=lambda v: DEPTH_RANK[v]) if known else None


def merge_analysis_assurance(
    blocks: Sequence[AnalysisAssurance | None],
) -> AnalysisAssurance | None:
    """One release-wide block from *blocks*, or ``None`` if none was given.

    ``None`` entries are members that carried no block at all; they are
    skipped rather than treated as ``complete``.
    """
    present = [b for b in blocks if isinstance(b, AnalysisAssurance)]
    if not present:
        return None
    if len(present) == 1:
        return present[0]

    requested = {b.requested_depth for b in present if b.requested_depth is not None}
    effective = [b.effective_depth for b in present if b.effective_depth is not None]
    satisfied = [b.depth_satisfied for b in present if b.depth_satisfied is not None]

    notes: list[str] = []
    for b in present:
        for note in b.notes:
            if note not in notes:
                notes.append(note)
    notes.append(
        "Release-wide roll-up over "
        f"{len(present)} libraries: each judgement axis reports its weakest "
        "member. Per-library target/translation-unit/export accounting is "
        "not aggregated and is reported per library, not here."
    )

    layout_unverified = sorted(
        {d for b in present for d in b.layout_unverified_detectors}
    )

    return dataclasses.replace(
        AnalysisAssurance(),
        status=_worst_status([b.status for b in present]),  # type: ignore[arg-type]
        # A depth the members disagree on was not one request for the
        # release, so the release requested none; the effective depth is the
        # shallowest any member reached, and the request is satisfied only if
        # it was satisfied for every member.
        requested_depth=next(iter(requested)) if len(requested) == 1 else None,
        # "explicit" only when *every* member's request was explicit: one
        # implicitly-normalized member makes the release-wide request
        # implicit too, so the roll-up can never present a normalization as
        # something a caller asked for.
        requested_depth_source=(
            "explicit"
            if present and all(b.requested_depth_source == "explicit" for b in present)
            else "implicit"
        ),
        effective_depth=_shallowest_depth(effective) if effective else None,
        depth_satisfied=all(satisfied) if satisfied else None,
        l0_context_status=_weakest(
            [b.l0_context_status for b in present], axis="l0_context_status"
        ),
        header_context_status=_weakest(
            [b.header_context_status for b in present], axis="header_context_status"
        ),
        dwarf_context_status=_weakest(
            [b.dwarf_context_status for b in present], axis="dwarf_context_status"
        ),
        l3_context_status=_weakest(
            [b.l3_context_status for b in present], axis="l3_context_status"
        ),
        fact_set_comparability=_weakest(
            [b.fact_set_comparability for b in present], axis="fact_set_comparability"
        ),
        graph_completeness=_weakest(
            [b.graph_completeness for b in present], axis="graph_completeness"
        ),
        schema_staleness_status=_weakest(
            [b.schema_staleness_status for b in present], axis="schema_staleness_status"
        ),
        layout_unverified_detectors=tuple(layout_unverified),
        notes=tuple(notes),
    )
