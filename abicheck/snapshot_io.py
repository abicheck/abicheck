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

import errno
import gzip
import hashlib
import io
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .errors import SnapshotError
from .storage.zstd_frame_guard import (
    read_past_leading_skippable_frames,
    skip_leading_skippable_frames,
    starts_with_skippable_frame_magic,
    validate_zstd_frame_completeness,
)

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

# Independent ceiling on a *compressed* file's stored size before a full
# read (Codex review, PR #699 -- third round on this same precheck).
#
# History of why this is a fixed, INDEPENDENT ceiling rather than anything
# derived from `max_decoded_bytes`/`limit`: two earlier attempts both tied
# the stored-size cap to the decoded limit via a margin (first a fixed 64
# KiB, then limit/256) on the assumption that "a stored file larger than
# the decoded-size ceiling can only ever fail that ceiling anyway". Fresh
# evidence disproved that assumption a second time, more fundamentally: a
# *valid* concatenated multi-member gzip stream (RFC 1952 permits this, and
# Python's gzip module transparently decodes it as one logical stream) can
# have stored-size overhead that is essentially unbounded relative to its
# decoded content -- one gzip member per byte turned a 3,974-byte payload
# into an 83,454-byte stored file (~21x), and `gzip.decompress()` still
# reproduces the original bytes exactly. No margin formula keyed off
# `limit` can ever be generous enough for this, since the overhead scales
# with the *number of members*, not the payload size the caller is
# bounding. The real decompression-bomb defense already lives where it
# actually belongs -- the incremental decode loops in `_decompress_gzip`/
# `_decompress_zstd` below, which enforce `max_decoded_bytes` against the
# growing *decoded* output on every chunk, independent of how much raw
# input was fed in. This stored-size precheck's only remaining job is a
# distinct, narrower one: refuse to buffer a truly enormous *raw* file into
# memory before even attempting decompression -- so it gets its own
# generous, fixed ceiling instead of borrowing (and distorting) the
# decoded-size one.
DEFAULT_MAX_STORED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# zstd window-size bound, independent of the decoded-size limit above: caps
# how much memory a hostile frame can force the decompressor to allocate for
# its sliding window, regardless of how much data it claims/produces.
# ``python-zstandard``'s ``ZstdDecompressor(max_window_size=...)`` docstring
# claims this value is in *kibibytes*, but the underlying implementation
# (``backend_cffi.py``'s ``_ensure_dctx``, and the C extension alike) passes
# it straight through to ``ZSTD_DCtx_setMaxWindowSize()`` with no `* 1024`
# anywhere -- and that libzstd API takes a raw *byte* count. Confirmed
# empirically: decompressing a real frame with an 8 MiB window succeeds with
# ``max_window_size=8 * 1024 * 1024`` and fails with
# ``max_window_size=8 * 1024`` (the KiB-scaled value the docstring implies),
# so the parameter is bytes in practice, not KiB. Dividing it by 1024 (as an
# earlier revision of this module did, reading the docstring at face value)
# silently shrank the accepted window to 1/1024th of the intended ceiling,
# making any snapshot the writer compressed with a larger window
# undecodable. See ADR-059 §8, which already documents the ceiling in bytes
# (`max_window_size = 1 << 31`).
_ZSTD_MAX_WINDOW_LOG = 31  # 2 GiB window ceiling, on a build that supports it


# `ZSTD_DCtx_setMaxWindowSize()` bound-checks its argument against the
# backend's own `[1 << windowLogMin, 1 << windowLogMax]` range and errors
# out (not just declines the frame) if it's exceeded -- and `windowLogMax`
# is only 31 (2 GiB) on a 64-bit libzstd build; a 32-bit build's
# `ZSTD_WINDOWLOG_MAX_32` is 30 (1 GiB) (Codex review). Passing our fixed
# 31 unconditionally would make `ZstdDecompressor(max_window_size=...)`
# itself raise on such a build, rejecting every zstd snapshot outright
# -- not just an oversized one. `zstandard.WINDOWLOG_MAX` reports the
# *actual* backend ceiling at runtime, so clamp to whichever is smaller.
def _zstd_max_window_size_bytes(zstandard: Any) -> int:
    log = min(_ZSTD_MAX_WINDOW_LOG, int(zstandard.WINDOWLOG_MAX))
    return 1 << log


_MAX_DECODED_BYTES_ENV = "_ABICHECK_SNAPSHOT_MAX_DECODED_BYTES"
_MAX_STORED_BYTES_ENV = "_ABICHECK_SNAPSHOT_MAX_STORED_BYTES"


def _max_decoded_bytes() -> int:
    override = os.environ.get(_MAX_DECODED_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_DECODED_BYTES


def _max_stored_bytes() -> int:
    override = os.environ.get(_MAX_STORED_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_STORED_BYTES


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
    """Classify a byte prefix by magic bytes. Never trusts a filename.

    Skips past any leading zstd skippable frame(s) first (Codex review,
    fresh evidence: an externally produced zstd stream can legitimately
    start with one, e.g. a metadata frame ahead of the real data frame) --
    a no-op when *prefix* is too short to contain a full skippable-frame
    header/body, or when it doesn't start with one at all, so a caller
    passing only a bare 4-byte prefix keeps its existing behavior exactly."""
    prefix = skip_leading_skippable_frames(prefix)
    if prefix.startswith(GZIP_MAGIC):
        return SnapshotCompression.GZIP
    if prefix.startswith(ZSTD_MAGIC):
        return SnapshotCompression.ZSTD
    return SnapshotCompression.NONE


def _read_past_leading_skippable_frames(f: Any, prefix: bytes) -> bytes:
    """This module's own step/cap bound to the shared primitive in
    `storage.zstd_frame_guard` -- see that function's docstring; kept as
    a thin wrapper so every call site here reads `_SNIFF_BYTES`/
    `_BOUNDED_PREFIX_MAX_RAW_BYTES` (this module's own, pre-existing
    bounded-prefix-sniffing constants) rather than the shared default."""
    return read_past_leading_skippable_frames(
        f, prefix, step=_SNIFF_BYTES, cap=_BOUNDED_PREFIX_MAX_RAW_BYTES
    )


def _classify_with_skippable_fallback(prefix: bytes, saw_skippable_magic: bool) -> SnapshotCompression:
    """`detect_compression_from_bytes(prefix)`, except a leading skippable-
    frame magic that outlasts the bounded escalation (`_BOUNDED_PREFIX_
    MAX_RAW_BYTES`) still classifies as `ZSTD` -- the magic alone already
    proves the zstd family (Codex review, sixth-order follow-up), shared
    by every skippable-frame-aware probe in this module."""
    compression = detect_compression_from_bytes(prefix)
    if compression is SnapshotCompression.NONE and saw_skippable_magic:
        return SnapshotCompression.ZSTD
    return compression


def detect_snapshot_compression(path: str | Path) -> SnapshotCompression:
    """Detect a stored snapshot's compression from its magic bytes.

    Reads a small bounded prefix — never a full decompression just to sniff
    the format. Escalates that read (up to `_BOUNDED_PREFIX_MAX_RAW_BYTES`)
    only when the prefix itself starts with a zstd skippable-frame magic
    (Codex review, fresh evidence: a leading skippable frame otherwise
    read as an uncompressed file, since 4 bytes can't see past it).
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            prefix = f.read(4)
            saw_skippable_magic = starts_with_skippable_frame_magic(prefix)
            if saw_skippable_magic:
                prefix = _read_past_leading_skippable_frames(f, prefix)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return _classify_with_skippable_fallback(prefix, saw_skippable_magic)


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
    """Report a stored snapshot's compression, size, and content hash.

    Opens the file exactly once (Codex review, #699): a concurrent
    atomic replace (``os.replace()``, the write path this module itself
    uses) between separate open()s could otherwise make the magic-byte
    probe, the stat()'d size, and the hashed content each describe a
    different file generation, producing an internally inconsistent
    :class:`SnapshotStorageInfo`. All three are derived from one fd's
    ``fstat()``/read stream instead.
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            stored_size = os.fstat(f.fileno()).st_size
            prefix = f.read(4)
            # Escalate only when ambiguous (a leading skippable-frame
            # magic) -- forward-only, so it stays compatible with the
            # sequential hash loop below (Codex review, fresh evidence).
            saw_skippable_magic = starts_with_skippable_frame_magic(prefix)
            if saw_skippable_magic:
                prefix = _read_past_leading_skippable_frames(f, prefix)
            compression = _classify_with_skippable_fallback(prefix, saw_skippable_magic)
            digest = hashlib.sha256()
            digest.update(prefix)
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
    dctx = zstandard.ZstdDecompressor(
        max_window_size=_zstd_max_window_size_bytes(zstandard)
    )
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
    # truncated frame. `validate_zstd_frame_completeness` is a shared,
    # three-Codex-round-corrected cross-check now also used by
    # `storage/bundle_archive.py`'s `read_blob()` -- see its own docstring
    # for the full multi-frame/CONTENTSIZE_UNKNOWN/memory-safety reasoning.
    validate_zstd_frame_completeness(zstandard, dctx, data, source=source)
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
    # CodeRabbit review: check the *stored* file size against a safety
    # ceiling before reading it fully into memory -- avoids buffering a
    # needlessly oversized file first.
    #
    # Codex review (three rounds on this same precheck): a *plain*
    # uncompressed file's stored size equals its decoded size exactly, so
    # it's checked against `limit` itself with no margin. A *compressed*
    # file's stored size must NOT be checked against anything derived from
    # `limit` -- two earlier attempts (a fixed 64 KiB margin, then
    # `limit`-proportional) both assumed stored size can only exceed
    # decoded size by a small/bounded amount, which a valid concatenated
    # multi-member gzip stream disproves: overhead scales with the *number
    # of members*, not payload size (a 3,974-byte payload encoded as one
    # gzip member per byte stored at 83,454 bytes, ~21x overhead, still
    # decodes correctly). See `DEFAULT_MAX_STORED_BYTES` above for why this
    # is instead an independent, fixed ceiling: the actual decompression-
    # bomb defense already lives in `_decompress_gzip`/`_decompress_zstd`'s
    # own incremental decoded-size enforcement below, so this precheck's
    # only remaining job is refusing to buffer a truly enormous *raw* file,
    # which has nothing to do with what decoded-size limit the caller
    # requested.
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
        # Codex review, fresh evidence: escalate past a leading zstd
        # skippable frame before selecting the cap below, the same way
        # this module's other three prefix probes do -- see `_classify_
        # with_skippable_fallback`'s docstring for why `NONE` can't be
        # trusted here. `prefix` never grows past `stored_size` (read
        # forward from this same file), so `stored_size > cap` below
        # still catches an oversized file regardless.
        saw_skippable_magic = starts_with_skippable_frame_magic(prefix)
        if saw_skippable_magic:
            prefix = _read_past_leading_skippable_frames(f, prefix)
        compression_hint = _classify_with_skippable_fallback(prefix, saw_skippable_magic)
        # Codex review: `max(limit, _max_stored_bytes())` let a raised
        # `max_decoded_bytes` (a caller's tolerance for large *decoded*
        # content) silently expand the *stored*-size ceiling too -- an
        # untrusted compressed file could then be buffered wholly into
        # memory well past `_max_stored_bytes()`'s own, separately
        # configured safety intent. The two are orthogonal knobs; use
        # `_max_stored_bytes()` alone for a compressed file, exactly as
        # documented above -- a caller who genuinely needs a larger stored-
        # size ceiling raises that knob directly (its own env var), not as
        # a side effect of raising the unrelated decoded-size one.
        cap = (
            limit
            if compression_hint is SnapshotCompression.NONE
            else _max_stored_bytes()
        )
        if stored_size > cap:
            raise SnapshotError(
                f"{p}: stored file exceeds the {cap} byte safety limit."
            )
        # Codex review: don't rewind via f.seek(0) to reread the magic-byte
        # prefix -- a FIFO/pipe (a named pipe, or /dev/stdin) is not
        # seekable, so a rewind raises io.UnsupportedOperation, regressing
        # the previous json.load(open(...)) implementation, which could
        # consume such a stream just fine (no rewind needed there since it
        # never separately sniffed a prefix first). Build the full read
        # from the prefix bytes already consumed plus whatever remains,
        # never re-reading the same bytes twice.
        raw = prefix + f.read(cap + 1 - len(prefix))
    if len(raw) > cap:
        raise SnapshotError(f"{p}: stored file exceeds the {cap} byte safety limit.")

    # The whole buffered `raw` (not just its first 4 bytes) is handed in
    # here -- unlike the earlier `compression_hint` probe above, `raw` is
    # already fully read at this point, so this is the one call site that
    # can actually see past a leading skippable frame of any size up to
    # `cap` (Codex review, fresh evidence).
    compression = detect_compression_from_bytes(raw)
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
        dctx = zstandard.ZstdDecompressor(
            max_window_size=_zstd_max_window_size_bytes(zstandard)
        )
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
            # Escalate only when ambiguous (a leading skippable-frame
            # magic) -- a plain/gzip/real-zstd-frame probe never has more
            # to find past it, so the common case pays nothing extra
            # (Codex review, fresh evidence).
            saw_skippable_magic = starts_with_skippable_frame_magic(probe)
            if saw_skippable_magic:
                probe = _read_past_leading_skippable_frames(f, probe)
            compression = _classify_with_skippable_fallback(probe, saw_skippable_magic)
            if compression is SnapshotCompression.NONE:
                more_needed = max(n, 4) - len(probe)
                if more_needed > 0:
                    probe += f.read(more_needed)
                return probe[:n]
            raw_size = max(n, len(probe))
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
    flush, best-effort fsync, then os.replace(), then best-effort fsync the
    parent directory. Never leaves a partial file at *path* on failure, and
    cleans up its own temp file either way.

    Note the parent-directory fsync's failure semantics differ from the
    pre-replace file fsync above (CodeRabbit review): by the time it runs,
    ``os.replace()`` has already completed and *target* already holds the
    new content -- a real error there (propagated the same narrow way as
    the file fsync, distinguishing genuine storage failures from an
    fsync-unsupported filesystem/platform) means the directory-entry
    update pointing at that content is not confirmed durable across a
    crash, not that the write itself failed or that *target*'s content is
    wrong on a running system. A caller catching it may still choose to
    retry the whole write; doing so is safe (idempotent) but unnecessary
    for correctness on a system that stays up.

    File mode (Codex/CodeRabbit review, two rounds): the temp file is
    created via :func:`_open_unique_temp`, so a genuinely *new* destination
    gets the normal umask-derived default -- honoring a caller's
    restrictive umask (e.g. 0077) exactly like a plain ``open(path, "w")``
    would, with no process-wide umask read/toggle involved. An *existing*
    destination's mode is explicitly preserved across the rewrite (a single
    ``os.chmod`` on our own just-created temp file -- not process-global,
    not racy), so re-dumping a shared, group/world-readable baseline does
    not silently strip its permissions down to whatever the current umask
    happens to be. Ownership -- both group (Codex review) and user (Codex
    review, second finding) -- is preserved the same way: the fresh temp
    file is owned by the writer, not an existing destination's owner, so
    copying only the mode would silently revoke a shared baseline's
    deliberately-assigned owner/group (e.g. a service-account UID or an
    ``abi-readers`` GID, with no setgid parent directory to inherit either
    from). ``os.chown`` is attempted, but NOT best-effort (two rounds of
    Codex review found the opposite framing wrong): a writer that isn't
    the destination's current owner can't restore its uid without
    CAP_CHOWN/root, and a writer that IS the owner but doesn't belong to
    its assigned group can't restore that gid either -- either failure
    aborts the whole write (raising before ``os.replace()`` runs, so the
    *existing* destination is untouched) rather than silently publishing
    the replacement under the writer's own uid/gid, which could revoke
    real group-based read access for a shared baseline's other readers,
    not just cosmetically differ from before.

    Symlink destinations (Codex review): if *path* is itself a symlink,
    ``os.replace(tmp_path, path)`` would swap the symlink's own directory
    entry for a regular file, destroying the link -- the previous plain
    ``open(path, "w")`` behavior instead follows the link and writes through
    it, leaving the link intact and updating its target's content. Resolve
    to the real target first so an atomic write behaves the same way: the
    symlink survives, and what actually gets atomically replaced is the
    file it points to.

    Non-regular destinations (Codex review): an existing FIFO, character/
    block device, or socket at *path* (e.g. ``/dev/stdout``, a named pipe
    in a streaming pipeline) has no meaningful "atomic replace" -- the
    previous ``open(path, "w")`` behavior wrote directly *through* it,
    while ``os.replace()`` would instead swap it out for a brand-new
    regular file, destroying the special file it was. None of this
    function's atomicity/mode/ownership machinery is meaningful for a
    FIFO/device either, so a pre-existing non-regular destination is
    written to directly, bypassing the temp-file-and-rename dance
    entirely.

    Hard-linked destinations (Codex review): if *target* has other hard
    links (``st_nlink > 1``), ``os.replace()`` installs the new content
    under *this* pathname's directory entry only -- every other link keeps
    pointing at the old inode's stale content, silently diverging from
    what this call just wrote. The previous ``open(path, "w")`` behavior
    wrote into the shared inode directly, keeping every alias in sync.
    There is no way to have both that in-place-update-every-alias behavior
    *and* this function's own atomicity/durability guarantees at once for
    the same call (an in-place write can leave a torn/partial file on a
    crash, exactly what the atomic design elsewhere in this function exists
    to prevent) -- silently picking either behavior would surprise a caller
    expecting the other, so this is a hard error instead: unlink (or
    otherwise resolve) the extra hard link(s) before writing to *path* if
    an atomic, single-alias update is what's wanted.

    Non-regular detection order (Codex review, second finding): the
    non-regular check below stats *path* directly via ``os.stat()``
    (kernel-level symlink resolution) *before* any ``os.path.realpath()``
    call, not after. ``os.path.realpath()`` is a userspace, string-based
    resolution -- for a symlink whose target is a pipe-backed file
    descriptor (e.g. ``/dev/stdout`` when it's connected to a pipe, as on
    a CI runner piping ``dump``'s output), it can produce a synthetic,
    unstat-able pseudo-path such as ``/proc/<pid>/fd/pipe:[12345]``.
    Resolving via ``realpath()`` first (the previous ordering) then made
    the follow-up ``stat()`` fail, so the non-regular destination was
    never recognized as such and the function fell through to the
    atomic-rename path -- attempting to create a temp file under that
    same bogus pseudo-directory. ``os.stat(path)`` alone follows the
    symlink at the kernel level and correctly reports the pipe's real
    mode without ever needing a resolved path string, so it is used for
    both the type check and the direct write below; ``os.path.realpath()``
    is only ever called afterward, on the regular-file/atomic-rename path,
    where it does not have this failure mode.

    Stat-failure handling (Codex review, third finding): both stat() calls
    below only treat *absence* -- ``FileNotFoundError``/
    ``NotADirectoryError`` (a path component genuinely doesn't exist) -- as
    "no pre-existing destination to worry about." Any other ``OSError``
    (a transient ``EIO``, ``EACCES``, ``ELOOP``, ...) propagates instead of
    being silently treated as absence: swallowing it would let the
    function fall through to the atomic-rename path for a destination
    whose real type was never actually established, bypassing the non-
    regular and hard-link safeguards above for an inode that may well
    still be a FIFO/device/multiply-linked file once the transient error
    clears.
    """
    try:
        initial_stat = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        initial_stat = None
    if initial_stat is not None and not stat.S_ISREG(initial_stat.st_mode):
        # open() itself follows a symlink to reach the real FIFO/device/
        # socket, so no path resolution is needed here at all.
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # best-effort; not meaningful for most non-regular files
        return
    target = Path(os.path.realpath(path)) if os.path.islink(path) else path
    try:
        existing_stat_for_type = target.stat()
    except (FileNotFoundError, NotADirectoryError):
        pass
    else:
        if (
            stat.S_ISREG(existing_stat_for_type.st_mode)
            and existing_stat_for_type.st_nlink > 1
        ):
            raise SnapshotError(
                f"{target}: has {existing_stat_for_type.st_nlink} hard links -- "
                "an atomic rewrite would silently desynchronize the other "
                "link(s) from this one (they would keep the old content, "
                "not see the new write). Unlink the extra hard link(s) "
                "first if you want this path atomically rewritten in "
                "isolation."
            )
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = _open_unique_temp(parent, f".{target.name}.", ".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as exc:
                # Codex review: only swallow an error that specifically
                # means "this filesystem/platform doesn't support fsync"
                # (EINVAL/ENOTSUP/EOPNOTSUPP -- seen on some network/overlay
                # filesystems and platforms) -- best-effort in that narrow
                # sense only. A real storage failure (ENOSPC disk full,
                # EIO, EDQUOT, EROFS a filesystem remounted read-only mid-
                # write, ...) means the data the kernel just reported
                # flushing may not actually be durable on disk; swallowing
                # that and proceeding to os.replace() would silently
                # overwrite a known-good snapshot with unconfirmed content,
                # contradicting this function's own atomic-write guarantee.
                # Let anything else propagate to the outer except, which
                # cleans up the temp file and leaves the existing
                # destination untouched.
                if exc.errno not in (
                    errno.EINVAL,
                    errno.ENOTSUP,
                    errno.EOPNOTSUPP,
                ):
                    raise
        try:
            existing_stat = target.stat()
            existing_mode: int | None = existing_stat.st_mode & 0o777
            existing_uid: int | None = existing_stat.st_uid
            existing_gid: int | None = existing_stat.st_gid
        except OSError:
            existing_mode = None
            existing_uid = None
            existing_gid = None
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        if (existing_uid is not None or existing_gid is not None) and hasattr(
            os, "chown"
        ):
            try:
                os.chown(
                    tmp_path,
                    existing_uid if existing_uid is not None else -1,
                    existing_gid if existing_gid is not None else -1,
                )
            except OSError:
                # Codex review, two rounds: a failure here must not be
                # silently swallowed the way it originally was. chown(uid,
                # gid) is all-or-nothing -- if the *owner* genuinely needed
                # to change (an unprivileged writer that isn't the
                # destination's current owner can never restore a
                # different uid without CAP_CHOWN/root), the combined call
                # fails entirely, so a legitimate gid restoration silently
                # fails right along with it too. The first round's fix
                # only re-raised when the uid itself needed changing, on
                # the assumption that a *gid-only* restoration failure (the
                # writer already owns the file, just isn't a member of its
                # assigned group) was lower-stakes and could stay
                # best-effort -- but fresh evidence showed that's wrong
                # too: the writer owning the file and simply not
                # belonging to its reader group is exactly the case where
                # silently falling back to the writer's own primary group
                # can revoke real access for that group's other readers,
                # not just cosmetically differ. Proceeding to os.replace()
                # after *any* failed ownership restoration would silently
                # change a shared baseline's owner and/or group access --
                # exactly the class of silent attribute loss the unguarded
                # os.chmod() call just above already refuses to tolerate
                # (a real chmod failure is never caught here either).
                # Abort the replacement unconditionally instead; the
                # *existing* destination is untouched either way (this
                # runs before os.replace()).
                raise
        os.replace(tmp_path, target)
        # CodeRabbit review: os.replace()'s directory-entry update is not
        # itself durable across a crash until the *parent* directory is
        # fsync'd too -- the file-content fsync above only guarantees the
        # new data reaches storage, not that the rename pointing at it
        # survives a power loss. Best-effort in the same narrow sense as
        # the file fsync above: some platforms/filesystems don't support
        # *fsync* on a directory fd at all (Windows has no directory fd
        # concept here), which is not a storage failure to propagate --
        # but a real one (EIO/ENOSPC/EROFS) means the rename may not
        # actually be durable, which this function should surface rather
        # than silently claim success for. Never blocks on a missing
        # `target.parent` (`hasattr(os, "O_DIRECTORY")` gates POSIX-only).
        #
        # Codex review: opening `parent` itself (not the later fsync) must
        # NOT be wrapped in the same "swallow, treat as unsupported"
        # handling -- unlike fsync-on-a-directory, which some real
        # filesystems genuinely don't support, opening a directory we just
        # wrote *through* moments ago has no legitimate "unsupported"
        # failure mode on any platform that reaches this branch at all
        # (Windows, the one platform without O_DIRECTORY, is already
        # excluded by the hasattr() gate above). A real failure here
        # (EMFILE out of descriptors, EIO, a permissions change mid-run)
        # must propagate the same way a real fsync failure does, not be
        # silently downgraded into an unconfirmed-but-reported-successful
        # write.
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            except OSError as exc:
                if exc.errno not in (
                    errno.EINVAL,
                    errno.ENOTSUP,
                    errno.EOPNOTSUPP,
                ):
                    raise
            finally:
                os.close(dir_fd)
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
