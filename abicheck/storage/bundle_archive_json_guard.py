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

"""JSON-encoding size guards for ``bundle_archive.py``/``bundle_facts.py``
(G40), split out purely to stay under both callers' ADR-061 800-line
production cap -- a leaf module with no coupling to either caller beyond
the functions below.
"""

from __future__ import annotations

import json

# Chunk size for the incremental UTF-8 byte count below -- large enough to
# keep the per-chunk encode() call cheap, small enough that no single
# encode() ever materializes more than this many *characters* worth of
# bytes at once (at most 4 bytes/char, so ~256 KiB per chunk).
_CHUNK_CHARS = 65536


def _utf8_length_exceeds(s: str, limit: int) -> int | None:
    """Return a byte count already known to exceed *limit* if *s*'s UTF-8
    encoding does, else `None` -- without ever encoding the whole string
    in one call, and without a caller needing to re-encode it a second
    time just to report a size.

    A prior revision called ``obj.encode("utf-8")`` on the whole string
    before comparing its length -- for a guaranteed-oversized value (a
    multi-gigabyte string, say) that allocates a second object as large as
    the input, exactly the allocation this preflight exists to prevent
    (Codex review, fresh evidence). Two steps close that: a Python `str`'s
    length in *characters* is always a lower bound on its UTF-8 length in
    *bytes* (each codepoint encodes to 1-4 bytes), so a character count
    alone already over *limit* proves the byte count is too, with no
    encoding at all (returned as the lower-bound count itself). Otherwise
    the character count is already `<= limit`, but encoding it in one call
    could still allocate up to `4 * limit` bytes at once -- so the
    remainder is encoded incrementally, in bounded chunks via
    ``errors="surrogatepass"`` (a lone surrogate -- e.g. from a POSIX
    filename captured through ``os.fsdecode``'s ``surrogateescape``
    handling of non-UTF-8 bytes -- raises `UnicodeEncodeError` under the
    default strict handling, even though `json.dumps()`'s own
    `ensure_ascii=True` escaping round-trips it just fine as a plain
    `\\uXXXX` sequence; Codex review, fresh evidence), stopping as soon as
    the running total exceeds *limit* and returning that real, already-
    computed partial total.
    """
    if len(s) > limit:
        return len(s)
    total = 0
    for start in range(0, len(s), _CHUNK_CHARS):
        total += len(s[start : start + _CHUNK_CHARS].encode("utf-8", errors="surrogatepass"))
        if total > limit:
            return total
    return None


def oversized_raw_string(obj: object, limit: int) -> tuple[str, int] | None:
    """Return ``(string, byte_count)`` for the first string leaf found in
    *obj* whose own raw UTF-8 byte length already exceeds *limit* --
    *byte_count* a real, already-computed lower bound on that length, not
    an exact total -- or `None` if no leaf does.

    Used to reject a manifest whose JSON encoding cannot possibly fit
    *limit* without ever materializing that (potentially far larger)
    encoded form: JSON string escaping only ever grows a string's encoded
    length (a quote/backslash/control character each become a longer
    escape sequence, never shorter), so a raw string already longer than
    *limit* is guaranteed to encode past it too. `json.JSONEncoder.
    iterencode()` yields one whole escaped string as a single chunk, so a
    chunk-by-chunk length check alone can't reject an oversized string
    before that one allocation already happened (Codex review, fresh
    evidence) -- this check runs first, over the original, unescaped
    strings, so the oversized allocation never happens at all. Returning
    the byte count already found lets a caller report *some* real size in
    an error message without re-encoding the (potentially still huge)
    string a second time just to do so (Codex review, fresh evidence).
    """
    if isinstance(obj, str):
        count = _utf8_length_exceeds(obj, limit)
        return (obj, count) if count is not None else None
    if isinstance(obj, dict):
        for k, v in obj.items():
            found = oversized_raw_string(k, limit) if isinstance(k, str) else None
            if found is not None:
                return found
            found = oversized_raw_string(v, limit)
            if found is not None:
                return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = oversized_raw_string(item, limit)
            if found is not None:
                return found
        return None
    return None


def bounded_encode_utf8(obj: object, limit: int) -> bytes | None:
    """Serialize *obj* to UTF-8 JSON (``indent=2``) bytes, or `None` if
    doing so would exceed *limit* bytes -- never materializes the
    complete oversized payload to find out.

    Two layers, covering both ways an encode can blow past *limit*
    before a caller-side check gets a chance to run: a single oversized
    string leaf is caught first and cheaply via `oversized_raw_string()`
    itself (`iterencode()` alone can't catch this -- see that function's
    own docstring); the remaining, aggregate size across many
    individually-bounded fields is then caught by streaming
    `JSONEncoder.iterencode()` and stopping as soon as the running byte
    count crosses *limit*, instead of a caller building the whole string
    with `json.dumps()` first (Codex review, fresh evidence -- reproduced
    against `write_bundle_facts_archive()`'s own per-library-snapshot
    encode, previously unbounded within one snapshot even though the
    loop's own cap was already checked *between* snapshots).
    """
    if oversized_raw_string(obj, limit) is not None:
        return None
    parts: list[bytes] = []
    total = 0
    for chunk in json.JSONEncoder(indent=2).iterencode(obj):
        chunk_bytes = chunk.encode("utf-8")
        total += len(chunk_bytes)
        if total > limit:
            return None
        parts.append(chunk_bytes)
    return b"".join(parts)
