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
`storage/` (ADR-061) cleanly today, without resolving the pre-existing
``bundle_facts.py`` <-> ``checker_types.py`` (``model`` <-> ``compare``)
coupling a naive "construct a ``BundleFacts`` directly here" design would
hit (confirmed via ``scripts/check_architecture.py``: ``bundle_facts.py``'s
own ``TYPE_CHECKING``-only import of ``checker_types.DiffResult`` creates a
real ``model -> compare -> model`` cycle once it joins the ``model`` layer).

The ``BundleFacts``-aware glue lives in ``serialization.py``'s
``save_bundle_facts``/``load_bundle_facts``, same as the plain-JSON format.
See the G40 design plan,
``docs/contribute/plans/g40-content-addressed-bundle-archive.md`` (added in
PR #866, a separate branch -- merge that first if this file isn't present
yet where you're reading this) for the full design.

Zip, not tar (`.tar.zst`, the original review sketch's own naming): zip
carries a real end-of-file central directory naming every member's offset
and independently-compressed length, so `zipfile.ZipFile.open(name)` reads
and decompresses exactly one member without touching any other -- the
random-access property this format exists to provide. Each member's own
*payload* is zstd-compressed independently (``ZIP_STORED``, matching how
``snapshot_io.py`` already treats zstd as a payload transform independent
of its outer container) rather than zip's own ``ZIP_DEFLATED``, since zstd
is this project's compression codec of record (ADR-059) with materially
better ratios than deflate at comparable speed.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..errors import SnapshotError

#: The manifest member's own name inside the archive -- always the first
#: thing a reader touches, and (per the zip format) readable without
#: scanning or decompressing any blob member.
MANIFEST_MEMBER = "manifest.json"

#: Blob member naming: content-hash-addressed, one per unique payload.
_BLOB_PREFIX = "blobs/"
_BLOB_SUFFIX = ".json.zst"

#: zstd compression level for archive blobs. Matches ADR-059's own
#: ``ZSTD_LEVEL_BASELINE`` reasoning (`abicheck/snapshot_io.py`): a bundle
#: archive is written rarely (an explicit capture/convert step) and read
#: often, so it takes the slow/best-ratio end rather than the fast,
#: internal-cache end.
ZSTD_LEVEL = 19

#: Same reasoning as `snapshot_io.py`'s own `_ZSTD_MAX_WINDOW_LOG`: bound
#: decompression memory to a window a legitimate blob will never need,
#: rather than trusting an archive's own embedded frame parameters -- a
#: decompression-bomb guard applied per-blob here (unlike the single
#: whole-document read `snapshot_io.py` guards), so one oversized blob
#: cannot exhaust memory on a request for an unrelated, small blob
#: elsewhere in the same archive.
_ZSTD_MAX_WINDOW_LOG = 27  # 128 MiB

#: Per-blob decompressed-size cap, mirroring `snapshot_io.py`'s own
#: `DEFAULT_MAX_DECODED_BYTES` (same 1 GiB value, independently applied --
#: this module does not import that constant, since `snapshot_io.py` is not
#: itself part of `storage/` yet; see the module docstring for why this
#: module avoids depending on it).
DEFAULT_MAX_BLOB_BYTES = 1024 * 1024 * 1024

#: `manifest.json`'s own size cap -- deliberately far smaller than
#: `DEFAULT_MAX_BLOB_BYTES`: the manifest holds only name/hash pairs, not
#: payload content, so even a bundle referencing tens of thousands of
#: libraries stays well under this (Codex review: rejecting deflate for a
#: member is not itself a size bound -- a still-`ZIP_STORED` member's own
#: claimed size is read via `ZipInfo.file_size` and checked *before* the
#: read, so a crafted archive can't exhaust memory merely by claiming (and
#: actually storing) an enormous manifest member).
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024 * 1024

#: Slack added to a decoded-size cap when bounding the *outer*,
#: still-compressed blob member read -- zstd frame/block overhead can make
#: an incompressible payload's compressed form slightly larger than its
#: decoded size, and this margin is deliberately far more generous than
#: any real zstd frame needs, so it costs nothing against a genuine
#: decompression-bomb attempt (which the tighter decoded running-total
#: check below still catches) while never spuriously rejecting a
#: legitimate payload at the cap (Codex review).
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


#: A fixed zip timestamp (the zip format's own epoch floor -- 1980-01-01,
#: since DOS-style zip timestamps cannot represent anything earlier) used
#: for every member this module writes. `ZipFile.writestr(name, data)`
#: with a bare string `name` builds its own `ZipInfo` stamped with
#: `time.localtime()` at write time -- so saving byte-identical facts on
#: two different days would otherwise produce two different archives (and
#: two different `SnapshotWriteResult.stored_sha256` values) for content
#: that a caller has every reason to expect is reproducible (Codex
#: review).
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
    # Windows, 3 Unix), serialized into the central directory -- identical
    # facts on Windows vs. Linux/macOS CI would otherwise still differ in
    # bytes and `stored_sha256`. Pinned to 3 (Unix) unconditionally,
    # matching this project's actual CI/release platforms.
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
    bundle archive can never actually be delivered that way regardless --
    `zipfile.ZipFile` seeks to the *end* of its input to locate the
    central directory, which a non-seekable stream cannot support -- so
    consuming this sniff's own 4-byte peek from a non-regular source would
    only cost the caller's later, separate open (``read_snapshot_text``)
    those same bytes for no benefit, and could hang or misparse a pipe
    that isn't rewindable.
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
    return "archive" if prefix.startswith(_ZIP_MAGIC_PREFIXES) else "json"


#: A real bundle archive (one manifest member + one per *distinct* content
#: hash) never needs anywhere near this many members -- a crafted archive
#: claiming more is rejected before `zipfile.ZipFile` is constructed:
#: `ZipFile.__init__` eagerly parses the whole central directory and
#: builds one `ZipInfo` per entry, so an enormous count can exhaust memory
#: merely by being opened. Below 0xFFFF (the non-ZIP64 EOCD sentinel
#: meaning "read the real count from ZIP64 instead", handled below).
_MAX_ARCHIVE_MEMBERS = 20_000

#: Bytes to search from the end of the file for the End-Of-Central-
#: Directory record's signature -- the record itself is 22 bytes plus up
#: to a 64 KiB archive comment (the zip format's own comment-length field
#: is 2 bytes), so this comfortably covers the worst case.
_EOCD_SEARCH_WINDOW_BYTES = 65536 + 22

#: Cap on the central directory's own declared byte size: the entry-
#: *count* cap above isn't itself a byte-size bound -- a low
#: `total_entries` can still pair with an enormous `cd_size`, which
#: `zipfile.ZipFile` reads and parses until fully consumed regardless of
#: the entry count. A real archive's directory is small (~120 bytes per
#: `blobs/<64-hex-sha256>.json.zst` record); generous but bounded.
_MAX_CENTRAL_DIRECTORY_BYTES = 8 * 1024 * 1024

#: ZIP64 End-Of-Central-Directory Locator (20 bytes, always immediately
#: preceding the standard EOCD when ZIP64 is in play) and Record
#: signatures -- recover the real count/size when the standard EOCD's
#: 2-/4-byte fields overflow to their ZIP64 sentinels.
_ZIP64_EOCD_LOCATOR_SIG = b"PK\x06\x07"
_ZIP64_EOCD_RECORD_SIG = b"PK\x06\x06"
_ZIP64_EOCD_LOCATOR_SIZE = 20


def _reject_absurd_central_directory(path: Path) -> None:
    """Reject *path* if its central directory claims more than
    `_MAX_ARCHIVE_MEMBERS` entries or `_MAX_CENTRAL_DIRECTORY_BYTES` --
    read directly from the EOCD (and, when present, the ZIP64 EOCD
    locator/record), without invoking `zipfile.ZipFile`'s own
    central-directory parse (the unbounded work this preflights against).

    Best-effort: if the EOCD (or a present ZIP64 locator/record) can't be
    found/read, this silently returns rather than raising --
    `zipfile.ZipFile`'s own error/read is authoritative for those cases;
    this only ever *adds* an earlier rejection for common attack shapes,
    never a false one.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    tail_len = min(size, _EOCD_SEARCH_WINDOW_BYTES)
    try:
        with open(path, "rb") as f:
            f.seek(size - tail_len)
            tail = f.read(tail_len)
            idx = tail.rfind(b"PK\x05\x06")
            if idx == -1 or idx + 22 > len(tail):
                return
            # EOCD layout: signature(4) this_disk(2) cd_start_disk(2)
            # entries_this_disk(2) total_entries(2) cd_size(4) cd_offset(4)
            # comment_len(2) [comment...]
            total_entries = int.from_bytes(tail[idx + 10 : idx + 12], "little")
            cd_size = int.from_bytes(tail[idx + 12 : idx + 16], "little")
            if total_entries == 0xFFFF or cd_size == 0xFFFFFFFF:
                # ZIP64: the real values live in the ZIP64 EOCD record, whose
                # own locator is always the fixed 20 bytes immediately
                # preceding this standard EOCD's signature.
                eocd_abs = (size - tail_len) + idx
                locator_start = eocd_abs - _ZIP64_EOCD_LOCATOR_SIZE
                if locator_start < 0:
                    return
                f.seek(locator_start)
                locator = f.read(_ZIP64_EOCD_LOCATOR_SIZE)
                if len(locator) != _ZIP64_EOCD_LOCATOR_SIZE or not locator.startswith(
                    _ZIP64_EOCD_LOCATOR_SIG
                ):
                    return
                zip64_eocd_offset = int.from_bytes(locator[8:16], "little")
                f.seek(zip64_eocd_offset)
                # Fixed portion only (56 bytes) -- signature/total_entries/
                # cd_size all live within it; no need for the record's own
                # variable "extensible data sector" tail.
                record = f.read(56)
                if len(record) != 56 or not record.startswith(_ZIP64_EOCD_RECORD_SIG):
                    return
                total_entries = int.from_bytes(record[32:40], "little")
                cd_size = int.from_bytes(record[40:48], "little")
    except OSError:
        return
    if total_entries > _MAX_ARCHIVE_MEMBERS:
        raise SnapshotError(
            f"{path}: central directory claims {total_entries} entries, "
            f"exceeding the {_MAX_ARCHIVE_MEMBERS} safety limit -- refusing "
            "to open (possible memory-exhaustion attack, or a genuinely "
            "malformed archive)."
        )
    if cd_size > _MAX_CENTRAL_DIRECTORY_BYTES:
        raise SnapshotError(
            f"{path}: central directory claims {cd_size} bytes, exceeding "
            f"the {_MAX_CENTRAL_DIRECTORY_BYTES} byte safety limit -- "
            "refusing to open (possible memory-exhaustion attack, or a "
            "genuinely malformed archive)."
        )


def content_hash(payload: bytes) -> str:
    """The content-address of *payload* -- sha256 hex digest.

    A public function (not folded into the writer) so a caller can compute
    a hash to check against an already-known manifest entry without opening
    the archive at all.
    """
    return hashlib.sha256(payload).hexdigest()


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
    -- interrupted by any error, including one from *put_blob*/
    *write_manifest* themselves -- can never leave a truncated archive in
    the destination's place when it already held a prior, valid one (Codex
    review: the original revision opened *path* directly with ``mode="w"``,
    which truncates immediately).

    If *path* is itself a symlink, the temp file is created next to --
    and ``close()`` replaces -- the link's *real target*, not the link
    (Codex review: a bare ``os.replace(tmp, path)`` on a symlink
    destination swaps the symlink's own directory entry for a regular
    file, destroying the link; every other reader still following that
    link would then see nothing written here). Mirrors
    ``snapshot_io._atomic_write_bytes``'s own symlink handling.

    A pre-existing destination with more than one hard link is rejected
    outright, before any write starts (Codex review): replacing just this
    one directory entry would silently desynchronize every other link from
    it, leaving them pointing at the old, now-stale content while this
    call reports success. The destination's existing file mode (and,
    where supported, owner/group) are preserved onto the replacement --
    without this, the temp file's fresh-file permissions/ownership would
    silently replace a shared baseline's real access, mirroring
    `snapshot_io._atomic_write_bytes`'s own guard for the plain-JSON path.
    Ownership restoration is *not* best-effort: a failed `os.chown` aborts
    the write rather than silently publishing under the wrong owner/group
    (same hard-won lesson as `snapshot_io.py`'s own two-round fix for this
    exact failure mode -- see that function's docstring).

    *path*'s parent directory is created (``parents=True``) if missing, so
    ``save_bundle_facts(..., format="archive")`` behaves like the
    ``format="json"`` path already does via ``snapshot_io.write_snapshot_text``
    (Codex review) rather than raising ``FileNotFoundError`` on a first
    write below a not-yet-existing directory.
    """

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
        # tempfile.mkstemp (not a predictable "<name>.tmp-<pid>-<id>" path
        # opened separately by zipfile.ZipFile) -- a predictable temp name
        # in a directory writable by another account could be pre-created
        # as a symlink, and `ZipFile(path, mode="w")` follows symlinks.
        # mkstemp randomizes the name and opens it with O_CREAT|O_EXCL, so
        # the fd this class holds always names a file we just created.
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=self._target.parent, prefix=f".{self._target.name}.", suffix=".tmp"
        )
        self._tmp_path = Path(tmp_name)
        self._tmp_file = os.fdopen(tmp_fd, "wb")
        # A file object, not a path, is passed here deliberately -- see
        # above; ZipFile doesn't close a fileobj it didn't open itself, so
        # close()/_abort() below own closing self._tmp_file.
        self._zf = zipfile.ZipFile(self._tmp_file, mode="w", compression=zipfile.ZIP_STORED)
        self._written_hashes: set[str] = set()
        self._manifest_written = False

    def put_blob(self, payload: bytes) -> str:
        """Write *payload* (zstd-compressed) if not already present under
        its own content hash; returns the hash either way.

        Deduplication happens here, at the point of writing: a second
        `put_blob` call with byte-identical content to an earlier one in
        the same archive is a no-op beyond computing the hash -- the
        archive ends up with exactly one member for that content,
        regardless of how many logical entries (library snapshots, an
        instantiation manifest) reference it.
        """
        h = content_hash(payload)
        if h in self._written_hashes:
            return h
        zstandard = _zstd_module()
        compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        compressed = compressor.compress(payload)
        self._zf.writestr(_deterministic_zipinfo(_blob_member_name(h)), compressed)
        self._written_hashes.add(h)
        return h

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        if self._manifest_written:
            raise SnapshotError("BundleArchiveWriter.write_manifest() called twice")
        self._zf.writestr(
            _deterministic_zipinfo(MANIFEST_MEMBER), json.dumps(manifest, indent=2)
        )
        self._manifest_written = True

    def close(self) -> None:
        if not self._manifest_written:
            self._abort()
            raise SnapshotError(
                "BundleArchiveWriter closed without write_manifest() -- the "
                "resulting archive would have no manifest.json member"
            )
        try:
            # self._zf.close() is now *inside* the guarded block (Codex
            # review, fresh evidence): a failure while writing the central
            # directory (ENOSPC/EIO) previously happened before the try,
            # leaving the temp file behind uncleaned.
            self._zf.close()
            # Durability (Codex review, mirroring snapshot_io._atomic_write_bytes's
            # own two-part fsync): ZipFile.close() only flushes to the OS's
            # buffer cache via the underlying file object's own close(),
            # which is not itself a durability guarantee -- a power loss
            # between here and os.replace() could leave the temp file's
            # data unflushed to actual storage even though close() reported
            # success. fsync's the *same* fd this class already holds open
            # (self._tmp_file, from tempfile.mkstemp) rather than reopening
            # the path -- no reason to pay a second open when the fd is
            # already ours. Best-effort only in the narrow sense of "this
            # filesystem/platform doesn't support fsync" (EINVAL/ENOTSUP/
            # EOPNOTSUPP); a real storage failure (ENOSPC, EIO, EROFS) must
            # propagate rather than let os.replace() publish unconfirmed
            # content over a known-good archive.
            self._fsync_tmp_file()
            # Ownership restored *before* mode (Codex review, fresh
            # evidence): on POSIX, chown() silently clears a file's
            # setuid/setgid bits as a security measure -- restoring mode
            # first and chown second (the original order) let a real
            # 06755 destination's setuid/setgid bits survive the chmod
            # only to be stripped by the chown that followed it, so
            # rewriting such a destination silently downgraded it to
            # 0755. Not best-effort (mirroring `snapshot_io.py`'s own
            # two-round fix for this exact failure mode): silently
            # proceeding after a failed ownership restoration would
            # publish under the wrong owner/group, which can revoke real
            # access for a shared baseline's other readers.
            if (
                self._existing_uid is not None or self._existing_gid is not None
            ) and hasattr(os, "chown"):
                os.chown(
                    self._tmp_path,
                    self._existing_uid if self._existing_uid is not None else -1,
                    self._existing_gid if self._existing_gid is not None else -1,
                )
            if self._existing_mode is not None:
                os.chmod(self._tmp_path, self._existing_mode)
            else:
                # mkstemp() always creates its file at mode 0600 regardless
                # of umask (Codex review): for a brand-new archive, leaving
                # that in place publishes more restrictively than a normal
                # `open(..., "wb")` would, silently breaking shared-baseline
                # read access on a format switch. os.umask() has no "peek"
                # mode, so read-then-immediately-restore is the only way.
                current_umask = os.umask(0)
                os.umask(current_umask)
                os.chmod(self._tmp_path, 0o666 & ~current_umask)
            # Re-sync after chown/chmod, before replace (Codex review,
            # fresh evidence): the earlier fsync only guarantees the
            # *file content* reached storage -- chown/chmod mutate the
            # inode's own metadata afterward, which a crash between here
            # and os.replace() could then lose even though the content
            # itself is durable, silently publishing the wrong owner/
            # mode after a successful-looking write survives a reboot.
            self._fsync_tmp_file()
            self._tmp_file.close()
            os.replace(self._tmp_path, self._target)
        except BaseException:
            # Codex review: a failure anywhere in this block (closing the
            # zip, the fsync, the ownership/mode restoration, or the
            # replace itself) must not leave the -- potentially very
            # large -- temp file behind next to the untouched destination;
            # a repeated failure (ENOSPC, EIO) would otherwise accumulate
            # temp files and starve later retries of the very space
            # they're trying to free up. A no-op once os.replace() has
            # actually succeeded, since the temp path no longer exists at
            # that point.
            if not self._tmp_file.closed:
                self._tmp_file.close()
            self._tmp_path.unlink(missing_ok=True)
            raise
        # os.replace()'s directory-entry update isn't durable across a
        # crash until the *parent* dir is fsync'd too (mirrors
        # snapshot_io._atomic_write_bytes's identical parent-directory
        # fsync, same best-effort unsupported-filesystem carve-out). Never
        # runs where there's no O_DIRECTORY concept (Windows).
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
        # Only close (which validates a manifest was written, then does the
        # real os.replace()) on a clean exit -- an exception mid-write
        # should propagate as-is, not be masked by "no manifest written
        # yet" when the real cause is upstream. Either way the temp file is
        # discarded rather than left behind or promoted over *path*.
        if exc_info[0] is None:
            self.close()
        else:
            self._abort()


class BundleArchiveReader:
    """Reads one content-addressed zip archive, lazily.

    `read_manifest()` and `read_blob()` each touch only the one zip member
    they name -- `zipfile.ZipFile.open()`'s own contract, which is exactly
    why this format is zip rather than a solid-stream tar (see the module
    docstring).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        _reject_absurd_central_directory(self._path)
        try:
            self._zf = zipfile.ZipFile(self._path, mode="r")
        except (zipfile.BadZipFile, OSError) as exc:
            # Every deliberate failure in this module raises SnapshotError,
            # which the CLI boundary translates into a clean usage error --
            # a truncated or hand-assembled archive must not surface as a
            # raw zipfile traceback instead (CodeRabbit review).
            # sniff_bundle_archive_format() only checks the first 4 bytes,
            # so a damaged archive routinely passes that detection and
            # reaches this constructor.
            raise SnapshotError(f"{self._path}: not a valid bundle archive: {exc}") from exc

    @classmethod
    def open(cls, path: str | Path) -> BundleArchiveReader:
        return cls(path)

    def _read_stored_member(self, name: str, *, max_bytes: int) -> bytes:
        """Read one zip member's raw bytes, rejecting anything but
        ``ZIP_STORED`` compression, and bounding the read to *max_bytes*
        (Codex review, two rounds).

        Every member `BundleArchiveWriter` produces is `ZIP_STORED`
        deliberately (see the module docstring's "Zip, not tar" section) --
        so a member's stored size is exactly its (already zstd-compressed,
        for a blob) payload size, no zip-level amplification. A crafted
        `ZIP_DEFLATED` member could otherwise expand to an arbitrary
        in-memory allocation via ``ZipExtFile.read()`` before `read_blob`'s
        own zstd decoded-size guard runs. Rejecting deflate alone isn't a
        size bound though: a still-`ZIP_STORED` member can simply claim
        (and contain) an enormous size -- checked first via the cheap
        ``ZipInfo.file_size`` metadata, enforced for real via a bounded,
        chunked read rather than one unbounded ``f.read()`` (in case that
        metadata were spoofed). Checked here, once, for both
        `read_manifest` and `read_blob`.
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
        decoded output regardless of *this* function's own window-size
        bound, which defeats the point of a per-blob memory guard.

        Also re-hashes the decoded payload and checks it against
        *content_hash_hex* before returning -- the member name alone is
        just a zip entry name, not a verified property of its content, so a
        corrupted or hand-assembled archive storing arbitrary bytes under a
        given hash's member name would otherwise be handed back to the
        caller unchecked, defeating the whole point of content-addressing
        (Codex review).

        The *outer*, still-compressed member read is bounded by
        *max_decoded_bytes* plus a fixed slack margin, not by
        *max_decoded_bytes* alone -- zstd's own frame/block overhead can
        make an incompressible payload's compressed form a handful of
        bytes *larger* than its decoded size, so using the decoded cap
        as-is for the outer read would reject a payload that legitimately
        satisfies the documented decoded-size contract (Codex review).
        The decoded running-total check below remains the tight,
        authoritative bound.
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
        self._zf.close()

    def __enter__(self) -> BundleArchiveReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
