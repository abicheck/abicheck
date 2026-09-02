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

"""``CanonicalEntity.template_arguments`` derivation for a record
(``extract.semantic_normalizer``, ADR-063 Phase 6's sixth slice).

Split out of ``semantic_normalizer.py`` (its only caller) purely to keep
that file under the AI-readiness gate's 800-line production maximum, the
identical reason ``semantic_normalizer_artifacts.py``/``semantic_normalizer_
dwarf.py`` were split out one/two slices earlier.

**Backend-agnostic and extraction-free, unlike the fifth slice's DWARF
carve-outs.** A concrete class-template specialization's own ``RecordType.
qualified_name``/``name`` already embeds its full ``Name<Arg1, Arg2>``
spelling on every backend that surfaces one at all (confirmed with real
castxml AND DWARF output: castxml's ``type_name_uncached`` resolves a
specialization to an ordinary, indistinguishable-from-non-template
``<Struct name="Box&lt;int, 3&gt;">`` element; a compiler's own DWARF
``DW_AT_name`` for an emitted instantiation is the identical compound
spelling) -- so decomposing it needs no new identity work, no new backend
call, and no producer-specific branch: it is a pure function of text
already present on every occurrence this normalizer already builds
``canonical_spelling`` from. One function, :func:`split_template_arguments`,
serves every producer this normalizer has ever had or will have, the same
"converge on one shared shape" property the module's own closing paragraph
already claims for the rest of it.

**clang is the one confirmed exception, and it is a missing OCCURRENCE, not
a wrong FACT.** ``dumper_clang.py``'s categorizing walk collects
``CXXRecordDecl``/``RecordDecl`` nodes for ``self._records`` (and therefore
``parse_types()``) but deliberately never a ``ClassTemplateSpecializationDecl``
(confirmed directly: ``build_specialization_index``'s own docstring states
this exactly, for an unrelated vtable/base-lookup reason) -- so a concrete
specialization is never itself a ``RecordType`` on that backend at all; only
the UNINSTANTIATED PATTERN (bare ``"Box"``, never ``"Box<int, 3>"``) is.
Every clang-produced record this normalizer sees is therefore, unconditionally
and confirmedly, NOT an instantiation -- so :func:`split_template_arguments`
correctly reports ``None`` (no top-level ``<...>``) for every one of them,
and this module's caller correctly reports ``Fact.present(())`` (a template
specialization's own compound name, when clang does eventually see one, would
also be positively decomposed, not just this pattern's own confirmed
absence). **What remains unmet, named explicitly rather than silently
dropped:** clang producing no *occurrence at all* for a concrete
specialization means the phase's own acceptance-criteria fixture (a
closure-parameterized template) cannot show cross-backend AGREEMENT for that
entity -- only castxml's own occurrence exists to check. Closing that needs
``dumper_clang.py``'s categorizing walk extended to also recognize
``ClassTemplateSpecializationDecl`` as record-producing (reusing
``extract.headers.clang.templates._specialization_spelling``, already built
for the identical qualified-name reconstruction one caller over for
scope/base-lookup purposes) -- a real, separately-scoped extraction-side
project this slice does not attempt, given its own blast radius across
``dumper_clang.py``'s entire existing test suite (that walk's output shape
has never included a concrete specialization as its own record in this
codebase's history), not a normalizer-only change.

**Functions, typedefs, variables, and enums are deliberately untouched --
none of them can BE a template instantiation the way a record can.** A
function TEMPLATE's own instantiation is real (``identity<int>``), but
neither backend's ``Function.name`` embeds the argument spelling the way a
record's compound name does -- confirmed with real castxml output: an
instantiated function's own ``name`` stays the bare, unparameterized
``"identity"``, with only the MANGLED name (Itanium-encoded, requiring a
real demangler to decode back into argument spellings) carrying the
argument. Closing that is a materially different, larger project (a
mangled-name argument decoder, not a compound-spelling text split) than
this slice's scope. An enum, typedef, or variable can never itself be a
template instantiation at all in the vocabulary this codebase's model
tracks (an enum's own template-ness is not a modeled concept; a plain
``using``/``typedef`` alias and an ordinary variable are never template
entities either) -- ``Fact.not_collected()`` for all three is therefore
left exactly as it already was before this slice, not a regression.

Leaf module: imports nothing beyond stdlib, per ADR-061 D10's leaf-module
contract for a module split out purely for size.
"""

from __future__ import annotations

__all__ = ["split_template_arguments"]

_OPEN_TO_CLOSE = {"<": ">", "(": ")", "[": "]"}
_CLOSERS = frozenset(_OPEN_TO_CLOSE.values())


def _find_last_top_level_scope_separator(name: str) -> int | None:
    """Index of the ``::`` that separates a qualified name's enclosing
    scope from its own leaf segment -- the LAST one at bracket depth 0, so
    a ``::`` inside a template argument (``Box<std::string>``) or inside a
    nested specialization's own scope (``Outer<int>::Inner<double>`` --
    this must find the ``::`` before ``Inner``, not before ``string``) is
    never mistaken for the top-level separator. ``None`` when *name* has no
    top-level ``::`` at all (an unscoped name).
    """
    stack: list[str] = []
    last_sep: int | None = None
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch in _OPEN_TO_CLOSE:
            stack.append(ch)
            i += 1
            continue
        if ch in _CLOSERS:
            if stack:
                stack.pop()
            i += 1
            continue
        if not stack and name.startswith("::", i):
            last_sep = i
            i += 2
            continue
        i += 1
    return last_sep


def _split_top_level_commas(text: str) -> tuple[str, ...]:
    """Split *text* on commas at bracket depth 0 only -- a comma inside a
    nested template argument list, a function-pointer parameter list, or an
    array bound is never a top-level argument separator (``Box<pair<int,
    int>, 3>`` has exactly two top-level arguments, not four).
    """
    parts: list[str] = []
    stack: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _OPEN_TO_CLOSE:
            stack.append(ch)
        elif ch in _CLOSERS:
            if stack:
                stack.pop()
        elif ch == "," and not stack:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return tuple(parts)


def split_template_arguments(name: str) -> tuple[str, ...] | None:
    """The ordered, verbatim template-argument spellings embedded in a
    record's own compound ``Name<Arg1, Arg2>`` spelling -- ``None`` when
    *name*'s own leaf segment (after its last top-level ``::``, if any)
    carries no top-level ``<...>`` at all, i.e. *name* does not name a
    template specialization.

    Each argument is returned VERBATIM (only leading/trailing whitespace
    trimmed) -- deliberately NOT run through ``canonicalize_type_name`` or
    any other canonicalizer. A plain text split cannot tell a TYPE argument
    (which such a canonicalizer targets) from a non-type one (a literal
    value, e.g. ``"3"``, or an enumerator, e.g. ``"Color::RED"``) apart from
    its own text alone -- applying a type-spelling canonicalizer to the
    latter would misinterpret ordinary value text as a type spelling, with
    no concrete cross-backend divergence observed to justify the risk (the
    same "no canonicalizer without a known target divergence to fix"
    discipline this module's own docstring names for ``normalize_header_
    ast``'s constants branch). A closure-typed argument's own raw
    ``"(lambda at <path>:<line>:<col>)"`` marker is therefore stored
    unrenumbered here too -- ``qualified_name_segments.
    renumber_anonymous_closure_identities`` already walks every string
    reachable from ``AbiSnapshot.semantic_ir`` (not excluded via
    ``_PAYLOAD_FIELD_EXCLUSIONS``) and canonicalizes it post-hoc, the
    identical mechanism that already renumbers the SAME marker embedded in
    this record's own ``canonical_spelling`` -- so this function does not
    need, and must not duplicate, that canonicalization itself.

    An explicit specialization using every parameter's own default value
    can render with an empty, but present, argument list (``"Box<>"``, a
    real castxml/clang spelling -- see
    ``extract.headers.clang.templates._specialization_spelling``'s own
    docstring for the confirmed repro) -- this returns ``("",)`` for that
    input, not ``None`` or ``()``, faithfully reporting exactly what the
    compound spelling states: an explicit, if empty, argument list. That
    edge case is deliberately not special-cased into ``()`` here (which
    would make it indistinguishable from "not a template at all") -- the
    caller decides what a blank verbatim entry means for its own purposes.

    >>> split_template_arguments("Box")
    >>> split_template_arguments("Box<int, 3>")
    ('int', '3')
    >>> split_template_arguments("ns::Box<int, 3>")
    ('int', '3')
    >>> split_template_arguments("Box<std::pair<int, int>, 3>")
    ('std::pair<int, int>', '3')
    >>> split_template_arguments("Outer<int>::Inner<double>")
    ('double',)
    >>> split_template_arguments("Box<void (*)(int, int), 3>")
    ('void (*)(int, int)', '3')
    >>> split_template_arguments("Wrapper<(lambda at f.cpp:7:14)>")
    ('(lambda at f.cpp:7:14)',)
    """
    sep = _find_last_top_level_scope_separator(name)
    leaf = name[sep + 2 :] if sep is not None else name
    open_idx = leaf.find("<")
    if open_idx == -1:
        return None
    stack: list[str] = []
    close_idx: int | None = None
    i = open_idx
    n = len(leaf)
    while i < n:
        ch = leaf[i]
        if ch in _OPEN_TO_CLOSE:
            stack.append(ch)
        elif ch in _CLOSERS:
            if stack:
                stack.pop()
            if not stack:
                close_idx = i
                break
        i += 1
    if close_idx is None:
        # Malformed/unterminated -- degrade to "not a template" rather than
        # guess at a truncated argument list.
        return None
    return _split_top_level_commas(leaf[open_idx + 1 : close_idx])
