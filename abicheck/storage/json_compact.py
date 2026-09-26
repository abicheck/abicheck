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

"""Streaming re-encoding of a JSON document into compact, pure-ASCII form.

Written for ``clang -ast-dump=json`` output, which the L2 AST cache used to
store byte-for-byte. Measured on a real 1.0 GB entry: 69.6% of it was
pretty-print whitespace, and a *single* non-ASCII character (an em dash in a
doxygen comment) made CPython decode the whole document as a 2-byte-per-char
``str`` -- 1.88 GiB instead of 0.94 GiB, because ``str`` has no
narrow-with-exceptions representation. Compacting and ``\\u``-escaping gives
the same JSON value in ~30% of the bytes, and every later reader decodes it
at 1 byte per character.

**Why splitting on newlines is safe.** A raw line feed can never occur
inside a JSON string (RFC 8259 §7 requires control characters to be
escaped), so every ``\\n`` in a valid document lies *between* tokens --
hence also between UTF-8 sequences -- and a line can neither begin nor end
inside a string. Its leading and trailing JSON whitespace is therefore
always insignificant, which is the whole transformation: strip each line,
join. Each read chunk is cut before its last line feed and the partial line
carried into the next, so every compacted block holds whole lines. A
document with no line feeds at all accumulates into one block -- correct,
just not streamed; clang's output is always pretty-printed.

**Memory.** At most one chunk plus one carried partial line is held, so the
store stays flat however large the document -- the property the byte copy
this replaces was written for.

The transformation is value-preserving by construction: whitespace outside
strings is insignificant, and a ``\\uXXXX`` escape (a surrogate pair above
the BMP) denotes exactly the character it replaces. ``json.loads`` of the
output equals ``json.loads`` of the input; the tests check that
differentially.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .cache_integrity import entry_intact, record_digest

__all__ = [
    "CompactedAst",
    "compact_json_stream",
    "compacted_ast",
    "migrate_legacy_entry",
    "open_cached_entry",
]

_NON_ASCII = re.compile(r"[^\x00-\x7f]")

_CHUNK = 8 << 20


def _escape_char(match: re.Match[str]) -> str:
    code = ord(match.group(0))
    if code < 0x10000:
        return f"\\u{code:04x}"
    code -= 0x10000
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def _compact(block: bytes) -> bytes:
    # A line never begins or ends inside a string (see the module docstring),
    # so its leading and trailing whitespace is always insignificant. The
    # space clang writes after each key's colon is kept: removing it safely
    # needs a tokenizer, measured at 2-5x this cost for ~4% more saving.
    # Default `bytes.strip` also drops \v/\f, which JSON forbids outside
    # strings and which cannot sit at a line edge inside one; `map` over the
    # method measured 1.29x faster than the equivalent generator.
    out = b"".join(map(bytes.strip, block.split(b"\n")))
    if out.isascii():
        return out
    # Non-ASCII bytes can only sit inside strings; the block is a run of
    # whole lines, so it is also a run of whole UTF-8 sequences.
    return _NON_ASCII.sub(_escape_char, out.decode("utf-8")).encode("ascii")


def compact_json_stream(src: BinaryIO, dst: BinaryIO, chunk_size: int = _CHUNK) -> int:
    """Copy the JSON document in *src* to *dst* compact and ASCII-only.

    Returns the number of bytes written. Raises ``UnicodeDecodeError`` on
    input that is not UTF-8 (clang always emits UTF-8); the caller decides
    whether that aborts the store.
    """
    written = 0
    carry = bytearray()
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        searched = len(carry)
        carry += chunk
        # Only whole lines are compacted; the partial last line is carried.
        cut = carry.rfind(b"\n", searched)
        if cut <= 0:
            continue
        out = _compact(bytes(carry[:cut]))
        del carry[:cut]
        dst.write(out)
        written += len(out)
    if carry:
        out = _compact(bytes(carry))
        dst.write(out)
        written += len(out)
    return written


class CompactedAst:
    """A compacted copy of one AST document, pending publication.

    Created by :func:`compacted_ast`. ``path`` is what every reader of the
    freshly dumped AST should read -- the compact form parses faster and at
    1 byte per character -- and :meth:`publish` moves it into the cache
    atomically (``os.replace`` within one directory), which is why the copy
    is written next to the cache entry rather than beside clang's output.
    """

    def __init__(self, source: Path, compact: Path | None) -> None:
        self._source = source
        self._compact = compact

    @property
    def path(self) -> Path:
        return self._compact if self._compact is not None else self._source

    def publish(self, cached: Path) -> None:
        """Install this document as the cache entry *cached*.

        Falls back to a streamed byte copy of the original when compaction
        was not possible, so a cache entry is written either way.
        """
        if self._compact is not None and self._compact.parent == cached.parent:
            os.replace(self._compact, cached)
            self._compact = None
            record_digest(cached)
            return
        fd, tmp = tempfile.mkstemp(dir=str(cached.parent), prefix=f".{cached.name}.")
        try:
            with os.fdopen(fd, "wb") as out, self.path.open("rb") as inp:
                shutil.copyfileobj(inp, out)
            os.replace(tmp, cached)
            record_digest(cached)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise


@contextmanager
def compacted_ast(source: Path, work_dir: Path | None) -> Iterator[CompactedAst]:
    """Compact *source* into a temp file in *work_dir* for the block's duration.

    *work_dir* is the cache directory when the document will be cached (so
    :meth:`CompactedAst.publish` is a rename), else ``None`` for a temp file
    beside *source*. Any failure to compact -- disk full, a non-UTF-8 byte --
    degrades to reading *source* itself, never to an error: compaction is an
    optimization of the store, not a condition of it. Whatever was not
    published is removed on exit.
    """
    directory = work_dir if work_dir is not None else source.parent
    compact: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=str(directory), prefix=".ast.", suffix=".json")
        compact = Path(name)
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            compact_json_stream(src, dst)
    except (OSError, UnicodeDecodeError):
        if compact is not None:
            compact.unlink(missing_ok=True)
        compact = None
    doc = CompactedAst(source, compact)
    try:
        yield doc
    finally:
        if doc._compact is not None:
            doc._compact.unlink(missing_ok=True)


_SNIFF = 64 << 10


def migrate_legacy_entry(path: Path) -> bool:
    """Rewrite a pretty-printed cache entry at *path* into compact form, in place.

    Entries written before compaction-at-store landed stay pretty-printed
    forever otherwise -- measured at ~70% whitespace and, when one non-ASCII
    character is present, decoded at 2 bytes per character: roughly half of
    the clang backend's peak RSS on a warm run. Compact output never holds a
    line feed (every one is stripped), so a line feed in the first
    ``_SNIFF`` bytes identifies a legacy entry; a compact entry costs one
    small read. The rewrite is atomic (temp file + ``os.replace`` in the same
    directory), so a concurrent reader sees the old or the new document,
    both the same JSON value. Returns whether the entry was rewritten; any
    failure leaves the entry untouched -- migration is an optimization.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_SNIFF)
    except OSError:
        return False
    if b"\n" not in head:
        return False
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        tmp = Path(name)
        with os.fdopen(fd, "wb") as dst, path.open("rb") as src:
            compact_json_stream(src, dst)
        os.replace(tmp, path)
    except (OSError, UnicodeDecodeError):
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        return False
    # The old sidecar (if any) described the pretty bytes.
    record_digest(path)
    return True


def open_cached_entry(path: Path) -> bool:
    """Ready the AST cache entry at *path* for reading.

    ``False`` (the entry evicted) when it fails its content digest, else
    ``True`` with a pre-compaction pretty entry migrated in place. The order
    is the point: migration re-records the digest, so migrating first would
    launder an edited entry into a "valid" one.
    """
    if not entry_intact(path):
        return False
    migrate_legacy_entry(path)
    return True
