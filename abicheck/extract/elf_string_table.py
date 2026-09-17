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

"""Resolve a symbol table's names from one in-memory read of its string table.

pyelftools resolves ``Symbol.name`` through
``StringTableSection.get_string``, which seeks the file stream to
``sh_offset + offset`` and reads forward in 64-byte chunks until it finds a
NUL. That is one seek plus at least one read *per name*. A real oneDAL
build makes that cost visible: ``libonedal_core.so.1`` carries 92,631
dynamic symbols, so a single parse performs ~92k stream operations to
recover names that all live in one contiguous, few-megabyte section.

:func:`buffered_string_table` reads that section once and resolves each
name with ``bytes.find``. It is deliberately **scoped**, not a cache: the
buffer lives for the duration of one ``with`` block over one section object
and is dropped at exit. There is no global keyed by path, inode or section
-- a process-lifetime table cache is exactly the unbounded retention the
surrounding performance work is trying to remove, and it would also have
to answer for a file rewritten underneath it.

**Behavioural equivalence is the whole contract**, because this shadows a
third-party accessor rather than adding a new one. Two details of
pyelftools' own semantics matter and are preserved rather than tidied:

* ``get_string`` does **not** bound its read by ``sh_size``. An offset past
  the end of the string table reads on into whatever bytes follow in the
  file, and a malformed table with no terminating NUL before end-of-file
  yields ``''`` (``parse_cstring_from_stream`` returns ``None``, which
  ``get_string`` maps to the empty string). Rather than reproduce that
  reasoning, any offset outside ``[0, sh_size)`` -- and any offset inside
  it whose NUL lies beyond the buffered bytes -- **falls back to the
  original accessor**, so those paths remain byte-for-byte whatever
  pyelftools does, including on a future pyelftools that changes them.
* Decoding is ``utf-8`` with ``errors="replace"``, and an empty string
  (offset pointing straight at a NUL) is ``''``. Both are matched exactly.

A table larger than :data:`MAX_BUFFERED_TABLE_BYTES` is not buffered at
all: the read would cost more memory than the seeks cost time, and the
whole point is to reduce peak retention, not trade it for speed.

**Measured, including what it does not buy.** On that oneDAL library
(``.dynstr`` 14,555,459 bytes, 92,995 symbols) name resolution alone goes
from 229.5ms to 68.4ms -- **3.36x** -- with byte-identical strings for all
92,995. End to end, though, ``parse_elf_metadata`` over three oneDAL
libraries improves only from 8.210s to 7.651s (**1.07x**), because
construct-based parsing of the symbol *entries* dominates and this touches
none of it. The honest summary is therefore "removes ~160ms of stream
overhead per large DSO", not "makes ELF parsing 3x faster"; the isolated
figure does not transfer and is recorded here so nobody quotes it as if it
did.

That gain is bought with a transient allocation the size of the table
(14.5 MB for the library above), which is why the buffer is scoped to the
``with`` block rather than retained: the surrounding work is trying to
lower peak memory, so a table held past the walk that needed it would be
taking back more than this gives.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["MAX_BUFFERED_TABLE_BYTES", "buffered_string_table"]

#: Largest string table read into memory in one piece. A real large C++ DSO
#: sits far below this (oneDAL's ``libonedal_core.so.1`` ``.dynstr`` is a few
#: MiB); the cap exists so a corrupt or hostile ``sh_size`` cannot turn a
#: parse into an arbitrary allocation.
MAX_BUFFERED_TABLE_BYTES = 64 * 1024 * 1024


@contextmanager
def buffered_string_table(strtab: Any) -> Iterator[bool]:
    """Serve *strtab*'s ``get_string`` from one buffered read inside the block.

    Yields whether buffering actually engaged, so a caller (or a test) can
    tell a fast-path run from a fall-through one rather than inferring it
    from timing. The original accessor is always restored on exit,
    including on an exception, so a section object never escapes this block
    with a shadowed method.
    """
    if strtab is None:
        yield False
        return
    try:
        table_offset = int(strtab["sh_offset"])
        table_size = int(strtab["sh_size"])
    except Exception:  # noqa: BLE001 - a malformed header stays on the slow path
        yield False
        return
    if table_size <= 0 or table_size > MAX_BUFFERED_TABLE_BYTES:
        yield False
        return
    stream = getattr(strtab, "stream", None)
    if stream is None:
        yield False
        return
    try:
        stream.seek(table_offset)
        blob = stream.read(table_size)
    except Exception:  # noqa: BLE001
        yield False
        return
    if not isinstance(blob, bytes) or len(blob) != table_size:
        # A truncated file read would silently shorten names; the original
        # accessor reads the real stream and is correct there.
        yield False
        return

    original = strtab.get_string
    limit = len(blob)

    def buffered_get_string(offset: int) -> str:
        # `int()` matches the original's own tolerance of a construct-parsed
        # value; anything it cannot compare is handed straight back.
        if not isinstance(offset, int) or offset < 0 or offset >= limit:
            return str(original(offset))
        end = blob.find(b"\x00", offset)
        if end < 0:
            # The NUL is past this section; pyelftools would keep reading
            # into the following bytes, so let it.
            return str(original(offset))
        # No `end == offset` special case: the slice is already empty there
        # and decodes to `""`, which is exactly what pyelftools returns for
        # an offset pointing straight at a NUL. A guard was written, found
        # to be semantically redundant by mutation (disabling it changed no
        # result), and removed rather than given a test that could not fail.
        return blob[offset:end].decode("utf-8", errors="replace")

    # Shadowed on the *instance*, so the class (and every other section
    # object sharing it) is untouched; deleting the instance attribute is
    # what restores the bound class method.
    object.__setattr__(strtab, "get_string", buffered_get_string)
    try:
        yield True
    finally:
        try:
            object.__delattr__(strtab, "get_string")
        except AttributeError:  # pragma: no cover - defensive
            object.__setattr__(strtab, "get_string", original)


def string_table_of(section: Any) -> Any:
    """The string table *section* resolves its symbol names through, or ``None``.

    ``SymbolTableSection`` exposes it as ``.stringtable``; anything else
    (or a pyelftools that renames it) yields ``None`` and so stays on the
    unbuffered path rather than raising.
    """
    return getattr(section, "stringtable", None)
