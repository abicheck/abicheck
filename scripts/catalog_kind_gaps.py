#!/usr/bin/env python3
"""Render a catalog case's ``expected_kinds_gaps`` for its generated doc page.

``catalog/ground_truth.json``'s ``expected_kinds_gaps`` declares, per header
frontend, the ``expected_kinds`` a native lane misses (the strict kinds gate,
``tests/expected_kinds_gate.py``, owns its schema and semantics). Split out of
``gen_examples_docs.py`` to keep that generator under its file-size record.
Pure stdlib.
"""

from __future__ import annotations

_DEFAULT_MODES = ("debug-headers", "build-source")


def gap_pairs(raw: object) -> list[tuple[str, dict]]:
    """``(frontend, gap)`` for every declared gap; a bare object is one gap."""
    if not isinstance(raw, dict):
        return []
    return [
        (frontend, gap)
        for frontend, value in raw.items()
        for gap in (value if isinstance(value, list) else [value])
    ]


def meta_rows(raw: object) -> str:
    """One meta-table row per declared gap: frontend, modes, kinds."""
    rows = ""
    for frontend, gap in gap_pairs(raw):
        modes = ", ".join(gap.get("modes") or _DEFAULT_MODES)
        kinds = ", ".join(f"`{k}`" for k in gap["kinds"])
        rows += (
            f"| **Known kind gap ({frontend}; {modes})** | {kinds} — see note below |\n"
        )
    return rows


def provenance_notes(raw: object) -> list[str]:
    """One provenance paragraph per declared gap: its reason and known gap."""
    return [
        f"**Known kind gap ({frontend}):** {gap['reason']} "
        f"(known gap: {gap['known_gap']})\n"
        for frontend, gap in gap_pairs(raw)
    ]
