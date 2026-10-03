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

"""Whether a shared object needs a slot in the static TLS block.

``ElfMetadata.has_static_tls`` answers "can this library still be reliably
``dlopen()``ed after startup?". ``DF_STATIC_TLS`` in ``DT_FLAGS`` is the
linker's *summary* of that, and not every linker writes it: GNU ld sets it
for an initial-exec access on x86-64, but not on AArch64, where the same
``-ftls-model=initial-exec`` build carries the requirement only as an
``R_AARCH64_TLS_TPREL`` dynamic relocation (measured with
``aarch64-linux-gnu-gcc`` 13 / binutils 2.42: v1 global-dynamic has an
``R_AARCH64_TLSDESC``, v2 initial-exec an ``R_AARCH64_TLS_TPREL``, and
neither has ``DT_FLAGS``).

The relocation is the cause, and the flag only reflects it. The dynamic
linker has to resolve a TP-relative offset for it at load time, which is
only possible for a module whose TLS block lives in the static TLS area. So
this module reads the relocation itself: one per architecture, numbers from
glibc's ``elf.h``. Global-/local-dynamic and TLS-descriptor relocations
(``*_DTPMOD*``, ``*_DTPOFF*``, ``*_TLSDESC``) are deliberately absent: they
are what a ``dlopen``-safe module uses.

Only ``ET_DYN`` objects are read, because a relocatable ``.o`` carries
link-time relocations (``R_X86_64_GOTTPOFF`` and friends) that say nothing
yet about the final module.
"""

from __future__ import annotations

from typing import Any

__all__ = ["STATIC_TLS_RELOCATION_TYPES", "has_static_tls_relocation"]

#: ``e_machine`` (pyelftools spelling) -> dynamic relocation types that
#: resolve a static-TLS (TP-relative) offset. Values from glibc ``elf.h``.
STATIC_TLS_RELOCATION_TYPES: dict[str, frozenset[int]] = {
    "EM_X86_64": frozenset({18, 23}),  # R_X86_64_TPOFF64, R_X86_64_TPOFF32
    "EM_386": frozenset({14, 37}),  # R_386_TLS_TPOFF, R_386_TLS_TPOFF32
    "EM_AARCH64": frozenset({1030}),  # R_AARCH64_TLS_TPREL
    "EM_ARM": frozenset({19}),  # R_ARM_TLS_TPOFF32
    "EM_PPC64": frozenset({73}),  # R_PPC64_TPREL64
    "EM_PPC": frozenset({73}),  # R_PPC_TPREL32
    "EM_S390": frozenset({56}),  # R_390_TLS_TPOFF
    "EM_RISCV": frozenset({10, 11}),  # R_RISCV_TLS_TPREL32, R_RISCV_TLS_TPREL64
    "EM_LOONGARCH": frozenset({10, 11}),  # R_LARCH_TLS_TPREL32, R_LARCH_TLS_TPREL64
}


def has_static_tls_relocation(elf: Any) -> bool:
    """True when *elf* (a pyelftools ``ELFFile``) carries a static-TLS relocation.

    Unknown machines answer ``False``: absence of evidence, which leaves the
    ``DF_STATIC_TLS`` reading as the only signal, exactly as before.
    """
    if elf.header["e_type"] != "ET_DYN":
        return False
    wanted = STATIC_TLS_RELOCATION_TYPES.get(elf.header["e_machine"])
    if not wanted:
        return False
    for section in elf.iter_sections():
        if section.header["sh_type"] not in ("SHT_RELA", "SHT_REL"):
            continue
        # Only dynamic relocations: those whose symbol table is .dynsym.
        # A static-link leftover against .symtab is not resolved at load time.
        link = section.header["sh_link"]
        if link and elf.get_section(link).header["sh_type"] != "SHT_DYNSYM":
            continue
        for reloc in section.iter_relocations():
            if reloc["r_info_type"] in wanted:
                return True
    return False
