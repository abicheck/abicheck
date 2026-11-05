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

"""Generation-40 migration: an older clang vtable over bases is not confirmed.

Before extractor generation 40 the direct-clang header backend resolved a
base specifier only by its spelling as a record key. A base it could not
resolve -- every base written relative to its scope under clang 22+ (which
dropped the qualified ``desugaredQualType``), a dependent base of a class
template, an implicit instantiation the index lacked -- contributed nothing,
and the record's ``vtable_fact`` was still stored ``present``: an empty or
short list read as a confirmed answer. Comparing such a baseline with a
generation-40 dump that resolves the base reports ``VPTR_INTRODUCED`` or
``TYPE_VTABLE_CHANGED`` for a class that did not change.

The stored document cannot say which bases resolved, so the migration is
conservative by provenance: in a ``clang`` or ``hybrid`` snapshot written
before generation 40, every record with at least one base keeps its vtable
list but has a ``present`` ``vtable_fact`` lowered to ``partial``, which the
vtable detectors already decline on (``compare.vtable_evidence``,
``diff_layout._check_vptr_introduced``). A record without bases never
depended on base resolution and is left as stored; a castxml snapshot is
untouched. Re-dumping the baseline restores a confirmed vtable wherever the
bases resolve.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CLANG_VTABLE_BASE_RESOLUTION_GENERATION",
    "migrate_clang_vtable_bases",
]

CLANG_VTABLE_BASE_RESOLUTION_GENERATION = 40

_DIAGNOSTIC = (
    "stored by an extractor generation before 40, whose clang backend could "
    "leave a base unresolved and still store the vtable as confirmed; re-dump "
    "the baseline"
)
_PRODUCERS = frozenset({"clang", "hybrid"})


def _has_bases(entry: Mapping[str, Any]) -> bool:
    return any(
        isinstance(entry.get(key), list) and entry.get(key)
        for key in ("bases", "virtual_bases")
    )


def _lowered(entry: Any, hits: list[int]) -> Any:
    if not isinstance(entry, Mapping) or not _has_bases(entry):
        return entry
    fact = entry.get("vtable_fact")
    if not isinstance(fact, Mapping) or fact.get("status") != "present":
        return entry
    diagnostics = list(fact.get("diagnostics") or [])
    hits[0] += 1
    return {
        **entry,
        "vtable_fact": {
            **fact,
            "status": "partial",
            "diagnostics": [*diagnostics, _DIAGNOSTIC],
        },
    }


def migrate_clang_vtable_bases(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with every pre-40 clang vtable over bases read as partial;
    never mutated in place."""
    if (
        extractor_generation >= CLANG_VTABLE_BASE_RESOLUTION_GENERATION
        or document.get("ast_producer") not in _PRODUCERS
    ):
        return document, []
    types = document.get("types")
    if not isinstance(types, list):
        return document, []
    hits = [0]
    lowered = [_lowered(entry, hits) for entry in types]
    if not hits[0]:
        return document, []
    return {**document, "types": lowered}, [
        f"lowered {hits[0]} stored clang vtable fact(s) over base classes from "
        "an older extractor generation to partial (a base may have been "
        "unresolved); re-dump the baseline to drop this migration"
    ]
