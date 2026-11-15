# SPDX-License-Identifier: Apache-2.0
"""The grade of a function removed from the public headers whose export stays.

A declaration that leaves the public headers is a contract removal, and it is
still reported as ``func_removed`` (silencing it would hide a real contract
change). But when the new library provably still exports the same symbol,
every already-built consumer keeps binding it and runs: only a consumer that
names the function stops compiling. That is a source break, so the finding is
graded ``API_BREAK``, not the kind's ``BREAKING`` default.

"Provably" means NEW's export table was read and lists the symbol; an unread
table proves nothing, so every removal keeps BREAKING.
"""

from __future__ import annotations

from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES, exported_symbol_names
from ..model import AbiSnapshot
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.change_catalog.registry import Verdict
from .edge_query import export_table_covered
from .parameter_replacement import pair_parameter_replacements

__all__ = ["finish_function_changes", "grade_declaration_removed_export_kept"]


def grade_declaration_removed_export_kept(
    changes: list[Change], new: AbiSnapshot
) -> list[Change]:
    """*changes* with each ``func_removed`` whose symbol NEW still exports
    graded ``API_BREAK`` (disclosed via ``modulation_reason``, ADR-025 D4.1).

    Mutates the matching changes in place and returns the same list.
    """
    if new.elf is None or not export_table_covered(new, "elf"):
        return changes
    new_exports = exported_symbol_names(new.elf, FUNCTION_SYMBOL_TYPES)
    for change in changes:
        if (
            change.kind is ChangeKind.FUNC_REMOVED
            and change.effective_verdict is None
            and change.symbol in new_exports
        ):
            change.effective_verdict = Verdict.API_BREAK
            change.modulation_reason = "declaration_removed_export_kept"
            change.modulation_rule = "func_removed.export_kept"
    return changes


def finish_function_changes(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """The functions detector's closing pass: pair provable parameter
    replacements, then grade removals whose export survives."""
    return grade_declaration_removed_export_kept(
        pair_parameter_replacements(changes, old, new), new
    )
