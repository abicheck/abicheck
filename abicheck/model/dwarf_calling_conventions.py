# SPDX-License-Identifier: Apache-2.0
"""``DW_AT_calling_convention`` codes and their names (DWARF 5 + vendor extensions).

Owned here, not in ``dwarf_advanced``: the calling-convention extractor reads
the codes, and the names are what its change reports print.
"""

from __future__ import annotations

import re

#: ``DW_AT_calling_convention`` value -> name. The LLVM entries are what clang 18
#: writes for x86-64 conventions, read back with ``llvm-dwarfdump`` beside the
#: numeric code (tests/test_abiforge_producer_capabilities.py).
CC_NAMES: dict[int, str] = {
    0x01: "normal",
    0x02: "program",
    0x03: "nocall",
    0x04: "pass_by_reference",  # DWARF 5
    0x05: "pass_by_value",  # DWARF 5
    0x40: "GNU_renesas_sh",
    0x41: "GNU_borland_fastcall_i386",
    0x80: "GNU_push_call_stub",  # GCC internal
    0x81: "GNU_push_arg",  # GCC internal
    0xB0: "BORLAND_safecall",
    0xB1: "BORLAND_stdcall",
    0xB2: "BORLAND_pascal",
    0xB3: "BORLAND_msfastcall",
    0xB4: "BORLAND_msreturn",
    0xB5: "BORLAND_thiscall",
    0xB6: "BORLAND_fastcall",
    0xB9: "LLVM_PreserveMost",
    # clang 18 writes these for x86-64 non-default conventions (llvm-dwarfdump
    # names DW_CC_LLVM_vectorcall / _Win64 / _X86RegCall on the same codes).
    0xC0: "LLVM_vectorcall",
    0xC1: "LLVM_Win64",  # ms_abi
    0xCB: "LLVM_X86RegCall",  # regcall
}


def named_convention(name: str) -> str:
    """A stored convention name, with a legacy ``unknown(0x..)`` code mapped to its name.

    A snapshot written before a code was named stores ``unknown(0xc1)``; the same
    function read now says ``LLVM_Win64``. Both must compare equal.
    """
    m = re.fullmatch(r"unknown\(0x([0-9a-fA-F]+)\)", name)
    if m is None:
        return name
    return CC_NAMES.get(int(m.group(1), 16), name)
