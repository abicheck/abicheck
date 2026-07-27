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
#: holding a potentially multi-GB tree indefinitely for no benefit (Codex
#: review). ``service.run_dump`` activates this around its own primary
#: ELF/PE/Mach-O dump call, since that is the one shape where
#: ``_attach_header_graph`` really does follow and consume the memo.
_ast_memoize_scope: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_ast_memoize_scope", default=False
)

#: The single pending AST handoff for the *current thread*, or ``None``.
#: ``(backend, key, root)`` -- a plain per-thread slot (``contextvars``),
#: never a shared dict keyed only by content hash (Codex review, fresh
#: evidence): ``service.compare``'s default worker pool
#: (``ABICHECK_PARALLEL_EXTRACTION``) dumps the old and new sides
#: *concurrently* in separate threads, and the AST cache key has no
#: binary/side component -- comparing two versions against the *same*
#: ``-H`` header set (the common case) makes both sides' primary passes
#: compute the identical key. A shared dict would let one side's
#: single-consumption pop steal the other side's entry (or one side's
#: fresh write silently clobber the other's, mid-flight), so one graph
#: pass falls back to a full disk re-read/re-parse regardless -- the exact
#: regression this reuse exists to remove, on top of a plausible
#: wrong-AST hazard if the two sides' headers ever do differ. A per-thread
#: slot sidesteps the whole class of bug structurally: each worker thread
#: only ever sees its own write, never another thread's, no matter what
#: the two computed keys happen to be. Does not cross a
#: ``ThreadPoolExecutor`` boundary on its own (same caveat as
#: ``deadline``'s own ContextVar) -- that's exactly the point here, not a
#: gap: each pooled worker thread gets its own independent slot.
_ast_memo_slot: contextvars.ContextVar[tuple[str, str, Any] | None] = (
    contextvars.ContextVar("_ast_memo_slot", default=None)
)


@contextmanager
def ast_memoize_scope() -> Iterator[None]:
    """Mark the current thread's AST parses as worth memoizing in-process.

    Clears this thread's pending slot if the scoped operation raises, since
    a failure partway through (e.g. snapshot construction fails after the
    header AST parse itself succeeded) means no downstream
    ``_attach_header_graph`` will ever run to consume it -- leaving it set
    would hold a potentially multi-GB tree in this thread for however long
    the thread itself lives afterward (Codex review).
    """
    active_token = _ast_memoize_scope.set(True)
    try:
        yield
    except BaseException:
        _ast_memo_slot.set(None)
        raise
    finally:
        _ast_memoize_scope.reset(active_token)


def ast_memoize_active() -> bool:
    """Whether :func:`ast_memoize_scope` is active on the calling thread."""
    return _ast_memoize_scope.get()


def store_cached_ast(key: str, backend: str, root: Any) -> None:
    """Memoize an already-parsed AST *root* for (*backend*, *key*) in the
    calling thread's own pending slot (see :data:`_ast_memo_slot`'s own
    docstring) -- overwrites whatever this thread's slot already held, if
    anything (there is at most one legitimate pending handoff at a time)."""
    _ast_memo_slot.set((backend, key, root))


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
    hit there has no further same-thread reader to hand off to, so setting
    this thread's slot would just hold a potentially multi-GB tree for no
    benefit (Codex review). The primary snapshot pass's own call keeps the
    default ``True`` -- that write *is* the intended handoff
    :func:`store_cached_ast`'s docstring describes.

    Checks this thread's own pending slot first (see :data:`_ast_memo_slot`'s
    own docstring) -- consuming it only when its ``(backend, key)`` matches
    the request (a mismatch means this thread's slot holds something
    unrelated, e.g. a stale leftover from an earlier, differently-shaped
    call in a reused worker thread; left alone rather than guessed at) --
    falling back to the on-disk cache, deadline-checked the same way the
    original inline disk-cache read was (once before the parse, so an
    already-exceeded deadline skips it, and once after, since parsing a
    huge cached AST can itself eat the rest of the budget). A
    corrupt/unreadable cache file is evicted, same as before. Never a
    staleness risk: this only ever returns a value this same thread already
    validated (from disk or a fresh parse) under this identical
    content-addressed key.

    A slot hit is itself deadline-checked too, even though it does no real
    work -- a caller relying on ``--budget``/``deadline.deadline_scope`` to
    bound the whole scan must see a consistently-enforced deadline on every
    path back out of this function, not just the ones that happen to be
    expensive (mirrors the disk-cache-hit contract PR #591 established).
    """
    slot = _ast_memo_slot.get()
    if slot is not None and slot[0] == backend and slot[1] == key:
        _ast_memo_slot.set(None)
        deadline.check()
        return slot[2]
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
