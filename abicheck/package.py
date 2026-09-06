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

"""Package extraction layer for compare-release (ADR-006).

Converts RPM, Deb, tar, conda, pip wheel packages into directories that
the existing compare-release pipeline can process.

The extraction flow is:

    Package → Extract → Directory → [compare-release] → AggregateResult

All extractors enforce strict security checks against path traversal,
symlink escapes, absolute paths, and special file types (character/block
devices, FIFOs).  See ``_validate_member_path()`` and
``TarExtractor._safe_extract()`` for the mandatory safety contract.
"""
from __future__ import annotations

import copy
import logging
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Protocol, runtime_checkable

from .errors import ExtractionSecurityError, SnapshotError

if TYPE_CHECKING:
    from .model.package_inventory import PackageInventory

_log = logging.getLogger(__name__)

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class ExtractResult:
    """Result of extracting a package."""

    lib_dir: Path  # path to extracted shared libraries
    debug_dir: Path | None = None  # path to extracted debug info
    header_dir: Path | None = None  # path to extracted headers (devel pkg)
    # Debian dpkg-gensymbols(1) contract shipped in a .deb's control.tar.* as
    # ./symbols (CLI-audit P2: "Debian .symbols not integrated" -- only
    # data.tar.* was ever read). None for every non-Deb extractor and for a
    # Deb package that ships no symbols file (most do not; only libraries
    # built with dpkg-gensymbols do).
    symbols_file: Path | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # ADR-065 S3 (D2's completeness proof): whether *this* result's lib_dir
    # holds the extractor's whole container. True for every archive
    # extractor below -- each unpacks the container in full or raises, so a
    # returned lib_dir is the package's complete content, never a partial
    # unpack that silently dropped a member. False for `DirExtractor`, whose
    # operand is a directory a user may have populated partially: that is
    # exactly the "absent is not removed" case, and the same reason a live
    # release directory is `InventoryCompleteness.UNPROVEN`.
    container_complete: bool = False


# ── Security validation ──────────────────────────────────────────────────────


def _validate_member_path(member_name: str, target_root: Path) -> Path:
    """Validate that an archive member path is safe to extract.

    Raises ExtractionSecurityError if the member contains path traversal,
    absolute paths, or resolves outside the extraction root.
    """
    # Reject absolute paths (check both OS-native and POSIX-style leading slash
    # so that "/etc/passwd" is caught on Windows too, where os.path.isabs("/…") is False)
    if os.path.isabs(member_name) or member_name.startswith("/"):
        raise ExtractionSecurityError(member_name, "absolute path in archive member")

    # Reject path traversal components
    parts = Path(member_name).parts
    if ".." in parts:
        raise ExtractionSecurityError(member_name, "path traversal via '..' component")

    # Canonicalize and verify destination stays within root
    dest = (target_root / member_name).resolve()
    root_resolved = target_root.resolve()
    try:
        dest.relative_to(root_resolved)
    except ValueError as exc:
        raise ExtractionSecurityError(
            member_name, f"resolved path escapes extraction root: {dest}"
        ) from exc

    return dest


def _validate_symlink_target(
    member_name: str, link_target: str, target_root: Path
) -> None:
    """Validate that a symlink target resolves within the extraction root.
    Resolves only `member_name`'s *lexical* parent (a string op, never its
    own final component): resolving the full path first instead followed a
    duplicate member's stale prior target, not its real parent (Codex)."""
    member_parent = (target_root / member_name).parent.resolve()
    resolved = (member_parent / link_target).resolve()
    root_resolved = target_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ExtractionSecurityError(
            member_name,
            f"symlink target '{link_target}' resolves outside extraction root: {resolved}",
        ) from exc


def _target_supports_symlinks(target_root: Path) -> bool:
    """Probe whether *target_root* can actually create a symlink. Most notably false on Windows without `SeCreateSymbolicLinkPrivilege` (no Developer Mode enabled, not running elevated): `os.symlink()` raises `OSError`, and Python's own `tarfile` extraction machinery (`TarFile.makelink()`) silently falls back to extracting a full copy of the symlink's *referenced* member instead -- no exception, no flag, nothing that distinguishes "a real symlink" from "a copy that used to be a symlink" in the extracted result. Left undetected, this surfaces far downstream as a confusing "manifest never declared this file" error rather than an honest diagnosis of the real, platform-level cause (Codex).

    A live, unlinked probe entry (mirroring `product_baseline.py`'s own `_umask_derived_mode()` technique) rather than a cached platform check: the answer can change within a process's lifetime (Developer Mode toggled, a differently-privileged re-exec), so caching risks staleness for no real benefit -- this probe is cheap, one create/unlink pair, and called at most once per extraction.

    Both probe entries use a `uuid4`-suffixed name (not a bare pid), created with exclusive-create semantics (`O_CREAT | O_EXCL`, matching `_umask_derived_mode()`'s own probe) rather than `Path.touch()`, and cleanup only ever removes an entry *this call* actually created -- a bare pid-suffixed name collides with a concurrent extraction in the same process (e.g. two threads unpacking into the same directory) or, worse, with real pre-existing user data at that exact predictable path, which `touch()` would silently reuse and the unconditional cleanup would then delete (Codex).

    Known gap, not fixed here (Codex): this probe (and `tarfile`'s own extraction call, `os.symlink(tarinfo.linkname, targetpath)` with no `target_is_directory` argument) always creates a *file*-type symlink on Windows, never a directory-type one -- `os.symlink()`'s `target_is_directory` parameter defaults to `False` there and `tarfile.TarFile.makelink()` never passes it. A symlink whose target is itself a directory (which the packer legitimately archives as a leaf, never descending into it) can therefore pass this probe on a fully-privileged Windows host and still restore as the wrong reparse-point type once actually extracted -- a `tarfile`-level limitation this probe cannot detect or work around, since it is unconditional on that platform rather than a function of privilege. Verifying and fixing this would need a real Windows host to confirm the failure mode and a custom directory-aware extraction step bypassing `tarfile.makelink()`'s own symlink creation, not a probe change.
    """
    probe_target = target_root / f".abicheck-symlink-probe-target-{uuid.uuid4().hex}"
    probe_link = target_root / f".abicheck-symlink-probe-link-{uuid.uuid4().hex}"
    target_created = False
    link_created = False
    try:
        os.close(os.open(probe_target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        target_created = True
        os.symlink(probe_target.name, probe_link)
        link_created = True
    except OSError:
        return False
    else:
        return True
    finally:
        if link_created:
            probe_link.unlink(missing_ok=True)
        if target_created:
            probe_target.unlink(missing_ok=True)


def _validate_member(
    member: tarfile.TarInfo,
    target_root: Path,
    *,
    supports_symlinks: bool = True,
    symlink_target_by_name: dict[str, str] | None = None,
) -> None:
    """Every `_safe_extract` member check, shared by the bulk (3.12+) and per-member (< 3.12) extraction paths so the two can't drift.

    `symlink_target_by_name`, when given, is one dict shared and mutated across every call in one extraction's archive-member order -- it lets a hard-link member's validation see whether the name it references was a *symlink*, and if so re-validate that symlink's raw target text as if it were being (re-)created at the hard link's own, possibly differently-nested location. This closes a real escape (Codex, fresh evidence): `tarfile.TarFile.makelink()` falls back to a full copy whenever `os.link()` fails, and for a hard link naming a *symlink* member, that fallback re-extracts the referenced symlink itself (`_find_link_target` finds the original `SYMTYPE` `TarInfo`, not a regular file) using its own unmodified, relative target text -- but at the hard link's own path, not the symlink's original one. A symlink `a/b/link -> ../../safe` validates safely at its own two-levels-deep location; a hard link `x` (at the archive root) naming `a/b/link` re-extracts, on fallback, as `x -> ../../safe` -- the identical text now resolved from one level shallower, escaping `target_root`. Whether `os.link()` actually fails is filesystem-dependent and unknowable at validation time, so this treats the fallback as the worst case (the same principle `_reject_hardlink_fallback_amplification` already applies to disk-usage amplification) rather than trusting it won't happen. Propagates transitively (a hard link to a hard link to a symlink) and evicts a name's entry the moment a later, non-symlink member reuses it, so no stale symlink text can leak to an unrelated hard link the way the round-64/65 amplification-check maps once did.
    """
    _validate_member_path(member.name, target_root)
    if member.ischr() or member.isblk() or member.isfifo():
        raise ExtractionSecurityError(
            member.name, "archive contains a device or FIFO entry"
        )
    if member.issym():
        _validate_symlink_target(member.name, member.linkname, target_root)
        if not supports_symlinks:
            # Not a security violation -- a platform/privilege limitation. tarfile would otherwise silently substitute a full copy of the symlink's target here, corrupting the archive's declared structure without any error at all (see _target_supports_symlinks()'s own docstring).
            raise SnapshotError(
                f"archive member {member.name!r} is a symlink, but this destination cannot create one (no privilege/support for symlinks here) -- extracting would silently substitute a full copy of its target instead of the declared link, corrupting the archive's structure. Extract somewhere that supports symlinks (on Windows: enable Developer Mode, or run elevated)."
            )
        if symlink_target_by_name is not None:
            symlink_target_by_name[member.name] = member.linkname
    elif member.islnk():
        _validate_member_path(member.linkname, target_root)  # a member name
        if symlink_target_by_name is not None:
            propagated = symlink_target_by_name.get(member.linkname)
            if propagated is not None:
                # The referenced member is (transitively) a symlink -- tarfile's fallback would re-create it here, so make sure its raw target text still resolves within bounds from THIS member's own location before allowing extraction.
                _validate_symlink_target(member.name, propagated, target_root)
                symlink_target_by_name[member.name] = propagated
            else:
                symlink_target_by_name.pop(member.name, None)
    elif symlink_target_by_name is not None:
        # A regular file (or other non-symlink, non-hard-link member)
        # reusing this exact name means any earlier symlink recorded
        # under it no longer applies to a further hard link.
        symlink_target_by_name.pop(member.name, None)


def _reject_oversized_declared_content(tf: tarfile.TarFile) -> None:
    """Reject an archive whose declared member sizes sum past the same limit `_safe_extract_zst_tar` enforces on the decompressed tar *stream* -- a GNU/PAX sparse member's `TarInfo.size` is the real logical extent tarfile materializes even though the stream only carries non-hole blocks. Iterates `tf` directly (not `tf.getmembers()`, which reads every header to EOF before returning anything) and also caps the member *count*, so millions of zero-length members -- whose declared sizes never trip the byte-sum check at all -- are rejected before all of them are parsed into memory, not after (Codex, two review rounds)."""
    max_bytes = _max_decompressed_tar_bytes()
    max_members = _max_tar_members()
    total = 0
    count = 0
    for member in tf:
        total += member.size
        count += 1
        if total > max_bytes:
            raise ExtractionSecurityError(
                member.name,
                f"archive declares more than the {max_bytes}-byte safety "
                f"limit's worth of extracted content (override via {_MAX_DECOMPRESSED_TAR_BYTES_ENV})",
            )
        if count > max_members:
            raise ExtractionSecurityError(
                member.name,
                f"archive declares more than the {max_members}-member safety "
                f"limit's worth of entries (override via {_MAX_TAR_MEMBERS_ENV})",
            )
    _reject_hardlink_fallback_amplification(tf, max_bytes)


def _reject_hardlink_fallback_amplification(
    tf: tarfile.TarFile, max_bytes: int
) -> None:
    """Reject an archive whose hard-link members could expand far past `max_bytes` if extraction falls back to copying instead of linking.

    A hard-link member's own declared `size` is 0 in tar format -- its data lives in the first-archived (regular) copy the link refers to by name -- so the byte-sum check above, which sums declared sizes as-is, never sees it. But `tarfile.TarFile.makelink()` falls back to extracting a full, independent copy of the *referenced* member whenever `os.link()` fails (unsupported filesystem, a cross-device link, ...), giving every alias its own distinct inode. An archive combining one large payload with up to the member-count cap's worth of hard links to it can therefore amplify real disk usage (and subsequent checksum work) far beyond the declared-byte-sum ceiling, entirely invisible to that check (Codex).

    Only ever called after the byte-sum/member-count pass above completed without rejecting, so `tf.getmembers()` here is free: for the seekable `TarFile` this function is always called with, that pass's own `for member in tf:` iteration already built the full `tf.members` list as a side effect (`TarFile.next()` does this regardless of iteration style for a non-stream file) -- getmembers() just returns it, no re-parse. Safe against a hard-link chain (a link to a link) and against a reference cycle, neither of which a real tar writer emits but an adversarial archive could.

    Resolves each hard link only against members *preceding* it in archive order, matching `tarfile.TarFile._getmember(tarinfo=...)`'s own truncated search (it slices `self.members` to everything before the link being resolved) -- a global, order-blind name map does not: an archive naming a large regular member `real`, many hard links to `real`, and then a *further*, zero-length regular member also named `real` would have a last-wins global map record size 0 for the name every earlier hard link actually resolves against, hiding the real amplification behind a member that appears too late to matter (Codex, fresh evidence). Built incrementally in one forward pass instead -- each member is resolved against, then folded into, the running map in the same order tarfile itself would see them.

    Keeps exactly one map, `resolved_size_by_name`, always holding the fully-resolved worst-case size for the *latest* member seen so far under a given name -- regardless of whether that latest member is a regular file or itself a hard link. An earlier revision kept two separate maps (`sizes_by_name` for regular files, `link_to_name` for hard-link targets) and never evicted a name's stale `sizes_by_name` entry when a later hard-link member reused that same name -- so a hard link A -> B, where B's name was earlier a small regular file B and is now itself a hard link to some large C, resolved through the stale `sizes_by_name[B]` (the small regular file's own size) instead of B's real, current worst-case size (C's) (Codex, fresh evidence). Storing one resolved size per name, overwritten unconditionally by every member in order, makes this impossible by construction: a hard link's own resolved size is looked up and folded into the map in the same step, so a subsequent link naming it always sees its true, current worst case with a single dict lookup -- no separate chain-walk or cycle guard needed, since the map is already fully resolved at each step rather than holding raw, unresolved link targets to walk later.
    """
    resolved_size_by_name: dict[str, int] = {}
    worst_case_total = 0
    for member in tf.getmembers():
        if member.islnk():
            # Dangling reference (no preceding member with this name):
            # extraction fails anyway, so the link's own declared size
            # (0 in tar format) is the honest worst case here.
            own_size = resolved_size_by_name.get(member.linkname, member.size)
        else:
            own_size = member.size
        worst_case_total += own_size
        if worst_case_total > max_bytes:
            raise ExtractionSecurityError(
                member.name,
                "archive's hard-link members could expand to more than "
                f"the {max_bytes}-byte safety limit's worth of content if "
                "this filesystem falls back to copying instead of "
                f"linking (override via {_MAX_DECOMPRESSED_TAR_BYTES_ENV})",
            )
        # Fold this member's *resolved* size into the map *after*
        # resolving it against the map, matching tarfile's own
        # precedes-me-only search -- unconditionally replacing whatever
        # this name previously mapped to, so a later member can never
        # resolve through a stale entry left by an earlier one.
        if member.isreg() or member.islnk():
            resolved_size_by_name[member.name] = own_size


# ── Protocol ─────────────────────────────────────────────────────────────────


@runtime_checkable
class PackageExtractor(Protocol):
    """Extract package contents to a temporary directory."""

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        """Extract package into target_dir and return extraction result."""
        ...

    def detect(self, pkg_path: Path) -> bool:
        """Return True if this extractor can handle the given path."""
        ...


# ── Tar extractor ────────────────────────────────────────────────────────────

#: Decompression-bomb defense for `.tar.zst` extraction (Codex review, fresh evidence): a tiny, highly compressible archive can otherwise fill the host/CI runner's disk via an unbounded `shutil.copyfileobj()` before any member is ever validated. Generous relative to `snapshot_io.py`'s 1 GiB single-snapshot limit (ADR-059) since this bounds a whole product archive's worth of shared libraries, not one snapshot payload. Overridable only via the private env var below, mirroring `snapshot_io.py`'s identical override mechanism.
DEFAULT_MAX_DECOMPRESSED_TAR_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB
_MAX_DECOMPRESSED_TAR_BYTES_ENV = "_ABICHECK_TAR_ZST_MAX_DECODED_BYTES"


def _max_decompressed_tar_bytes() -> int:
    override = os.environ.get(_MAX_DECOMPRESSED_TAR_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_DECOMPRESSED_TAR_BYTES


#: Independent of the byte-sum limit above -- millions of zero-length
#: members never trip a size check at all, but each still costs a retained
#: TarInfo object (Codex). Generous for any real archive.
DEFAULT_MAX_TAR_MEMBERS = 200_000
_MAX_TAR_MEMBERS_ENV = "_ABICHECK_TAR_ZST_MAX_MEMBERS"


def _max_tar_members() -> int:
    override = os.environ.get(_MAX_TAR_MEMBERS_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_TAR_MEMBERS


#: A PAX ('x'/'g') or GNU longname/longlink ('L'/'K') extended-header record's own declared byte length -- read from the SAME 512-byte header block every ordinary member's size is, so it's known before any data is read. `tarfile.TarInfo._proc_pax()`/`_proc_gnulong()` both read that many bytes in ONE `fileobj.read(size)` call before returning control to a caller iterating the archive, materializing the whole declared blob regardless of any per-member check a caller runs afterward -- so `_reject_oversized_declared_content`'s own streaming, member-by-member checks never get a chance to intervene before this allocation already happened (Codex, fresh evidence: reproduced an uncompressed tar with a 2 MiB PAX header being accepted even with a 1 MiB overall decoded-bytes limit, and noted the same shape applies to a highly compressible `.tar.zst` forcing an allocation approaching the far larger overall decoded-stream limit). Real PAX/GNU extended headers are practically always well under a few KiB (a handful of path/uid/gid/mtime key=value pairs, or a single long path/linkpath) even for unusual legitimate metadata, so this is generous headroom, not a tight fit.
DEFAULT_MAX_TAR_EXTENDED_HEADER_BYTES = 1 * 1024 * 1024  # 1 MiB
_MAX_TAR_EXTENDED_HEADER_BYTES_ENV = "_ABICHECK_TAR_ZST_MAX_EXTENDED_HEADER_BYTES"


def _max_tar_extended_header_bytes() -> int:
    override = os.environ.get(_MAX_TAR_EXTENDED_HEADER_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_TAR_EXTENDED_HEADER_BYTES


class _BoundedTarInfo(tarfile.TarInfo):
    """A `tarfile.TarInfo` that pre-screens a PAX/GNU extended-header record's declared size against `_max_tar_extended_header_bytes()` BEFORE `tarfile`'s own parsing reads that many bytes into memory in one call -- closing the gap `DEFAULT_MAX_TAR_EXTENDED_HEADER_BYTES`'s own docstring describes. Passed as `tarfile.open(..., tarinfo=_BoundedTarInfo)`; `TarFile` uses this class for every member it parses for the lifetime of that one open archive, so setting it once at open time covers every subsequent `next()` call, including ones made later by `getmembers()`/`extractall()` on the same `TarFile` object.

    Raising `ExtractionSecurityError` directly (rather than a `tarfile`-specific exception needing translation afterward) works cleanly here because `TarFile.next()`'s own exception handling only special-cases `tarfile`'s own header-parsing exception hierarchy (`EOFHeaderError`/`InvalidHeaderError`/`EmptyHeaderError`/`TruncatedHeaderError`/`SubsequentHeaderError`) plus `zlib.error`; any other exception -- ours included -- is re-raised completely unchanged, verified directly against a real archive before relying on it.

    Only `_proc_pax`/`_proc_gnulong` are overridden -- the other extended-record path, `_proc_sparse` (old-style GNU sparse), reads a bounded, fixed `BLOCKSIZE` (512 bytes) per iteration regardless of the record's own declared size, so it doesn't exhibit the single-large-allocation pattern this guards against.
    """

    def _proc_pax(self, tarfile_obj: tarfile.TarFile) -> tarfile.TarInfo:
        self._reject_if_oversized()
        return super()._proc_pax(tarfile_obj)  # type: ignore[misc,no-any-return]

    def _proc_gnulong(self, tarfile_obj: tarfile.TarFile) -> tarfile.TarInfo:
        self._reject_if_oversized()
        return super()._proc_gnulong(tarfile_obj)  # type: ignore[misc,no-any-return]

    def _reject_if_oversized(self) -> None:
        limit = _max_tar_extended_header_bytes()
        if self.size > limit:
            raise ExtractionSecurityError(
                self.name or "<extended header>",
                f"archive's extended header record declares {self.size} bytes, exceeding the {limit}-byte safety limit (override via {_MAX_TAR_EXTENDED_HEADER_BYTES_ENV})",
            )


#: The `zstd` CLI's own `--memory=` window-size bound (used only by the external-binary fallback below) rejects any value above 2048 MiB as "out of bound" (verified against a real `zstd` 1.5.5 binary) -- independent of, and much smaller than, DEFAULT_MAX_DECOMPRESSED_TAR_BYTES above, which bounds total decoded *output* size via this module's own streaming byte count, not the decoder's working-memory window. Mirrors snapshot_io.py's identical `_ZSTD_MAX_WINDOW_LOG = 31` ceiling for the zstandard-module decoder path.
_ZSTD_CLI_MAX_MEMORY_MB = 2048

#: Overall wall-clock deadline for the external `zstd` CLI fallback, covering both the streamed decode loop and the final wait/communicate calls -- matches what the pre-streaming `subprocess.run(..., timeout=120)` call enforced, so a stalled/hung process still can't block extraction indefinitely (Codex review, fresh evidence).
_ZSTD_CLI_TIMEOUT_S = 120

#: Bound on the reader-thread-to-main-thread chunk queue in the external `zstd` CLI fallback (each chunk is at most 1 MiB). Keeps the reader thread from racing arbitrarily far ahead of the main thread's own decompression-bomb size check -- an unbounded queue could otherwise buffer gigabytes of already-decoded data before total_bytes is ever compared against the limit (Codex review, fresh evidence). A small bound still allows real streaming throughput while keeping the in-memory decoded backlog to a fixed, small multiple of the chunk size.
_ZSTD_READER_QUEUE_MAXSIZE = 8


def _drain_reader_queue(
    chunk_queue: queue.Queue[bytes | None | Exception],
    reader_thread: threading.Thread,
    timeout: float,
) -> None:
    """Unblock a `_safe_extract_zst_tar` reader thread possibly stuck in
    `put()` on the now-full bounded queue after the consumer stopped
    draining it mid-abort -- keeps pulling items until the reader finishes
    on its own (its producer, the killed/exhausted zstd process, closes
    its pipe quickly) rather than leaking a permanently-blocked daemon
    thread and its queued chunks (Codex review, fresh evidence). *timeout*
    is a backstop, not the expected case.
    """
    deadline = time.monotonic() + timeout
    while reader_thread.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            chunk_queue.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
    reader_thread.join(timeout=max(0.0, deadline - time.monotonic()))


class TarExtractor:
    """Extract tar, tar.gz, tar.xz, tar.bz2, and .tgz archives."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        return name.endswith((".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz"))

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting tar archive: %s", pkg_path)
        if pkg_path.name.lower().endswith(".tar.zst"):
            self._safe_extract_zst_tar(pkg_path, target_dir)
        else:
            self._safe_extract(pkg_path, target_dir)
        return ExtractResult(lib_dir=target_dir, container_complete=True)

    @staticmethod
    def _safe_extract(archive_path: Path, target_dir: Path) -> None:
        """Extract tar archive with full security validation on every member.

        Validates each member before extraction:
        - Rejects absolute paths and path traversal (via ``_validate_member_path``)
        - Rejects symlinks that escape the extraction root
        - Rejects special file types (character/block devices, FIFOs) that could
          create dangerous filesystem entries
        """
        target_root = target_dir.resolve()
        with tarfile.open(archive_path, tarinfo=_BoundedTarInfo) as tf:
            _reject_oversized_declared_content(tf)
            # Probed once per extraction, not per member -- see
            # _target_supports_symlinks()'s own docstring for why this
            # isn't cached across calls instead.
            supports_symlinks = _target_supports_symlinks(target_root)
            # One dict shared across every _validate_member() call in this
            # extraction, in archive-member order -- see _validate_member()'s
            # own docstring for why a hard-link member's validation needs it.
            symlink_target_by_name: dict[str, str] = {}
            if sys.version_info >= (3, 12):
                for member in tf.getmembers():
                    _validate_member(
                        member,
                        target_root,
                        supports_symlinks=supports_symlinks,
                        symlink_target_by_name=symlink_target_by_name,
                    )
                # `filter="data"` (PEP 706) carries its own, equivalent per-member safety contract.
                tf.extractall(path=target_dir, filter="data")  # nosec B202 — members validated above
            else:
                TarExtractor._safe_extract_member_by_member(
                    tf,
                    target_dir,
                    target_root,
                    supports_symlinks=supports_symlinks,
                    symlink_target_by_name=symlink_target_by_name,
                )

    @staticmethod
    def _safe_extract_member_by_member(
        tf: tarfile.TarFile,
        target_dir: Path,
        target_root: Path,
        *,
        supports_symlinks: bool = True,
        symlink_target_by_name: dict[str, str] | None = None,
    ) -> None:
        """Validate and extract each member right after the previous one
        lands on disk -- the Python < 3.12 fallback (`filter="data"` isn't
        reliably available there). Validate-all-then-bulk-extractall has a
        path-depth-collapse TOCTOU: a symlink `a -> .` validates as
        contained pre-extraction (sibling `a/b/c/link` reads as 3 real,
        nonexistent levels deep), but once `a` is a real symlink the
        filesystem collapses that sibling to 2 levels, letting
        `../../../victim` resolve outside target_root despite its own
        stale pre-extraction validation (Codex). Re-validating each member
        against the real, live-extracted state closes this generally."""
        directories: list[tarfile.TarInfo] = []
        for member in tf.getmembers():
            _validate_member(
                member,
                target_root,
                supports_symlinks=supports_symlinks,
                symlink_target_by_name=symlink_target_by_name,
            )
            if member.isdir():
                directories.append(member)
                extract_info = copy.copy(member)
                extract_info.mode = 0o700
            else:
                extract_info = member
            tf.extract(  # nosec B202 — member validated above, against live fs state
                extract_info, path=target_dir, set_attrs=not member.isdir()
            )
        # Mirror extractall()'s deferred dir attr fixup: extracted early
        # (mode 0o700, writable) then restored once every member lands.
        directories.sort(key=lambda info: info.name)
        directories.reverse()
        for member in directories:
            dirpath = os.path.join(target_dir, member.name)
            try:
                tf.chown(member, dirpath, numeric_owner=False)
                tf.utime(member, dirpath)
                tf.chmod(member, dirpath)
            except tarfile.ExtractError:
                pass

    @staticmethod
    def _safe_extract_zst_tar(zst_path: Path, target_dir: Path) -> None:
        """Extract a zstd-compressed tar archive with the normal tar safety checks."""
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".abicheck-zst-"))
        tar_path = staging / "payload.tar"
        try:
            try:
                import zstandard
            except ImportError:
                zstandard_mod: Any | None = None
            else:
                zstandard_mod = zstandard
            if zstandard_mod is not None:
                # Bounded, incremental decompression -- shutil.copyfileobj()
                # materialized the entire decoded tar unconditionally
                # before any member was ever validated, so a tiny, highly
                # compressible archive could fill the host/CI runner's
                # disk before extraction ever got a chance to raise
                # (Codex review, fresh evidence). max_window_size reuses
                # snapshot_io.py's own zstd-memory-bomb defense rather
                # than a second, independently-derived window ceiling.
                from .snapshot_io import _zstd_max_window_size_bytes

                max_decoded_bytes = _max_decompressed_tar_bytes()
                dctx = zstandard_mod.ZstdDecompressor(
                    max_window_size=_zstd_max_window_size_bytes(zstandard_mod)
                )
                total_bytes = 0
                with open(zst_path, "rb") as compressed, open(tar_path, "wb") as out:
                    with dctx.stream_reader(compressed) as reader:
                        while True:
                            chunk = reader.read(1024 * 1024)
                            if not chunk:
                                break
                            total_bytes += len(chunk)
                            if total_bytes > max_decoded_bytes:
                                raise SnapshotError(
                                    f"{zst_path}: decompressed payload exceeds "
                                    f"the {max_decoded_bytes} byte safety "
                                    "limit -- refusing to continue "
                                    "decompressing (possible decompression "
                                    f"bomb; override via "
                                    f"{_MAX_DECOMPRESSED_TAR_BYTES_ENV})"
                                )
                            out.write(chunk)
            else:
                zstd = shutil.which("zstd")
                if zstd is None:
                    raise SnapshotError(
                        "Cannot extract .tar.zst: install 'zstandard' Python package "
                        "or 'zstd' command-line tool."
                    )
                # Streamed via a pipe and bounded the same way as the zstandard-module branch above -- the earlier `zstd -d -f ... -o ...` form let the CLI write its own decoded output straight to disk, unbounded, so only the Python-module decoder path got the size limit even though both are reachable in production (this fallback runs whenever the always-installed `zstandard` package is somehow absent) (Codex review, fresh evidence). `--memory=` bounds the CLI's own decompression window -- unrelated to max_decoded_bytes (total output size, enforced by the streaming loop below); see _ZSTD_CLI_MAX_MEMORY_MB's own docstring for why these two bounds can't share a value.
                max_decoded_bytes = _max_decompressed_tar_bytes()
                proc = subprocess.Popen(
                    [
                        zstd,
                        "-dc",
                        f"--memory={_ZSTD_CLI_MAX_MEMORY_MB}MB",
                        str(zst_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                assert proc.stdout is not None
                stdout_pipe: IO[bytes] = proc.stdout
                total_bytes = 0
                # subprocess.run(..., timeout=120) previously bounded this fallback -- the streaming rewrite above (for the size bound) replaced it with a plain blocking proc.stdout.read() with no deadline at all, so a stalled/hung zstd process could block extraction indefinitely (Codex review, fresh evidence). A read() on a pipe has no portable timeout parameter (select() doesn't work on Windows pipes), so the read is offloaded to a daemon thread and the main loop polls a queue with the same overall 120s deadline the old subprocess.run() call enforced.
                #
                # The queue is bounded (a small, fixed number of 1 MiB chunks) rather than unbounded: an untrusted archive that expands quickly can otherwise let the reader thread race ahead of the main thread's own size check, buffering gigabytes of decoded data in the queue before total_bytes > max_decoded_bytes is ever evaluated -- defeating the whole point of the decompression-bomb limit below (Codex review, fresh evidence). A bounded queue's put() blocks once full, so the reader thread stalls -- real pipe backpressure -- until the main thread drains a chunk, capping in-memory decoded data to a small, fixed multiple of the chunk size regardless of how fast zstd itself can decompress.
                chunk_queue: queue.Queue[bytes | None | Exception] = queue.Queue(
                    maxsize=_ZSTD_READER_QUEUE_MAXSIZE
                )

                def _reader() -> None:
                    try:
                        while True:
                            piece = stdout_pipe.read(1024 * 1024)
                            chunk_queue.put(piece if piece else None)
                            if not piece:
                                return
                    except Exception as exc:  # pragma: no cover - defensive
                        chunk_queue.put(exc)

                reader_thread = threading.Thread(target=_reader, daemon=True)
                reader_thread.start()
                deadline = time.monotonic() + _ZSTD_CLI_TIMEOUT_S
                try:
                    with open(tar_path, "wb") as out:
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                proc.kill()
                                proc.wait(timeout=_ZSTD_CLI_TIMEOUT_S)
                                raise SnapshotError(
                                    f"{zst_path}: 'zstd' decompression "
                                    "timed out after "
                                    f"{_ZSTD_CLI_TIMEOUT_S}s -- the process "
                                    "may be hung or stalled"
                                )
                            try:
                                chunk = chunk_queue.get(timeout=remaining)
                            except queue.Empty:
                                proc.kill()
                                proc.wait(timeout=_ZSTD_CLI_TIMEOUT_S)
                                raise SnapshotError(
                                    f"{zst_path}: 'zstd' decompression "
                                    "timed out after "
                                    f"{_ZSTD_CLI_TIMEOUT_S}s -- the process "
                                    "may be hung or stalled"
                                ) from None
                            if isinstance(chunk, Exception):
                                raise chunk
                            if chunk is None:
                                break
                            total_bytes += len(chunk)
                            if total_bytes > max_decoded_bytes:
                                proc.kill()
                                proc.wait(timeout=_ZSTD_CLI_TIMEOUT_S)
                                raise SnapshotError(
                                    f"{zst_path}: decompressed payload "
                                    f"exceeds the {max_decoded_bytes} byte "
                                    "safety limit -- refusing to continue "
                                    "decompressing (possible decompression "
                                    f"bomb; override via "
                                    f"{_MAX_DECOMPRESSED_TAR_BYTES_ENV})"
                                )
                            out.write(chunk)
                    _, stderr = proc.communicate(timeout=_ZSTD_CLI_TIMEOUT_S)
                    if proc.returncode != 0:
                        raise SnapshotError(
                            f"{zst_path}: 'zstd' decompression failed: "
                            f"{stderr.decode('utf-8', errors='replace')}"
                        )
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=_ZSTD_CLI_TIMEOUT_S)
                    # An abort (timeout, the size limit above, a reader exception) can leave _reader blocked in put() on the now-full bounded queue -- the consumer stopped draining it the moment it raised, so killing the process alone doesn't unblock a reader stuck on a full queue rather than its own read() call. Leaks a daemon thread and its queued chunks for the life of a long-running process otherwise (Codex review, fresh evidence). Cheap no-op on the success path, where the reader already finished before the terminal None was consumed.
                    _drain_reader_queue(chunk_queue, reader_thread, _ZSTD_CLI_TIMEOUT_S)
            TarExtractor._safe_extract(tar_path, target_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


# ── RPM extractor ────────────────────────────────────────────────────────────

_RPM_MAGIC = b"\xed\xab\xee\xdb"


class RpmExtractor:
    """Extract RPM packages using rpm2cpio + cpio."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".rpm"):
            return True
        # Check magic bytes
        try:
            with open(pkg_path, "rb") as f:
                return f.read(4) == _RPM_MAGIC
        except OSError:
            return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting RPM: %s", pkg_path)
        self._rpm_extract(pkg_path, target_dir)
        self._post_validate(target_dir)
        return ExtractResult(lib_dir=target_dir, container_complete=True)

    @staticmethod
    def _rpm_extract(rpm_path: Path, target_dir: Path) -> None:
        """Extract RPM via rpm2cpio | cpio pipeline."""
        rpm2cpio = shutil.which("rpm2cpio")
        cpio = shutil.which("cpio")
        if not rpm2cpio:
            raise SnapshotError(
                "rpm2cpio not found. Install rpm-tools or use a tar archive instead."
            )
        if not cpio:
            raise SnapshotError(
                "cpio not found. Install cpio or use a tar archive instead."
            )

        _EXTRACT_TIMEOUT = 120  # seconds

        rpm2cpio_proc = subprocess.Popen(
            [rpm2cpio, str(rpm_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        cpio_proc = subprocess.Popen(
            [cpio, "-id", "--no-absolute-filenames", "--quiet"],
            stdin=rpm2cpio_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(target_dir),
        )
        # Allow rpm2cpio to receive SIGPIPE
        if rpm2cpio_proc.stdout:
            rpm2cpio_proc.stdout.close()

        try:
            _cpio_out, cpio_err = cpio_proc.communicate(timeout=_EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            cpio_proc.kill()
            rpm2cpio_proc.kill()
            cpio_proc.wait()
            rpm2cpio_proc.wait()
            raise SnapshotError(
                f"RPM extraction timed out after {_EXTRACT_TIMEOUT}s"
            ) from exc

        try:
            rpm2cpio_proc.wait(timeout=_EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            rpm2cpio_proc.kill()
            rpm2cpio_proc.wait()
            raise SnapshotError(
                f"rpm2cpio timed out after {_EXTRACT_TIMEOUT}s"
            ) from exc

        if rpm2cpio_proc.returncode != 0:
            raise SnapshotError(f"rpm2cpio failed (exit {rpm2cpio_proc.returncode})")
        if cpio_proc.returncode != 0:
            err_msg = cpio_err.decode("utf-8", errors="replace").strip()
            raise SnapshotError(f"cpio extraction failed: {err_msg}")

    @staticmethod
    def _post_validate(target_dir: Path) -> None:
        """Post-extraction validation: check no paths escape root.

        Iterates both directory and file entries to catch directory symlinks
        or escaped paths that file-only validation would miss.  Uses
        ``topdown=True`` so directory symlinks are validated before descent.
        """
        root = target_dir.resolve()
        for dirpath, dirnames, filenames in os.walk(
            target_dir, followlinks=False, topdown=True
        ):
            dp = Path(dirpath)
            # Validate both directory and file entries
            for name in list(dirnames) + filenames:
                full = (dp / name).resolve()
                try:
                    full.relative_to(root)
                except ValueError as exc:
                    raise ExtractionSecurityError(
                        str(full), "extracted path escapes extraction root"
                    ) from exc
                # Check symlinks (files and directories)
                fp = dp / name
                if fp.is_symlink():
                    link_target = os.readlink(fp)
                    resolved = fp.resolve()
                    try:
                        resolved.relative_to(root)
                    except ValueError as exc:
                        raise ExtractionSecurityError(
                            str(fp.relative_to(target_dir)),
                            f"symlink target '{link_target}' escapes extraction root",
                        ) from exc


# ── Deb extractor ────────────────────────────────────────────────────────────

_DEB_MAGIC = b"!<arch>\n"


class DebExtractor:
    """Extract Debian packages using ar + tar."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".deb"):
            return True
        try:
            with open(pkg_path, "rb") as f:
                return f.read(8) == _DEB_MAGIC
        except OSError:
            return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting Deb: %s", pkg_path)
        symbols_file = self._deb_extract(pkg_path, target_dir)
        return ExtractResult(
            lib_dir=target_dir, symbols_file=symbols_file, container_complete=True
        )

    def _deb_extract(self, deb_path: Path, target_dir: Path) -> Path | None:
        """Extract Debian package: ar x to get data.tar.*/control.tar.*, then
        tar extract each. Returns the path to control.tar.*'s ``symbols``
        member (dpkg-gensymbols(1) contract) if the package ships one, else
        ``None`` -- most packages don't; only libraries built with
        dpkg-gensymbols do (CLI-audit P2)."""
        ar = shutil.which("ar")
        if not ar:
            raise SnapshotError(
                "ar not found. Install binutils or use a tar archive instead."
            )

        # ar extract into a staging area
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".deb_staging_"))
        try:
            subprocess.run(
                [ar, "x", str(deb_path.resolve())],
                cwd=str(staging),
                check=True,
                capture_output=True,
                timeout=120,
            )

            # Find data.tar.* member
            data_tar = None
            control_tar = None
            for candidate in staging.iterdir():
                if candidate.name.startswith("data.tar"):
                    data_tar = candidate
                elif candidate.name.startswith("control.tar"):
                    control_tar = candidate

            if data_tar is None:
                raise SnapshotError(
                    f"No data.tar.* found in Deb package: {deb_path}"
                )

            # Extract data.tar.* with security checks
            if data_tar.name.endswith(".tar.zst"):
                TarExtractor._safe_extract_zst_tar(data_tar, target_dir)
            else:
                TarExtractor._safe_extract(data_tar, target_dir)

            # control.tar.* holds package metadata (control, md5sums, and --
            # for a library package built with dpkg-gensymbols -- symbols).
            # Extracted into its own subdirectory so its ./control never
            # collides with a same-named path under data.tar.*'s payload.
            if control_tar is None:
                return None
            control_dir = target_dir / ".deb_control"
            # data.tar.* was just extracted into target_dir above and could
            # itself contain a member literally named .deb_control/symbols
            # (crafted or coincidental) -- if control.tar.* then has no
            # symbols member of its own, exist_ok=True would silently leave
            # that payload file in place and it would be returned below as
            # if it were the genuine dpkg-gensymbols(1) contract (Codex
            # review). Clear any pre-existing content first so only
            # control.tar.*'s real members ever populate this directory,
            # regardless of what data.tar.* planted at the same path.
            if control_dir.is_symlink() or control_dir.is_file():
                control_dir.unlink()
            elif control_dir.is_dir():
                shutil.rmtree(control_dir)
            control_dir.mkdir()
            if control_tar.name.endswith(".tar.zst"):
                TarExtractor._safe_extract_zst_tar(control_tar, control_dir)
            else:
                TarExtractor._safe_extract(control_tar, control_dir)
            symbols_path = control_dir / "symbols"
            return symbols_path if symbols_path.is_file() else None
        finally:
            shutil.rmtree(staging, ignore_errors=True)


# ── Zip-based security helper ────────────────────────────────────────────────


def _safe_zip_extract(archive_path: Path, target_dir: Path) -> None:
    """Extract a zip archive with full security validation on every member."""
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            _validate_member_path(info.filename, target_root)
        zf.extractall(path=target_dir)  # nosec B202 — members validated above


# ── Conda extractor ─────────────────────────────────────────────────────────


class CondaExtractor:
    """Extract conda packages (.conda v2 format and legacy .tar.bz2).

    .conda format is a zip archive containing:
      - metadata.json
      - pkg-<name>-<hash>.tar.zst  (package payload)
      - info-<name>-<hash>.tar.zst (metadata)

    Legacy .tar.bz2 conda packages are plain bzip2-compressed tarballs.
    """

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".conda"):
            return True
        # Legacy conda packages end with .tar.bz2 but we need to distinguish
        # from generic tar.bz2.  Check for conda-style naming:
        # <name>-<version>-<build>.tar.bz2
        if name.endswith(".tar.bz2") and name.count("-") >= 2:
            # Peek inside for info/ directory (conda marker)
            try:
                with tarfile.open(pkg_path, "r:bz2", tarinfo=_BoundedTarInfo) as tf:
                    names = tf.getnames()
                    return any(n.startswith("info/") for n in names[:50])
            # ExtractionSecurityError alongside tarfile's own exceptions: a
            # _BoundedTarInfo rejection during this mere format-sniff peek
            # should degrade to "not detected as this format" the same way
            # a malformed archive already does here, not propagate out of
            # a detection step and abort dispatch to every other detector.
            except (tarfile.TarError, OSError, ExtractionSecurityError):
                return False
        return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting conda package: %s", pkg_path)
        name = pkg_path.name.lower()

        if name.endswith(".conda"):
            self._extract_v2(pkg_path, target_dir)
        else:
            # Legacy .tar.bz2 format
            TarExtractor._safe_extract(pkg_path, target_dir)

        return ExtractResult(lib_dir=target_dir, container_complete=True)

    @staticmethod
    def _extract_v2(conda_path: Path, target_dir: Path) -> None:
        """Extract .conda v2 format (zip containing tar.zst payloads)."""
        # First extract the outer zip
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".conda_staging_"))
        try:
            _safe_zip_extract(conda_path, staging)

            # Find and extract pkg-*.tar.zst (the main payload)
            for member in staging.iterdir():
                if member.name.startswith("pkg-") and member.name.endswith(".tar.zst"):
                    CondaExtractor._extract_zst_tar(member, target_dir)
                elif member.name.startswith("info-") and member.name.endswith(".tar.zst"):
                    # Also extract info for metadata
                    info_dir = target_dir / "info"
                    info_dir.mkdir(exist_ok=True)
                    CondaExtractor._extract_zst_tar(member, info_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _extract_zst_tar(zst_path: Path, target_dir: Path) -> None:
        """Extract a .tar.zst file using zstd + tar or Python zstandard."""
        TarExtractor._safe_extract_zst_tar(zst_path, target_dir)


# ── Wheel (pip) extractor ────────────────────────────────────────────────────


# The wheel filename-tag/``METADATA`` fact readers moved to
# ``extract/wheel_tags.py`` (ADR-065 S3 -- see that module's docstring for
# why). Re-exported here, private helpers included, so every existing
# ``from abicheck.package import ...`` call site and test keeps resolving.
from .extract.wheel_tags import (  # noqa: E402
    _implementation_name_from_wheel_filename,  # noqa: F401
    _implementation_version_from_wheel_filename,  # noqa: F401
    _os_name_from_wheel_filename,  # noqa: F401
    _platform_machine_from_wheel_filename,  # noqa: F401
    _platform_python_implementation_from_wheel_filename,  # noqa: F401
    _platform_system_from_wheel_filename,  # noqa: F401
    _python_full_version_from_wheel_filename,  # noqa: F401
    _python_version_from_wheel_filename,  # noqa: F401
    _sys_platform_from_wheel_filename,  # noqa: F401
    _wheel_platform_tag_segment,  # noqa: F401
    parse_macos_deployment_target_floor,  # noqa: F401
    parse_manylinux_glibc_floor,  # noqa: F401
    parse_musllinux_floor,  # noqa: F401
    parse_numpy_requirement_from_metadata,  # noqa: F401
    parse_wheel_architecture_claim,  # noqa: F401
    parse_wheel_numpy_requirement,  # noqa: F401
)


class WheelExtractor:
    """Extract Python wheel (.whl) packages.

    Wheels are zip archives containing the package's files plus
    a .dist-info directory with metadata.
    """

    def detect(self, pkg_path: Path) -> bool:
        return pkg_path.name.lower().endswith(".whl")

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting wheel: %s", pkg_path)
        _safe_zip_extract(pkg_path, target_dir)
        return ExtractResult(lib_dir=target_dir, container_complete=True)


# ── Directory passthrough ────────────────────────────────────────────────────


class DirExtractor:
    """Passthrough extractor for directories (no extraction needed)."""

    def detect(self, pkg_path: Path) -> bool:
        return pkg_path.is_dir()

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        return ExtractResult(lib_dir=pkg_path)


# ── Auto-detection ───────────────────────────────────────────────────────────

_EXTRACTORS: list[PackageExtractor] = [
    DirExtractor(),
    CondaExtractor(),
    WheelExtractor(),
    TarExtractor(),
    RpmExtractor(),
    DebExtractor(),
]


def detect_extractor(path: Path) -> PackageExtractor | None:
    """Auto-detect package format and return the appropriate extractor.

    Returns None if the path is not a recognized package format.
    """
    for ext in _EXTRACTORS:
        if ext.detect(path):
            return ext
    return None


def is_package(path: Path) -> bool:
    """Return True if path is a recognized package format (not a plain directory)."""
    if path.is_dir():
        return False
    name = path.name.lower()
    if name.endswith((
        ".rpm", ".deb", ".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz",
        ".conda", ".whl",
    )):
        return True
    # Check magic bytes for RPM / Deb
    try:
        with open(path, "rb") as f:
            magic = f.read(8)
        if magic[:4] == _RPM_MAGIC:
            return True
        if magic[:8] == _DEB_MAGIC:
            return True
    except OSError:
        pass
    return False


# ── Binary discovery ─────────────────────────────────────────────────────────

# ELF magic bytes
_ELF_MAGIC = b"\x7fELF"
# ELF type ET_DYN (shared object)
_ET_DYN = 3
# Program header type PT_INTERP (interpreter segment — present in executables, absent in DSOs)
_PT_INTERP = 3
# Program header type PT_DYNAMIC (the .dynamic section, describing dynamic-linking metadata)
_PT_DYNAMIC = 2
# Dynamic section tag DT_FLAGS_1 and its DF_1_PIE bit — the standard linker
# signal a genuine PIE executable (static or dynamic) carries and a real
# shared object never does, even one built with a custom entry point.
_DT_NULL = 0
_DT_FLAGS_1 = 0x6FFFFFFB
_DF_1_PIE = 0x08000000
_SO_NAME_RE = re.compile(r"^(?P<stem>.+)\.so(?P<version>(?:\.[A-Za-z0-9]+)*)$", re.IGNORECASE)
_NUMERIC_SO_VERSION_RE = re.compile(r"(?:\.\d+)+$")
_ALNUM_SO_VERSION_RE = re.compile(r"(?:\.[A-Za-z0-9]+)+$")


def _has_shared_object_name(path: Path | str) -> bool:
    """Return True for .so or versioned SONAME-style filenames."""
    match = _SO_NAME_RE.match(Path(path).name)
    if match is None:
        return False
    version = match.group("version")
    if not version:
        return True
    if _NUMERIC_SO_VERSION_RE.fullmatch(version):
        return True
    return match.group("stem").lower().startswith("lib") and (
        _ALNUM_SO_VERSION_RE.fullmatch(version) is not None
    )


def _has_interp_segment(f: IO[bytes], ei_class: int, byte_order: str) -> bool | None:
    """Check if an ELF file has a PT_INTERP program header (i.e. is an executable)."""
    try:
        if ei_class == 1:  # 32-bit
            # e_phoff at offset 28 (4 bytes), e_phentsize at 42 (2 bytes), e_phnum at 44 (2 bytes)
            f.seek(28)
            e_phoff = struct.unpack(f"{byte_order}I", f.read(4))[0]
            f.seek(42)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]
        else:  # 64-bit
            # e_phoff at offset 32 (8 bytes), e_phentsize at 54 (2 bytes), e_phnum at 56 (2 bytes)
            f.seek(32)
            e_phoff = struct.unpack(f"{byte_order}Q", f.read(8))[0]
            f.seek(54)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]

        if e_phoff == 0 or e_phnum == 0:
            return False
        expected_phentsize = 32 if ei_class == 1 else 56
        # The program-header entry size is fixed by ELF class (32 bytes for
        # ELFCLASS32, 56 for ELFCLASS64). Any mismatch — undersized *or*
        # oversized — means the layout we read p_type from at fixed offsets is
        # not trustworthy, so treat it as inconclusive rather than risk
        # misclassifying the file.
        if e_phentsize != expected_phentsize:
            return None

        for i in range(e_phnum):
            f.seek(e_phoff + i * e_phentsize)
            p_type = struct.unpack(f"{byte_order}I", f.read(4))[0]
            if p_type == _PT_INTERP:
                return True
        return False
    except (OSError, struct.error):
        return None


def _find_pt_dynamic(f: IO[bytes], ei_class: int, byte_order: str) -> tuple[int, int] | None:
    """Return (p_offset, p_filesz) of the PT_DYNAMIC segment, or None if
    absent or the program header table is malformed/untrustworthy."""
    try:
        if ei_class == 1:  # 32-bit
            f.seek(28)
            e_phoff = struct.unpack(f"{byte_order}I", f.read(4))[0]
            f.seek(42)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]
        else:  # 64-bit
            f.seek(32)
            e_phoff = struct.unpack(f"{byte_order}Q", f.read(8))[0]
            f.seek(54)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]

        if e_phoff == 0 or e_phnum == 0:
            return None
        expected_phentsize = 32 if ei_class == 1 else 56
        if e_phentsize != expected_phentsize:
            return None

        for i in range(e_phnum):
            f.seek(e_phoff + i * e_phentsize)
            entry = f.read(e_phentsize)
            if len(entry) != e_phentsize:
                return None
            p_type = struct.unpack_from(f"{byte_order}I", entry, 0)[0]
            if p_type != _PT_DYNAMIC:
                continue
            if ei_class == 1:
                p_offset = struct.unpack_from(f"{byte_order}I", entry, 4)[0]
                p_filesz = struct.unpack_from(f"{byte_order}I", entry, 16)[0]
            else:
                p_offset = struct.unpack_from(f"{byte_order}Q", entry, 8)[0]
                p_filesz = struct.unpack_from(f"{byte_order}Q", entry, 32)[0]
            return p_offset, p_filesz
        return None
    except (OSError, struct.error):
        return None


def _has_pie_executable_flag(f: IO[bytes], ei_class: int, byte_order: str) -> bool:
    """Check the .dynamic section's DT_FLAGS_1 for the DF_1_PIE bit.

    This is the standard signal linkers set on a genuine PIE executable
    (static *or* dynamic) that a real shared object never carries — even
    one built with a custom entry point (``-Wl,-e,entry``), which a naive
    "entry point must be zero" check would incorrectly reject (Codex
    review; verified against real ``-static-pie``/``-Wl,-e``-linked
    binaries). Deliberately conservative: any inconclusive case (no
    PT_DYNAMIC, malformed/truncated dynamic table, DT_FLAGS_1 not present)
    returns False rather than rejecting the file — this signal is used
    only as a targeted, positive rejection, not a required-presence gate,
    so a minimal/synthetic ELF stub with no real .dynamic section at all
    is unaffected.
    """
    location = _find_pt_dynamic(f, ei_class, byte_order)
    if location is None:
        return False
    p_offset, p_filesz = location
    entry_size = 8 if ei_class == 1 else 16  # d_tag + d_val/d_ptr, word-sized
    if p_filesz <= 0 or p_filesz % entry_size != 0:
        return False
    # p_filesz is an 8-byte ELF64 field with no natural bound (unlike
    # e_phnum, a uint16 already capped at 65535) -- a crafted or sparse-
    # file-backed ELF advertising a huge PT_DYNAMIC p_filesz could
    # otherwise drive an effectively unbounded seek+read loop over
    # externally-supplied binaries (CodeRabbit review). Real .dynamic
    # sections have at most a few hundred entries; cap well above that.
    _MAX_DYNAMIC_ENTRIES = 8192
    entry_count = min(p_filesz // entry_size, _MAX_DYNAMIC_ENTRIES)
    word_fmt = "I" if ei_class == 1 else "Q"
    try:
        for i in range(entry_count):
            f.seek(p_offset + i * entry_size)
            entry = f.read(entry_size)
            if len(entry) != entry_size:
                return False
            d_tag = struct.unpack_from(f"{byte_order}{word_fmt}", entry, 0)[0]
            if d_tag == _DT_NULL:
                return False
            if d_tag == _DT_FLAGS_1:
                d_val = struct.unpack_from(
                    f"{byte_order}{word_fmt}", entry, entry_size // 2
                )[0]
                return bool(d_val & _DF_1_PIE)
        return False
    except (OSError, struct.error):
        return False


def _is_elf_shared_object(path: Path) -> bool:
    """Check if a file is an ELF shared object (ET_DYN) and not a PIE executable."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != _ELF_MAGIC:
                return False
            # Read EI_CLASS (byte 4), then EI_DATA (byte 5) for endianness
            ei_class = struct.unpack("B", f.read(1))[0]
            ei_data = struct.unpack("B", f.read(1))[0]

            # Seek to e_type at offset 16
            f.seek(16)
            byte_order = "<" if ei_data == 1 else ">"
            e_type = struct.unpack(f"{byte_order}H", f.read(2))[0]
            if e_type != _ET_DYN:
                return False

            # Check the DF_1_PIE flag *before* the PT_INTERP/filename fallback below, not only in the no-PT_INTERP branch: a normal `gcc -pie -o fake.so` executable has *both* PT_INTERP and DF_1_PIE set, and previously reached the has_interp branch's filename check first, which accepts anything shaped like a library name -- silently admitting a real, directly-invocable PIE executable that only happens to be named ``*.so`` (Codex review; verified against a real `gcc -pie` binary). DF_1_PIE is unconditionally decisive when set: a real shared object never carries it, PT_INTERP or not.
            if _has_pie_executable_flag(f, ei_class, byte_order):
                return False

            # Distinguish PIE executables from true shared objects:
            # executables have a PT_INTERP segment, shared objects don't.
            has_interp = _has_interp_segment(f, ei_class, byte_order)
            if has_interp is None:
                return False
            if has_interp:
                # A few distro runtime DSOs are ET_DYN, named like libraries,
                # and intentionally carry PT_INTERP (and a nonzero entry
                # point, since they're meant to be directly invocable) so
                # they can be run directly (for example Ubuntu's
                # libcap.so.2.66) -- these don't carry DF_1_PIE (already
                # ruled out above), so this filename fallback is now only
                # reached for a real, non-PIE-executable ET_DYN. Keep the
                # PIE-executable guard for app-like filenames, but do not
                # drop real versioned .so files from package discovery.
                return _has_shared_object_name(path)

            # No PT_INTERP and no DF_1_PIE: a real shared object (the
            # common case -- most .so files carry neither). A
            # `gcc -static-pie` executable is also ET_DYN with no
            # PT_INTERP, but it's already been rejected above via DF_1_PIE.
            return True
    except (OSError, struct.error):
        return False


def discover_shared_libraries(
    extract_dir: Path,
    *,
    include_private: bool = False,
) -> list[Path]:
    """Find all shared libraries in an extracted package directory.

    Walks the directory tree, identifies ELF shared objects (ET_DYN),
    and returns their paths sorted by name.

    Args:
        extract_dir: Root directory to search.
        include_private: If True, include DSOs from non-standard paths
            (e.g. private plugin directories).
    """
    _PUBLIC_LIB_DIRS = {"lib", "lib64", "usr/lib", "usr/lib64", "usr/local/lib", "usr/local/lib64"}

    libraries: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(extract_dir, followlinks=False):
        # DebExtractor extracts a .deb's control.tar.* (package metadata -- control, md5sums, and the dpkg-gensymbols(1) symbols contract) into this internal, dot-prefixed sentinel subdirectory of the same target_dir this function walks. It is package *metadata*, never payload -- a crafted/malicious control.tar.* could plant a file shaped like a shared object there (or a legitimate payload could coincidentally use the name .deb_control/, which the earlier pre-extraction cleanup now removes), and this function's own "accept any .so-suffixed file at any depth" fallback would then discover it as though it were a real library in the package (Codex review). No legitimate package payload uses this name, so pruning it from the walk is safe.
        _dirnames[:] = [d for d in _dirnames if d != ".deb_control"]
        for fn in filenames:
            fp = Path(dirpath) / fn
            elf_path = fp
            if fp.is_symlink():
                # Follow symlinks only to check the target, don't add symlinks themselves
                # unless the target is a real shared object
                try:
                    real = fp.resolve()
                    if not real.exists():
                        continue
                    elf_path = real
                except OSError:
                    continue

            if not _is_elf_shared_object(elf_path):
                continue

            # Filter by path convention unless --include-private-dso
            if not include_private:
                try:
                    rel = fp.relative_to(extract_dir)
                except ValueError:
                    continue
                rel_parts = "/".join(rel.parts[:-1])
                # Check if it's in a known library directory
                in_public = any(
                    rel_parts == d or rel_parts.startswith(d + "/")
                    for d in _PUBLIC_LIB_DIRS
                )
                # Also accept files with a .so suffix/versioned SONAME at any
                # depth as a fallback for flat directory layouts.
                has_so_ext = _has_shared_object_name(fn)
                if not in_public and not has_so_ext:
                    continue

            libraries.append(fp)

    return sorted(libraries, key=lambda p: p.name)


# ── Component inventory (ADR-065 S3) ─────────────────────────────────────────

#: How a fully-unpacked container's inventory explains its own completeness.
_CONTAINER_COMPLETE_PROVENANCE = (
    "package container unpacked in full (an archive extractor unpacks every "
    "member or raises), so the components discovered in the extracted tree "
    "are the whole set this package ships"
)
#: ...and how a directory operand explains why it proves nothing.
_CONTAINER_UNPROVEN_PROVENANCE = (
    "directory operand: no container to enumerate, so a component this tree "
    "happens not to hold may simply never have been copied in -- absence "
    "cannot be proven (ADR-065 D2)"
)


def package_component_inventory(
    extract_dir: Path,
    libraries: Iterable[Path],
    *,
    container_complete: bool,
) -> PackageInventory:
    """ADR-065 S3: this package side's declared **component inventory**.

    *libraries* is what :func:`discover_shared_libraries` selected from
    *extract_dir* under this run's own selection convention -- passed in
    rather than rediscovered so the inventory can never disagree with the
    map the comparison actually runs over. *container_complete* is
    :attr:`ExtractResult.container_complete`: the extractor's statement that
    *extract_dir* holds its whole container, which is the only thing that
    makes this inventory a D2 completeness proof.

    :attr:`~abicheck.model.package_inventory.PackageInventory.unproduced` is
    the state ADR-065 S2 reserved and left without a producer
    (``EXPECTED_NOT_PRODUCED``): a shared-object-named entry present in the
    complete tree whose own content could not be reached -- a dangling
    symlink, an unreadable file. The package plainly means to ship it, so it
    is *not* an absence D2 may read as a removal; it is an acquisition
    failure the completeness axis reports. An entry that resolves fine and
    simply is not an ELF shared object (a linker script, a static archive
    named ``.so``) is neither: it was never a comparison operand.
    """
    from .binary_utils import _canonical_library_key
    from .model.package_inventory import (
        ComponentKind,
        PackageComponent,
        PackageInventory,
    )

    libraries = list(libraries)
    selected = set(libraries)
    components: list[PackageComponent] = []
    seen: set[str] = set()
    for path in sorted(libraries, key=lambda p: (p.name, str(p))):
        key = _canonical_library_key(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            rel = str(path.relative_to(extract_dir))
        except ValueError:
            rel = path.name
        components.append(
            PackageComponent(member=key, path=rel, kind=ComponentKind.SHARED_LIBRARY)
        )
    unproduced: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(extract_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".deb_control"]
        for fn in filenames:
            if not _has_shared_object_name(fn):
                continue
            fp = Path(dirpath) / fn
            if fp in selected:
                continue
            key = _canonical_library_key(fp)
            if key in seen or key in unproduced:
                continue
            try:
                reachable = fp.resolve(strict=True).is_file()
            except OSError:
                reachable = False
            if reachable:
                continue  # present, just not an operand -- never "unproduced"
            unproduced[key] = (
                f"the package ships {fn!r}, but its content could not be reached "
                "after extraction (dangling link or unreadable file) -- expected, "
                "not produced (ADR-065 D1)"
            )
    return PackageInventory(
        components=tuple(components),
        complete=container_complete,
        provenance=(
            _CONTAINER_COMPLETE_PROVENANCE
            if container_complete
            else _CONTAINER_UNPROVEN_PROVENANCE
        ),
        unproduced=unproduced,
    )


# ── Debug info resolution ────────────────────────────────────────────────────


def resolve_debug_info(
    binary_path: Path,
    debug_dir: Path,
) -> Path | None:
    """Resolve debug info file for a binary from an extracted debug package.

    Tries three strategies in order:

    1. **Build-id** — read ``NT_GNU_BUILD_ID`` from the binary and look up the
       canonical ``.build-id/ab/cdef1234.debug`` path.  This is the most
       reliable method and produces an unambiguous match.
    2. **Path mirror** — look for a ``.debug`` file whose path under the debug
       directory mirrors the binary's path (e.g. the binary at
       ``/usr/lib64/libfoo.so.1`` → ``<debug_dir>/usr/lib/debug/usr/lib64/libfoo.so.1.debug``).
    3. **Basename rglob with disambiguation** — search for ``<name>.debug``
       anywhere under the debug directory.  When multiple candidates exist,
       prefer one whose build-id matches the binary, then one whose path
       components overlap most with the binary's path.
    """
    name = binary_path.name

    # Strategy 1: build-id (most reliable, unambiguous)
    build_id = _read_build_id(binary_path)
    if build_id:
        # build-id layout: .build-id/ab/cdef1234.debug
        bid_dir = build_id[:2]
        bid_file = build_id[2:] + ".debug"
        for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
            candidate = search_root / ".build-id" / bid_dir / bid_file
            if candidate.exists():
                _log.debug("Debug info resolved via build-id: %s", candidate)
                return candidate

    # Strategy 2: path mirror — binary at usr/lib64/libfoo.so.1 has debug at
    # <debug_dir>/usr/lib/debug/usr/lib64/libfoo.so.1.debug
    binary_parts = binary_path.parts
    for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
        # Try to mirror the binary's absolute path under the search root
        # e.g. binary /tmp/extract/usr/lib64/libfoo.so → search for
        #      search_root/usr/lib64/libfoo.so.debug
        for i, part in enumerate(binary_parts):
            if part in ("usr", "lib", "lib64"):
                mirrored = search_root.joinpath(*binary_parts[i:])
                debug_candidate = mirrored.parent / f"{mirrored.name}.debug"
                if debug_candidate.exists():
                    _log.debug("Debug info resolved via path mirror: %s", debug_candidate)
                    return debug_candidate

    # Strategy 3: basename rglob with disambiguation
    # Collect all candidates and pick the best one
    candidates: list[Path] = []
    for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
        candidates.extend(search_root.rglob(f"{name}.debug"))

    if not candidates:
        return None

    if len(candidates) == 1:
        _log.debug("Debug info resolved via path convention: %s", candidates[0])
        return candidates[0]

    # Multiple candidates — disambiguate
    # Prefer a candidate whose build-id matches the binary
    if build_id:
        for candidate in candidates:
            cand_bid = _read_build_id(candidate)
            if cand_bid == build_id:
                _log.debug(
                    "Debug info resolved via build-id match among %d candidates: %s",
                    len(candidates), candidate,
                )
                return candidate

    # Fall back to path similarity: prefer the candidate whose path
    # components overlap most with the binary's path
    binary_part_set = set(binary_path.parts)
    best: Path | None = None
    best_overlap = -1
    for candidate in candidates:
        overlap = len(set(candidate.parts) & binary_part_set)
        if overlap > best_overlap:
            best_overlap = overlap
            best = candidate

    _log.debug(
        "Debug info resolved via path similarity among %d candidates: %s",
        len(candidates), best,
    )
    return best


def _read_build_id(binary_path: Path) -> str | None:
    """Read GNU build-id from an ELF binary.

    Returns the build-id as a hex string, or None if not found.
    """
    try:
        from elftools.elf.elffile import ELFFile
        with open(binary_path, "rb") as f:
            elf = ELFFile(f)
            for section in elf.iter_sections():
                if section.name == ".note.gnu.build-id":
                    for note in section.iter_notes():
                        if note["n_type"] == "NT_GNU_BUILD_ID":
                            return str(note["n_desc"])
    except Exception:
        _log.debug("Failed to read build-id from %s", binary_path, exc_info=True)
    return None
