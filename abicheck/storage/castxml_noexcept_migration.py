# SPDX-License-Identifier: Apache-2.0
"""Generation-45 migration: an older castxml ``is_noexcept=False`` is unknown.

castxml writes no ``noexcept`` exception specification, so before extractor
generation 45 the castxml header backend stored ``is_noexcept=False`` for
every function, ``noexcept`` or not. Generation 45 reads the specification
from the clang AST of the same header (``extract/headers/castxml/
clang_semantics_join.py``); comparing an older baseline with a fresh dump
would otherwise report ``func_noexcept_added`` for every function that was
always ``noexcept``.

The stored ``False`` cannot be told apart from a real "no ``noexcept``", so in
a ``castxml`` snapshot written before generation 45 every C++ function's
``False`` loads as ``None`` (not observed): the noexcept detector declines it
and the assurance rollup names the gap. A plain C function (a name that is
not an Itanium or MSVC mangling) has no exception specification and keeps its
value; a ``True`` was never stored by castxml and is left alone. A hybrid
snapshot is untouched: its merge did not read the castxml value as a fact.
Re-dumping the baseline drops the migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .versioning import GENERATION_DRIFT_NOTICE_MARKER

__all__ = ["CASTXML_NOEXCEPT_GENERATION", "migrate_castxml_noexcept"]

CASTXML_NOEXCEPT_GENERATION = 45


def _cxx_symbol(entry: Mapping[str, Any]) -> bool:
    mangled = entry.get("mangled")
    return isinstance(mangled, str) and (
        mangled.startswith(("_Z", "?"))
        or mangled.startswith("__abicheck_")
        or "~" in mangled
    )


def _lowered(entry: Any, hits: list[int]) -> Any:
    if (
        not isinstance(entry, Mapping)
        or entry.get("is_noexcept", False) is not False
        or not _cxx_symbol(entry)
    ):
        return entry
    hits[0] += 1
    return {**entry, "is_noexcept": None}


def migrate_castxml_noexcept(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with every pre-45 castxml C++ ``is_noexcept=False`` read as
    unknown; never mutated in place."""
    if (
        extractor_generation >= CASTXML_NOEXCEPT_GENERATION
        or document.get("ast_producer") != "castxml"
    ):
        return document, []
    functions = document.get("functions")
    if not isinstance(functions, list):
        return document, []
    hits = [0]
    lowered = [_lowered(entry, hits) for entry in functions]
    if not hits[0]:
        return document, []
    return {**document, "functions": lowered}, [
        f"read {hits[0]} stored castxml noexcept value(s) as not observed: the "
        f"snapshot was {GENERATION_DRIFT_NOTICE_MARKER} extractor semantics "
        "(castxml recorded no exception specification before generation 45); "
        "re-dump the baseline to drop this migration"
    ]
