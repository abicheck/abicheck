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
    SnapshotCompression,
    SnapshotWriteResult,
    _atomic_write_bytes,
    resolve_write_compression,
    write_snapshot_text,
)

__all__ = ["write_snapshot_text_stream"]


def write_snapshot_text_stream(
    chunks: Iterable[str],
    path: str | Path,
    *,
    compression: SnapshotCompression = SnapshotCompression.AUTO,
    zstd_level: int | None = None,
) -> SnapshotWriteResult:
    """Write a fragment stream through the same chokepoint, without joining.

    Same envelope, same atomicity, same :class:`SnapshotWriteResult`
    (including ``stored_sha256``, computed over the bytes actually written)
    as :func:`write_snapshot_text` -- the difference is only that an
    *uncompressed* write never materialises the whole document as one
    ``str`` and then again as one ``bytes``. For a multi-member baseline
    those two copies sit on top of the member graph itself, so removing
    them removes a real peak, not a bookkeeping one.

    A **compressed** write joins and delegates, deliberately and visibly:
    both codecs here are one-shot (``_compress_gzip``/``_compress_zstd``
    take and return whole buffers, and the deterministic-output guarantees
    documented on them are stated for that form), so streaming into them
    would mean a second compression path with its own determinism story to
    keep in step. That is a real remaining gap rather than a hidden one --
    the *default* baseline path is uncompressed, which is the one measured.
    """
    p = Path(path)
    resolved = resolve_write_compression(p, compression)
    if resolved is not SnapshotCompression.NONE:
        return write_snapshot_text(
            "".join(chunks), p, compression=compression, zstd_level=zstd_level
        )

    digest = hashlib.sha256()
    written = 0

    def _counted() -> Iterator[bytes]:
        nonlocal written
        for chunk in chunks:
            encoded = chunk.encode("utf-8")
            digest.update(encoded)
            written += len(encoded)
            yield encoded

    _atomic_write_bytes(_counted(), p)
    return SnapshotWriteResult(
        path=p,
        compression=resolved,
        # Uncompressed: stored bytes *are* the decoded bytes, which is what
        # makes one running digest sufficient for both figures.
        decoded_size_bytes=written,
        stored_size_bytes=written,
        stored_sha256=digest.hexdigest(),
    )
