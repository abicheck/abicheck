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
    CTF_K_POINTER,
    CTF_MAGIC,
    CTF_VERSION_2,
    CTF_VERSION_3,
    _parse_types,
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


def test_truncated_type_section_propagates_to_partial(monkeypatch) -> None:
    """P2 review, fresh evidence: `_parse_types()` silently truncating a
    type record (log + return, never raise) must still mark the receipt
    `partial`, not `parsed` -- the earlier fix above only covered a
    raised-and-caught extraction-stage exception, not a truncation the type
    parser itself swallows before extraction even runs (mirrors the
    identical BTF fix). Exercises the real `parse_ctf_from_bytes` ->
    `_parse_types` wiring, with `_parse_types` itself only stubbed to
    report the same "truncated" signal its own unit tests
    (TestParseTypesTruncation in test_ctf_metadata.py) already prove it
    produces for a genuinely cut-off buffer."""
    from abicheck import ctf_metadata as ctf_mod

    real_parse_types = ctf_mod._parse_types

    def wrapped(type_data: bytes, version: int, truncated=None):
        result = real_parse_types(type_data, version)
        if truncated is not None:
            truncated.append(True)
        return result

    monkeypatch.setattr(ctf_mod, "_parse_types", wrapped)

    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.has_ctf is True
    assert meta.extraction_partial is True
    assert meta.to_dwarf_metadata().evidence_state == "partial"


def test_clean_parse_reports_parsed_and_not_partial() -> None:
    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.extraction_partial is False
    assert meta.to_dwarf_metadata().evidence_state == "parsed"


class TestParseTypesTruncationOutParam:
    """P2 review, fresh evidence: none of ``_parse_types()``'s ``break``
    sites (see ``TestParseTypesTruncation`` in ``test_ctf_metadata.py``)
    raise, so a caller only reading the returned list can't tell
    "truncated" from "genuinely nothing more to parse". The opt-in
    ``truncated`` out-param must fire for each ``break`` shape, and must
    NOT fire for a section the loop consumed in full. Split into this
    sibling file (mirroring ``test_struct_extraction_failure_propagates_
    to_partial`` above) purely to stay under ``test_ctf_metadata.py``'s own
    architecture debt-no-growth baseline.
    """

    def test_v3_truncated_header_sets_truncated(self) -> None:
        truncated: list[bool] = []
        _parse_types(b"\x00" * 8, CTF_VERSION_3, truncated)
        assert truncated == [True]

    def test_v2_truncated_header_sets_truncated(self) -> None:
        truncated: list[bool] = []
        _parse_types(b"\x00" * 4, CTF_VERSION_2, truncated)
        assert truncated == [True]

    def test_v2_truncated_size_field_sets_truncated(self) -> None:
        data = struct.pack("<I", 0) + struct.pack("<H", (CTF_K_POINTER << 11))
        truncated: list[bool] = []
        _parse_types(data, CTF_VERSION_2, truncated)
        assert truncated == [True]

    def test_v3_truncated_extra_sets_truncated(self) -> None:
        info = CTF_K_INTEGER << 24
        data = struct.pack("<III", 0, info, 4)
        truncated: list[bool] = []
        _parse_types(data, CTF_VERSION_3, truncated)
        assert truncated == [True]

    def test_empty_section_is_not_truncated(self) -> None:
        """A zero-length type section is a legitimately empty (not
        truncated) parse."""
        truncated: list[bool] = []
        _parse_types(b"", CTF_VERSION_3, truncated)
        assert truncated == []

    def test_fully_consumed_section_is_not_truncated(self) -> None:
        """Positive control: a v3 type section with no leftover bytes at
        all must not be flagged."""
        int_enc = struct.pack("<I", 32)
        info = CTF_K_INTEGER << 24
        one_type = struct.pack("<III", 0, info, 4) + int_enc
        truncated: list[bool] = []
        result = _parse_types(one_type, CTF_VERSION_3, truncated)
        assert len(result) == 2  # void + the one complete integer
        assert truncated == []
