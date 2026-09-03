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

"""`BundleArchiveReader.read_blob`'s stored-vs-decoded size-cap decoupling
(G40, Codex review) -- split out from ``test_bundle_archive.py`` (already
at its ADR-061 test-file line cap) rather than grown there."""

from __future__ import annotations

import json
import os
import struct
import zipfile
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.bundle_archive import (
    MANIFEST_MEMBER,
    BundleArchiveReader,
    content_hash,
)


class TestBundleArchiveReadBlobStoredCapIndependentOfDecodedBudget:
    """Codex review: a small *max_decoded_bytes* (e.g. a low remaining
    aggregate bundle-read budget) must not also shrink how much still-
    *compressed* data `read_blob` reads before decompression starts. A
    valid blob can carry several MiB of leading zstd skippable-frame
    metadata ahead of a real frame decoding to a handful of bytes -- the
    stored-read cap must be the independent, fixed
    `DEFAULT_MAX_STORED_BLOB_BYTES`, not ``max_decoded_bytes + slack``."""

    @staticmethod
    def _write_archive_with_skippable_prefix(
        path: Path, *, skippable_size: int, payload: bytes
    ) -> str:
        """One real archive whose sole blob is a zstd skippable frame of
        *skippable_size* bytes followed by a real frame decoding to
        *payload* -- bypasses `put_blob` (never emits a skippable frame)."""
        import zstandard

        h = content_hash(payload)
        skippable_frame = struct.pack("<II", 0x184D2A50, skippable_size) + (
            b"\x00" * skippable_size
        )
        real_frame = zstandard.ZstdCompressor().compress(payload)
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(
                f"blobs/{h}.json.zst",
                skippable_frame + real_frame,
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
        return h

    @pytest.mark.parametrize(
        ("payload", "max_decoded_bytes"),
        [
            pytest.param(b"{}", 100, id="reported-repro"),
            pytest.param(b"x", 1, id="extreme-1-byte-budget"),
        ],
    )
    def test_large_leading_skippable_frame_with_a_tiny_decode_budget_still_succeeds(
        self, tmp_path: Path, payload: bytes, max_decoded_bytes: int
    ) -> None:
        """A 2 MiB leading skippable frame ahead of a tiny real frame, read
        with a tiny decode budget -- the pre-fix stored-read cap
        (`max_decoded_bytes` + a fixed 1 MiB slack) rejected this before
        decompression ever ran, even though the decoded payload is tiny."""
        path = tmp_path / "bundle.archive.zip"
        h = self._write_archive_with_skippable_prefix(
            path, skippable_size=2 * 1024 * 1024, payload=payload
        )
        with BundleArchiveReader.open(path) as reader:
            assert reader.read_blob(h, max_decoded_bytes=max_decoded_bytes) == payload

    def test_decoded_size_check_still_enforced_regardless_of_stored_cap(
        self, tmp_path: Path
    ) -> None:
        """The independent stored-size ceiling must not weaken the real
        bomb defense: a payload genuinely exceeding *max_decoded_bytes* is
        still rejected, even with no skippable-frame padding at all."""
        path = tmp_path / "bundle.archive.zip"
        payload = os.urandom(500)
        h = self._write_archive_with_skippable_prefix(path, skippable_size=0, payload=payload)
        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=100)

    def test_a_genuinely_oversized_stored_member_is_still_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The independent ceiling is still a real bound: a stored member
        past `DEFAULT_MAX_STORED_BLOB_BYTES` is rejected regardless of how
        generous *max_decoded_bytes* is. Monkeypatched down from the real
        2 GiB default so this test needn't allocate gigabytes."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_STORED_BLOB_BYTES", 4096)
        path = tmp_path / "bundle.archive.zip"
        h = self._write_archive_with_skippable_prefix(path, skippable_size=8192, payload=b"{}")
        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=100)
