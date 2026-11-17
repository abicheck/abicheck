"""What each producer compiler writes into DWARF, as a table abiforge reads.

A kind's ``observable_at`` says which evidence *can* show a fact in general.
Some DWARF alternatives depend on the producer: a compiler that does not write
the attribute cannot show the fact at DWARF, whatever the edit. The table here
records those rows, so :mod:`abiforge.model.observable` derives a producer's
minimal evidence from what that producer emits, not from the catalog alone.

Every row was checked against the toolchains abiforge installs (gcc 13.3,
clang 18.1) by ``tests/test_abiforge_producer_capabilities.py``, which reads the
attribute back with ``readelf``/``llvm-dwarfdump`` as the oracle. A row with no
verified producer is absent, never guessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from abiforge.run.toolchains import Toolchain

#: ``DW_AT_calling_convention`` carried on a subprogram or subroutine type for a
#: non-default x86 convention (``ms_abi``, ``regcall``, ``vectorcall``).
DW_AT_CALLING_CONVENTION_NON_DEFAULT = "dw_at_calling_convention.non_default"

#: capability -> producer family -> does that producer write it (verified rows).
CAPABILITIES: dict[str, dict[str, bool]] = {
    DW_AT_CALLING_CONVENTION_NON_DEFAULT: {
        "clang": True,  # clang 18: ms_abi -> DW_CC_LLVM_Win64 on the subprogram
        "gcc": False,  # gcc 13: ms_abi leaves no DW_AT_calling_convention at all
    },
}

#: A kind whose ``{B, D}`` alternative needs a producer capability: the
#: alternative is observable only where the capability holds.
KIND_DWARF_WITNESS: dict[str, str] = {
    "CALLING_CONVENTION_CHANGED": DW_AT_CALLING_CONVENTION_NON_DEFAULT,
}


def producer_writes(capability: str, producer: Toolchain) -> bool:
    """Whether *producer* writes *capability* into its DWARF (False when unlisted)."""
    return CAPABILITIES.get(capability, {}).get(producer.family, False)


def dwarf_witness_present(kind: str, producer: Toolchain) -> bool:
    """Whether *producer* can show *kind* at DWARF at all (True when no requirement)."""
    capability = KIND_DWARF_WITNESS.get(kind)
    return capability is None or producer_writes(capability, producer)
