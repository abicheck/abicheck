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
deliberately **no** merge helper here for a plain name-keyed mapping like
``AbiSnapshot.constants``: a header constant carries no identity beyond its
own value, and value-equality alone was tried and repeatedly shown
(Codex review, P1, three rounds) to be indistinguishable from coincidence
in both directions -- it can hide a real value divergence between two
unrelated declarations that happen to start equal, and it can also merge
two unrelated declarations that never even coexist in the same snapshot,
each present on only one side. ``diff_symbols._diff_constants`` therefore
compares ``AbiSnapshot.constants`` unmodified; double-reporting a constant
value change once per versioned-namespace spelling is an accepted, documented
limitation (see that function's docstring) rather than a heuristic that
cannot be made sound with the data available today.
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


#: Toolchain *ABI-tag* inline namespaces that are not version-number-shaped
#: and therefore invisible to :func:`version_suffix`: libstdc++'s dual-ABI
#: ``std::__cxx11`` and the Android NDK's ``std::__ndk1``. Both are inline
#: namespaces — transparent for name lookup — so a declaration gaining or
#: losing one is the *same* entity spelled two ways, exactly like the
#: ``v1``/``__1`` family ``version_suffix`` already recognizes.
_ABI_TAG_NS_RE = _re.compile(r"^__(?:cxx|ndk)\d+$")


def is_inline_abi_namespace_segment(segment: str) -> bool:
    """True when *segment* names an inline namespace used as an ABI tag.

    Union of the version-number family (``v1``, ``__1``, ``_V2`` — see
    :func:`version_suffix`) and the named toolchain tags (``__cxx11``,
    ``__ndk1``). Deliberately not a general "looks like an implementation
    detail" test: a segment such as ``detail``, ``impl`` or oneTBB's ``d1``
    is an *ordinary* namespace whose rename is a real move of the
    declarations inside it, so it must not be stripped.
    """
    return version_suffix(segment) is not None or bool(_ABI_TAG_NS_RE.match(segment))


def strip_inline_abi_namespaces(qualified: str) -> tuple[str, ...]:
    """Return *qualified*'s segments with every inline ABI-tag namespace removed.

    Two spellings that reduce to the same tuple name the same entity through
    a transparent inline namespace (``std::basic_string`` vs.
    ``std::__cxx11::basic_string``); two that do not are genuinely different
    scopes (``tbb::detail::d1::graph`` vs. ``tbb::detail::d2::graph``).

    The leaf segment is never stripped, for the same reason
    :func:`version_strip_segments` never scans it: a declaration may
    legitimately *be* named ``v1``, and stripping its own name would collapse
    it onto its enclosing scope.

    Splitting keeps each segment's template arguments, unlike
    :func:`segments`: two spellings that differ only inside an *enclosing*
    segment's argument list (``ns::Outer<int>::Inner`` vs.
    ``ns::Outer<float>::Inner``) name different entities, and dropping the
    arguments would reduce both to the same tuple.
    """
    segs = raw_segments(qualified)
    if len(segs) < 2:
        return tuple(segs)
    leaf = len(segs) - 1
    return tuple(
        s
        for i, s in enumerate(segs)
        if i == leaf or not is_inline_abi_namespace_segment(s)
    )


def raw_segments(qualified: str) -> list[str]:
    """:func:`segments`, but keeping each segment's template arguments.

    Splits on ``::`` at template-nesting depth zero only, so
    ``ns::Map<std::pair<int, int>>::iterator`` yields three segments and the
    ``::`` inside the argument list is not a separator.
    """
    if not qualified:
        return []
    if "::" not in qualified and "<" not in qualified:
        return [qualified]
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            if depth > 0:
                depth -= 1
        elif depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]
