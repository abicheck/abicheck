"""Retired config-key spellings (ADR-091 D7), kept apart from
`retired_surfaces.py`, whose file-size record is fixed.

`retired_surfaces.RETIRED_SURFACES` appends these so the docs sweep checks both.
"""

from __future__ import annotations

RETIRED_CONFIG_SURFACES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        "ADR-091 R3/R13 config keys (acknowledgment: -> acknowledgments:, source.method -> sources.method)",
        ("source.method", "acknowledgment.file", "acknowledgment.breaks"),
        frozenset(
            {"start/upgrading-to-0.7.md", "AGENTS.md", "contribute/known-gaps.md"}
        ),
    ),
)
