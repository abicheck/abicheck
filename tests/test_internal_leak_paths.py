# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Path selection for the internal-namespace leak walk (ADR-046 D6, partial)."""

from __future__ import annotations

from abicheck.compare.internal_leak_paths import select_preferred_path


class TestSelectPreferredPath:
    """ADR-046 D6 (partial): prefer a value-propagating path over a shorter
    indirect-only one instead of plain ``min(paths, key=len)``."""

    def test_prefers_value_propagating_over_shorter_indirect(self) -> None:
        indirect_short = ["Public", "indirect:ptr", "Internal"]
        value_longer = ["Public", "field:x", "Mid", "field:y", "Internal"]
        assert select_preferred_path([indirect_short, value_longer]) == value_longer

    def test_shortest_wins_within_the_same_tier(self) -> None:
        short = ["Public", "field:x", "Internal"]
        long_ = ["Public", "field:x", "Mid", "field:y", "Internal"]
        assert select_preferred_path([long_, short]) == short

    def test_pointer_or_signature_beats_pure_indirect(self) -> None:
        # Neither value-propagating nor indirection-marked (e.g. a bare
        # signature-seed path) ranks above an indirect-marked one.
        plain = ["Public", "signature", "Internal"]
        indirect = ["Public", "indirect:ptr", "Internal"]
        assert select_preferred_path([indirect, plain]) == plain

    def test_single_path_returned_unchanged(self) -> None:
        only = ["Public", "field:x", "Internal"]
        assert select_preferred_path([only]) == only


# ---------------------------------------------------------------------------
# TraversalPolicy (ADR-046 D5, partial)
# ---------------------------------------------------------------------------
