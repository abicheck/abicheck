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

"""Shared zstd frame-completeness validation.

Extracted from `snapshot_io._decompress_zstd`'s own third validation pass
(`abicheck/snapshot_io.py` is at its ADR-061 no-growth line-count baseline,
so the logic moved here rather than growing that file) so a second caller
-- `abicheck/storage/bundle_archive.py`'s `read_blob()` -- can share it
without either duplicating this already-three-times-review-corrected logic
or importing a private helper whose own window-size/decompression policy
it does not want to inherit.
"""

from __future__ import annotations

import struct
from typing import Any

from ..errors import SnapshotError

#: Zstandard "skippable frame" magic range (format spec): the low nibble
#: is a free-form Magic_Number_Value, all 16 values are legal skippable-
#: frame markers. `get_frame_parameters()`/`decompressobj()` don't
#: recognize these at all -- they'd misread the frame's own 4-byte
#: Frame_Size field as a bogus content-size declaration (Codex review,
#: fresh evidence) -- so they must be detected and skipped explicitly.
_SKIPPABLE_FRAME_MAGIC_LOW = 0x184D2A50
_SKIPPABLE_FRAME_MAGIC_HIGH = 0x184D2A5F

#: Real-data-frame chunk-feed sizing: start small so a stream of many tiny
#: real frames (the common minimal-payload case) reaches `dobj.eof` within
#: the first chunk, bounding `decompressobj().unused_data`'s own copy to
#: roughly one chunk's worth rather than the entire unread tail; grow
#: geometrically so a single large frame still finishes in O(log) calls
#: rather than one call per few hundred bytes (Codex review, fresh
#: evidence -- see `validate_zstd_frame_completeness`'s own docstring).
_INITIAL_FRAME_CHUNK_BYTES = 256
_MAX_FRAME_CHUNK_BYTES = 1 << 20


def validate_zstd_frame_completeness(
    zstandard: Any, dctx: Any, data: bytes, *, source: str
) -> None:
    """Raise `SnapshotError` if any zstd frame in *data* decoded to fewer
    bytes than its own declared content size, or didn't reach its own end
    at all -- a truncated frame can otherwise decompress with no error,
    silently yielding fewer bytes than intended instead of raising
    (confirmed against a real truncated frame, Codex review).

    Cross-checks against each frame's own declared content size (present
    on every frame *this codebase's* writers produce, via
    ``write_content_size=True``, zstd's own default) using
    ``decompressobj()``'s per-frame boundary tracking (``.eof``/
    ``.unused_data``) rather than trusting a single ``stream_reader()``
    pass alone -- confirmed empirically: ``.eof`` is `False` exactly when
    a frame's decompression didn't reach its own end.

    Walks every frame independently (not just the first): a valid zstd
    stream may legitimately be multiple concatenated frames, and checking
    only the first frame's declared size against the aggregate decoded
    total can miss a truncated *later* frame entirely (Codex review, two
    rounds -- see `snapshot_io.py`'s own git history for the two
    counterexamples that forced this). `.eof` is checked unconditionally,
    independent of whether a frame declares its size at all: a frame with
    no declared size (``CONTENTSIZE_UNKNOWN``, only possible for a foreign
    encoder) used to abandon validation for that frame *and every
    subsequent one*, letting a truncated later frame through unchecked
    (Codex review, third round).

    Memory safety: ``decompressobj().decompress()`` has no output-size cap
    the way a bounded chunked ``stream_reader()`` read does -- feeding it
    even a tiny sliver of a highly-compressible frame's compressed bytes
    can fully materialize that frame's entire decoded output in one call.
    Safe here only because the caller must call this *after* its own
    bounded primary decompression pass already completed successfully, so
    the total decoded content across every frame is already known to fit
    within whatever cap that pass enforced -- redecompressing the same,
    already-proven-bounded data a second time for validation cannot exceed
    that same total, whatever this loop's own call granularity is.

    Known, accepted trade-off (Codex review, fresh evidence): the two
    passes' full outputs -- the caller's own already-materialized primary
    result and this call's own current-frame ``frame_out`` -- can be
    alive at once, so peak transient memory for a near-cap single-frame
    blob (this module's own writer never splits one payload across
    multiple frames) approaches 2x the configured cap, not 1x. Running
    this validation *before* the bounded primary pass to avoid the
    overlap was considered and rejected: that would mean calling this
    function's own unbounded ``decompress()`` on a not-yet-proven-safe
    input, reintroducing the exact unbounded-decompression-bomb risk the
    ordering above exists to prevent -- a materially worse failure mode
    than a transient 2x memory ceiling. ``python-zstandard``'s
    ``decompressobj().decompress()`` has no ``max_length``/chunked
    variant (confirmed empirically) to decode a single frame in bounded
    increments the way ``stream_reader()`` does for the whole stream, so
    closing this without either regressing that ordering or a materially
    larger redesign merging both passes into one is not attempted here.
    A caller sizing ``max_decoded_bytes`` should budget for roughly
    double that value as the real peak, not the nominal cap alone.

    A frame header parse failure (``get_frame_parameters``/
    ``decompressobj().decompress()`` raising) is itself treated as
    corruption, not swallowed: truncating right after the 4-byte zstd
    magic makes the caller's own bounded ``stream_reader()`` pass return
    an empty payload with no error at all, so "the primary pass already
    proved it decodes cleanly" cannot be trusted to rule this out here
    -- confirmed empirically (Codex review, fresh evidence). Since the
    loop only ever calls this on a non-empty ``remaining`` (the ``while``
    guard), there is no legitimate reason for a parse to fail here.

    *data* itself must contain at least one real *data* frame -- a
    skippable frame contributes no decoded content (confirmed
    empirically) and `ZstdCompressor.compress` always emits a real
    (non-empty) data frame even for an empty payload, so *data* made up
    of nothing but skippable frames (including the zero-byte case) can
    never be this codebase's own legitimate output. Checked once, after
    the walk, rather than up front -- a leading skippable frame ahead of
    a real one is legitimate and must not be rejected (Codex review,
    fresh evidence, two rounds: the first fix skipped skippable frames
    but didn't also re-check this invariant afterward).

    Advances via a zero-copy ``memoryview`` rather than re-slicing
    ``bytes`` on every skippable-frame iteration: a plain ``remaining =
    remaining[total:]`` on ``bytes`` copies the entire unread suffix each
    time, making the walk quadratic in stored size -- confirmed
    empirically at ~11s for 200,000 tiny skippable frames (~1.6 MiB)
    before this fix, a real DoS vector given the archive reader permits
    stored blobs near 1 GiB (Codex review, fresh evidence).
    ``struct.unpack_from``/``get_frame_parameters``/``decompressobj().
    decompress()`` all accept a ``memoryview`` directly (confirmed
    empirically), so no data frame's own decompression pays a conversion
    cost either. ``decompressobj().unused_data`` still returns a fresh
    ``bytes`` copy of whatever follows the frame it consumed -- rewrapped
    in a ``memoryview`` immediately so a real data frame followed by more
    skippable frames doesn't reintroduce the same quadratic slicing.

    The real-data-frame path has its own, independent copy of the same
    quadratic shape: feeding a whole (potentially huge) ``remaining``
    memoryview to ``decompressobj().decompress()`` in one call makes
    ``.unused_data`` materialize a fresh ``bytes`` copy of *everything*
    after the frame, so a stream of many small real data frames still
    walked in O(n^2) -- confirmed empirically at ~8s for 160,000 empty
    data frames (~1.4 MiB) before this fix (Codex review, fresh
    evidence). Fixed by feeding each frame incrementally in small,
    geometrically-growing chunks and stopping as soon as
    ``decompressobj.eof`` flips: ``.unused_data`` then only ever holds
    the tail of the *last chunk fed*, not the whole remaining stream, so
    its copy cost is bounded by chunk size rather than input size.
    ``python-zstandard`` supports feeding one frame across multiple
    ``decompress()`` calls on the same ``decompressobj()`` (confirmed
    empirically) -- ``.eof``/``.unused_data`` reflect the position
    reached across all calls, not just the most recent one."""
    remaining = memoryview(data)
    saw_data_frame = False
    while remaining:
        if len(remaining) >= 4:
            (magic,) = struct.unpack_from("<I", remaining, 0)
            if _SKIPPABLE_FRAME_MAGIC_LOW <= magic <= _SKIPPABLE_FRAME_MAGIC_HIGH:
                if len(remaining) < 8:
                    raise SnapshotError(
                        f"{source}: corrupt or truncated zstd stream (a "
                        "skippable frame header is itself truncated)"
                    )
                (frame_size,) = struct.unpack_from("<I", remaining, 4)
                total = 8 + frame_size
                if len(remaining) < total:
                    raise SnapshotError(
                        f"{source}: corrupt or truncated zstd stream (a "
                        f"skippable frame declares {frame_size} bytes of "
                        f"user data but only {len(remaining) - 8} remain)"
                    )
                remaining = remaining[total:]  # zero-copy memoryview slice
                continue
        total_len = len(remaining)
        try:
            frame_declared = zstandard.get_frame_parameters(remaining).content_size
            dobj = dctx.decompressobj()
            frame_out_len = 0
            consumed = 0
            chunk_size = _INITIAL_FRAME_CHUNK_BYTES
            while not dobj.eof and consumed < total_len:
                end = min(consumed + chunk_size, total_len)
                frame_out_len += len(dobj.decompress(remaining[consumed:end]))
                consumed = end
                chunk_size = min(chunk_size * 4, _MAX_FRAME_CHUNK_BYTES)
        except Exception as exc:
            raise SnapshotError(
                f"{source}: corrupt or truncated zstd stream (failed to "
                f"parse a frame header: {exc})"
            ) from exc
        if not dobj.eof or (
            frame_declared != zstandard.CONTENTSIZE_UNKNOWN
            and frame_out_len != frame_declared
        ):
            raise SnapshotError(
                f"{source}: corrupt or truncated zstd stream (a frame "
                f"declares {frame_declared} bytes but only "
                f"{frame_out_len} decoded)"
            )
        saw_data_frame = True
        # .unused_data is bounded by the last chunk fed, not the whole
        # remaining stream -- slice the *original* memoryview by position
        # rather than rewrapping .unused_data itself, so this stays
        # zero-copy for the (common) case where the frame consumed
        # everything and there is nothing left to slice.
        remaining = remaining[consumed - len(dobj.unused_data) :]
    if not saw_data_frame:
        raise SnapshotError(f"{source}: corrupt or truncated zstd stream (no data frame at all)")


def skip_leading_skippable_frames(data: bytes) -> bytes:
    """Return *data* with any leading, well-formed zstd skippable frames
    stripped off -- so a *format-detection* magic-byte check sees the real
    frame's own magic instead of a skippable metadata frame's (Codex
    review, fresh evidence: an externally produced ``.json.zst`` starting
    with one skippable frame was classified as uncompressed, since a bare
    4-byte prefix check never looks past it).

    Detection-only: a truncated/malformed skippable frame is left alone
    rather than raising -- that is `validate_zstd_frame_completeness`'s
    job, once real decompression is attempted, not this cheap pre-check's.

    Advances via an integer cursor into *data* -- never re-slicing it
    inside the loop -- and slices exactly once at the end, so a stream of
    many small leading skippable frames stays linear rather than
    quadratic (mirrors `validate_zstd_frame_completeness`'s own zero-copy
    advancement; a bare `remaining = remaining[total:]` on `bytes` inside
    this loop copied the entire unread tail every iteration, ~11s for
    200,000 zero-length frames confirmed empirically, Codex review)."""
    length = len(data)
    pos = 0
    while length - pos >= 8:
        (magic,) = struct.unpack_from("<I", data, pos)
        if not (_SKIPPABLE_FRAME_MAGIC_LOW <= magic <= _SKIPPABLE_FRAME_MAGIC_HIGH):
            break
        (frame_size,) = struct.unpack_from("<I", data, pos + 4)
        total = 8 + frame_size
        if length - pos < total:
            break  # truncated -- leave it for the real decode path to report
        pos += total
    return data[pos:]


def starts_with_skippable_frame_magic(prefix: bytes) -> bool:
    """Whether *prefix*'s first 4 bytes are a zstd skippable-frame magic --
    the fast, no-I/O check a caller uses to decide whether it's worth
    reading further before giving up on a small fixed-size probe (only a
    skippable-frame-prefixed stream can have more to find past it; a
    plain/gzip/real-zstd-frame prefix never does, so callers skip the
    escalated read entirely for the overwhelmingly common case)."""
    if len(prefix) < 4:
        return False
    (magic,) = struct.unpack_from("<I", prefix, 0)
    return bool(_SKIPPABLE_FRAME_MAGIC_LOW <= magic <= _SKIPPABLE_FRAME_MAGIC_HIGH)


#: Default step/cap for `read_past_leading_skippable_frames()` -- matches
#: `snapshot_io.py`'s own `_SNIFF_BYTES`/`_BOUNDED_PREFIX_MAX_RAW_BYTES`
#: bounded-prefix-sniffing conventions, shared here so every caller (not
#: just that module) reads the same bounded amount by default.
DEFAULT_PROBE_STEP_BYTES = 4096
DEFAULT_PROBE_MAX_BYTES = 1024 * 1024  # 1 MiB


def read_past_leading_skippable_frames(
    f: Any,
    prefix: bytes,
    *,
    step: int = DEFAULT_PROBE_STEP_BYTES,
    cap: int = DEFAULT_PROBE_MAX_BYTES,
) -> bytes:
    """Given *prefix* (a small already-read run of bytes from the START of
    *f*, an open, forward-readable file-like object) that starts with a
    zstd skippable-frame magic, keep reading additional bytes from *f*
    (appended, never re-reading what's already been consumed -- safe for
    a non-seekable stream too) until `skip_leading_skippable_frames`
    finds either real content past every leading skippable frame, or
    *cap* total bytes have been read.

    Shared by every caller that needs to see past a leading skippable
    frame before classifying a small, bounded prefix -- `snapshot_io.py`'s
    compression probes and `bundle_archive.py`'s own format sniff alike
    (Codex review, fresh evidence on both). Callers check
    `starts_with_skippable_frame_magic(prefix)` first and only call this
    when it's true -- a plain/gzip/real-zstd-frame/zip prefix never has
    more to find past it, so the overwhelmingly common case never pays
    for this escalated read at all."""
    buf = prefix
    chunk_size = max(len(buf), step)
    while len(buf) < cap:
        skipped = skip_leading_skippable_frames(buf)
        # A *non-empty* result that no longer starts with a skippable
        # magic is resolved -- real content (or genuinely non-magic
        # bytes) was found. An *empty* result means the buffer ended
        # exactly on a skippable-frame boundary -- ambiguous (there may
        # be more just past it), so keep reading rather than treating it
        # as "nothing left" prematurely.
        if skipped and not starts_with_skippable_frame_magic(skipped):
            break
        more = f.read(min(chunk_size, cap - len(buf)))
        if not more:
            break  # true EOF -- nothing more to read
        buf += more
        chunk_size = min(chunk_size * 4, cap)
    return buf
