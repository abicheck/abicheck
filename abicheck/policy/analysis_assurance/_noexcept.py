# SPDX-License-Identifier: Apache-2.0
"""Assurance gap for C++ ``noexcept`` exception specifications.

castxml writes no exception specification; its backend reads ``noexcept``
from the clang AST of the same header (``extract/headers/castxml/
clang_semantics_join.py``). When that join could not read a function whose
header spells one -- no clang, a clang parse error -- or the snapshot is a
castxml baseline from before extractor generation 44 (which stored a blanket
``False``), the function's ``is_noexcept_fact`` is ``NOT_COLLECTED`` and the noexcept
detector declines it. This module names that side, so the rollup reads
``partial`` instead of a clean ``complete``. A coverage gap, never a verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model import AbiSnapshot

from ...model.availability import FactStatus
from ._unobserved_finding import Side, UnobservedFinding

__all__ = ["NOEXCEPT_GAP_DIMENSION", "noexcept_findings"]

NOEXCEPT_GAP_DIMENSION = (
    "C++ noexcept exception specifications (not observed by the header backend)"
)


def _unobserved_count(snap: AbiSnapshot) -> int:
    """Functions whose ``is_noexcept_fact`` a producer recorded as not
    collected (a function no producer judged at all carries no fact)."""
    return sum(
        1
        for f in snap.declarations.functions
        if f.is_noexcept_fact is not None
        and f.is_noexcept_fact.status is FactStatus.NOT_COLLECTED
    )


def noexcept_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per side holding a function whose exception specification
    was not observed."""
    out: list[UnobservedFinding] = []
    sides: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    for label, snap in sides:
        if snap is None:
            continue
        count = _unobserved_count(snap)
        if count:
            out.append(
                UnobservedFinding(
                    NOEXCEPT_GAP_DIMENSION,
                    label,
                    f"{NOEXCEPT_GAP_DIMENSION}: {count} function(s) of the {label} "
                    "snapshot have no observed exception specification (castxml "
                    "writes none, and its clang join did not read them, or the "
                    "snapshot predates that join), so a noexcept change to them "
                    "is not assessed",
                )
            )
    return out
