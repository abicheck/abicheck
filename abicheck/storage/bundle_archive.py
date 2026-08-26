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
``dict``, and get raw bytes back. That split is deliberate, not incidental:
this module is `storage/`'s (ADR-061) content-addressed-container primitive,
and keeping it free of any ``model``/``compare``-layer import means it joins
`storage/` cleanly today, without first having to resolve the pre-existing
``bundle_facts.py`` <-> ``checker_types.py`` (``model`` <-> ``compare``)
coupling a naive "make ``storage/bundle_archive.py`` construct a
``BundleFacts`` directly" design would immediately hit (confirmed by running
``scripts/check_architecture.py`` against exactly that shape before writing
this module: ``bundle_facts.py``'s own ``TYPE_CHECKING``-only import of
``checker_types.DiffResult`` creates a real ``model -> compare -> model``
cycle the moment ``bundle_facts.py`` joins the ``model`` layer -- a genuine,
pre-existing coupling this module does not attempt to resolve).

The ``BundleFacts``-aware glue -- turning a real ``BundleFacts`` into the
blobs/manifest shape this module writes, and back -- lives in
``serialization.py``'s ``save_bundle_facts``/``load_bundle_facts``, exactly
where that conversion already lives for the plain-JSON format. See
``docs/contribute/plans/g40-content-addressed-bundle-archive.md`` for the
full design.

Zip, not tar (`.tar.zst`, the original review sketch's own naming): zip
carries a real end-of-file central directory naming every member's offset
and independently-compressed length, so `zipfile.ZipFile.open(name)` reads
and decompresses exactly one member without touching any other -- the
random-access property this format exists to provide. Each member's own
*payload* is zstd-compressed independently (stored in the zip with
``ZIP_STORED``, matching how ``snapshot_io.py`` already treats zstd as a
payload transform independent of its outer container) rather than relying
on zip's own built-in ``ZIP_DEFLATED``, since zstd is already this
project's compression codec of record (ADR-059) and gives materially better
ratios than deflate at comparable speed.
"""

from __future__ import annotations

import hashlib
import io
import json
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


_ZIP_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06")


def sniff_bundle_archive_format(path: str | Path) -> str:
    """``"archive"`` if *path*'s own bytes start with a zip local-file-header
    or empty-archive magic; ``"json"`` otherwise (including gzip/zstd,
    which the plain-JSON ``BundleFacts`` path already detects and
    transparently decompresses from those same magic-byte conventions).
    Used by ``serialization.load_bundle_facts``'s ``format="auto"``.
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            prefix = f.read(4)
    except OSError as exc:
        raise SnapshotError(f"Cannot read {p}: {exc}") from exc
    return "archive" if prefix.startswith(_ZIP_MAGIC_PREFIXES) else "json"


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
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._zf = zipfile.ZipFile(self._path, mode="w", compression=zipfile.ZIP_STORED)
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
        self._zf.writestr(_blob_member_name(h), compressed)
        self._written_hashes.add(h)
        return h

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        if self._manifest_written:
            raise SnapshotError("BundleArchiveWriter.write_manifest() called twice")
        self._zf.writestr(MANIFEST_MEMBER, json.dumps(manifest, indent=2))
        self._manifest_written = True

    def close(self) -> None:
        if not self._manifest_written:
            raise SnapshotError(
                "BundleArchiveWriter closed without write_manifest() -- the "
                "resulting archive would have no manifest.json member"
            )
        self._zf.close()

    def __enter__(self) -> BundleArchiveWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Only close (which validates a manifest was written) on a clean
        # exit -- an exception mid-write should propagate as-is, not be
        # masked by "no manifest written yet" when the real cause is
        # upstream. A raw ZipFile.close() on the failure path is enough to
        # avoid leaking the file handle.
        if exc_info[0] is None:
            self.close()
        else:
            self._zf.close()


class BundleArchiveReader:
    """Reads one content-addressed zip archive, lazily.

    `read_manifest()` and `read_blob()` each touch only the one zip member
    they name -- `zipfile.ZipFile.open()`'s own contract, which is exactly
    why this format is zip rather than a solid-stream tar (see the module
    docstring).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._zf = zipfile.ZipFile(self._path, mode="r")

    @classmethod
    def open(cls, path: str | Path) -> BundleArchiveReader:
        return cls(path)

    def read_manifest(self) -> dict[str, Any]:
        with self._zf.open(MANIFEST_MEMBER) as f:
            raw = f.read()
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
        """
        member = _blob_member_name(content_hash_hex)
        try:
            with self._zf.open(member) as f:
                compressed = f.read()
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
        return out.getvalue()

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> BundleArchiveReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
