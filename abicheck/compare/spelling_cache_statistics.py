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

"""Every counter the spelling-match caches keep, grouped by owner.

Split out of :mod:`abicheck.compare.spelling_match_cache` (which holds the
caches themselves) to keep that module under the architecture line ceiling;
read by ``workflows.cache_counters`` for the memory trace.
"""

from __future__ import annotations

from .spelling_match_cache import MATCH_CACHE, PATTERN_REGISTRY, VOCABULARY_CACHE

__all__ = ["cache_statistics"]


def cache_statistics() -> dict[str, object]:
    """Every counter these caches keep, **grouped by owner**.

    Deliberately reports ``retained_bytes`` per owner and a total, rather
    than one merged number: a pattern's cost belongs to the registry, a
    result's to the match cache, and the working set the matcher's callers
    hold in their own attributes belongs to neither. Summing them as if they
    were independent is how the double-charge this module was redesigned to
    remove got introduced in the first place.

    Each owner is read under its own lock, so every group is internally
    consistent. The groups are not a single instant of the whole module --
    that would need all three locks at once, which this module never does.
    """
    match_total = MATCH_CACHE.hits + MATCH_CACHE.misses
    return {
        "match": {
            "entries": len(MATCH_CACHE),
            "hits": MATCH_CACHE.hits,
            "misses": MATCH_CACHE.misses,
            "hit_rate": (MATCH_CACHE.hits / match_total if match_total else None),
            "bypasses": MATCH_CACHE.bypasses,
            "bypass_text_too_long": MATCH_CACHE.bypass_text_too_long,
            "bypass_too_many_matches": MATCH_CACHE.bypass_too_many_matches,
            "evictions": MATCH_CACHE.evictions,
            "retained_bytes": MATCH_CACHE.retained_bytes,
        },
        "vocabulary": {
            "entries": len(VOCABULARY_CACHE),
            "hits": VOCABULARY_CACHE.hits,
            "misses": VOCABULARY_CACHE.misses,
            "compilations": VOCABULARY_CACHE.compilations,
            "evictions": VOCABULARY_CACHE.evictions,
        },
        "patterns": {
            "held": len(PATTERN_REGISTRY),
            "registered": PATTERN_REGISTRY.registrations,
            "released": PATTERN_REGISTRY.releases,
            "retained_bytes": PATTERN_REGISTRY.retained_bytes,
        },
        "retained_bytes_total": (
            MATCH_CACHE.retained_bytes + PATTERN_REGISTRY.retained_bytes
        ),
    }
