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
import sys
import tempfile
import time
from pathlib import Path

__all__ = ["atomic_write", "replace_file"]

#: Back-off before each retry of a rename Windows refused (seconds, ~1.9 s total).
_WINDOWS_REPLACE_BACKOFF_S = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)


def replace_file(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """``os.replace(src, dst)``, retried briefly on Windows' transient refusal.

    POSIX ``rename`` onto a path other writers are replacing or readers hold
    open always succeeds. Windows' ``MoveFileEx`` instead fails with
    ``ERROR_ACCESS_DENIED``/``ERROR_SHARING_VIOLATION`` (``PermissionError``)
    while another handle has *dst* open or another writer's replace of it is
    in flight, so two publishers of one ``manifest.json`` racing without the
    cross-process lock failed one of them outright. The refusal clears as soon
    as that handle closes; a refusal that outlasts the back-off is raised.
    Elsewhere a ``PermissionError`` is a real one and is raised at once.
    """
    for delay in _WINDOWS_REPLACE_BACKOFF_S:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32":
                raise
            time.sleep(delay)  # perf-ok: bounded Windows replace retry
    os.replace(src, dst)


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
        replace_file(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
