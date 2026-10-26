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

"""Zip/tar container integrity for package operands.

A file that is zip- or tar-shaped but cannot be read as that archive is a
*corrupt* operand, not an unrecognized format. Detection predicates stay
boolean (a corrupt archive is "not mine" to them); the release-input resolver
calls :func:`require_readable_archive` after every extractor declined, to name
the real cause.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path


def _head_bytes(path: Path, size: int) -> bytes:
    """The first *size* bytes of *path*, or ``b""`` when unreadable."""
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except OSError:
        return b""


class CorruptPackageArchiveError(ValueError):
    """A file that is zip- or tar-shaped but cannot be read as that archive.

    Raised only for a *corrupt* container, never for a file that is simply not
    one: a non-archive stays "not mine" (``detect()`` answers ``False`` and
    :func:`detect_extractor` returns ``None``). Surfacing it as its own type
    lets the release-input caller say "corrupt or truncated archive" instead
    of the misleading "unrecognized package format".
    """


_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")
_ZIP_FAMILY_SUFFIXES = (".whl", ".zip", ".egg", ".jar", ".apk", ".nupkg", ".conda")
_TAR_FAMILY_SUFFIXES = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz2",
    ".tar.zst",
)


def _is_zip_shaped(path: Path) -> bool:
    """Whether *path* claims to be a zip: a zip magic at byte 0 or a zip-family name."""
    if path.name.lower().endswith(_ZIP_FAMILY_SUFFIXES):
        return True
    return _head_bytes(path, 4) in _ZIP_MAGICS


def _zip_entry_names(path: Path) -> list[str]:
    """Every member name of a zip container.

    ``[]`` for a file that is not zip-shaped (not mine). A zip-shaped file
    that cannot be read raises :class:`CorruptPackageArchiveError`: an empty
    listing there would read as "a zip with no members" and turn corruption
    into an unrecognized format.

    Deliberately uncapped. An earlier revision read only the first 200
    names, which made detection depend on where a builder happened to place
    the metadata directory: wheel tools commonly append `*.dist-info/` after
    the package payload, so a large wheel's own marker fell outside the
    window and the archive was rejected (Codex review, PR #1253). The whole
    list is the zip's already-parsed central directory -- bounded by member
    count, not by uncompressed size -- so there is nothing to bound here.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        if not _is_zip_shaped(path):
            return []
        raise CorruptPackageArchiveError(
            f"{path} is a corrupt or truncated archive: {exc}"
        ) from exc


def _readable_tar(path: Path) -> bool:
    """Whether :mod:`tarfile` recognises *path* (plain, gzip, bzip2 or xz)."""
    try:
        return tarfile.is_tarfile(path)
    except (OSError, EOFError, tarfile.TarError, ValueError):
        return False


def require_readable_archive(path: Path) -> None:
    """Raise :class:`CorruptPackageArchiveError` if *path* is a corrupt archive.

    A file that is neither zip- nor tar-shaped passes silently. A zip-shaped
    file must list its members; a tar-shaped file (tar-family name) must be
    readable by :mod:`tarfile` (``.tar.zst``, which it has no codec for, is
    not checked rather than misreported). A file readable as either zip or
    tar is never corrupt, whatever its name says. Called by the release-input
    resolver after every extractor declined, so it never changes detection.
    """
    if not path.is_file() or _readable_tar(path):
        return
    _zip_entry_names(path)
    if zipfile.is_zipfile(path):
        return  # a zip whose member list reads, whatever its suffix says
    name = path.name.lower()
    if (
        name.endswith(_TAR_FAMILY_SUFFIXES)
        and not name.endswith(".tar.zst")
        and not _readable_tar(path)
    ):
        raise CorruptPackageArchiveError(
            f"{path} is a corrupt or truncated archive: not readable as tar"
        )
