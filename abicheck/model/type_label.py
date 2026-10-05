# SPDX-License-Identifier: Apache-2.0
"""The qualified label of a type finding, from its own ``entity_id``.

A leaf of :mod:`abicheck.compare.type_symbol_disambiguation`'s rule, kept in
``model`` because two layers need it: ``compare`` relabels findings whose
bare name is ambiguous, and ``policy.selectors`` matches a suppression rule
written against that qualified label before the relabel has run. ``policy``
reaching the ``compare`` module would pull in the reconciliation stack and
close an import cycle, so the rule lives inward of both.
"""

from __future__ import annotations

from .identity import EntityId, EntityKind, InlineNamespace, Namespace, Record
from .qualified_name_split import split_top_level_scopes

__all__ = [
    "TYPE_ENTITY_KINDS",
    "qualified_spelling",
    "qualified_type_label",
    "relabelled",
]

TYPE_ENTITY_KINDS = frozenset({EntityKind.TYPE, EntityKind.ENUM, EntityKind.TYPEDEF})


def qualified_spelling(entity_id: EntityId | None) -> str | None:
    """``ns::Outer::Leaf`` for *entity_id*, or ``None`` when a scope segment
    has no spelling a reader could use (anonymous, function-local)."""
    if entity_id is None:
        return None
    parts: list[str] = []
    for seg in entity_id.scope:
        if isinstance(seg, (Namespace, Record, InlineNamespace)) and seg.name:
            parts.append(seg.name)
        else:
            return None
    parts.append(entity_id.leaf_name)
    return "::".join(parts)


def relabelled(symbol: str, bare: str, qualified: str) -> str | None:
    """*symbol* with its leading *bare* segment replaced by *qualified*, or
    ``None`` when *symbol* is not ``bare`` / ``bare::member``."""
    parts = split_top_level_scopes(symbol)
    if not parts or parts[0] != bare:
        return None
    return "::".join([qualified, *parts[1:]])


def qualified_type_label(change: object) -> str | None:
    """The label :func:`abicheck.compare.type_symbol_disambiguation.disambiguate_type_symbols` would give *change* if its
    bare name were ambiguous: its ``entity_id``'s qualified spelling in place
    of the bare leaf. ``None`` for a non-type finding, one without a
    spellable identity, or one whose label already differs from the leaf."""
    eid = getattr(change, "entity_id", None)
    if eid is None or eid.kind not in TYPE_ENTITY_KINDS:
        return None
    qualified = qualified_spelling(eid)
    if qualified is None:
        return None
    label = relabelled(getattr(change, "symbol", "") or "", eid.leaf_name, qualified)
    return None if label == getattr(change, "symbol", None) else label
