# SPDX-License-Identifier: Apache-2.0
"""The grade of a removed ELF version node that bound no symbol."""

from __future__ import annotations

from typing import Any

from ..model.change_catalog.registry import Verdict

__all__ = ["unbound_node_grade"]


def unbound_node_grade(elf: Any, version: str) -> dict[str, Any]:
    """Grade the removal of a version node no OLD symbol was bound to.

    A consumer records in ``.gnu.version_r`` only the versions of symbols it
    binds, so a node that bound none cannot be required by any consumer:
    dropping it breaks nobody, and the kind's BREAKING default is lowered to
    a risk (a hand-written ``dlvsym`` lookup could still name it). Disclosed
    through ``modulation_reason`` (ADR-025 D4.1).
    """
    versions = {getattr(sym, "version", "") for sym in getattr(elf, "symbols", [])}
    # No versioned symbol at all: the binding facts were not captured (or the
    # table is empty), which proves nothing unbound -- keep BREAKING.
    if version in versions or not (versions - {""}):
        return {}
    return {
        "effective_verdict": Verdict.COMPATIBLE_WITH_RISK,
        "modulation_reason": "version_node_bound_no_symbol",
        "modulation_rule": "symbol_version_defined_removed.unbound_node",
    }
