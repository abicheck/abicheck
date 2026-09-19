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

"""Chunk-at-a-time gzip/zstd encoding for the streaming snapshot write.

``write_snapshot_text_stream`` avoided materialising a whole document for an
*uncompressed* write and then, for a compressed one, did ``"".join(chunks)``
and handed the whole buffer to :mod:`abicheck.snapshot_io`'s one-shot
codecs -- so a compressed baseline still peaked at the full JSON string plus
its encoded copy, on top of the member graphs that produced it. These
encoders consume the same fragment stream and emit compressed output as they
go, so the peak is one fragment plus the codec's own window, not the
document.

**gzip is byte-identical to the one-shot path.** The frame is assembled
here rather than via :mod:`gzip` so every header byte is pinned explicitly:
``mtime=0`` and ``OS=0xFF`` are the same two normalisations
``snapshot_io._compress_gzip`` applies (see its comment for why the OS byte
cannot be left to CPython's version-dependent framing), and the payload is
raw deflate from one :func:`zlib.compressobj` at the same level, which
zlib emits identically regardless of how the input was chunked. Verified
differentially against ``gzip.compress`` in
``tests/test_incremental_compression.py``, including at chunk size 1.

**zstd is byte-identical when the decoded size is known up front, and
omits the frame's content-size field when it is not.** ``ZstdCompressor.
chunker(size=...)`` reproduces the one-shot frame exactly; without a size
the frame header simply carries no declared content size, which is a
legal, fully supported zstd frame that ``snapshot_io._decompress_zstd``
already reads (``validate_zstd_frame_completeness`` handles
``CONTENTSIZE_UNKNOWN`` explicitly). The tradeoff is real and is stated
rather than hidden: a size-unknown frame loses the *declared-size*
cross-check that catches a frame truncated mid-header, so a caller that
can cheaply state the total should, via *decoded_size*. Output is
deterministic either way -- the same fragments always produce the same
bytes.

Neither encoder accumulates its own output: both are generators, and the
atomic writer consumes them one buffer at a time.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable, Iterator

from ..errors import SnapshotError
from ..snapshot_io import (
    GZIP_COMPRESSLEVEL,
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    _zstd_module,
)

__all__ = ["encode_chunks"]

#: gzip member header: magic, CM=deflate, FLG=0, MTIME=0, XFL, OS.
#: ``XFL=2`` is "maximum compression", which is what ``compresslevel=9``
#: makes CPython's own :mod:`gzip` emit; ``OS=0xFF`` is the normalisation
#: ``snapshot_io._compress_gzip`` patches in after the fact.
_GZIP_XFL_MAX_COMPRESSION = 2
_GZIP_OS_UNKNOWN = 0xFF


def _gzip_header(level: int) -> bytes:
    xfl = _GZIP_XFL_MAX_COMPRESSION if level >= 9 else (4 if level == 1 else 0)
    return struct.pack(
        "<BBBBIBB", 0x1F, 0x8B, 8, 0, 0, xfl, _GZIP_OS_UNKNOWN
    )


def _gzip_chunks(chunks: Iterable[bytes], *, level: int) -> Iterator[bytes]:
    yield _gzip_header(level)
    compressor = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
    crc = 0
    size = 0
    for chunk in chunks:
        if not chunk:
            continue
        crc = zlib.crc32(chunk, crc)
        size += len(chunk)
        out = compressor.compress(chunk)
        if out:
            yield out
    tail = compressor.flush()
    if tail:
        yield tail
    # ISIZE is the decoded length modulo 2**32 -- the format's own field
    # width, not a truncation this code chose.
    yield struct.pack("<II", crc & 0xFFFFFFFF, size & 0xFFFFFFFF)


def _zstd_chunks(
    chunks: Iterable[bytes], *, level: int, decoded_size: int | None
) -> Iterator[bytes]:
    zstandard = _zstd_module()
    cctx = zstandard.ZstdCompressor(
        level=level,
        write_checksum=False,
        write_content_size=True,
    )
    chunker = cctx.chunker() if decoded_size is None else cctx.chunker(size=decoded_size)
    produced = 0
    for chunk in chunks:
        if not chunk:
            continue
        produced += len(chunk)
        yield from chunker.compress(chunk)
    if decoded_size is not None and produced != decoded_size:
        # Better a hard error than a frame whose declared content size
        # disagrees with its payload: that frame would decompress and then
        # fail the reader's own completeness cross-check, which reports
        # "corrupt or truncated" -- blaming storage for a producer bug.
        raise SnapshotError(
            f"streaming zstd write: declared decoded size {decoded_size} but "
            f"the fragment stream produced {produced} bytes"
        )
    yield from chunker.finish()


def encode_chunks(
    chunks: Iterable[bytes],
    compression: SnapshotCompression,
    *,
    zstd_level: int = ZSTD_LEVEL_BASELINE,
    decoded_size: int | None = None,
) -> Iterator[bytes]:
    """Encode a fragment stream under *compression*, incrementally.

    Yields storage-ready buffers. *decoded_size*, when known, is passed to
    zstd so the frame declares its content size exactly as the one-shot
    path does; it is unused by gzip, whose ISIZE trailer is accumulated as
    the stream is consumed. ``NONE`` passes fragments straight through, so
    one caller can hold one loop for all three envelopes.
    """
    if compression is SnapshotCompression.NONE:
        return (c for c in chunks if c)
    if compression is SnapshotCompression.GZIP:
        return _gzip_chunks(chunks, level=GZIP_COMPRESSLEVEL)
    if compression is SnapshotCompression.ZSTD:
        return _zstd_chunks(chunks, level=zstd_level, decoded_size=decoded_size)
    raise SnapshotError(f"Cannot encode with compression={compression!r}")
