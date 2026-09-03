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

_ASCII_DIGITS = "0123456789"

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


def _read_length_prefixed_name(s: str, i: int) -> tuple[str | None, int]:
    """Read a ``<len><identifier>`` source-name at ``s[i]``.

    Returns ``(name, next_index)`` or ``(None, i)`` if malformed. Only ASCII
    digits count as the length prefix — Python's ``str.isdigit()`` also accepts
    Unicode digits (e.g. ``²``) that ``int()`` then rejects. Accumulates
    digit-by-digit, capped at ``len(s)`` (mirrors ``source_link.
    _consume_source_name``), so an untrusted symbol can't trip Python's
    integer-conversion digit limit (Codex review, PR #930)."""
    j, n = i, 0
    while j < len(s) and s[j] in _ASCII_DIGITS:
        n = n * 10 + (ord(s[j]) - ord("0"))
        if n > len(s):
            return None, i
        j += 1
    name = s[j : j + n]
    return (None, i) if j == i or len(name) != n else (name, j + n)


def _skip_template_args(s: str, i: int) -> int | None:
    """``s[i] == 'I'``: return the index past the matching ``E``, or ``None``.

    Tracks nested template-argument (``I``) and nested-name (``N``) openers so
    the inner ``E`` of e.g. ``Box<ns::T>`` does not close the outer list early,
    skips length-prefixed names so their literal ``I``/``N``/``E`` letters are
    not miscounted, and consumes ``L<type><value>E`` literal operands as a unit
    (non-type template args, e.g. ``Array<4>`` → ``ILi4EE``) so their value
    digits aren't read as a length and their closing ``E`` isn't counted.
    Pathological encodings (e.g. substitutions whose base-36 index contains
    ``E``) may mis-balance; the caller treats ``None`` as "unparseable" and falls
    back, so a wrong guess never produces a finding.
    """
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in _ASCII_DIGITS:
            name, i = _read_length_prefixed_name(s, i)
            if name is None:
                return None
            continue
        if c == "L":
            # Literal operand `L <type> <value> E` — consume through its own
            # terminating E (literal values never contain an uppercase E).
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
    check rejects every symbol on that platform. Normalized away here by
    stripping one leading underscore before the check, mirroring
    ``dumper_clang.py``'s own ``_symbol_candidates()`` de-prefixing
    approach for the identical Mach-O quirk.
    """
    if mangled.startswith("__Z"):
        mangled = mangled[1:]
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


def _parse_source_name_component(s: str, i: int) -> tuple[str | None, int, bool]:
    """Parse a length-prefixed source-name component (with optional template args
    and GNU ABI tags) starting at ``s[i]``.

    Returns ``(name, next_index, template_attached)`` where *name* includes
    any directly-attached ``I…E`` template-argument list and ``B<tag>`` GNU
    ABI tags, and *template_attached* is ``True`` exactly when a template-
    argument list was consumed -- tracked structurally here, at the one
    place that ever attaches one, rather than left for a caller to guess
    back out of the assembled *name* text. Guessing from text is unsound:
    ``component_embeds_template_args()``'s own text-based heuristic (kept
    for the qualified-name/header-tier-fallback shape, which has no parser
    to ask) misreads an ordinary identifier like ``"ICE"`` or ``"IWidgetE"``
    as a balanced ``I...E`` template block purely by coincidental spelling
    (Codex review, fresh evidence) -- a real false positive this structural
    flag exists to avoid for the one shape (a real Itanium mangling) that
    has a parser available to answer the question exactly. Returns
    ``(None, i, False)`` on any parse failure.
    """
    name, i = _read_length_prefixed_name(s, i)
    if name is None:
        return None, i, False
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
    if i < n and s[i] == "I":
        end = _skip_template_args(s, i)
        if end is None:
            return None, i, False
        name = name + s[i:end]
        i = end
        return name, i, True
    return name, i, False


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
        label, new_i = _parse_operator_component(s, i)
    return label, new_i


def _step_next_component(
    s: str, i: int, nested: bool
) -> tuple[str | None, int, bool, bool] | None:
    """Advance one component in the Itanium nested-name body ``s`` at position ``i``.

    Returns ``(label, next_i, done, template_attached)`` on success:

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

    Returns ``None`` (not a 4-tuple) when the component cannot be parsed at all
    (an unrecognized/vendor operator, substitution, truncated source name) so
    the caller propagates failure by returning ``None`` from its own scope.
    """
    c = s[i]
    if nested and c == "E":
        # Normal terminator of the ``N…E`` nested-name wrapper; no component.
        return None, i + 1, True, False
    if c in _ASCII_DIGITS:
        name, new_i, template_attached = _parse_source_name_component(s, i)
        if name is None:
            return None  # malformed source name — propagate failure
        return name, new_i, not nested, template_attached
    label, new_i = _parse_non_source_name_component(s, i)
    if label is None:
        return None  # conversion operator / substitution / vendor — not modelled
    if label.startswith("{op:cv:"):
        # A conversion operator's own leaf component is always last; its
        # target type follows immediately and is deliberately not parsed
        # (see _parse_operator_component) so stop right here regardless of
        # nesting, rather than attempt to step into that unparsed type.
        return label, new_i, True, False
    return label, new_i, not nested, False


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
        label, i, done, template_attached = step
        if label is not None:
            if template_attached:
                template_positions.add(len(components))
            components.append(label)
        if done:
            break
    if not components:
        return None
    return components, frozenset(template_positions)


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
