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

"""Open a PE image for reading without pefile's forced garbage collection.

``pefile.PE.close()`` ends in an unconditional ``gc.collect()``: a full
collection of the whole host process on every PE file read. In a process
holding large snapshots -- a release compare, a typed-API host, a test
session -- that is tens of milliseconds per read, and a compare reads each
DLL more than once (exports, the CodeView PDB reference, consumer imports).
On the Windows integration lane it was a quarter of a live compare's time.

:func:`open_pe` maps the file read-only itself and hands pefile the mapping
as ``data``, so the mapping's lifetime is this context's and pefile's
``close()`` -- the only path to its collection besides a failed parse -- is
never needed. Memory behaviour is unchanged: pefile maps a file the same
way when given a name.
"""

from __future__ import annotations

import mmap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = ["open_pe"]


@contextmanager
def open_pe(path: str | Path, *, fast_load: bool = True) -> Iterator[Any]:
    """A ``pefile.PE`` for *path*, valid inside the ``with`` body only.

    Raises what ``pefile.PE(path)`` raises for an unreadable or malformed
    file: ``OSError`` when the file cannot be opened, ``PEFormatError`` when
    it is empty or not a PE image.
    """
    import pefile  # type: ignore[import-untyped]

    with open(path, "rb") as fh:
        try:
            data = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as exc:  # an empty file cannot be mapped
            raise pefile.PEFormatError("The file is empty") from exc
    with data:
        yield pefile.PE(data=data, fast_load=fast_load)
