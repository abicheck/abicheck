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

from typing import Any

from ..errors import SnapshotError


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

    A frame header parse failure (``get_frame_parameters``/
    ``decompressobj().decompress()`` raising) is itself treated as
    corruption, not swallowed: truncating right after the 4-byte zstd
    magic makes the caller's own bounded ``stream_reader()`` pass return
    an empty payload with no error at all, so "the primary pass already
    proved it decodes cleanly" cannot be trusted to rule this out here
    -- confirmed empirically (Codex review, fresh evidence). Since the
    loop only ever calls this on a non-empty ``remaining`` (the ``while``
    guard), there is no legitimate reason for a parse to fail here.

    *data* itself must contain at least one frame -- a genuinely
    zero-byte *data* would otherwise skip this ``while`` loop entirely,
    "validating" with zero frames checked, and `ZstdCompressor.compress`
    always emits a real (non-empty) frame even for an empty payload
    (confirmed empirically), so an empty *data* can never be this
    codebase's own legitimate output (Codex review, fresh evidence)."""
    if not data:
        raise SnapshotError(f"{source}: corrupt or truncated zstd stream (no data at all)")
    remaining = data
    while remaining:
        try:
            frame_declared = zstandard.get_frame_parameters(remaining).content_size
            dobj = dctx.decompressobj()
            frame_out = dobj.decompress(remaining)
        except Exception as exc:
            raise SnapshotError(
                f"{source}: corrupt or truncated zstd stream (failed to "
                f"parse a frame header: {exc})"
            ) from exc
        if not dobj.eof or (
            frame_declared != zstandard.CONTENTSIZE_UNKNOWN
            and len(frame_out) != frame_declared
        ):
            raise SnapshotError(
                f"{source}: corrupt or truncated zstd stream (a frame "
                f"declares {frame_declared} bytes but only "
                f"{len(frame_out)} decoded)"
            )
        remaining = dobj.unused_data
