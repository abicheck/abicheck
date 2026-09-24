# SPDX-License-Identifier: Apache-2.0
"""Opt-in multi-threaded zstd writes are byte-identical for any worker count.

Through the real write chokepoint (``encode_snapshot_bytes``) at a size past
the multi-thread threshold, varying the machine's reported CPU count; the
oracle is the one-worker frame, decoded back by the real read path.
"""

from __future__ import annotations

import json
import random
import sys

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

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="multi-threaded zstd is disabled on Windows (see zstd_compress docstring)",
)


def _document(size: int) -> bytes:
    """Deterministic snapshot-shaped JSON of at least *size* bytes."""
    rng = random.Random(1234)
    rows: list[str] = []
    total = 0
    while total < size:
        row = json.dumps(
            {
                "name": f"ns{rng.randrange(50)}::fn_{rng.randrange(10**6)}",
                "params": [
                    f"T{rng.randrange(40)} const&" for _ in range(rng.randrange(4))
                ],
            }
        )
        rows.append(row)
        total += len(row) + 1
    return ("[" + ",".join(rows) + "]").encode()


@pytest.fixture(scope="module")
def big() -> bytes:
    return _document(ZSTD_MULTITHREAD_MIN_BYTES + 3 * 1024 * 1024)


@_POSIX_ONLY
@pytest.mark.parametrize("workers", [1, 2, 3, 4, 8, 64])
def test_frame_is_independent_of_worker_count(
    big: bytes, workers: int, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv(zstd_compress.ZSTD_THREADS_ENV, str(workers))
    assert zstd_compress.zstd_threads(len(big)) == min(
        workers, zstd_compress.ZSTD_MAX_THREADS
    )
    frame = encode_snapshot_bytes(big, SnapshotCompression.ZSTD, zstd_level=3)
    oracle = zstandard.ZstdCompressor(
        level=3, write_checksum=False, write_content_size=True, threads=1
    ).compress(big)
    assert frame == oracle
    path = tmp_path / "snap.json.zst"
    path.write_bytes(frame)
    assert snapshot_io.read_snapshot_bytes(path) == big


def test_default_is_single_threaded(
    big: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not opted in: the frame every earlier release wrote, on every platform."""
    monkeypatch.delenv(zstd_compress.ZSTD_THREADS_ENV, raising=False)
    assert zstd_compress.zstd_threads(len(big)) == 0
    assert encode_snapshot_bytes(big, SnapshotCompression.ZSTD, zstd_level=3) == (
        zstandard.ZstdCompressor(
            level=3, write_checksum=False, write_content_size=True
        ).compress(big)
    )


def test_small_input_keeps_the_single_threaded_frame() -> None:
    small = _document(64 * 1024)
    assert encode_snapshot_bytes(
        small, SnapshotCompression.ZSTD, zstd_level=ZSTD_LEVEL_BASELINE
    ) == zstandard.ZstdCompressor(
        level=ZSTD_LEVEL_BASELINE, write_checksum=False, write_content_size=True
    ).compress(small)


@pytest.mark.parametrize("value", ["", "0", "-2", "abc"])
def test_invalid_or_zero_opt_in_stays_single_threaded(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(zstd_compress.ZSTD_THREADS_ENV, value)
    assert zstd_compress.zstd_threads(ZSTD_MULTITHREAD_MIN_BYTES) == 0


@_POSIX_ONLY
def test_threshold_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(zstd_compress.ZSTD_THREADS_ENV, "4")
    assert zstd_compress.zstd_threads(ZSTD_MULTITHREAD_MIN_BYTES - 1) == 0
    assert zstd_compress.zstd_threads(ZSTD_MULTITHREAD_MIN_BYTES) == 4
