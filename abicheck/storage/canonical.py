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
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .guards import binary_buffer as _is_binary_buffer

__all__ = [
    "CAPTURE_METADATA_KEY",
    "canonical_form",
    "canonical_json",
    "copy_of_canonical_form",
    "raw_digest",
    "semantic_digest",
    "semantic_digest_of_canonical_form",
    "strip_capture_metadata",
]

#: The one reserved key at a document's **root**, whose entire subtree is
#: excluded from the semantic-hash domain. Everything a capture records about
#: *when and where* it ran — timestamps, hostname, pid, scratch paths, wall
#: clock — lives under here, so two captures of byte-identical inputs agree
#: without any key elsewhere in the document being treated as volatile.
#:
#: This replaced a ``VOLATILE_KEYS`` frozenset of names stripped recursively
#: at any depth, and the replacement is the point rather than an
#: implementation detail. That design asked an unanswerable question of every
#: key in the document — "does this name mean capture metadata *here*?" — and
#: got it wrong twice in review. First ``host``, which is as likely to name a
#: platform as a hostname, made ``semantic_digest({"host": "linux"})``,
#: ``semantic_digest({"host": "windows"})`` and ``semantic_digest({})``
#: identical. Removing that one name did not fix the class: ``pid`` is an
#: entirely ordinary C struct field, so ``{"entities": {"pid": {"type":
#: "int"}}}`` still hashed equal to ``{"entities": {}}``, and
#: ``working_directory`` is a real build input. Each fix drew the next
#: instance of the same defect, which is the signal to stop patching the list
#: and change the mechanism.
#:
#: Position, not spelling, is what makes this sound: only the document root
#: is inspected, so a nested ``{"entities": {"capture": …}}`` is ordinary
#: content and is hashed. A payload may therefore use any name it likes for
#: content at any depth; the format reserves exactly one slot, at exactly one
#: place, and a producer that puts content there is misusing a declared part
#: of the format rather than being silently second-guessed.
CAPTURE_METADATA_KEY = "capture"


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
    """Normalize one set member, recursively. See :func:`canonical_form`.

    The collapse has to reach *inside* composite members, not just their top
    level: Python considers ``{(True,)}`` and ``{(1,)}`` equal sets, so which
    tuple survives construction depends only on which was inserted first, and
    emitting ``[[true]]`` for one and ``[[1]]`` for the other gave two equal
    sets two different digests (Codex review). That is precisely the defect the
    top-level collapse was written to fix, one level down — the fix was scoped
    to the shape that had been demonstrated rather than to the rule.

    Only set members are treated this way. Outside a set, ``{"x": True}`` and
    ``{"x": 1}`` are genuinely different documents and must hash differently;
    it is *set membership* that makes the distinction unrecoverable, so it is
    only there that agreeing is the sole option left.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, tuple):
        return tuple(_set_member(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_set_member(item) for item in value)
    return value


def canonical_form(value: Any) -> Any:
    """Recursively normalize a value into its canonical logical form.

    Accepts the shapes a storage payload is built from: mappings, sequences,
    sets, strings, numbers, booleans, and ``None``. Anything else is a
    programming error here rather than something to coerce — a silent
    ``str()`` fallback would let an object whose ``repr`` contains a memory
    address into the hash domain, making the digest differ run to run for
    identical content.

    This function normalizes only; it never removes anything. Excluding the
    reserved capture-metadata subtree is :func:`strip_capture_metadata`'s job,
    and it happens once at the document root rather than at every level.

    **Container *type* is deliberately collapsed, and that is not a
    collision.** JSON has one array type, so a list, a tuple, a set and a
    frozenset with the same contents all serialize to the same array — which
    means they are the same *stored document*, and a content address is an
    address of the document. `{(1, 2)}` and `{frozenset({1, 2})}` were
    reported as an ambiguity because they are unequal in Python and share a
    digest; they also share a `canonical_json` output, byte for byte
    (Codex review, declined with evidence).

    Encoding the collection kind to separate them would be the actively
    worse choice, not merely an unnecessary one: a reader parsing the stored
    document back gets lists, so it could not reconstruct the tag, and
    `semantic_digest(read_back)` would stop matching the digest the document
    was stored under. That breaks the one property the whole module exists
    to provide. The invariant that matters — a digest is a pure function of
    the document, in both directions — is asserted as a property test rather
    than argued here.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _canonical_number(value)
    # Fast path for the overwhelmingly common concrete `dict`/`list` shapes
    # (every storage payload is built from these, not from a custom
    # `Mapping`/`Sequence`), checked by exact `type()` ahead of the general
    # `isinstance(..., Mapping)`/`isinstance(..., Sequence)` checks below.
    # `isinstance` against an `abc`-registered protocol walks the class's
    # MRO/registry on every call (`abc.ABCMeta.__instancecheck__`), which is
    # measurably more expensive than a `type() is dict` identity check at
    # the scale a large snapshot's canonical form is computed at — this
    # changes no observable behavior, since the general branches below still
    # handle every other `Mapping`/`Sequence` subtype exactly as before.
    value_type = type(value)
    if value_type is dict:
        for raw_key in value:
            if not isinstance(raw_key, str):
                raise TypeError(
                    f"mapping key {raw_key!r} is {type(raw_key).__name__}, not str; "
                    "canonical storage form does not coerce keys"
                )
        return {
            k: canonical_form(v) for k, v in sorted(value.items(), key=lambda kv: kv[0])
        }
    if value_type is list:
        return [canonical_form(v) for v in value]
    if _is_binary_buffer(value):
        # `bytes` is a Sequence, so without this guard it would fall through
        # and encode as a list of integers — a silent, lossy reinterpretation
        # rather than an error. Binary payloads belong in the object store as
        # raw objects referenced by digest, never inline in a facts document.
        raise TypeError(
            f"{type(value).__name__} has no canonical storage form; store "
            "binary payloads as raw objects and reference them by digest"
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
            (canonical_form(_set_member(v)) for v in value),
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, Mapping):
        # Keys must already be strings. Coercing with `str()` was lossy in two
        # ways at once (Codex review): `{1: "a", "1": "b"}` collapsed to a
        # single entry, so the digest matched a document that never held the
        # discarded one; and sorting `(str(k), v)` pairs fell through to
        # comparing *values* whenever two keys tied, so `{1: {}, "1": []}`
        # raised `TypeError: '<' not supported between instances of 'list' and
        # 'dict'` from inside a digest call. A JSON object's keys are strings
        # anyway, so a non-string key is a producer bug that a round-trip
        # would silently rewrite — rejecting it is the same no-silent-coercion
        # rule the `bytes` and unsupported-type branches already apply.
        for raw_key in value:
            if not isinstance(raw_key, str):
                raise TypeError(
                    f"mapping key {raw_key!r} is {type(raw_key).__name__}, not str; "
                    "canonical storage form does not coerce keys"
                )
        # Sorted by key alone — never by the pair — so no value comparison can
        # occur and mutually unorderable values are simply irrelevant here.
        return {
            k: canonical_form(v) for k, v in sorted(value.items(), key=lambda kv: kv[0])
        }
    if isinstance(value, Sequence):
        # Order preserved: a sequence is the shape that *means* something is
        # ordered. A caller whose collection is conceptually unordered must
        # pass a set, or sort it with its own explicit key, rather than
        # relying on this function to guess.
        return [canonical_form(v) for v in value]
    raise TypeError(
        f"{type(value).__name__} has no canonical storage form; "
        "convert it to a mapping, sequence, set, string, number, bool, or None"
    )


def copy_of_canonical_form(value: Any) -> Any:
    """A fresh, deep copy of *value*, which must already be `canonical_form`'s
    own output (only `dict`/`list`/`str`/`int`/`float`/`bool`/`None`, dict
    keys already sorted).

    Used where a caller needs an isolated copy of content it knows is
    already canonical -- `InMemoryObjectStore.get()`'s "never the store's
    own object" contract, for one -- without paying `canonical_form`'s own
    cost again: no re-sorting, no dict-key type validation, and no
    `isinstance` checks against `Mapping`/`Sequence`/`set`/`frozenset`,
    since the input's shape is already known and none of that work can
    change the result. Passing anything that is not already canonical is a
    programming error; this function does not validate that, unlike
    `canonical_form`, which is the whole reason it is faster.
    """
    if type(value) is dict:
        return {k: copy_of_canonical_form(v) for k, v in value.items()}
    if type(value) is list:
        return [copy_of_canonical_form(v) for v in value]
    return value


def strip_capture_metadata(value: Any) -> Any:
    """Canonical form with the reserved root capture-metadata slot removed.

    Only the document **root** is inspected. A ``capture`` key nested anywhere
    below it is ordinary content and survives — see
    :data:`CAPTURE_METADATA_KEY` for why position rather than spelling is what
    makes this sound.
    """
    canonical = canonical_form(value)
    if isinstance(canonical, dict):
        return {k: v for k, v in canonical.items() if k != CAPTURE_METADATA_KEY}
    return canonical


def canonical_json(
    value: Any, *, drop_capture_metadata: bool = False, indent: int | None = None
) -> str:
    """Serialize a value through :func:`canonical_form`.

    ``drop_capture_metadata`` defaults to ``False``: the stored document keeps
    its capture metadata, which is excluded from *hashing* only. ``indent``
    affects only presentation — :func:`semantic_digest` never reads this
    function's output, so a pretty-printed object and a compact one are the
    same content by construction rather than by convention.

    This keeps ``ensure_ascii=False``, unlike :func:`semantic_digest`: the
    stored document is meant to be read, and escaping every non-ASCII
    identifier would make it materially worse for that. The consequence is
    that a value containing a lone surrogate (a ``surrogateescape``-decoded
    POSIX path) round-trips through *this* function but cannot be encoded to
    UTF-8 by whatever eventually writes it. The digest path is what had to be
    made total, since an unaddressable package is worse than an unwritable
    one; how a Phase 1 writer handles such a path is that writer's decision to
    make explicitly rather than to discover.
    """
    return json.dumps(
        strip_capture_metadata(value)
        if drop_capture_metadata
        else canonical_form(value),
        indent=indent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
    )


#: Compiled once rather than re-walking each string character-by-character
#: in Python for every call — `_has_surrogate_pair` runs once per string in
#: the entire document (via `_reject_surrogate_pairs`'s full traversal), so
#: its per-call cost is directly the cost of hashing a large document. `re`'s
#: matcher is implemented in C and short-circuits on the first match, same
#: as the `any(...)` it replaces, with identical semantics: `text[index]` in
#: `\ud800`-`\udbff` immediately followed by `text[index + 1]` in
#: `\udc00`-`\udfff`.
_SURROGATE_PAIR_RE = re.compile("[\ud800-\udbff][\udc00-\udfff]")


def _has_surrogate_pair(text: str) -> bool:
    """Whether ``text`` contains a high surrogate followed by a low one."""
    return _SURROGATE_PAIR_RE.search(text) is not None


def _reject_surrogate_pairs(value: Any) -> None:
    """Refuse a string whose ASCII payload would not identify it uniquely.

    The ASCII payload is what makes this module's lone-surrogate support work
    (see :func:`semantic_digest`), and it has one blind spot: JSON escapes a
    non-BMP scalar *as* a surrogate pair, so ``"\\U0001f600"`` and the
    two-code-unit string ``"\\ud83d\\ude00"`` — which are not equal, and which
    :func:`canonical_json` does distinguish — render identically and hash to
    one address (Codex review). Two unequal values sharing a content address
    is the failure a content-addressed store cannot tolerate, and here the
    *stored document* and its *address* disagree about whether they differ,
    which is worse than a plain collision.

    Refused rather than escaped, and the two are not symmetric. A lone
    surrogate is a real POSIX path this module deliberately supports; a
    surrogate *pair* is a non-BMP scalar spelled the way UTF-16 spells it,
    which `json.loads` of the payload silently turns into the scalar anyway.
    Inventing an escape for it would mean this module addressing content by a
    rule no other implementation could re-derive — giving up exactly the
    property the ASCII payload was chosen for. Refusing says which of the two
    the caller should pass.
    """
    if isinstance(value, str):
        if _has_surrogate_pair(value):
            raise ValueError(
                "a surrogate pair cannot be addressed distinctly from the "
                f"non-BMP character it spells ({value!r}); pass the character "
                "itself. A *lone* surrogate is supported — it is what a "
                "surrogateescape-decoded path carries."
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_surrogate_pairs(key)
            _reject_surrogate_pairs(item)
        return
    if isinstance(value, (Sequence, set, frozenset)) and not isinstance(
        value, (str, bytes)
    ):
        for item in value:
            _reject_surrogate_pairs(item)


def semantic_digest(value: Any, *, algorithm: str = "sha256") -> str:
    """Content digest of a value's canonical form, capture metadata excluded.

    Returned as ``"<algorithm>:<hex>"`` rather than a bare hex string so that
    a stored digest names the function that produced it. A package written
    today and read after an algorithm change must be able to say "this digest
    is sha256" instead of leaving a reader to assume.

    The hash payload is deliberately **ASCII** (``ensure_ascii=True``), unlike
    :func:`canonical_json`'s stored document. Two reasons, and the first is a
    real defect this closes: a POSIX path carrying a non-UTF-8 byte decodes
    through ``surrogateescape`` into a lone surrogate — ``os.fsdecode(b"caf\\xe9")``
    is ``"caf\\udce9"``, which is an ordinary source path on a real filesystem —
    and encoding that to UTF-8 raises ``UnicodeEncodeError``. So a supported
    string could make a package unaddressable, and worse, asymmetrically:
    ``canonical_json`` accepted the same value happily, since only the encode
    step failed (Codex review). Standard JSON escaping represents a lone
    surrogate as ``\\udce9`` and the payload encodes cleanly.

    Second, an ASCII payload is re-derivable by any implementation that can
    produce the same JSON escaping, rather than requiring agreement on UTF-8
    byte sequences or on Python's ``surrogatepass`` — which was the other
    candidate fix and would have written WTF-8 no other reader is obliged to
    understand. A content-addressed store that other tools may re-derive
    digests for should not depend on the quirks of one runtime's encoder.

    Every digest this function returns is domain-separated from
    :func:`raw_digest`'s (a fixed tag is mixed in ahead of the payload --
    see `_JSON_DOMAIN`), so a JSON value and an unrelated raw byte buffer
    that happen to encode to the same bytes can never share an address.

    This changes the digest of any value relative to earlier revisions of
    this module (the non-ASCII fix above, and now the domain tag). That is
    free precisely because Phase 0 persists nothing: no stored package
    carries a digest computed the old way. It would not be free later,
    which is why it is settled now.
    """
    stripped = strip_capture_metadata(value)
    return semantic_digest_of_canonical_form(stripped, algorithm=algorithm)


def semantic_digest_of_canonical_form(
    canonical_stripped: Any, *, algorithm: str
) -> str:
    """:func:`semantic_digest`'s hashing half, given an already-canonical,
    capture-metadata-stripped value.

    Split out so a caller that has *already* produced this exact form (e.g.
    `InMemoryObjectStore.put`, which calls :func:`strip_capture_metadata`
    itself to snapshot what it stores before hashing it -- see that call
    site's own comment) can hash it without a second, redundant
    `canonical_form` traversal of content that is already canonical.
    `semantic_digest` itself still always re-derives the canonical form from
    whatever it is given, since its public contract is "the digest of this
    value's canonical form" for an arbitrary, possibly non-canonical input.
    """
    _reject_surrogate_pairs(canonical_stripped)
    payload = json.dumps(
        canonical_stripped,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return _digest_from_payload(payload, algorithm=algorithm, domain=_JSON_DOMAIN)


#: Domain separators mixed into the hashed bytes ahead of the actual payload
#: (see `_digest_from_payload`) so a raw binary buffer and an unrelated
#: JSON-shaped value that happen to encode to the same bytes -- `b"{}"` and
#: `{}` both encode to the two bytes `b"{}"` -- can never collide on one
#: address. Without this, `InMemoryObjectStore.put()` would keep whichever
#: representation was stored first and `get()` would return it regardless of
#: which one a later caller actually asked to store (Codex review).
_JSON_DOMAIN = b"json:"
_RAW_DOMAIN = b"raw:"


def _digest_from_payload(payload: bytes, *, algorithm: str, domain: bytes) -> str:
    """Shared `<algorithm>:<hex>` formatting for an already-encoded payload.

    Used by both :func:`semantic_digest` (JSON-encoded content) and
    :func:`raw_digest` (raw binary content) so the two rules below can't
    drift between two independent copies. `domain` is prepended to `payload`
    before hashing -- see `_JSON_DOMAIN`/`_RAW_DOMAIN`.
    """
    digester = hashlib.new(algorithm, domain + payload)
    if digester.digest_size == 0:
        # SHAKE and friends are extendable-output functions: `hashlib.new`
        # accepts them, but `hexdigest()` requires a length, so a caller
        # selecting one got a bare `TypeError: hexdigest() missing required
        # argument 'length'` from inside a digest call (Codex review).
        # `algorithm` is public and exists so a future algorithm change is
        # expressible, which makes an unusable-but-accepted value a real trap
        # rather than a theoretical one.
        #
        # Detected by `digest_size == 0` rather than an allowlist of names: an
        # allowlist would also refuse a future fixed-length algorithm, which is
        # the opposite of what this parameter is for. Refusing rather than
        # picking a length is the point — an arbitrary output length chosen
        # here would silently become part of every digest this build writes,
        # and no reader could know which length produced one.
        raise ValueError(
            f"{algorithm!r} is an extendable-output function with no fixed "
            "digest size; a content address needs a fixed-length digest, so "
            "choose a fixed-length algorithm such as sha256"
        )
    if digester.name not in hashlib.algorithms_guaranteed:
        # `hashlib.algorithms_available` is whatever this host's OpenSSL
        # happens to expose (`sm3`/`ripemd160`/`sha512_224`/`sha512_256` on
        # a typical Linux build) -- real and fixed-size, so nothing above
        # would refuse it, but `object_relpath`/`ObjectRef` already refuse
        # such an algorithm for the identical portability reason. Without
        # this check here too, `put()` could mint a digest no `ObjectRef`
        # could ever be built from -- a store producing an unreferenceable
        # object (Codex review). Checked on `digester.name` (the resolved
        # canonical spelling), not the caller's own, so an alias for a
        # guaranteed algorithm isn't rejected by its unresolved spelling.
        raise ValueError(
            f"{digester.name!r} is not in hashlib.algorithms_guaranteed, so "
            "it is not portable to every platform this package might be "
            "read on"
        )
    # `digester.name`, not the caller's spelling. `hashlib` accepts aliases
    # (`SHA256`, `sha-256`, `SHA-512`), and preserving one gave the same
    # logical object several content addresses whose hex halves were
    # identical — defeating deduplication, and emitting prefixes a reader has
    # no reason to recognize (Codex review). The address is supposed to be a
    # property of the content, so nothing incidental to the caller may reach
    # it.
    return f"{digester.name}:{digester.hexdigest()}"


def raw_digest(
    payload: bytes | bytearray | memoryview, *, algorithm: str = "sha256"
) -> str:
    """Content digest of a raw binary payload -- D7's counterpart to
    :func:`semantic_digest` for content :func:`canonical_form` cannot
    represent at all (it raises ``TypeError`` on any binary buffer, by
    design -- its own docstring already directs a caller with such a
    payload here: "store binary payloads as raw objects and reference them
    by digest"). Hashes the payload's bytes directly: no JSON encoding, and
    no capture-metadata stripping, since a raw payload carries no such
    structure to strip.

    Same ``"<algorithm>:<hex>"`` format and the same fixed-digest-size and
    canonical-spelling rules as `semantic_digest` (via the shared
    `_digest_from_payload`), so a caller building an `ObjectRef` for a raw
    artifact and the `ObjectStore` that stores it always agree on its
    address.
    """
    if not _is_binary_buffer(payload):
        raise TypeError(
            f"{type(payload).__name__} is not a binary buffer; raw_digest "
            "hashes bytes-like payloads only -- use semantic_digest for "
            "JSON-shaped content"
        )
    return _digest_from_payload(bytes(payload), algorithm=algorithm, domain=_RAW_DOMAIN)
