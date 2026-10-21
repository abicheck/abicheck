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
"""Exported-symbol accounting axis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._result import ExportAccounting

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _export_accounting(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> ExportAccounting:
    """Roll up total/source-linked/internal/unaccounted export counts across
    BOTH sides -- the export analogue of :func:`_translation_units`'s
    additive TU rollup, not a pick-one-side snapshot.

    Finding (review, P1, round 7): the previous implementation picked ONE
    side's accounting -- new first, falling back to old only when new
    carried no L4 surface at all -- so a comparison where BOTH sides link an
    L4 surface, but only the OLD side has unmatched exports (e.g. a
    baseline-only symbol-mapping gap that was fixed forward on the new
    side), silently returned the new side's clean accounting and never
    examined the old side at all: ``export_accounting.unaccounted`` read 0,
    and with no other partial signal tripping, the overall ``status`` could
    read ``"complete"`` under ``--require-complete-analysis`` despite
    genuinely incomplete baseline linking. Fixed by summing both sides'
    counts additively, mirroring :func:`_translation_units`'s own
    both-sides summation for TU accounting -- a nonzero ``unaccounted`` on
    EITHER side now always contributes to the combined ``export_accounting``
    rather than being silently shadowed by whichever side the old
    "new-first" selection happened to prefer.

    Finding (Codex review, PR #788): once ``source_link.link_source_abi``
    stopped leaving a classified ``non_public`` symbol in
    ``symbols_without_decl`` too (the double-count fix this rollup exists
    alongside), ``source_linked = total - unaccounted`` started silently
    counting every ``internal``-classified export as ALSO ``source_linked``
    -- correct arithmetically (the two no longer sum past ``total``), but
    wrong in what ``source_linked`` claims: a ``dependency:stdlib`` export
    is definitionally not linked to *this* library's own source. Fixed by
    also subtracting ``internal``, so the three categories are now a true
    partition of ``total`` (``source_linked + internal + unaccounted ==
    total``), not just two of the three kept mutually exclusive.

    Finding (Codex review, PR #788): the fix above assumed every
    ``SourceAbiSurface`` reaching this rollup was already produced by the
    fixed ``link_source_abi``/``relink_surface_exports`` (which now folds a
    classified symbol into ``all_matched``, so it can no longer appear in
    ``symbols_without_decl`` too). A *persisted* surface produced by a
    pre-fix build -- the ordinary "compare an existing baseline JSON against
    a fresh candidate" workflow -- round-trips through
    ``SourceAbiSurface.from_dict()`` with that stale overlap still intact:
    the same symbol sits in both ``symbols_without_decl`` and
    ``non_public_symbol_to_reason``. Counting ``len(...)`` of each
    independently double-subtracts that symbol (once as ``unaccounted``,
    once as ``internal``), so ``source_linked`` under-reports (or clamps to
    0) and ``unaccounted`` stays nonzero even though the symbol IS
    genuinely classified -- ``--require-complete-analysis`` still fails on
    this upgrade path. Fixed by deduplicating per side: a symbol already
    classified into ``non_public_symbol_to_reason`` no longer counts toward
    ``unaccounted`` here regardless of whether the surface that produced it
    already excluded it from ``symbols_without_decl`` -- this rollup is now
    correct for both a freshly-linked surface and a legacy persisted one.
    """
    total: int | None = None
    unaccounted: int | None = None
    internal: int | None = None
    for pack in (old_pack, new_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        side_total = len(sa.roots.get("exported_symbols", []))
        non_public_keys = set(sa.mappings.get("non_public_symbol_to_reason", {}))
        # Subtract any overlap rather than trusting symbols_without_decl to
        # already be disjoint from non_public_symbol_to_reason -- true for a
        # freshly-linked surface, not guaranteed for one persisted before
        # this fix (see the Codex-review docstring paragraph above).
        side_unaccounted = len(
            set(sa.unmatched.get("symbols_without_decl", [])) - non_public_keys
        )
        side_internal = len(non_public_keys)
        total = (total or 0) + side_total
        unaccounted = (unaccounted or 0) + side_unaccounted
        internal = (internal or 0) + side_internal
    if total is None:
        return ExportAccounting()
    source_linked = max(0, total - (unaccounted or 0) - (internal or 0))
    return ExportAccounting(
        total=total,
        source_linked=source_linked,
        internal=internal,
        unaccounted=unaccounted,
    )
