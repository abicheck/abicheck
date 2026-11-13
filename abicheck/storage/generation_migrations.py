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
from typing import Any

from .castxml_array_migration import migrate_castxml_array_counts
from .castxml_base_offset_migration import migrate_castxml_base_offsets
from .castxml_noexcept_migration import migrate_castxml_noexcept
from .clang_vtable_base_migration import migrate_clang_vtable_bases
from .dwarf_field_size_migration import migrate_dwarf_field_size_unknown
from .generation_spelling_table import SPELLING_MIGRATIONS
from .versioning import (
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
    UNSTATED_GENERATION,
    FactGenerations,
    StorageVersions,
    reader_generation_compatibility,
)

__all__ = [
    "FACT_GENERATIONS_KEY",
    "decode_fact_generations",
    "decode_spellings_generation",
    "encode_fact_generations",
    "erased_extents_restored_between",
    "extractor_generation_of",
    "pending_extent_shift",
    "migrate_and_describe",
    "retired_function_prefixes_between",
    "without_snapshot_generation_notices",
]

#: Top-level snapshot document key holding the producing build's generations.
FACT_GENERATIONS_KEY = "fact_generations"

#: Key inside :data:`FACT_GENERATIONS_KEY` naming the extractor generation
#: whose spellings the document already carries, written when a load migrated
#: an older document and it is saved again. The stamp itself is never
#: re-dated, so without this a re-saved baseline would be migrated twice
#: (harmless for a rename, wrong for a count shift).
SPELLINGS_KEY = "spellings"

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


def extractor_generation_of(fact_generations: FactGenerations | None) -> int:
    """The extractor generation a snapshot's facts were extracted under:
    ``None`` (extracted by this process) reads as this build's
    ``EXTRACTOR_GENERATION``; an unstated stamp stays
    :data:`~.versioning.UNSTATED_GENERATION`."""
    if fact_generations is None:
        return EXTRACTOR_GENERATION
    return fact_generations[0]


def pending_extent_shift(old_generation: int, new_generation: int) -> int:
    """The array-extent shift the generations in (*old_generation*,
    *new_generation*] declared; 0 when none did or the order is reversed."""
    return sum(
        change.extent_shift
        for introduced, change in SPELLING_MIGRATIONS.items()
        if old_generation < introduced <= new_generation
    )


def erased_extents_restored_between(generation_a: int, generation_b: int) -> bool:
    """Whether a generation in between the two (either order) started
    spelling the array bounds an older DWARF extractor erased to ``[]``."""
    low, high = sorted((generation_a, generation_b))
    return any(
        change.restores_erased_extents
        for introduced, change in SPELLING_MIGRATIONS.items()
        if low < introduced <= high
    )


def retired_function_prefixes_between(
    generation_a: int, generation_b: int
) -> tuple[str, ...]:
    """Function-name prefixes a generation in between the two (either order)
    stopped recording: an entity the older operand holds only as an extractor
    artifact."""
    low, high = sorted((generation_a, generation_b))
    return tuple(
        prefix
        for introduced in sorted(SPELLING_MIGRATIONS)
        if low < introduced <= high
        for prefix in SPELLING_MIGRATIONS[introduced].retires_function_prefixes
    )


def encode_fact_generations(
    generations: FactGenerations, spellings_generation: int | None = None
) -> dict[str, int]:
    extractor, resolver, source = generations
    encoded = {"extractor": extractor, "resolver": resolver, "source": source}
    if spellings_generation is not None and spellings_generation > extractor:
        encoded[SPELLINGS_KEY] = spellings_generation
    return encoded


def decode_spellings_generation(document: Mapping[str, Any]) -> int | None:
    """The generation whose spellings *document* already carries, when a
    previous load migrated it; ``None`` when it was never migrated."""
    raw = document.get(FACT_GENERATIONS_KEY)
    value = raw.get(SPELLINGS_KEY) if isinstance(raw, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
) -> tuple[dict[str, Any], FactGenerations, int | None, tuple[str, ...]]:
    """Read *document*'s generation stamp, apply the spelling migrations its
    extractor generation predates, and describe both as load notices.

    Returns the (possibly rewritten, never mutated in place) document, the
    stated generations, the generation whose spellings the returned document
    carries (``None`` when no migration was due), and the notices. Drift is decided by
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
    previous = decode_spellings_generation(document)
    spellings = max(extractor, previous or extractor)
    if previous is not None and spellings > extractor:
        # Migrated by an earlier load and saved again: the spellings are
        # already rewritten, but the document still holds them, so say so
        # exactly as the first load did (a dry run that keeps only notices).
        notices.extend(migrate_castxml_array_counts(document, extractor)[1])
        notices.extend(migrate_castxml_base_offsets(document, extractor)[1])
        notices.extend(migrate_clang_vtable_bases(document, extractor)[1])
        notices.extend(migrate_castxml_noexcept(document, extractor)[1])
        notices.extend(migrate_dwarf_field_size_unknown(document, extractor)[1])
    if spellings >= EXTRACTOR_GENERATION:
        return document, generations, previous, tuple(notices)
    migrated, spelling_notices = _migrate_spellings(document, spellings)
    notices.extend(spelling_notices)
    migrated, array_notices = migrate_castxml_array_counts(migrated, spellings)
    notices.extend(array_notices)
    migrated, offset_notices = migrate_castxml_base_offsets(migrated, spellings)
    notices.extend(offset_notices)
    migrated, vtable_notices = migrate_clang_vtable_bases(migrated, spellings)
    notices.extend(vtable_notices)
    migrated, noexcept_notices = migrate_castxml_noexcept(migrated, spellings)
    notices.extend(noexcept_notices)
    migrated, field_size_notices = migrate_dwarf_field_size_unknown(migrated, spellings)
    notices.extend(field_size_notices)
    return migrated, generations, EXTRACTOR_GENERATION, tuple(notices)
