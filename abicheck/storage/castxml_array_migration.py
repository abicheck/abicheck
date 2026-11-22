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

"""Generation-15 and generation-91 migrations of castxml array spellings.

Generation 91 is the bound order of a multi-dimensional array; see
:func:`migrate_castxml_array_order`. The rest of this docstring is generation 15.

Before extractor generation 15, the castxml backend spelled an array from
castxml's ``ArrayType max`` attribute, which is the last valid index, so
``double items[2]`` was stored as ``double[1]``. Generation 15 spells the
element count (``max + 1``). Without a migration, every header baseline
dumped earlier diffs each array field, parameter and variable as a type
change against a fresh dump.

The rewrite is sound by provenance: in a snapshot whose ``ast_producer`` is
``castxml``, the declaration spellings below are built by castxml's type
resolver, and every ``[N]`` outside a template argument list came from an
``ArrayType`` node. A ``[N]`` inside ``<...>`` is part of a record name castxml
printed itself (correctly), so it is left alone. Hybrid and clang snapshots
are not touched: clang always spelled the element count.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "CASTXML_ARRAY_COUNT_GENERATION",
    "CASTXML_ARRAY_ORDER_GENERATION",
    "migrate_castxml_array_counts",
    "migrate_castxml_array_order",
    "reverse_array_bounds",
    "shift_array_counts",
]

#: The extractor generation that started spelling castxml array element counts.
CASTXML_ARRAY_COUNT_GENERATION = 15

#: The extractor generation that started spelling a castxml multi-dimensional
#: array outermost bound first (``int[2][3]``, not ``int[3][2]``).
CASTXML_ARRAY_ORDER_GENERATION = 91

_COUNT = re.compile(r"\[(\d+)\]")
_BOUND = re.compile(r"\[[^\[\]]*\]")


def shift_array_counts(spelling: str) -> str:
    """*spelling* with every ``[N]`` outside a template argument list read
    as ``[N + 1]``."""
    if "[" not in spelling:
        return spelling
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(spelling):
        char = spelling[index]
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif char == "[" and depth == 0:
            match = _COUNT.match(spelling, index)
            if match is not None:
                out.append(f"[{int(match.group(1)) + 1}]")
                index = match.end()
                continue
        out.append(char)
        index += 1
    return "".join(out)


def reverse_array_bounds(spelling: str) -> str:
    """*spelling* with every run of adjacent ``[..]`` bounds outside a
    template argument list reversed: ``int[4][3][2]`` -> ``int[2][3][4]``.

    Before generation 91 castxml spelled each outer bound after the inner
    ones, so a run of adjacent bounds is exactly one multi-dimensional array
    written innermost first. A bound separated by anything else (``*``, a
    qualifier) belongs to a different array declarator and is its own run.
    """
    if "][" not in spelling:
        return spelling
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(spelling):
        char = spelling[index]
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif char == "[" and depth == 0:
            run: list[str] = []
            while (match := _BOUND.match(spelling, index)) is not None:
                run.append(match.group(0))
                index = match.end()
            if run:
                out.extend(reversed(run))
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _shift_key(
    entry: Any, key: str, hits: list[int], respell: Any = shift_array_counts
) -> Any:
    if not isinstance(entry, Mapping) or not isinstance(entry.get(key), str):
        return entry
    new = respell(entry[key])
    if new == entry[key]:
        return entry
    hits[0] += 1
    return {**entry, key: new}


def _shift_list(entries: Any, apply: Any) -> Any:
    if not isinstance(entries, list):
        return entries
    shifted = [apply(entry) for entry in entries]
    return (
        shifted
        if any(a is not b for a, b in zip(shifted, entries, strict=True))
        else entries
    )


def migrate_castxml_array_counts(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with castxml array counts respelled, when it is a castxml
    header snapshot extracted before generation 15; never mutated in place."""
    return _migrate(
        document,
        extractor_generation,
        CASTXML_ARRAY_COUNT_GENERATION,
        shift_array_counts,
        "migrated stored castxml array spelling(s) from an older "
        "extractor generation (last index -> element count, e.g. 'T[N-1]' -> "
        "'T[N]'); re-dump the baseline to drop this migration",
    )


def migrate_castxml_array_order(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with castxml multi-dimensional array bounds put back in C
    order, when it is a castxml header snapshot extracted before generation
    91; never mutated in place.

    Sound by the same provenance as the count migration: every adjacent run
    of bounds outside ``<...>`` in a castxml spelling came from one nested
    ``ArrayType`` chain, which the old resolver spelled innermost first.
    """
    return _migrate(
        document,
        extractor_generation,
        CASTXML_ARRAY_ORDER_GENERATION,
        reverse_array_bounds,
        "migrated stored castxml multi-dimensional array spelling(s) from an "
        "older extractor generation (bounds were innermost first, e.g. "
        "'int[3][2]' -> 'int[2][3]'); re-dump the baseline to drop this migration",
    )


def _migrate(
    document: dict[str, Any],
    extractor_generation: int,
    fixed_at: int,
    respell: Any,
    notice: str,
) -> tuple[dict[str, Any], list[str]]:
    if extractor_generation >= fixed_at or document.get("ast_producer") != "castxml":
        return document, []
    hits = [0]

    def function(entry: Any) -> Any:
        entry = _shift_key(entry, "return_type", hits, respell)
        if isinstance(entry, Mapping):
            params = _shift_list(
                entry.get("params"), lambda p: _shift_key(p, "type", hits, respell)
            )
            if params is not entry.get("params"):
                entry = {**entry, "params": params}
        return entry

    def record(entry: Any) -> Any:
        if not isinstance(entry, Mapping):
            return entry
        fields = _shift_list(
            entry.get("fields"), lambda f: _shift_key(f, "type", hits, respell)
        )
        return entry if fields is entry.get("fields") else {**entry, "fields": fields}

    migrated = dict(document)
    migrated["functions"] = _shift_list(document.get("functions"), function)
    migrated["variables"] = _shift_list(
        document.get("variables"), lambda v: _shift_key(v, "type", hits, respell)
    )
    migrated["types"] = _shift_list(document.get("types"), record)
    for key in ("typedefs", "typedefs_qualified"):
        table = document.get(key)
        if isinstance(table, Mapping):
            shifted = {
                alias: respell(target) if isinstance(target, str) else target
                for alias, target in table.items()
            }
            changed = sum(1 for alias in table if shifted[alias] != table[alias])
            if changed:
                hits[0] += changed
                migrated[key] = shifted
    if not hits[0]:
        return document, []
    return migrated, [notice]
