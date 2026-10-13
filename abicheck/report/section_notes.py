# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Structured notes for the Markdown report's optional sections (ADR-061 gap C).

The compute half of the redundancy, out-of-surface, suppression and RTTI notes:
each ``compute_*`` reads a completed :class:`~abicheck.checker_types.DiffResult`
(or its changes) and returns a frozen struct the matching ``render_*`` in
:mod:`abicheck.report.render_markdown` formats. Moved out of
``reporter_markdown.py`` so the report layer owns them; ``None`` still means
"section absent", distinct from an empty one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..report_summary import surface_breakdown
from .redundancy_reasons import redundancy_reason_text
from .render_markdown import (
    OutOfSurfaceNote,
    RedundancyNote,
    RttiNote,
    SuppressedEntry,
    SuppressionNote,
    _contract_decision_text,
)

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model.change import Change


def compute_redundancy_note(result: DiffResult) -> RedundancyNote | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_redundancy_note`."""
    if result.redundant_count > 0:
        return RedundancyNote(
            redundant_count=result.redundant_count,
            reasons=redundancy_reason_text(result),
        )
    return None


def compute_out_of_surface_note(result: DiffResult) -> OutOfSurfaceNote | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_out_of_surface_note`."""
    if result.scope_to_public_surface and result.out_of_surface_count:
        return OutOfSurfaceNote(count=result.out_of_surface_count)
    return None


def compute_suppression_note(result: DiffResult) -> SuppressionNote | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_suppression_note`."""
    if not result.suppression_file_provided:
        return None
    entries: list[SuppressedEntry] = []
    if result.suppressed_count != 0:
        for sc in result.suppressed_changes:
            contract_text = None
            relevance = getattr(sc, "contract_relevance", None)
            if relevance is not None:
                reason_code = getattr(sc, "contract_reason_code", None)
                assurance = getattr(sc, "contract_assurance", None)
                contract_text = _contract_decision_text(
                    relevance, reason_code, assurance
                )
            entries.append(
                SuppressedEntry(
                    symbol=sc.symbol,
                    description=sc.description,
                    contract_text=contract_text,
                )
            )
    return SuppressionNote(
        suppressed_count=result.suppressed_count, entries=tuple(entries)
    )


def compute_rtti_note(breaking: list[Change]) -> RttiNote | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_rtti_note`."""
    bd = surface_breakdown(breaking)
    if not (bd.rtti or bd.internal):
        return None
    return RttiNote(
        rtti=bd.rtti, internal=bd.internal, total=bd.total, public=bd.public
    )
