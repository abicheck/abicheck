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

"""Content digests for L2 header-AST cache entries.

Cache entries are keyed by their *inputs* (headers, flags, toolchain), not by
their content, so an entry that was truncated, bit-rotted on a shared cache
volume, or edited by hand used to be consumed without any signal. Each entry
now carries a ``<entry>.sha256`` sidecar holding the SHA-256 of its bytes:

* a writer records it right after publishing the entry
  (:func:`record_digest`);
* a reader checks it before parsing (:func:`verify_entry`); a mismatch means
  the entry is not what was stored, so the caller evicts it (:func:`evict`)
  and re-parses, exactly as for an unparseable entry;
* an entry with no sidecar -- written before this existed, or by a writer
  that does not record one -- is trusted on first use and recorded then.

Scope: this detects accidental corruption and edits that do not also rewrite
the sidecar. It is not an authenticity check -- anyone able to write the
cache directory can write both files -- which is the same trust boundary the
cache directory always had.

The sidecar is written after the entry, both atomically, so a concurrent
reader sees either no sidecar (trust on first use, then a correct record) or
the digest of the complete entry. The digest is computed by streaming the
file, so it never holds a multi-GB entry in memory.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

__all__ = [
    "digest_file",
    "entry_intact",
    "evict",
    "record_digest",
    "sidecar_path",
    "verify_entry",
]

log = logging.getLogger(__name__)

_SUFFIX = ".sha256"
_HEX = frozenset("0123456789abcdef")


def sidecar_path(entry: Path) -> Path:
    return entry.with_name(entry.name + _SUFFIX)


def digest_file(entry: Path) -> str:
    """Streaming SHA-256 of *entry*'s bytes, as lowercase hex."""
    with entry.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def record_digest(entry: Path, digest: str | None = None) -> None:
    """Write *entry*'s sidecar (computing the digest when not given).

    Best effort: an I/O failure leaves the entry without a sidecar, which a
    later read treats as unrecorded -- never as corrupt.
    """
    try:
        value = digest if digest is not None else digest_file(entry)
        fd, tmp = tempfile.mkstemp(dir=str(entry.parent), prefix=f".{entry.name}.")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as out:
                out.write(value + "\n")
            os.replace(tmp, sidecar_path(entry))
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        # Never leave a previous entry's digest paired with this one: that
        # would evict a valid entry on its next read. No sidecar at all is
        # read as "unrecorded" and trusted on first use instead.
        log.debug("could not record digest for %s: %s", entry, exc)
        try:
            sidecar_path(entry).unlink(missing_ok=True)
        except OSError as cleanup_exc:
            # A read-only cache: recording is an optimization, never a
            # reason to fail the load that asked for it.
            log.debug("could not drop stale digest for %s: %s", entry, cleanup_exc)


def verify_entry(entry: Path) -> bool | None:
    """``True`` when *entry* matches its recorded digest, ``False`` when it
    does not, ``None`` when no usable digest is recorded (the caller then
    records one). An unreadable sidecar counts as unrecorded."""
    try:
        recorded = sidecar_path(entry).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if len(recorded) != 64 or not all(c in _HEX for c in recorded):
        return None
    try:
        return digest_file(entry) == recorded
    except OSError:
        return False


def evict(entry: Path) -> None:
    """Remove *entry* and its sidecar."""
    entry.unlink(missing_ok=True)
    sidecar_path(entry).unlink(missing_ok=True)


def entry_intact(entry: Path) -> bool:
    """The reader's gate: ``False`` (having evicted *entry*) when it does not
    match its recorded digest, else ``True`` -- recording a digest first for
    an entry that has none."""
    intact = verify_entry(entry)
    if intact is False:
        log.warning(
            "cache entry %s does not match its recorded digest; re-parsing", entry
        )
        evict(entry)
        return False
    if intact is None:
        record_digest(entry)
    return True
