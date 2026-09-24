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

"""zstd compression for the snapshot write path, and its worker policy.

Split out of `snapshot_io` (at its ADR-061 no-growth line baseline).

zstd's multi-threaded mode is **opt-in**, via ``ABICHECK_ZSTD_THREADS=N``.
Its frame is independent of the worker count (any ``nbWorkers >= 1`` yields
the same frame) but differs from the single-threaded frame, so enabling it
by default would make snapshot bytes depend on whether a machine took the
multi-threaded path. It stays off on Windows: the first Windows CI run of
its tests lost its xdist workers, most likely to a (since fixed) quadratic
fixture hitting pytest-timeout rather than to zstd, but that was never
isolated. Level 19 over oneDAL's 203 MB snapshot: 33.9 s
single-threaded, 21.6 s with four workers on a busy four-core Linux box,
0.05% larger -- worth it for a large-snapshot publisher that opts in.
"""

from __future__ import annotations

import os
import sys
from typing import Any

#: Inputs at least this large may be compressed multi-threaded (when opted
#: in); a small frame is not worth a worker pool.
ZSTD_MULTITHREAD_MIN_BYTES = 8 * 1024 * 1024

#: Cap on workers. Level-19 jobs span several window sizes each, so a
#: ~200 MB input has only a handful to spread; more workers add memory.
ZSTD_MAX_THREADS = 8

#: The opt-in: a positive worker count (capped at `ZSTD_MAX_THREADS`).
ZSTD_THREADS_ENV = "ABICHECK_ZSTD_THREADS"


def zstd_threads(size: int) -> int:
    """The ``threads`` argument for compressing *size* bytes: ``0``
    (single-threaded) unless opted in, off Windows, and large enough."""
    try:
        requested = int(os.environ.get(ZSTD_THREADS_ENV, "0"))
    except ValueError:
        requested = 0
    if requested <= 0 or sys.platform == "win32" or size < ZSTD_MULTITHREAD_MIN_BYTES:
        return 0
    return min(requested, ZSTD_MAX_THREADS)


def compress_zstd(zstandard: Any, data: bytes, *, level: int) -> bytes:
    """*data* as one zstd frame: content size recorded, no checksum."""
    cctx = zstandard.ZstdCompressor(
        level=level,
        write_checksum=False,
        write_content_size=True,
        threads=zstd_threads(len(data)),
    )
    return bytes(cctx.compress(data))
