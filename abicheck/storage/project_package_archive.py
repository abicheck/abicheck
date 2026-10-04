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

"""One-file transport for a ``ProjectSnapshot`` package directory
(storage-format-v2 A1.1).

A package is a directory (``manifest.json``, ``refs/**``, ``objects/**``).
Moving one around -- a CI artifact, a release attachment -- wants a single
file, so this module packs that directory into a zip and unpacks it back.
It is a transport of the *same* D6 layout, not a new storage shape.

**Zip, not the ``.tar.zst`` the plan first sketched**, for the reason G40's
content-addressed bundle archive already recorded (``bundle_archive.py``):
zip's central directory lets a reader open one member without reading the
rest, and every object is already ``zstd``-compressed, so members are stored
(``ZIP_STORED``) rather than compressed a second time. The same pinned
``ZipInfo`` fields make the archive bytes a function of content alone.

**An archive is untrusted input.** :func:`unpack_project_package` refuses,
before writing anything, any member that is not exactly one of the D6 paths
(so no ``..``, absolute path, backslash, directory entry or stray file can
reach the filesystem), a symlink, a compressed member (a stored member's
size is its real size, which is what makes the size limits meaningful), a
duplicate name, or an archive over the member/size budgets. Object content
is not re-hashed here: ``DirectoryObjectStore.get`` verifies every object's
digest when it is read, so a substituted object fails at first use.
"""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

from ..errors import SnapshotError
from .bundle_archive_cd_guard import reject_absurd_central_directory
from .package import (
    MANIFEST_RELPATH,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from .zip_member import deterministic_zipinfo

__all__ = [
    "MIMETYPE",
    "is_project_package_archive",
    "pack_project_package",
    "unpack_project_package",
]

#: First member of every archive, stored, so the file identifies itself
#: without a suffix convention (the ODF/EPUB idiom) -- and is told apart
#: from a G40 bundle archive, which carries ``blobs/`` instead.
MIMETYPE = "application/vnd.abicheck.project-snapshot+zip"
_MIMETYPE_MEMBER = "mimetype"

#: Budgets for an untrusted archive. Generous for a real project (tens of
#: thousands of objects, each well under a GiB) and far below what would
#: exhaust a CI runner.
MAX_MEMBERS = 50_000
MAX_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024

_OBJECT_SUFFIXES = (".json", ".json.zst", ".bin.zst")


def _is_package_member(name: str) -> bool:
    """Whether *name* is exactly a path the D6 layout can contain."""
    if name == MANIFEST_RELPATH:
        return True
    for prefix, relpath in (
        ("refs/variants/", variant_ref_relpath),
        ("refs/artifacts/", artifact_ref_relpath),
    ):
        if name.startswith(prefix) and name.endswith(".json"):
            ref_id = name[len(prefix) : -len(".json")]
            try:
                return relpath(ref_id) == name
            except (TypeError, ValueError):
                return False
    parts = name.split("/")
    if len(parts) == 4 and parts[0] == "objects":
        algorithm, _fanout, filename = parts[1], parts[2], parts[3]
        for suffix in _OBJECT_SUFFIXES:
            if filename.endswith(suffix):
                digest = f"{algorithm}:{filename[: -len(suffix)]}"
                try:
                    logical = object_relpath(digest)
                except (TypeError, ValueError):
                    return False
                return name == logical[: -len(".json")] + suffix
    return False


def pack_project_package(package_dir: str | Path, out_path: str | Path) -> None:
    """Write the package at *package_dir* to the zip archive *out_path*.

    Deterministic: members in sorted path order after the ``mimetype``
    member, every ``ZipInfo`` field pinned, so two packs of the same content
    are byte-identical. Refuses a directory with no ``manifest.json``, a
    symlink, or any file the D6 layout does not name -- packing it would
    publish something no reader expects. Written to a temporary sibling and
    renamed, so a failed pack never leaves a truncated archive behind.
    """
    root = Path(package_dir)
    if not (root / MANIFEST_RELPATH).is_file():
        raise SnapshotError(f"{root}: not a ProjectSnapshot package (no manifest.json)")
    members: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SnapshotError(f"{root}: refusing to pack symlink {rel!r}")
        if path.is_dir():
            continue
        if not _is_package_member(rel):
            raise SnapshotError(
                f"{root}: {rel!r} is not part of the package layout; refusing to pack it"
            )
        members.append((rel, path))
    members.sort()
    if len(members) + 1 > MAX_MEMBERS:
        raise SnapshotError(f"{root}: {len(members)} files exceed the archive budget")
    out = Path(out_path)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(
                deterministic_zipinfo(_MIMETYPE_MEMBER), MIMETYPE.encode("ascii")
            )
            for rel, path in members:
                zf.writestr(deterministic_zipinfo(rel), path.read_bytes())
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def is_project_package_archive(path: str | Path) -> bool:
    """Whether *path* is a regular file holding a project-package archive:
    a zip whose first member is the ``mimetype`` naming this format. Never
    raises; anything unreadable answers ``False``."""
    p = Path(path)
    try:
        if not p.is_file():
            return False
        with p.open("rb") as fp:
            if fp.read(4) != b"PK\x03\x04":
                return False
            fp.seek(0)
            with zipfile.ZipFile(fp) as zf:
                first = zf.infolist()[0] if zf.infolist() else None
                return (
                    first is not None
                    and first.filename == _MIMETYPE_MEMBER
                    and first.file_size == len(MIMETYPE)
                    and zf.read(first) == MIMETYPE.encode("ascii")
                )
    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError, NotImplementedError):
        return False


def _check_member(info: zipfile.ZipInfo, archive: Path) -> None:
    name = info.filename
    # The name as stored, not as zipfile rewrote it: on Windows ``filename``
    # has every backslash turned into "/" (and any NUL-suffix cut), so a
    # member spelled ``refs\\artifacts\\x.json`` would pass as a D6 path there
    # while being refused everywhere else. The layout is a byte-exact
    # contract, so any rewriting at all is a refusal.
    if info.orig_filename != name:
        raise SnapshotError(
            f"{archive}: member {info.orig_filename!r} is not a portable package path"
        )
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise SnapshotError(f"{archive}: member {name!r} is a symlink")
    if info.is_dir() or not _is_package_member(name):
        raise SnapshotError(
            f"{archive}: member {name!r} is not part of the package layout"
        )
    if info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size:
        raise SnapshotError(f"{archive}: member {name!r} is not stored uncompressed")
    if info.file_size > MAX_MEMBER_BYTES:
        raise SnapshotError(f"{archive}: member {name!r} exceeds the size budget")


def unpack_project_package(archive_path: str | Path, dest_dir: str | Path) -> None:
    """Unpack *archive_path* into *dest_dir*, which must be empty or absent.

    Every member is validated (see the module docstring) before the first
    byte is written, so a rejected archive leaves *dest_dir* untouched.
    """
    archive = Path(archive_path)
    dest = Path(dest_dir)
    if dest.exists() and any(dest.iterdir()):
        raise SnapshotError(f"{dest}: unpack target is not empty")
    try:
        with archive.open("rb") as fp:
            reject_absurd_central_directory(fp, archive, max_entries=MAX_MEMBERS)
            fp.seek(0)
            with zipfile.ZipFile(fp) as zf:
                infos = zf.infolist()
                if not infos or infos[0].filename != _MIMETYPE_MEMBER:
                    raise SnapshotError(f"{archive}: not a project-package archive")
                if zf.read(infos[0]) != MIMETYPE.encode("ascii"):
                    raise SnapshotError(f"{archive}: unknown archive mimetype")
                body = infos[1:]
                seen: set[str] = set()
                total = 0
                for info in body:
                    _check_member(info, archive)
                    if info.filename in seen:
                        raise SnapshotError(
                            f"{archive}: duplicate member {info.filename!r}"
                        )
                    seen.add(info.filename)
                    total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise SnapshotError(f"{archive}: unpacked size exceeds the budget")
                if MANIFEST_RELPATH not in seen:
                    raise SnapshotError(f"{archive}: no manifest.json member")
                dest.mkdir(parents=True, exist_ok=True)
                for info in body:
                    target = dest / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # `x`: never follow or overwrite anything already there.
                    with target.open("xb") as out:
                        out.write(zf.read(info))
    except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise SnapshotError(
            f"{archive}: not a valid project-package archive: {exc}"
        ) from exc
