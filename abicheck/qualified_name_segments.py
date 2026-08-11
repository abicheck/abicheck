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
spellings as separate top-level declarations (observed for both function/
type declarations and header constants), a name-keyed detector that doesn't
canonicalize away the version segment double-reports the identical change
once per spelling. :func:`dedupe_versioned_spellings` is the shared fix for
name-keyed collections (currently used by ``diff_symbols._diff_constants``);
``diff_namespaces.py``'s own namespace-shape detectors canonicalize inline,
using :func:`segments`/:func:`version_strip_segments` directly, since they
need the split list itself, not just a deduped mapping.
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
    """Strip any one versioned-namespace segment and return
    ``(stripped_segments, version_int)``.

    Returns ``(tuple(segs), None)`` unchanged when no versioned segment is
    present. Only the first matching segment is stripped -- nested
    versioned namespaces are vanishingly rare in practice and the simple
    rule keeps the matching key stable.
    """
    for i, s in enumerate(segs):
        v = version_suffix(s)
        if v is not None:
            return tuple(segs[:i] + segs[i + 1 :]), v
    return tuple(segs), None


def dedupe_versioned_spellings(names: dict[str, str]) -> dict[str, str]:
    """Collapse entries keyed by a qualified name that are the same
    declaration spelled two ways via an elided versioned inline-namespace
    segment (``detail::v1::x`` and ``detail::x``).

    A version-shaped segment name is not proof of an *inline* namespace --
    ``v1`` is a legal name for an ordinary namespace too, in which case
    ``detail::v1::x`` and ``detail::x`` are two unrelated declarations that
    happen to share a leaf name (Codex review, P1: collapsing purely on
    name shape can hide a real value change on one spelling while
    discarding the other). There is no symbol/mangled identity available
    for a name-keyed mapping like this to check instead, so the one piece
    of corroborating evidence within reach is used: a group is only merged
    when *every* spelling in it already carries the identical value -- the
    invariant a true alias must satisfy (they're the same declaration) and
    one two unrelated declarations satisfy only by coincidence. A group
    whose values disagree is left unmerged, each spelling kept as its own
    entry, so the pre-existing double-report is the accepted fallback
    rather than a newly-introduced false suppression.
    """
    groups: dict[str, list[str]] = {}
    for name in names:
        canon_segs, _ = version_strip_segments(segments(name))
        canon = "::".join(canon_segs)
        groups.setdefault(canon, []).append(name)
    out: dict[str, str] = {}
    for canon, group_names in groups.items():
        if len(group_names) > 1 and len({names[n] for n in group_names}) != 1:
            for n in group_names:
                out[n] = names[n]
            continue
        rep = canon if canon in group_names else group_names[0]
        out[rep] = names[rep]
    return out
