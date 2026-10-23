# SPDX-License-Identifier: Apache-2.0
"""ADR-063 amendment "L4 clang payload comparability": the revision-mismatch rule.

A stored L4 surface and a freshly produced one can carry the same ``fact_set``
name/version but different clang ``producer_version`` stamps. Their payloads
then differ in ways that are not real source changes (identity-bearing fields
are filled or spelled differently). This module names that condition
(A1) and holds the pinned field set (A3/A5) that the comparison intersects on.

Leaf module: no imports from the rest of ``buildsource``, so ``source_diff``
and tests can both depend on it without a cycle.
"""

from __future__ import annotations

from typing import Any

#: The clang producer stamp written by ``source_extractors/clang.py``.
CLANG_PRODUCER = "abicheck-cc-clang-extractor"

#: ``(kind, field)`` pairs whose clang projection is equal to the stored
#: payload on every catalog TU (ADR-063 amendment test 1, measured by
#: ``tests/test_l4_clang_projection_golden.py``). Only these may take part in a
#: comparison across a payload-revision mismatch.
#:
#: ``typedef.visibility`` and ``typedef.api_relevant`` are deliberately not
#: pinned: they are equal only because ``base.entity_from_typedef`` hard-codes
#: them, so equality says nothing about the projection. ``typedef.qualified_name``
#: and ``typedef.value`` are measured equal on 22 typedefs with real evidence.
REVISION_COMPARABLE_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("typedef", "qualified_name"),
        ("typedef", "value"),
    }
)


def is_payload_revision_mismatch(
    old_fact_set: dict[str, Any], new_fact_set: dict[str, Any]
) -> bool:
    """A1: same ``fact_set`` contract, both clang-produced, different producer_version.

    Only a pair where both sides name the clang producer, agree on the
    ``fact_set`` name and version, and carry two different ``producer_version``
    stamps is a payload-revision mismatch. Every other pair (including a
    contract mismatch, which is hard-blocking on its own) keeps the existing
    rules.
    """
    if not old_fact_set or not new_fact_set:
        return False
    if old_fact_set.get("producer") != CLANG_PRODUCER:
        return False
    if new_fact_set.get("producer") != CLANG_PRODUCER:
        return False
    if old_fact_set.get("name") != new_fact_set.get("name"):
        return False
    if old_fact_set.get("version") != new_fact_set.get("version"):
        return False
    old_pv = old_fact_set.get("producer_version")
    new_pv = new_fact_set.get("producer_version")
    return old_pv is not None and new_pv is not None and old_pv != new_pv
