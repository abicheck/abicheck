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

"""``open_regular_file_for_format_sniff()``/``sniff_bundle_archive_format()``
tests for a leading zstd skippable frame, split out of
``tests/test_bundle_archive.py`` purely to stay under that file's
ADR-061 no-growth line baseline (it's already at exactly 1200 lines)
rather than growing it further.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


def _skippable(user_data: bytes) -> bytes:
    return struct.pack("<I", 0x184D2A50) + struct.pack("<I", len(user_data)) + user_data


class TestSniffRecognizesALeadingSkippableFrame:
    """A zstd-compressed ``BundleFacts`` JSON envelope may legitimately
    start with a standard skippable frame (e.g. metadata) ahead of its
    real magic -- the archive-format sniff only ever read a bare 4-byte
    prefix, so it could not recognize the real zstd magic past one,
    falling through to the ZIP-tail EOCD heuristic unnecessarily. Worse,
    a *trailing* skippable frame (zstd permits skippable frames anywhere
    in a stream, including after the real data frame) whose own user
    data is crafted to end in a structurally-plausible empty-ZIP EOCD
    lands exactly at the file's true end -- letting a real, independently
    decodable zstd JSON blob misclassify as ``"archive"`` (Codex review,
    fresh evidence, follow-up to the earlier leading-skippable-frame fix
    for ``read_snapshot_bytes()``)."""

    def _leading_skippable_zstd_json(self, payload: bytes) -> bytes:
        zstandard = pytest.importorskip("zstandard")
        cctx = zstandard.ZstdCompressor(write_content_size=True)
        real_frame = cctx.compress(payload)
        return _skippable(b"some-metadata") + real_frame

    def _leading_and_trailing_skippable_zstd_json_with_eocd(self, payload: bytes) -> bytes:
        """A real, fully valid, independently-decodable zstd stream:
        [leading skippable frame][real data frame][trailing skippable
        frame whose user data is a minimal empty-ZIP EOCD, landing
        exactly at the file's own end]. Premise: this is exactly the
        crafted-EOCD construction the review comment describes, built
        from a genuinely decodable file rather than a synthetic one."""
        zstandard = pytest.importorskip("zstandard")
        cctx = zstandard.ZstdCompressor(write_content_size=True)
        real_frame = cctx.compress(payload)
        leading = _skippable(b"meta")
        eocd = b"PK\x05\x06" + b"\x00" * 18  # minimal empty-ZIP EOCD, comment_len=0
        trailing = _skippable(eocd)
        data = leading + real_frame + trailing
        assert b"PK\x05\x06" in data  # premise: the coincidental match exists
        assert data.endswith(eocd)  # premise: it lands exactly at file end
        return data

    def test_sniff_recognizes_it_as_json_not_archive(self, tmp_path: Path) -> None:
        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        path = tmp_path / "envelope.json.zst"
        path.write_bytes(self._leading_skippable_zstd_json(b'{"library": "x", "version": "1"}'))

        fp, fmt = open_regular_file_for_format_sniff(path)
        if fp is not None:
            fp.close()
        assert fmt == "json"

    def test_a_crafted_trailing_eocd_still_does_not_fool_the_sniff(self, tmp_path: Path) -> None:
        """The real regression: without the fix, this real, decodable
        zstd stream's leading skippable frame prevents direct magic
        recognition, so the sniff falls through to the ZIP-tail
        heuristic -- which the crafted trailing skippable frame's own
        EOCD-shaped tail then satisfies, misclassifying a genuinely
        valid JSON envelope as an archive."""
        from abicheck.snapshot_io import read_snapshot_bytes
        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        payload = b'{"library": "x", "version": "1"}'
        path = tmp_path / "envelope-with-crafted-eocd.json.zst"
        path.write_bytes(self._leading_and_trailing_skippable_zstd_json_with_eocd(payload))

        # Premise: it's a real, fully decodable zstd stream.
        assert read_snapshot_bytes(path) == payload

        fp, fmt = open_regular_file_for_format_sniff(path)
        if fp is not None:
            fp.close()
        assert fmt == "json"

    def test_sniff_bundle_archive_format_matches(self, tmp_path: Path) -> None:
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        path = tmp_path / "envelope.json.zst"
        path.write_bytes(self._leading_skippable_zstd_json(b'{"library": "x", "version": "1"}'))
        assert sniff_bundle_archive_format(path) == "json"

    def test_sniff_and_load_bundle_facts_treat_it_as_json(self, tmp_path: Path) -> None:
        """End-to-end pin through the public loader, mirroring the
        sibling gzip-FEXTRA-EOCD test's own coverage shape."""
        import json

        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.serialization import bundle_facts_to_dict, load_bundle_facts
        from abicheck.storage.bundle_archive import (
            open_regular_file_for_format_sniff,
            sniff_bundle_archive_format,
        )

        zstandard = pytest.importorskip("zstandard")
        facts = capture_bundle_facts({})
        payload = json.dumps(bundle_facts_to_dict(facts), indent=2).encode("utf-8")
        cctx = zstandard.ZstdCompressor(write_content_size=True)
        blob = _skippable(b"meta") + cctx.compress(payload)

        path = tmp_path / "envelope-facts.json.zst"
        path.write_bytes(blob)

        fp, fmt = open_regular_file_for_format_sniff(path)
        if fp is not None:
            fp.close()
        assert fmt == "json"
        assert sniff_bundle_archive_format(path) == "json"
        loaded = load_bundle_facts(path)  # format="auto" default
        assert loaded.per_library_snapshots == facts.per_library_snapshots
