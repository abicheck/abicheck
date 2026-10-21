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

"""Verify, then unpack, a release-contract baseline-set archive.

Both Action consumers of a baseline-set archive -- the root Action's
release fallback (``action/run.sh``) and ``actions/resolve-baseline`` --
need the same steps in the same order: check the archive's bytes against a
caller-pinned digest, extract it with the safe tar extractor, reject any
symlink, and locate the directory holding ``manifest.json``. Keeping them in
one owner means the digest is always checked *before* any member is written
to disk, and the two consumers cannot drift on what "a usable archive" means.

The digest is a SHA-256 over the archive file's raw bytes. It is not the
per-snapshot content digest recorded inside ``manifest.json``: that one is
computed over normalized snapshot content, and a publisher who rewrites the
archive can recompute it. The pin binds the caller's expectation to the exact
bytes it was produced from, which is the anchor that survives a consistent
repack.

The Actions reach this through the ``abicheck.frontends.action.cli``
``prepare-baseline-set`` command, which owns the command-line surface and its
exit codes (:data:`EXIT_MALFORMED`, :data:`EXIT_DIGEST_MISMATCH`,
:data:`EXIT_USAGE`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..extract.packages import TarExtractor

__all__ = [
    "ARCHIVE_FORMATS",
    "EXIT_DIGEST_MISMATCH",
    "EXIT_MALFORMED",
    "EXIT_OK",
    "EXIT_USAGE",
    "BaselineArchiveError",
    "PreparedBaselineSet",
    "archive_sha256",
    "normalize_expected_digest",
    "prepare_baseline_set",
]

ARCHIVE_FORMATS = ("tar.zst", "tar.gz", "tar")

EXIT_OK = 0
EXIT_MALFORMED = 1
EXIT_DIGEST_MISMATCH = 3
EXIT_USAGE = 64

CODE_USAGE = "usage"
CODE_DIGEST_MISMATCH = "digest-mismatch"
CODE_MALFORMED = "malformed"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_DIGEST_PREFIX = "sha256:"
_CHUNK = 1024 * 1024
_MAX_LISTED_SYMLINKS = 5


class BaselineArchiveError(Exception):
    """A refusal, with a machine code and a one-line message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def exit_status(self) -> int:
        if self.code == CODE_USAGE:
            return EXIT_USAGE
        if self.code == CODE_DIGEST_MISMATCH:
            return EXIT_DIGEST_MISMATCH
        return EXIT_MALFORMED


@dataclass(frozen=True)
class PreparedBaselineSet:
    """A verified, extracted baseline-set archive."""

    #: Directory the archive was extracted into, narrowed to the single
    #: subdirectory that holds ``manifest.json`` when the archive wraps its set
    #: in one. Not checked for ``manifest.json`` here: whether a missing
    #: manifest is "malformed" or "not found" is the consumer's decision.
    manifest_root: Path
    #: ``sha256:<hex>`` of the archive bytes that were extracted.
    archive_digest: str
    #: ``True`` only when an expected digest was supplied and matched.
    verified: bool


def normalize_expected_digest(value: str) -> str:
    """Return the bare lowercase hex of an expected digest.

    Accepts ``sha256:<hex>`` or ``<hex>``, in any letter case. Anything else --
    another algorithm, a truncated or padded value -- is a usage error, never a
    silently unpinned comparison.
    """
    text = value.strip().lower()
    if text.startswith(_DIGEST_PREFIX):
        text = text[len(_DIGEST_PREFIX) :]
    if not _HEX64.fullmatch(text):
        raise BaselineArchiveError(
            CODE_USAGE,
            f"expected-archive-digest {value!r} is not a SHA-256 digest. Use "
            "'sha256:<64 hex characters>' or the 64 hex characters alone.",
        )
    return text


def archive_sha256(path: Path) -> str:
    """SHA-256 hex digest of *path*'s raw bytes, read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_baseline_set(
    archive: Path,
    dest: Path,
    *,
    archive_format: str,
    expected_digest: str | None = None,
) -> PreparedBaselineSet:
    """Verify *archive* against *expected_digest*, then extract it into *dest*.

    The digest comparison happens before extraction, so a mismatched archive
    writes nothing into *dest*. *dest* must already exist. A missing or
    unreadable archive is a malformed-archive refusal, the same class as a
    corrupt one.
    """
    if archive_format not in ARCHIVE_FORMATS:
        raise BaselineArchiveError(
            CODE_USAGE,
            f"archive format {archive_format!r} is not one of "
            f"{', '.join(ARCHIVE_FORMATS)}.",
        )
    expected = normalize_expected_digest(expected_digest) if expected_digest else None
    if not dest.is_dir():
        raise BaselineArchiveError(
            CODE_MALFORMED,
            f"extraction directory {dest} does not exist; create it before extracting.",
        )
    name = archive.name
    try:
        actual = archive_sha256(archive)
    except OSError as exc:
        raise BaselineArchiveError(
            CODE_MALFORMED,
            f"could not read baseline-set archive {name}: {exc.strerror or exc}.",
        ) from exc

    if expected is not None and actual != expected:
        raise BaselineArchiveError(
            CODE_DIGEST_MISMATCH,
            f"baseline-set archive {name} does not match the pinned digest: "
            f"expected sha256:{expected}, got sha256:{actual}. The archive was "
            "altered after the digest was pinned, or the pin is stale. Refusing "
            "to extract it.",
        )

    _extract(archive, dest, archive_format)
    _reject_symlinks(name, dest)

    return PreparedBaselineSet(
        manifest_root=_manifest_root(dest),
        archive_digest=f"{_DIGEST_PREFIX}{actual}",
        verified=expected is not None,
    )


def _extract(archive: Path, dest: Path, archive_format: str) -> None:
    extract = (
        TarExtractor._safe_extract_zst_tar
        if archive_format == "tar.zst"
        else TarExtractor._safe_extract
    )
    try:
        extract(archive, dest)
    except Exception as exc:  # noqa: BLE001 -- every extractor refusal is one class here
        hint = (
            " or this runner has neither a 'zstd' command-line tool nor the Python "
            "'zstandard' package available"
            if archive_format == "tar.zst"
            else ""
        )
        raise BaselineArchiveError(
            CODE_MALFORMED,
            f"failed to extract baseline-set archive {archive.name} "
            f"(.{archive_format}) -- it is truncated or corrupted, or contains a "
            "disallowed member (path traversal, a symlink escaping the extraction "
            f"root, or a device/FIFO entry){hint}. Detail: {exc}",
        ) from exc


def _reject_symlinks(name: str, dest: Path) -> None:
    symlinks = sorted(
        str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_symlink()
    )
    if not symlinks:
        return
    listed = ", ".join(symlinks[:_MAX_LISTED_SYMLINKS])
    more = "" if len(symlinks) <= _MAX_LISTED_SYMLINKS else " (more not listed)"
    raise BaselineArchiveError(
        CODE_MALFORMED,
        f"baseline-set archive {name} contains a symlink, which is not supported -- "
        "baseline-set archives must contain only plain files and directories. "
        f"Symlinks found: {listed}{more}.",
    )


def _manifest_root(dest: Path) -> Path:
    if (dest / "manifest.json").is_file():
        return dest
    subdirs = sorted(child for child in dest.iterdir() if child.is_dir())
    if len(subdirs) == 1 and (subdirs[0] / "manifest.json").is_file():
        return subdirs[0]
    return dest
