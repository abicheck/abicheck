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

"""Bounded *decoded prefix* of a snapshot storage envelope — input
classification, not storage I/O.

``snapshot_io.py`` owns moving a whole snapshot in and out of a file.
Answering "what are the first N decoded bytes of this thing, cheaply, and is
it a snapshot at all?" is a different job with a different failure mode: it
must decide against a deliberately *partial* read, so it owns an escalation
policy and a truncated-vs-corrupt distinction that the whole-file paths
never face. Split out under ADR-061's own rule that the way to shrink a
`no_growth` debt entry is to give a responsibility its own owner
(`architecture/debt.yaml`; AGENTS.md "Files that are large").

``snapshot_io`` re-exports :func:`bounded_decoded_prefix` and
:func:`_try_decode_prefix`, so every existing
``from .snapshot_io import bounded_decoded_prefix`` call site is unchanged.
This module's own imports from ``snapshot_io`` are function-local for that
reason: a module-level import in both directions is the cycle the split
would otherwise create (AGENTS.md "Don't" → ``IMPORT_CYCLE_ALLOWLIST``).
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path
from typing import TYPE_CHECKING

from .bounded_read import read_up_to
from .zstd_frame_guard import starts_with_skippable_frame_magic

if TYPE_CHECKING:
    from ..snapshot_io import SnapshotCompression


def _try_decode_prefix(
    head: bytes, compression: SnapshotCompression, n: int
) -> bytes | None:
    """One decode attempt of a raw prefix; ``None`` means "try a larger raw
    prefix" (truncated mid-frame) rather than "this is not a snapshot".

    A result of *fewer than* ``n`` bytes is not a success signal either: a
    decoder handed a frame cut at an arbitrary raw byte boundary returns a
    short result -- including ``b""`` -- without raising. Reading through
    :func:`~abicheck.storage.bounded_read.read_up_to` (which owns that rule,
    and the multi-frame case with it) makes a short result here mean the
    *stream* is exhausted; only the caller knows whether a larger raw prefix
    exists, so :func:`bounded_decoded_prefix` owns "short means truncated".
    """
    from ..snapshot_io import (
        SnapshotCompression,
        _zstd_max_window_size_bytes,
        _zstd_module,
    )

    try:
        if compression is SnapshotCompression.GZIP:
            with gzip.GzipFile(fileobj=io.BytesIO(head), mode="rb") as gz:
                return read_up_to(gz, n)
        zstandard = _zstd_module()
        dctx = zstandard.ZstdDecompressor(
            max_window_size=_zstd_max_window_size_bytes(zstandard)
        )
        with dctx.stream_reader(io.BytesIO(head)) as reader:
            return read_up_to(reader, n)
    except Exception:
        return None


def bounded_decoded_prefix(path: str | Path, n: int | None = None) -> bytes | None:
    """Return up to *n* decoded bytes of *path*, or ``None`` if it cannot be
    decoded as a snapshot storage envelope at all (corrupt, or a format this
    module doesn't recognize as plain/gzip/zstd, e.g. a `.tar.zst` archive).

    Used for input classification: distinguishing a compressed *snapshot*
    from an unrelated compressed *archive* without a full decompression.

    Reading exactly *n* raw (stored) bytes is not always enough to produce
    *n* *decoded* bytes -- for low-compression-ratio content (already-dense
    data, or a very small file whose compressor overhead dominates), a
    frame truncated at the raw-byte boundary can legitimately fail to
    decode at all (CodeRabbit review, fresh evidence). Escalating the raw
    read (doubling up to `_BOUNDED_PREFIX_MAX_RAW_BYTES`) before giving up
    still keeps this bounded and cheap for the common case (typically
    succeeds on the first, smallest attempt for real ABI snapshot JSON,
    which compresses well) while no longer misclassifying a valid but
    less-compressible compressed snapshot as unreadable.
    """
    from ..snapshot_io import (
        _BOUNDED_PREFIX_MAX_RAW_BYTES,
        _SNIFF_BYTES,
        SnapshotCompression,
        _classify_with_skippable_fallback,
        _read_past_leading_skippable_frames,
    )

    if n is None:
        n = _SNIFF_BYTES
    p = Path(path)
    try:
        with open(p, "rb") as f:
            probe = f.read(4)
            # Escalate only when ambiguous (a leading skippable-frame
            # magic) -- a plain/gzip/real-zstd-frame probe never has more
            # to find past it, so the common case pays nothing extra
            # (Codex review, fresh evidence).
            saw_skippable_magic = starts_with_skippable_frame_magic(probe)
            if saw_skippable_magic:
                probe = _read_past_leading_skippable_frames(f, probe)
            compression = _classify_with_skippable_fallback(probe, saw_skippable_magic)
            if compression is SnapshotCompression.NONE:
                more_needed = max(n, 4) - len(probe)
                if more_needed > 0:
                    probe += f.read(more_needed)
                return probe[:n]
            raw_size = max(n, len(probe))
            while True:
                f.seek(0)
                head = f.read(raw_size)
                # A short read means EOF: `head` is the entire file, so
                # no larger raw prefix exists and the decode is final.
                exhausted = len(head) < raw_size
                result = _try_decode_prefix(head, compression, n)
                # "No exception" is not "decoded everything the file has to
                # offer": a frame cut at the raw-byte boundary decodes short
                # -- commonly to zero bytes -- without raising, so escalate
                # as for a raised exception. Accepting one made a real,
                # less-compressible `.json.zst` snapshot classify as `b""`
                # and surface as "Cannot detect format".
                if result is not None and (len(result) >= n or exhausted):
                    return result
                if exhausted or raw_size >= _BOUNDED_PREFIX_MAX_RAW_BYTES:
                    # Whole file already read (genuinely corrupt, not just
                    # truncated at the boundary), or the cap was reached:
                    # return the best-effort short decode rather than
                    # discarding real decoded content.
                    return result
                raw_size = min(raw_size * 4, _BOUNDED_PREFIX_MAX_RAW_BYTES)
    except OSError:
        return None
