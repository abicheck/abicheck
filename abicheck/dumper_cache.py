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
#: check_app_compatibility``, a direct Python-API caller selecting the
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


#: Subtree size, in *nodes* (containers + scalars), at or below which
#: :func:`_write_json_chunked` hands a subtree to the one-shot C encoder
#: whole instead of descending into it. Sizing note: this is one of the two
#: knobs that bound peak transient memory, so it is deliberately modest.
_JSON_CHUNK_NODE_LIMIT = 200_000

#: The *other* bound, in estimated encoded bytes. A node count alone does not
#: bound the encoded size of a subtree: a DPC++ AST full of long
#: template-qualified spellings can hold well under
#: :data:`_JSON_CHUNK_NODE_LIMIT` nodes and still encode to hundreds of MiB,
#: which would be handed to a single ``json.dumps`` call and reintroduce
#: exactly the second full-size copy this writer exists to avoid (Codex
#: review, PR #1275). Both bounds are checked, so a subtree is delegated whole
#: only when it is small in *both* dimensions.
_JSON_CHUNK_BYTE_LIMIT = 8 << 20

#: Worst-case encoded characters per input character under
#: ``ensure_ascii=True``: a non-ASCII or control character becomes
#: ``\uXXXX``. Used to keep :func:`_subtree_exceeds`'s byte figure an upper
#: bound (see its docstring for why a lower bound was wrong).
_MAX_ESCAPE = 6

#: Floor for the descent-depth cap below, so a caller that has lowered the
#: interpreter's recursion limit still gets a useful amount of chunking.
_JSON_CHUNK_MIN_DEPTH = 40


def _max_descent_depth() -> int:
    """How deep :func:`_write_json_chunked` may descend on this stack.

    The cap exists only to bound *this module's* Python recursion (one frame
    per level) and, with it, to make a cyclic input -- impossible from
    ``json.load``, but not from a hand-built dict -- terminate here and raise
    from the C encoder's own circular-reference check instead of blowing the
    stack.

    Derived from the live recursion limit rather than a fixed small number,
    because the cap and the memory bound are in tension: past the cap a large
    subtree is delegated whole, so a *low* cap silently disables the byte bound
    for any document whose bulk sits deeper than it (a fixed 40 did exactly
    that -- Codex review, PR #1275). A quarter of the recursion limit leaves
    ample headroom for whatever stack the caller already occupies while putting
    the cap far beyond any depth at which a document remains encodable at all:
    ``json.dumps`` itself raises ``RecursionError`` past the same limit, so the
    residual unbounded case is confined to documents the stdlib encoder could
    not have written either way.
    """
    return max(_JSON_CHUNK_MIN_DEPTH, sys.getrecursionlimit() // 4)


#: Encoded-fragment bytes buffered before one ``write`` call. Keeps the
#: structural fragments ("{", "\"inner\": [", ", ") from costing one
#: buffered-writer call each without ever holding a meaningful amount of the
#: document.
_JSON_WRITE_BUFFER = 1 << 18


def _subtree_exceeds(obj: object, limit: int, byte_limit: int = -1) -> bool:
    """Whether *obj* is too big to encode in one piece, either way it can be.

    Two independent bounds, because neither implies the other: more than
    *limit* JSON nodes, or an estimated encoded size over *byte_limit* (pass a
    negative value to check node count alone). A handful of nodes carrying very
    long strings is small by count and large by bytes, and that is the shape
    that made a node-only bound insufficient.

    The byte figure is a deliberate **upper** bound on the encoded size, which
    is the only safe direction here: a ``False`` answer is what authorises
    encoding a subtree whole, so an estimate that can come in *under* the real
    size is no bound at all. An earlier revision had this backwards -- it used
    ``len(s)`` and called under-counting safe -- which ``ensure_ascii=True``
    (``json``'s default, and this writer's) makes plainly wrong: every
    non-ASCII character becomes a six-character ``\\uXXXX`` escape, as does any
    control character, so a subtree of Unicode identifier spellings estimated
    at a fraction of what it really encodes to (Codex review, PR #1275).
    Each character is therefore charged its worst case of six, plus two for the
    quotes. That over-charges plain ASCII by ~6x, which only ever splits
    earlier than strictly necessary -- the harmless direction.

    Stops the moment either answer is known, so the probe costs
    O(min(size, limit)) regardless of how large the subtree really is -- that
    bound is what makes it safe to call on the way down
    :func:`_write_json_chunked`'s descent rather than measuring the whole tree
    up front.
    """
    stack: list[object] = [obj]
    seen = 0
    weight = 0
    check_bytes = byte_limit >= 0
    while stack:
        cur = stack.pop()
        seen += 1
        if seen > limit:
            return True
        if type(cur) is dict:
            stack.extend(cur.values())
            if check_bytes:
                # Keys are encoded too; counted here rather than pushed, since
                # a key is never itself descended into.
                for key in cur:
                    weight += _MAX_ESCAPE * len(key) + 4 if type(key) is str else 8
        elif type(cur) is list:
            stack.extend(cur)
        elif check_bytes:
            weight += _MAX_ESCAPE * len(cur) + 2 if type(cur) is str else 8
        if check_bytes and weight > byte_limit:
            return True
    return False


def _write_json_chunked(obj: object, write: Any, depth: int = 0) -> None:
    """Write *obj* as JSON through *write*, one bounded chunk at a time.

    Byte-identical to ``json.dump(obj, f)`` -- same default separators
    (``", "``/``": "``), same ``ensure_ascii`` escaping, same key order --
    but **much** faster on a large tree, because ``json.dump`` does not use
    the C encoder at all: ``JSONEncoder.iterencode`` only selects
    ``c_make_encoder`` under ``_one_shot``, which is ``dumps``' path, not
    ``dump``'s. A streaming ``json.dump`` therefore encodes the entire
    document with the pure-Python fallback encoder, a fragment at a time
    (measured 27x slower than this function on a deep-template AST fixture,
    1.9x on a real header AST).

    The obvious alternative -- ``_atomic_write(path, json.dumps(obj)
    .encode())`` -- is what the streaming write was introduced to avoid: it
    holds the whole encoded document as a second full-size object on top of
    the tree itself, which is exactly the doubling this cache path cannot
    afford for a multi-GB DPC++ AST.

    So: descend through the *large* containers with Python (cheap -- there
    are few of them, and only their punctuation is written here) and hand
    every subtree that is small enough to the one-shot C encoder whole.
    "Small enough" is decided by :func:`_subtree_exceeds`, not by depth
    alone, so a single enormous namespace subtree is still split rather than
    encoded in one piece -- and by node count *and* an upper bound on encoded
    bytes,
    since a few nodes holding very long strings are small by one measure and
    huge by the other. The peak transient string therefore stays bounded by
    :data:`_JSON_CHUNK_BYTE_LIMIT` for every subtree shallower than
    :func:`_max_descent_depth`, which is every subtree in a document the stdlib
    encoder could write at all.

    One thing no bound here can split is a *single scalar*: a 100 MB string
    value encodes as one fragment, because JSON has nowhere to break it. That
    is inherent rather than overlooked -- the shapes this writer is for (an AST
    of many modest nodes) never contain one, and a caller that did would have
    the same peak with any encoder.

    Two shapes are deliberately delegated whole rather than descended into,
    both on the "never re-implement a coercion" principle: a ``dict``/``list``
    *subclass* (the exact-type tests below), and a ``dict`` with a non-``str``
    key. Both encode correctly this way -- only the memory bound relaxes, and
    neither occurs in a tree that came out of ``json.load``, which is the only
    thing this cache path ever writes.

    A dict with a non-``str`` key is delegated whole rather than split:
    ``json`` coerces such keys (``1`` -> ``"1"``, ``True`` -> ``"true"``) and
    re-deriving that coercion here would be a second implementation of it to
    keep byte-identical. Clang ASTs never contain one, so nothing is lost.
    """
    max_depth = _max_descent_depth()
    parts: list[str] = []
    size = 0

    def emit(fragment: str) -> None:
        nonlocal size
        parts.append(fragment)
        size += len(fragment)
        if size >= _JSON_WRITE_BUFFER:
            write("".join(parts))
            parts.clear()
            size = 0

    def walk(node: object, depth: int) -> None:
        if depth < max_depth and _subtree_exceeds(
            node, _JSON_CHUNK_NODE_LIMIT, _JSON_CHUNK_BYTE_LIMIT
        ):
            if type(node) is dict:
                if all(type(k) is str for k in node):
                    emit("{")
                    first = True
                    for key, value in node.items():
                        emit(
                            json.dumps(key) + ": "
                            if first
                            else ", " + json.dumps(key) + ": "
                        )
                        first = False
                        walk(value, depth + 1)
                    emit("}")
                    return
            elif type(node) is list:
                emit("[")
                first = True
                for value in node:
                    if not first:
                        emit(", ")
                    first = False
                    walk(value, depth + 1)
                emit("]")
                return
        emit(json.dumps(node))

    walk(obj, depth)
    if parts:
        write("".join(parts))


def _atomic_write_json(path: Path, obj: object) -> None:
    """Serialize *obj* as JSON straight into *path* via a same-directory
    temp file + ``os.replace``, without ever materializing the fully
    encoded document as one Python ``str``/``bytes`` object first.

    The encoding itself goes through :func:`_write_json_chunked` rather than
    ``json.dump`` -- same bytes, same bounded peak memory, without paying
    ``json.dump``'s pure-Python encoder for the whole document (see that
    function's own docstring for why ``dump`` never reaches the C encoder).
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _write_json_chunked(obj, f.write)
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
