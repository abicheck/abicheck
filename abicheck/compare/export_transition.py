# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The *binary* half of a surface change whose declaration did not change.

One detector, owned here rather than grown into ``diff_symbols.py``: it
belongs to the export axis, not the signature axis every other check in
that module compares. See ``model/surface_facts.py`` for the three facts it
reads and the ``Visibility.PUBLIC`` entry in
``docs/contribute/known-gaps.md`` for why this finding exists at all.
"""

from __future__ import annotations

from ..checker_types import Change
from ..diff_helpers import make_change
from ..model import Function, Variable
from ..model.change_catalog.kinds import ChangeKind
from ..model.surface_facts import (
    is_abi_visible,
    is_binary_exported,
    is_export_confirmed_absent,
    surface_fact_summary,
)

__all__ = [
    "check_export_gained",
    "check_export_lost",
    "check_function",
    "check_variable",
    "check_variable_export_gained",
    "check_variable_export_lost",
    "deleted_declaration_is_public",
]


def check_function(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Every finding this module has about a matched function pair's export.

    The one entry point ``diff_symbols`` calls. Which *direction* a
    transition went, and whether either direction has a finding at all, is
    this module's question rather than the caller's -- a caller that lists
    the directions itself has to be edited again the next time one is added,
    and has no way to enforce that they stay mutually exclusive.
    """
    return check_export_lost(mangled, f_old, f_new) + check_export_gained(
        mangled, f_old, f_new
    )


def check_variable(mangled: str, v_old: Variable, v_new: Variable) -> list[Change]:
    """:func:`check_function` for data symbols."""
    return check_variable_export_lost(mangled, v_old, v_new) + (
        check_variable_export_gained(mangled, v_old, v_new)
    )


def deleted_declaration_is_public(new: Function | None, old: Function | None) -> bool:
    """Whether *new* is a ``= delete``d declaration that counts as public ABI,
    judged on whichever side still carries the evidence.

    ``False`` for a ``None`` or non-deleted *new*, so a caller can use this as
    its whole guard rather than restating the deleted check beside it.

    A deleted declaration has no symbol *by construction* -- ``dwarf_snapshot``
    keeps it only for cross-reference -- so its own export fact is a confirmed
    ``False``, and an eligibility test that asks the new side alone rejects
    exactly the declarations the deletion detector exists to report (Codex
    review, P2). The old side is what says whether the deleted API was public.

    Both the detector and the removal path's "defer to the detector" guard
    call this, so the two cannot disagree about when a deletion is reported --
    disagreement there is a double report, not a missed one.
    """
    if new is None or not new.is_deleted:
        return False
    return is_abi_visible(new) or (old is not None and is_abi_visible(old))


def _export_was_lost(old: Function | Variable, new: Function | Variable) -> bool:
    """Confirmed exported before, confirmed not exported now.

    Both halves must be *confirmed* (``PRESENT``/``PARTIAL`` with a real
    value): "exported before, unknown now" is a gap in this run's evidence,
    not an observed transition, and rendering it as one would manufacture a
    finding out of missing evidence -- the failure mode the whole split
    exists to close.
    """
    return is_binary_exported(old) and is_export_confirmed_absent(new)


def _export_was_gained(old: Function | Variable, new: Function | Variable) -> bool:
    """:func:`_export_was_lost` in the other direction.

    Symmetric in the same way and for the same reason: both halves must be
    *confirmed*, so "unknown before, exported now" stays a gap in this run's
    evidence rather than becoming an observed addition.
    """
    return is_export_confirmed_absent(old) and is_binary_exported(new)


def check_export_gained(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """A matched pair that gained an export while its declaration stayed.

    The compatible mirror of :func:`check_export_lost`, and not optional
    symmetry: before the three facts were split, a promised-but-unexported
    declaration failed the old ``visibility in (PUBLIC, ELF_ONLY)`` filter
    outright, so the old side was *absent* from the public index and the pair
    never matched -- the run reported ``FUNC_ADDED``. Now that the declaration
    keeps its place on both sides the pair matches, and without this the run
    reports **nothing at all** for a version script that newly exports an
    existing declaration (Codex review, P2).

    That silence is the failure this closes. Trading one wrong finding for a
    missing one is the same mistake the variable half of the loss side made,
    and an addition that vanishes is precisely what "record before disposing"
    forbids: an observed change to the export table is recorded, then
    classified as the compatible thing it is -- never dropped because it
    happened to be good news.
    """
    if not _export_was_gained(f_old, f_new):
        return []
    return [
        make_change(
            ChangeKind.FUNC_EXPORT_ADDED,
            symbol=mangled,
            name=f_new.name,
            description=(
                f"Function now exported by the binary, and already declared "
                f"in the available headers: {f_new.name}"
            ),
            old_value=f_old.visibility.value,
            new_value=f_new.visibility.value,
            symbol_binding=f_new.elf_binding.value if f_new.elf_binding else None,
            entity_id=f_new.entity_id or f_old.entity_id,
            surface_facts=surface_fact_summary(f_new),
        )
    ]


def check_variable_export_gained(
    mangled: str, v_old: Variable, v_new: Variable
) -> list[Change]:
    """:func:`check_export_gained` for data symbols.

    Present for the same reason its loss-side sibling is: nothing else in
    ``_check_variable`` compares export presence, so a data symbol that
    gained an export would otherwise produce no finding on either axis.
    """
    if not _export_was_gained(v_old, v_new):
        return []
    return [
        make_change(
            ChangeKind.VAR_EXPORT_ADDED,
            symbol=mangled,
            name=v_new.name,
            description=(
                f"Variable now exported by the binary, and already declared "
                f"in the available headers: {v_new.name}"
            ),
            old_value=v_old.visibility.value,
            new_value=v_new.visibility.value,
            symbol_binding=v_new.elf_binding.value if v_new.elf_binding else None,
            entity_id=v_new.entity_id or v_old.entity_id,
            surface_facts=surface_fact_summary(v_new),
        )
    ]


def check_export_lost(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """A matched pair whose export went away while its declaration stayed.

    This is the second of the two findings the ``Visibility.PUBLIC``
    known-gaps entry calls for. Before the split, a declaration that stayed
    byte-identical in the headers while the artifact stopped exporting it
    left the new side out of the public index entirely, and the run reported
    one wrong finding -- a removed source API. Now the declaration keeps its
    place (``in_source_declaration_index``) and the export change is
    reported on its own axis instead: a disappearing dynamic export can
    still break an already-linked consumer, so it is not something to
    ignore, it is something to report as what it is.

    Gated on *confirmed* evidence on both sides (``PRESENT``/``PARTIAL``
    with a real value): "exported before, unknown now" is a gap in this
    run's evidence, not an observed transition, and must not be rendered as
    one. Findings whose old side was never exported, or whose new side still
    is, produce nothing here.
    """
    if not _export_was_lost(f_old, f_new):
        return []
    # A removal reported elsewhere is not this: the caller only reaches here
    # for a *matched* pair, i.e. a declaration present on both sides.
    return [
        make_change(
            ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            name=f_old.name,
            description=(
                f"Function no longer exported by the binary, but still "
                f"declared in the available headers: {f_old.name}"
            ),
            old_value=f_old.visibility.value,
            new_value=f_new.visibility.value,
            symbol_binding=f_old.elf_binding.value if f_old.elf_binding else None,
            entity_id=f_old.entity_id or f_new.entity_id,
            surface_facts=surface_fact_summary(f_new),
        )
    ]


def check_variable_export_lost(
    mangled: str, v_old: Variable, v_new: Variable
) -> list[Change]:
    """:func:`check_export_lost` for data symbols.

    Not an optional symmetry: a variable whose export disappears breaks an
    already-linked consumer exactly as a function's does, and before the
    split that break *was* reported -- as ``VAR_REMOVED``, because the
    unexported new side dropped out of the public index. Now that the
    declaration keeps its place there, the pair matches and nothing else in
    ``_check_variable`` compares export presence, so without this the run
    would report no change at all (Codex review, P1) -- trading one wrong
    finding for a missing one.
    """
    if not _export_was_lost(v_old, v_new):
        return []
    return [
        make_change(
            ChangeKind.VAR_VISIBILITY_CHANGED,
            symbol=mangled,
            name=v_old.name,
            description=(
                f"Variable no longer exported by the binary, but still "
                f"declared in the available headers: {v_old.name}"
            ),
            old_value=v_old.visibility.value,
            new_value=v_new.visibility.value,
            symbol_binding=v_old.elf_binding.value if v_old.elf_binding else None,
            entity_id=v_old.entity_id or v_new.entity_id,
            surface_facts=surface_fact_summary(v_new),
        )
    ]
