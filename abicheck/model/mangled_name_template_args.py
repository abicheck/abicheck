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

"""Length-prefixed source-name/template-argument-skip primitives, and
recursive type-candidate identifier extraction for an Itanium
template-argument span (``mangled_name.py``'s findings-analysis-fixes review
round 3, finding 4 fix), split into its own leaf module purely to keep
``mangled_name.py`` under the AI-readiness production file-size cap --
``read_length_prefixed_name``/``skip_template_args`` are relocated,
unchanged, pre-existing primitives (``mangled_name.py`` imports them back
rather than keeping a second copy); the two ``collect_*``/
``_collect_nested_name_candidates`` functions below are genuinely new logic,
not a relocation of pre-existing responsibility.

``mangled_name.itanium_special_name_owner_identifiers`` needs to collect
every class/namespace name embedded in a templated owner's own
template-argument list(s), at any nesting depth, but must NOT flatten a
*nested, namespace-qualified* template argument (e.g. the ``ns::Inner<int>``
in ``Outer<ns::Inner<int>>``) into independent ``"ns"``/``"Inner"`` tokens --
a bare namespace-path *component* must never stand in as its own implicated
type candidate (Codex review, fresh evidence: an unrelated modeled type
sharing a namespace's own bare name could otherwise masquerade as a
genuinely unresolvable owner and wrongly demote a real break). This module
is the general, recursive counterpart of that rule: any ``N…E`` nested-name
production found anywhere within a template-argument span is itself parsed
and its own components fused with ``"::"`` (plus its own bare tail),
mirroring ``surface.py``'s ``_type_identifiers`` treatment of an ordinary
demangled qualified type string.
"""

from __future__ import annotations

_ASCII_DIGITS = "0123456789"


def read_length_prefixed_name(s: str, i: int) -> tuple[str | None, int]:
    """Read a ``<len><identifier>`` source-name at ``s[i]``.

    Returns ``(name, next_index)`` or ``(None, i)`` if malformed. Only ASCII
    digits count as the length prefix — Python's ``str.isdigit()`` also
    accepts Unicode digits (e.g. ``²``) that ``int()`` then rejects.
    Accumulates digit-by-digit, capped at ``len(s)`` (mirrors
    ``source_link._consume_source_name``), so an untrusted symbol can't trip
    Python's integer-conversion digit limit (Codex review, PR #930).
    """
    j, n = i, 0
    while j < len(s) and s[j] in _ASCII_DIGITS:
        n = n * 10 + (ord(s[j]) - ord("0"))
        if n > len(s):
            return None, i
        j += 1
    name = s[j : j + n]
    return (None, i) if j == i or len(name) != n else (name, j + n)


def skip_template_args(s: str, i: int) -> int | None:
    """``s[i] == 'I'``: return the index past the matching ``E``, or ``None``.

    Tracks nested template-argument (``I``) and nested-name (``N``) openers so
    the inner ``E`` of e.g. ``Box<ns::T>`` does not close the outer list early,
    skips length-prefixed names so their literal ``I``/``N``/``E`` letters are
    not miscounted, and consumes ``L<type><value>E`` literal operands as a unit
    (non-type template args, e.g. ``Array<4>`` → ``ILi4EE``) so their value
    digits aren't read as a length and their closing ``E`` isn't counted.
    Pathological encodings (e.g. substitutions whose base-36 index contains
    ``E``) may mis-balance; the caller treats ``None`` as "unparseable" and
    falls back, so a wrong guess never produces a finding.
    """
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in _ASCII_DIGITS:
            name, i = read_length_prefixed_name(s, i)
            if name is None:
                return None
            continue
        if c == "L":
            close = s.find("E", i + 1)
            if close == -1:
                return None
            i = close + 1
            continue
        if c in ("I", "N"):
            depth += 1
            i += 1
        elif c == "E":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1  # builtin type, qualifier, or substitution character
    return None


def _collect_nested_name_candidates(
    s: str, i: int, end: int
) -> tuple[frozenset[str], int] | None:
    """Parse an Itanium ``N…E`` nested-name production starting at
    ``s[i] == "N"`` (within ``s[:end]``), returning the type-candidate
    identifiers it contributes -- its own fully-qualified name (components
    fused with ``"::"``, never emitted as separate standalone tokens), its
    own bare tail, and every identifier found in any of its components' own
    directly-attached template-argument list (recursively, via
    :func:`collect_type_candidate_identifiers`) -- together with the index
    just past the closing ``E``.

    This is what lets a *nested* qualified type inside a template-argument
    list (e.g. the ``ns::Inner<int>`` in ``Outer<ns::Inner<int>>``) fuse its
    own ``ns``/``Inner`` the same way a top-level owner does, instead of
    flattening them into independent tokens -- see this module's own
    docstring for the concrete regression this closes. Returns ``None``
    when the production cannot be parsed (an operator/ctor/substitution
    component, or running off the end without a terminating ``E``) so the
    caller treats it as opaque and moves on -- a wrong guess never
    fabricates a candidate, it can only omit one, matching
    ``mangled_name.py``'s established fail-safe convention throughout.
    """
    j = i + 1
    while j < end and s[j] in ("r", "V", "K", "R", "O"):
        j += 1
    bare_components: list[str] = []
    extra: set[str] = set()
    while j < end:
        c = s[j]
        if c == "E":
            j += 1
            break
        if c not in _ASCII_DIGITS:
            return None  # operator/ctor/substitution component -- not modelled
        name, after_name = read_length_prefixed_name(s, j)
        if name is None:
            return None
        j = after_name
        while j < end and s[j] == "B":
            tag, k = read_length_prefixed_name(s, j + 1)
            if tag is None:
                break
            name = f"{name}[abi:{tag}]"
            j = k
        bare_components.append(name)
        if j < end and s[j] == "I":
            t_end = skip_template_args(s, j)
            if t_end is None:
                return None
            extra |= collect_type_candidate_identifiers(s, j, t_end)
            j = t_end
    else:
        return None  # ran off the end without a terminating E
    if not bare_components:
        return None
    result = set(extra)
    result.add("::".join(bare_components))
    result.add(bare_components[-1])
    return frozenset(result), j


def collect_type_candidate_identifiers(s: str, start: int, end: int) -> frozenset[str]:
    """Type-candidate identifiers found anywhere in ``s[start:end]`` -- the
    contents of a directly-attached Itanium template-argument list (an
    ``I…E`` block, called with its inner ``start``/``end`` bounds) -- at any
    nesting depth.

    Unlike a flat "every length-prefixed name in the span" scan, a nested
    ``N…E`` qualified-name production found here is parsed and fused via
    :func:`_collect_nested_name_candidates` rather than flattened into
    separate scope-path tokens, mirroring how a top-level owner's own scope
    path is handled and how ``surface.py``'s ``_type_identifiers`` treats an
    ordinary demangled qualified type string (a namespace qualifier is only
    ever part of the fused qualified token or its own trailing ``::``
    segment, never a free-standing token). A ``L<type><value>E`` non-type
    template-argument operand is skipped as a unit (its value digits are not
    a length prefix and must not be misread as one); every other byte
    (substitution codes, builtin type letters, qualifiers) is simply stepped
    over. A malformed length prefix or nested-name production stops the scan
    early / falls through to treating it as opaque, rather than raising --
    mirrors ``mangled_name.py``'s fail-safe convention throughout.
    """
    names: set[str] = set()
    i = start
    while i < end:
        c = s[i]
        if c == "N":
            parsed = _collect_nested_name_candidates(s, i, end)
            if parsed is None:
                i += 1
                continue
            fused, after = parsed
            names |= fused
            i = after
            continue
        if c in _ASCII_DIGITS:
            name, j = read_length_prefixed_name(s, i)
            if name is None:
                break
            names.add(name)
            i = j
            if i < end and s[i] == "I":
                t_end = skip_template_args(s, i)
                if t_end is None:
                    break
                names |= collect_type_candidate_identifiers(s, i, t_end)
                i = t_end
            continue
        if c == "L":
            close = s.find("E", i + 1, end)
            if close == -1:
                break
            i = close + 1
            continue
        i += 1
    return frozenset(names)
