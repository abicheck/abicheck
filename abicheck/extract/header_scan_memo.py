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

"""A content-validated, in-process memo for pure header-text scans.

A header-text scan (the C++20 dialect scan in ``dumper_ast_config_cpp20``)
is a pure function of the header set it is given and the content of the
files that set reaches through quoted includes. One dump calls it several
times with the same set (language mode, standard provenance, the castxml
invocation). A directory ``compare`` gives *every* member the whole
release's header set, so without a memo the identical scan was repeated
once per member per call site. That repetition is part of what made the
release cost grow with the square of its member count
(docs/contribute/known-gaps.md, "Multi-library L2 compare scales
quadratically with library count").

Soundness rests on the stamp, not on a time window or on mtimes, whose
granularity can be coarser than an in-place rewrite. An entry records a
digest of every file the scan reached **and** the name listing of each of
those files' directories. A quoted include that did not resolve when the
entry was made, and resolves now, therefore invalidates the entry too.
Checking a stamp reads and hashes those files, which is far cheaper than the
literal-, comment- and guard-aware scan it replaces. Any read failure is a
miss. A hit returns a fresh copy of the stored result, so a caller cannot
mutate the memo's copy.
"""

from __future__ import annotations

import functools
import hashlib
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

_R = TypeVar("_R")
_Stamp = tuple[tuple[str, str], ...]
#: Bounded, so a long-lived process scanning many distinct header sets does
#: not grow without limit. A release uses one set per side.
_MAX_ENTRIES = 64


def _digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _nearest_existing_dir(path: Path) -> Path:
    while not path.is_dir() and path != path.parent:
        path = path.parent
    return path


def stamp_files(paths: Sequence[Path], probed: Sequence[Path] = ()) -> _Stamp | None:
    """Digest every file and list every directory the result depends on.

    The directories are each file's own parent plus, for every *probed* path
    (an include looked up and not found), the nearest directory that exists
    on the way to it: creating the missing file, or any missing directory
    above it, changes that listing. ``None`` on any read failure.
    """
    files = sorted({str(p) for p in paths})
    dirs = {str(p.parent) for p in paths}
    dirs |= {str(_nearest_existing_dir(p.parent)) for p in probed}
    out: list[tuple[str, str]] = []
    try:
        for name in files:
            with open(name, "rb") as fh:
                out.append((name, _digest(fh.read())))
        for name in sorted(dirs):
            listing = "\0".join(sorted(os.listdir(name)))
            out.append((name + os.sep, _digest(os.fsencode(listing))))
    except OSError:
        return None
    return tuple(out)


def _stamp_still_matches(stamp: _Stamp) -> bool:
    files = [Path(n) for n, _ in stamp if not n.endswith(os.sep)]
    dirs = [n for n, _ in stamp if n.endswith(os.sep)]
    # Re-list exactly the recorded directories: re-deriving them from the
    # probe paths now could pick a different "nearest existing" directory.
    fresh = stamp_files(files)
    if fresh is None:
        return False
    fresh_files = tuple(e for e in fresh if not e[0].endswith(os.sep))
    if fresh_files != tuple(e for e in stamp if not e[0].endswith(os.sep)):
        return False
    try:
        for name in dirs:
            listing = "\0".join(sorted(os.listdir(name[: -len(os.sep)])))
            if (name, _digest(os.fsencode(listing))) not in stamp:
                return False
    except OSError:
        return False
    return True


def memoize_header_scan(
    expand: Callable[..., list[Path]],
) -> Callable[[Callable[..., list[_R]]], Callable[..., list[_R]]]:
    """Memoize ``fn(header_paths, **kwargs) -> list`` by input and file content.

    *expand* is the scan's own include expansion, called with the same header
    list and keyword arguments plus an ``unresolved`` collector. On a miss it
    tells the memo which files the result depends on and where an include was
    looked for and not found.
    """

    def decorate(fn: Callable[..., list[_R]]) -> Callable[..., list[_R]]:
        memo: dict[tuple[Any, ...], tuple[_Stamp, list[_R]]] = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(header_paths: list[Path], **kwargs: Any) -> list[_R]:
            key = (tuple(str(p) for p in header_paths), tuple(sorted(kwargs.items())))
            with lock:
                entry = memo.get(key)
            if entry is not None and _stamp_still_matches(entry[0]):
                return list(entry[1])
            # Stamped *before* the scan: a file edited while the scan runs
            # leaves a stamp that no longer matches, so the next call rescans
            # rather than trusting a result computed from the edited file.
            probed: list[Path] = []
            reached = expand(list(header_paths), **kwargs, unresolved=probed)
            stamp = stamp_files(reached, probed)
            result = fn(header_paths, **kwargs)
            if stamp is not None:
                with lock:
                    if key not in memo and len(memo) >= _MAX_ENTRIES:
                        memo.pop(next(iter(memo)))
                    memo[key] = (stamp, list(result))
            return result

        wrapper.cache_clear = memo.clear  # type: ignore[attr-defined]
        return wrapper

    return decorate
