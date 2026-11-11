# Copyright 2026 Nikolay Petrov
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

"""The declared alignment of an exported variable, for corroborating an
address-derived alignment change (``diff_platform_elf_symbols``), and the
upper bound an exported object's address puts on it (``placement_bits``)."""

from __future__ import annotations

from ..model import AbiSnapshot
from ..model.arithmetic_type_spellings import is_arithmetic_spelling
from ..model.availability import FactStatus


def declared_alignment_bits(snap: AbiSnapshot, sym_name: str) -> int | None:
    """Return the DWARF/header-declared alignment (bits) for a variable, if known.

    An explicit ``alignment_bits`` wins (unless it is only a lower bound,
    ``PARTIAL``: then nothing is known); otherwise the compiler-derived natural
    alignment of an arithmetic-typed variable (``type_abi_fact``, PRESENT only)
    corroborates -- the clang header backend records no computed alignment for
    a plain variable but does record this fact, so address-placement noise on
    an unchanged ``extern int`` is not reported as a reduction (case61).
    """
    var = snap.var_by_mangled(sym_name)
    if var is None:
        return None
    recorded = var.alignment_bits_fact
    if recorded is not None and recorded.status is FactStatus.PARTIAL:
        # A lower bound (PARTIAL: the producer could not see the declared
        # alignment): it can neither corroborate nor veto a placement, and
        # the natural alignment below would be the same lower bound again.
        return None
    if var.alignment_bits is not None:
        return var.alignment_bits
    # Only a variable spelled with an arithmetic type: the fact describes the
    # *desugared* type, so a typedef carrying its own alignment attribute
    # (``typedef int al16 __attribute__((aligned(16)))``) would read as the
    # plain int's alignment and hide a real reduction.
    if not is_arithmetic_spelling(var.type):
        return None
    # Only for a declaration a header AST parsed: there an explicit
    # ``AlignedAttr`` would have been recorded as ``alignment_bits``, so its
    # absence means none was declared. A DWARF-derived variable records no
    # ``DW_AT_alignment`` at all, so for it an ``alignas(16)`` is unknown, and
    # the natural alignment must not stand in for it.
    declared = var.declared_in_headers_fact
    if (
        declared is None
        or declared.status is not FactStatus.PRESENT
        or not declared.value
    ):
        return None
    fact = var.type_abi_fact
    if (
        fact is not None
        and fact.status is FactStatus.PRESENT
        and fact.value is not None
    ):
        return fact.value.align * 8
    return None


def declared_alignment_is_lower_bound(snap: AbiSnapshot, sym_name: str) -> bool:
    """Whether *snap* records only a lower bound for the variable's declared
    alignment (``PARTIAL``: the producer could not see the declared
    alignment), so its declared requirement is unknown rather than absent."""
    var = snap.var_by_mangled(sym_name)
    fact = var.alignment_bits_fact if var is not None else None
    return fact is not None and fact.status is FactStatus.PARTIAL


#: ``ElfSymbol.value_alignment`` is capped at the page size: a symbol there may
#: be aligned further, so the cap is no upper bound.
_PAGE_ALIGNMENT_CAP_BYTES = 4096


def placement_bits(snap: AbiSnapshot) -> dict[str, int]:
    """Each exported object's address alignment in bits, by symbol name.

    An object aligned to ``A`` sits at a multiple of ``A``, so the largest
    power of two dividing its ``st_value`` (``value_alignment``) is an upper
    bound on its alignment, whatever was declared. Unknown (0) and page-capped
    values bound nothing and are left out.
    """
    elf = snap.elf
    if elf is None:
        return {}
    return {
        sym.name: sym.value_alignment * 8
        for sym in elf.symbols
        if 0 < sym.value_alignment < _PAGE_ALIGNMENT_CAP_BYTES
    }
