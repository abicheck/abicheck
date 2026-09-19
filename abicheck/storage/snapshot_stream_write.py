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

"""Streaming snapshot-envelope writes (the memory work, step 3).

Lives beside the document producers rather than in :mod:`abicheck.
snapshot_io` because it adds no envelope *policy*: compression resolution,
atomicity, mode/ownership preservation and the digest are all that module's,
reused unchanged. What is here is only the decision to feed them a fragment
stream instead of one buffer."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..snapshot_io import (
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    SnapshotWriteResult,
    _atomic_write_bytes,
    resolve_write_compression,
)
from .incremental_encode import encode_chunks

__all__ = ["write_snapshot_text_stream"]


def write_snapshot_text_stream(
    chunks: Iterable[str],
    path: str | Path,
    *,
    compression: SnapshotCompression = SnapshotCompression.AUTO,
    zstd_level: int | None = None,
    decoded_size: int | None = None,
) -> SnapshotWriteResult:
    """Write a fragment stream through the same chokepoint, without joining.

    Same envelope, same atomicity, same :class:`SnapshotWriteResult`
    (including ``stored_sha256``, computed over the bytes actually written)
    as :func:`write_snapshot_text` -- the difference is only that an
    *uncompressed* write never materialises the whole document as one
    ``str`` and then again as one ``bytes``. For a multi-member baseline
    those two copies sit on top of the member graph itself, so removing
    them removes a real peak, not a bookkeeping one.

    A **compressed** write no longer joins: it streams through
    :func:`~abicheck.storage.incremental_encode.encode_chunks`, so neither
    the whole JSON document nor a whole encoded copy is ever materialised.
    gzip output is byte-identical to the one-shot ``_compress_gzip``; zstd
    output is byte-identical when *decoded_size* is supplied and otherwise
    omits the frame's declared content size (a legal frame the reader
    already handles -- see that module's docstring for the tradeoff).

    The two size figures are counted independently now that they can
    differ: *decoded* bytes are summed as fragments are encoded, *stored*
    bytes as buffers are handed to the writer, and ``stored_sha256`` is
    taken over the stored side, matching :func:`write_snapshot_text`.
    """
    p = Path(path)
    resolved = resolve_write_compression(p, compression)
    level = zstd_level if zstd_level is not None else ZSTD_LEVEL_BASELINE

    digest = hashlib.sha256()
    decoded = 0
    stored = 0

    def _encoded() -> Iterator[bytes]:
        nonlocal decoded
        for chunk in chunks:
            raw = chunk.encode("utf-8")
            decoded += len(raw)
            yield raw

    def _counted() -> Iterator[bytes]:
        nonlocal stored
        for buf in encode_chunks(
            _encoded(), resolved, zstd_level=level, decoded_size=decoded_size
        ):
            digest.update(buf)
            stored += len(buf)
            yield buf

    _atomic_write_bytes(_counted(), p)
    return SnapshotWriteResult(
        path=p,
        compression=resolved,
        decoded_size_bytes=decoded,
        stored_size_bytes=stored,
        stored_sha256=digest.hexdigest(),
    )
