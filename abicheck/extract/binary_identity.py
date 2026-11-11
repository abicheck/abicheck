# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The identity fields a binary snapshot records about the file it was read from.

A cold dump writes ``library``, ``source_path``, ``source_mtime``,
``source_mtime_epoch`` and ``source_size`` from the binary's path. The
whole-snapshot cache keys on the binary's *content*, so one warm entry serves
every path holding those bytes. :func:`stamp_binary_identity` re-derives the
same fields from the path a hit was asked for, so a hit never reports the path
that first populated the entry.

The content identity is :func:`binary_sha256`: the SHA-256 of the bytes a
binary operand stands for. ADR-090 D1(c) proves two compared sides one input
when theirs are equal. A live operand is hashed when it is compared
(``input_resolution.collect_metadata``) and a dump records the same value
(``AbiSnapshot.binary_sha256``, :func:`record_binary_sha256`), both through
``cache_input_digest.file_digest``, so the two are never taken differently
and a dump's cache key has usually already hashed the file.
"""

from __future__ import annotations

from pathlib import Path

from ..model import AbiSnapshot
from .headers.toolchain import _safe_mtime, _safe_size

__all__ = ["binary_sha256", "record_binary_sha256", "stamp_binary_identity"]


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


def binary_sha256(path: Path) -> str | None:
    """The SHA-256 of the binary *path* stands for, or ``None`` when it is not
    a binary (a directory, a snapshot, a manifest, an unreadable file).

    A GNU ld linker script is followed to its final target, as the link step
    would, and as a live compare hashes it.
    """
    from ..binary_utils import detect_binary_format, resolve_linker_script_chain
    from .cache_input_digest import file_digest

    if not path.is_file():
        return None
    target = resolve_linker_script_chain(path)
    if detect_binary_format(target) is None:
        return None
    try:
        return file_digest(target)
    except OSError:
        return None


def record_binary_sha256(snapshot: AbiSnapshot, path: Path) -> None:
    """Stamp the content identity of the binary *snapshot* was dumped from.

    A non-binary *path* leaves the recorded value alone. Persisted as the
    ``abicheck.binary_sha256`` extension fact.
    """
    digest = binary_sha256(path)
    if digest is not None:
        snapshot.binary_sha256 = digest
