# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Path selection for the internal-namespace leak walk (ADR-046 D6, partial)."""

from __future__ import annotations

import pytest

from abicheck.compare.internal_leak_names import is_internal_candidate
from abicheck.compare.internal_leak_paths import (
    _declaration_scope,
    select_preferred_path,
)


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


# ---------------------------------------------------------------------------
# Header-frontend spellings: declaration scopes and bare record names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "mangled", "scope"),
    [
        # Header backends write the leaf; the scope comes from the mangling.
        ("make", "_ZN3lib4makeEv", "lib"),
        ("g", "_ZN3lib1gE", "lib"),
        ("get", "_ZNK3lib3Pub3getEv", "lib::Pub"),
        ("free_fn", "", ""),
        # An operator's scope is the text before `::operator`, whatever
        # spaces, angle brackets or parentheses its own name carries.
        ("operator new", "_ZN3lib3PubnwEm", "lib::Pub"),
        ("lib::Pub::operator new(unsigned long)", "", "lib::Pub"),
        ("lib::Pub::operator<(lib::Pub const&) const", "", "lib::Pub"),
        ("lib::Pub::operator char const*() const", "", "lib::Pub"),
        ("lib::Pub::operator()(int)", "", "lib::Pub"),
        ("lib::operator<<(lib::Pub const&)", "", "lib"),
        # A template function's demangling leads with its return type.
        ("int lib::api::f<lib::X>(lib::X)", "", "lib::api"),
    ],
)
def test_declaration_scope(name: str, mangled: str, scope: str) -> None:
    assert _declaration_scope(name, mangled)() == scope


@pytest.mark.parametrize(
    ("name", "records", "internal"),
    [
        # A bare record name reads through its one qualified identity...
        ("Impl", {"lib::detail::Impl"}, True),
        # ...including a template instance whose arguments are qualified, as
        # castxml writes it: the leaf is `Box<lib::X>`, not `X>`.
        ("Box<lib::X>", {"lib::detail::Box<lib::X>"}, True),
        ("Box<lib::detail::X>", {"lib::Box<lib::detail::X>"}, False),
        ("Box<lib::X>", {"lib::Box<lib::X>"}, False),
        # An ambiguous leaf falls back to the spelling, which is not internal.
        ("Impl", {"lib::detail::Impl", "lib::Impl"}, False),
    ],
)
def test_is_internal_candidate_reads_a_bare_name_through_its_record(
    name: str, records: set[str], internal: bool
) -> None:
    assert is_internal_candidate(name, frozenset(records)) is internal
