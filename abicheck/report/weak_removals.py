# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""How many of a section's removed exports were WEAK-bound -- a disclosure,
never a reclassification.

A vanished export is breaking whatever its binding: demoting a WEAK
(vague-linkage) removal was tried twice and reverted twice, because an
``extern template`` consumer links against the library's WEAK definition
and does break (docs/contribute/known-gaps/linkage-blind-removal-attempted-
twice-reverted-twice-the.md). What a reader cannot see without this note is
that a flood such as oneDAL's 1,744 ``core`` removals is entirely WEAK, which
changes what they should check before acting. The per-finding JSON already
carries ``symbol_binding``; this only summarises it where the list is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.elf_facts import SymbolBinding
from .change_operation import operation_for_kind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.change import Change

__all__ = ["weak_removal_counts", "weak_removal_note_lines"]


def weak_removal_counts(changes: Sequence[Change]) -> tuple[int, int]:
    """``(weak, removed_with_binding)`` over *changes*' removal findings.

    Only removals whose binding was observed count: an unstamped removal
    (no symbol table read) is neither weak nor strong, so it is left out of
    both numbers rather than read as GLOBAL.
    """
    bindings = [
        c.symbol_binding
        for c in changes
        if c.symbol_binding and operation_for_kind(c.kind.value) == "removed"
    ]
    return bindings.count(SymbolBinding.WEAK.value), len(bindings)


def weak_removal_note_lines(changes: Sequence[Change]) -> tuple[str, ...]:
    """A Markdown blockquote naming the WEAK share of the removals, or ``()``."""
    weak, removed = weak_removal_counts(changes)
    if not weak:
        return ()
    return (
        f"> **{weak} of {removed} removed export(s) with an observed binding "
        "were WEAK** (vague linkage: template instantiations, inline functions).",
        "> They stay breaking: a consumer that relied on the library's",
        "> definition (`extern template`, explicit instantiation) fails to link",
        "> or load. One that compiled its own copy is unaffected -- check which",
        "> applies before suppressing with a `binding: weak` rule.",
    )
