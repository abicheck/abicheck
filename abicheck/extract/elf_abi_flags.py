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
"""Per-architecture ELF ``e_flags`` ABI decoding (G23-A3).

Moved out of ``elf_metadata.py`` (over its no-growth baseline). The decoder
reads only the ``e_flags`` word and the machine name, so it is a pure
extraction helper with no parser state.
"""

from __future__ import annotations

# Only the bits that select an *incompatible* calling convention are decoded;
# unknown architectures fall back to reporting the raw e_flags value.
_EF_ARM_ABI_FLOAT_HARD = 0x00000400
_EF_ARM_ABI_FLOAT_SOFT = 0x00000200
_EF_ARM_EABI_MASK = 0xFF000000
_EF_RISCV_FLOAT_ABI_MASK = 0x0006  # 0=soft, 2=single, 4=double, 6=quad
# EF_RISCV_RVE (0x8) reduces the integer register file 32→16 and changes the
# calling convention, so it IS ABI-selecting. EF_RISCV_RVC (0x1, compressed
# instructions) is an ISA-encoding choice with no calling-convention effect, so
# it is deliberately NOT decoded — toggling it must not report an ABI break.
_EF_RISCV_RVE = 0x0008
_EF_MIPS_ABI_MASK = 0x0000F000
_RISCV_FLOAT_ABI_NAMES = {
    0x0: "float-soft",
    0x2: "float-single",
    0x4: "float-double",
    0x6: "float-quad",
}


def _decode_abi_flags(machine: str, e_flags: int) -> frozenset[str]:
    """Decode the ABI-selecting bits of ``e_flags`` for architectures we know.

    Returns a set of human-readable tokens whose *change* between two versions
    signals a calling-convention-incompatible rebuild (float ABI, EABI version).
    Unknown architectures return an empty set; the raw ``e_flags`` value is
    diffed separately so drift is still caught, just without a decoded label.
    """
    tokens: set[str] = set()
    if machine in ("EM_ARM",):
        if e_flags & _EF_ARM_ABI_FLOAT_HARD:
            tokens.add("float-hard")
        elif e_flags & _EF_ARM_ABI_FLOAT_SOFT:
            tokens.add("float-soft")
        eabi = (e_flags & _EF_ARM_EABI_MASK) >> 24
        if eabi:
            tokens.add(f"eabi{eabi}")
    elif machine in ("EM_RISCV",):
        tokens.add(
            _RISCV_FLOAT_ABI_NAMES.get(
                e_flags & _EF_RISCV_FLOAT_ABI_MASK, "float-unknown"
            )
        )
        if e_flags & _EF_RISCV_RVE:
            tokens.add("rve")
    elif machine in ("EM_MIPS",):
        abi = e_flags & _EF_MIPS_ABI_MASK
        if abi:
            tokens.add(f"mips-abi-{abi:#x}")
    return frozenset(tokens)
