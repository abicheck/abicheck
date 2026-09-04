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

"""P2 review finding, split out of ``test_ctf_metadata.py`` (``_extra``-style
sibling, matching e.g. ``test_analysis_assurance_depth_and_graph_overlap.
py``) purely to stay under that file's AI-readiness no-growth debt baseline.

Finding: ``parse_ctf_from_bytes`` catches a struct-extraction exception
internally and returns a partially-populated object, but the conversion to
``DwarfMetadata`` still labelled the basic channel ``parsed``. Fixed by
``CtfMetadata.extraction_partial``, set whenever any extraction stage raises,
and read by ``to_dwarf_metadata()`` to report ``partial`` instead.

Duplicates the small ``CtfBuilder`` fixture it needs rather than importing it
from the parent module -- every other ``_extra``-style sibling test file in
this suite is self-contained the same way.
"""

from __future__ import annotations

import struct

from abicheck.ctf_metadata import (
    CTF_K_INTEGER,
    CTF_MAGIC,
    CTF_VERSION_3,
    parse_ctf_from_bytes,
)


class CtfBuilder:
    """Minimal CTF v3 blob builder -- just enough for one INTEGER type."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")
        self._type_entries: list[bytes] = []
        self._str_offsets: dict[str, int] = {"": 0}

    def add_string(self, s: str) -> int:
        if s in self._str_offsets:
            return self._str_offsets[s]
        off = len(self._strings)
        self._strings.extend(s.encode("utf-8") + b"\x00")
        self._str_offsets[s] = off
        return off

    def add_type(
        self, name: str, kind: int, vlen: int, size_or_type: int, extra: bytes = b""
    ) -> int:
        name_off = self.add_string(name) if name else 0
        info = (kind << 24) | (vlen & 0xFFFF)
        entry = struct.pack("<III", name_off, info, size_or_type) + extra
        self._type_entries.append(entry)
        return len(self._type_entries)

    def build(self) -> bytes:
        type_data = b"".join(self._type_entries)
        str_data = bytes(self._strings)
        str_off = len(type_data)
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, str_off, len(str_data))
        return header + type_data + str_data


def test_struct_extraction_failure_propagates_to_partial(monkeypatch) -> None:
    """P2 review: parse_ctf_from_bytes catches a struct-extraction exception
    internally and returns a partially-populated object, but must not
    silently claim "parsed" basic layout evidence for it (mirrors the
    identical BTF fix)."""
    from abicheck import ctf_metadata as ctf_mod

    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    def boom(*_a, **_k):
        raise RuntimeError("bad struct record")

    monkeypatch.setattr(ctf_mod, "_extract_structs", boom)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.has_ctf is True
    assert meta.extraction_partial is True

    dwarf = meta.to_dwarf_metadata()
    assert dwarf.has_dwarf is True
    assert dwarf.evidence_state == "partial"


def test_clean_parse_reports_parsed_and_not_partial() -> None:
    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.extraction_partial is False
    assert meta.to_dwarf_metadata().evidence_state == "parsed"
