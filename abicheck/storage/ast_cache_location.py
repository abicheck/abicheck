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
import functools
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

from ..model.execution_cache_scoped import DiskCache
from . import ast_cache_budget

__all__ = ["AST_DISK_CACHE", "ast_cache_entry_path", "reference_scratch_scoped"]

log = logging.getLogger(__name__)

AST_DISK_CACHE = DiskCache("abicheck.storage.header_ast_cache.ast_disk")

#: The one process-wide reference-mode scratch root (created on first use,
#: removed at interpreter exit); each call gets a fresh directory under it.
_REFERENCE_SCRATCH: list[Path] = []


def _reference_scratch_root() -> Path:
    if not _REFERENCE_SCRATCH:
        _REFERENCE_SCRATCH.append(Path(tempfile.mkdtemp(prefix="abicheck-ref-")))
    return _REFERENCE_SCRATCH[0]


def _remove_reference_scratch() -> None:
    import shutil

    for path in _REFERENCE_SCRATCH:
        shutil.rmtree(path, ignore_errors=True)
    _REFERENCE_SCRATCH.clear()


atexit.register(_remove_reference_scratch)


#: The scratch root of the extraction in progress, if one opened a scope.
_SCOPE_ROOT: ContextVar[Path | None] = ContextVar("abicheck_ref_scratch", default=None)

_F = TypeVar("_F", bound=Callable[..., Any])


def reference_scratch_scoped(fn: _F) -> _F:
    """Give reference-mode AST scratch paths the lifetime of one call to *fn*.

    The outermost call opens a scratch root and removes it (with every AST
    written under it) when it returns, so a long-lived process that dumps
    repeatedly does not accumulate scratch files until exit. Nested calls
    share the outer root. Outside reference mode this costs one check.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if AST_DISK_CACHE.enabled() or _SCOPE_ROOT.get() is not None:
            return fn(*args, **kwargs)
        import shutil

        root = Path(tempfile.mkdtemp(prefix="abicheck-ref-"))
        token = _SCOPE_ROOT.set(root)
        try:
            return fn(*args, **kwargs)
        finally:
            _SCOPE_ROOT.reset(token)
            shutil.rmtree(root, ignore_errors=True)

    return wrapper  # type: ignore[return-value]


def ast_cache_entry_path(key: str, backend: str = "castxml") -> Path:
    # One sub-directory + extension per backend: castxml XML and clang JSON coexist.
    ext = "json" if backend == "clang" else "xml"
    if not AST_DISK_CACHE.enabled():
        AST_DISK_CACHE.record_bypass()
        root = _SCOPE_ROOT.get() or _reference_scratch_root()
        root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f"{backend}-", dir=root))
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
