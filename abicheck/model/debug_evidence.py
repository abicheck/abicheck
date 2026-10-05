# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Snapshot-level debug-info *evidence* facts (ADR-063, "no backend-specific
collection").

Evidence-tier questions -- "did this side carry usable debug info?", "did it
carry any debug *layout* content?" -- used to be answered by each checker
reaching into the snapshot's debug layout itself (now the IR store's
``declarations.debug_layout``/``debug_advanced``). This module is the one owner
of those answers, so checkers ask a backend-neutral question instead. Both
are what every debug carrier reduces to (DWARF directly; BTF, CTF and PDB via
their ``to_dwarf_metadata`` reductions), so the facts below name no format.

Layout *content* (the records/enums/base types themselves) is a separate
question owned by :mod:`abicheck.compare.debug_layout_view`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .dwarf_facts import debug_info_present

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["NO_DEBUG_INFO_EVIDENCE", "DebugInfoEvidence", "debug_info_evidence"]


@dataclass(frozen=True)
class DebugInfoEvidence:
    """What debug-info evidence one snapshot side carries.

    ``basic`` and ``advanced`` are two independent channels (each feeds its
    own detector family), so they are kept apart rather than folded into one
    boolean -- folding them once hid a both-sides-skipped asymmetry
    (``analysis_assurance._dwarf_context_status``).
    """

    #: The basic (layout) channel recorded collected debug info.
    basic: bool
    #: The advanced channel recorded debug info (may be a section-presence-
    #: only answer; see ``model.dwarf_facts.advanced_facts_collected``).
    advanced: bool
    #: The basic channel carries at least one record or enum layout. Stricter
    #: than ``basic``: a stripped binary can carry an empty ``.debug_*``
    #: section.
    layout_content: bool

    @property
    def any(self) -> bool:
        """Either channel recorded debug info."""
        return self.basic or self.advanced


NO_DEBUG_INFO_EVIDENCE = DebugInfoEvidence(
    basic=False, advanced=False, layout_content=False
)


def debug_info_evidence(snap: AbiSnapshot | None) -> DebugInfoEvidence:
    """The :class:`DebugInfoEvidence` *snap* carries (none for ``None``)."""
    if snap is None:
        return NO_DEBUG_INFO_EVIDENCE
    store = snap.declarations
    basic = store.debug_layout
    advanced = store.debug_advanced
    return DebugInfoEvidence(
        basic=debug_info_present(basic),
        advanced=debug_info_present(advanced),
        layout_content=bool(basic is not None and (basic.structs or basic.enums)),
    )
