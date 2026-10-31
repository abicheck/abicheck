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

"""Content digests of the files a header parse can read (ADR-062 D11).

One owner for the *identity* half of every header-parse cache key: the
whole-snapshot key (``workflows.dump.cache.key``) and the header-AST keys
(``extract.headers.ast_config``) fold these digests instead of mtimes.

* :func:`file_digest` -- SHA-256 of a file's bytes. A process-wide memo keyed
  by the file's stat signature avoids re-reading an unchanged file; the stat
  fields only decide whether the memo may be *reused*, they never enter a
  digest (D11: "size and mtime may be used to avoid recomputing a digest, but
  never to define semantic identity"). That is also why nothing here can
  repeat the reverted castxml L4 fold: no device, inode or mtime reaches a key.
* :func:`tree_digest` -- the digest of every regular file under an include
  root, whatever its suffix. The preprocessor opens ``#include "x.def"`` and
  ``#include <vector>`` exactly like ``x.h``, so a suffix filter is an
  under-approximation of the parse's inputs (the closed known gap "the cache
  keys walk include trees only over ``CACHE_HEADER_SUFFIXES``").
* :func:`compiler_search_dirs` -- the compiler's own ``#include <...>``
  search list (``-E -v``), memoized per tool identity, so the keys can hash
  the system headers a parse reads without the caller naming them.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import abicheck.deadline as deadline

from .cache_header_scan import iter_cache_input_files

__all__ = [
    "compiler_search_dirs",
    "env_include_material",
    "file_digest",
    "parse_include_search_dirs",
    "search_dirs_material",
    "tree_digest",
]

_LOCK = threading.Lock()

#: ``path -> (stat signature, digest)``. Process-wide: the signature check makes
#: a stale entry unreachable, so the memo never needs a run scope to be correct.
_FILE_MEMO: dict[str, tuple[tuple[int, ...], str]] = {}

#: ``(tool identity, cpp) -> search dirs``.
_SEARCH_DIR_MEMO: dict[tuple[str, bool], tuple[str, ...]] = {}

#: A file whose mtime is this close to "now" is digested but not memoized:
#: a second write inside the same timestamp tick, with the same size, would
#: otherwise leave the signature unchanged ("racy git"). Two seconds covers
#: the coarsest common filesystem granularity (FAT, some network mounts).
_RACY_WINDOW_NS = 2_000_000_000


def _signature(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def file_digest(path: Path | str) -> str:
    """SHA-256 of *path*'s bytes, or a fixed marker for a non-regular file.

    Raises :class:`OSError` when *path* cannot be opened or read; callers
    fold their own marker for that case.
    """
    key = os.fspath(path)
    fd = os.open(key, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return "nonregular"
        sig = _signature(st)
        with _LOCK:
            hit = _FILE_MEMO.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        h = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        digest = h.hexdigest()
    finally:
        os.close(fd)
    if time.time_ns() - st.st_mtime_ns > _RACY_WINDOW_NS:
        with _LOCK:
            _FILE_MEMO[key] = (sig, digest)
    return digest


def _marked_digest(path: Path) -> str:
    try:
        return file_digest(path)
    except OSError:
        return "unreadable"


def tree_digest(root: Path | str) -> str:
    """Digest of every file a parse could open under *root*.

    A regular file digests as itself; a directory as the ordered
    ``(relative path, content digest)`` sequence of
    :func:`~.cache_header_scan.iter_cache_input_files`; anything else as a
    fixed marker. Paths inside a directory are hashed relative to it, so the
    digest is a property of the tree's content, and the caller decides
    whether the root's own spelling is part of its key.
    """
    p = Path(root)
    try:
        if p.is_file():
            return "file:" + _marked_digest(p)
        if not p.is_dir():
            return "missing"
    except OSError:
        return "unreadable"
    h = hashlib.sha256()
    try:
        children = iter_cache_input_files(p)
    except OSError:
        return "unreadable"
    for child in children:
        rel = os.path.relpath(child, p)
        h.update(rel.encode("utf-8", "surrogateescape"))
        h.update(b"\x00")
        h.update(_marked_digest(child).encode())
        h.update(b"\x00")
    return "dir:" + h.hexdigest()


def parse_include_search_dirs(stderr: str) -> list[str]:
    """The ``#include <...>`` block of a GNU/clang ``-E -v`` stderr, in order.

    Pure and string-only. The block sits between ``#include <...> search
    starts here:`` and ``End of search list.``, one directory per indented
    line (Darwin appends `` (framework directory)``); the preceding quote
    block lists ``-iquote`` dirs and is not captured.
    """
    dirs: list[str] = []
    in_block = False
    for line in stderr.splitlines():
        stripped = line.strip()
        if "<...> search starts here:" in stripped:
            in_block = True
            continue
        if stripped.startswith("End of search list."):
            break
        if in_block and stripped:
            dirs.append(stripped.split(" (", 1)[0].strip())
    return dirs


def _tool_content_identity(cc_bin: str) -> str:
    selected = shutil.which(cc_bin) or cc_bin
    try:
        return f"{selected}={file_digest(os.path.realpath(selected))}"
    except OSError as exc:
        return f"{selected}=unavailable:{type(exc).__name__}"


def compiler_search_dirs(cc_bin: str, *, cpp: bool) -> tuple[str, ...]:
    """The existing ``#include <...>`` directories *cc_bin* searches.

    Memoized per tool identity (resolved path and content digest), so an
    in-place compiler upgrade re-probes. Best-effort: a missing compiler or
    a failed probe yields ``()``; the caller's key already folds the
    compiler's identity, so an absent list cannot alias a present one.
    """
    identity = _tool_content_identity(cc_bin)
    memo_key = (identity, cpp)
    with _LOCK:
        hit = _SEARCH_DIR_MEMO.get(memo_key)
    if hit is not None:
        return hit
    try:
        with deadline.deadline_scope(15.0):
            proc = deadline.run_bounded(
                [cc_bin, "-E", "-x", "c++" if cpp else "c", "-v", "-"],
                input="",
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        dirs = tuple(
            d for d in parse_include_search_dirs(proc.stderr or "") if Path(d).is_dir()
        )
    except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded):
        dirs = ()
    with _LOCK:
        _SEARCH_DIR_MEMO[memo_key] = dirs
    return dirs


def search_dirs_material(cc_bin: str, *, cpp: bool) -> str:
    """Key material: each built-in search dir of *cc_bin* and its content."""
    return "\x00".join(
        f"{d}={tree_digest(d)}" for d in compiler_search_dirs(cc_bin, cpp=cpp)
    )


def env_include_material() -> str:
    """Key material for ``CPATH``/``C_INCLUDE_PATH``/``CPLUS_INCLUDE_PATH``.

    Every frontend inherits the process environment, so these add search
    directories no argument names: fold each variable's value *and* the
    content of every directory it lists. Unset and empty both key as empty.
    """
    from .compiler_driver import ENV_INCLUDE_VARS

    parts: list[str] = []
    for var in ENV_INCLUDE_VARS:
        value = os.environ.get(var, "")
        dirs = [d for d in value.split(os.pathsep) if d]
        parts.append(f"{var}={value}=" + ",".join(tree_digest(d) for d in dirs))
    return "env:" + "\x00".join(parts)
