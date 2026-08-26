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
from json.encoder import encode_basestring_ascii

# Chunk size for the incremental escaped-length count below -- large enough
# to keep the per-chunk call cheap, small enough that no single call ever
# materializes more than this many *characters* worth of escaped text at
# once.
_CHUNK_CHARS = 65536


def _escaped_length_exceeds(s: str, limit: int) -> int | None:
    """Return a byte count already known to exceed *limit* if *s*'s
    JSON-escaped (``ensure_ascii=True``) encoding does, else `None` --
    without ever escaping the whole string in one call, and without a
    caller needing to re-encode it a second time just to report a size.

    Must check the *escaped*, not the raw UTF-8, length: JSON escaping can
    inflate a string's size well past its raw byte count -- a quote or
    backslash doubles, and a control character or lone surrogate becomes a
    six-character ``\\uXXXX`` sequence (up to 6x its raw 1-byte form). A
    raw-length-only check can therefore pass a string whose *escaped* form
    is still far larger than *limit*, and `JSONEncoder.iterencode()`
    yields one whole escaped string as a single chunk regardless -- so
    that oversized chunk still gets fully materialized before any
    chunk-by-chunk running-total check gets a chance to reject it,
    reproducing the exact vulnerability this whole module exists to
    prevent (Codex review, fresh evidence). A Python `str`'s length in
    *characters* is still always a lower bound on its *escaped* length too
    (every character maps to at least one output character), so a
    character count alone already over *limit* proves the escaped length
    is too, with no escaping at all. Otherwise the remainder is escaped
    incrementally, in bounded chunks, via `json.encoder.
    encode_basestring_ascii()` -- the exact function `json.dumps()`/
    `JSONEncoder(ensure_ascii=True)` use internally, so this measures the
    real escaped form rather than approximating it, and (being pure ASCII
    output) needs no separate `.encode()` call to count bytes, sidestepping
    the lone-surrogate `UnicodeEncodeError` a raw `.encode("utf-8")` would
    otherwise raise. Stops as soon as the running total exceeds *limit*.
    """
    if len(s) > limit:
        return len(s)
    total = 2  # the two wrapping quote characters, counted once, not per chunk
    if total > limit:
        return total
    for start in range(0, len(s), _CHUNK_CHARS):
        # encode_basestring_ascii() wraps its own quotes -- subtracted so
        # they aren't double-counted across chunks.
        total += len(encode_basestring_ascii(s[start : start + _CHUNK_CHARS])) - 2
        if total > limit:
            return total
    return None


def oversized_raw_string(obj: object, limit: int) -> tuple[str, int] | None:
    """Return ``(string, byte_count)`` for the first string leaf found in
    *obj* whose own JSON-escaped byte length already exceeds *limit* --
    *byte_count* a real, already-computed lower bound on that length, not
    an exact total -- or `None` if no leaf does.

    Used to reject a manifest whose JSON encoding cannot possibly fit
    *limit* without ever materializing that (potentially far larger)
    encoded form: `json.JSONEncoder.iterencode()` yields one whole escaped
    string as a single chunk, so a chunk-by-chunk length check on the
    *iterencode() output* alone can't reject an oversized string before
    that one allocation already happened -- this check runs first, over
    the original strings' own *escaped* size (see
    `_escaped_length_exceeds()`'s own docstring for why the raw,
    unescaped size alone isn't a safe proxy for this), so the oversized
    allocation never happens at all (Codex review, fresh evidence).
    Returning the byte count already found lets a caller report *some*
    real size in an error message without re-encoding the (potentially
    still huge) string a second time just to do so (Codex review, fresh
    evidence).
    """
    if isinstance(obj, str):
        count = _escaped_length_exceeds(obj, limit)
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
