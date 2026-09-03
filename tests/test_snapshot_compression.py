# Copyright 2026 Nikolay Petrov
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

"""Core snapshot storage-envelope tests (ADR-059).

Covers the canonical ``abicheck/snapshot_io.py`` layer directly, plus the
``serialization.load_snapshot``/``save_snapshot``/``write_snapshot``
compatibility surface built on top of it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os

import pytest
from _production_scale_snapshot import (
    graph_heavy_snapshot as _graph_heavy_snapshot,
    graph_heavy_snapshot_at_scale_flat_json_bytes,
)

from abicheck.errors import SnapshotError
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    write_snapshot,
)
from abicheck.snapshot_io import (
    GZIP_MAGIC,
    ZSTD_MAGIC,
    SnapshotCompression,
    detect_compression_from_bytes,
    detect_snapshot_compression,
    read_snapshot_bytes,
    resolve_write_compression,
    write_snapshot_bytes,
    write_snapshot_text,
)


def _sample_snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="foo_init",
                mangled="_Z8foo_initv",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=64,
                alignment_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            ),
        ],
    )


# `_graph_heavy_snapshot` (the small-n builder used directly below and at
# n=8600 by the two production-scale tests further down) and the shared,
# cached n=8600 fixture (and its serialized/compressed derivatives) used by
# those tests and by test_snapshot_compression_public_api_scale.py now live
# in _production_scale_snapshot.py -- a leaf helper module, not grown in
# here a second time (this module was already at this repo's own file-size
# soft cap for the identical reason that sibling test file was split out of
# it in the first place). See that module's own docstring.


# ── Round trips across encodings ────────────────────────────────────────


@pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
def test_round_trip_all_encodings(tmp_path, compression):
    snap = _sample_snapshot()
    # Canonical suffixes drive auto-resolution.
    suffix = {
        "none": ".abicheck.json",
        "gzip": ".abicheck.json.gz",
        "zstd": ".abicheck.json.zst",
    }[compression]
    path = tmp_path / f"snap{suffix}"
    result = write_snapshot(snap, path)
    assert result.compression.value == compression
    loaded = load_snapshot(path)
    assert loaded.library == snap.library
    assert len(loaded.functions) == 1
    assert loaded.functions[0].mangled == "_Z8foo_initv"
    assert len(loaded.types) == 1
    assert loaded.types[0].name == "Widget"


def test_snapshot_from_dict_equal_across_encodings(tmp_path):
    snap = _sample_snapshot()
    plain = tmp_path / "a.abicheck.json"
    gz = tmp_path / "a.abicheck.json.gz"
    zst = tmp_path / "a.abicheck.json.zst"
    write_snapshot(snap, plain)
    write_snapshot(snap, gz)
    write_snapshot(snap, zst)

    d_plain = snapshot_to_dict(load_snapshot(plain))
    d_gz = snapshot_to_dict(load_snapshot(gz))
    d_zst = snapshot_to_dict(load_snapshot(zst))
    assert d_plain == d_gz == d_zst


def test_decompressed_bytes_match_plain_writer_bytes(tmp_path):
    """The decoded payload from a compressed write must be byte-identical to
    what the plain writer produces for the same logical snapshot — P0
    deliberately keeps compression as a pure envelope around identical JSON
    bytes, no separate compact dialect."""
    snap = _sample_snapshot()
    plain_path = tmp_path / "p.abicheck.json"
    gz_path = tmp_path / "p.abicheck.json.gz"
    zst_path = tmp_path / "p.abicheck.json.zst"
    write_snapshot(snap, plain_path)
    write_snapshot(snap, gz_path)
    write_snapshot(snap, zst_path)

    plain_bytes = plain_path.read_bytes()
    assert read_snapshot_bytes(gz_path) == plain_bytes
    assert read_snapshot_bytes(zst_path) == plain_bytes


# ── Detection ────────────────────────────────────────────────────────────


def test_magic_byte_detection_without_suffix(tmp_path):
    snap = _sample_snapshot()
    neutral_gz = tmp_path / "neutral_gz.dat"
    neutral_zst = tmp_path / "neutral_zst.dat"
    write_snapshot(snap, neutral_gz, compression="gzip")
    write_snapshot(snap, neutral_zst, compression="zstd")

    assert detect_snapshot_compression(neutral_gz) == SnapshotCompression.GZIP
    assert detect_snapshot_compression(neutral_zst) == SnapshotCompression.ZSTD
    assert load_snapshot(neutral_gz).library == snap.library
    assert load_snapshot(neutral_zst).library == snap.library


def test_bounded_decoded_prefix_escalates_for_low_compression_content(tmp_path):
    """CodeRabbit review: reading only the first ``n`` *raw* bytes of a
    low-compression-ratio stream can produce fewer than ``n`` *decoded*
    bytes (or fail outright, mid-frame) -- bounded_decoded_prefix must
    escalate its raw read rather than giving up on the first attempt."""
    import random

    from abicheck.snapshot_io import bounded_decoded_prefix

    random.seed(99)
    # Low-entropy/incompressible payload wrapped in valid JSON so a
    # successful decode is still verifiable; large enough that its
    # compressed form exceeds a small requested prefix `n`.
    blob = "".join(chr(random.randrange(0x21, 0x7E)) for _ in range(20000))
    text = json.dumps({"library": "x", "version": "1", "blob": blob})

    gz_path = tmp_path / "incompressible.abicheck.json.gz"
    gz_path.write_bytes(gzip.compress(text.encode(), compresslevel=9))
    assert gz_path.stat().st_size > 4096  # compressed stream spans multiple reads

    prefix = bounded_decoded_prefix(gz_path, n=100)
    assert prefix is not None
    assert prefix.startswith(b'{"library"')


def test_bounded_decoded_prefix_zstd_with_realistic_window(tmp_path):
    """zstd counterpart to the gzip test above -- also the direct regression
    test for the KiB/bytes unit bug in `_try_decode_prefix`'s own
    ``max_window_size=`` call (the sniffing path `sniff_text_format`/
    `service.resolve_input` uses to classify a `.json.zst` baseline before
    ever reaching `_decompress_zstd`'s full-read path). Mirrors a real
    written baseline: highly-compressible JSON content large enough that its
    *frame* records the full 8 MiB window (content exceeding the window
    keeps zstd out of the single-segment mode that otherwise collapses
    `window_size` down to the content size -- see
    `test_zstd_decoder_rejects_realistic_writer_window` below for that
    mechanism spelled out) while the *stored* bytes stay tiny, same as real
    ABI snapshot JSON."""
    zstandard = pytest.importorskip("zstandard")

    from abicheck.snapshot_io import bounded_decoded_prefix

    blob = "a" * (9 * 1024 * 1024)
    text = json.dumps({"library": "x", "version": "1", "blob": blob})

    params = zstandard.ZstdCompressionParameters.from_level(19, window_log=23)
    cctx = zstandard.ZstdCompressor(compression_params=params)
    compressed = cctx.compress(text.encode())
    frame = zstandard.get_frame_parameters(compressed)
    assert frame.window_size == 8 * 1024 * 1024

    zst_path = tmp_path / "realistic_window.abicheck.json.zst"
    zst_path.write_bytes(compressed)

    prefix = bounded_decoded_prefix(zst_path, n=100)
    assert prefix is not None
    assert prefix.startswith(b'{"library"')


def test_detect_compression_from_bytes_plain():
    assert detect_compression_from_bytes(b"{not compressed") == SnapshotCompression.NONE


@pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
def test_read_snapshot_storage_info_matches_actual_file(tmp_path, compression):
    """Codex review (#699): ``read_snapshot_storage_info`` derives
    compression, size, and hash from one open() rather than three separate
    pathname operations -- verify all three still describe the one real
    file on disk (size/hash against a direct re-read, not just internal
    self-consistency)."""
    from abicheck.snapshot_io import read_snapshot_storage_info

    snap = _sample_snapshot()
    path = (
        tmp_path
        / f"lib.abicheck.json{'' if compression == 'none' else '.' + ('gz' if compression == 'gzip' else 'zst')}"
    )
    write_snapshot(snap, path, compression=compression)

    info = read_snapshot_storage_info(path)
    raw = path.read_bytes()

    assert info.path == path
    assert info.compression == SnapshotCompression(compression)
    assert info.stored_size_bytes == len(raw)
    assert info.stored_sha256 == hashlib.sha256(raw).hexdigest()
    assert (
        detect_compression_from_bytes(GZIP_MAGIC + b"rest") == SnapshotCompression.GZIP
    )
    assert (
        detect_compression_from_bytes(ZSTD_MAGIC + b"rest") == SnapshotCompression.ZSTD
    )


def test_suffix_vs_magic_mismatch_is_a_hard_error(tmp_path):
    """A ``.json.zst`` file that is actually plain JSON (or vice versa) is a
    loud, diagnosable error -- never a silent guess either direction."""
    bad = tmp_path / "bad.abicheck.json.zst"
    bad.write_text('{"library": "x", "version": "1"}', encoding="utf-8")
    with pytest.raises(SnapshotError, match="zstd"):
        load_snapshot(bad)

    bad2 = tmp_path / "bad2.abicheck.json"
    bad2.write_bytes(ZSTD_MAGIC + b"payload")
    with pytest.raises(SnapshotError):
        load_snapshot(bad2)


def test_resolve_write_compression_conflict(tmp_path):
    with pytest.raises(SnapshotError):
        resolve_write_compression(
            tmp_path / "x.abicheck.json.gz", SnapshotCompression.ZSTD
        )
    # Non-conflicting explicit requests are fine.
    assert (
        resolve_write_compression(
            tmp_path / "x.abicheck.json.gz", SnapshotCompression.GZIP
        )
        == SnapshotCompression.GZIP
    )
    assert (
        resolve_write_compression(tmp_path / "x.neutral", SnapshotCompression.ZSTD)
        == SnapshotCompression.ZSTD
    )
    assert (
        resolve_write_compression(
            tmp_path / "x.abicheck.json", SnapshotCompression.AUTO
        )
        == SnapshotCompression.NONE
    )


# ── Corruption / truncation ─────────────────────────────────────────────


def test_corrupt_gzip_raises_snapshot_error(tmp_path):
    p = tmp_path / "c.abicheck.json.gz"
    p.write_bytes(GZIP_MAGIC + b"\x00\x00garbage-not-really-gzip")
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_truncated_gzip_raises_snapshot_error(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "t.abicheck.json.gz"
    write_snapshot(snap, p)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_corrupt_zstd_raises_snapshot_error(tmp_path):
    p = tmp_path / "c.abicheck.json.zst"
    p.write_bytes(ZSTD_MAGIC + b"\x00\x00garbage-not-really-zstd-content")
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_truncated_zstd_raises_snapshot_error(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "t.abicheck.json.zst"
    write_snapshot(snap, p)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_concatenated_multi_frame_zstd_not_rejected_as_corrupt(tmp_path):
    """Codex review, PR #699: ``get_frame_parameters(data)`` only inspects
    the *first* frame of a zstd stream -- this module's own writer never
    produces more than one, but a valid zstd stream may legitimately be
    multiple concatenated frames (a foreign/external snapshot), and
    ``stream_reader`` correctly decompresses all of them. The aggregate
    decoded output then exceeds the first frame's own declared content
    size, which must not be flagged as truncation/corruption -- only
    *under*-decoding relative to that declared size is a real truncation
    signal."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1"}).encode()
    half = len(payload) // 2
    cctx1 = zstandard.ZstdCompressor(write_content_size=True)
    frame1 = cctx1.compress(payload[:half])
    cctx2 = zstandard.ZstdCompressor(write_content_size=True)
    frame2 = cctx2.compress(payload[half:])
    p = tmp_path / "multiframe.abicheck.json.zst"
    p.write_bytes(frame1 + frame2)

    assert read_snapshot_bytes(p) == payload


def test_truncated_second_frame_of_multi_frame_zstd_detected(tmp_path):
    """Codex review, PR #699 (second round on the same multi-frame fix): the
    first round's fix only compared the aggregate decoded output against
    the *first* frame's own declared size -- a truncated *later* frame
    still passed, since a fully-intact first frame's contribution alone
    could already exceed that first frame's own declared size (the
    signal the first-round fix relied on), with the truncated remainder
    hidden inside the aggregate. Confirm a genuinely truncated second
    frame is now caught."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1", "extra": "y" * 50}).encode()
    half = len(payload) // 2
    cctx1 = zstandard.ZstdCompressor(write_content_size=True)
    frame1 = cctx1.compress(payload[:half])
    cctx2 = zstandard.ZstdCompressor(write_content_size=True)
    frame2 = cctx2.compress(payload[half:])
    truncated_frame2 = frame2[: len(frame2) - 2]
    p = tmp_path / "multiframe_truncated.abicheck.json.zst"
    p.write_bytes(frame1 + truncated_frame2)

    with pytest.raises(SnapshotError, match="corrupt or truncated"):
        read_snapshot_bytes(p)


def test_truncated_frame_after_unknown_size_frame_still_detected(tmp_path):
    """Codex review, PR #699 (third round on the same multi-frame fix): a
    frame with no declared content size (CONTENTSIZE_UNKNOWN -- a foreign
    encoder that didn't set zstd's content-size flag) used to abandon
    per-frame validation entirely, for that frame *and every subsequent
    one*. A truncated frame anywhere after it could then hit the exact
    same silent short-read this whole validation pass exists to catch,
    with nothing left checking it. Confirm a truncated third frame is
    still caught even though the second frame in between has no declared
    size."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1", "extra": "y" * 80}).encode()
    third = len(payload) // 3
    cctx1 = zstandard.ZstdCompressor(write_content_size=True)
    frame1 = cctx1.compress(payload[:third])
    cctx2 = zstandard.ZstdCompressor(write_content_size=False)  # unknown size
    frame2 = cctx2.compress(payload[third : 2 * third])
    cctx3 = zstandard.ZstdCompressor(write_content_size=True)
    frame3 = cctx3.compress(payload[2 * third :])
    truncated_frame3 = frame3[: len(frame3) - 2]
    p = tmp_path / "multiframe_unknown_then_truncated.abicheck.json.zst"
    p.write_bytes(frame1 + frame2 + truncated_frame3)

    with pytest.raises(SnapshotError, match="corrupt or truncated"):
        read_snapshot_bytes(p)


def test_unknown_size_frame_among_valid_frames_not_rejected(tmp_path):
    """Sanity: a legitimate unknown-size frame surrounded by otherwise-
    valid, intact frames must still round-trip correctly -- only a real
    truncation should be flagged."""
    zstandard = pytest.importorskip("zstandard")

    payload = json.dumps({"library": "x", "version": "1", "extra": "y" * 80}).encode()
    third = len(payload) // 3
    cctx1 = zstandard.ZstdCompressor(write_content_size=True)
    frame1 = cctx1.compress(payload[:third])
    cctx2 = zstandard.ZstdCompressor(write_content_size=False)
    frame2 = cctx2.compress(payload[third : 2 * third])
    cctx3 = zstandard.ZstdCompressor(write_content_size=True)
    frame3 = cctx3.compress(payload[2 * third :])
    p = tmp_path / "multiframe_unknown_valid.abicheck.json.zst"
    p.write_bytes(frame1 + frame2 + frame3)

    assert read_snapshot_bytes(p) == payload


# ── Decompression limits ────────────────────────────────────────────────


def test_decoded_size_overflow_gzip(tmp_path):
    big_text = json.dumps({"library": "x", "version": "1", "pad": "a" * 5000})
    p = tmp_path / "big.abicheck.json.gz"
    p.write_bytes(gzip.compress(big_text.encode(), compresslevel=9, mtime=0))
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_decoded_size_overflow_zstd(tmp_path):
    big_text = json.dumps({"library": "x", "version": "1", "pad": "a" * 5000})
    p = tmp_path / "big.abicheck.json.zst"
    write_snapshot_text(big_text, p, compression=SnapshotCompression.ZSTD)
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_plain_snapshot_overflow(tmp_path):
    p = tmp_path / "big.abicheck.json"
    p.write_text(json.dumps({"library": "x", "version": "1", "pad": "a" * 5000}))
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_zstd_max_window_size_is_bytes_not_kibibytes():
    """python-zstandard's ``ZstdDecompressor(max_window_size=...)`` docstring
    *claims* kibibytes, but the underlying implementation (both the C
    extension and ``backend_cffi.py``'s ``_ensure_dctx``) passes the value
    straight through to ``ZSTD_DCtx_setMaxWindowSize()`` with no ``* 1024``
    -- and that libzstd API takes a raw byte count. An earlier revision of
    this module divided the intended byte ceiling by 1024 (reading the
    docstring at face value), which shrank the accepted window to
    1/1024th of the intended 2 GiB and made any snapshot compressed with a
    real multi-megabyte window (e.g. the writer's 8 MiB baseline level)
    undecodable. See ``test_zstd_decoder_rejects_realistic_writer_window``
    below for the end-to-end repro of that failure mode."""
    import zstandard

    from abicheck.snapshot_io import _ZSTD_MAX_WINDOW_LOG, _zstd_max_window_size_bytes

    result = _zstd_max_window_size_bytes(zstandard)
    assert result == 1 << min(_ZSTD_MAX_WINDOW_LOG, zstandard.WINDOWLOG_MAX)
    # On any real (64-bit) build this is the documented 2 GiB ceiling; a
    # 32-bit build would clamp lower (see _zstd_max_window_size_bytes's own
    # docstring) rather than asserting this exact value everywhere.
    if zstandard.WINDOWLOG_MAX >= _ZSTD_MAX_WINDOW_LOG:
        assert result == 2 * 1024 * 1024 * 1024  # 2 GiB, per the comment


def test_zstd_max_window_size_clamps_to_backend_windowlog_max():
    """Codex review: ``ZSTD_DCtx_setMaxWindowSize()`` bound-checks its
    argument against the backend's own reported ``windowLogMax`` and
    *errors* (not just declines the frame) if exceeded -- and a 32-bit
    libzstd build's ``ZSTD_WINDOWLOG_MAX_32`` is 30, not this module's
    fixed 31. Passing the fixed value unconditionally would make
    ``ZstdDecompressor(max_window_size=...)`` itself raise on such a
    build, rejecting every zstd snapshot outright. Simulate that build
    with a stand-in exposing a lower ``WINDOWLOG_MAX`` and confirm the
    computed ceiling clamps down to it rather than using the fixed 31."""
    from abicheck.snapshot_io import _zstd_max_window_size_bytes

    class _Fake32BitBuild:
        WINDOWLOG_MAX = 30  # ZSTD_WINDOWLOG_MAX_32

    assert _zstd_max_window_size_bytes(_Fake32BitBuild) == 1 << 30


def test_zstd_decoder_rejects_window_above_ceiling(tmp_path):
    """The *effective* max_window_size passed to python-zstandard's
    ZstdDecompressor must actually cap accepted windows at the ceiling --
    not silently allow an oversized one because of a unit-conversion bug.
    zstd's own ZSTD_WINDOWLOG_MAX is 31 (2 GiB) on a 64-bit build, i.e.
    exactly this module's ceiling, so a legitimate max-size frame (window_log
    31) must still decode successfully with the fixed byte-denominated value
    -- if the conversion silently permitted a *smaller* effective window than
    intended, this would fail instead."""
    zstandard = pytest.importorskip("zstandard")

    params = zstandard.ZstdCompressionParameters(window_log=31)
    cctx = zstandard.ZstdCompressor(compression_params=params)
    compressed = cctx.compress(b"a" * (1 << 20))
    p = tmp_path / "max_window.abicheck.json.zst"
    p.write_bytes(compressed)
    # Not real JSON, so it'll fail JSON parsing downstream, but the
    # decompression step itself (the thing max_window_size gates) must
    # succeed rather than raising "too much memory" for a legitimate,
    # at-the-ceiling window.
    assert read_snapshot_bytes(p) == b"a" * (1 << 20)


def test_zstd_decoder_rejects_realistic_writer_window(tmp_path):
    """End-to-end repro of the KiB/bytes unit bug: a frame whose *actually
    required* window (not just its nominal window_log ceiling -- see the
    highly-compressible fixture in `test_zstd_decoder_rejects_window_above_
    ceiling` above, whose real required window collapses to far less than
    its window_log) is a realistic multi-megabyte size, matching what the
    writer picks at its baseline compression level. Confirmed the old,
    KiB-denominated ceiling (2097152, interpreted as raw bytes by
    python-zstandard) rejects this frame with "Frame requires too much
    memory for decoding", while the fixed byte ceiling decodes it.

    Codex review: an earlier revision of this test compressed 9 MiB of
    genuinely incompressible (`random.randrange(256)`) data to force the
    window -- correct, but real zstd level-19 compression of incompressible
    input is slow (~5-8s by itself), leaving this in the *default fast*
    lane despite costing about as much as the dedicated `slow`-marked test
    two below it. Content only needs to *exceed* the 8 MiB window for zstd
    to stop collapsing the frame's recorded `window_size` down to
    `content_size` (confirmed empirically, and exercised the same way by
    `test_zstd_round_trip_at_production_scale_and_level`'s real snapshot
    fixture below) -- highly-compressible content works identically for
    that purpose and compresses in milliseconds, so no `slow` marker is
    needed at all."""
    zstandard = pytest.importorskip("zstandard")

    # An 8 MiB window (window_log=23); content merely needs to exceed the
    # window for the frame to record the full 8 MiB rather than collapsing
    # to its own (smaller) content size -- compressibility doesn't matter.
    payload = b"a" * (9 * 1024 * 1024)
    params = zstandard.ZstdCompressionParameters.from_level(19, window_log=23)
    cctx = zstandard.ZstdCompressor(compression_params=params)
    compressed = cctx.compress(payload)
    frame = zstandard.get_frame_parameters(compressed)
    assert frame.window_size == 8 * 1024 * 1024

    p = tmp_path / "realistic_window.abicheck.json.zst"
    p.write_bytes(compressed)
    assert read_snapshot_bytes(p, max_decoded_bytes=len(payload) + 10) == payload


@pytest.mark.slow
def test_zstd_round_trip_at_production_scale_and_level(tmp_path):
    """The postmortem regression test for the KiB/bytes unit bug (ADR-059
    §12): every test above hand-builds a `zstandard.CompressionParameters`
    object to force a specific window -- useful for pinning the exact
    mechanism, but none of them go through the *actual* production write
    path, which never sets an explicit window and instead lets zstd
    auto-select one from `ZSTD_LEVEL_BASELINE` and the input size. This test
    calls only the same public functions `dump`/`write_snapshot` call
    (`write_snapshot_bytes`/`read_snapshot_bytes`, no manual compression
    params) against a real, large-enough `AbiSnapshot` (`_graph_heavy_
    snapshot`, scaled up past the point its serialized JSON exceeds 8 MiB --
    the threshold where zstd stops collapsing its recorded window down to
    the content size) so the frame really does carry the same 8 MiB window
    a real oneDAL-scale baseline does (confirmed below), then asserts a full
    round trip. Would have caught the original bug directly: it fails with
    the same "Frame requires too much memory for decoding" against the
    pre-fix ceiling and passes with the fix, with no knowledge of zstd's
    internal APIs required to write or understand it. `slow`-marked (not in
    the fast default suite): real level-19 compression of an 8+ MiB payload
    takes several seconds, unlike every other test in this file -- still
    covered by CI's dedicated `-m slow` lane (`ci.yml`)."""
    zstandard = pytest.importorskip("zstandard")

    original_bytes = graph_heavy_snapshot_at_scale_flat_json_bytes()

    # The real production chokepoint: no manual CompressionParameters, no
    # explicit window -- exactly what `dump`/`write_snapshot` call.
    p = tmp_path / "production_scale.abicheck.json.zst"
    result = write_snapshot_bytes(
        original_bytes, p, compression=SnapshotCompression.ZSTD
    )
    assert result.compression is SnapshotCompression.ZSTD

    # Sanity-check the premise before trusting the round trip below: the
    # real writer (level=ZSTD_LEVEL_BASELINE, no explicit window) must
    # actually produce the realistic 8 MiB window this test exists to catch
    # a regression against -- if a future zstandard/libzstd upgrade changed
    # that auto-selection, this assertion (not a silent pass) is what would
    # tell us the fixture needs revisiting.
    frame = zstandard.get_frame_parameters(p.read_bytes())
    assert frame.window_size == 8 * 1024 * 1024
    assert frame.content_size == len(original_bytes)

    assert (
        read_snapshot_bytes(p, max_decoded_bytes=len(original_bytes) + 10)
        == original_bytes
    )


def test_gzip_round_trip_at_production_scale(tmp_path):
    """gzip counterpart to `test_zstd_round_trip_at_production_scale_and_level`
    above -- added per the AGENTS.md Test-quality-gates principle that
    prompted that test ("at least one test per algorithm must go through
    the module's actual public entry point, at a realistic content scale"),
    which a review round correctly flagged as unfulfilled for gzip: every
    existing gzip test used either a tiny (`_sample_snapshot`) fixture or a
    hand-built low-level fixture, never the real `write_snapshot_bytes`/
    `read_snapshot_bytes` chokepoint at production scale. Unlike zstd, gzip
    (`zlib` under the hood) has no window-size *parameter* a caller can get
    a unit wrong on -- this test exists to keep the stated principle
    actually true for every supported algorithm, not because a gzip-specific
    bug is already known. Cheap to run (gzip compresses this size in well
    under a second, unlike zstd level 19), so no `slow` marker needed."""
    original_bytes = graph_heavy_snapshot_at_scale_flat_json_bytes()

    p = tmp_path / "production_scale.abicheck.json.gz"
    result = write_snapshot_bytes(
        original_bytes, p, compression=SnapshotCompression.GZIP
    )
    assert result.compression is SnapshotCompression.GZIP

    assert (
        read_snapshot_bytes(p, max_decoded_bytes=len(original_bytes) + 10)
        == original_bytes
    )


def test_oversized_stored_file_rejected_before_full_read(tmp_path, monkeypatch):
    """CodeRabbit review, refined by two later Codex rounds: the stored-
    file-size check (via fstat(), before a full read) must reject a file
    whose *stored* size alone already exceeds the effective cap -- but (see
    `DEFAULT_MAX_STORED_BYTES`'s docstring above) that cap is now an
    independent, fixed ceiling rather than anything derived from the
    caller's `max_decoded_bytes`, since a valid concatenated multi-member
    gzip stream disproved both earlier margin-based formulas. Lower the
    ceiling via its private env var to keep this test fast rather than
    needing an actually-multi-gigabyte fixture."""
    import os as _os
    import random

    import abicheck.snapshot_io as snapshot_io_mod

    random.seed(1234)
    incompressible = bytes(random.randrange(256) for _ in range(200_000))
    p = tmp_path / "big.abicheck.json.gz"
    p.write_bytes(gzip.compress(incompressible, compresslevel=9))
    stored_size = _os.path.getsize(p)
    assert stored_size > 100
    monkeypatch.setenv(snapshot_io_mod._MAX_STORED_BYTES_ENV, "100")
    # cap = max(max_decoded_bytes, effective _max_stored_bytes()) -- keep
    # the decoded-size argument small too, so the lowered stored-bytes
    # ceiling (not an incidentally large decoded limit) is what's exercised.
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=50)


def test_tiny_compressed_payload_not_rejected_by_stored_size_precheck(tmp_path):
    """Codex review, PR #699: gzip/zstd container framing adds a small,
    fixed overhead independent of payload size (e.g. `{}` decodes to 2
    bytes but gzip-compresses to 22) -- the stored-size precheck above must
    not reject a legitimately tiny/boundary decoded payload purely because
    its *stored* size (with framing overhead) exceeds the exact decoded
    limit. Only a plain/uncompressed file, whose stored size equals its
    decoded size exactly, gets the tight comparison."""
    for compression in (SnapshotCompression.GZIP, SnapshotCompression.ZSTD):
        p = tmp_path / f"tiny.abicheck.json.{compression.value}"
        write_snapshot_text("{}", p, compression=compression)
        stored_size = p.stat().st_size
        assert stored_size > 2  # framing overhead really does exceed the payload
        assert read_snapshot_bytes(p, max_decoded_bytes=2) == b"{}"


def test_stored_size_cap_is_independent_of_max_decoded_bytes():
    """Codex review, PR #699: the compressed-file stored-size cap is a
    fixed, independent ceiling (`DEFAULT_MAX_STORED_BYTES`), not derived
    from the caller's `max_decoded_bytes` -- verify the pure arithmetic
    directly rather than only through an end-to-end fixture."""
    from abicheck.snapshot_io import DEFAULT_MAX_STORED_BYTES, _max_stored_bytes

    assert _max_stored_bytes() == DEFAULT_MAX_STORED_BYTES
    # A tiny caller-requested decoded limit must not shrink the stored cap
    # -- it stays at the independent default regardless.
    assert DEFAULT_MAX_STORED_BYTES > 100


def test_raised_decoded_limit_does_not_expand_stored_size_ceiling(
    tmp_path, monkeypatch
):
    """Codex review, PR #699 (fourth round on this precheck): an earlier fix
    used ``max(limit, _max_stored_bytes())`` for the compressed-file cap --
    a caller raising ``max_decoded_bytes`` (tolerance for large *decoded*
    content) silently raised the *stored*-size ceiling too, letting an
    untrusted compressed file past a deliberately lower/default
    ``_max_stored_bytes()``. The two are orthogonal knobs; a raised decoded
    limit must not widen the independent stored-size safety ceiling."""
    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "big.abicheck.json.gz"
    write_snapshot(snap, p)
    stored_size = p.stat().st_size

    # Lower the stored-bytes ceiling below the real file's own stored size,
    # then raise max_decoded_bytes far past it -- the file must still be
    # rejected on the (unrelated, still-lower) stored-size ceiling.
    monkeypatch.setenv(snapshot_io_mod._MAX_STORED_BYTES_ENV, str(stored_size - 1))
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=10 * 1024 * 1024 * 1024)


def test_concatenated_multi_member_gzip_not_rejected_by_stored_size_precheck(
    tmp_path,
):
    """Codex review, PR #699 (third round on this precheck): a *valid*
    concatenated multi-member gzip stream (RFC 1952 permits this, and
    Python's gzip module transparently decodes it as one logical stream)
    can have stored-size overhead that scales with the *number of
    members*, not the payload size -- disproving both an earlier fixed
    margin and a later limit-proportional one. One gzip member per byte is
    the worst realistic case; confirm it still round-trips correctly even
    with `max_decoded_bytes` set exactly to the payload's own size (the
    tightest possible boundary)."""
    payload = json.dumps({"library": "x", "version": "1", "pad": "ab" * 500}).encode()
    members = b"".join(gzip.compress(bytes([b]), compresslevel=9) for b in payload)
    p = tmp_path / "concatenated.abicheck.json.gz"
    p.write_bytes(members)
    assert len(members) > len(payload) * 5  # overhead really does dominate here

    decoded = read_snapshot_bytes(p, max_decoded_bytes=len(payload))
    assert decoded == payload


# ── Determinism ──────────────────────────────────────────────────────────


def test_deterministic_gzip_bytes(tmp_path):
    snap = _sample_snapshot()
    p1 = tmp_path / "a.abicheck.json.gz"
    p2 = tmp_path / "b.abicheck.json.gz"
    write_snapshot(snap, p1)
    write_snapshot(snap, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_deterministic_zstd_bytes(tmp_path):
    snap = _sample_snapshot()
    p1 = tmp_path / "a.abicheck.json.zst"
    p2 = tmp_path / "b.abicheck.json.zst"
    write_snapshot(snap, p1)
    write_snapshot(snap, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_gzip_header_has_no_embedded_filename_or_mtime(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "some_very_specific_name.abicheck.json.gz"
    write_snapshot(snap, p)
    data = p.read_bytes()
    # gzip header: mtime is bytes 4-7 (must be zero); FNAME flag bit is bit 3
    # of the flags byte (byte 3) and must be unset.
    assert data[4:8] == b"\x00\x00\x00\x00"
    flags = data[3]
    assert not (flags & 0x08), "gzip FNAME flag must not be set (no embedded filename)"
    # OS-identifier byte: CPython's gzip module disagrees on this across
    # supported 3.10-3.14 interpreters (3 "Unix" vs. 255 "unknown") even with
    # mtime pinned, so it's forced to 255 explicitly -- see _compress_gzip.
    assert data[9] == 0xFF, (
        "gzip OS byte must be forced to 255 (unknown) for cross-version determinism"
    )


# ── Atomic writes ────────────────────────────────────────────────────────


def test_atomic_write_leaves_no_temp_file(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "x.abicheck.json.zst"
    write_snapshot(snap, p)
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
def test_new_file_honors_umask_not_owner_only(tmp_path):
    """tempfile.mkstemp() always creates 0600; a snapshot write must not
    silently make every new file owner-only. It should also not ignore an
    explicit, more restrictive umask by forcing a fixed mode (two rounds of
    Codex/CodeRabbit review): the temp file is created via a direct
    os.open(..., 0o666) so the kernel applies the *caller's* umask
    atomically at creation time -- no process-wide os.umask() read/toggle
    involved either way."""
    old_umask = os.umask(0o022)
    try:
        snap = _sample_snapshot()
        p = tmp_path / "new.abicheck.json"
        write_snapshot(snap, p)
        assert oct(p.stat().st_mode & 0o777) == oct(0o644)
    finally:
        os.umask(old_umask)

    old_umask = os.umask(0o077)
    try:
        snap = _sample_snapshot()
        p2 = tmp_path / "restrictive.abicheck.json"
        write_snapshot(snap, p2)
        assert oct(p2.stat().st_mode & 0o777) == oct(0o600)
    finally:
        os.umask(old_umask)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
def test_existing_file_mode_is_preserved_across_rewrite(tmp_path):
    """Rewriting an existing snapshot (e.g. re-dumping a shared baseline)
    must not silently strip its group/world-readable permissions."""
    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    os.chmod(p, 0o640)
    write_snapshot(snap, p)
    assert oct(p.stat().st_mode & 0o777) == oct(0o640)


@pytest.mark.skipif(
    os.name == "nt", reason="os.chown/getgrall not available on Windows"
)
def test_existing_file_group_is_preserved_across_rewrite(tmp_path):
    """Codex review, PR #699: rewriting an existing snapshot used to
    silently revoke a deliberately-assigned group owner (e.g. an
    `abi-readers` group with no setgid parent directory) -- the fresh temp
    file inherits the writer's own default group, and only the mode was
    carried over to the replacement, not the group. Requires root or
    CAP_CHOWN to reassign an arbitrary group, so this is best-effort like
    the write path itself."""
    import grp

    try:
        target_gid = grp.getgrnam("daemon").gr_gid
    except KeyError:
        pytest.skip("no 'daemon' group on this system")

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    try:
        os.chown(p, -1, target_gid)
    except (OSError, AttributeError):
        pytest.skip("cannot chown to an arbitrary group in this environment")
    if p.stat().st_gid != target_gid:
        pytest.skip("chown did not take effect (insufficient privileges)")

    write_snapshot(snap, p)
    assert p.stat().st_gid == target_gid


@pytest.mark.skipif(
    os.name == "nt", reason="os.chown/getpwall not available on Windows"
)
def test_existing_file_owner_is_preserved_across_rewrite(tmp_path):
    """Codex review, PR #699 (second finding on the same fix): rewriting an
    existing snapshot also used to silently transfer ownership to the
    writer -- the fresh temp file is owned by the writer, and only the mode
    (then, after the first fix, the group) was carried over to the
    replacement, not the uid. Affects a shared baseline owned by a service
    account. Requires root/CAP_CHOWN to reassign an arbitrary uid, so this
    is best-effort like the write path itself."""
    import pwd

    try:
        target_uid = pwd.getpwnam("daemon").pw_uid
    except KeyError:
        pytest.skip("no 'daemon' user on this system")

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    try:
        os.chown(p, target_uid, -1)
    except (OSError, AttributeError):
        pytest.skip("cannot chown to an arbitrary uid in this environment")
    if p.stat().st_uid != target_uid:
        pytest.skip("chown did not take effect (insufficient privileges)")

    write_snapshot(snap, p)
    assert p.stat().st_uid == target_uid


@pytest.mark.skipif(os.name == "nt", reason="POSIX chown/uid semantics only")
def test_owner_restoration_failure_aborts_the_replacement(tmp_path, monkeypatch):
    """Codex review, PR #699 (third finding on the same fix): the previous
    fix made ownership restoration best-effort (silently swallowing any
    chown() failure) -- but chown(uid, gid) is all-or-nothing, so when an
    unprivileged writer genuinely can't restore a *different* existing
    owner (the exact case ownership preservation exists for -- a shared
    baseline owned by a service account, refreshed by a non-privileged
    writer), silently proceeding to os.replace() transfers ownership to
    the writer instead. That's exactly the class of silent attribute loss
    the unguarded os.chmod() call already refuses to tolerate. The write
    must abort instead, leaving the existing destination untouched."""
    import errno
    import pwd

    import abicheck.snapshot_io as snapshot_io_mod

    try:
        target_uid = pwd.getpwnam("daemon").pw_uid
    except KeyError:
        pytest.skip("no 'daemon' user on this system")
    if target_uid == os.getuid():
        pytest.skip("test process already runs as the target uid")

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    try:
        os.chown(p, target_uid, -1)
    except (OSError, AttributeError):
        pytest.skip("cannot chown to an arbitrary uid in this environment")
    if p.stat().st_uid != target_uid:
        pytest.skip("chown did not take effect (insufficient privileges)")
    original_bytes = p.read_bytes()

    def _eperm_chown(path, uid, gid):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(snapshot_io_mod.os, "chown", _eperm_chown)
    with pytest.raises(OSError):
        write_snapshot(snap, p)
    # Destination and its ownership must be untouched -- os.replace() must
    # never have run -- and no stray temp file left behind.
    assert p.read_bytes() == original_bytes
    assert p.stat().st_uid == target_uid
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX chown/gid semantics only")
def test_group_only_restoration_failure_also_aborts_the_replacement(
    tmp_path, monkeypatch
):
    """Codex review, PR #699 (fourth finding on the same fix): the previous
    round's fix only re-raised a chown() failure when the *uid* itself
    needed changing, on the assumption that a writer who already owns the
    destination but isn't a member of its assigned group is a lower-
    stakes, best-effort-tolerable case. Fresh evidence showed that's
    wrong: silently falling back to the writer's own primary group in
    that case can revoke real group-based read access for a shared
    baseline's other readers, not just cosmetically differ. A gid-only
    restoration failure must abort the replacement exactly like a uid
    one does."""
    import errno

    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    original_bytes = p.read_bytes()
    # The writer owns this file (default ownership) -- existing_uid ==
    # os.getuid(), the exact case the previous fix's condition let through
    # without re-raising.
    assert p.stat().st_uid == os.getuid()

    def _eperm_chown(path, uid, gid):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(snapshot_io_mod.os, "chown", _eperm_chown)
    with pytest.raises(OSError):
        write_snapshot(snap, p)
    assert p.read_bytes() == original_bytes
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need elevated privileges on Windows"
)
def test_write_through_symlink_preserves_the_link(tmp_path):
    """Codex review, PR #699: os.replace(tmp_path, path) swaps *path*'s own
    directory entry, so writing to a symlinked destination used to destroy
    the link -- regressing the previous open(path, "w") behavior, which
    follows a symlink and writes through it. Writing to a path that is a
    symlink must update the link's *target* and leave the link itself
    intact, exactly like the plain-open behavior it replaced."""
    real = tmp_path / "real.abicheck.json"
    write_snapshot_text('{"a": 1}', real, compression=SnapshotCompression.NONE)
    link = tmp_path / "link.abicheck.json"
    link.symlink_to(real)

    write_snapshot_text('{"a": 2}', link, compression=SnapshotCompression.NONE)

    assert link.is_symlink()
    assert os.readlink(link) == str(real)
    assert real.read_text() == '{"a": 2}'
    assert link.read_text() == '{"a": 2}'


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need elevated privileges on Windows"
)
def test_write_through_dangling_symlink_creates_target(tmp_path):
    """A symlink whose target doesn't exist yet must still resolve -- the
    write creates the target file, not a plain file at the link's own
    path."""
    target = tmp_path / "does_not_exist_yet.abicheck.json"
    link = tmp_path / "link.abicheck.json"
    link.symlink_to(target)

    write_snapshot_text('{"a": 3}', link, compression=SnapshotCompression.NONE)

    assert link.is_symlink()
    assert target.read_text() == '{"a": 3}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX device files only")
def test_write_through_character_device_does_not_replace_it(tmp_path):
    """Codex review, PR #699: an existing non-regular destination (here a
    character device, /dev/null) has no meaningful "atomic replace" --
    os.replace() would swap it out for a brand-new regular file, destroying
    the special file. The previous open(path, "w") behavior wrote directly
    through it instead; verify write_snapshot does the same (no error, and
    /dev/null is still a character device afterward, not a regular file)."""
    import stat as stat_mod
    from pathlib import Path

    dev_null = Path("/dev/null")
    if not dev_null.exists() or not stat_mod.S_ISCHR(dev_null.stat().st_mode):
        pytest.skip("/dev/null is not a character device on this system")

    snap = _sample_snapshot()
    write_snapshot(snap, dev_null, compression="none")  # must not raise
    assert stat_mod.S_ISCHR(dev_null.stat().st_mode)  # still a char device


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs only")
def test_write_through_fifo_does_not_replace_it(tmp_path):
    """Same regression as the character-device test above, for a named
    pipe. A FIFO's write-end open() blocks until a reader has opened the
    read end -- opening the read end non-blocking *first* (the standard
    POSIX trick) lets the write-side open proceed without a concurrent
    reader thread."""
    import stat as stat_mod

    fifo_path = tmp_path / "pipe.abicheck.json"
    os.mkfifo(fifo_path)
    read_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        snap = _sample_snapshot()
        write_snapshot(snap, fifo_path, compression="none")  # must not raise
        assert stat_mod.S_ISFIFO(fifo_path.stat().st_mode)  # still a FIFO
    finally:
        os.close(read_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs only")
def test_write_through_a_symlink_to_a_fifo_does_not_need_realpath(
    tmp_path, monkeypatch
):
    """Codex review, PR #699 (second finding): the non-regular check used
    to resolve *path* via os.path.realpath() before stat()-ing it -- for a
    symlink whose target is a pipe-backed file descriptor (e.g. /dev/stdout
    connected to a pipe on a CI runner), realpath() can return a synthetic,
    unstat-able pseudo-path like /proc/<pid>/fd/pipe:[12345], which then
    made the follow-up stat() fail and the non-regular destination was
    never recognized -- the write fell through to the atomic-rename path
    and tried to create a temp file under that bogus pseudo-directory.

    Reproduce the failure mode directly: monkeypatch os.path.realpath to
    return a path that cannot be stat()-ed at all, and confirm
    write_snapshot through a symlink-to-FIFO still succeeds -- proving the
    non-regular detection no longer depends on realpath() succeeding."""
    import stat as stat_mod

    fifo_path = tmp_path / "real_pipe"
    os.mkfifo(fifo_path)
    link_path = tmp_path / "link_to_pipe.abicheck.json"
    link_path.symlink_to(fifo_path)

    def _broken_realpath(path, *args, **kwargs):
        return "/proc/nonexistent-pid/fd/pipe:[999999]"

    monkeypatch.setattr(os.path, "realpath", _broken_realpath)

    read_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        snap = _sample_snapshot()
        write_snapshot(snap, link_path, compression="none")  # must not raise
        assert stat_mod.S_ISFIFO(fifo_path.stat().st_mode)  # still a FIFO
        assert stat_mod.S_ISLNK(link_path.lstat().st_mode)  # symlink intact
    finally:
        os.close(read_fd)


def test_non_absence_stat_failure_on_destination_propagates(tmp_path, monkeypatch):
    """Codex review, PR #699 (third finding): the non-regular/hard-link
    destination checks in _atomic_write_bytes() only treat
    FileNotFoundError/NotADirectoryError (a path component genuinely
    doesn't exist) as "no pre-existing destination to worry about." Any
    other OSError (a transient EIO/EACCES/ELOOP, ...) must propagate
    instead of being silently treated as absence -- swallowing it would
    let the write fall through to the atomic-rename path for a
    destination whose real type (FIFO, device, multiply-linked file, ...)
    was never actually established, bypassing the non-regular/hard-link
    safeguards for an inode that may still be exactly that once the
    transient error clears."""
    import errno

    target_path = tmp_path / "out.abicheck.json"
    real_stat = os.stat

    def _flaky_stat(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(target_path):
            raise OSError(errno.EIO, "simulated transient I/O error")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", _flaky_stat)

    snap = _sample_snapshot()
    with pytest.raises(OSError):
        write_snapshot(snap, target_path, compression="none")
    # No partial/replacement file was created at the destination (checked
    # via the real os.stat, since the monkeypatched one always raises for
    # this exact path).
    with pytest.raises(FileNotFoundError):
        real_stat(target_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs only")
def test_read_snapshot_from_a_fifo(tmp_path):
    """Codex review, PR #699: read_snapshot_bytes() used to rewind via
    f.seek(0) after sniffing the 4-byte magic prefix -- a FIFO/pipe (or
    /dev/stdin) is not seekable, so the rewind raised
    io.UnsupportedOperation, regressing the previous json.load(open(...))
    implementation, which could consume such a stream just fine. Confirm
    a snapshot can be read from a real FIFO end to end (the read-open of
    a FIFO blocks until a writer appears, so the payload is fed from a
    background thread rather than pre-buffered)."""
    import threading

    payload = json.dumps({"library": "libpipe", "version": "1.0"}).encode()
    fifo_path = tmp_path / "in.abicheck.json"
    os.mkfifo(fifo_path)

    def _feed():
        with open(fifo_path, "wb") as w:
            w.write(payload)

    writer = threading.Thread(target=_feed)
    writer.start()
    try:
        assert read_snapshot_bytes(fifo_path) == payload
    finally:
        writer.join(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links only")
def test_hard_linked_destination_is_rejected(tmp_path):
    """Codex review, PR #699: os.replace() installs the new content under
    *this* pathname's directory entry only -- an existing hard link keeps
    pointing at the old inode's stale content, silently diverging from
    what this call just wrote (the previous open(path, "w") behavior wrote
    into the shared inode directly, keeping every alias in sync). There is
    no way to have both that every-alias-updated behavior and this
    function's own atomicity guarantees at once, so a hard-linked
    destination is a hard error instead of a silent, surprising choice
    either way -- and the existing content must stay untouched."""
    p = tmp_path / "original.abicheck.json"
    p.write_text('{"a": 1}')
    alias = tmp_path / "alias.abicheck.json"
    os.link(p, alias)
    assert p.stat().st_nlink == 2
    original_bytes = p.read_bytes()

    snap = _sample_snapshot()
    with pytest.raises(SnapshotError, match="hard link"):
        write_snapshot(snap, p, compression="none")

    # Neither alias's content changed, and no stray temp file was left.
    assert p.read_bytes() == original_bytes
    assert alias.read_bytes() == original_bytes
    leftovers = [f for f in tmp_path.iterdir() if f not in (p, alias)]
    assert leftovers == []


def test_failed_write_preserves_existing_destination(tmp_path, monkeypatch):
    snap = _sample_snapshot()
    p = tmp_path / "x.abicheck.json.zst"
    write_snapshot(snap, p)
    original_bytes = p.read_bytes()

    import abicheck.snapshot_io as snapshot_io_mod

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(snapshot_io_mod.os, "replace", _boom)
    with pytest.raises(OSError):
        write_snapshot(snap, p)
    # Destination must be untouched, and no stray temp file left behind.
    assert p.read_bytes() == original_bytes
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


def test_fsync_storage_failure_aborts_write_and_preserves_destination(
    tmp_path, monkeypatch
):
    """Codex review, PR #699: a real storage failure from fsync() (disk
    full, I/O error, ...) must abort the write rather than being swallowed
    as "this platform doesn't support fsync" -- the previous blanket
    `except OSError: pass` would proceed to os.replace() with data the
    kernel just reported it could not durably flush, overwriting a
    known-good destination with unconfirmed content."""
    import errno

    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    write_snapshot(snap, p)
    original_bytes = p.read_bytes()

    def _enospc(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(snapshot_io_mod.os, "fsync", _enospc)
    with pytest.raises(OSError):
        write_snapshot(snap, p)
    # Destination must be untouched (os.replace() must never have run), and
    # no stray temp file left behind.
    assert p.read_bytes() == original_bytes
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


def test_fsync_unsupported_error_is_still_best_effort(tmp_path, monkeypatch):
    """The narrowed fsync error handling must still swallow the specific
    "this filesystem/platform doesn't support fsync" errnos (EINVAL/
    ENOTSUP/EOPNOTSUPP), not turn every fsync failure into a hard abort."""
    import errno

    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "new.abicheck.json"

    def _einval(fd):
        raise OSError(errno.EINVAL, "fsync not supported on this filesystem")

    monkeypatch.setattr(snapshot_io_mod.os, "fsync", _einval)
    write_snapshot(snap, p)  # must not raise
    assert p.is_file()


@pytest.mark.skipif(os.name == "nt", reason="no O_DIRECTORY on Windows")
def test_replace_fsyncs_the_parent_directory(tmp_path, monkeypatch):
    """CodeRabbit review, PR #699: os.replace()'s directory-entry update is
    not itself durable across a crash until the parent directory is
    fsync'd -- the file-content fsync alone only guarantees the new data
    reaches storage, not that the rename pointing at it survives a power
    loss. Verify the parent directory is actually opened and fsync'd, and
    that it happens strictly after the file's own content fsync (fsync'ing
    the directory before the data it points at is durable would defeat the
    point)."""
    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    write_snapshot(snap, p)  # pre-existing destination, so this is a rewrite

    calls: list[tuple[str, int]] = []
    real_fsync = os.fsync
    real_open = os.open

    def _tracking_fsync(fd):
        calls.append(("fsync", fd))
        return real_fsync(fd)

    dir_fds: list[int] = []

    def _tracking_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        if flags & os.O_DIRECTORY:
            dir_fds.append(fd)
        return fd

    monkeypatch.setattr(snapshot_io_mod.os, "fsync", _tracking_fsync)
    monkeypatch.setattr(snapshot_io_mod.os, "open", _tracking_open)
    write_snapshot(snap, p)

    assert len(calls) == 2, calls  # file content fsync, then directory fsync
    assert len(dir_fds) == 1
    # fd numbers can be reused once closed, so identify the directory fsync
    # by *position* (it must be the second, later call) rather than by a
    # fd-number inequality against the first.
    assert calls[1] == ("fsync", dir_fds[0])  # directory fd, strictly after


@pytest.mark.skipif(os.name == "nt", reason="no O_DIRECTORY on Windows")
def test_directory_fsync_real_failure_propagates_but_content_is_already_live(
    tmp_path, monkeypatch
):
    """A real error (not the fsync-unsupported case) fsync'ing the parent
    directory still surfaces to the caller -- but by that point os.replace()
    has already succeeded, so unlike the pre-replace file-fsync failure
    case, the new content is genuinely on disk at the destination; only its
    directory-entry durability across a crash is unconfirmed."""
    import errno

    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    write_snapshot(snap, p)

    new_snap = AbiSnapshot(library="libbar.so.2", version="2.0")
    real_fsync = os.fsync
    real_open = os.open
    call_count = 0

    def _fail_second_fsync(fd):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # the directory fsync
            raise OSError(errno.EIO, "I/O error")
        return real_fsync(fd)

    def _passthrough_open(path, flags, *a, **kw):
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(snapshot_io_mod.os, "fsync", _fail_second_fsync)
    monkeypatch.setattr(snapshot_io_mod.os, "open", _passthrough_open)
    with pytest.raises(OSError):
        write_snapshot(new_snap, p)
    # The rename already completed -- content reflects the new write, not
    # the old one, despite the raised exception.
    assert load_snapshot(p).library == "libbar.so.2"


@pytest.mark.skipif(os.name == "nt", reason="no O_DIRECTORY on Windows")
def test_directory_open_real_failure_propagates(tmp_path, monkeypatch):
    """Codex review, PR #699: a real failure *opening* the parent directory
    (EMFILE, EIO, a permissions change mid-run) must propagate the same way
    a real fsync failure does -- an earlier version wrapped the open() call
    itself in a blanket `except OSError: dir_fd = None`, silently skipping
    the durability fsync entirely and reporting a successful write even
    when the directory couldn't be opened for a genuine, non-"unsupported"
    reason."""
    import errno

    import abicheck.snapshot_io as snapshot_io_mod

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    write_snapshot(snap, p)

    real_open = os.open

    def _fail_directory_open(path, flags, *a, **kw):
        if flags & os.O_DIRECTORY:
            raise OSError(errno.EMFILE, "Too many open files")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(snapshot_io_mod.os, "open", _fail_directory_open)
    with pytest.raises(OSError):
        write_snapshot(snap, p)


# ── Content ──────────────────────────────────────────────────────────────


def test_unicode_content_round_trips(tmp_path):
    snap = AbiSnapshot(library="libéé", version="1.0-ä")
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"u{suffix}"
        write_snapshot(snap, p)
        loaded = load_snapshot(p)
        assert loaded.library == snap.library
        assert loaded.version == snap.version


def test_empty_minimal_snapshot(tmp_path):
    snap = AbiSnapshot(library="empty", version="0")
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"e{suffix}"
        write_snapshot(snap, p)
        loaded = load_snapshot(p)
        assert loaded.library == "empty"
        assert loaded.functions == []


def test_graph_heavy_snapshot_round_trips_and_compresses_well(tmp_path):
    snap = _graph_heavy_snapshot(400)
    plain = tmp_path / "g.abicheck.json"
    zst = tmp_path / "g.abicheck.json.zst"
    write_snapshot(snap, plain)
    result = write_snapshot(snap, zst)
    loaded = load_snapshot(zst)
    assert len(loaded.functions) == 400
    # Repeated, path-heavy content compresses well -- generous bound (not the
    # tuned ~10-15% target from ADR-059's real-world benchmark) so this stays
    # robust to CPU/zstd-version noise while still catching a regression to
    # "compression barely helps at all".
    assert result.stored_size_bytes < result.decoded_size_bytes * 0.5


def test_dump_provenance_survives_load_save_round_trip(tmp_path):
    """dump_provenance (folded into the payload dict by cli_dump_helpers, not
    an AbiSnapshot field) is opaque top-level JSON -- verify the storage layer
    itself doesn't drop or corrupt an arbitrary extra top-level key across
    every encoding, independent of the CLI fold step."""
    snap = _sample_snapshot()
    payload = snapshot_to_dict(snap)
    payload["dump_provenance"] = {
        "requested_depth": "headers",
        "effective_depth": "headers",
        "degraded": False,
        "frontend": "clang",
    }
    text = json.dumps(payload, indent=2)
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"prov{suffix}"
        write_snapshot_text(text, p)
        loaded_bytes = read_snapshot_bytes(p)
        loaded_payload = json.loads(loaded_bytes)
        assert loaded_payload["dump_provenance"]["frontend"] == "clang"
        # snapshot_from_dict must not choke on the unknown extra key either.
        assert snapshot_from_dict(loaded_payload).library == snap.library


def test_pre_compression_fixture_still_loads(tmp_path):
    """A snapshot written before ADR-059 (plain UTF-8 JSON, no magic bytes)
    must keep loading with zero behavior change."""
    snap = _sample_snapshot()
    p = tmp_path / "legacy.abi.json"
    p.write_text(json.dumps(snapshot_to_dict(snap), indent=2), encoding="utf-8")
    loaded = load_snapshot(p)
    assert loaded.library == snap.library


def test_save_snapshot_legacy_positional_signature_unchanged(tmp_path):
    """save_snapshot(snap, path) -- the historical two-positional-arg call --
    must keep working exactly as before; compression is keyword-only."""
    snap = _sample_snapshot()
    p = tmp_path / "legacy_call.abicheck.json"
    save_snapshot(snap, p)  # no keyword args at all
    assert load_snapshot(p).library == snap.library


def test_write_snapshot_bytes_result_fields(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "r.abicheck.json.zst"
    result = write_snapshot(snap, p)
    assert result.path == p
    assert result.compression == SnapshotCompression.ZSTD
    assert result.decoded_size_bytes > 0
    assert result.stored_size_bytes > 0
    assert 0 < result.ratio <= 1.0
    assert result.stored_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_write_snapshot_bytes_direct(tmp_path):
    data = b'{"library": "x", "version": "1"}'
    p = tmp_path / "direct.abicheck.json.gz"
    result = write_snapshot_bytes(data, p, compression=SnapshotCompression.GZIP)
    assert result.decoded_size_bytes == len(data)
    assert read_snapshot_bytes(p) == data
