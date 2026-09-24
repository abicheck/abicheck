# SPDX-License-Identifier: Apache-2.0
"""Multi-threaded zstd writes are byte-identical on any core count.

Through the real write chokepoint (``encode_snapshot_bytes``) at a size past
the multi-thread threshold, varying the machine's reported CPU count; the
oracle is the one-worker frame, decoded back by the real read path.
"""

from __future__ import annotations

import json
import random

import pytest

from abicheck import snapshot_io
from abicheck.snapshot_io import (
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    encode_snapshot_bytes,
)
from abicheck.storage import zstd_compress
from abicheck.storage.zstd_compress import ZSTD_MULTITHREAD_MIN_BYTES

zstandard = pytest.importorskip("zstandard")


def _document(size: int) -> bytes:
    rng = random.Random(1234)
    rows = []
    while sum(map(len, rows)) < size:
        rows.append(
            json.dumps(
                {
                    "name": f"ns{rng.randrange(50)}::fn_{rng.randrange(10**6)}",
                    "params": [
                        f"T{rng.randrange(40)} const&" for _ in range(rng.randrange(4))
                    ],
                }
            )
        )
    return ("[" + ",".join(rows) + "]").encode()


@pytest.fixture(scope="module")
def big() -> bytes:
    return _document(ZSTD_MULTITHREAD_MIN_BYTES + 3 * 1024 * 1024)


@pytest.mark.parametrize("cpus", [1, 2, 3, 4, 8, 64, None])
def test_frame_is_independent_of_core_count(
    big: bytes, cpus: int | None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(zstd_compress.os, "cpu_count", lambda: cpus)
    frame = encode_snapshot_bytes(big, SnapshotCompression.ZSTD, zstd_level=3)
    oracle = zstandard.ZstdCompressor(
        level=3, write_checksum=False, write_content_size=True, threads=1
    ).compress(big)
    assert frame == oracle
    path = tmp_path / "snap.json.zst"
    path.write_bytes(frame)
    assert snapshot_io.read_snapshot_bytes(path) == big


def test_small_input_keeps_the_single_threaded_frame() -> None:
    small = _document(64 * 1024)
    assert encode_snapshot_bytes(
        small, SnapshotCompression.ZSTD, zstd_level=ZSTD_LEVEL_BASELINE
    ) == zstandard.ZstdCompressor(
        level=ZSTD_LEVEL_BASELINE, write_checksum=False, write_content_size=True
    ).compress(small)


def test_threshold_boundary() -> None:
    assert zstd_compress.zstd_threads(ZSTD_MULTITHREAD_MIN_BYTES - 1) == 0
    assert zstd_compress.zstd_threads(ZSTD_MULTITHREAD_MIN_BYTES) >= 1
