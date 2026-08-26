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

"""One canonical logical encoding — ADR-062 D5.

A content-addressed store is only as good as its notion of "the same
content". Today's serialization does not fully specify one:
``snapshot_to_json()`` does not globally sort keys, entity list order can
follow producer traversal order, and the stable snapshot hash sorts mapping
keys while treating list order as significant. ``BundleFacts`` cannot use
recursive key sorting *at all*, because one template-instantiation mapping
uses insertion order to carry template-argument order — sorting it would
silently change what the document means.

That last case is the one worth naming, because it is what this module is
shaped around: structural order and incidental order had not been
separated. A mapping whose insertion order is load-bearing is not really a
mapping; it is an array wearing one. So:

* a **sequence** is ordered, and its order is preserved verbatim;
* a **mapping** is unordered, and its keys are sorted — which is safe only
  because anything order-carrying is expected to be a sequence of explicit
  entries (``[{"parameter": …, "value": …}, …]``) instead;
* a **set/frozenset** is unordered and is emitted as a sorted array;
* volatile capture metadata is excluded before hashing, so two captures of
  identical content agree regardless of when or where they ran.

The invariant every consumer may rely on: ``semantic_digest`` is invariant
under mapping key order, set iteration order, and pretty-printing, and is
*not* invariant under sequence order.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "VOLATILE_KEYS",
    "canonical_form",
    "canonical_json",
    "semantic_digest",
    "strip_volatile",
]

#: Keys excluded from the semantic-hash domain wherever they appear. These
#: record *when and where* a capture ran, never *what it found*, so two
#: captures of byte-identical inputs must agree despite differing here.
#: Kept as a frozenset of exact key names rather than a prefix/suffix rule:
#: a heuristic like "anything ending in ``_at``" would silently swallow a
#: real fact (a field genuinely named ``deprecated_at`` is content), and a
#: digest that quietly ignores content is far worse than one that includes
#: an extra timestamp.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "created_at",
        "captured_at",
        "generated_at",
        "duration_seconds",
        "elapsed_seconds",
        "hostname",
        "host",
        "pid",
        "tmpdir",
        "scratch_dir",
        "working_directory",
        "wall_clock_seconds",
    }
)


def _canonical_number(value: float) -> Any:
    """Normalize a float so equal values encode identically.

    Three cases have bitten JSON-hashing schemes before and are handled
    explicitly rather than left to ``json``'s defaults: ``-0.0`` (which
    compares equal to ``0.0`` but encodes as ``-0.0``), integral floats
    (``2.0``, which must not hash differently from a producer that emitted
    ``2``), and non-finite values. ``NaN``/``Infinity`` are rejected outright
    — ``json`` emits them as bare literals that are not valid JSON, so a
    document containing one is unreadable by a conforming parser and must not
    be written in the first place.
    """
    if math.isnan(value) or math.isinf(value):
        raise ValueError(
            f"non-finite float {value!r} cannot appear in canonical storage form"
        )
    if value == 0.0:
        return 0
    if value.is_integer():
        return int(value)
    return value


def _set_member(value: Any) -> Any:
    """Normalize one set member. See the set branch of :func:`canonical_form`."""
    return int(value) if isinstance(value, bool) else value


def canonical_form(value: Any, *, drop_volatile: bool = True) -> Any:
    """Recursively normalize a value into its canonical logical form.

    Accepts the shapes a storage payload is built from: mappings, sequences,
    sets, strings, numbers, booleans, and ``None``. Anything else is a
    programming error here rather than something to coerce — a silent
    ``str()`` fallback would let an object whose ``repr`` contains a memory
    address into the hash domain, making the digest differ run to run for
    identical content.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, (bytes, bytearray)):
        # `bytes` is a Sequence, so without this guard it would fall through
        # and encode as a list of integers — a silent, lossy reinterpretation
        # rather than an error. Binary payloads belong in the object store as
        # raw objects referenced by digest, never inline in a facts document.
        raise TypeError(
            "bytes have no canonical storage form; store binary payloads as "
            "raw objects and reference them by digest"
        )
    if isinstance(value, (set, frozenset)):
        # Sorted by canonical JSON text, so heterogeneous members (ints
        # alongside strings) order deterministically instead of raising the
        # way a direct `sorted()` would.
        #
        # `_set_member` collapses bool to int, because Python already does:
        # `1 == True` and `hash(1) == hash(True)`, so `{1, "x"}` and
        # `{True, "x"}` are the *same set*, and which spelling survives
        # construction depends only on which was inserted first. Emitting
        # `true` for one and `1` for the other would give two equal sets two
        # different digests — an incidental-order dependence exactly like the
        # one this module exists to remove, just hidden inside `set.__hash__`
        # rather than in a producer's traversal. The distinction is
        # unrecoverable at this point by construction, so preserving it is not
        # among the options; agreeing is.
        return sorted(
            (
                canonical_form(_set_member(v), drop_volatile=drop_volatile)
                for v in value
            ),
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, Mapping):
        items = sorted((str(k), v) for k, v in value.items())
        return {
            k: canonical_form(v, drop_volatile=drop_volatile)
            for k, v in items
            if not (drop_volatile and k in VOLATILE_KEYS)
        }
    if isinstance(value, Sequence):
        # Order preserved: a sequence is the shape that *means* something is
        # ordered. A caller whose collection is conceptually unordered must
        # pass a set, or sort it with its own explicit key, rather than
        # relying on this function to guess.
        return [canonical_form(v, drop_volatile=drop_volatile) for v in value]
    raise TypeError(
        f"{type(value).__name__} has no canonical storage form; "
        "convert it to a mapping, sequence, set, string, number, bool, or None"
    )


def strip_volatile(value: Any) -> Any:
    """Canonical form with volatile capture metadata removed."""
    return canonical_form(value, drop_volatile=True)


def canonical_json(
    value: Any, *, drop_volatile: bool = True, indent: int | None = None
) -> str:
    """Serialize a value through :func:`canonical_form`.

    ``indent`` affects only presentation. :func:`semantic_digest` never reads
    this function's output, so a pretty-printed object and a compact one are
    the same content by construction rather than by convention.
    """
    return json.dumps(
        canonical_form(value, drop_volatile=drop_volatile),
        indent=indent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
    )


def semantic_digest(value: Any, *, algorithm: str = "sha256") -> str:
    """Content digest of a value's canonical form, volatile keys excluded.

    Returned as ``"<algorithm>:<hex>"`` rather than a bare hex string so that
    a stored digest names the function that produced it. A package written
    today and read after an algorithm change must be able to say "this digest
    is sha256" instead of leaving a reader to assume.
    """
    payload = json.dumps(
        canonical_form(value, drop_volatile=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{algorithm}:{hashlib.new(algorithm, payload).hexdigest()}"
