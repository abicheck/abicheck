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

"""``atomic_qualifier_changed`` type-slot canonicalization for
:mod:`abicheck.finding_identity` (``canonical_finding_id``, Codex review,
three rounds).

Split out of ``finding_identity.py`` purely to keep that file under the
2000-line hard cap (CLAUDE.md file-size gate) -- this is a leaf helper with
no dependency back on the rest of that module beyond
:func:`~abicheck.name_classification.canonicalize_type_name`.
"""

from __future__ import annotations

import re

from .name_classification import canonicalize_type_name

# Matches an `_Atomic`-qualified spelling (after stripping a leading
# const/volatile prefix -- see _CV_PREFIX_RE) in either form castxml/clang
# emit: a bare `_Atomic` sentinel (castxml, see below) or a real wrapped
# spelling like `_Atomic(struct Foo *)` (clang). Anchored rather than
# reusing diff_atomic.py's own word-boundary search, since this helper's job
# is "is this ENTIRE type-slot spelling the atomic side", not "does
# _Atomic appear anywhere".
_ATOMIC_SLOT_RE = re.compile(r"^_Atomic\b")
# A leading cv-qualifier castxml's CvQualifiedType prefixes onto a
# non-pointer-value base (e.g. "const _Atomic" wrapping the Atomic node) --
# stripped before atomic detection, then reattached to the result.
_CV_PREFIX_RE = re.compile(r"^(const|volatile)\s+")


def canonicalize_atomic_slot(value: str, other: str) -> str:
    """Canonicalize one ``diff_atomic.py`` type-slot spelling for the
    identity discriminator, given the *other* (opposite) side's raw
    spelling for comparison.

    Some legacy CastXML C11 ``_Atomic`` nodes omit the reference to the
    wrapped type, so ``dumper_castxml.py`` spells them as the lossy sentinel
    ``"_Atomic"``. Nodes that retain the reference and the direct-clang
    backend preserve the wrapped spelling (e.g. ``"_Atomic(struct Foo *)"``).
    Plain
    :func:`canonicalize_type_name` has no notion these name the same
    qualified type, so without this helper CastXML's sentinel and Clang's
    concrete spelling never hash to the same canonical id.

    Collapsing the wrapped content to a fixed marker is only safe when it
    is *redundant* with the transition's other side -- reconstructing
    ``qualifiers + inner + outer-declarator`` and comparing it (through
    :func:`canonicalize_type_name`, so spelling/ordering differences don't
    matter) against ``other``. If they match, the qualifier is the only
    real change (``diff_atomic.py``'s own detection, ``_has_atomic``, only
    tests presence) and CastXML's inner content is genuinely unrecoverable
    noise -- collapse. If they DON'T match, a compound change (e.g. ``int``
    -> ``_Atomic(long)``) also altered the payload; collapsing that would
    conflate it with a pure ``int`` -> ``_Atomic(int)`` qualifier change
    under one ``finding_id`` -- keep the payload instead.

    A declarator applied *outside* the wrapper is never discarded:
    ``dumper_castxml.py``'s ``PointerType``/``ReferenceType`` handling
    appends its own sigil suffix onto whatever it wraps, so "pointer to
    atomic T" (``_Atomic(T) *`` on Clang) spells as ``"_Atomic*"`` on
    CastXML -- distinguishably different from "atomic pointer to T"
    (``_Atomic(T *)`` on Clang), spelled as the bare ``"_Atomic"`` sentinel
    (the Atomic node wraps the pointer, so no outer sigil is appended).
    Collapsing both to one sentinel would conflate two different qualified
    types.
    """
    stripped = value.strip()
    rest = stripped
    quals: list[str] = []
    while True:
        m = _CV_PREFIX_RE.match(rest)
        if not m:
            break
        quals.append(m.group(1))
        rest = rest[m.end() :]
    match = _ATOMIC_SLOT_RE.match(rest)
    if not match:
        return canonicalize_type_name(stripped)
    trailing = rest[match.end() :]
    inner = None
    if trailing.startswith("("):
        # Find the close paren matching the one right after "_Atomic",
        # tracking nesting depth so an inner type that itself contains
        # parens (a function-pointer pointee, say) doesn't end the search
        # early. Unbalanced parens shouldn't occur for a real type-slot
        # spelling, but degrade to "no trailing declarator" rather than
        # raising or keeping unparsed text in the identity.
        depth = 0
        close_idx = None
        for i, ch in enumerate(trailing):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        inner = trailing[1:close_idx] if close_idx is not None else None
        trailing = trailing[close_idx + 1 :] if close_idx is not None else ""
    trailing = trailing.strip()
    redundant = inner is None or canonicalize_type_name(
        " ".join(p for p in (*quals, inner, trailing) if p)
    ) == canonicalize_type_name(other.strip())
    core = (
        "_Atomic"
        if inner is None or redundant
        else f"_Atomic({canonicalize_type_name(inner)})"
    )
    if trailing:
        core = f"{core} {canonicalize_type_name(trailing)}"
    if quals:
        core = f"{' '.join(sorted(set(quals)))} {core}"
    return core
