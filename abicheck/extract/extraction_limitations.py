# SPDX-License-Identifier: Apache-2.0
"""Header-extraction limitations recorded in a snapshot's dump provenance.

A header backend that could not read part of what it is meant to record --
an optional clang pass that failed, a type the frontend could not spell --
still returns the facts it did read. What it could not read must not then
compare as "nothing changed" with a ``complete`` assurance claim (ADR-063/064:
missing evidence lowers assurance, never upgrades to clean). Each such gap is
a plain-text reason under ``dump_provenance["extraction_limitations"]``, the
list ``policy/analysis_assurance/_extraction_limitations.py`` already rolls
up as an unobserved dimension, live and stored alike.

Three carriers feed that one list:

* a header-AST parser carries the reasons of its own optional passes as the
  :data:`PARSER_LIMITATIONS_ATTR` attribute (:func:`note_parser_limitation`),
  read when its fields are assembled (``extract/header_ast_fields.py``);
* a castxml XML root carries the failed clang joins as the
  :data:`JOIN_GAPS_ATTR` attribute (:func:`with_join_gap`), copied onto the
  parser by the castxml backend;
* a finished snapshot gains a reason directly (:func:`add_snapshot_limitation`),
  for an enrichment pass that runs after the parse (clang record layout).

Reasons are fixed sentences without paths or exception text, so the same
failure stores the same snapshot bytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from xml.etree.ElementTree import Element

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, RecordType, Variable

__all__ = [
    "EXTRACTION_LIMITATIONS_KEY",
    "JOIN_GAPS_ATTR",
    "PARSER_LIMITATIONS_ATTR",
    "UNRESOLVED_SPELLING_LIMITATION",
    "add_snapshot_limitation",
    "join_gaps",
    "merged_extraction_provenance",
    "note_parser_limitation",
    "parser_limitations",
    "provenance_with_limitations",
    "unresolved_spelling_limitation",
    "with_join_gap",
]

#: The ``dump_provenance`` key the assurance rollup reads.
EXTRACTION_LIMITATIONS_KEY = "extraction_limitations"

#: The attribute a header-AST parser carries its limitation reasons under.
PARSER_LIMITATIONS_ATTR = "_abicheck_extraction_limitations"

#: The castxml-root attribute a failed clang join is recorded under (a JSON
#: list of reasons), on a copy of the root -- the parsed tree is never written.
JOIN_GAPS_ATTR = "abicheck-clang-join-gaps"

#: castxml's spelling of a type it cannot express (a vector, complex or
#: bit-precise integer) when the clang join did not supply one.
_UNRESOLVED_TOKEN = re.compile(r"\bUnimplemented\b")

UNRESOLVED_SPELLING_LIMITATION = (
    "unresolved type spellings: {count} recorded declaration type(s) are "
    "spelled 'Unimplemented' (a vector, complex or _BitInt type castxml cannot "
    "express and its clang join did not resolve), so a change to that type is "
    "not assessed"
)


def note_parser_limitation(parser: object, reason: str) -> None:
    """Record *reason* on a header-AST *parser* (kept once, in order)."""
    current = tuple(getattr(parser, PARSER_LIMITATIONS_ATTR, ()))
    if reason not in current:
        setattr(parser, PARSER_LIMITATIONS_ATTR, (*current, reason))


def parser_limitations(parser: object) -> tuple[str, ...]:
    """The reasons :func:`note_parser_limitation` recorded on *parser*."""
    raw = getattr(parser, PARSER_LIMITATIONS_ATTR, ())
    return tuple(r for r in raw if isinstance(r, str) and r)


def with_join_gap(root: Element, reason: str) -> Element:
    """A copy of castxml *root* that also records the failed join *reason*."""
    reasons = [*join_gaps(root)]
    if reason not in reasons:
        reasons.append(reason)
    carried = Element(root.tag, dict(root.attrib))
    carried.set(JOIN_GAPS_ATTR, json.dumps(reasons))
    carried.extend(list(root))
    carried.text = root.text
    carried.tail = root.tail
    return carried


def join_gaps(root: object) -> tuple[str, ...]:
    """The failed-join reasons :func:`with_join_gap` recorded on *root*.

    A malformed attribute is itself a gap: the joins it would have named are
    unknown, so it reads as one reason saying so rather than as none.
    """
    if not isinstance(root, Element):
        return ()
    raw = root.get(JOIN_GAPS_ATTR)
    if not raw:
        return ()
    try:
        loaded = json.loads(raw)
    except ValueError:
        loaded = None
    if not isinstance(loaded, list) or not all(isinstance(r, str) for r in loaded):
        return (
            "clang join record unreadable: which castxml facts were joined is unknown",
        )
    return tuple(loaded)


def unresolved_spelling_limitation(
    functions: Iterable[Function],
    variables: Iterable[Variable],
    types: Iterable[RecordType],
    typedefs: dict[str, str],
) -> str | None:
    """The limitation for recorded type spellings that stayed unresolved.

    Reads what the snapshot will compare -- return, parameter, variable,
    field and typedef spellings -- so a placeholder in a declaration the
    parse never recorded does not count.
    """
    spellings: list[str] = []
    for fn in functions:
        spellings.append(fn.return_type)
        spellings.extend(p.type for p in fn.params)
    spellings.extend(v.type for v in variables)
    for rec in types:
        spellings.extend(f.type for f in rec.fields)
    spellings.extend(typedefs.values())
    count = sum(1 for s in spellings if s and _UNRESOLVED_TOKEN.search(s))
    return UNRESOLVED_SPELLING_LIMITATION.format(count=count) if count else None


def provenance_with_limitations(
    provenance: dict[str, Any] | None, reasons: Iterable[str]
) -> dict[str, Any] | None:
    """*provenance* with *reasons* appended to its limitation list, once each.

    ``None`` stays ``None`` when there is nothing to add.
    """
    added = [r for r in reasons if r]
    if not added:
        return provenance
    merged = dict(provenance or {})
    existing = merged.get(EXTRACTION_LIMITATIONS_KEY)
    current = list(existing) if isinstance(existing, list) else []
    merged[EXTRACTION_LIMITATIONS_KEY] = list(dict.fromkeys([*current, *added]))
    return merged


def merged_extraction_provenance(
    provenances: Iterable[dict[str, Any] | None],
) -> dict[str, Any] | None:
    """The limitation lists of several translation units' provenance, unioned.

    A multi-TU merge keeps one snapshot; any unit's gap is that snapshot's gap.
    """
    reasons: list[str] = []
    for provenance in provenances:
        raw = (provenance or {}).get(EXTRACTION_LIMITATIONS_KEY)
        if isinstance(raw, list):
            reasons.extend(r for r in raw if isinstance(r, str))
    return provenance_with_limitations(None, reasons)


def add_snapshot_limitation(snap: AbiSnapshot, reason: str) -> None:
    """Record *reason* in *snap*'s dump provenance."""
    snap.dump_provenance = provenance_with_limitations(snap.dump_provenance, [reason])
