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

"""Manifest-encoding size guard for ``bundle_archive.py``/``bundle_facts.py``
(G40), split out purely to stay under both callers' ADR-061 800-line
production cap -- a leaf, dependency-free module with no coupling to either
caller beyond the one function below.
"""

from __future__ import annotations

# Chunk size for the incremental UTF-8 byte count below -- large enough to
# keep the per-chunk encode() call cheap, small enough that no single
# encode() ever materializes more than this many *characters* worth of
# bytes at once (at most 4 bytes/char, so ~256 KiB per chunk).
_CHUNK_CHARS = 65536


def _utf8_length_exceeds(s: str, limit: int) -> bool:
    """Return whether *s*'s UTF-8 encoding exceeds *limit* bytes, without
    ever encoding the whole string in one call.

    A prior revision called ``obj.encode("utf-8")`` on the whole string
    before comparing its length -- for a guaranteed-oversized value (a
    multi-gigabyte string, say) that allocates a second object as large as
    the input, exactly the allocation this preflight exists to prevent
    (Codex review, fresh evidence). Two steps close that: a Python `str`'s
    length in *characters* is always a lower bound on its UTF-8 length in
    *bytes* (each codepoint encodes to 1-4 bytes), so a character count
    alone already over *limit* proves the byte count is too, with no
    encoding at all. Otherwise the character count is already `<= limit`,
    but encoding it in one call could still allocate up to `4 * limit`
    bytes at once -- so the remainder is encoded incrementally, in bounded
    chunks, stopping as soon as the running total exceeds *limit*.
    """
    if len(s) > limit:
        return True
    total = 0
    for start in range(0, len(s), _CHUNK_CHARS):
        total += len(s[start : start + _CHUNK_CHARS].encode("utf-8"))
        if total > limit:
            return True
    return False


def oversized_raw_string(obj: object, limit: int) -> str | None:
    """Return the first string leaf found in *obj* whose own raw UTF-8
    byte length already exceeds *limit*, or `None` if none does.

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
    strings, so the oversized allocation never happens at all.
    """
    if isinstance(obj, str):
        return obj if _utf8_length_exceeds(obj, limit) else None
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
