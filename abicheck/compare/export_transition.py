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

A note on the finding descriptions below: they say the *declaration remains
present*, never that it is declared in the available headers. These detectors
read only the two export facts, and a matched pair can reach them from a
producer that never saw a header at all -- a DWARF-derived record leaves fact
(a) unknown. Claiming header evidence there would be the same over-claim the
whole split exists to remove (CodeRabbit review).

One detector, owned here rather than grown into ``diff_symbols.py``: it
belongs to the export axis, not the signature axis every other check in
that module compares. See ``model/surface_facts.py`` for the three facts it
reads and the ``Visibility.PUBLIC`` entry in
``docs/contribute/known-gaps.md`` for why this finding exists at all.
"""

from __future__ import annotations

from collections.abc import Container

from ..checker_types import Change
from ..diff_helpers import make_change
from ..model import Function, Variable
from ..model.change_catalog.kinds import ChangeKind
from ..model.surface_facts import (
    has_observed_contract_evidence,
    in_public_contract,
    is_abi_visible,
    is_binary_exported,
    is_confirmed_false,
    is_export_confirmed_absent,
    is_legacy_derived,
    surface_fact_summary,
)
from ..model.synthetic_key import is_synthetic_ctor_key, is_synthetic_dtor_key

__all__ = [
    "survives_export_narrowing",
    "check_export_gained",
    "check_export_lost",
    "check_function",
    "check_variable",
    "check_variable_export_gained",
    "check_variable_export_lost",
    "deleted_declaration_is_public",
    "surface_exit_is_evidence_gap",
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


def survives_export_narrowing(
    key: str,
    decl: Function,
    exported: set[str],
    name_counts: dict[str, int],
) -> bool:
    """Whether *decl* survives narrowing a snapshot to its observed exports.

    ``diff_symbols._public_functions`` restricts a snapshot that carries ELF
    symbols to the declarations the binary actually exports, which is right
    for the DWARF-recorded internal subprograms it exists to exclude. Four
    things legitimately escape it, and they belong on the export axis rather
    than inline in the symbol differ:

    * an exact mangled match, or an unambiguous display-name match;
    * a ``= delete``d declaration that DWARF did not supply — it has no
      symbol by construction;
    * a synthetic constructor/destructor key. castxml omitted the real
      mangled name, so the key can never equal a real exported symbol;
      requiring a match would always fail and silently drop a genuinely
      public overload (case78's removed / case111's added overload) or a
      public virtual destructor whose visibility was resolved from source
      access (Codex review, PR #582);
    * a declaration the run was *told* is promised and that simply is not
      exported — a public inline member, or one a version script stopped
      exporting. Dropping those reported a gained export as ``FUNC_ADDED``
      rather than ``FUNC_EXPORT_ADDED`` and left a public unexported inline
      out of the map altogether (CodeRabbit review). See
      ``has_observed_contract_evidence`` for why legacy-derived evidence
      deliberately does not widen this.
    """
    return (
        key in exported
        or (decl.name in exported and name_counts.get(decl.name) == 1)
        or (decl.is_deleted and not decl.deleted_from_dwarf)
        or is_synthetic_ctor_key(key)
        or is_synthetic_dtor_key(key)
        or has_observed_contract_evidence(decl)
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
                f"Function now exported by the binary while its declaration "
                f"remains present: {f_new.name}"
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
                f"Variable now exported by the binary while its declaration "
                f"remains present: {v_new.name}"
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
                f"Function no longer exported by the binary, but its "
                f"declaration remains present: {f_old.name}"
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
                f"Variable no longer exported by the binary, but its "
                f"declaration remains present: {v_old.name}"
            ),
            old_value=v_old.visibility.value,
            new_value=v_new.visibility.value,
            symbol_binding=v_old.elf_binding.value if v_old.elf_binding else None,
            entity_id=v_old.entity_id or v_new.entity_id,
            surface_facts=surface_fact_summary(v_new),
        )
    ]


def surface_exit_is_evidence_gap(
    old: Function | Variable,
    new_decl: Function | Variable | None,
    *,
    old_exported_symbols: Container[str] = frozenset(),
    key: str = "",
) -> bool:
    """Whether *old*'s disappearance from the compared public surface is an
    asymmetry in this run's *evidence*, not an observed change.

    ``diff_symbols._public_functions``/``_public_variables`` build each side's
    public surface from that side's own facts, and one of the facts they read
    -- ``in_public_contract`` (b) -- is only ever *established* by a producer
    the run actually gave a public-header set to. So two snapshots of the same
    library, one captured with that set and one without, disagree about which
    declarations belong to the surface even when nothing about the library
    changed: every promised-but-unexported declaration (a public inline
    member, one a version script keeps out of ``.dynsym``) is in OLD's surface
    and absent from NEW's. The removal path then read that as a transition and
    reported it -- ``FUNC_VISIBILITY_CHANGED`` with ``old_value ==
    new_value == "hidden"`` for a function whose visibility did not change, or
    ``VAR_REMOVED`` for a variable still declared on both sides -- an ABI
    break manufactured out of missing evidence.

    This is the same rule :func:`_export_was_lost` already applies on the
    matched-pair path ("exported before, unknown now" is a gap, not a
    transition); the unmatched path simply never applied it.

    * the entity is still declared on the NEW side (a declaration that is
      genuinely gone is a real removal, and is reported);
    * OLD was not exported. Asked of two independent sources, because a
      lost export *is* an observation (reported by :func:`check_export_lost`
      and the removal path) and must never be suppressed: the declaration's
      own (c), and -- since a legacy/pre-split record's (c) is re-derived
      from the ``visibility`` enum rather than observed -- the OLD
      artifact's own export table, passed in as *old_exported_symbols*
      (membership tested for *key*, the same map key the surface was built
      under). A caller that passes neither still gets the fact-only answer;
    * NEW is not confirmed exported (if it is, nothing left any surface);
    A caller that gets ``True`` here must then treat the pair as **matched**
    and compare it, never drop it (Codex review, P1). The declaration is on
    both sides, so its signature/type is still comparable, and weakening the
    evidence for one question ("is this still in the promised surface?") must
    not silence a different one the evidence does answer ("did its return
    type change?"). Emitting nothing would leave a real change on such a
    declaration reported by nothing at all -- as would the pre-fix behaviour,
    which reported a manufactured visibility finding and no signature diff.

    Four conditions, all required:

    * OLD's place in the surface rested on real producer contract evidence
      while NEW's own (b) was never established by a producer. A NEW side
      that *observed* the declaration out of the contract (a genuine move to
      a private header) is a real finding and is not suppressed -- only a
      value re-derived from the legacy ``visibility`` enum counts as
      unestablished, exactly as :func:`has_observed_contract_evidence` treats
      it on the positive side.
    """
    if new_decl is None:
        return False
    if is_binary_exported(new_decl):
        return False
    # OLD must not have been exported, asked of both available sources: the
    # declaration's own (c), and the OLD artifact's observed export table.
    # The second is what makes a legacy/pre-split record safe here -- its (c)
    # is re-derived from the `visibility` enum rather than observed, so a
    # real export loss would otherwise be suppressed on evidence that never
    # looked at the binary. That is the one failure worse than the
    # manufactured finding this guard removes.
    if key in old_exported_symbols or is_binary_exported(old):
        return False
    if not has_observed_contract_evidence(old):
        return False
    new_contract = in_public_contract(new_decl)
    observed_out_of_contract = is_confirmed_false(
        new_contract
    ) and not is_legacy_derived(new_contract)
    return not has_observed_contract_evidence(new_decl) and not observed_out_of_contract
