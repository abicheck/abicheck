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

Split out of `snapshot_io` (at its ADR-061 no-growth line baseline). Inputs
of :data:`ZSTD_MULTITHREAD_MIN_BYTES` or more use zstd's multi-threaded
mode. zstd documents that mode's output as independent of the worker count
(any ``nbWorkers >= 1`` yields the same frame), so stored bytes stay the
same across machines with different core counts; only the single-threaded
mode (``threads=0``) differs, which is why a smaller input keeps it and its
bytes are unchanged from earlier releases. Level 19 over oneDAL's 203 MB
snapshot: 33.9 s single-threaded, 21.6 s with four workers on a four-core box
already busy with a test run, 0.05% larger.
"""

from __future__ import annotations

import os
from typing import Any

#: Inputs at least this large are compressed multi-threaded.
ZSTD_MULTITHREAD_MIN_BYTES = 8 * 1024 * 1024

#: Cap on workers. Level-19 jobs span several window sizes each, so a
#: ~200 MB input has only a handful to spread; more workers add memory.
ZSTD_MAX_THREADS = 8


def zstd_threads(size: int) -> int:
    """The ``threads`` argument for compressing *size* bytes."""
    if size < ZSTD_MULTITHREAD_MIN_BYTES:
        return 0
    return max(1, min(os.cpu_count() or 1, ZSTD_MAX_THREADS))


def compress_zstd(zstandard: Any, data: bytes, *, level: int) -> bytes:
    """*data* as one zstd frame: content size recorded, no checksum."""
    cctx = zstandard.ZstdCompressor(
        level=level,
        write_checksum=False,
        write_content_size=True,
        threads=zstd_threads(len(data)),
    )
    return bytes(cctx.compress(data))
