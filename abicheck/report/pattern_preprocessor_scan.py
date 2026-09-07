# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""JSON projection of ``DiffResult.pattern_preprocessor_scan`` (ADR-068 D3/D4/D5;
plan §3 #6/#8, Phase 2b).

Mirrors ``report/finding_evolution.py``'s own shape: the *decision* (what
each primitive found, and how it folds OLD vs NEW) is made once, by
``workflows.pattern_preprocessor_scan.compute_pattern_preprocessor_scan``,
at ``checker.compare()`` time; this module only projects whatever
``DiffResult.pattern_preprocessor_scan`` already carries into the JSON
report (``compute_*``/``render_*`` split, ``abicheck/report/AGENTS.md``).
Unconditional, like ``add_finding_evolution`` -- a plain single comparison
still owes every consumer this block, naming it explicitly (``None`` when
``pattern_preprocessor_scan=False`` was passed to ``compare()``, which no
front end does today) rather than omitting the field.

JSON is the only wired projection so far, same deliberate scope
``finding_evolution``'s own module docstring documents for Markdown/HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import DiffResult


def compute_pattern_preprocessor_scan_json(result: DiffResult) -> dict[str, Any] | None:
    """Project *result*'s already-computed pattern/preprocessor scan facts.

    Decides nothing; only reads ``DiffResult.pattern_preprocessor_scan`` and
    calls its own ``to_dict()``. ``getattr``/duck-typed, for the same reason
    ``finding_evolution.compute_finding_evolution_summary`` reads its input
    that way -- a report path may hand this a duck-typed stand-in rather
    than a real ``DiffResult``.
    """
    facts = getattr(result, "pattern_preprocessor_scan", None)
    if facts is None:
        return None
    to_dict = getattr(facts, "to_dict", None)
    if not callable(to_dict):
        return None
    return dict(to_dict())


def add_pattern_preprocessor_scan(d: dict[str, Any], result: DiffResult) -> None:
    """Attach the ``pattern_preprocessor_scan`` block to a JSON report mapping *d*."""
    d["pattern_preprocessor_scan"] = compute_pattern_preprocessor_scan_json(result)
