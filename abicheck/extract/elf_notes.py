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

"""Decode the ELF notes ``parse_elf_metadata`` reads: GNU-property
control-flow-protection features (G23-A2) and the NT_GNU_ABI_TAG kernel floor.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Any

# ── GNU-property control-flow-protection decoding (G23-A2) ──────────────────
# pyelftools reports the note type as the *string* "NT_GNU_PROPERTY_TYPE_0"
# (its known-type name), not the raw numeric 5 — accept both forms.
_NT_GNU_PROPERTY_TYPE_0 = 5
_NT_GNU_PROPERTY_TYPE_0_NAMES = frozenset({5, "NT_GNU_PROPERTY_TYPE_0"})
_GNU_PROPERTY_X86_FEATURE_1_AND = 0xC0000002
_GNU_PROPERTY_X86_FEATURE_1_IBT = 0x1
_GNU_PROPERTY_X86_FEATURE_1_SHSTK = 0x2
_GNU_PROPERTY_AARCH64_FEATURE_1_AND = 0xC0000000
_GNU_PROPERTY_AARCH64_FEATURE_1_BTI = 0x1
_GNU_PROPERTY_AARCH64_FEATURE_1_PAC = 0x2
# Required micro-architecture level (glibc-hwcaps builds, -march=x86-64-vN).
# A raised level means older CPUs can no longer run the library at all.
_GNU_PROPERTY_X86_ISA_1_NEEDED = 0xC0008002
_X86_ISA_1_LEVEL_TOKENS: tuple[tuple[int, str], ...] = (
    (0x1, "x86-64-baseline"),
    (0x2, "x86-64-v2"),
    (0x4, "x86-64-v3"),
    (0x8, "x86-64-v4"),
)


def decode_gnu_property_desc(
    desc: bytes, little_endian: bool, align: int = 8
) -> frozenset[str]:
    """Parse a NT_GNU_PROPERTY_TYPE_0 note description into feature tokens.

    The description is a sequence of properties, each laid out as
    ``pr_type (u32) | pr_datasz (u32) | pr_data[pr_datasz] | pad``. Each
    property is padded up to *align* bytes — 8 for ELFCLASS64, **4** for
    ELFCLASS32 — so a wrong alignment skips or misreads later properties. Only
    the x86 and AArch64 control-flow-protection AND-features are decoded.
    """
    endian = "<" if little_endian else ">"
    tokens: set[str] = set()
    off = 0
    n = len(desc)
    while off + 8 <= n:
        pr_type, pr_datasz = struct.unpack_from(endian + "II", desc, off)
        off += 8
        if off + pr_datasz > n:
            break
        data = desc[off : off + pr_datasz]
        if pr_type == _GNU_PROPERTY_X86_FEATURE_1_AND and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            if bits & _GNU_PROPERTY_X86_FEATURE_1_IBT:
                tokens.add("IBT")
            if bits & _GNU_PROPERTY_X86_FEATURE_1_SHSTK:
                tokens.add("SHSTK")
        elif pr_type == _GNU_PROPERTY_AARCH64_FEATURE_1_AND and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            if bits & _GNU_PROPERTY_AARCH64_FEATURE_1_BTI:
                tokens.add("BTI")
            if bits & _GNU_PROPERTY_AARCH64_FEATURE_1_PAC:
                tokens.add("PAC")
        elif pr_type == _GNU_PROPERTY_X86_ISA_1_NEEDED and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            for bit, token in _X86_ISA_1_LEVEL_TOKENS:
                if bits & bit:
                    tokens.add(token)
        # Advance past pr_data, padded up to the class alignment.
        off += (pr_datasz + align - 1) & ~(align - 1)
    return frozenset(tokens)


# NT_GNU_ABI_TAG (n_type 1, name "GNU"): 4 words — OS id (0 = Linux) followed
# by the minimum required kernel version (major, minor, subminor).
NT_GNU_ABI_TAG_NAMES = frozenset({1, "NT_GNU_ABI_TAG"})
_ELF_OSABI_TAG_LINUX = 0


def decode_abi_tag_desc(desc: bytes, little_endian: bool) -> str:
    """Decode an NT_GNU_ABI_TAG description into a kernel-floor string.

    Returns ``"major.minor.subminor"`` for a Linux tag, ``""`` for a non-Linux
    OS id or a malformed description.
    """
    if len(desc) < 16:
        return ""
    endian = "<" if little_endian else ">"
    os_id, major, minor, subminor = struct.unpack_from(endian + "IIII", desc, 0)
    if os_id != _ELF_OSABI_TAG_LINUX:
        return ""
    return f"{major}.{minor}.{subminor}"


def iter_gnu_property_descs(elf: Any) -> Iterator[bytes]:
    """Yield NT_GNU_PROPERTY_TYPE_0 description blobs from section or segment."""
    section = elf.get_section_by_name(".note.gnu.property")
    if section is not None and hasattr(section, "iter_notes"):
        found = False
        for note in section.iter_notes():
            found = True
            if note.get("n_type") not in _NT_GNU_PROPERTY_TYPE_0_NAMES:
                continue
            desc = note.get("n_descdata") or note.get("n_desc")
            if isinstance(desc, str):
                desc = desc.encode("latin-1", "replace")
            if isinstance(desc, (bytes, bytearray)):
                yield bytes(desc)
        if found:
            return
    # Fallback: section absent / empty (stripped) — parse the PT_GNU_PROPERTY
    # program segment's raw note bytes directly.
    try:
        segments = list(elf.iter_segments())
    except Exception:  # noqa: BLE001
        return
    for seg in segments:
        if getattr(seg.header, "p_type", None) != "PT_GNU_PROPERTY":
            continue
        yield from _parse_raw_notes(seg.data(), elf.little_endian)


def _parse_raw_notes(data: bytes, little_endian: bool) -> Iterator[bytes]:
    """Parse ELF notes from raw bytes, yielding GNU-property description blobs.

    The note wrapper (namesz | descsz | n_type | name | desc) uses 4-byte
    padding regardless of ELF class; only the property array *inside* the
    description follows the class alignment (handled by the desc decoder).
    """
    endian = "<" if little_endian else ">"
    off = 0
    n = len(data)
    while off + 12 <= n:
        namesz, descsz, n_type = struct.unpack_from(endian + "III", data, off)
        off += 12
        name = data[off : off + namesz]
        off += (namesz + 3) & ~3
        desc = data[off : off + descsz]
        off += (descsz + 3) & ~3
        if n_type in _NT_GNU_PROPERTY_TYPE_0_NAMES and name.rstrip(b"\x00") == b"GNU":
            yield desc
