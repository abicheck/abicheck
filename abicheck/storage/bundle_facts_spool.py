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

"""A temp-dir, content-addressed store of encoded member snapshots.

``--bundle-facts-out`` needs every OLD member's whole snapshot, but only at
the end of the run, when it writes the document. Holding the live
``AbiSnapshot`` until then multiplies one member's graph by the member
count. This module holds the *encoded* member on disk instead:
:meth:`BlobSpool.put_snapshot` writes it as soon as the member's comparison
finishes, and the caller keeps only the small :class:`SpooledBlob` handle.

**The blob is the G40 archive blob.** A blob's bytes are
``json.dumps(snapshot_to_dict(snap), indent=2)`` in UTF-8, the payload
``storage.bundle_facts_archive`` stores, and its name is that payload's
content hash. So the same spool serves both output forms with no second
encode:

* the archive form copies each blob into the zip under its own hash;
* the JSON form splices each blob into the document, re-padded to its
  depth (:class:`~abicheck.storage.json_stream.PreEncoded`), which is
  byte-identical to encoding the member in place.

Neither form ever decodes a blob back into a snapshot.

**Fork- and thread-safe by construction.** A handle carries only the hash
and the length, and a put writes a private temp file and renames it onto
the hash. Two writers of one content write the same bytes, so whichever
rename lands last is correct. The store keeps no shared index, so a member
spooled in a forked worker (``workflows.release_member_processes``) is
readable from the parent through the handle the worker returns.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["BlobSpool", "SpooledBlob"]

#: Bytes read per chunk when a blob is streamed back out.
_READ_CHUNK = 1 << 20


@dataclass(frozen=True)
class SpooledBlob:
    """A handle to one spooled payload: its sha256 hex digest and length.

    Picklable and small, so a member entry can carry it across a process
    boundary in place of the snapshot it stands for."""

    sha256: str
    length: int


class BlobSpool:
    """A content-addressed blob store in a private temporary directory.

    Use as a context manager; the directory and every blob in it are
    deleted on exit. *parent* is where the directory is created (the
    system temporary directory by default).
    """

    def __init__(self, parent: str | Path | None = None) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="abicheck-spool-", dir=parent))

    @property
    def directory(self) -> Path:
        return self._dir

    def __enter__(self) -> BlobSpool:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _path(self, blob: SpooledBlob) -> Path:
        return self._dir / blob.sha256

    def _write(self, chunks: Iterator[bytes]) -> SpooledBlob:
        hasher = hashlib.sha256()
        length = 0
        fd, tmp_name = tempfile.mkstemp(dir=self._dir, prefix=".put-")
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in chunks:
                    hasher.update(chunk)
                    length += len(chunk)
                    out.write(chunk)
            blob = SpooledBlob(hasher.hexdigest(), length)
            os.replace(tmp_name, self._path(blob))
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return blob

    def put_bytes(self, payload: bytes) -> SpooledBlob:
        """Store *payload* under its content hash."""
        return self._write(iter((payload,)))

    def put_snapshot(self, snap: AbiSnapshot) -> SpooledBlob:
        """Store *snap*'s G40 blob: ``json.dumps(snapshot_to_dict(snap),
        indent=2)`` as UTF-8, encoded fragment by fragment straight to disk.

        Refuses, exactly as every ``BundleFacts`` writer does, a snapshot
        whose stored state a flat document cannot carry
        (``wire_extensions.refuse_package_flattening``)."""
        from ..serialization import snapshot_to_dict
        from .json_stream import iter_json_indented
        from .wire_extensions import refuse_package_flattening

        refuse_package_flattening(snap)
        document: Any = snapshot_to_dict(snap)
        return self._write(
            fragment.encode("utf-8") for fragment in iter_json_indented(document)
        )

    def read_bytes(self, blob: SpooledBlob) -> bytes:
        """The whole payload (for the archive, which stores it whole)."""
        data = self._path(blob).read_bytes()
        if len(data) != blob.length:
            raise OSError(
                f"spooled blob {blob.sha256} is {len(data)} bytes, expected "
                f"{blob.length}: the spool directory was modified"
            )
        return data

    def iter_text(self, blob: SpooledBlob) -> Iterator[str]:
        """The payload as text, a chunk at a time.

        A blob is ``ensure_ascii`` JSON, so every chunk boundary is a
        character boundary."""
        seen = 0
        with self._path(blob).open("rb") as src:
            while chunk := src.read(_READ_CHUNK):
                seen += len(chunk)
                yield chunk.decode("ascii")
        if seen != blob.length:
            raise OSError(
                f"spooled blob {blob.sha256} is {seen} bytes, expected "
                f"{blob.length}: the spool directory was modified"
            )
