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

"""Atomic same-directory writes for cache files (temp file + ``os.replace``).

Moved from ``dumper_cache`` unchanged: generic file I/O with no knowledge of
what is being cached, which is ``storage``'s job, not the AST cache's.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write"]


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* via a same-directory temp file + ``os.replace``.

    Plain ``open(path, "wb")``/``shutil.copy2`` can leave a torn file behind if
    two processes race to populate the same cache key (e.g. comparing two
    releases that share an unchanged header tree, with old/new extracted
    concurrently) — a reader would then see a partially-written file instead
    of a clean cache miss. ``os.replace`` is atomic on both POSIX and Windows,
    so a concurrent reader always sees either the old (absent) or the new
    (complete) file, never something in between.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
