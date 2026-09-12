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

"""Allocation invariant for the snapshot storage reader.

Bug class: a safety *limit* used as a read *size*. ``read_snapshot_bytes``
read the whole file with ``f.read(cap + 1 - len(prefix))``, where ``cap`` is
the gigabyte-scale decoded/stored safety ceiling — so reading an 8 KiB
snapshot asked the buffered reader for ~1 GiB up front. Linux's
overcommitting allocator hid it; Windows commits and zeroes, which is why
the stored-package comparison lanes dominated the Windows CI wall clock
(one test: 118.03s before, 1.80s after, assertions unchanged).

The invariant tested here is deliberately *not* "the reader uses a 1 MiB
chunk" — that would re-assert the implementation's own constant, the
tautology this repository's own test-quality guidance (AGENTS.md,
"Third-party-boundary tests ...") names as the reason the ADR-059 §12
window-size regression survived. The oracle is independent of any formula
in the module: **peak allocation during a read is a function of the file's
content size, not of the safety cap.** Vary the cap across four orders of
magnitude with the content fixed and the peak must stay put; vary the
content with the cap fixed and the peak may grow with it. Both directions
are checked, for every supported storage format, at several sizes — the
original expression fails the first direction for all of them.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from abicheck.snapshot_io import (
    SnapshotCompression,
    read_snapshot_bytes,
    write_snapshot_bytes,
)

# Slack absorbing the decoded buffer, the join, and interpreter noise. Far
# below the gigabyte-scale caps under test, so it cannot mask a peak that
# actually tracks the cap.
_SLACK_BYTES = 16 * 1024 * 1024

_COMPRESSIONS = (
    SnapshotCompression.NONE,
    SnapshotCompression.GZIP,
    SnapshotCompression.ZSTD,
)

# Spans four orders of magnitude and both sides of the reader's internal
# chunking, without naming it.
_CAPS = (
    4 * 1024 * 1024,
    64 * 1024 * 1024,
    1024 * 1024 * 1024,
    2 * 1024 * 1024 * 1024,
)

_CONTENT_SIZES = (512, 8 * 1024, 256 * 1024)


def _payload(size: int) -> bytes:
    """Deterministic, poorly-compressible-enough JSON-ish content of *size*."""
    filler = "".join(chr(0x61 + (i * 7) % 26) for i in range(max(size, 1)))
    blob = ('{"v": "' + filler + '"}').encode()
    return blob[:size] if len(blob) >= size else blob + b" " * (size - len(blob))


def _written(tmp_path: Path, content: bytes, compression: SnapshotCompression) -> Path:
    suffix = {
        SnapshotCompression.NONE: ".json",
        SnapshotCompression.GZIP: ".json.gz",
        SnapshotCompression.ZSTD: ".json.zst",
    }[compression]
    path = tmp_path / f"snap-{len(content)}{suffix}"
    write_snapshot_bytes(content, path, compression=compression)
    return path


def _peak_bytes(path: Path, cap: int) -> int:
    read_snapshot_bytes(path, max_decoded_bytes=cap)  # warm any lazy imports
    tracemalloc.start()
    try:
        read_snapshot_bytes(path, max_decoded_bytes=cap)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


@pytest.mark.parametrize("compression", _COMPRESSIONS, ids=lambda c: c.value)
@pytest.mark.parametrize("content_size", _CONTENT_SIZES)
def test_read_peak_allocation_does_not_track_the_safety_cap(
    tmp_path: Path,
    compression: SnapshotCompression,
    content_size: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peak allocation is flat as the cap grows by >2 GiB.

    This is the direction the bug broke: with ``f.read(cap + 1 - ...)`` the
    peak rose in lockstep with ``cap``, so the largest case allocated ~2 GiB
    to read a half-kilobyte file.
    """
    path = _written(tmp_path, _payload(content_size), compression)
    peaks = []
    for cap in _CAPS:
        # A compressed file's stored-size ceiling is the orthogonal knob;
        # move both so no format is silently exempt from the sweep.
        monkeypatch.setenv("_ABICHECK_SNAPSHOT_MAX_STORED_BYTES", str(cap))
        peaks.append(_peak_bytes(path, cap))

    spread = max(peaks) - min(peaks)
    assert spread < _SLACK_BYTES, (
        f"peak allocation tracks the safety cap ({compression.value}, "
        f"{content_size}B content): caps {_CAPS} -> peaks {peaks}"
    )
    # And the absolute peak stays proportionate to the file, not the cap.
    assert max(peaks) < content_size + _SLACK_BYTES


@pytest.mark.parametrize("compression", _COMPRESSIONS, ids=lambda c: c.value)
def test_read_peak_allocation_tracks_content_not_cap(
    tmp_path: Path, compression: SnapshotCompression
) -> None:
    """The complement: with the cap fixed, a much larger file may allocate
    more. Without this, a reader that allocated a constant tiny buffer and
    silently truncated would pass the test above."""
    cap = _CAPS[-1]
    small = _written(tmp_path, _payload(512), compression)
    large_content = _payload(4 * 1024 * 1024)
    large = _written(tmp_path, large_content, compression)

    assert read_snapshot_bytes(large, max_decoded_bytes=cap) == large_content
    assert _peak_bytes(large, cap) > _peak_bytes(small, cap)


@pytest.mark.parametrize("compression", _COMPRESSIONS, ids=lambda c: c.value)
@pytest.mark.parametrize("content_size", _CONTENT_SIZES)
def test_bounded_read_round_trips_every_format_and_size(
    tmp_path: Path, compression: SnapshotCompression, content_size: int
) -> None:
    """Content equality across the same sweep — the chunked read must join
    its chunks in order and lose nothing at a chunk boundary."""
    content = _payload(content_size)
    path = _written(tmp_path, content, compression)
    assert read_snapshot_bytes(path) == content


@pytest.mark.parametrize("compression", _COMPRESSIONS, ids=lambda c: c.value)
def test_oversized_file_is_still_rejected(
    tmp_path: Path, compression: SnapshotCompression, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-byte overshoot the single-shot read provided is preserved:
    a file past the cap raises rather than being silently truncated."""
    content = _payload(256 * 1024)
    path = _written(tmp_path, content, compression)
    tiny = 1024
    monkeypatch.setenv("_ABICHECK_SNAPSHOT_MAX_STORED_BYTES", str(tiny))
    with pytest.raises(Exception):
        read_snapshot_bytes(path, max_decoded_bytes=tiny)
