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

"""ADR-062 D9 / storage-format-v2 A1.6: pair two packages' variants.

The replacement for the deleted ``bundle_variants_config.pair_variants()``:
pairing is by ``variant_id`` alone -- an exact, stated identity, never a
fuzzy match on coordinates. Deliberately so: pairing by coordinates would
hide exactly the thing this report exists to show (a variant whose target
triple or compiler family changed is the *same* variant with a boundary
change, not a removal plus an addition), and two variants that happen to
share coordinates would become ambiguous.

Pure: operates on :class:`~abicheck.model.variant_pairing.VariantView`
values, however they were read. ``workflows.bundle_variants_capture.
package_variant_views`` reads them off a stored package.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model.variant_pairing import (
    PAIRED,
    UNMATCHED_NEW,
    UNMATCHED_OLD,
    VariantPair,
    VariantPairing,
    VariantView,
)

__all__ = ["pair_variant_views"]


def _index(views: Iterable[VariantView], side: str) -> dict[str, VariantView]:
    index: dict[str, VariantView] = {}
    for view in views:
        if view.variant_id in index:
            raise ValueError(
                f"{side} package lists variant_id {view.variant_id!r} twice"
            )
        index[view.variant_id] = view
    return index


def pair_variant_views(
    old: Iterable[VariantView],
    new: Iterable[VariantView],
    *,
    compared_old: str | None = None,
    compared_new: str | None = None,
) -> VariantPairing:
    """Pair *old* and *new* by ``variant_id``. Every id either side carries
    appears exactly once in the result; the result does not depend on the
    input order."""
    old_index, new_index = _index(old, "OLD"), _index(new, "NEW")
    pairs = []
    for variant_id in sorted(set(old_index) | set(new_index)):
        old_view, new_view = old_index.get(variant_id), new_index.get(variant_id)
        status = (
            PAIRED
            if old_view is not None and new_view is not None
            else UNMATCHED_OLD
            if old_view is not None
            else UNMATCHED_NEW
        )
        pairs.append(VariantPair(variant_id, status, old_view, new_view))
    return VariantPairing(
        pairs=tuple(pairs), compared_old=compared_old, compared_new=compared_new
    )
