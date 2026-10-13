# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The identity fields a binary snapshot records about the file it was read from.

A cold dump writes ``library``, ``source_path``, ``source_mtime``,
``source_mtime_epoch`` and ``source_size`` from the binary's path. The
whole-snapshot cache keys on the binary's *content*, so one warm entry serves
every path holding those bytes. :func:`stamp_binary_identity` re-derives the
same fields from the path a hit was asked for, so a hit never reports the path
that first populated the entry.
"""

from __future__ import annotations

from pathlib import Path

from ..model import AbiSnapshot
from .headers.toolchain import _safe_mtime, _safe_size


def stamp_binary_identity(snapshot: AbiSnapshot, path: Path) -> None:
    """Set *snapshot*'s path-derived identity fields from *path*, as a cold dump does.

    ``library`` is the name of *path* as given. ``source_path`` is resolved, so a
    symlink reports its target; ``library`` keeps the symlink's own name.
    """
    mtime, mtime_epoch = _safe_mtime(path)
    snapshot.library = path.name
    snapshot.source_path = str(path.resolve())
    snapshot.source_mtime = mtime
    snapshot.source_mtime_epoch = mtime_epoch
    snapshot.source_size = _safe_size(path)
