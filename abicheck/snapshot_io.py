# Copyright 2026 Nikolay Petrov
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

"""Canonical snapshot *storage envelope* I/O — ADR-059.

This module is the single place that knows how a *logical* ABI snapshot (a
JSON object with a top-level ``schema_version``, produced by
``serialization.snapshot_to_dict``/consumed by
``serialization.snapshot_from_dict``) is stored on disk: as plain UTF-8 JSON,
gzip-compressed JSON, or zstd-compressed JSON.

Compression here is a *storage/transport envelope*, not a new snapshot
schema — see ADR-059 for the full rationale. This module never inspects
snapshot content; it only moves bytes/text in and out of files, honestly
and atomically.

Deliberately dependency-free of the rest of ``abicheck`` (a leaf module) so
it can be imported from ``serialization.py``, ``snapshot_cache.py``, and
CLI/service code without creating an import cycle.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .errors import SnapshotError

# ── Compression selector ────────────────────────────────────────────────────


class SnapshotCompression(str, Enum):
    """One project-wide vocabulary for snapshot storage encoding.

    ``AUTO`` is a *request*, never a stored/resolved value: callers resolve
    it via :func:`resolve_write_compression` before writing, and a read path
    never needs it at all (detection is by magic bytes, not intent).
    """

    AUTO = "auto"
    NONE = "none"
    GZIP = "gzip"
    ZSTD = "zstd"


# Canonical file suffixes, longest-first so ``.json.zst`` is recognized before
# a bare ``.json`` match. Order matters for suffix stripping.
_COMPRESSED_SUFFIXES: tuple[tuple[str, SnapshotCompression], ...] = (
    (".json.zst", SnapshotCompression.ZSTD),
    (".json.gz", SnapshotCompression.GZIP),
)

GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Bytes read for magic-byte / bounded-prefix sniffing. Both magics fit in 4
# bytes; a larger window lets callers also bounded-sniff the decoded JSON
# prefix (e.g. to distinguish a snapshot from a `.tar.zst` archive) without a
# second read.
_SNIFF_BYTES = 4096

# Decompression bomb defence (Section 8). Real oneDAL-sized snapshots are
# ~150 MB decoded; this floor is comfortably above that with headroom for
# larger libraries, while still bounding a maliciously/accidentally huge
# decompressed payload. Overridable only via the private env var below, for
# tests — there is no public CLI flag for this (AGENTS.md: no new knobs
# without product need).
DEFAULT_MAX_DECODED_BYTES = 1024 * 1024 * 1024  # 1 GiB

# Margin added to the decoded-size limit when precheck-rejecting a
# *compressed* file's stored size before a full read (Codex review, PR
# #699): gzip/zstd container framing is a small, fixed overhead independent
# of payload size, so an exact stored-size == decoded-limit comparison can
# reject a legitimately tiny/boundary decoded payload purely because of that
# overhead. Generous enough to absorb any realistic framing cost; still
# trivially small next to a genuine decompression-bomb-sized stored file.
_STORED_SIZE_PRECHECK_MARGIN = 65536  # 64 KiB

# zstd window-size bound, independent of the decoded-size limit above: caps
# how much memory a hostile frame can force the decompressor to allocate for
# its sliding window, regardless of how much data it claims/produces.
# ``python-zstandard``'s ``ZstdDecompressor(max_window_size=...)`` takes this
# value in *kibibytes*, not bytes (confirmed against its own docstring: "an
# upper limit on the window size for decompression operations in kibibytes")
# -- passing a raw byte count here would silently permit a window 1024x
# larger than intended. `_ZSTD_MAX_WINDOW_SIZE_KIB` is the value actually
# passed to the constructor; the bit-shift below only sizes the byte ceiling
# this comment/the ADR describe.
_ZSTD_MAX_WINDOW_LOG = 31  # 2 GiB window ceiling
_ZSTD_MAX_WINDOW_SIZE_KIB = (1 << _ZSTD_MAX_WINDOW_LOG) // 1024

_MAX_DECODED_BYTES_ENV = "_ABICHECK_SNAPSHOT_MAX_DECODED_BYTES"


def _max_decoded_bytes() -> int:
    override = os.environ.get(_MAX_DECODED_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_DECODED_BYTES


# Deterministic compression settings (Section 6). Fixed, project-owned —
# not user-configurable in P0, to avoid a second profile/storage drift axis.
#
# gzip: level 9, mtime=0, no embedded filename -> byte-identical output for
# byte-identical input, on any platform/Python build using the stdlib gzip
# module (which itself just wraps zlib deterministically at a given level).
GZIP_COMPRESSLEVEL = 9

# zstd: two project-owned levels rather than one, chosen from a measured
# trade-off (see ADR-059 / CHANGELOG for the benchmark this was picked
# from): a ~18.5 MB graph-heavy synthetic snapshot compressed at level 19 in
# ~13s at a 7.6% ratio, vs. ~0.4s at 12.3% for level 10, and ~0.07s at 15.6%
# for level 3. Baseline/release artifacts are written rarely (a CI publish
# job) and read often, so they take the slow/best-ratio end; the internal
# per-dump cache is written on nearly every `dump`/`compare` invocation, so
# it takes the fast end.
ZSTD_LEVEL_BASELINE = 19
ZSTD_LEVEL_CACHE = 3


def _zstd_module() -> Any:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - core dependency, see pyproject.toml
        raise SnapshotError(
            "zstd snapshot support requires the 'zstandard' package, which "
            "is a core abicheck dependency (pyproject.toml) — reinstall "
            "abicheck ('pip install abicheck') to restore it."
        ) from exc
    return zstandard


# ── Detection ────────────────────────────────────────────────────────────


def detect_compression_from_bytes(prefix: bytes) -> SnapshotCompression:
    """Classify a byte prefix by magic bytes. Never trusts a filename."""
    if prefix.startswith(GZIP_MAGIC):
        return SnapshotCompression.GZIP
    if prefix.startswith(ZSTD_MAGIC):
        return SnapshotCompression.ZSTD
    return SnapshotCompression.NONE


def detect_snapshot_compression(path: str | Path) -> SnapshotCompression:
    """Detect a stored snapshot's compression from its magic bytes.

    Reads a small bounded prefix — never a full decompression just to sniff
    the format.
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            prefix = f.read(4)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return detect_compression_from_bytes(prefix)


def suffix_compression(path: str | Path) -> SnapshotCompression | None:
    """Return the compression a canonical suffix (``.json.gz``/``.json.zst``)
    implies, or ``None`` for a plain/neutral filename (no claim either way).
    """
    name = Path(path).name.lower()
    for suffix, compression in _COMPRESSED_SUFFIXES:
        if name.endswith(suffix):
            return compression
    return None


def resolve_write_compression(
    path: str | Path, requested: SnapshotCompression
) -> SnapshotCompression:
    """Resolve an ``auto``/explicit compression request against *path*'s suffix.

    - ``auto``: inferred from the canonical suffix; a plain/neutral suffix
      (including bare ``.json``) resolves to ``none``.
    - explicit ``none``/``gzip``/``zstd``: honored as given, UNLESS the
      filename carries a canonical suffix for a *different* compression —
      that is a hard, loud error rather than a silent rename or silent
      override (Section 3.5).
    """
    suffix_hint = suffix_compression(path)
    if requested == SnapshotCompression.AUTO:
        return suffix_hint if suffix_hint is not None else SnapshotCompression.NONE
    if suffix_hint is not None and suffix_hint != requested:
        raise SnapshotError(
            f"--compression {requested.value} conflicts with the filename "
            f"suffix of {path!s}, which implies {suffix_hint.value}. Rename "
            "the output file or drop the explicit --compression to let it "
            "follow the suffix."
        )
    return requested


# ── Result / info types ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotWriteResult:
    """What a snapshot write actually did, for summaries and manifests."""

    path: Path
    compression: SnapshotCompression
    decoded_size_bytes: int
    stored_size_bytes: int
    stored_sha256: str

    @property
    def ratio(self) -> float:
        """stored / decoded, in (0, 1] for compressed output (lower is better)."""
        if self.decoded_size_bytes == 0:
            return 1.0
        return self.stored_size_bytes / self.decoded_size_bytes


@dataclass(frozen=True)
class SnapshotStorageInfo:
    """What a snapshot's on-disk storage looks like, without decoding it."""

    path: Path
    compression: SnapshotCompression
    stored_size_bytes: int
    stored_sha256: str


def read_snapshot_storage_info(path: str | Path) -> SnapshotStorageInfo:
    p = Path(path)
    compression = detect_snapshot_compression(p)
    try:
        stored_size = p.stat().st_size
        digest = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return SnapshotStorageInfo(
        path=p,
        compression=compression,
        stored_size_bytes=stored_size,
        stored_sha256=digest.hexdigest(),
    )


# ── Decompression (read path) ───────────────────────────────────────────


def _decompress_gzip(data: bytes, *, max_decoded_bytes: int, source: str) -> bytes:
    out = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > max_decoded_bytes:
                    raise SnapshotError(
                        f"{source}: decompressed gzip payload exceeds the "
                        f"{max_decoded_bytes} byte safety limit — refusing "
                        "to continue decompressing (possible decompression "
                        "bomb, or a genuinely oversized snapshot; see "
                        "ADR-059 for how to raise the limit)."
                    )
    except SnapshotError:
        raise
    except (OSError, EOFError) as exc:
        # gzip.GzipFile raises plain EOFError (not OSError) for a stream that
        # ends before its end-of-stream marker -- the exact truncation shape
        # this function needs to turn into a diagnosable SnapshotError
        # instead of an uncaught EOFError.
        raise SnapshotError(
            f"{source}: corrupt or truncated gzip stream ({exc})"
        ) from exc
    return out.getvalue()


def _decompress_zstd(data: bytes, *, max_decoded_bytes: int, source: str) -> bytes:
    zstandard = _zstd_module()
    dctx = zstandard.ZstdDecompressor(max_window_size=_ZSTD_MAX_WINDOW_SIZE_KIB)
    out = io.BytesIO()
    try:
        with dctx.stream_reader(io.BytesIO(data)) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > max_decoded_bytes:
                    raise SnapshotError(
                        f"{source}: decompressed zstd payload exceeds the "
                        f"{max_decoded_bytes} byte safety limit — refusing "
                        "to continue decompressing (possible decompression "
                        "bomb, or a genuinely oversized snapshot; see "
                        "ADR-059 for how to raise the limit)."
                    )
    except SnapshotError:
        raise
    except Exception as exc:  # zstandard raises its own ZstdError subclasses
        raise SnapshotError(
            f"{source}: corrupt or truncated zstd stream ({exc})"
        ) from exc

    # A frame truncated early enough (e.g. mid-header) can decompress with no
    # error at all, silently yielding fewer bytes than the frame's own
    # declared content size instead of raising -- confirmed against a real
    # truncated frame. Cross-check against that declared size (present on
    # every frame this module writes, via write_content_size=True) rather
    # than trusting a clean decompression loop alone.
    try:
        declared_size = zstandard.get_frame_parameters(data).content_size
    except Exception:
        declared_size = None
    if (
        declared_size is not None
        and declared_size != zstandard.CONTENTSIZE_UNKNOWN
        and out.tell() != declared_size
    ):
        raise SnapshotError(
            f"{source}: corrupt or truncated zstd stream (decompressed "
            f"{out.tell()} bytes, frame declares {declared_size})"
        )
    return out.getvalue()


def read_snapshot_bytes(
    path: str | Path, *, max_decoded_bytes: int | None = None
) -> bytes:
    """Read *path* and return the decoded (decompressed) snapshot bytes.

    Transparently handles plain, gzip, and zstd storage by magic-byte
    detection — the filename suffix is never required to be correct for a
    read to succeed (Section 3).
    """
    p = Path(path)
    limit = max_decoded_bytes if max_decoded_bytes is not None else _max_decoded_bytes()
    # CodeRabbit review: check the *stored* file size against the same
    # safety limit before reading it fully into memory -- a stored file
    # larger than the decoded-size ceiling can only ever fail that ceiling
    # anyway (compression never expands a snapshot's own JSON payload by
    # orders of magnitude), so there's no legitimate case this rejects that
    # the post-read checks below wouldn't have rejected regardless; it just
    # avoids buffering the whole oversized file first.
    #
    # Codex review: for a *compressed* file this comparison must not be
    # exact -- gzip/zstd framing (headers, footers, block overhead) adds a
    # small, bounded number of bytes independent of payload size, so a tiny
    # decoded payload right at (or just under) the limit can have a
    # slightly larger stored size purely from that overhead (e.g. `{}`
    # decodes to 2 bytes but gzip-compresses to 22). A plain/uncompressed
    # file has stored size == decoded size exactly, so it keeps the exact
    # check; a compressed file gets a generous fixed margin -- enough to
    # absorb any realistic container overhead, but trivially small next to
    # a genuine decompression-bomb-sized stored file, which this precheck
    # still catches before a full read.
    #
    # CodeRabbit review: the size probe and the actual content read must
    # come from the *same* open file descriptor. An earlier version stat()'d
    # the pathname, then separately open()'d it again for the magic-byte
    # probe, then read it a third time via Path.read_bytes() -- three
    # separate opens of the same *pathname*, not the same file. A concurrent
    # rename or symlink swap between any of those opens could substitute an
    # oversized file after the size check already passed, defeating the
    # precheck and buffering unbounded content. A single fd refers to the
    # underlying inode for its whole lifetime, so once opened it cannot be
    # swapped out from under us by a pathname-level change; bounding the
    # read itself to one byte past the cap (rather than trusting fstat()
    # alone) additionally guards against the file growing through that same
    # fd after the size check, e.g. a concurrent writer sharing the inode.
    with open(p, "rb") as f:
        stored_size = os.fstat(f.fileno()).st_size
        prefix = f.read(4)
        compression_hint = detect_compression_from_bytes(prefix)
        margin = (
            0
            if compression_hint is SnapshotCompression.NONE
            else _STORED_SIZE_PRECHECK_MARGIN
        )
        cap = limit + margin
        if stored_size > cap:
            raise SnapshotError(
                f"{p}: stored file exceeds the {limit} byte safety limit."
            )
        f.seek(0)
        raw = f.read(cap + 1)
    if len(raw) > cap:
        raise SnapshotError(f"{p}: stored file exceeds the {limit} byte safety limit.")

    compression = detect_compression_from_bytes(raw[:4])
    suffix_hint = suffix_compression(p)
    if (
        suffix_hint is not None
        and compression is not SnapshotCompression.NONE
        and suffix_hint != compression
    ):
        raise SnapshotError(
            f"{p}: filename suffix implies {suffix_hint.value} but the "
            f"file's magic bytes indicate {compression.value} — refusing "
            "to guess; the file is either misnamed or corrupt."
        )
    if suffix_hint is not None and compression is SnapshotCompression.NONE:
        # A canonical compressed suffix with plain content is the same class
        # of contradiction, the other direction.
        raise SnapshotError(
            f"{p}: filename suffix implies {suffix_hint.value} but the "
            "file's magic bytes indicate uncompressed content — refusing "
            "to guess; the file is either misnamed or corrupt."
        )

    if compression is SnapshotCompression.NONE:
        if len(raw) > limit:
            raise SnapshotError(
                f"{p}: plain snapshot exceeds the {limit} byte safety limit."
            )
        return raw
    if compression is SnapshotCompression.GZIP:
        return _decompress_gzip(raw, max_decoded_bytes=limit, source=str(p))
    return _decompress_zstd(raw, max_decoded_bytes=limit, source=str(p))


def read_snapshot_text(
    path: str | Path, *, max_decoded_bytes: int | None = None
) -> str:
    data = read_snapshot_bytes(path, max_decoded_bytes=max_decoded_bytes)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotError(
            f"{path}: decoded content is not valid UTF-8 ({exc})"
        ) from exc


#: Hard ceiling for bounded_decoded_prefix's escalating raw-prefix reads
#: (see its docstring) -- bounds the retry loop's worst case without
#: approaching a full decompression of a large file.
_BOUNDED_PREFIX_MAX_RAW_BYTES = 1024 * 1024  # 1 MiB


def _try_decode_prefix(
    head: bytes, compression: SnapshotCompression, n: int
) -> bytes | None:
    """One decode attempt of a raw prefix; ``None`` means "try a larger raw
    prefix" (truncated mid-frame) rather than "this is not a snapshot"."""
    try:
        if compression is SnapshotCompression.GZIP:
            with gzip.GzipFile(fileobj=io.BytesIO(head), mode="rb") as gz:
                return bytes(gz.read(n))
        zstandard = _zstd_module()
        dctx = zstandard.ZstdDecompressor(max_window_size=_ZSTD_MAX_WINDOW_SIZE_KIB)
        with dctx.stream_reader(io.BytesIO(head)) as reader:
            return bytes(reader.read(n))
    except Exception:
        return None


def bounded_decoded_prefix(path: str | Path, n: int = _SNIFF_BYTES) -> bytes | None:
    """Return up to *n* decoded bytes of *path*, or ``None`` if it cannot be
    decoded as a snapshot storage envelope at all (corrupt, or a format this
    module doesn't recognize as plain/gzip/zstd, e.g. a `.tar.zst` archive).

    Used for input classification: distinguishing a compressed *snapshot*
    from an unrelated compressed *archive* without a full decompression.

    Reading exactly *n* raw (stored) bytes is not always enough to produce
    *n* *decoded* bytes -- for low-compression-ratio content (already-dense
    data, or a very small file whose compressor overhead dominates), a
    frame truncated at the raw-byte boundary can legitimately fail to
    decode at all (CodeRabbit review, fresh evidence). Escalating the raw
    read (doubling up to `_BOUNDED_PREFIX_MAX_RAW_BYTES`) before giving up
    still keeps this bounded and cheap for the common case (typically
    succeeds on the first, smallest attempt for real ABI snapshot JSON,
    which compresses well) while no longer misclassifying a valid but
    less-compressible compressed snapshot as unreadable.
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            probe = f.read(4)
            compression = detect_compression_from_bytes(probe)
            if compression is SnapshotCompression.NONE:
                return (probe + f.read(max(n, 4) - len(probe)))[:n]
            raw_size = max(n, 4)
            while True:
                f.seek(0)
                head = f.read(raw_size)
                result = _try_decode_prefix(head, compression, n)
                if result is not None:
                    return result
                if len(head) < raw_size or raw_size >= _BOUNDED_PREFIX_MAX_RAW_BYTES:
                    # Either the whole file was already read (genuinely
                    # corrupt/incompatible, not just truncated-at-the-
                    # boundary) or the escalation cap was reached.
                    return None
                raw_size = min(raw_size * 4, _BOUNDED_PREFIX_MAX_RAW_BYTES)
    except OSError:
        return None


# ── Compression (write path) ────────────────────────────────────────────


def _compress_gzip(data: bytes) -> bytes:
    encoded = bytearray(gzip.compress(data, compresslevel=GZIP_COMPRESSLEVEL, mtime=0))
    # Byte 9 of the gzip header is the OS-identifier field. mtime=0 fixes the
    # mtime bytes deterministically, but the OS byte is not otherwise pinned:
    # CPython's gzip module takes different internal code paths across
    # supported versions (a direct zlib.compress(..., wbits=31) fast path on
    # some, GzipFile-based framing on others), and they disagree on this byte
    # -- confirmed empirically (3 "Unix" vs. 255 "unknown" across supported
    # 3.10-3.14 interpreters). Force it to 255 (unknown/not specified, the
    # conventional cross-platform-safe value) unconditionally so stored bytes
    # -- and therefore stored_sha256 -- are identical for identical input
    # regardless of which Python produced them, not just within one version.
    encoded[9] = 0xFF
    return bytes(encoded)


def _compress_zstd(data: bytes, *, level: int) -> bytes:
    zstandard = _zstd_module()
    cctx = zstandard.ZstdCompressor(
        level=level,
        write_checksum=False,
        write_content_size=True,
    )
    return bytes(cctx.compress(data))


def encode_snapshot_bytes(
    data: bytes,
    compression: SnapshotCompression,
    *,
    zstd_level: int = ZSTD_LEVEL_BASELINE,
) -> bytes:
    """Encode already-serialized snapshot bytes for storage under *compression*."""
    if compression is SnapshotCompression.NONE:
        return data
    if compression is SnapshotCompression.GZIP:
        return _compress_gzip(data)
    if compression is SnapshotCompression.ZSTD:
        return _compress_zstd(data, level=zstd_level)
    raise SnapshotError(f"Cannot encode with compression={compression!r}")


def _open_unique_temp(parent: Path, prefix: str, suffix: str) -> tuple[int, Path]:
    """Atomically create a unique, exclusively-owned temp file in *parent*,
    with mode ``0o666`` filtered through the process umask by the kernel at
    creation time (``os.O_CREAT`` respects umask the same way a plain
    ``open(path, "w")`` does) -- unlike :func:`tempfile.mkstemp`, which
    hard-codes mode ``0o600`` regardless of umask specifically for its own
    security stance, which is the wrong default for a file meant to become
    a shared, group/world-readable snapshot. Retries on a name collision
    (vanishingly unlikely with a 16-byte random suffix) rather than reusing
    :func:`os.umask`'s read-zero-restore dance, which is process-wide and
    not thread-safe (CodeRabbit review, citing CPython's own documented
    caveat: a concurrent thread creating a file during that window could
    observe the temporarily-zeroed umask)."""
    import secrets

    for _ in range(100):
        candidate = parent / f"{prefix}{secrets.token_hex(8)}{suffix}"
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            return fd, candidate
        except FileExistsError:
            continue
    raise SnapshotError(f"Could not create a unique temp file in {parent}")


def _atomic_write_bytes(data: bytes, path: Path) -> None:
    """Write *data* to *path* atomically: temp file in the same directory,
    flush, best-effort fsync, then os.replace(). Never leaves a partial file
    at *path* on failure, and cleans up its own temp file either way.

    File mode (Codex/CodeRabbit review, two rounds): the temp file is
    created via :func:`_open_unique_temp`, so a genuinely *new* destination
    gets the normal umask-derived default -- honoring a caller's
    restrictive umask (e.g. 0077) exactly like a plain ``open(path, "w")``
    would, with no process-wide umask read/toggle involved. An *existing*
    destination's mode is explicitly preserved across the rewrite (a single
    ``os.chmod`` on our own just-created temp file -- not process-global,
    not racy), so re-dumping a shared, group/world-readable baseline does
    not silently strip its permissions down to whatever the current umask
    happens to be.

    Symlink destinations (Codex review): if *path* is itself a symlink,
    ``os.replace(tmp_path, path)`` would swap the symlink's own directory
    entry for a regular file, destroying the link -- the previous plain
    ``open(path, "w")`` behavior instead follows the link and writes through
    it, leaving the link intact and updating its target's content. Resolve
    to the real target first so an atomic write behaves the same way: the
    symlink survives, and what actually gets atomically replaced is the
    file it points to.
    """
    target = Path(os.path.realpath(path)) if os.path.islink(path) else path
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = _open_unique_temp(parent, f".{target.name}.", ".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # best-effort; some filesystems/platforms don't support it
        try:
            existing_mode = target.stat().st_mode & 0o777
        except OSError:
            existing_mode = None
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_snapshot_bytes(
    data: bytes,
    path: str | Path,
    *,
    compression: SnapshotCompression = SnapshotCompression.AUTO,
    zstd_level: int | None = None,
) -> SnapshotWriteResult:
    """Atomically write already-serialized snapshot *data* to *path* under the
    resolved compression envelope. This is the one write chokepoint every
    snapshot writer (dump, cache, baseline packaging) should route through."""
    p = Path(path)
    resolved = resolve_write_compression(p, compression)
    level = zstd_level if zstd_level is not None else ZSTD_LEVEL_BASELINE
    encoded = encode_snapshot_bytes(data, resolved, zstd_level=level)
    _atomic_write_bytes(encoded, p)
    return SnapshotWriteResult(
        path=p,
        compression=resolved,
        decoded_size_bytes=len(data),
        stored_size_bytes=len(encoded),
        stored_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def write_snapshot_text(
    text: str,
    path: str | Path,
    *,
    compression: SnapshotCompression = SnapshotCompression.AUTO,
    zstd_level: int | None = None,
) -> SnapshotWriteResult:
    return write_snapshot_bytes(
        text.encode("utf-8"), path, compression=compression, zstd_level=zstd_level
    )
