# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-065 D6/D7's scope-completeness axis as ``abicheck aggregate`` reads
it: the third orthogonal ``0``/``1`` exit floor a per-target report can
carry, the exact sibling of the contract-coverage and analysis-assurance
readers. A sibling leaf rather than more lines in ``load.py``/``gate.py``,
both of which sit at the 800-line production cap.

Read, never recomputed: a release report's ``exit`` block states
``incomplete_scope_contribution`` (``1`` only under ``--on-incomplete-scope
block``) and ``no_comparison_completed_contribution`` (``1`` whenever the
run completed no comparison, under either policy), and this aggregate holds
none of the acquisition evidence needed to answer either again. Without
this fold, a release whose ``run_outcome`` reads ``scope: incomplete`` while
``gate``/``operational`` stay ``none`` aggregated to a green target even
though the originating comparison exited ``1`` (Codex review).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .gate import _is_valid_contribution, contract_coverage_blocks

#: The ``exit``-block keys this axis folds, in the order they are stated.
SCOPE_EXIT_KEYS: tuple[str, ...] = (
    "incomplete_scope_contribution",
    "no_comparison_completed_contribution",
)


def scope_completeness_exit(data: Mapping[str, Any]) -> int:
    """The report's own ADR-065 scope-completeness contribution (``0``/``1``).

    The ``max`` over :data:`SCOPE_EXIT_KEYS` in every ``exit`` block the
    document's shape can carry (a scan-shaped report nests its exit block
    under ``diff``, exactly where its coverage fields live). Fails open
    like its siblings: a report without an ``exit`` block, or with a
    malformed value, contributes ``0``.
    """
    worst = 0
    for block in contract_coverage_blocks(data):
        exit_block = block.get("exit")
        if not isinstance(exit_block, Mapping):
            continue
        for key in SCOPE_EXIT_KEYS:
            raw = exit_block.get(key)
            if _is_valid_contribution(raw):
                worst = max(worst, raw)
    return worst
