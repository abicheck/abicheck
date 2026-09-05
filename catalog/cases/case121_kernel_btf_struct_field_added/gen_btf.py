#!/usr/bin/env python3
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

"""Regenerate the committed v1.btf / v2.btf fixtures for case121.

These are minimal, hand-assembled BTF blobs (the same on-disk format that
``pahole -J`` / ``bpftool btf dump`` emit and that abicheck's
``parse_btf_from_bytes`` consumes). v1 models a kernel struct with two fields;
v2 adds a third field, growing the struct — the canonical out-of-tree-module
"struct vs vmlinux BTF" ABI break.

Usage::

    python gen_btf.py        # writes v1.btf and v2.btf next to this script

A real kernel workflow would instead run::

    pahole -J vmlinux        # embeds .BTF into vmlinux
    bpftool btf dump file vmlinux format raw
"""
from __future__ import annotations

import struct
from pathlib import Path

# BTF on-disk constants (see linux/btf.h).
BTF_MAGIC = 0xEB9F
BTF_VERSION = 1
BTF_KIND_INT = 1
BTF_KIND_STRUCT = 4


def build_struct_btf(struct_name: str, n_fields: int) -> bytes:
    """Build a BTF blob: one 32-bit INT type plus a struct of ``n_fields`` ints."""
    strings = bytearray(b"\x00")
    offsets: dict[str, int] = {"": 0}

    def _str(s: str) -> int:
        if s in offsets:
            return offsets[s]
        off = len(strings)
        strings.extend(s.encode() + b"\x00")
        offsets[s] = off
        return off

    types: list[bytes] = []

    # type 1: int (4 bytes / 32 bits)
    info = (BTF_KIND_INT << 24) | 0
    types.append(struct.pack("<III", _str("int"), info, 4) + struct.pack("<I", 32))

    # type 2: the struct, n_fields members each referencing type 1 (int)
    members = b""
    for i in range(n_fields):
        members += struct.pack("<III", _str(f"f{i}"), 1, i * 32)
    info = (BTF_KIND_STRUCT << 24) | (n_fields & 0xFFFF)
    types.append(struct.pack("<III", _str(struct_name), info, n_fields * 4) + members)

    type_data = b"".join(types)
    str_data = bytes(strings)
    header = struct.pack(
        "<HBBIIIII", BTF_MAGIC, BTF_VERSION, 0, 24,
        0, len(type_data), len(type_data), len(str_data),
    )
    return header + type_data + str_data


def main() -> None:
    here = Path(__file__).parent
    (here / "v1.btf").write_bytes(build_struct_btf("task_state", n_fields=2))
    (here / "v2.btf").write_bytes(build_struct_btf("task_state", n_fields=3))
    print("wrote v1.btf (2 fields) and v2.btf (3 fields)")


if __name__ == "__main__":
    main()
