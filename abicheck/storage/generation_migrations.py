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

"""Single-file snapshot generation stamp, drift notice and spelling migrations
(ADR-062 D2).

A single-file snapshot records the ``(extractor, resolver, source)``
generations of the build that *extracted its facts* under the top-level
``fact_generations`` key. An absent key reads as
:data:`~.versioning.UNSTATED_GENERATION` (a snapshot written before the stamp
existed), never as "current".

On load, the stated generations go through the same owner every package read
uses, :func:`~.versioning.reader_generation_compatibility`, and a drift (or an
unstated extractor generation) becomes a runtime ``load_notice`` that reaches
``DiffResult.coverage_warnings``.

An extractor-generation bump that changed how a type is *spelled* must also
say how to read the older spelling, or every baseline dumped before the bump
diffs a renamed spelling as a real change (field report: castxml's undeduced
``AutoType`` became ``auto`` at generation 6, and a stored ``AutoType&``
return compared against a fresh ``auto&`` read as ``func_return_changed``).
:data:`SPELLING_MIGRATIONS` holds one row per extractor generation: either the
spelling rewrites that generation introduced, or the reason it introduced
none. ``tests/test_snapshot_generation_migration.py`` fails when
``EXTRACTOR_GENERATION`` moves without a row. Each applied rewrite is
reported as a load notice (record before disposing, ADR-067), never applied
silently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .versioning import (
    GENERATION_DRIFT_NOTICE_MARKER,
    UNSTATED_GENERATION,
    FactGenerations,
    StorageVersions,
    reader_generation_compatibility,
)

__all__ = [
    "FACT_GENERATIONS_KEY",
    "SPELLING_MIGRATIONS",
    "GenerationSpellingChange",
    "decode_fact_generations",
    "encode_fact_generations",
    "migrate_and_describe",
    "without_snapshot_generation_notices",
]

#: Top-level snapshot document key holding the producing build's generations.
FACT_GENERATIONS_KEY = "fact_generations"

#: Prefix of the drift/unstated notice a single-file snapshot's own stamp
#: raises, so a package reader whose manifest owns the generation can drop it.
_SNAPSHOT_GENERATION_NOTICE_PREFIX = "stored snapshot"


def without_snapshot_generation_notices(notices: tuple[str, ...]) -> tuple[str, ...]:
    """*notices* minus the per-document generation notices: a package reader
    states generations once, from its manifest (one package states one), and
    keeps only the spelling-migration notices the document load raised."""
    return tuple(
        n for n in notices if not n.startswith(_SNAPSHOT_GENERATION_NOTICE_PREFIX)
    )


@dataclass(frozen=True)
class GenerationSpellingChange:
    """What one extractor generation changed about stored type spellings.

    *rewrites* maps an old whole-word spelling to the spelling this
    generation writes instead; empty means the generation changed no
    spelling, and *reason* then says why (it must be non-empty either way).
    """

    reason: str
    rewrites: tuple[tuple[str, str], ...] = ()


_NO_SPELLING = "no stored type spelling changed in this generation"

#: One row per extractor generation, keyed by the generation that *introduced*
#: the change. A snapshot stamped below a row's key (or unstated) gets that
#: row's rewrites on load.
SPELLING_MIGRATIONS: Mapping[int, GenerationSpellingChange] = {
    1: GenerationSpellingChange("first stamped generation"),
    2: GenerationSpellingChange(_NO_SPELLING),
    3: GenerationSpellingChange(_NO_SPELLING),
    4: GenerationSpellingChange(_NO_SPELLING),
    5: GenerationSpellingChange(_NO_SPELLING),
    6: GenerationSpellingChange(
        "castxml's undeduced AutoType placeholder is spelled 'auto' "
        "(extract/headers/castxml/type_resolution.py)",
        (("AutoType", "auto"),),
    ),
    7: GenerationSpellingChange(_NO_SPELLING),
    8: GenerationSpellingChange(_NO_SPELLING),
    9: GenerationSpellingChange(_NO_SPELLING),
    10: GenerationSpellingChange(_NO_SPELLING),
    11: GenerationSpellingChange(_NO_SPELLING),
    12: GenerationSpellingChange(_NO_SPELLING),
    13: GenerationSpellingChange(_NO_SPELLING),
    14: GenerationSpellingChange(
        "DWARF vector types spell as 'T __attribute__((vector_size(N)))' instead "
        "of 'T[]' (dwarf_utils.array_type_info); a stored 'T[]' cannot say whether "
        "it was an array or a vector, so no rewrite is sound and a baseline "
        "holding a vector type is re-dumped"
    ),
}


def encode_fact_generations(generations: FactGenerations) -> dict[str, int]:
    extractor, resolver, source = generations
    return {"extractor": extractor, "resolver": resolver, "source": source}


def decode_fact_generations(document: Mapping[str, Any]) -> FactGenerations:
    """The stated triple; any absent or malformed axis reads as unstated."""
    raw = document.get(FACT_GENERATIONS_KEY)
    if not isinstance(raw, Mapping):
        return (UNSTATED_GENERATION, UNSTATED_GENERATION, UNSTATED_GENERATION)
    versions = StorageVersions.from_dict(
        {
            "extractor_generation": raw.get("extractor"),
            "resolver_generation": raw.get("resolver"),
            "source_generation": raw.get("source"),
        }
    )
    return (
        versions.extractor_generation,
        versions.resolver_generation,
        versions.source_generation,
    )


def _pending_rewrites(extractor_generation: int) -> list[tuple[str, str]]:
    return [
        pair
        for generation in sorted(SPELLING_MIGRATIONS)
        if generation > extractor_generation
        for pair in SPELLING_MIGRATIONS[generation].rewrites
    ]


def _declared_type_names(document: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("types", "enums"):
        for entry in document.get(key) or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                names.add(entry["name"].rsplit("::", 1)[-1])
    typedefs = document.get("typedefs")
    if isinstance(typedefs, Mapping):
        names.update(str(k).rsplit("::", 1)[-1] for k in typedefs)
    return names


def _rewrite(
    value: Any, pattern: re.Pattern[str], table: dict[str, str], hits: list[int]
) -> Any:
    """*value* with every whole-word old spelling replaced; an unchanged
    subtree is returned as the same object, so a document with nothing to
    migrate is never copied."""
    if isinstance(value, str):
        if pattern.search(value) is None:
            return value
        hits[0] += 1
        return pattern.sub(lambda m: table[m.group(0)], value)
    if isinstance(value, dict):
        out: dict[Any, Any] | None = None
        for index, (key, item) in enumerate(value.items()):
            new_key = _rewrite(key, pattern, table, hits)
            new_item = _rewrite(item, pattern, table, hits)
            if out is None and (new_key is not key or new_item is not item):
                out = dict(list(value.items())[:index])
            if out is not None:
                out[new_key] = new_item
        return value if out is None else out
    if isinstance(value, list):
        items = [_rewrite(item, pattern, table, hits) for item in value]
        changed = any(a is not b for a, b in zip(items, value, strict=True))
        return items if changed else value
    return value


def _migrate_spellings(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    pending = _pending_rewrites(extractor_generation)
    if not pending:
        return document, []
    declared = _declared_type_names(document)
    notices: list[str] = []
    applicable: dict[str, str] = {}
    for old, new in pending:
        if old in declared:
            # A user type really named like the old placeholder: rewriting
            # would rename it, so keep it and say so.
            notices.append(
                f"spelling migration '{old}' -> '{new}' skipped: this snapshot "
                f"declares a type named '{old}'; re-dump the baseline"
            )
            continue
        applicable[old] = new
    if not applicable:
        return document, notices
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(old) for old in applicable) + r")\b"
    )
    hits = [0]
    migrated = _rewrite(document, pattern, applicable, hits)
    if hits[0]:
        rules = ", ".join(f"'{o}' -> '{n}'" for o, n in applicable.items())
        notices.append(
            f"migrated {hits[0]} stored spelling(s) from an older extractor "
            f"generation ({rules}); re-dump the baseline to drop this migration"
        )
    return migrated, notices


def migrate_and_describe(
    document: dict[str, Any],
) -> tuple[dict[str, Any], FactGenerations, tuple[str, ...]]:
    """Read *document*'s generation stamp, apply the spelling migrations its
    extractor generation predates, and describe both as load notices.

    Returns the (possibly rewritten, never mutated in place) document, the
    stated generations, and the notices. Drift is decided by
    :func:`~.versioning.reader_generation_compatibility` -- the one owner
    every package read uses -- not by a second comparison here.
    """
    generations = decode_fact_generations(document)
    extractor, resolver, source = generations
    notices: list[str] = []
    compatibility = reader_generation_compatibility(
        StorageVersions(
            extractor_generation=extractor,
            resolver_generation=resolver,
            source_generation=source,
        )
    )
    if compatibility.semantics_differ:
        notices.append(f"{_SNAPSHOT_GENERATION_NOTICE_PREFIX}: {compatibility.reason}")
    elif extractor == UNSTATED_GENERATION:
        notices.append(
            f"{_SNAPSHOT_GENERATION_NOTICE_PREFIX} does not state its extractor "
            "generation (written "
            "before generation stamping), so it may have been "
            f"{GENERATION_DRIFT_NOTICE_MARKER} extractor semantics; "
            "re-dump the baseline"
        )
    migrated, spelling_notices = _migrate_spellings(document, extractor)
    notices.extend(spelling_notices)
    return migrated, generations, tuple(notices)
