"""Shared test helpers for the test_comparability_gate*.py family.

Split out as a leaf module (rather than duplicated or cross-imported between
test files) once `_ANY_NEW_HEADERS`/`_hdrs_slot` were needed by more than one
test_comparability_gate*.py file — see test_comparability_gate.py's own
module docstring for why this family is split across multiple files in the
first place (the shared file-size hard cap).
"""

from __future__ import annotations

import json

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
