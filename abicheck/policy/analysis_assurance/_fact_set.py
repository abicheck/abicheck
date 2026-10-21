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
"""Cross-side L4 fact-set comparability axis."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...buildsource.fact_set import check_fact_compatibility

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _surface_fact_set(sa: Any) -> dict[str, Any]:
    """The rolled-up ``fact_set`` a ``SourceAbiSurface``'s ``coverage`` block
    carries, or ``{}``. Mirrors ``buildsource.source_diff._surface_fact_set``
    exactly (same dict-shaped ``coverage.fact_set`` field, same
    ``isinstance`` guard) rather than importing that module's private
    helper across a package boundary.
    """
    cov = sa.coverage if isinstance(sa.coverage, dict) else {}
    raw = cov.get("fact_set")
    return dict(raw) if isinstance(raw, dict) else {}


def _surface_fact_set_inconsistent(sa: Any) -> bool:
    """Whether this surface's own TUs disagreed on ``fact_set``. Mirrors
    ``buildsource.source_diff._surface_fact_set_inconsistent`` exactly,
    including its ``is True`` strictness (a hand-edited/forward-produced
    surface storing this flag as the JSON string ``"false"`` must not read
    as truthy).
    """
    cov = sa.coverage if isinstance(sa.coverage, dict) else {}
    return cov.get("fact_set_inconsistent") is True


def _fact_set_comparability(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Cross-side L4 fact-set comparability -- the ``analysis_assurance``
    analogue of ``buildsource.source_diff.diff_source_abi``'s own
    ``SOURCE_FACT_COVERAGE_INCOMPLETE`` gating.

    Finding (review, P1): the previous implementation only ever checked each
    SIDE's own ``fact_set_inconsistent`` flag independently -- it never
    compared the two sides' ``fact_set`` identities AGAINST EACH OTHER, so
    two internally-consistent but mutually-DIFFERENT fact-set identities
    (a producer/version mismatch between old and new) fell through to
    ``"comparable"`` even though ``diff_source_abi()``'s own
    ``check_fact_compatibility()`` -- the real, already-correct cross-side
    comparison this codebase already has -- identifies that exact same
    mismatch as ``fact_set_name_mismatch``/``fact_set_version_mismatch`` and
    suppresses source comparisons that cannot be trusted for it. Fixed by
    calling the identical ``buildsource.fact_set.check_fact_compatibility``
    both sides' rolled-up ``fact_set`` dicts, the same helper
    ``diff_source_abi()`` uses, instead of re-implementing a second,
    independent comparison here.

    A hard mismatch (``compat.structured_facts_comparable is False`` -- a
    ``fact_set`` name/version disagreement, or either side's own TUs
    disagreeing on ``fact_set``) reads ``"inconsistent"``, same bucket as
    the pre-existing per-side ``fact_set_inconsistent`` case. A softer
    mismatch (``compat.issues`` non-empty but ``structured_facts_comparable``
    still true -- a producer/producer_version/compiler_version/
    compiler_family difference, or an asymmetric one-sided ``fact_set``
    stamp) reads ``"unknown"``, since opaque-hash/content comparisons are
    not guaranteed trustworthy even though existence/removal detection
    still is. A symmetric, genuinely EMPTY ``fact_set`` on both sides (no
    signal at all -- a pre-C.8 producer, or two hand-edited packs) keeps
    reading ``"comparable"``, preserving the same forward-compat convention
    ``check_fact_compatibility`` itself documents for that shape.
    """
    notes: list[str] = []
    old_sa = getattr(old_pack, "source_abi", None)
    new_sa = getattr(new_pack, "source_abi", None)
    if old_sa is None and new_sa is None:
        return "not_applicable", notes
    if old_sa is None or new_sa is None:
        notes.append(
            "fact-set comparability unknown: only one side carries a linked "
            "L4 source-ABI surface"
        )
        return "unknown", notes

    old_inconsistent = _surface_fact_set_inconsistent(old_sa)
    new_inconsistent = _surface_fact_set_inconsistent(new_sa)
    old_fact_set = _surface_fact_set(old_sa)
    new_fact_set = _surface_fact_set(new_sa)
    has_signal = bool(
        old_fact_set or new_fact_set or old_inconsistent or new_inconsistent
    )
    if not has_signal:
        # Symmetric, genuinely empty on both sides -- nothing to compare,
        # same forward-compat treatment check_fact_compatibility() itself
        # gives this shape.
        return "comparable", notes

    compat = check_fact_compatibility(
        old_fact_set,
        new_fact_set,
        old_inconsistent=old_inconsistent,
        new_inconsistent=new_inconsistent,
    )
    if not compat.structured_facts_comparable:
        issue_notes = [f"{i.rule}: {i.message}" for i in compat.issues] or [
            "fact_set_inconsistent on at least one side's L4 surface -- the "
            "compile units backing it disagreed on their own fact-set identity"
        ]
        notes.extend(issue_notes)
        return "inconsistent", notes
    if compat.issues:
        notes.extend(f"{i.rule}: {i.message}" for i in compat.issues)
        return "unknown", notes
    return "comparable", notes
