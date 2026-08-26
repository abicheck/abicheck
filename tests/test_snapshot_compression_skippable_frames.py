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
    bounded_decoded_prefix,
    detect_compression_from_bytes,
    detect_snapshot_compression,
    read_snapshot_bytes,
    read_snapshot_storage_info,
)
from abicheck.storage.zstd_frame_guard import skip_leading_skippable_frames


def _leading_skippable_zstd_bytes(payload: bytes, zstandard, *, user_data: bytes = b"some-metadata") -> bytes:
    cctx = zstandard.ZstdCompressor(write_content_size=True)
    real_frame = cctx.compress(payload)
    skippable_magic = struct.pack("<I", 0x184D2A50)
    skippable_frame = skippable_magic + struct.pack("<I", len(user_data)) + user_data
    return skippable_frame + real_frame


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
    skipped -- confirms `detect_compression_from_bytes()` itself degrades
    to the pre-existing behavior rather than guessing when too little of
    the buffer is available. `detect_snapshot_compression()` and its
    siblings now escalate their own on-disk read when ambiguous (see
    `test_public_probe_call_sites_see_past_a_leading_skippable_frame`
    below) -- this test is scoped to the pure byte-prefix classifier,
    which has no file to read further from."""
    prefix = struct.pack("<I", 0x184D2A50)  # only 4 bytes -- no Frame_Size
    assert detect_compression_from_bytes(prefix) is SnapshotCompression.NONE


def test_public_probe_call_sites_see_past_a_leading_skippable_frame(tmp_path):
    """`read_snapshot_bytes()`'s own internal classification call was
    fixed to pass its full buffer, but the *other* public probes --
    `detect_snapshot_compression()`, `read_snapshot_storage_info()`, and
    `bounded_decoded_prefix()` -- each still only read a bare 4-byte
    prefix from disk, so none of them could see past a leading skippable
    frame either: `detect_snapshot_compression()`/`read_snapshot_storage_
    info()` reported `NONE` (uncompressed) for a real zstd file, and
    `bounded_decoded_prefix()` returned the still-compressed raw bytes
    as though they were the decoded content (Codex review, fresh
    evidence, follow-up to the `read_snapshot_bytes()` fix)."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1"}).encode()
    blob = _leading_skippable_zstd_bytes(payload, zstandard)
    p = tmp_path / "leading_skippable.abicheck.json.zst"
    p.write_bytes(blob)

    assert detect_snapshot_compression(p) is SnapshotCompression.ZSTD
    assert read_snapshot_storage_info(p).compression is SnapshotCompression.ZSTD
    assert bounded_decoded_prefix(p, n=64) == payload


def test_bounded_prefix_escalation_is_skipped_for_ordinary_files(tmp_path, monkeypatch):
    """The escalated read is gated on `starts_with_skippable_frame_magic`
    so the overwhelmingly common case (no leading skippable frame at all)
    never pays for it -- confirmed by making the escalation helper raise
    if it's ever reached for a plain gzip/zstd/plain file."""
    import abicheck.snapshot_io as snapshot_io_module

    def _must_not_be_called(*_a, **_kw):
        raise AssertionError("escalation helper called for a non-ambiguous prefix")

    monkeypatch.setattr(
        snapshot_io_module, "read_past_leading_skippable_frames", _must_not_be_called
    )

    plain = tmp_path / "plain.abicheck.json"
    plain.write_bytes(json.dumps({"library": "x", "version": "1"}).encode())
    assert detect_snapshot_compression(plain) is SnapshotCompression.NONE
    assert read_snapshot_storage_info(plain).compression is SnapshotCompression.NONE
    assert bounded_decoded_prefix(plain) is not None


def test_read_past_leading_skippable_frames_stays_linear(tmp_path):
    """`skip_leading_skippable_frames()` (the new function added earlier
    in this PR to fix `read_snapshot_bytes()`'s own leading-skippable-
    frame detection) reintroduced the exact quadratic-slicing bug
    `validate_zstd_frame_completeness` already had to fix in an earlier
    round: a bare `remaining = remaining[total:]` on `bytes` copies the
    entire unread tail on every iteration, making the walk quadratic in
    stored size. Confirmed empirically before the cursor-based fix: ~11s
    for 200,000 zero-length skippable frames (~1.6 MiB) -- and
    `read_snapshot_bytes()` now passes its whole buffer through this
    same helper, so this is a real, newly-reachable DoS vector, not a
    hypothetical one (Codex review, fresh evidence). Mirrors
    `tests/test_bundle_archive_cd_guard.py`'s identical historical
    coverage for `validate_zstd_frame_completeness`'s own instance of
    this bug."""
    import time

    magic = struct.pack("<I", 0x184D2A50)
    one_frame = magic + struct.pack("<I", 0)
    n_frames = 200_000
    data = one_frame * n_frames

    t0 = time.monotonic()
    result = skip_leading_skippable_frames(data)
    elapsed = time.monotonic() - t0

    assert result == b""
    assert elapsed < 5.0, f"expected near-linear walk, took {elapsed:.2f}s for {n_frames} frames"


def test_read_snapshot_bytes_cap_selection_sees_past_leading_skippable_frame(tmp_path):
    """`read_snapshot_bytes()`'s own cap-selection probe (the code just
    above the decisive, full-buffer `compression = detect_compression_
    from_bytes(raw)` call) read only a bare 4-byte prefix to decide
    whether `max_decoded_bytes` (the decoded-size cap) or
    `_max_stored_bytes()` (the independent, much larger stored-size cap)
    applies -- so a skippable-frame-prefixed zstd file was misclassified
    as uncompressed *for cap-selection purposes* and checked against the
    stored-file cap instead, even though the later decisive classification
    correctly sees it as zstd (Codex review, fresh evidence, third-order
    follow-up to the leading-skippable-frame fixes above). Matches the
    finding's own repro shape: a 200-byte skippable frame (192 bytes of
    user data) ahead of a real zstd frame decoding to ``{}`` (2 bytes),
    read with a 100-byte *decoded*-size limit that only the wrong
    (stored-size) cap would reject."""
    zstandard = pytest.importorskip("zstandard")

    user_data = b"\x00" * 192
    blob = _leading_skippable_zstd_bytes(b"{}", zstandard, user_data=user_data)
    assert len(blob) > 100  # stored size exceeds the decoded-size limit below

    p = tmp_path / "leading_skippable_small_payload.abicheck.json.zst"
    p.write_bytes(blob)

    assert read_snapshot_bytes(p, max_decoded_bytes=100) == b"{}"


def test_read_snapshot_bytes_cap_selection_survives_escalation_ceiling(tmp_path):
    """Fifth-order follow-up (Codex review, fresh evidence): the escalated
    probe `_read_past_leading_skippable_frames()` uses to see past a
    leading skippable frame is deliberately bounded
    (`_BOUNDED_PREFIX_MAX_RAW_BYTES`, 1 MiB) so classifying an adversarial
    file with an enormous/unbounded run of leading skippable frames can't
    force an unbounded read. When *legitimate* leading skippable-frame
    metadata exceeds that bound, the escalation can hit its ceiling
    without ever reaching the real data frame's own magic -- and cap
    selection previously fell all the way back to treating the file as
    uncompressed (`compression_hint is NONE`), applying the small decoded-
    size cap to the file's real, much larger stored size. Matches the
    finding's own repro: a 2 MiB skippable frame (past the 1 MiB probe
    ceiling) ahead of a real zstd frame decoding to ``{}`` (2 bytes), read
    with a 100-byte *decoded*-size limit that only the wrong (stored-size)
    cap would reject. The cheap, no-I/O leading-magic check alone already
    proves the file is zstd-family, independent of whether the bounded
    escalation manages to resolve the exact frame structure."""
    zstandard = pytest.importorskip("zstandard")

    # User data comfortably past the 1 MiB escalation ceiling, so the
    # escalated read exhausts its cap before finding the real zstd magic.
    user_data = b"\x00" * (2 * 1024 * 1024)
    blob = _leading_skippable_zstd_bytes(b"{}", zstandard, user_data=user_data)
    assert len(blob) > 1024 * 1024  # past the escalation ceiling
    assert len(blob) > 100  # stored size still exceeds the decoded-size limit below

    p = tmp_path / "leading_skippable_past_escalation_ceiling.abicheck.json.zst"
    p.write_bytes(blob)

    assert read_snapshot_bytes(p, max_decoded_bytes=100) == b"{}"


def test_probe_call_sites_stay_correct_past_the_escalation_ceiling(tmp_path):
    """Sixth-order follow-up (Codex review, fresh evidence): the same
    >1 MiB-leading-skippable-frame shape as the test above, but for the
    *other* three skippable-frame-aware probes -- `detect_snapshot_
    compression()`, `read_snapshot_storage_info()`, and `bounded_decoded_
    prefix()` -- each of which independently fell back to `NONE`/raw-bytes
    once its own escalation hit the same 1 MiB ceiling without finding the
    real data frame, unlike `read_snapshot_bytes()`'s cap-selection probe
    (fixed in the prior round). `detect_snapshot_compression()`/`read_
    snapshot_storage_info()` reported the file as uncompressed even though
    the leading magic alone already proves it's zstd; `bounded_decoded_
    prefix()` returned the still-compressed raw skippable-frame bytes as
    though they were decoded content -- the exact bug this whole area's
    fixes exist to prevent, just reached through the escalation-ceiling
    edge case rather than a bare 4-byte prefix. All four probes now share
    one fallback (`_classify_with_skippable_fallback`)."""
    zstandard = pytest.importorskip("zstandard")

    user_data = b"\x00" * (2 * 1024 * 1024)
    payload = json.dumps({"library": "x", "version": "1"}).encode()
    blob = _leading_skippable_zstd_bytes(payload, zstandard, user_data=user_data)
    assert len(blob) > 1024 * 1024  # past the escalation ceiling

    p = tmp_path / "leading_skippable_past_ceiling_probes.abicheck.json.zst"
    p.write_bytes(blob)

    assert detect_snapshot_compression(p) is SnapshotCompression.ZSTD
    assert read_snapshot_storage_info(p).compression is SnapshotCompression.ZSTD

    prefix = bounded_decoded_prefix(p, n=64)
    # The old bug: this equalled the raw, still-compressed skippable-frame
    # bytes (starting with the skippable magic) rather than anything
    # actually decoded.
    skippable_magic = struct.pack("<I", 0x184D2A50)
    assert prefix is None or not prefix.startswith(skippable_magic)


def test_read_snapshot_bytes_handles_many_leading_skippable_frames_at_realistic_scale(tmp_path):
    """Covers the public reader end-to-end at the same realistic frame
    count the primitive-level test above uses directly, per the review
    comment's own request ("cover the public reader at a realistic frame
    count")."""
    import time

    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1"}).encode()
    magic = struct.pack("<I", 0x184D2A50)
    one_frame = magic + struct.pack("<I", 0)
    n_frames = 200_000
    cctx = zstandard.ZstdCompressor(write_content_size=True)
    blob = one_frame * n_frames + cctx.compress(payload)

    p = tmp_path / "many_leading_skippable.abicheck.json.zst"
    p.write_bytes(blob)

    t0 = time.monotonic()
    result = read_snapshot_bytes(p)
    elapsed = time.monotonic() - t0

    assert result == payload
    assert elapsed < 5.0, f"expected near-linear walk, took {elapsed:.2f}s for {n_frames} frames"
