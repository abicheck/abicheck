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
``dict``, and get raw bytes back. That split is deliberate: keeping this
module free of any ``model``/``compare``-layer import lets it join
`storage/` (ADR-061) cleanly, without resolving the pre-existing
``bundle_facts.py`` <-> ``checker_types.py`` (``model`` <-> ``compare``)
coupling a naive "construct a ``BundleFacts`` directly here" design would
hit.

The ``BundleFacts``-aware glue lives in ``serialization.py``'s
``save_bundle_facts``/``load_bundle_facts``, same as the plain-JSON format.
See the G40 design plan,
``docs/contribute/plans/g40-content-addressed-bundle-archive.md``.

Zip, not tar (`.tar.zst`, the original review sketch's own naming): zip
carries a real end-of-file central directory naming every member's offset
and independently-compressed length, so `zipfile.ZipFile.open(name)` reads
and decompresses exactly one member without touching any other. Each
member's own *payload* is zstd-compressed independently (``ZIP_STORED``)
rather than zip's own ``ZIP_DEFLATED``, this project's codec of record
(ADR-059).
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

#: The manifest member's own name -- always the first thing a reader
#: touches, readable without scanning or decompressing any blob member.
MANIFEST_MEMBER = "manifest.json"

#: Blob member naming: content-hash-addressed, one per unique payload.
_BLOB_PREFIX = "blobs/"
_BLOB_SUFFIX = ".json.zst"

#: zstd compression level for archive blobs. Matches ADR-059's own
#: ``ZSTD_LEVEL_BASELINE`` reasoning: a bundle archive is written rarely
#: (an explicit capture/convert step) and read often, so it takes the
#: slow/best-ratio end rather than the fast, internal-cache end.
ZSTD_LEVEL = 19

#: Same reasoning as `snapshot_io.py`'s own `_ZSTD_MAX_WINDOW_LOG`: bound
#: decompression memory to a window a legitimate blob will never need,
#: rather than trusting an archive's own embedded frame parameters --
#: per-blob, so one oversized blob can't exhaust memory on an unrelated one.
_ZSTD_MAX_WINDOW_LOG = 27  # 128 MiB

#: Per-blob decompressed-size cap, mirroring `snapshot_io.py`'s own
#: `DEFAULT_MAX_DECODED_BYTES` (same 1 GiB value, independently applied --
#: this module doesn't import that constant; see the module docstring for
#: why it avoids depending on `snapshot_io.py`).
DEFAULT_MAX_BLOB_BYTES = 1024 * 1024 * 1024

#: `manifest.json`'s own size cap -- far smaller than
#: `DEFAULT_MAX_BLOB_BYTES`: the manifest holds only name/hash pairs, not
#: payload content. Rejecting deflate for a member isn't itself a size
#: bound -- a still-`ZIP_STORED` member's own claimed size is read via
#: `ZipInfo.file_size` and checked before the read.
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024 * 1024

#: Slack added to a decoded-size cap when bounding the *outer*,
#: still-compressed blob member read -- zstd frame/block overhead can make
#: an incompressible payload's compressed form slightly larger than its
#: decoded size; generous enough to never spuriously reject a legitimate
#: payload at the cap, while the tighter decoded running-total check below
#: still catches a genuine decompression-bomb attempt.
_ZSTD_FRAME_OVERHEAD_SLACK_BYTES = 1024 * 1024


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


#: A fixed zip timestamp (the format's own epoch floor -- 1980-01-01,
#: since DOS-style zip timestamps can't represent anything earlier) used
#: for every member this module writes. `ZipFile.writestr(name, data)`
#: with a bare string `name` stamps its own `ZipInfo` with
#: `time.localtime()` at write time -- so saving byte-identical facts on
#: two different days would otherwise produce two different archives
#: (and two different `stored_sha256` values) for reproducible content.
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
    # `ZipInfo.__init__` defaults `create_system` to the *host* platform (0
    # Windows, 3 Unix) -- identical facts on Windows vs. Linux/macOS CI
    # would otherwise differ in bytes and `stored_sha256`. Pinned to 3
    # (Unix) unconditionally, matching this project's CI/release platforms.
    info.create_system = 3
    return info


_ZIP_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06")


def sniff_bundle_archive_format(path: str | Path) -> str:
    """``"archive"`` if *path*'s own bytes start with a zip local-file-header
    or empty-archive magic; ``"json"`` otherwise (including gzip/zstd,
    which the plain-JSON ``BundleFacts`` path already detects and
    transparently decompresses from those same magic-byte conventions).
    Used by ``serialization.load_bundle_facts``'s ``format="auto"``.

    Always ``"json"`` for a non-regular-file source (a FIFO, `/dev/stdin`,
    a socket) without reading anything from it (Codex review): a real
    bundle archive can never be delivered that way regardless --
    `zipfile.ZipFile` seeks to the *end* of its input, which a
    non-seekable stream can't support -- so consuming this sniff's own
    4-byte peek could hang or misparse a pipe that isn't rewindable.
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        return "json"
    try:
        with open(p, "rb") as f:
            prefix = f.read(4)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return _classify_prefix(prefix)


def _classify_prefix(prefix: bytes) -> str:
    return "archive" if prefix.startswith(_ZIP_MAGIC_PREFIXES) else "json"


def open_regular_file_for_format_sniff(
    path: str | Path,
) -> tuple[Any | None, str]:
    """``format="auto"``'s fd-sharing counterpart to
    `sniff_bundle_archive_format` -- opens *path* once, peeks its 4-byte
    magic, and returns ``(fp, "archive"|"json")`` with *fp* left at its
    post-peek position for the caller to reuse (`BundleArchiveReader.
    from_open_file`) instead of reopening *path* a second time, else a
    concurrent atomic replacement between two opens could swap in a
    different generation (Codex review). ``(None, "json")`` for a
    non-regular-file source -- the caller must not close *fp* then."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        return None, "json"
    try:
        fp = open(p, "rb")
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    try:
        prefix = fp.read(4)
    except OSError as exc:
        # A failure reading the peek itself (e.g. EIO on a network
        # filesystem) must not leak the fd or propagate a raw OSError --
        # this module's whole error contract is SnapshotError.
        fp.close()
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return fp, _classify_prefix(prefix)


#: A real bundle archive (one manifest member + one per *distinct* content
#: hash) never needs anywhere near this many members -- a crafted archive
#: claiming more is rejected before `zipfile.ZipFile` is constructed, since
#: `ZipFile.__init__` eagerly parses the whole central directory and
#: builds one `ZipInfo` per entry. Below 0xFFFF (the non-ZIP64 EOCD
#: sentinel, "read the real count from ZIP64 instead", handled below). Public
#: (no leading underscore): `bundle_facts.py` aligns its own writer-side
#: member-count budget to this reader-side cap so it can never write an
#: archive it wouldn't itself agree to reopen (Codex review, fresh evidence).
MAX_ARCHIVE_MEMBERS = 20_000

#: Central-directory bomb guard (EOCD/ZIP64 preflight) lives in a sibling
#: module, `bundle_archive_cd_guard.py`, split out purely to stay under
#: this module's ADR-061 800-line production cap -- see that module's own
#: docstring. `reject_absurd_central_directory` is imported below, at each
#: call site, rather than aliased here.


def content_hash(payload: bytes) -> str:
    """The content-address of *payload* -- sha256 hex digest. A public
    function (not folded into the writer) so a caller can compute a hash to
    check against an already-known manifest entry without opening the
    archive at all."""
    return hashlib.sha256(payload).hexdigest()


def _open_unique_temp(parent: Path, prefix: str, suffix: str) -> tuple[int, Path]:
    """Atomically create a unique, exclusively-owned temp file in *parent*,
    mode ``0o666`` filtered through the umask at creation. Mirrors
    ``snapshot_io._open_unique_temp`` (not imported, to stay dependency-
    free), except ``O_RDWR`` not ``O_WRONLY``: `BundleArchiveWriter.
    close()` reads this same fd back (via `os.dup`) to hash what it
    actually wrote, without a path-based reopen a hostile actor sharing
    the directory could redirect (Codex review, fresh evidence). Retries
    on a name collision rather than a process-wide ``os.umask()`` dance."""
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
    manifest references has already been written (so a reader opening a
    completed archive never observes a manifest naming a hash with no
    corresponding member).

    Writes go to a temporary file next to the real destination; *close()*
    (a clean context-manager exit) only ``os.replace()``s it over the
    destination once the archive is fully written, so an in-progress write
    interrupted by any error can never leave a truncated archive in the
    destination's place when it already held a prior, valid one.

    If *path* is itself a symlink, the temp file is created next to -- and
    ``close()`` replaces -- the link's *real target*, not the link itself
    (a bare ``os.replace(tmp, path)`` on a symlink destination would
    destroy the link for every other reader still following it). Mirrors
    ``snapshot_io._atomic_write_bytes``'s own symlink handling.

    A pre-existing destination with more than one hard link is rejected
    outright: replacing just this one directory entry would silently
    desynchronize every other link. The destination's existing file mode
    (and, where supported, owner/group) are preserved onto the
    replacement, mirroring `snapshot_io._atomic_write_bytes`'s own guard --
    ownership restoration is *not* best-effort, a failed chown aborts the
    write. *path*'s parent directory is created (``parents=True``) if
    missing, matching the ``format="json"`` path's own behavior."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._target = (
            Path(os.path.realpath(self._path)) if self._path.is_symlink() else self._path
        )
        try:
            existing_stat = self._target.stat()
        except (FileNotFoundError, NotADirectoryError):
            # Only genuine absence is treated as "no pre-existing
            # destination" -- any other OSError (Codex review, fresh
            # evidence: a cyclic symlink raises ELOOP here) must propagate
            # rather than be silently treated as absence, which would
            # bypass the regular-file/hard-link/metadata-preservation
            # checks below for a destination whose real type was never
            # actually established. Mirrors
            # `snapshot_io._atomic_write_bytes`'s own identical rule.
            existing_stat = None
        self._existing_mode: int | None = None
        self._existing_uid: int | None = None
        self._existing_gid: int | None = None
        if existing_stat is not None:
            if not stat.S_ISREG(existing_stat.st_mode):
                # os.replace() would silently destroy a pre-existing FIFO/
                # socket/device by installing a regular zip in its place --
                # unlike snapshot_io._atomic_write_bytes (which can write
                # straight through a non-regular destination, since it
                # already holds the complete payload as one bytes object),
                # this writer builds a zip incrementally into a temp file
                # and only ever publishes via an atomic rename, so there is
                # no way to "write through" such a destination at all
                # (Codex review).
                raise SnapshotError(
                    f"{self._target}: already exists and is not a regular "
                    "file (a FIFO, socket, or device) -- refusing to "
                    "replace it with a zip archive."
                )
            if existing_stat.st_nlink > 1:
                raise SnapshotError(
                    f"{self._target}: has {existing_stat.st_nlink} hard links -- "
                    "an atomic rewrite would silently desynchronize the other "
                    "link(s) from this one (they would keep the old content, "
                    "not see the new write). Unlink the extra hard link(s) "
                    "first if you want this path atomically rewritten in "
                    "isolation."
                )
            self._existing_mode = stat.S_IMODE(existing_stat.st_mode)
            self._existing_uid = existing_stat.st_uid
            self._existing_gid = existing_stat.st_gid
        self._target.parent.mkdir(parents=True, exist_ok=True)
        # _open_unique_temp, not a predictable "<name>.tmp-<pid>-<id>" path
        # opened separately by zipfile.ZipFile -- such a name in a
        # directory writable by another account could be pre-created as a
        # symlink, and `ZipFile(path, mode="w")` follows symlinks. This
        # randomizes the name and opens it O_CREAT|O_EXCL, so the fd this
        # class holds always names a file we just created, at a
        # umask-filtered 0o666 rather than tempfile.mkstemp's hardcoded
        # 0600 (a brand-new archive under a typical umask must not be more
        # restrictive than the plain-JSON path's ordinary `open()` mode).
        tmp_fd, self._tmp_path = _open_unique_temp(
            self._target.parent, f".{self._target.name}.", ".tmp"
        )
        self._tmp_file = os.fdopen(tmp_fd, "wb")
        # A file object, not a path, is passed here deliberately -- see
        # above; ZipFile doesn't close a fileobj it didn't open itself, so
        # close()/_abort() below own closing self._tmp_file.
        self._zf = zipfile.ZipFile(self._tmp_file, mode="w", compression=zipfile.ZIP_STORED)
        self._written_hashes: set[str] = set()
        self._manifest_written = False
        #: The published archive's own size/sha256, computed from the still-
        #: private temp file right before `os.replace()` (see `close()`),
        #: not by re-reading *path* afterward -- avoids a real TOCTOU where
        #: a concurrent writer replacing the same destination in between
        #: would make a later re-read describe someone else's write instead
        #: of this one's (Codex review, fresh evidence). Set on success only.
        self.stored_sha256: str | None = None
        self.stored_size_bytes: int | None = None

    def put_blob(self, payload: bytes) -> str:
        """Write *payload* (zstd-compressed) if not already present under
        its own content hash; returns the hash either way.

        Deduplication happens here, at the point of writing: a second
        `put_blob` call with byte-identical content to an earlier one is a
        no-op beyond computing the hash -- the archive ends up with
        exactly one member for that content, regardless of how many
        logical entries reference it."""
        h = content_hash(payload)
        if h in self._written_hashes:
            return h
        # Enforced here too, not only by write_bundle_facts_archive()'s own
        # preflight -- a direct caller adding MAX_ARCHIVE_MEMBERS blobs
        # then write_manifest() would else exceed the reader's own cap.
        # +1 for this blob, +1 reserved for the mandatory manifest member.
        if len(self._written_hashes) + 1 + 1 > MAX_ARCHIVE_MEMBERS:
            raise SnapshotError(
                f"BundleArchiveWriter: writing this blob would produce "
                f"more than {MAX_ARCHIVE_MEMBERS} zip members (the "
                "reader's own safety limit) -- refusing to write an "
                "archive that could not be reopened."
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
        encoded = json.dumps(manifest, indent=2)
        # Enforced here too, not only by bundle_facts.write_bundle_facts_
        # archive()'s own higher-level preflight -- a direct caller of
        # this public primitive bypasses that check, and read_manifest()
        # rejects anything over this same limit unconditionally (Codex
        # review, fresh evidence).
        encoded_bytes = len(encoded.encode("utf-8"))
        if encoded_bytes > DEFAULT_MAX_MANIFEST_BYTES:
            raise SnapshotError(
                f"manifest.json would be {encoded_bytes} bytes, exceeding "
                f"the {DEFAULT_MAX_MANIFEST_BYTES} byte safety limit "
                "read_manifest() enforces on load -- refusing to write an "
                "archive that could not be reopened."
            )
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
            # Inside the guarded block (Codex review): a failure writing the
            # central directory (ENOSPC/EIO) previously happened before the
            # try, leaving the temp file behind uncleaned.
            self._zf.close()
            # ZipFile.close() only flushes to the OS buffer cache, not
            # storage -- fsync's the same fd this class already holds,
            # mirroring snapshot_io._atomic_write_bytes's own two-part
            # fsync. Best-effort only for "fs doesn't support fsync".
            self._fsync_tmp_file()
            # Ownership restored *before* mode: chown() silently clears
            # setuid/setgid on POSIX, so restoring mode first would let a
            # real 06755 destination's bits survive the chmod only to be
            # stripped by chown right after (Codex review). Not
            # best-effort, mirroring `snapshot_io.py`'s own fix: publishing
            # under the wrong owner/group can revoke real access.
            #
            # fchown/fchmod on `self._tmp_file`'s own fd, not chown/chmod on
            # `self._tmp_path` -- a shared, non-sticky directory writable by
            # another account could substitute a file/symlink at that path
            # between this exclusively-created fd and a later path-based
            # reopen (Codex review, fresh evidence). The fd held open since
            # creation cannot be redirected that way.
            if (
                self._existing_uid is not None or self._existing_gid is not None
            ) and hasattr(os, "fchown"):
                os.fchown(
                    self._tmp_file.fileno(),
                    self._existing_uid if self._existing_uid is not None else -1,
                    self._existing_gid if self._existing_gid is not None else -1,
                )
            # A brand-new archive needs no mode fixup -- `_open_unique_temp`
            # already applied the umask-filtered 0o666 default at creation,
            # without touching the process-wide umask.
            if self._existing_mode is not None and hasattr(os, "fchmod"):
                os.fchmod(self._tmp_file.fileno(), self._existing_mode)
            # Re-sync after chown/chmod, before replace: the earlier fsync
            # only guarantees content reached storage -- chown/chmod mutate
            # inode metadata afterward, which a crash could lose otherwise.
            self._fsync_tmp_file()
            # Computed from a duplicated fd of `self._tmp_file`, not by
            # reopening `self._tmp_path` -- the same path-substitution race
            # above would let this hash verify attacker content instead of
            # what was actually written. Write position is already
            # finished, so the `os.lseek` below can't disturb it (`os.dup`
            # shares the open-file description).
            self.stored_size_bytes = os.fstat(self._tmp_file.fileno()).st_size
            hasher = hashlib.sha256()
            read_fd = os.dup(self._tmp_file.fileno())
            os.lseek(read_fd, 0, os.SEEK_SET)
            with os.fdopen(read_fd, "rb") as tmp_reader:
                for chunk in iter(lambda: tmp_reader.read(1024 * 1024), b""):
                    hasher.update(chunk)
            self.stored_sha256 = hasher.hexdigest()
            self._tmp_file.close()
            # Known residual gap: `os.replace()` is inherently path-based
            # (no portable fd-scoped rename in stdlib `os`), so a
            # substitution right before this call still publishes attacker
            # content -- but stored_sha256/stored_size_bytes above were
            # already computed from the real fd, so this is no longer a
            # silent MITM: a caller verifying the published file against
            # stored_sha256 detects the mismatch. Full closure needs an
            # OS-specific fd-scoped publish primitive or rejecting unsafe
            # parent directories -- a separate design question.
            os.replace(self._tmp_path, self._target)
        except BaseException:
            # A failure anywhere above must not leave the -- potentially
            # large -- temp file behind next to the untouched destination;
            # a repeated failure (ENOSPC, EIO) would otherwise starve later
            # retries of the space they're trying to free (Codex review).
            # No-op once os.replace() has already succeeded.
            #
            # The unlink is in a nested `finally` -- not a plain sibling
            # statement -- so it still runs even when close() *itself*
            # raises (e.g. ENOSPC/EIO flushing buffered bytes during an
            # exception-driven abort); a plain sibling statement would
            # never reach the unlink in that case (Codex review, fresh
            # evidence).
            try:
                if not self._tmp_file.closed:
                    self._tmp_file.close()
            finally:
                self._tmp_path.unlink(missing_ok=True)
            raise
        # os.replace()'s directory-entry update isn't durable across a
        # crash until the *parent* dir is fsync'd too (mirrors
        # snapshot_io._atomic_write_bytes's identical fsync). Skipped where
        # there's no O_DIRECTORY concept (Windows).
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
        a bare `os.fsync(fd)` without first flushing `self._tmp_file`'s
        buffered wrapper (`os.fdopen`) would skip whatever's still sitting
        in the Python-level buffer. Best-effort only for "no fsync support"
        (EINVAL/ENOTSUP/EOPNOTSUPP); a real storage failure propagates."""
        self._tmp_file.flush()
        try:
            os.fsync(self._tmp_file.fileno())
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise

    def _abort(self) -> None:
        """Close the in-progress zip handle and discard its temp file --
        *path* (if it already held a prior archive) is never touched.

        `self._zf.close()` itself can raise (CodeRabbit review: ENOSPC/EIO
        while writing the central directory) -- guarded so the temp file
        is still unlinked either way rather than left behind next to an
        untouched destination."""
        try:
            self._zf.close()
        finally:
            if not self._tmp_file.closed:
                self._tmp_file.close()
            self._tmp_path.unlink(missing_ok=True)

    def __enter__(self) -> BundleArchiveWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Only close() (validate + os.replace()) on a clean exit -- an
        # exception mid-write should propagate as-is, not be masked by "no
        # manifest written yet". Either way the temp file is discarded.
        if exc_info[0] is None:
            self.close()
        else:
            self._abort()


class BundleArchiveReader:
    """Reads one content-addressed zip archive, lazily.

    `read_manifest()` and `read_blob()` each touch only the one zip member
    they name -- `zipfile.ZipFile.open()`'s own contract, which is exactly
    why this format is zip rather than a solid-stream tar."""

    def __init__(self, path: str | Path, *, _fp: Any | None = None) -> None:
        self._path = Path(path)
        # Opened once; the identical fd is handed to both the preflight
        # below and `zipfile.ZipFile` -- reopening *path* a second time for
        # `ZipFile` would let a concurrent atomic replacement swap in a
        # different generation in between (Codex review, fresh evidence).
        # `_fp`, when given, is an already-open fd a caller sniffed the
        # format from (`from_open_file`) -- extends the identical guarantee
        # one layer up, to the sniff-then-open gap (see that classmethod).
        if _fp is not None:
            fp = _fp
            fp.seek(0)
        else:
            try:
                fp = open(self._path, "rb")
            except OSError as exc:
                raise SnapshotError(
                    f"{self._path}: not a valid bundle archive: {exc}"
                ) from exc
        try:
            from .bundle_archive_cd_guard import reject_absurd_central_directory

            reject_absurd_central_directory(fp, self._path, max_entries=MAX_ARCHIVE_MEMBERS)
            fp.seek(0)
            self._zf = zipfile.ZipFile(fp, mode="r")
        except (zipfile.BadZipFile, OSError) as exc:
            # Every deliberate failure in this module raises SnapshotError,
            # which the CLI boundary translates into a clean usage error --
            # a truncated or hand-assembled archive must not surface as a
            # raw zipfile traceback instead (CodeRabbit review).
            # sniff_bundle_archive_format() only checks the first 4 bytes,
            # so a damaged archive routinely passes that detection and
            # reaches this constructor.
            fp.close()
            raise SnapshotError(f"{self._path}: not a valid bundle archive: {exc}") from exc
        except BaseException:
            # SnapshotError from the preflight itself, or anything else --
            # the fd must not leak even on a rejection this constructor
            # doesn't translate.
            fp.close()
            raise
        self._fp = fp

    @classmethod
    def open(cls, path: str | Path) -> BundleArchiveReader:
        return cls(path)

    @classmethod
    def from_open_file(cls, fp: Any, path: str | Path) -> BundleArchiveReader:
        """Construct from an already-open, seekable binary file object at
        *path* (left at any position -- rewound internally) -- shares the
        fd a caller used to sniff *path*'s format, closing the gap
        between "this looked like an archive" and "open it as one" (Codex
        review, fresh evidence). Takes ownership of *fp* -- closed by this
        reader's own `close()`/context-manager exit."""
        return cls(path, _fp=fp)

    def _read_stored_member(self, name: str, *, max_bytes: int) -> bytes:
        """Read one zip member's raw bytes, rejecting anything but
        ``ZIP_STORED`` compression, and bounding the read to *max_bytes*
        (Codex review, two rounds).

        Every member `BundleArchiveWriter` produces is `ZIP_STORED`
        deliberately -- a crafted `ZIP_DEFLATED` member could otherwise
        expand to an arbitrary in-memory allocation via ``ZipExtFile.
        read()``. Rejecting deflate alone isn't a size bound though: a
        still-`ZIP_STORED` member can claim (and contain) an enormous
        size -- checked via the cheap ``ZipInfo.file_size`` metadata,
        enforced for real via a bounded, chunked read. Checked here,
        once, for both `read_manifest` and `read_blob`.
        """
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
        # The zip "encrypted" bit (bit 0 of `flag_bits`) makes `ZipFile.
        # open()` raise a bare `RuntimeError`, not `BadZipFile` -- the only
        # exception the except clause below translates -- so it would leak
        # past this module's SnapshotError contract (Codex review, fresh
        # evidence). No member this writer produces is ever encrypted, so
        # checked here rather than caught after (RuntimeError is too
        # generic to safely narrow in an except clause).
        if info.flag_bits & 0x1:
            raise SnapshotError(
                f"{self._path}: member {name!r} is encrypted -- not a "
                "BundleArchiveWriter-produced archive, or a corrupted/"
                "hostile one."
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
            # ZipExtFile validates the member's CRC-32 as it's consumed,
            # raising BadZipFile (typically at the `with` block's own close)
            # on a mismatch -- otherwise a raw zipfile exception escaping
            # this module's "every failure raises SnapshotError" contract.
            raise SnapshotError(
                f"{self._path}: member {name!r} failed its CRC-32 check -- "
                "the archive is corrupted or was tampered with."
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
        value = json.loads(raw)
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
        *content_hash_hex* -- no other archive member is read or
        decompressed.

        Streams the decompression in bounded chunks and enforces
        *max_decoded_bytes* against the running decoded size (mirroring
        `snapshot_io.py`'s own `_decompress_zstd` pattern) -- a bare
        ``ZstdDecompressor.decompress(data)`` call would allocate the full
        decoded output regardless of this function's own window-size bound.

        Also re-hashes the decoded payload and checks it against
        *content_hash_hex* -- the member name alone is just a zip entry
        name, not a verified property of its content (Codex review).

        The *outer*, still-compressed member read is bounded by
        *max_decoded_bytes* plus a fixed slack margin, not the bare
        decoded cap -- zstd's own overhead can make an incompressible
        payload's compressed form larger than its decoded size (Codex
        review). The decoded running-total check below is the tight bound.
        """
        member = _blob_member_name(content_hash_hex)
        try:
            compressed = self._read_stored_member(
                member, max_bytes=max_decoded_bytes + _ZSTD_FRAME_OVERHEAD_SLACK_BYTES
            )
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
            # Not a SnapshotError already (this is the third-party
            # decompressor's own exception type) -- a corrupted or
            # non-zstd payload stored under a valid-looking hash-named
            # member must still surface as this module's normal error
            # type, not a raw zstandard traceback (CodeRabbit review).
            raise SnapshotError(
                f"{self._path}: blob {content_hash_hex!r} failed to "
                f"decompress: {exc}"
            ) from exc
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
