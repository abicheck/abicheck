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

"""Leading zstd skippable-frame detection tests, split out of
``tests/test_snapshot_compression.py`` purely to stay under that file's
ADR-061 no-growth line baseline rather than growing it further -- see
that file's own module docstring for the sibling coverage of everything
else in ``abicheck/snapshot_io.py``.
"""

from __future__ import annotations

import json
import struct

import pytest

from abicheck.snapshot_io import (
    SnapshotCompression,
    detect_compression_from_bytes,
    read_snapshot_bytes,
)


def test_leading_skippable_frame_recognized_as_zstd(tmp_path):
    """An externally produced ``.json.zst`` may legitimately start with a
    standard zstd skippable frame (e.g. metadata) ahead of the real data
    frame -- ``validate_zstd_frame_completeness`` already accepts and
    decodes this correctly once reached, but compression *detection*
    classified the leading skippable magic as uncompressed, so
    ``read_snapshot_bytes()`` never got that far: it raised a suffix/magic
    mismatch instead (Codex review, fresh evidence)."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1"}).encode()
    cctx = zstandard.ZstdCompressor(write_content_size=True)
    real_frame = cctx.compress(payload)

    skippable_magic = struct.pack("<I", 0x184D2A50)
    user_data = b"some-metadata"
    skippable_frame = skippable_magic + struct.pack("<I", len(user_data)) + user_data

    p = tmp_path / "leading_skippable.abicheck.json.zst"
    p.write_bytes(skippable_frame + real_frame)

    assert read_snapshot_bytes(p) == payload
    assert detect_compression_from_bytes(skippable_frame + real_frame) is SnapshotCompression.ZSTD


def test_leading_skippable_frame_too_short_to_classify_stays_none(tmp_path):
    """A bare 4-byte skippable-frame magic prefix (no room for the
    Frame_Size field, let alone the real frame after it) cannot be safely
    skipped -- confirms the detection fix degrades to the pre-existing
    behavior rather than guessing when too little of the buffer is
    available, as every 4-byte-prefix-only caller (e.g.
    ``detect_snapshot_compression``) still is."""
    prefix = struct.pack("<I", 0x184D2A50)  # only 4 bytes -- no Frame_Size
    assert detect_compression_from_bytes(prefix) is SnapshotCompression.NONE
