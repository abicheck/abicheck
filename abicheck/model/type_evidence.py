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

"""Per-side type-level evidence of a comparison (ADR-090 D1(b)/(c)).

A compared side carries *type-level evidence* when a backend gave it usable
type or layout data: debug info (DWARF directly; BTF, CTF and PDB through
their ``to_dwarf_metadata`` reductions, which :mod:`.debug_evidence` already
folds into one backend-neutral answer) or a parsed header AST
(``AbiSnapshot.from_headers``). A side with only a symbol/export table has
none: a parameter, return-type, enumerator or layout change on an exported
symbol is invisible to it.

This module records that *fact* per side. Whether the fact turns a clean
verdict into ``UNCONFIRMED`` is a policy decision
(:mod:`abicheck.policy.unconfirmed_compatibility`), because it also depends
on the change-derived verdict and the run's ``assurance.unconfirmed`` mode.

A stored snapshot that predates a field reads it at its absent default
(``from_headers: False``, no debug layout), so a missing fact can only read
as *no* evidence -- never as a clean claim (ADR-090, maintainer answer 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .debug_evidence import debug_info_evidence

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["TypeEvidenceFacts", "side_has_type_evidence", "type_evidence_facts"]


def side_has_type_evidence(snap: AbiSnapshot) -> bool:
    """Whether *snap* carries type-level evidence.

    A snapshot with no binary metadata at all (no ELF, PE or Mach-O block and
    not a symbols-only dump) is not a binary operand: it is a hand-built or
    header-derived model surface (the typed-API and unit-test construction
    path), whose declarations *are* its type-level evidence, the same reading
    ``confidence._detect_evidence_tiers`` gives it.
    """
    if bool(getattr(snap, "from_headers", False)):
        return True
    if debug_info_evidence(snap).any:
        return True
    is_binary_operand = (
        snap.elf is not None
        or getattr(snap, "pe", None) is not None
        or getattr(snap, "macho", None) is not None
        or bool(getattr(snap, "elf_only_mode", False))
    )
    return not is_binary_operand


@dataclass(frozen=True)
class TypeEvidenceFacts:
    """What one comparison's two sides carried, and whether they are one input.

    ``old`` is ``None`` for a ``compare --no-baseline`` audit (no compared
    baseline side). ``identical_inputs`` is ``True`` only when the two sides
    are provably the same comparison input -- byte-identical binaries, or
    snapshots with the same persisted content -- which D1(c) exempts.
    """

    old: bool | None
    new: bool
    identical_inputs: bool = False
    #: ``(side, reason)`` for each side lacking evidence although headers were
    #: supplied: header scoping fell back to the export table
    #: (``AbiSnapshot.scope_fallback``, e.g. ``"mangling-fallback"``), so the
    #: parsed header surface was not kept.
    header_scope_fallback: tuple[tuple[str, str], ...] = ()

    def sides_without_type_evidence(self) -> tuple[str, ...]:
        """``"old"``/``"new"``, in that order, for each compared side lacking it."""
        sides: list[str] = []
        if self.old is False:
            sides.append("old")
        if not self.new:
            sides.append("new")
        return tuple(sides)

    def to_dict(self) -> dict[str, object]:
        return {
            "old": self.old,
            "new": self.new,
            "identical_inputs": self.identical_inputs,
            "header_scope_fallback": dict(self.header_scope_fallback),
        }


def type_evidence_facts(
    old: AbiSnapshot | None,
    new: AbiSnapshot,
    *,
    identical_inputs: bool = False,
) -> TypeEvidenceFacts:
    """The :class:`TypeEvidenceFacts` of comparing *old* to *new*."""
    old_has = side_has_type_evidence(old) if old is not None else None
    new_has = side_has_type_evidence(new)
    fallback = tuple(
        (side, str(reason))
        for side, snap, has in (("old", old, old_has), ("new", new, new_has))
        if snap is not None
        and has is False
        and (reason := getattr(snap, "scope_fallback", None))
    )
    return TypeEvidenceFacts(
        old=old_has,
        new=new_has,
        identical_inputs=identical_inputs,
        header_scope_fallback=fallback,
    )
