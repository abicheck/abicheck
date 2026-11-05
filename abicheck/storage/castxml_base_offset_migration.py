# Copyright 2026 abicheck contributors
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

"""Generation-38 migration of castxml base-class offsets to bits.

``RecordType.base_offsets`` holds each direct base's offset in bits, which is
what DWARF and the clang record-layout pass store. Before extractor
generation 38 the castxml backend stored castxml's ``<Base offset>`` attribute
verbatim, and castxml writes that offset in bytes, so a castxml snapshot said
``Base2: 16`` where the other producers said ``Base2: 128``. Without a
migration, every class with a base at a nonzero offset reports
``base_class_offset_changed`` between an older castxml baseline and a fresh
dump.

The rewrite is sound by provenance: in a snapshot whose ``ast_producer`` is
``castxml``, every ``base_offsets`` entry came from castxml's ``<Base>``
element -- the DWARF layout backfill leaves a record castxml already sized
untouched, and fills nothing on a castxml dump. A ``hybrid`` snapshot is not
rewritten: its castxml records held bytes, but a record the clang leg filled
held bits, and the stored document does not say which, so such a baseline is
re-dumped. Clang snapshots always stored bits.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CASTXML_BASE_OFFSET_BITS_GENERATION",
    "migrate_castxml_base_offsets",
]

#: The extractor generation that started storing castxml base offsets in bits.
CASTXML_BASE_OFFSET_BITS_GENERATION = 38


def _record_in_bits(entry: Any, hits: list[int]) -> Any:
    if not isinstance(entry, Mapping):
        return entry
    offsets = entry.get("base_offsets")
    if not isinstance(offsets, Mapping) or not offsets:
        return entry
    converted = {
        base: off * 8 if isinstance(off, int) and not isinstance(off, bool) else off
        for base, off in offsets.items()
    }
    changed = sum(1 for base in offsets if converted[base] != offsets[base])
    if not changed:
        return entry
    hits[0] += changed
    return {**entry, "base_offsets": converted}


def migrate_castxml_base_offsets(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with castxml base offsets read as bits, when it is a castxml
    header snapshot extracted before generation 38; never mutated in place."""
    if (
        extractor_generation >= CASTXML_BASE_OFFSET_BITS_GENERATION
        or document.get("ast_producer") != "castxml"
    ):
        return document, []
    types = document.get("types")
    if not isinstance(types, list):
        return document, []
    hits = [0]
    converted = [_record_in_bits(entry, hits) for entry in types]
    if not hits[0]:
        return document, []
    return {**document, "types": converted}, [
        f"migrated {hits[0]} stored castxml base-class offset(s) from an older "
        "extractor generation (bytes -> bits, e.g. 16 -> 128); re-dump the "
        "baseline to drop this migration"
    ]
