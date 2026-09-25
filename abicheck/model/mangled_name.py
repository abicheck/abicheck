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

"""Itanium and MSVC mangled-name scope-component parsing (ADR-061 D1, Phase
2's "fourth, pre-existing tension" closure).

Split out of ``diff_cxx_rules.py`` (a whole-file ``compare``-classified
module) because this parsing chain -- :func:`itanium_scope_components` and
the private helpers it's built from, plus :func:`msvc_scope_components` --
is pure string decoding with no I/O and no dependency on ``model``'s own
entity types, needed by ``extract`` (``dumper_clang_expr.py``'s own scope
recovery, ``dumper_hybrid.py``'s CastXML/clang contract reconciliation) and
by ``buildsource``'s own extract-destined modules (``ctor_export_match.py``'s
export-table rescue, ``virtual_dispatch_graph.py``'s vtable-owner recovery)
as well as by ``compare``'s ``diff_cxx_rules.py`` itself (``owner_class_of``'s
mangled-name fallback) -- the same shared-leaf shape
``qualified_name_segments.py``, ``binary_naming.py`` and ``cc_attributes.py``
already have, since ``extract`` may not import ``compare``.
:func:`msvc_scope_components` joined :func:`itanium_scope_components` here
later than the rest of this module (the Itanium half moved first) --
``diff_cxx_rules.py`` re-exports every name here by value for back-compat.
"""

from __future__ import annotations

import re

from .mangled_name_template_args import (
    read_length_prefixed_name as _read_length_prefixed_name,
    skip_template_args as _skip_template_args,
)

_ASCII_DIGITS = "0123456789"

# Matches the `[abi:tag]` suffix `_parse_source_name_component` (and
# `mangled_name_template_args._collect_nested_name_candidates`) attach to a
# GNU-ABI-tagged (`__attribute__((abi_tag(...)))`) name's own bare spelling.
# Only `itanium_special_name_owner_identifiers` strips this back off (see its
# own docstring) -- `itanium_special_name_owner_scope_components` and every
# other identity-oriented caller of the shared component parser must keep the
# tag, so this pattern is deliberately not applied inside the parser itself.
_ABI_TAG_SUFFIX_RE = re.compile(r"\[abi:[^\]]*\]")


def _strip_abi_tag_suffixes(name: str) -> str:
    """Remove every ``[abi:tag]`` marker from *name*, e.g. turning
    ``"C[abi:tag]"`` back into the model's own untagged spelling ``"C"``."""
    return _ABI_TAG_SUFFIX_RE.sub("", name)


# Fixed Itanium operator-function codes (a leaf, like a source-name). Used so
# operator overloads group (e.g. `operator[](int)` / `operator[](long)` both
# `ix`). Deliberately excludes `cv` (conversion-to-T — carries a type and is not
# an overload of other conversions) and variable forms (`li` literal, vendor).
_ITANIUM_OPERATORS = frozenset(
    {
        "nw",
        "na",
        "dl",
        "da",
        "ng",
        "ad",
        "de",
        "co",
        "pl",
        "mi",
        "ml",
        "dv",
        "rm",
        "an",
        "or",
        "eo",
        "aS",
        "pL",
        "mI",
        "mL",
        "dV",
        "rM",
        "aN",
        "oR",
        "eO",
        "ls",
        "rs",
        "lS",
        "rS",
        "eq",
        "ne",
        "lt",
        "gt",
        "le",
        "ge",
        "ss",
        "nt",
        "aa",
        "oo",
        "pp",
        "mm",
        "cm",
        "pm",
        "pt",
        "cl",
        "ix",
        "qu",
        "aw",
    }
)


def strip_macho_itanium_decoration(mangled: str) -> str:
    """Strip Darwin/Mach-O's own linker leading-underscore decoration from
    an otherwise-real Itanium mangled name, when doing so is unambiguous.

    Darwin's linker prepends one leading underscore to *every* global C/C++
    symbol at the object-file level. For a genuine Itanium mangled name --
    which always starts with a single ``"_Z"`` -- that decoration produces
    an unmistakable ``"__Z..."`` shape: no real Itanium mangling ever
    begins with two underscores, so recognizing and stripping this one is
    always safe and needs no platform/target-triple confirmation at all
    (unlike a bare, singly-underscore-prefixed C-linkage name, which IS
    genuinely ambiguous with a real, distinct ``asm("_foo")`` label --
    callers that also know the declaration is genuinely ``extern "C"``
    resolve that narrower case themselves; see
    ``extract.headers.clang.context.strip_darwin_itanium_decoration``).

    **Known gap, carried here from the two `buildsource` graph copies this
    replaced** (their own self-review round, fresh evidence): the "always
    safe" claim above holds for a *compiler-produced* mangled name, not for
    an explicit GNU ``asm("__Zfake")`` label -- clang reports that literal
    spelling verbatim on any platform, confirmed empirically, and stripping
    it corrupts that decl's identity. A caller that joins against a symbol
    table should try the exact spelling first and fall back to this strip
    (``buildsource.template_graph._resolve_emitted_symbol`` does);
    ``call_graph``/``type_graph``'s own joins still call this
    unconditionally, which is a real, separately-scoped gap in those joins
    rather than in this function.

    Returns *mangled* unchanged for every other shape. The single canonical
    home for this specific structural check -- previously duplicated,
    independently, by :func:`_itanium_strip_prefix` below,
    ``extract.headers.clang.context.strip_darwin_itanium_decoration``, and
    ``dumper_hybrid._macho_normalize_mangled`` -- so a Mach-O-target
    identity fix made in one no longer needs to be separately rediscovered
    and reapplied in the other two.
    """
    return mangled[1:] if mangled.startswith("__Z") else mangled


def _itanium_strip_prefix(mangled: str) -> tuple[str, bool] | None:
    """Strip ``_Z`` and optional nested-name prefix from a mangled symbol.

    Returns ``(body, is_nested)`` where *body* is the string starting at the
    first component and *is_nested* indicates whether a ``N`` nested-name
    wrapper was opened (and must be closed by ``E``). Returns ``None`` when
    the symbol does not carry the ``_Z`` Itanium prefix.

    A Mach-O direct-clang mangled name carries an extra platform leading
    underscore (Codex review, fresh evidence: confirmed via
    ``dumper_clang.py``'s own ``_visibility()`` docstring — clang's
    ``mangledName`` is ``"__ZN3lib3addEii"`` on macOS, not the plain
    Itanium ``"_ZN3lib3addEii"``), so a bare ``mangled.startswith("_Z")``
    check rejects every symbol on that platform. Normalized away here
    (via :func:`strip_macho_itanium_decoration`) before the check, mirroring
    ``dumper_clang.py``'s own ``_symbol_candidates()`` de-prefixing
    approach for the identical Mach-O quirk.
    """
    mangled = strip_macho_itanium_decoration(mangled)
    if not mangled.startswith("_Z"):
        return None
    s = mangled[2:]
    nested = s.startswith("N")
    if nested:
        s = s[1:]
        # Skip CV-qualifiers (r/V/K) and ref-qualifiers (R/O) on the implicit
        # object parameter, e.g. NK… (const), NR… (lvalue &), NO… (rvalue &&).
        while s[:1] in ("r", "V", "K", "R", "O"):
            s = s[1:]
    return s, nested


def _parse_source_name_component(
    s: str, i: int
) -> tuple[str | None, int, bool, str | None]:
    """Parse a length-prefixed source-name component (with optional template args
    and GNU ABI tags) starting at ``s[i]``.

    Returns ``(name, next_index, template_attached, bare_name)`` where *name*
    includes any directly-attached ``I…E`` template-argument list and
    ``B<tag>`` GNU ABI tags, and *template_attached* is ``True`` exactly when
    a template-argument list was consumed -- tracked structurally here, at
    the one place that ever attaches one, rather than left for a caller to
    guess back out of the assembled *name* text. Guessing from text is
    unsound: ``component_embeds_template_args()``'s own text-based heuristic
    (kept for the qualified-name/header-tier-fallback shape, which has no
    parser to ask) misreads an ordinary identifier like ``"ICE"`` or
    ``"IWidgetE"`` as a balanced ``I...E`` template block purely by
    coincidental spelling (Codex review, fresh evidence) -- a real false
    positive this structural flag exists to avoid for the one shape (a real
    Itanium mangling) that has a parser available to answer the question
    exactly. *bare_name* is *name* with any directly-attached template-arg
    text stripped back off (identical to *name* when *template_attached* is
    ``False``) -- added so a caller building a *type* candidate (as opposed
    to a signature-identity string, which wants the raw template text kept
    distinct) can recover the component's own plain spelling without
    re-guessing the split point from the assembled text (surface.py's
    ``itanium_special_name_owner_identifiers`` review: a namespace-path
    component's own bare name must never be conflated with names embedded in
    a *different* component's template-argument list). Returns
    ``(None, i, False, None)`` on any parse failure.
    """
    name, i = _read_length_prefixed_name(s, i)
    if name is None:
        return None, i, False, None
    n = len(s)
    # GNU ABI tags (`B<source-name>`, e.g. the libstdc++ `cxx11` tag, or a
    # user `__attribute__((abi_tag(...)))`) attach to the unqualified name
    # itself and are mangled *before* any template-argument list — verified
    # against a real compiled `template <typename T> struct
    # __attribute__((abi_tag("tag"))) C { C(); };` instantiated as `C<int>`:
    # `nm`/`c++filt` show `_ZN1CB3tagIiEC1Ev` (Codex review, fresh
    # evidence) -- name "C", then tag "B3tag", then template-args "IiE",
    # not the reverse. Checking template-args first (the previous order)
    # left a real ABI-tagged class template's own "IiE" unconsumed after
    # the tag loop only found "B3tag" first, which made every caller of
    # this component parser -- including :func:`itanium_scope_components`
    # and :func:`itanium_ctor_dtor_marker_span` -- fail outright on this
    # real, non-synthetic case instead of just mis-grouping it.
    while i < n and s[i] == "B":
        tag, j = _read_length_prefixed_name(s, i + 1)
        if tag is None:
            break
        # Delimited as "[abi:tag]" -- not the raw "B<tag>" the mangling
        # itself uses -- so a flattened identity can't collide with an
        # unrelated, plainly-spelled class merely starting with the same
        # letters (Codex review, fresh evidence): `C[abi_tag("tag")]<int>`
        # (mangled ...CB3tagIiE...) and a class literally named `CBtag<int>`
        # (mangled ...CBtagIiE...) both flattened to the identical
        # "CBtagIiE" before this fix, confirmed against two real compiled
        # symbols -- `_ZN1CB3tagIiE1fEv` vs. `_ZN5CBtagIiE1fEv`, genuinely
        # different classes' own `f()`. No real C++ identifier can contain
        # `[`/`:`/`]`, so this delimiter can never collide with a real name.
        name = f"{name}[abi:{tag}]"
        i = j
    # A directly-attached template-argument list belongs to this
    # component; keep it raw so Box<int> and Box<float> stay distinct.
    bare_name = name
    if i < n and s[i] == "I":
        end = _skip_template_args(s, i)
        if end is None:
            return None, i, False, None
        name = name + s[i:end]
        i = end
        return name, i, True, bare_name
    return name, i, False, bare_name


#: Internal marker appended by :func:`_parse_non_source_name_component` to an
#: inheriting-constructor label so :func:`_step_next_component` knows to stop
#: without a second parse. Stripped before the label is ever returned.
_INHERITED_CTOR_TERMINAL = "\x00inherited"


def _parse_ctor_dtor_component(s: str, i: int) -> tuple[str | None, int]:
    """Parse a constructor (``C1``/``C2``/…) or destructor (``D0``/``D1``/…) at ``s[i]``.

    Returns ``("{ctor}", i+2)``, ``("{dtor}", i+2)``, or ``(None, i)`` if
    ``s[i:]`` does not start a ctor/dtor encoding.
    """
    c = s[i] if i < len(s) else ""
    next_char = s[i + 1] if i + 1 < len(s) else ""
    if c == "C" and next_char in "12345":
        return "{ctor}", i + 2
    if c == "D" and next_char in "012345":
        return "{dtor}", i + 2
    return None, i


def _parse_inherited_ctor_component(s: str, i: int) -> tuple[str | None, int]:
    """Parse an *inherited* constructor (``CI1``/``CI2``/...) at ``s[i]``.

    C++11 inheriting constructors (``using Base::Base;``) mangle as
    ``CI<n><base-type>`` (Itanium ABI 5.1.4.3) -- confirmed against a real
    GCC 13 build of ``struct Derived : Base { using Base::Base; };``, which
    emits ``_ZN3api7DerivedCI1NS_4BaseEEi`` and ``...CI2...``. The owner of
    such a symbol is the **derived** class (``api::Derived``), the enclosing
    nested-name scope -- not the base class named by the ``<base-type>``
    production that follows the code, and not whatever a demangler prints
    first.

    Returns ``("{ctor}", i + 3)`` or ``(None, i)``. The caller stops parsing
    immediately after this component (see :func:`_step_next_component`): what
    follows is an arbitrary Itanium ``<type>`` encoding -- here a
    substitution, ``NS_4BaseE`` -- which this structural parser deliberately
    does not implement, exactly as it declines to parse a conversion
    operator's target type. Stopping is sound because an inheriting
    constructor is always its own nested name's leaf component.

    Deliberately **not** folded into :func:`_parse_ctor_dtor_component`:
    that function's 2-character span is what
    ``diff_cxx_rules.itanium_ctor_dtor_marker_span`` returns to callers that
    derive a *sibling* mangling by rewriting the marker in place
    (``buildsource.template_graph._ctor_dtor_symbol_variants``). ``CI1`` is
    three characters and its trailing base-type encoding is part of the
    name, so admitting it there would hand those callers a span that
    produces a corrupt symbol. They keep declining the form; only scope
    recovery, which needs no rewrite, gains it.
    """
    if s[i : i + 2] != "CI":
        return None, i
    if s[i + 2 : i + 3] not in tuple("12345"):
        return None, i
    return "{ctor}", i + 3


def _parse_operator_component(s: str, i: int) -> tuple[str | None, int]:
    """Parse an Itanium operator-function code at ``s[i]``.

    Returns ``("{op:XX}", i+2)`` for a known 2-char operator code,
    ``("{op:cv:<raw remainder>}", len(s))`` for a conversion operator (see
    below), or ``(None, i)`` if ``s[i:i+2]`` is not a recognized operator
    code.

    A conversion operator's own Itanium code, ``cv``, is deliberately kept
    out of ``_ITANIUM_OPERATORS`` for *signature-identity* purposes (a
    fixed 2-char code there is used so operator overloads group together,
    but every conversion operator carries a different target type and is
    never an overload of another one) — handled separately here instead of
    folded into that set, since it needs different treatment for *scope
    recovery*: a direct-clang snapshot stores a conversion operator's own
    ``Function.name`` bare (e.g. ``"operator Bar"``, confirmed via a real
    ``clang -ast-dump``, no owning-class prefix at all — the same
    unqualified-leaf convention CastXML uses for ordinary methods), so
    ``owner_class_of()``'s mangled-name fallback is the only way to
    recover the owner, and it previously failed outright here (Codex
    review, fresh evidence): ``cv`` is immediately followed by the full
    Itanium encoding of the conversion's target type (e.g.
    ``cvN2ns3BarE`` for ``operator ns::Bar()``), which is not a simple
    length-prefixed name — parsing an arbitrary Itanium ``<type>``
    production (builtin codes, pointers, nested names, substitutions, ...)
    is a much larger grammar than this structural parser attempts
    elsewhere. Recovering the *scope* doesn't need the target type parsed
    at all: ``cv`` is always this member's own leaf component (a
    conversion operator can't itself enclose further nested-name
    components), so it is safe to stop parsing immediately after
    recognizing it — see the ``done`` override in
    :func:`_step_next_component` — rather than attempt (and risk
    mis-parsing) the type that follows.

    The leaf *label* embeds the raw, unparsed remainder of the mangled
    string after ``cv`` rather than a fixed placeholder (Codex review,
    fresh evidence): ``diff_types._overload_group_key()`` uses
    ``itanium_qualified_name()`` — which chains this label onto the scope
    prefix — to decide whether two declarations are genuine overloads of
    one another. A fixed placeholder made every conversion operator on a
    class produce the *same* qualified name regardless of target (e.g.
    both ``operator int()`` and ``operator double()`` on the same class
    reduced to ``"Foo::{op:cv}"``), which collapsed two conversion
    operators that are never overloads of each other (each is a distinct,
    unambiguous conversion function — there is no shared ``&Foo::operator
    T`` to become ambiguous) into one group, producing a false
    ``OVERLOAD_ADDED`` — confirmed empirically:
    ``_diff_overload_additions()`` fired for exactly this case before this
    fix. The target type's mangled encoding is itself deterministic (the
    same target always mangles identically, distinct targets always
    mangle differently), so embedding the raw, un-decoded remainder
    verbatim is sufficient to keep distinct targets in distinct groups and
    identical targets in the same group, without needing to parse the
    arbitrary ``<type>`` grammar it contains. Advances ``i`` to
    ``len(s)`` (nothing meaningful follows for this parser's purposes
    anyway, since :func:`_step_next_component` always stops immediately
    after a ``cv`` component regardless of nesting).
    """
    code = s[i : i + 2]
    if code == "cv":
        return f"{{op:cv:{s[i + 2 :]}}}", len(s)
    if code in _ITANIUM_OPERATORS:
        # Keep the code so operator overloads group (e.g. operator[](int)/(long))
        # while distinct operators stay distinct. Conversion operators (`cv`) are
        # excluded — they carry a target type and are not overloads of each other.
        return f"{{op:{code}}}", i + 2
    return None, i


def _parse_non_source_name_component(s: str, i: int) -> tuple[str | None, int]:
    """Parse a constructor, destructor, or operator component at ``s[i]``.

    Tries ctor/dtor first, then operator codes. Returns ``(label, next_index)``
    or ``(None, i)`` when none of those forms match (e.g. conversion operator,
    substitution, vendor encoding — caller should return ``None``).
    """
    label, new_i = _parse_ctor_dtor_component(s, i)
    if label is None:
        label, new_i = _parse_inherited_ctor_component(s, i)
        if label is not None:
            # Marked terminal for the caller the same way a conversion
            # operator is: an arbitrary <type> encoding follows.
            return f"{label}{_INHERITED_CTOR_TERMINAL}", new_i
    if label is None:
        label, new_i = _parse_operator_component(s, i)
    return label, new_i


def _step_next_component(
    s: str, i: int, nested: bool
) -> tuple[str | None, int, bool, bool, str | None] | None:
    """Advance one component in the Itanium nested-name body ``s`` at position ``i``.

    Returns ``(label, next_i, done, template_attached, bare_name)`` on
    success:

    - *label* is the parsed component string, or ``None`` when the position
      holds the nested-name ``E`` terminator (no component to append, just stop).
    - *next_i* is the index to continue from.
    - *done* is ``True`` when the caller should stop iterating (``E`` reached
      for a nested name, or a free-function's single component was consumed).
    - *template_attached* is ``True`` exactly when this step consumed a
      directly-attached template-argument list -- structurally known only for
      a source-name component (see :func:`_parse_source_name_component`);
      always ``False`` for a ctor/dtor/operator component, none of which can
      carry one.
    - *bare_name* is *label* with any directly-attached template-arg text
      stripped back off (equal to *label* when *template_attached* is
      ``False``) -- see :func:`_parse_source_name_component`'s own docstring
      for why a caller building type candidates wants this separately from
      the raw, template-inclusive *label*.

    Returns ``None`` (not a 5-tuple) when the component cannot be parsed at all
    (an unrecognized/vendor operator, substitution, truncated source name) so
    the caller propagates failure by returning ``None`` from its own scope.
    """
    c = s[i]
    if nested and c == "E":
        # Normal terminator of the ``N…E`` nested-name wrapper; no component.
        return None, i + 1, True, False, None
    if c in _ASCII_DIGITS:
        name, new_i, template_attached, bare_name = _parse_source_name_component(s, i)
        if name is None:
            return None  # malformed source name — propagate failure
        return name, new_i, not nested, template_attached, bare_name
    label, new_i = _parse_non_source_name_component(s, i)
    if label is None:
        return None  # conversion operator / substitution / vendor — not modelled
    if label.endswith(_INHERITED_CTOR_TERMINAL):
        # An inheriting constructor's own <base-type> encoding follows and is
        # deliberately not parsed (see _parse_inherited_ctor_component); the
        # component is always the nested name's leaf, so stop here. The label
        # is normalized back to the plain "{ctor}" every other ctor form
        # produces -- callers classify on the component, not on how it was
        # spelled in the mangling.
        plain = label[: -len(_INHERITED_CTOR_TERMINAL)]
        return plain, new_i, True, False, plain
    if label.startswith("{op:cv:"):
        # A conversion operator's own leaf component is always last; its
        # target type follows immediately and is deliberately not parsed
        # (see _parse_operator_component) so stop right here regardless of
        # nesting, rather than attempt to step into that unparsed type.
        return label, new_i, True, False, label
    return label, new_i, not nested, False, label


def itanium_ctor_dtor_marker_span(mangled: str) -> tuple[int, int] | None:
    """``(start, end)`` indices of *mangled*'s own Itanium ctor/dtor code
    (``C1``/``C2``/``C3``/``D0``/``D1``/``D2``) -- the exact 2-character
    span, structurally located the same length-prefix-aware way
    :func:`itanium_scope_components` walks a nested name, so a class or
    template-argument name that happens to embed the literal substring
    ``"C1"``/``"D1"`` is never mistaken for the real marker: each
    length-prefixed identifier is skipped as one whole unit via
    :func:`_parse_source_name_component`, never scanned character-by-
    character for a coincidental match.

    Exists for a caller that needs to locate, not merely recognize, the
    marker -- e.g. to derive a sibling ctor/dtor mangling (``buildsource.
    template_graph._ctor_dtor_symbol_variants``, Codex review, fresh
    evidence): a naive ``"C1E"`` substring search finds ``C1Evil<int>``'s
    own embedded ``"C1E"`` inside its *class name* first (``_ZN6C1EvilIiE
    C1Ev``), not the real ctor code that follows it, deriving the
    genuinely different class ``C2Evil<int>``'s own real constructor
    mangling by coincidence -- a false positive, not merely a missed one.

    *mangled* need not be pre-normalized for the Mach-O double-underscore
    prefix -- :func:`_itanium_strip_prefix` strips it on its own local
    variable only, never mutating the caller's *mangled*, and this
    function's own offset arithmetic (``offset = len(mangled) -
    len(s)``) is computed against that same untouched *mangled*, so the
    returned span is correct relative to whatever prefix form the caller
    passed in (confirmed empirically: ``__ZN1CC1Ev`` and ``_ZN1CC1Ev``
    both locate the identical ``"C1"`` text within their own respective
    strings).

    Returns ``None`` when *mangled* does not carry a ctor/dtor code this
    parser can locate (a plain function/operator, a non-Itanium or
    unmangled name, or any other form :func:`itanium_scope_components`
    itself does not model)."""
    prefix = _itanium_strip_prefix(mangled)
    if prefix is None:
        return None
    s, nested = prefix
    if not nested:
        return None  # a free function's own single component is never a ctor/dtor
    offset = len(mangled) - len(s)
    i = 0
    n = len(s)
    if s[i : i + 2] == "St":
        i += 2
    while i < n:
        c = s[i]
        if c == "E":
            return None  # nested name closed with no ctor/dtor component found
        if c in _ASCII_DIGITS:
            _name, new_i, _template_attached, _bare_name = _parse_source_name_component(
                s, i
            )
            if new_i == i:
                return None  # malformed source name
            i = new_i
            continue
        label, new_i = _parse_ctor_dtor_component(s, i)
        if label is not None:
            return offset + i, offset + new_i
        return None  # an operator or other non-source-name, non-ctor/dtor form
    return None


def itanium_scope_components_with_template_positions(
    mangled: str,
) -> tuple[list[str], frozenset[int]] | None:
    """Like :func:`itanium_scope_components`, but also reports exactly which
    component indices carry a directly-attached template-argument list.

    Tracked structurally at parse time -- from :func:`_step_next_component`'s
    (in turn :func:`_parse_source_name_component`'s) own knowledge of
    whether it consumed an ``I…E`` block -- rather than guessed back out of
    the assembled component text by a caller. See
    :func:`_parse_source_name_component`'s own docstring for why the
    structural signal matters: a text-based guess (``component_embeds_
    template_args()``, kept for the qualified-name/header-tier-fallback
    shape, which has no parser to ask) misreads an ordinary identifier like
    ``"ICE"`` as a balanced template block purely by coincidental spelling.

    Returns ``None`` under the identical conditions
    :func:`itanium_scope_components` does (see its own docstring); the
    ``list[str]`` half of a successful result is byte-for-byte the same
    list that function returns for the same input.
    """
    prefix = _itanium_strip_prefix(mangled)
    if prefix is None:
        return None
    s, nested = prefix
    components: list[str] = []
    template_positions: set[int] = set()
    i = 0
    n = len(s)
    if s[i : i + 2] == "St":
        components.append("std")
        i += 2
    while i < n:
        step = _step_next_component(s, i, nested)
        if step is None:
            return None  # unmodelled or malformed component
        label, i, done, template_attached, _bare_name = step
        if label is not None:
            if template_attached:
                template_positions.add(len(components))
            components.append(label)
        if done:
            break
    if not components:
        return None
    return components, frozenset(template_positions)


def itanium_name_carries_template_arguments(mangled: str) -> bool | None:
    """Whether *mangled* encodes a template-argument list (``I…E``) on any
    component of its name production -- i.e. whether the entity it names is a
    template specialization/instantiation rather than an ordinary function.

    ``True``/``False`` when the structural parser reached a verdict, ``None``
    when it could not parse the name at all (an unmodelled or malformed
    production, or a non-Itanium spelling). A caller must treat ``None`` as
    "unknown", never as ``False``.

    This exists because the *display* spelling is not a template model. The
    header backends can supply a bare display name (``is_specified``) for an
    entity whose real mangling is ``_Z12is_specifiedI12OptionalBoolEbT_``, so a
    bracket-spotting check over ``Function.name`` -- which is what
    ``buildsource.cross_source_checks._has_export_obligation`` used -- silently
    missed exactly the instantiations it was written to exclude and demanded a
    dynamic export for a function template whose definition is right there in
    the public header. Measured: a consumer that takes the address of
    ``is_specified<OptionalBool>`` compiles, links and runs at ``-O0
    -fno-inline`` under both GCC and Clang with no library present at all,
    because its own translation unit emits the vague-linkage definition; the
    library exporting nothing for it is correct, not a missing export.

    Structural, from the mangling the ABI actually specifies, not from text:
    it reuses :func:`itanium_scope_components_with_template_positions`'s own
    parse-time signal, so an ordinary identifier that merely *looks* like a
    balanced template block (the ``"ICE"`` case that function's docstring
    names) cannot be misread as one, and the answer never varies with whether
    an optional demangler is installed on the host.
    """
    parsed = itanium_scope_components_with_template_positions(mangled)
    if parsed is None:
        return None
    _components, template_positions = parsed
    return bool(template_positions)


def itanium_scope_components(mangled: str) -> list[str] | None:
    """Scope components of an Itanium-mangled C++ symbol, parsed structurally.

    Decoding the nested-name encoding directly avoids any dependency on an
    external demangler (``c++filt`` / ``cxxfilt``), which is not installed on
    every platform — so this works identically on Linux, macOS, and Windows and
    never shells out. Handles the common length-prefixed forms, including
    class-template specializations (the raw template-argument encoding is kept so
    distinct specializations stay distinct)::

        _Z4drawi                       -> ["draw"]                  (free function)
        _ZN1C3barEv                    -> ["C", "bar"]              (member)
        _ZNK1C3barEv                   -> ["C", "bar"]              (const member)
        _ZN3lib12experimental4sortEv   -> ["lib", "experimental", "sort"]
        _ZN3BoxIiE4sizeEv              -> ["BoxIiE", "size"]        (Box<int>::size)
        _ZSt5touchv                    -> ["std", "touch"]          (std::touch(), no wrapper)
        _ZNSt6detail3fooEv             -> ["std", "detail", "foo"]  (std::detail::foo())

    The Itanium ABI mandates the 2-character substitution ``St`` for the
    *first* occurrence of the ``std::`` scope prefix in a mangled name —
    confirmed empirically against two real GCC-compiled symbols:
    ``namespace std { void touch() {} }`` mangles to the bare ``_ZSt5touchv``
    (``St`` directly after ``_Z``, no ``N…E`` nested-name wrapper needed for
    a single trailing component), while ``namespace std { namespace detail {
    void foo() {} } }`` mangles to ``_ZNSt6detail3fooEv`` (``St`` right after
    the ``N`` nested-name marker, with further components following before
    ``E``). Recognized only as the very first component (this parser does
    not attempt general substitution-table resolution for the other
    Itanium substitution abbreviations — ``Sa``/``Sb``/``Ss``/``Si``/``So``/
    ``Sd`` — which stand for a complete template *type*, not a scope prefix
    that can have more components appended, and are irrelevant to "what
    scope is this declaration in").

    Returns ``None`` for forms it does not model (constructors/operators,
    other substitutions, non-Itanium or unmangled names) so callers fall
    back.
    """
    result = itanium_scope_components_with_template_positions(mangled)
    return result[0] if result is not None else None


def msvc_scope_components(mangled: str) -> list[str] | None:
    """Scope components of an MSVC-mangled C++ symbol, parsed structurally.

    Direct-clang snapshots taken with ``clang-cl`` (or any ``--target=
    *-windows-msvc`` invocation) record ``mangledName`` in the proprietary
    Microsoft C++ ABI scheme, not Itanium — confirmed empirically::

        ?run@Foo@@QEAAXXZ            -> ["Foo", "run"]        (Foo::run())
        ?freefunc@ns@@YAXXZ          -> ["ns", "freefunc"]    (ns::freefunc())
        ?method@Box@inner@outer@@... -> ["outer", "inner", "Box", "method"]
        ?instantiate@@YAXXZ          -> ["instantiate"]       (free function)

    The qualified name is written ``<leaf>@<scope1>@<scope2>...@@<type-enc>``
    with scope components listed *innermost first*, terminated by the first
    ``@@`` — the reverse order and terminator convention Itanium uses, so this
    is a genuinely separate parser, not a reuse of ``itanium_scope_components``.

    Returns ``None`` for forms it does not model, mirroring
    ``itanium_scope_components``'s "return None, let the caller fall back"
    contract:

    * Special member functions and operators (constructors ``??0``,
      destructors ``??1``/``??_D``, ``operator=`` ``??4``, ...) all mangle
      with a *second* ``?`` immediately after the first — the "name" slot
      is an operator code, not a plain identifier, so the simple
      leaf/scope split below does not apply.
    * Template classes/functions (``?$Name@Args@``) embed the template
      argument list inside the same ``@``-delimited region as the scope
      chain using the identical separator, and argument encodings can
      themselves be arbitrary nested type strings — a naive split cannot
      tell an argument token from a scope token, so any component
      starting with ``?`` (the template marker ``?$`` or the anonymous-
      namespace marker ``?A``) is rejected rather than mis-parsed.
    * A bare-digit component is a name-backreference into MSVC's
      per-symbol substitution table, not a literal identifier — no real
      C++ identifier is all-digits, so this is an unambiguous signal to
      bail rather than resolve it wrong.
    """
    if not mangled.startswith("?") or mangled[1:2] == "?":
        return None
    idx = mangled.find("@@")
    if idx == -1:
        return None
    head = mangled[1:idx]
    if not head:
        return None
    parts = head.split("@")
    if any(not p or p.startswith("?") or p.isdigit() for p in parts):
        return None
    name = parts[0]
    scope = list(reversed(parts[1:]))
    return [*scope, name]
