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

"""Template-nesting-aware ``"::"`` splitting, plus versioned/ABI-tag
inline-namespace *segment recognition*, for a fully-qualified C++ name --
primitives both ``qualified_name_segments.py`` (the ``compare`` layer's own
namespace-segment splitter) and a producer building a typed ``ScopePath``
straight from a flat qualified-name STRING (rather than walking a tree/AST
that already carries scope structure, as DWARF's DIE walk and the two
header-AST backends' own AST walks do) need.

Lives in ``model/`` rather than at the package root specifically so
``extract`` may depend on it (``extract -> model``, ADR-061) --
``qualified_name_segments.py`` itself belongs to the ``compare`` layer, which
``extract`` may not import (confirmed by ``scripts/check_architecture.py``
failing on exactly that edge). Splitting a fully-qualified name back into
segments, and recognizing whether one already-split segment's *spelling*
matches a versioned (``v1``, ``__1``, ...) or named-toolchain-tag
(``__cxx11``, ``__ndk1``) inline namespace, are both pure text
classification with no ``compare``-specific *decision* in them: unlike
:func:`~abicheck.qualified_name_segments.version_strip_segments`/
:func:`~abicheck.qualified_name_segments.strip_inline_abi_namespaces`
(which decide that two differently-spelled qualified names identify the
*same* declaration for diffing purposes -- a real compare-layer judgement,
and rightly left there), :func:`version_suffix` and
:func:`is_inline_abi_namespace_segment` only label one segment string; they
merge nothing and compare no two names against each other.

That distinction is why this pair moved down from ``qualified_name_segments.py``
after this module first shipped only :func:`split_top_level_scopes` (Codex
review, PR #1025): ``extract/headers/scope_segments.py``'s ``namespace_segment``
already documented -- correctly, at the time -- that it could not read
``qualified_name_segments.version_suffix`` to populate
``InlineNamespace.version_tag`` without crossing the same forbidden
``extract -> compare`` edge, and left the field empty rather than importing
across it or duplicating the regex. A second, independent
``extract``-adjacent need for the identical *recognition* (not merging) --
PDB's own ``pdb_metadata._is_user_visible``, which must NOT drop a
declaration nested under libc++'s ``std::__1`` or libstdc++'s
``std::__cxx11`` the way it correctly drops one under a genuinely
compiler-internal ``__``-prefixed segment -- is what tips this from "leave
the gap, don't drive-by migrate" (that module's own stated policy) to a real,
now-doubly-motivated move: two independently constructible copies of the same
recognition regex is exactly the shape ADR-063's governing invariant exists
to forbid, the identical reasoning that already moved
:func:`split_top_level_scopes` down here. ``qualified_name_segments.py``
re-exports both names unchanged for every existing
``from .qualified_name_segments import version_suffix`` (or
``is_inline_abi_namespace_segment``) call site.

``qualified_name_segments.raw_segments`` also delegates here rather than
keeping its own independent copy of the splitting loop -- the same
"no duplicate representation" reasoning as above.

Leaf module: no imports beyond the stdlib.
"""

from __future__ import annotations

import re as _re
from collections.abc import Iterator as _Iterator

__all__ = [
    "is_inline_abi_namespace_segment",
    "iter_top_level_chars",
    "split_top_level_scopes",
    "version_suffix",
]

# Matches segment-name shapes commonly used as a versioned inline
# namespace: ``_V1``, ``__v2``, ``v3``, ``__1``. Anchored to whole
# segment match (caller passes a single segment string). Captures the
# integer suffix for ordering checks.
_VERSION_NS_RE = _re.compile(r"^_{0,2}[Vv]?(\d+)$")

#: Toolchain *ABI-tag* inline namespaces that are not version-number-shaped
#: and therefore invisible to :func:`version_suffix`: libstdc++'s dual-ABI
#: ``std::__cxx11`` and the Android NDK's ``std::__ndk1``. Both are inline
#: namespaces — transparent for name lookup — so a declaration gaining or
#: losing one is the *same* entity spelled two ways, exactly like the
#: ``v1``/``__1`` family ``version_suffix`` already recognizes.
_ABI_TAG_NS_RE = _re.compile(r"^__(?:cxx|ndk)\d+$")


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


def split_top_level_scopes(qualified: str) -> list[str]:
    """Split *qualified* on ``"::"`` at template-nesting depth zero only,
    keeping each segment's own template arguments intact.

    ``ns::Map<std::pair<int, int>>::iterator`` yields three segments
    (``["ns", "Map<std::pair<int, int>>", "iterator"]``) -- the ``::``
    inside the argument list is not a separator, since bracket depth is
    tracked via a plain ``<``/``>`` counter (matching this splitter's
    existing use: real qualified type/function names, not arbitrary text
    that might contain an unrelated ``<``/``>`` comparison operator pair --
    a caller with that concern needs a bracket-KIND-aware scanner instead,
    e.g. :func:`~abicheck.extract.semantic_normalizer_artifacts.
    has_unresolved_component`'s own).

    >>> split_top_level_scopes("ns::Map<std::pair<int, int>>::iterator")
    ['ns', 'Map<std::pair<int, int>>', 'iterator']
    >>> split_top_level_scopes("Widget")
    ['Widget']
    >>> split_top_level_scopes("")
    []
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


def iter_top_level_chars(text: str) -> _Iterator[tuple[int, str]]:
    """Yield ``(index, char)`` for every character of *text* sitting at
    true top level -- outside any ``(...)``/``[...]``/``<...>`` bracket
    nesting and outside any quoted string/character literal.

    Tracks a bracket-KIND-aware STACK, not a flat depth counter, mirroring
    ``extract.semantic_normalizer_artifacts.has_unresolved_component``'s
    own hardened design (see that function's docstring for the full
    account, arrived at over seven review rounds): a real ``>>`` shift/
    comparison operator sitting inside a parenthesized non-type template
    argument is not two template closers, and a ``<`` already inside an
    open paren/bracket is a real comparison operator, not a template
    opener -- a ``<``/``>`` only pushes/pops the stack when the innermost
    still-open entry is (or, for ``>``, already is) itself a ``<``.
    Because ``<``/``>`` are consumed as stack operations rather than
    yielded, a caller never sees them directly; it only ever learns where
    the *other* top-level characters are (``::``, ``*``, ``&``, ...),
    which is all :func:`~abicheck.diff_helpers.depth_aware_bare_name` and
    :func:`~abicheck.compare.opaque_types._is_indirect_spelling` need.

    Adds quote handling on top of that shared design (not needed by
    ``has_unresolved_component``'s own castxml-sentinel search, needed by
    both callers above): a quoted literal is skipped outright, honoring
    backslash escapes (``'\\''`` doesn't end one character early), so a
    ``'>'``/``'<'`` inside a non-type template argument's own literal
    doesn't affect the bracket stack either.
    """
    stack: list[str] = []
    quote = ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if stack:
                stack.pop()
        elif ch == "<":
            if not stack or stack[-1] not in "([":
                stack.append(ch)
        elif ch == ">":
            if stack and stack[-1] == "<":
                stack.pop()
        elif not stack:
            yield i, ch
        i += 1
