"""The declared alignment of an exported variable, for corroborating an
address-derived alignment change (``diff_platform_elf_symbols``)."""

from __future__ import annotations

from ..model import AbiSnapshot
from ..model.availability import FactStatus


def declared_alignment_bits(snap: AbiSnapshot, sym_name: str) -> int | None:
    """Return the DWARF/header-declared alignment (bits) for a variable, if known.

    An explicit ``alignment_bits`` wins; otherwise the compiler-derived natural
    alignment of a fundamental-typed variable (``type_abi_fact``, PRESENT only)
    corroborates -- the clang header backend records no computed alignment for
    a plain variable but does record this fact, so address-placement noise on
    an unchanged ``extern int`` is not reported as a reduction (case61).
    """
    var = snap.var_by_mangled(sym_name)
    if var is None:
        return None
    if var.alignment_bits is not None:
        return var.alignment_bits
    fact = var.type_abi_fact
    if (
        fact is not None
        and fact.status is FactStatus.PRESENT
        and fact.value is not None
    ):
        return fact.value.align * 8
    return None
