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

"""ADR-062 D16's ``transport`` digest role: the hash of the bytes as
stored, compression included.

The ``publication`` and ``evidence`` digests address *content*; a stored
object is verified against them only after it is decompressed and parsed.
The ``transport`` digest answers the narrower question a mover asks -- "are
these exactly the bytes that were written?" -- without decompressing
anything, so a truncated or bit-flipped member of a one-file package
archive (`project_package_archive.py`) is refused by name before a byte of
it reaches the filesystem.

It travels in each zip member's own extra field (header id
:data:`TRANSPORT_EXTRA_ID`), so the archive layout is unchanged: a reader
that predates it ignores the field, and an archive written before it (no
member carries the field) is still read -- the D13 adapter. An archive in
which *some* members carry it and others do not is refused: the writer
tags every member, so a partial set means the archive was altered.

Pure: imports only the standard library.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable

__all__ = [
    "TRANSPORT_EXTRA_ID",
    "stored_transport_digest",
    "transport_digest",
    "transport_extra",
]

#: Zip extra-field header id ("ad" for abicheck digest), outside the ids
#: PKWARE's APPNOTE assigns.
TRANSPORT_EXTRA_ID = 0x6461
_ALGORITHM = "sha256"
_DIGEST_SIZE = hashlib.sha256().digest_size


def transport_digest(chunks: bytes | Iterable[bytes]) -> str:
    """``sha256:<hex>`` of the stored bytes (one buffer or a chunk stream)."""
    h = hashlib.sha256()
    for chunk in (chunks,) if isinstance(chunks, bytes) else chunks:
        h.update(chunk)
    return f"{_ALGORITHM}:{h.hexdigest()}"


def transport_extra(data: bytes) -> bytes:
    """The zip extra field carrying *data*'s transport digest."""
    raw = hashlib.sha256(data).digest()
    return struct.pack("<HH", TRANSPORT_EXTRA_ID, len(raw)) + raw


def stored_transport_digest(extra: bytes) -> str | None:
    """The transport digest in a member's extra field, ``None`` when the
    member carries none. A malformed extra field or a wrong-sized digest
    raises `ValueError`."""
    offset = 0
    found: str | None = None
    while offset < len(extra):
        if offset + 4 > len(extra):
            raise ValueError("truncated zip extra field")
        header_id, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        if offset + size > len(extra):
            raise ValueError("truncated zip extra field")
        if header_id == TRANSPORT_EXTRA_ID:
            if size != _DIGEST_SIZE or found is not None:
                raise ValueError("malformed transport digest extra field")
            found = f"{_ALGORITHM}:{extra[offset : offset + size].hex()}"
        offset += size
    return found
