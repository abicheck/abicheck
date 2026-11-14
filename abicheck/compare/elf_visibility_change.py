# SPDX-License-Identifier: Apache-2.0
"""``symbol_elf_visibility_changed``: a transition between two exported ELF
visibilities that no more specific detector reports."""

from __future__ import annotations

from typing import Any

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.elf_facts import SymbolType

__all__ = ["elf_visibility_change"]


def elf_visibility_change(sym_name: str, s_old: Any, s_new: Any) -> list[Change]:
    """Detect ELF st_other visibility transitions among exported visibilities.

    HIDDEN/INTERNAL transitions are already caught by FUNC_VISIBILITY_CHANGED or
    FUNC_REMOVED (symbol disappears from exported set). Only emit for transitions
    among exported visibilities (DEFAULT↔PROTECTED).
    """
    if s_old.visibility == s_new.visibility:
        return []
    old_vis = s_old.visibility
    new_vis = s_new.visibility
    if old_vis in ("hidden", "internal") or new_vis in ("hidden", "internal"):
        return []
    # One event, one finding: a function's DEFAULT<->PROTECTED change is
    # FUNC_VISIBILITY_PROTECTED_CHANGED, a data object's (both sides
    # OBJECT/COMMON) PROTECTED_VISIBILITY_CHANGED. This kind keeps the rest
    # (TLS, IFUNC, NOTYPE), which neither specific detector reports.
    if s_old.sym_type == SymbolType.FUNC or {s_old.sym_type, s_new.sym_type} <= {
        SymbolType.OBJECT,
        SymbolType.COMMON,
    }:
        return []
    return [
        make_change(
            ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
            symbol=sym_name,
            name=sym_name,
            old=old_vis,
            new=new_vis,
        )
    ]
