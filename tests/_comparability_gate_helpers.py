"""Shared test helpers for the test_comparability_gate*.py family.

Split out as a leaf module (rather than duplicated or cross-imported between
test files) once `_ANY_NEW_HEADERS`/`_hdrs_slot` were needed by more than one
test_comparability_gate*.py file — see test_comparability_gate.py's own
module docstring for why this family is split across multiple files in the
first place (the shared file-size hard cap).
"""

from __future__ import annotations

import json

from abicheck.comparability_sequences import (
    HeaderSetDelta,
    header_sequence_is_scope_confirmed_evolution,
    include_sequence_is_scope_confirmed_evolution,
    scope_header_delta,
)

# scope_new_headers is required by the header_sequence/include_sequence
# carve-out helpers (Codex review, PR #641 follow-up, ninth P1): the
# appended sequence entries must correspond to headers genuinely new to the
# declared scope, not merely an additive-shaped sequence on its own.
# _ANY_NEW_HEADERS is a permissive stand-in covering every header name used
# across the shape-only test blocks in this family, so those tests keep
# isolating the SHAPE logic (trailing-append/reorder/removal/owned-growth)
# from the dedicated correspondence-check tests.
_ANY_NEW_HEADERS = frozenset({"a.h", "b.h", "c.h"})


def _hdrs_slot(idx: int, pairs: list[tuple[str, str]]) -> str:
    return f"{idx}:hdrs:{json.dumps(sorted(pairs))}"


# Growth-only views of the header-set evolution predicates. The dedicated
# growth-only predicates were deleted once header-set evolution (additions AND
# removals) superseded them; these adapters keep the growth-shape assertions
# stated against the one production path, with nothing scope-removed.


def _growth_delta(added) -> HeaderSetDelta | None:
    return None if added is None else HeaderSetDelta(frozenset(added), frozenset())


def seq_growth(old_value, new_value, added) -> bool:
    return header_sequence_is_scope_confirmed_evolution(
        old_value, new_value, _growth_delta(added)
    )


def incl_growth(old_value, new_value, added) -> bool:
    return include_sequence_is_scope_confirmed_evolution(
        old_value, new_value, HeaderSetDelta(frozenset(added or ()), frozenset())
    )


def scope_added(old_value, new_value) -> set[str] | None:
    delta = scope_header_delta(old_value, new_value)
    if delta is None or delta.opaque:
        return None
    return set(delta.added)
