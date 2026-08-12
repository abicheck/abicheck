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

"""Shared qualified-name segmentation and versioned inline-namespace helpers.

Leaf module (no imports from the rest of ``abicheck``) so it can be shared
between ``diff_namespaces.py`` (which owns the segment-splitting logic this
module was extracted from) and any other detector that needs to recognize a
versioned inline-namespace segment (``v1``, ``_V2``, ``__1``, ...) without
re-implementing the same regex.

A versioned inline namespace makes the *same* declaration reachable under
two qualified spellings: the full path (``detail::v1::x``) and the
version-elided path (``detail::x``) that unqualified lookup from the
enclosing scope also resolves to. When a header-AST producer surfaces both
spellings as separate top-level declarations, a name-keyed detector that
doesn't canonicalize away the version segment double-reports the identical
change once per spelling.

``diff_namespaces.py``'s function/type detectors use :func:`segments`/
:func:`version_strip_segments` directly, gated on real extraction-data
identity (a shared mangled name, a shared source location) before ever
merging two spellings -- see its own module docstring. There is
deliberately **no** merge helper here for comparing a plain name-keyed
mapping like ``AbiSnapshot.constants`` *across two different snapshots*: a
header constant carries no identity beyond its own value there, and
value-equality alone was tried and repeatedly shown (Codex review, P1,
three rounds) to be indistinguishable from coincidence in both directions
-- it can hide a real value divergence between two unrelated declarations
that happen to start equal, and it can also merge two unrelated
declarations that never even coexist in the same snapshot, each present on
only one side. ``diff_symbols._diff_constants`` therefore compares
``AbiSnapshot.constants`` unmodified across snapshots; double-reporting a
constant value *change* once per versioned-namespace spelling remains an
accepted, documented limitation there (see that function's docstring).

:func:`dedup_versioned_namespace_alias_items` is a narrower, different
operation this module *does* provide: collapsing a using-declaration's
re-exported spelling of a constant back onto the single declaration it
names, *within one already-collected snapshot's own item list* -- not
across two snapshots being compared. It is safe where the rejected
cross-snapshot merge was not because it is gated on strictly more, and
different, evidence: both a real :func:`version_strip_segments` structural
match (never a declaration's own leaf name) AND identical values, for two
entries that provably coexist in the same extraction pass. A
using-declaration cannot legally re-export a constant under a different
value, so within one snapshot this combination can only misfire on two
genuinely distinct declarations that coincidentally share both a
version-alias-shaped name pair and an identical value -- a materially
narrower coincidence than the bare value equality rejected above, and an
accepted residual risk documented on the function itself (Codex review,
P2) rather than eliminated, since doing so needs real using-shadow/target
evidence no header-AST producer available here exposes.

It always keeps the version-*qualified* spelling and drops the
version-*stripped* alias, never the reverse (Codex review, P1: an earlier
revision kept the shorter alias instead, which silently manufactured a
``CONSTANT_REMOVED``/``CONSTANT_ADDED`` pair whenever a release added or
removed only the ``using`` re-export while the real declaration was
unchanged -- see the function's own docstring for why keeping the
canonical spelling is the direction that stays diff-invariant across that
transition).
"""

from __future__ import annotations

import re as _re

# Matches segment-name shapes commonly used as a versioned inline
# namespace: ``_V1``, ``__v2``, ``v3``, ``__1``. Anchored to whole
# segment match (caller passes a single segment string). Captures the
# integer suffix for ordering checks.
_VERSION_NS_RE = _re.compile(r"^_{0,2}[Vv]?(\d+)$")


def segments(qualified: str) -> list[str]:
    """Split a qualified C++ name into namespace segments.

    Template arguments are stripped before splitting so that
    ``ns::experimental::sort<int>`` -> ``["ns", "experimental", "sort"]``.
    Operator names containing ``::`` (extremely rare in declared form) are
    not handled specially; this is acceptable because callers only care
    about segment ordering for namespace identification.
    """
    if not qualified:
        return []
    # Fast path: a name with neither a ``::`` separator nor a template ``<``
    # is its own single segment -- the overwhelmingly common case. See
    # diff_namespaces.py's history for why this matters for perf on
    # versioned-symbol libraries with thousands of plain-name findings.
    if "::" not in qualified and "<" not in qualified:
        return [qualified]
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
            i += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        if depth == 0:
            buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]


def version_suffix(segment: str) -> int | None:
    """Return the integer suffix if *segment* looks like a versioned
    inline-namespace tag (``_V1``, ``__1``, ``v2``, ...); else ``None``.
    """
    m = _VERSION_NS_RE.match(segment)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def version_strip_segments(segs: list[str]) -> tuple[tuple[str, ...], int | None]:
    """Strip any one versioned-*namespace* segment and return
    ``(stripped_segments, version_int)``.

    Returns ``(tuple(segs), None)`` unchanged when no versioned segment is
    present. Only the first matching segment is stripped -- nested
    versioned namespaces are vanishingly rare in practice and the simple
    rule keeps the matching key stable.

    Only scans segments *before* the last one (CodeRabbit review: a
    version-shaped segment can legitimately be the declaration's own leaf
    name, not a namespace -- a constant or type literally named ``v1`` or
    ``v2``). The last segment is always the leaf, never a namespace, so it
    is never a candidate: scanning it could strip the entity's own name
    (corrupting the key to just its enclosing scope) or make two genuinely
    different leaves (``ns::v1``, ``ns::v2``) collide on the same stripped
    key.
    """
    for i, s in enumerate(segs[:-1]):
        v = version_suffix(s)
        if v is not None:
            return tuple(segs[:i] + segs[i + 1 :]), v
    return tuple(segs), None


def dedup_versioned_namespace_alias_items(
    items: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Collapse a using-declaration's re-exported spelling of a header
    constant back onto the single declaration it names.

    *items* is ``(qualified_name, value, declaring_header)`` triples from one
    already-collected header-AST extraction pass (the shape
    ``dumper_castxml.py``'s ``_iter_public_constants`` produces). A plain
    ``namespace v1 { constexpr T x = ...; } using v1::x;`` re-export makes a
    header-AST producer emit a *second*, independent declaration for the
    using-declaration -- keyed under its own qualified name (``ns::x``)
    alongside the real declaration's (``ns::v1::x``). Nothing keyed by exact
    name -- not ``model.py``'s first-wins dedup, not a caller iterating
    ``AbiSnapshot.constants`` -- can tell these apart on its own: the two
    don't collide on a key, they are two distinct keys naming one
    declaration. See this module's own docstring for why this is a
    deliberately narrower, same-snapshot operation, not the value-equality
    merge already rejected for cross-snapshot comparison.

    For every item whose qualified name strips one versioned-inline-namespace
    segment (:func:`version_strip_segments`), if the stripped (alias)
    spelling is ALSO present in *items* with an identical value, the
    version-*stripped* alias is dropped and only the version-*qualified*
    original -- the real declaration's own full name -- is kept.

    This direction (drop the alias, keep the qualified original) is
    deliberate and load-bearing, not a style choice (Codex review, P1: an
    earlier revision did the reverse). ``AbiSnapshot.constants`` is built
    independently per side of a comparison, so whether a given side's item
    list even contains the alias spelling depends only on whether *that
    side's* headers happened to declare the ``using`` re-export -- a
    release can add or remove just the re-export while the real declaration
    is completely unchanged. Keeping the version-stripped alias (the
    earlier, wrong direction) meant a side WITHOUT the re-export kept its
    one qualified key while a side WITH it collapsed down to the alias key
    instead -- two different surviving keys for the unchanged declaration,
    which ``_diff_constants`` then read as a spurious
    ``CONSTANT_REMOVED``/``CONSTANT_ADDED`` pair. Keeping the qualified
    spelling instead is invariant to whether either side happens to carry
    the alias: a side with the re-export collapses down to the SAME key a
    side without it already used, so adding or removing only the re-export
    produces no key-set difference at all. The trade-off this accepts is the
    complementary one already documented for the direct-clang backend in
    ``AGENTS.md``'s Known gaps -- the re-export's own addition/removal goes
    undetected -- rather than a new, castxml-only failure mode of fabricated
    add/remove noise.

    Returns *items* unchanged (same list identity not guaranteed, but same
    contents) when no such pair exists, which is the overwhelmingly common
    case: most versioned-namespace declarations have no re-export at all.

    **Accepted residual risk (Codex review, P2):** the gate is name-shape
    plus value-equality, not proven using-shadow/target identity -- no
    header-AST producer available here exposes that relationship. Two
    genuinely independent declarations that happen to form a
    version-alias-shaped name pair AND happen to share a value at the
    moment of extraction (e.g. an unrelated top-level ``x`` and a nested
    ``v1::x`` that both currently equal the same literal) will merge, and a
    later, independent change to the dropped one can then read as an
    ``ADDED`` instead of a ``CHANGED``. This is the same class of
    accepted, documented limitation as ``diff_symbols._diff_constants``'s
    own value-fingerprint caveat and `_type_index_items`'s structural-
    identity caveat -- narrower here than either (it requires the
    coincidence to hold at extraction time, not just eventually), but not
    eliminated, since doing so needs real identity evidence this module
    does not have.
    """
    by_name = {name: value for name, value, _header in items}
    drop: set[str] = set()
    for name, value, _header in items:
        stripped_segs, version = version_strip_segments(segments(name))
        if version is None:
            continue
        stripped_name = "::".join(stripped_segs)
        if stripped_name == name:
            continue
        alias_value = by_name.get(stripped_name)
        if alias_value is not None and alias_value == value:
            drop.add(stripped_name)
    if not drop:
        return items
    return [item for item in items if item[0] not in drop]
