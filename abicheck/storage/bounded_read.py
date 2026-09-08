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

"""Reading a bounded amount from a stream, honestly.

A dependency-free leaf: no compression format, no snapshot vocabulary, no
imports from the rest of ``abicheck``. It owns one rule that is not specific
to snapshot storage at all, which is why it does not live in
``snapshot_io.py`` next to its first caller.
"""

from __future__ import annotations

from typing import Any


def read_up_to(reader: Any, n: int) -> bytes:
    """Read until *n* bytes are available, or *reader* is at true EOF.

    A single ``read(n)`` is not guaranteed to return ``n`` bytes even when
    the stream holds them: a reader may stop at any internal boundary and
    hand back what it has so far. Decompressing readers do this routinely --
    ``zstandard``'s ``stream_reader`` stops at every *frame* boundary, so a
    valid multi-frame (concatenated) envelope yields only its first frame's
    payload on one call, however small that frame is. A snapshot split after
    its opening ``{`` decoded to exactly ``b"{"`` (Codex review, PR #1165).

    Only ``b""`` means "nothing more"; that is the one signal every reader
    shares, so it is the only one this loop stops on. Callers can therefore
    read a short result as *the stream is exhausted* rather than *this read
    happened to stop early* -- two conditions a single ``read()`` cannot
    distinguish, and conflating them is its own bug class
    (``storage.short_decode_mistaken_for_complete_decode``).

    Deliberately not ``zstandard``'s own ``read_across_frames=True``: that
    covers the zstd frame case alone, and only on newer ``python-zstandard``.
    This loop is the general rule, for every reader and every version.
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
