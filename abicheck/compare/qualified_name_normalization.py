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

"""Qualified-name segmentation and versioned/inline-namespace identity
decisions used for old/new declaration matching (ADR-061 gap B).

The real owner behind the flat ``abicheck.compare.qualified_name_normalization``
compatibility facade's ``segments``/``version_strip_segments``/
``strip_inline_abi_namespaces``/``raw_segments`` half. The *recognition*
primitives this half builds on (:func:`~abicheck.model.qualified_name_split.
version_suffix`/:func:`~abicheck.model.qualified_name_split.
is_inline_abi_namespace_segment`) already live in ``model/`` (re-exported
here unchanged); what stays here is the genuinely `compare`-layer
*decision* built on top of that recognition -- that two differently-spelled
qualified names identify the same declaration for diffing purposes. See
``diff_namespaces.py``'s own module docstring for how its function/type
detectors use these, gated on real extraction-data identity before ever
merging two spellings.

The module's sibling half -- anonymous/lambda-closure ordinal identity,
used to normalize a snapshot's string fields on dump/load rather than to
decide a diff -- moved to :mod:`abicheck.storage.closure_identity` instead,
since its own real caller (``storage.snapshot_load_normalization``) cannot
statically depend on `compare` (``storage``'s ADR-061 imports are `model`
only).
"""

from __future__ import annotations

from ..model.qualified_name_split import (
    is_inline_abi_namespace_segment as is_inline_abi_namespace_segment,
    split_top_level_scopes as _split_top_level_scopes,
    version_suffix as version_suffix,
)


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
    ``::`` inside the argument list is not a separator. Delegates to
    :func:`~abicheck.model.qualified_name_split.split_top_level_scopes` --
    see that module's own docstring for why the splitting primitive itself
    lives in ``model/`` rather than here.
    """
    result: list[str] = _split_top_level_scopes(qualified)
    return result
