# SPDX-License-Identifier: Apache-2.0
"""Best-effort AST cache path helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import deadline

log = logging.getLogger(__name__)

#: Whether the calling thread is inside a scope where writing to the AST
#: memo is actually worthwhile (mirrors ``deadline.py``'s own
#: ``contextvars.ContextVar`` propagation pattern). Default ``False``: a
#: direct :func:`abicheck.dumper.dump` caller with no downstream
#: ``service._attach_header_graph`` consumer (``appcompat.
#: check_app_compatibility``, a direct Python-API/MCP caller selecting the
#: clang backend) would otherwise memoize an AST nothing will ever pop,
#: holding a potentially multi-GB tree until evicted by the entry-count
#: bound for no benefit (Codex review). ``service.run_dump`` activates this
#: around its own primary ELF/PE/Mach-O dump call, since that is the one
#: shape where ``_attach_header_graph`` really does follow and consume the
#: memo. Does not cross a ``ThreadPoolExecutor`` boundary (same caveat as
#: ``deadline``'s own ContextVar) -- not a concern today since header-graph
#: attach isn't wired for the pooled manifest-dump path.
_ast_memoize_scope: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_ast_memoize_scope", default=False
)


@contextmanager
def ast_memoize_scope() -> Iterator[None]:
    """Mark the current thread's AST parses as worth memoizing in-process."""
    token = _ast_memoize_scope.set(True)
    try:
        yield
    finally:
        _ast_memoize_scope.reset(token)


def ast_memoize_active() -> bool:
    """Whether :func:`ast_memoize_scope` is active on the calling thread."""
    return _ast_memoize_scope.get()


#: In-process, single-consumption handoff for an already-parsed AST, keyed by
#: (backend, cache key) -- e.g. the header-only semantic graph's own AST pass
#: (ADR-041/G31, always on) over the identical header aggregate the main L2
#: clang dump already parsed for the same snapshot. A memo hit skips both the
#: disk read and the JSON re-parse of what can be a multi-GB tree (see
#: ``_atomic_copy``'s own docstring).
#:
#: Deliberately evicted on first read (:func:`load_cached_ast` pops, not
#: gets), not kept around as a general cache: the only intended reader is the
#: header-graph attach step immediately following the main snapshot pass that
#: wrote it, so once consumed there is no legitimate reason to keep a
#: potentially multi-GB tree strongly referenced (Codex/CodeRabbit review --
#: bounding by entry count alone still let a long-lived process, e.g. the MCP
#: server or ``scan`` over many libraries, accumulate several such trees at
#: once). ``_AST_MEMO_MAXSIZE`` is only a last-resort safety net for an entry
#: that is written but never consumed (header graph disabled, or an
#: exception between the write and the would-be read) -- kept small since
#: pop-on-read already handles the common single-handoff case.
_AST_MEMO_LOCK = threading.Lock()
_AST_MEMO: dict[tuple[str, str], Any] = {}
_AST_MEMO_MAXSIZE = 2


def store_cached_ast(key: str, backend: str, root: Any) -> None:
    """Memoize an already-parsed AST *root* for (*backend*, *key*) in-process."""
    with _AST_MEMO_LOCK:
        _AST_MEMO[(backend, key)] = root
        while len(_AST_MEMO) > _AST_MEMO_MAXSIZE:
            _AST_MEMO.pop(next(iter(_AST_MEMO)))


def load_cached_ast(
    key: str, backend: str, cache_path: Path, *, memoize: bool = True
) -> Any | None:
    """Return a previously-parsed AST for (*backend*, *key*), or ``None``.

    *cache_path* is the caller's own already-resolved :func:`_cache_path`
    result, not recomputed here -- callers that patch ``_cache_path`` in
    their own module namespace (as several tests do) must have that
    override actually govern the disk path this function reads, which a
    second, independent ``_cache_path`` call from this module could not see.

    *memoize* -- ``False`` for a caller that is itself the *final* consumer
    of this AST (the header-graph attach step, when the primary snapshot
    pass used ``castxml`` and never wrote a memo entry of its own): a disk
    hit there has no further same-process reader to hand off to, so
    re-inserting it into :data:`_AST_MEMO` would just hold a potentially
    multi-GB tree until evicted by the entry-count bound, dead weight in a
    long-lived process (Codex review). The primary snapshot pass's own call
    keeps the default ``True`` -- that write *is* the intended handoff
    :func:`store_cached_ast`'s docstring describes.

    Checks the in-process memo first (see :data:`_AST_MEMO`'s own docstring)
    -- popping it on a hit, since the memo is a single-consumption handoff,
    not a general cache -- falling back to the on-disk cache -- deadline-
    checked the same way the original inline disk-cache read was (once
    before the parse, so an already-exceeded deadline skips it, and once
    after, since parsing a huge cached AST can itself eat the rest of the
    budget). A corrupt/unreadable cache file is evicted, same as before.
    Never a staleness risk: this only ever returns a value this same process
    already validated (from disk or a fresh parse) under this identical
    content-addressed key.

    A memo hit is itself deadline-checked too, even though it does no real
    work -- a caller relying on ``--budget``/``deadline.deadline_scope`` to
    bound the whole scan must see a consistently-enforced deadline on every
    path back out of this function, not just the ones that happen to be
    expensive (mirrors the disk-cache-hit contract PR #591 established).
    """
    with _AST_MEMO_LOCK:
        memoized = _AST_MEMO.pop((backend, key), None)
    if memoized is not None:
        deadline.check()
        return memoized
    if not cache_path.exists():
        return None
    deadline.check()
    try:
        root = json.loads(cache_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        cache_path.unlink(missing_ok=True)
        return None
    deadline.check()  # loading a huge cached AST can eat the rest of the budget
    if memoize:
        store_cached_ast(key, backend, root)
    return root


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy *src* into *dst* via a same-directory temp file + ``os.replace``.

    Same atomicity rationale as :func:`_atomic_write` (a concurrent reader
    never sees a torn file), but streams the copy (``shutil.copyfileobj``)
    instead of reading *src* fully into a Python ``bytes`` object first — the
    L2 clang AST-dump cache write is exactly the case this matters for: the
    JSON tree it is caching can be hundreds of MB to multiple GB for a
    pathological header (P0 SVS field report), and the caller already holds
    one in-memory copy of it (the parsed dict) — a second full-size ``bytes``
    copy just to write the cache would double peak memory for no reason.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp_name, dst)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, obj: object) -> None:
    """Serialize *obj* as JSON straight into *path* via a same-directory
    temp file + ``os.replace``, without ever materializing the fully
    encoded document as one Python ``str``/``bytes`` object first.

    ``json.dump`` writes incrementally to the file object as it encodes,
    unlike ``_atomic_write(path, json.dumps(obj).encode(...))`` -- the
    latter's ``json.dumps`` call builds the entire encoded string in memory
    before ``_atomic_write`` ever sees it, doubling peak memory again on
    top of *obj* itself for exactly the kind of multi-GB DPC++ AST tree
    this cache path exists to write out (Codex review).
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write(path: Path, data: bytes) -> None:
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


def _cache_path(key: str, backend: str = "castxml") -> Path:
    # One sub-directory + file extension per backend so castxml XML and clang
    # JSON caches live side by side without clashing.
    ext = "json" if backend == "clang" else "xml"
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
    return cache_dir / f"{key}.{ext}"
