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

"""Where a header-AST cache entry lives (the ``dumper_cache`` disk cache).

One sub-directory and extension per backend (castxml XML and clang JSON
coexist), under ``LOCALAPPDATA`` on Windows and ``XDG_CACHE_HOME`` (or
``~/.cache``) elsewhere, degrading to the system temp directory when that
cannot be created -- caching is best-effort.

The disk cache's policy and counters are the central wrapper's
(design-hardening plan, Phase 4): with ``ABICHECK_REFERENCE_MODE=1`` every
call hands out a fresh scratch path, so no read can hit and no write is ever
read back. The scratch directories are removed at interpreter exit.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import tempfile
from pathlib import Path

from ..model.execution_cache_scoped import DiskCache
from . import ast_cache_budget

__all__ = ["AST_DISK_CACHE", "ast_cache_entry_path"]

log = logging.getLogger(__name__)

AST_DISK_CACHE = DiskCache("abicheck.dumper_cache.ast_disk")

#: Reference-mode scratch directories, removed at interpreter exit.
_REFERENCE_SCRATCH: list[Path] = []


def _remove_reference_scratch() -> None:
    import shutil

    for path in _REFERENCE_SCRATCH:
        shutil.rmtree(path, ignore_errors=True)
    _REFERENCE_SCRATCH.clear()


atexit.register(_remove_reference_scratch)


def ast_cache_entry_path(key: str, backend: str = "castxml") -> Path:
    # One sub-directory + extension per backend: castxml XML and clang JSON coexist.
    ext = "json" if backend == "clang" else "xml"
    if not AST_DISK_CACHE.enabled():
        AST_DISK_CACHE.record_bypass()
        scratch = Path(tempfile.mkdtemp(prefix=f"abicheck-ref-{backend}-"))
        _REFERENCE_SCRATCH.append(scratch)
        return scratch / f"{key}.{ext}"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        cache_dir = (
            Path(local) / "abi_check" / backend
            if local
            else Path.home() / "AppData" / "Local" / "abi_check" / backend
        )
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        cache_dir = base / "abi_check" / backend
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "abi_check" / backend
        log.warning(
            "AST cache directory %s is unavailable (%s); using %s",
            cache_dir,
            exc,
            fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        cache_dir = fallback
    ast_cache_budget.note_use(cache_dir, entry := cache_dir / f"{key}.{ext}")
    return entry
