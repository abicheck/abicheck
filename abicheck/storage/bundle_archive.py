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

"""Content-addressed zip archive container (G40).

A pure, format-only primitive: a zip file with one uncompressed
``manifest.json`` member plus one ``blobs/<sha256-hex>.json.zst`` member per
*unique* content hash. Nothing here knows what a ``BundleFacts`` or an
``AbiSnapshot`` is -- callers hand this module raw bytes and a manifest
``dict``, and get raw bytes back. That split keeps this module free of any
``model``/``compare``-layer import, letting it join `storage/` (ADR-061)
cleanly without resolving the pre-existing ``bundle_facts.py`` <->
``checker_types.py`` coupling a naive "construct a ``BundleFacts`` here"
design hits. The ``BundleFacts``-aware glue lives in ``serialization.py``'s
``save_bundle_facts``/``load_bundle_facts``; see the G40 design plan
(``docs/contribute/plans/g40-content-addressed-bundle-archive.md``).

Zip, not tar: its end-of-file central directory names every member's
offset/compressed length, so `zipfile.ZipFile.open(name)` reads exactly
one member without touching any other. Each payload is zstd-compressed
independently (``ZIP_STORED``), not ``ZIP_DEFLATED`` (ADR-059's codec).
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import secrets
import stat
import zipfile
from pathlib import Path
from typing import Any

from ..errors import SnapshotError
from .json_budget import (
    DEFAULT_MAX_JSON_CONTAINER_NODES,
    JsonContainerBudgetExceeded,
    JsonNestingTooDeepError,
    check_json_container_budget,
)
from .zstd_frame_guard import (
    read_past_leading_skippable_frames,
    skip_leading_skippable_frames,
    starts_with_skippable_frame_magic,
    validate_zstd_frame_completeness,
)

#: The manifest member's own name -- always the first thing a reader
#: touches, readable without scanning or decompressing any blob member.
MANIFEST_MEMBER = "manifest.json"

#: Blob member naming: content-hash-addressed, one per unique payload.
_BLOB_PREFIX = "blobs/"
_BLOB_SUFFIX = ".json.zst"

#: zstd compression level for archive blobs (ADR-059's ``ZSTD_LEVEL_
#: BASELINE`` reasoning: written rarely, read often).
ZSTD_LEVEL = 19

#: Same reasoning as `snapshot_io.py`'s own `_ZSTD_MAX_WINDOW_LOG`: bound
#: decompression memory to a window a legitimate blob will never need.
_ZSTD_MAX_WINDOW_LOG = 27  # 128 MiB

#: Per-blob decompressed-size cap (mirrors `snapshot_io.py`'s own
#: `DEFAULT_MAX_DECODED_BYTES`, same 1 GiB value, applied independently).
DEFAULT_MAX_BLOB_BYTES = 1024 * 1024 * 1024

#: `manifest.json`'s own size cap -- far smaller than
#: `DEFAULT_MAX_BLOB_BYTES`: only name/hash pairs, not payload content.
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024 * 1024

#: `manifest.json`'s own container-node budget -- a sub-cap-sized manifest
#: can still hold millions of nodes under an ignored field (Codex review,
#: fresh evidence). Same default as `bundle_facts.py`'s per-blob budget.
DEFAULT_MAX_MANIFEST_JSON_CONTAINER_NODES = DEFAULT_MAX_JSON_CONTAINER_NODES

#: Independent ceiling on a blob member's *stored* (still-compressed) size --
#: NOT derived from `max_decoded_bytes` (mirrors `snapshot_io.py`'s own
#: `DEFAULT_MAX_STORED_BYTES`/reasoning: a low `max_decoded_bytes` budget
#: must not reject a valid blob carrying several MiB of leading zstd
#: skippable-frame metadata ahead of a tiny real frame -- the bomb defense
#: is the incremental decoded-size check below, not this stored precheck).
DEFAULT_MAX_STORED_BLOB_BYTES = 2 * 1024 * 1024 * 1024


def _zstd_module() -> Any:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - core dependency, see pyproject.toml
        raise SnapshotError(
            "zstd bundle-archive support requires the 'zstandard' package, "
            "which is a core abicheck dependency (pyproject.toml) -- "
            "reinstall abicheck ('pip install abicheck') to restore it."
        ) from exc
    return zstandard


def _blob_member_name(content_hash: str) -> str:
    return f"{_BLOB_PREFIX}{content_hash}{_BLOB_SUFFIX}"


#: A fixed zip timestamp (the format's epoch floor) for every member this
#: module writes -- else `ZipFile.writestr` stamps `time.localtime()`.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _deterministic_zipinfo(name: str) -> zipfile.ZipInfo:
    """A ``ZipInfo`` for member *name* with every reproducibility-affecting
    field pinned, so the bytes this module writes depend only on the
    member's own name and content -- never on when or by whom it was
    written."""
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    # A fixed, portable permission bit (rw-r--r--) rather than whatever
    # `ZipInfo`'s own platform-dependent default would otherwise stamp.
    info.external_attr = 0o644 << 16
    # `ZipInfo.__init__` defaults `create_system` to the host platform (0
    # Windows, 3 Unix); pinned to 3 unconditionally so identical facts on
    # Windows vs. Linux/macOS CI don't differ in bytes/`stored_sha256`.
    info.create_system = 3
    return info


_ZIP_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06")

#: gzip/zstd magic -- a matching prefix already identifies a compressed
#: `BundleFacts` JSON envelope, so `looks_like_zip_from_tail()` must never
#: run against one: a crafted gzip `FEXTRA` sub-field (unlike `FCOMMENT`,
#: already closed) can embed a `PK\x05\x06` landing exactly at file end.
_JSON_ENVELOPE_MAGIC_PREFIXES = (b"\x1f\x8b", b"\x28\xb5\x2f\xfd")


def sniff_bundle_archive_format(path: str | Path) -> str:
    """``"archive"`` if *path*'s own bytes start with a zip local-file-header
    or empty-archive magic, or (for a prefix matching neither that nor a
    recognized gzip/zstd envelope) its tail contains a structurally
    plausible EOCD, per `looks_like_zip_from_tail()`; ``"json"`` otherwise.
    Used by ``serialization.load_bundle_facts``'s ``format="auto"``. Always
    ``"json"`` for a non-regular-file source. Delegates to
    `open_regular_file_for_format_sniff()`'s stat-then-open
    classification (closing the fd itself)."""
    fp, fmt = open_regular_file_for_format_sniff(path)
    if fp is not None:
        fp.close()
    return fmt


def _classify_prefix(prefix: bytes) -> str:
    return "archive" if prefix.startswith(_ZIP_MAGIC_PREFIXES) else "json"


def open_regular_file_for_format_sniff(
    path: str | Path,
) -> tuple[Any | None, str]:
    """``format="auto"``'s fd-sharing counterpart to
    `sniff_bundle_archive_format` -- opens *path* once, peeks its magic
    (past a leading zstd skippable frame if any), and returns ``(fp,
    "archive"|"json")`` with *fp* left at its post-peek position to
    reuse (`BundleArchiveReader.from_open_file`) instead of reopening,
    else a concurrent atomic replacement could swap generations (Codex).
    ``(None, "json")`` for a non-regular-file source -- don't close *fp*."""
    p = Path(path)
    # A path-level stat() first, BEFORE any open() -- opening a FIFO at
    # all, even nonblocking, can complete a one-shot producer's own
    # blocking open()-for-write, leaving a later open() to block forever
    # (Codex, reproduced with a real FIFO). stat() never blocks; the
    # fd-level fstat() below stays the source of truth against a swap.
    try:
        path_st = os.stat(p)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    if not stat.S_ISREG(path_st.st_mode):
        return None, "json"
    # Open first, nonblocking, then fstat() *that* fd -- a separate
    # stat()/open() risks a concurrent swap to a blocking FIFO.
    nonblock = getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(p, os.O_RDONLY | nonblock)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    try:
        st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None, "json"
    fp = os.fdopen(fd, "rb")
    try:
        prefix = fp.read(4)
        # A zstd JSON envelope may start with a skippable metadata frame
        # ahead of its real magic -- read past it so the check below
        # sees the real frame instead of falling through to the ZIP-tail
        # heuristic (which a crafted trailing skippable frame's own
        # EOCD-shaped tail can then satisfy; Codex, reproduced with a
        # real decodable stream). `read_past_...` returns raw bytes (some
        # callers need them unstripped); `skip_...` strips them here.
        if starts_with_skippable_frame_magic(prefix):
            prefix = skip_leading_skippable_frames(read_past_leading_skippable_frames(fp, prefix))
    except OSError as exc:
        # A failure reading the peek must not leak the fd or propagate a
        # raw OSError -- this module's error contract is SnapshotError.
        fp.close()
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    fmt = _classify_prefix(prefix)
    if fmt == "json" and not prefix.startswith(_JSON_ENVELOPE_MAGIC_PREFIXES):
        # A prefixed archive's byte-0 magic misses it (see looks_like_zip_
        # from_tail()); skipped for a recognized gzip/zstd envelope.
        from .bundle_archive_cd_guard import looks_like_zip_from_tail

        if looks_like_zip_from_tail(fp):
            fmt = "archive"
    return fp, fmt


#: A real bundle archive never needs anywhere near this many members -- a
#: crafted archive claiming more is rejected before `zipfile.ZipFile` is
#: constructed (it eagerly parses the whole central directory). Below
#: 0xFFFF (the non-ZIP64 EOCD sentinel). Public: `bundle_facts.py` aligns
#: its own writer-side budget to this cap.
MAX_ARCHIVE_MEMBERS = 20_000

#: Central-directory bomb guard and manifest string-size guard live in
#: sibling modules (`bundle_archive_cd_guard.py`/`_json_guard.py`), split
#: out to stay under this module's ADR-061 800-line cap.


def content_hash(payload: bytes) -> str:
    """The content-address of *payload* -- sha256 hex digest. Public so a
    caller can check against a known manifest entry without opening it."""
    return hashlib.sha256(payload).hexdigest()


def _open_unique_temp(parent: Path, prefix: str, suffix: str) -> tuple[int, Path]:
    """Atomically create a unique, exclusively-owned temp file in *parent*,
    mode ``0o666`` filtered through the umask. Mirrors ``snapshot_io.
    _open_unique_temp`` (dependency-free copy), except ``O_RDWR`` not
    ``O_WRONLY``: `close()` reads this fd back (`os.dup`) to hash what
    it wrote, avoiding a path-based reopen an attacker could redirect."""
    for _ in range(100):
        candidate = parent / f"{prefix}{secrets.token_hex(8)}{suffix}"
        try:
            fd = os.open(candidate, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
            return fd, candidate
        except FileExistsError:
            continue
    raise SnapshotError(f"Could not create a unique temp file in {parent}")


class BundleArchiveWriter:
    """Writes one content-addressed zip archive.

    Usage::

        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(payload1)
            h2 = writer.put_blob(payload2)  # payload2 == payload1 -> same hash, written once
            writer.write_manifest({"library_blobs": {"a": h1, "b": h2}, ...})

    *put_blob* may be called any number of times before *write_manifest*;
    *write_manifest* must be called exactly once, after every blob the
    manifest references has already been written (so a reader never
    observes a manifest naming a hash with no corresponding member).

    Writes go to a temp file next to the real destination; *close()* (a
    clean context-manager exit) only ``os.replace()``s it over the
    destination once fully written, so an interrupted write can never
    leave a truncated archive where a prior, valid one already stood.

    If *path* is a symlink, the temp file is created next to -- and
    ``close()`` replaces -- the link's *real target* (a bare
    ``os.replace(tmp, path)`` on a symlink destination would destroy the
    link for every other reader). Mirrors ``snapshot_io._atomic_write_
    bytes``'s own symlink handling.

    A pre-existing destination with more than one hard link is rejected
    outright (replacing one entry would desynchronize the others). Its
    existing mode/owner/group are preserved onto the replacement (not
    best-effort -- a failed chown aborts the write). *path*'s parent
    directory is created (``parents=True``) if missing."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._target = (
            Path(os.path.realpath(self._path)) if self._path.is_symlink() else self._path
        )
        try:
            existing_stat = self._target.stat()
        except (FileNotFoundError, NotADirectoryError):
            # Only genuine absence means "no pre-existing destination" --
            # any other OSError (e.g. ELOOP) must propagate, not bypass this.
            existing_stat = None
        self._existing_mode: int | None = None
        self._existing_uid: int | None = None
        self._existing_gid: int | None = None
        if existing_stat is not None:
            if not stat.S_ISREG(existing_stat.st_mode):
                # os.replace() would destroy a pre-existing FIFO/socket/
                # device -- no way to "write through" it via atomic rename.
                raise SnapshotError(
                    f"{self._target}: already exists and is not a regular "
                    "file (a FIFO, socket, or device) -- refusing to "
                    "replace it with a zip archive."
                )
            if existing_stat.st_nlink > 1:
                raise SnapshotError(
                    f"{self._target}: has {existing_stat.st_nlink} hard links -- "
                    "an atomic rewrite would silently desynchronize the other "
                    "link(s), which would keep the old content. Unlink the "
                    "extra hard link(s) first to rewrite this path in isolation."
                )
            self._existing_mode = stat.S_IMODE(existing_stat.st_mode)
            self._existing_uid = existing_stat.st_uid
            self._existing_gid = existing_stat.st_gid
        self._target.parent.mkdir(parents=True, exist_ok=True)
        # _open_unique_temp, not a predictable path a writable directory
        # could pre-create as a symlink. Randomized, O_CREAT|O_EXCL.
        tmp_fd, self._tmp_path = _open_unique_temp(
            self._target.parent, f".{self._target.name}.", ".tmp"
        )
        self._tmp_file = os.fdopen(tmp_fd, "wb")
        # A file object, not a path: ZipFile won't close a fileobj it
        # didn't itself open, so close()/_abort() own closing it.
        self._zf = zipfile.ZipFile(self._tmp_file, mode="w", compression=zipfile.ZIP_STORED)
        self._written_hashes: set[str] = set()
        self._manifest_written = False
        #: The published archive's size/sha256, from the still-private
        #: temp file -- avoids a TOCTOU vs. a concurrent writer's re-read.
        self.stored_sha256: str | None = None
        self.stored_size_bytes: int | None = None

    def put_blob(self, payload: bytes) -> str:
        """Write *payload* (zstd-compressed) if not already present under
        its own content hash; returns the hash either way. Deduplication
        happens here: a second `put_blob` call with byte-identical
        content is a no-op beyond computing the hash -- one member per
        unique content, regardless of how many entries reference it."""
        h = content_hash(payload)
        if h in self._written_hashes:
            return h
        # +1 for this blob, +1 reserved for the manifest member.
        if len(self._written_hashes) + 1 + 1 > MAX_ARCHIVE_MEMBERS:
            raise SnapshotError(
                f"BundleArchiveWriter: writing this blob would produce "
                f"more than {MAX_ARCHIVE_MEMBERS} zip members (the "
                "reader's own safety limit) -- refusing to write an "
                "archive that could not be reopened."
            )
        # Symmetry: read_blob()'s own default cap is exactly this value.
        if len(payload) > DEFAULT_MAX_BLOB_BYTES:
            raise SnapshotError(
                f"BundleArchiveWriter: this payload is {len(payload)} "
                f"bytes, exceeding the {DEFAULT_MAX_BLOB_BYTES} byte "
                "safety limit read_blob() enforces on load by default -- "
                "refusing to write an archive that could not be reopened."
            )
        zstandard = _zstd_module()
        compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        compressed = compressor.compress(payload)
        self._zf.writestr(_deterministic_zipinfo(_blob_member_name(h)), compressed)
        self._written_hashes.add(h)
        return h

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        if self._manifest_written:
            raise SnapshotError("BundleArchiveWriter.write_manifest() called twice")
        # Enforced here too, not only by write_bundle_facts_archive()'s
        # preflight. Streams via iterencode(), so it never fully materializes.
        from .bundle_archive_json_guard import oversized_raw_string

        oversized = oversized_raw_string(manifest, DEFAULT_MAX_MANIFEST_BYTES)
        if oversized is not None:
            _, oversized_bytes = oversized
            raise SnapshotError(
                f"manifest.json contains a single string value of at "
                f"least {oversized_bytes} bytes, alone exceeding the "
                f"{DEFAULT_MAX_MANIFEST_BYTES} byte safety limit read_manifest() "
                "enforces -- refusing to write an archive that could not be reopened."
            )
        chunks: list[str] = []
        encoded_bytes = 0
        for chunk in json.JSONEncoder(indent=2).iterencode(manifest):
            chunks.append(chunk)
            encoded_bytes += len(chunk.encode("utf-8"))
            if encoded_bytes > DEFAULT_MAX_MANIFEST_BYTES:
                raise SnapshotError(
                    f"manifest.json would be more than {encoded_bytes} "
                    f"bytes, exceeding the {DEFAULT_MAX_MANIFEST_BYTES} "
                    "byte safety limit read_manifest() enforces on load "
                    "-- refusing to write an archive that could not be "
                    "reopened."
                )
        encoded = "".join(chunks)
        self._zf.writestr(_deterministic_zipinfo(MANIFEST_MEMBER), encoded)
        self._manifest_written = True

    def close(self) -> None:
        if not self._manifest_written:
            self._abort()
            raise SnapshotError(
                "BundleArchiveWriter closed without write_manifest() -- the "
                "resulting archive would have no manifest.json member"
            )
        try:
            # Inside the guarded block: a failure writing the central
            # directory (ENOSPC/EIO) must not leave the temp file uncleaned.
            self._zf.close()
            # ZipFile.close() only flushes to the OS buffer cache -- fsync's
            # the same fd, mirroring snapshot_io._atomic_write_bytes.
            self._fsync_tmp_file()
            # Ownership before mode: chown() clears setuid/setgid. fchown/
            # fchmod on the fd, not the path, to dodge a substitution.
            if (
                self._existing_uid is not None or self._existing_gid is not None
            ) and hasattr(os, "fchown"):
                os.fchown(
                    self._tmp_file.fileno(),
                    self._existing_uid if self._existing_uid is not None else -1,
                    self._existing_gid if self._existing_gid is not None else -1,
                )
            # A brand-new archive needs no mode fixup -- `_open_unique_temp`
            # already applied the umask-filtered 0o666 default at creation.
            if self._existing_mode is not None and hasattr(os, "fchmod"):
                os.fchmod(self._tmp_file.fileno(), self._existing_mode)
            # Re-sync after chown/chmod: the earlier fsync only covers
            # content, not the inode metadata chown/chmod mutate after it.
            self._fsync_tmp_file()
            # A duplicated fd, not a path reopen, avoids the same
            # substitution race; `os.dup` shares the open-file description.
            self.stored_size_bytes = os.fstat(self._tmp_file.fileno()).st_size
            hasher = hashlib.sha256()
            read_fd = os.dup(self._tmp_file.fileno())
            os.lseek(read_fd, 0, os.SEEK_SET)
            with os.fdopen(read_fd, "rb") as tmp_reader:
                for chunk in iter(lambda: tmp_reader.read(1024 * 1024), b""):
                    hasher.update(chunk)
            self.stored_sha256 = hasher.hexdigest()
            self._tmp_file.close()
            # Known residual gap: os.replace() is path-based, so a swap
            # right before this call still publishes attacker content --
            # but stored_sha256 (real fd) detects it.
            os.replace(self._tmp_path, self._target)
        except BaseException:
            # Must not leave the potentially large temp file behind; a
            # no-op once os.replace() succeeded. Nested `finally` so the
            # unlink runs even if close() itself raises.
            try:
                if not self._tmp_file.closed:
                    self._tmp_file.close()
            finally:
                self._tmp_path.unlink(missing_ok=True)
            raise
        # os.replace()'s directory-entry update isn't durable until the
        # parent dir is fsync'd too. Skipped where there's no O_DIRECTORY.
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(self._target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                    raise
            finally:
                os.close(dir_fd)

    def _fsync_tmp_file(self) -> None:
        """Flush this class's userspace write buffer and fsync the result --
        a bare `os.fsync(fd)` alone would skip whatever's still in
        `self._tmp_file`'s buffered wrapper. Best-effort only for "no
        fsync support"; a real failure propagates."""
        self._tmp_file.flush()
        try:
            os.fsync(self._tmp_file.fileno())
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise

    def _abort(self) -> None:
        """Close the in-progress zip handle and discard its temp file --
        *path* (if it already held a prior archive) is never touched.
        `self._zf.close()`/`self._tmp_file.close()` can each raise --
        both nested in their own `finally` so the unlink always runs
        regardless of which fails (Codex)."""
        try:
            try:
                self._zf.close()
            finally:
                if not self._tmp_file.closed:
                    self._tmp_file.close()
        finally:
            self._tmp_path.unlink(missing_ok=True)

    def __enter__(self) -> BundleArchiveWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Only close() on a clean exit -- an exception mid-write should
        # propagate as-is, not be masked by "no manifest written yet".
        if exc_info[0] is None:
            self.close()
        else:
            self._abort()


class BundleArchiveReader:
    """Reads one content-addressed zip archive, lazily. `read_manifest()`
    and `read_blob()` each touch only the one zip member they name --
    `zipfile.ZipFile.open()`'s own contract, exactly why this format is
    zip rather than a solid-stream tar."""

    def __init__(self, path: str | Path, *, _fp: Any | None = None) -> None:
        self._path = Path(path)
        # Opened once; handed to both the preflight and `zipfile.ZipFile`
        # -- reopening would let a concurrent replacement swap generations.
        if _fp is not None:
            # Rewound inside the guarded try below, not here -- a seek()
            # failure outside any handler that closes it would leak fp.
            fp = _fp
        else:
            # Same O_NONBLOCK-open + fstat()-classify shape as
            # `open_regular_file_for_format_sniff` -- an explicit
            # `format="archive"` caller bypasses that sniff's own guard,
            # so a FIFO with no writer would otherwise hang here.
            nonblock = getattr(os, "O_NONBLOCK", 0)
            try:
                fd = os.open(self._path, os.O_RDONLY | nonblock)
                try:
                    st = os.fstat(fd)
                except OSError:
                    # fstat() itself failing (e.g. EIO) must not leak fd.
                    os.close(fd)
                    raise
                if not stat.S_ISREG(st.st_mode):
                    os.close(fd)
                    raise SnapshotError(
                        f"{self._path}: not a regular file -- a bundle "
                        "archive must be seekable, which a FIFO/socket/"
                        "device cannot provide."
                    )
                fp = os.fdopen(fd, "rb")
            except OSError as exc:
                raise SnapshotError(
                    f"{self._path}: not a valid bundle archive: {exc}"
                ) from exc
        try:
            if _fp is not None:
                fp.seek(0)
            from .bundle_archive_cd_guard import reject_absurd_central_directory

            validated_size = reject_absurd_central_directory(
                fp, self._path, max_entries=MAX_ARCHIVE_MEMBERS
            )
            # Sharing one fd closes a path-substitution race but not an
            # in-place one -- re-checked to narrow that window (see
            # reject_absurd_central_directory's own docstring).
            if os.fstat(fp.fileno()).st_size != validated_size:
                raise SnapshotError(
                    f"{self._path}: changed size while being opened -- "
                    "refusing to parse a central directory that may no "
                    "longer be the one just validated."
                )
            fp.seek(0)
            self._zf = zipfile.ZipFile(fp, mode="r")
        except (zipfile.BadZipFile, OSError, NotImplementedError, UnicodeDecodeError) as exc:
            # Every deliberate failure raises SnapshotError -- a truncated/
            # hand-assembled archive must not surface a raw zipfile
            # traceback (NotImplementedError: unsupported extract_version;
            # UnicodeDecodeError: an invalid UTF-8-flagged filename).
            fp.close()
            raise SnapshotError(f"{self._path}: not a valid bundle archive: {exc}") from exc
        except BaseException:
            # SnapshotError from the preflight, or anything else -- fp
            # must not leak even on a rejection this doesn't translate.
            fp.close()
            raise
        self._fp = fp

    @classmethod
    def open(cls, path: str | Path) -> BundleArchiveReader:
        return cls(path)

    @classmethod
    def from_open_file(cls, fp: Any, path: str | Path) -> BundleArchiveReader:
        """Construct from an already-open, seekable binary file object at
        *path* (left at any position -- rewound internally) -- shares
        the fd a caller used to sniff *path*'s format. Takes ownership
        of *fp* -- closed by this reader's own `close()`/context exit."""
        return cls(path, _fp=fp)

    def _read_stored_member(self, name: str, *, max_bytes: int) -> bytes:
        """Read one zip member's raw bytes, rejecting anything but
        ``ZIP_STORED`` compression, and bounding the read to *max_bytes*
        (a still-`ZIP_STORED` member can claim an enormous size --
        checked via `ZipInfo.file_size`, enforced via a chunked read).
        Every member `BundleArchiveWriter` produces is `ZIP_STORED`
        deliberately -- a `ZIP_DEFLATED` member could otherwise expand
        to an arbitrary allocation via ``ZipExtFile.read()``."""
        info = self._zf.getinfo(name)
        if info.compress_type != zipfile.ZIP_STORED:
            raise SnapshotError(
                f"{self._path}: member {name!r} uses compression method "
                f"{info.compress_type} instead of the required ZIP_STORED "
                "-- not a BundleArchiveWriter-produced archive, or a "
                "corrupted/hostile one."
            )
        if info.file_size > max_bytes:
            raise SnapshotError(
                f"{self._path}: member {name!r} claims {info.file_size} bytes, "
                f"exceeding the {max_bytes} byte safety limit -- refusing to "
                "read (possible decompression bomb, or a genuinely oversized "
                "member)."
            )
        # The zip "encrypted" bit makes open() raise a bare RuntimeError --
        # too generic to narrow in an except, so checked here.
        if info.flag_bits & 0x1:
            raise SnapshotError(
                f"{self._path}: member {name!r} is encrypted -- not a "
                "BundleArchiveWriter-produced archive, or a corrupted/"
                "hostile one."
            )
        # Flag bits 5/6 (patched data, strong encryption) make `open()`
        # raise a bare `NotImplementedError` -- same reason as above.
        if info.flag_bits & 0x60:
            raise SnapshotError(
                f"{self._path}: member {name!r} uses an unsupported "
                "general-purpose flag -- not a BundleArchiveWriter-"
                "produced archive, or a corrupted/hostile one."
            )
        out = io.BytesIO()
        try:
            with self._zf.open(name) as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    if out.tell() > max_bytes:
                        raise SnapshotError(
                            f"{self._path}: member {name!r} exceeds the "
                            f"{max_bytes} byte safety limit while streaming -- "
                            "refusing to continue reading (its declared "
                            "file_size did not match its actual stored bytes)."
                        )
        except zipfile.BadZipFile as exc:
            # ZipExtFile validates the member's CRC-32 as consumed (raised
            # typically at the `with` block's close) on a mismatch.
            raise SnapshotError(
                f"{self._path}: member {name!r} failed its CRC-32 check -- "
                "the archive is corrupted or was tampered with."
            ) from exc
        except UnicodeDecodeError as exc:
            # open() re-decodes the LOCAL header's own filename (a separate
            # copy from the central directory's, already validated) -- a
            # crafted local header can set its own UTF-8 bit invalidly.
            raise SnapshotError(
                f"{self._path}: member {name!r} has an invalid local file "
                f"header filename encoding: {exc}"
            ) from exc
        except OSError as exc:
            # A transient I/O failure (e.g. EIO) must not escape raw either.
            raise SnapshotError(
                f"{self._path}: member {name!r} could not be read: {exc}"
            ) from exc
        return out.getvalue()

    def read_manifest(self) -> dict[str, Any]:
        try:
            raw = self._read_stored_member(
                MANIFEST_MEMBER, max_bytes=DEFAULT_MAX_MANIFEST_BYTES
            )
        except KeyError as exc:
            raise SnapshotError(
                f"{self._path}: archive has no {MANIFEST_MEMBER!r} member"
            ) from exc
        # A sub-64 MiB manifest.json can still hold millions of container
        # nodes or nest pathologically deep -- bounded here, before
        # json.loads() ever runs (Codex review, fresh evidence).
        try:
            check_json_container_budget(raw, DEFAULT_MAX_MANIFEST_JSON_CONTAINER_NODES)
        except JsonContainerBudgetExceeded:
            raise SnapshotError(
                f"{self._path}: manifest.json contains more than "
                f"{DEFAULT_MAX_MANIFEST_JSON_CONTAINER_NODES} JSON containers "
                "-- refusing to decode (possible container-count "
                "amplification attack)"
            ) from None
        except JsonNestingTooDeepError:
            raise SnapshotError(f"{self._path}: manifest.json is too deeply nested to parse") from None
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            # Invalid UTF-8/JSON syntax (or Python 3.11+'s integer-digit
            # limit, a bare `ValueError`) must not surface raw (Codex).
            raise SnapshotError(
                f"{self._path}: manifest.json is not valid JSON: {exc}"
            ) from exc
        except RecursionError as exc:
            # Fallback net only -- the pre-scan above enforces this
            # portably (3.14's json.loads() no longer raises here at all).
            raise SnapshotError(
                f"{self._path}: manifest.json is too deeply nested to parse"
            ) from exc
        if not isinstance(value, dict):
            raise SnapshotError(
                f"{self._path}: manifest.json must be a JSON object, got "
                f"{type(value).__name__}"
            )
        return value

    def read_blob(
        self, content_hash_hex: str, *, max_decoded_bytes: int = DEFAULT_MAX_BLOB_BYTES
    ) -> bytes:
        """Decompress and return exactly the one blob named by
        *content_hash_hex* -- no other archive member is read.

        Streams the decompression in bounded chunks against
        *max_decoded_bytes* (a bare ``ZstdDecompressor.decompress(data)``
        call would allocate the full output regardless). Re-hashes the
        decoded payload against *content_hash_hex*. The outer, still-
        compressed read is bounded by `DEFAULT_MAX_STORED_BLOB_BYTES`
        (independent of *max_decoded_bytes* -- see its own docstring)."""
        member = _blob_member_name(content_hash_hex)
        try:
            compressed = self._read_stored_member(member, max_bytes=DEFAULT_MAX_STORED_BLOB_BYTES)
        except KeyError as exc:
            raise SnapshotError(
                f"{self._path}: manifest references blob {content_hash_hex!r} "
                f"with no corresponding archive member"
            ) from exc
        zstandard = _zstd_module()
        decompressor = zstandard.ZstdDecompressor(
            max_window_size=1 << _ZSTD_MAX_WINDOW_LOG
        )
        out = io.BytesIO()
        try:
            with decompressor.stream_reader(io.BytesIO(compressed)) as reader:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    if out.tell() > max_decoded_bytes:
                        raise SnapshotError(
                            f"{self._path}: decompressed blob {content_hash_hex!r} "
                            f"exceeds the {max_decoded_bytes} byte safety limit "
                            "-- refusing to continue decompressing (possible "
                            "decompression bomb, or a genuinely oversized blob)."
                        )
        except zstandard.ZstdError as exc:
            # Third-party exception type -- translated so a corrupted or
            # non-zstd payload still surfaces as this module's error type.
            raise SnapshotError(
                f"{self._path}: blob {content_hash_hex!r} failed to "
                f"decompress: {exc}"
            ) from exc
        # A frame truncated at just the right point can decompress above
        # with no error, silently yielding fewer bytes than intended --
        # this shared cross-check catches it (a hostile archive can name
        # a member after a truncated payload's own hash, defeating the
        # content-hash check below alone).
        validate_zstd_frame_completeness(
            zstandard, decompressor, compressed, source=f"{self._path}: blob {content_hash_hex!r}"
        )
        decoded = out.getvalue()
        actual_hash = content_hash(decoded)
        if actual_hash != content_hash_hex:
            raise SnapshotError(
                f"{self._path}: blob member {member!r} decoded to content "
                f"hashing {actual_hash!r}, not the {content_hash_hex!r} its "
                "own member name claims -- archive is corrupted or was not "
                "produced by BundleArchiveWriter."
            )
        return decoded

    def close(self) -> None:
        # ZipFile.close() does not close a file object it was *given* --
        # self._fp is ours to close.
        self._zf.close()
        self._fp.close()

    def __enter__(self) -> BundleArchiveReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
