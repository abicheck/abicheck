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

"""C++-specific ABI-rule helpers shared by the symbol/type diff passes.

Kept as a leaf module (depending only on the data model and result types) so
``diff_symbols`` can import it without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .checker_policy import ChangeKind
from .checker_types import Change
from .compare.vtable_evidence import vtable_transition_is_evidenced
from .diff_helpers import make_change
from .model import Fact, Function, RecordType
from .model.availability import FactStatus

# The Itanium/MSVC mangled-name scope-component parsers' real home is
# model/mangled_name.py (ADR-061 D1): pure string decoding with no I/O,
# needed by extract (dumper_clang_expr.py, dumper_hybrid.py) and by
# buildsource's own extract-destined modules (ctor_export_match.py,
# virtual_dispatch_graph.py) as well as by this module's own remaining
# name-derivation helpers below. Re-exported by value for back-compat.
from .model.mangled_name import (
    _ASCII_DIGITS as _ASCII_DIGITS,
    _itanium_strip_prefix as _itanium_strip_prefix,
    _parse_ctor_dtor_component as _parse_ctor_dtor_component,
    _parse_source_name_component as _parse_source_name_component,
    _skip_template_args as _skip_template_args,
    itanium_scope_components as itanium_scope_components,
    itanium_scope_components_with_template_positions as itanium_scope_components_with_template_positions,
    msvc_scope_components as msvc_scope_components,
)

# `model/namespace_spelling.py`'s real home is documented on that module
# itself (ADR-063 Track 2, 5B closure): pure string matching over an
# already-spelled identity, needed here (`virtual_method_addition`'s own
# call into `compare.vtable_evidence.vtable_transition_is_evidenced`) and by
# `type_reachability_spelling.py`, which re-exports it by value for
# back-compat and cannot be imported from here at all (it already imports
# this module at its own top level).
from .model.namespace_spelling import _namespace_suffix_spellings


def itanium_qualified_name(mangled: str) -> str | None:
    """Fully scope-qualified name (``ns::C::bar``) from a mangled symbol, or None."""
    comps = itanium_scope_components(mangled)
    return "::".join(comps) if comps else None


def component_embeds_template_args(component: str) -> bool:
    """Whether *component* -- one entry from
    :func:`qualified_name_scope_components` (a demangled or already
    pretty-printed spelling) -- embeds a template-argument list, rather than
    naming a bare namespace/class/function segment.

    A pretty-printed spelling keeps the human-readable ``<...>`` form (e.g.
    ``"Box<int>"``), so a literal ``<`` anywhere in *component* is a sound,
    exact signal for this shape: no ordinary C++ identifier can contain
    ``<``.

    This is deliberately a TEXT-ONLY heuristic and is NOT used for an
    Itanium-mangled component: :func:`itanium_scope_components` keeps a
    directly-attached template-argument list RAW -- see
    :func:`_parse_source_name_component`'s own docstring, "keep it raw so
    ``Box<int>`` and ``Box<float>`` stay distinct" -- so ``Box<int>`` there
    is the component ``"BoxIiE"``, containing no literal ``<`` at all, and a
    naive scan for a raw ``I...E`` block is unsound: an ordinary identifier
    like ``"ICE"`` or ``"IWidgetE"`` parses as a balanced template-args
    block purely by coincidental spelling (Codex review, fresh evidence --
    this function itself carried exactly that guessing branch until this
    fix, and it produced a real false positive: a genuine namespace move of
    a class spelled e.g. ``ICE`` -> ``ACE`` was silently skipped). The sound
    answer for an Itanium mangling is the STRUCTURAL one,
    :func:`itanium_scope_components_with_template_positions`, which knows
    at parse time -- from :func:`_parse_source_name_component`'s own
    return value -- whether a template-argument list was actually consumed,
    rather than guessing it back out of the assembled text. Every caller of
    *this* function must therefore route an Itanium-mangled component
    through that structural answer instead (see
    :mod:`diff_symbols_renames`'s ``_scope_components``, this function's
    only production consumer).

    Deliberately conservative like every other guard in this module: an
    unrecognized shape returns ``False`` (not template-bearing) rather than
    risking a false positive that would suppress a genuine namespace-move
    finding.
    """
    return "<" in component


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
            _name, new_i, _template_attached = _parse_source_name_component(s, i)
            if new_i == i:
                return None  # malformed source name
            i = new_i
            continue
        label, new_i = _parse_ctor_dtor_component(s, i)
        if label is not None:
            return offset + i, offset + new_i
        return None  # an operator or other non-source-name, non-ctor/dtor form
    return None


def msvc_qualified_name(mangled: str) -> str | None:
    """Fully scope-qualified name (``ns::C::bar``) from an MSVC-mangled symbol, or None."""
    comps = msvc_scope_components(mangled)
    return "::".join(comps) if comps else None


#: Symbol-operator spellings that carry a literal ``<``/``>`` as part of the
#: operator token itself (stream insertion/extraction, relational, C++20
#: three-way comparison) rather than as template-argument delimiters --
#: longest-first so ``"<<="`` matches before the shorter ``"<<"``/``"<"``
#: prefixes it also starts with.
_OPERATOR_ANGLE_TOKENS = ("<<=", ">>=", "<=>", "<<", ">>", "<=", ">=", "<", ">")


def _operator_angle_token_len(qualified: str, i: int) -> int:
    """Length of a symbol-operator angle token at *qualified[i:]* immediately
    following a literal ``"operator"`` (checked by the caller), or 0.

    ``ns::Stream::operator<<`` has no space after ``operator`` -- unlike a
    conversion operator (``operator ns::Bar``), so it never matches the
    ``"::operator "`` marker above -- and its own ``<``/``>`` are the
    operator's spelling, not template-argument delimiters. Without
    recognizing this, the depth tracker below would see an unmatched ``<``
    (or two, for ``operator<<``) with no closing ``>`` anywhere in the
    string, reporting "unbalanced nesting" and rejecting an otherwise
    perfectly ordinary qualified name (Codex/CodeRabbit review, fresh
    evidence).
    """
    for tok in _OPERATOR_ANGLE_TOKENS:
        if qualified.startswith(tok, i):
            return len(tok)
    return 0


def _operator_keyword_precedes(qualified: str, i: int) -> bool:
    """Whether ``qualified[i-8:i] == "operator"`` is the COMPLETE leaf
    token, not merely a suffix of a longer identifier.

    A bare suffix match alone is unsound: ``lib::myoperator<old::A>::f`` also
    ends in the eight characters ``"operator"`` immediately before its ``<``,
    but the real identifier is ``myoperator`` -- a legal (if unusual) class
    name, not an overloaded-operator declaration. Requires the character
    immediately before ``"operator"`` to be a scope/token boundary (start of
    string, or anything that isn't an identifier character) rather than
    assuming one (Codex review, fresh evidence).
    """
    if i < 8:
        return False
    if qualified[i - 8 : i] != "operator":
        return False
    before = i - 8
    return before == 0 or not (
        qualified[before - 1].isalnum() or qualified[before - 1] == "_"
    )


def _is_template_opening_angle(qualified: str, i: int) -> bool:
    """Whether ``qualified[i] == "<"`` is a real template-opening delimiter,
    as opposed to an unparenthesized ``<`` comparison in a dependent
    non-type template argument.

    Unlike ``>`` (see :func:`qualified_name_scope_components`'s own
    docstring -- a bare ``>`` there is a genuine, unparenthesized-forbidden
    compile error), a bare ``<`` comparison is legal C++ and a real
    compiler's parser disambiguates it via name lookup, which this scanner
    has no access to. Falls back to a spacing signal instead, confirmed
    against real clang output: a template-opening ``<`` is always rendered
    immediately after its name with no preceding space, while a binary
    comparison operator is always rendered with a space on both sides
    regardless of the original source's own spacing.
    """
    return i == 0 or not qualified[i - 1].isspace()


#: Multi-character ``<``-led expression-operator tokens (as opposed to a
#: lone ``<``, which needs :func:`_is_template_opening_angle`'s spacing
#: signal). Longest-first so ``<<=`` matches before ``<<``.
_LESS_THAN_LED_OPERATOR_TOKENS = ("<<=", "<=>", "<<", "<=")


def _less_than_led_operator_token_len(qualified: str, i: int) -> int:
    """Length of a multi-character ``<``-led expression-operator token
    (``<<=``, ``<=>``, ``<<``, ``<=``) at ``qualified[i:]``, or 0.

    Unlike a lone ``<`` (see :func:`_is_template_opening_angle`), ANY
    multi-character ``<``-led token is structurally guaranteed to be a
    real operator, never two adjacent template-opening delimiters: a
    template-argument-list can never begin with a bare ``<`` or ``=`` (no
    expression or type-id starts with either), so two consecutive ``<``
    characters, or a ``<`` immediately followed by ``=``, cannot be two
    independent delimiters -- they can only be this operator's own
    spelling. Confirmed against real clang: ``operator B<N << M>``
    compiles and is pretty-printed verbatim for an uninstantiated member
    (Codex review, fresh evidence -- the second ``<`` of ``<<`` is not
    preceded by whitespace, so :func:`_is_template_opening_angle`'s
    per-character spacing signal alone misclassified it as a template
    opener). No whitespace check needed here, unlike the lone-``<`` case:
    the grammar guarantee is unconditional.
    """
    for tok in _LESS_THAN_LED_OPERATOR_TOKENS:
        if qualified.startswith(tok, i):
            return len(tok)
    return 0


def qualified_name_scope_components(qualified: str) -> list[str] | None:
    """Scope components of an already-demangled, ``::``-qualified name.

    A structural counterpart to :func:`itanium_scope_components`/
    :func:`msvc_scope_components` for callers that hold a plain qualified
    spelling rather than a mangled symbol — e.g. a header-tier snapshot key
    that was never mangled at all (a synthesized constructor/destructor
    identity, a plain-C fallback name) but is already scope-qualified text::

        "ns::Class::method" -> ["ns", "Class", "method"]
        "Class::method"     -> ["Class", "method"]
        "freefunc"          -> ["freefunc"]              (no scope to split)

    Splits only at TOP-LEVEL ``"::"`` — bracket/paren nesting depth is
    tracked (mirroring ``clang_layout_tool._bare_base_name``'s identical
    concern) so a template argument's own ``"::"`` is never mistaken for a
    scope separator. Without this, ``"lib::foo<old::A>"`` would split into
    ``["lib", "foo<old", "A>"]`` — the fabricated middle component
    ``"foo<old"`` can then coincidentally collide with an unrelated
    ``"foo<new"`` from a different instantiation, producing a false
    namespace-move grouping between two type arguments that were never
    renamed at all (Codex review, fresh evidence: exactly this happened for
    ``lib::foo<old::A>``/``lib::foo<old::B>`` vs.
    ``lib::foo<new::A>``/``lib::foo<new::B>``, reported as a spurious
    BREAKING ``symbol_renamed_batch``)::

        "lib::foo<old::A>" -> ["lib", "foo<old::A>"]   (not ["lib", "foo<old", "A>"])

    A conversion operator's own target type can itself carry ``"::"``
    (``"api::C::operator old::X"`` for `operator old::X()`) — without special
    handling, the target's own scope separator would be treated as an
    enclosing-scope boundary too, splitting into
    ``["api", "C", "operator old", "X"]`` instead of the correct
    ``["api", "C", "operator old::X"]``. That fabricated middle component
    can then collide with an unrelated target sharing the same "operator
    <prefix>" spelling, producing a false namespace-move grouping (Codex
    review, fresh evidence — mirrors the identical concern
    :func:`owner_class_of` already documents for exactly this shape).
    Recognized the same way that function already does: a top-level
    ``"::operator "`` marker is the true scope/leaf boundary, and
    everything from ``"operator "`` onward (including the target's own
    ``"::"``) is kept as ONE opaque leaf component, never split further.

    Deliberately conservative: returns ``None`` for an empty string, a
    component list with any empty segment (a leading/trailing/doubled
    top-level ``"::"``, e.g. ``"::foo"`` or ``"foo::::bar"``), or unbalanced
    bracket/paren nesting, rather than silently dropping or fabricating a
    component, mirroring the "return ``None``, let the caller fall back"
    contract the mangled-name parsers above use. That conservatism applies
    to the WHOLE string, including a conversion operator's own target past
    the ``"::operator "`` marker -- the scan below keeps tracking depth
    through the opaque leaf and rejects the whole input if it ends
    unbalanced, rather than stopping at the marker and silently accepting a
    malformed target like ``"api::C::operator old::X<"`` (CodeRabbit
    review, fresh evidence: the earlier revision broke out of the loop the
    moment the marker was found, so nothing past it was ever validated).

    Angle-bracket (``<``/``>``) and paren (``(``/``)``) nesting are tracked
    as two INDEPENDENT counters, not one shared ``depth`` -- a real,
    demangled non-type template argument can legitimately contain a bare
    ``<``/``>`` comparison, e.g. ``operator
    std::integral_constant<bool, (sizeof(T) > 1)>`` for
    ``std::integral_constant<bool, (sizeof(T) > 1)>`` (Codex review, fresh
    evidence: an earlier revision used one shared counter for both bracket
    kinds, so the comparison's ``>`` was miscounted as closing the
    ``integral_constant<`` template, driving the counter negative and
    rejecting a perfectly well-formed target). This is not a heuristic: the
    C++ grammar itself requires such a comparison to be parenthesized
    wherever it appears as a non-type template argument, specifically to
    remove this exact ambiguity for any parser -- so a compiler's own
    demangled/pretty-printed text is guaranteed to already carry the
    disambiguating parens around it. A ``>`` character can therefore only be
    a REAL template-closing delimiter while no paren is currently open
    (``paren_depth == 0``); while a paren is open, it is guaranteed by that
    same grammar rule to be part of an expression, never a delimiter, so it
    is left untouched rather than folded into a bracket-kind-blind counter.

    That grammar guarantee is ONE-SIDED, though: only a ``>``-bearing
    expression must be parenthesized as a non-type template argument (a
    bare, unparenthesized ``>`` there is a genuine, confirmed compile
    error) -- an unparenthesized ``<`` comparison is perfectly legal and
    unambiguous to a real parser, which disambiguates it via *name lookup*
    (is the identifier immediately to its left a known template name?), not
    via any textual rule this scanner could replicate. Confirmed directly
    against real clang: ``template<int N, int M> struct C { operator
    B<N < M>() const; };`` compiles cleanly, and clang's own AST dump
    prints the *unparenthesized* comparison verbatim as ``operator B<N <
    M>`` for the uninstantiated (template-parameter-dependent) member --
    exactly the shape :func:`qualified_name_scope_components` receives from
    this codebase's own castxml/clang-derived declaration names (Codex
    review, fresh evidence: an earlier revision treated every ``<`` at
    ``paren_depth == 0`` as a real template opener unconditionally, so this
    exact input drove ``angle_depth`` one too high and never came back
    down, rejecting valid input). Since a `<` cannot be soundly resolved by
    grammar alone, a ``<`` at ``paren_depth == 0`` is instead treated as a
    real template-opening delimiter only when it is NOT preceded by
    whitespace -- confirmed, empirically, against real clang output: a
    template-opening ``<`` is always rendered immediately after its name
    with no space (``Other<D>``, ``B<N < M>``'s own leading ``<``), while a
    binary comparison operator is always rendered with a space on both
    sides (``N < M``) REGARDLESS of the source's own spacing (confirmed by
    compiling the identical construct spelled ``N<M`` with no spaces at
    all -- clang's pretty-printer still re-inserts them). This is not
    airtight for arbitrary hand-crafted text, but it is sound for every
    real input this function actually receives, which originates from a
    compiler's own canonical printer, never from hand-written source.

    Brace (``{``/``}``) nesting is tracked as a THIRD independent counter,
    for a different reason than the ``<``/``>``/``(``/``)`` cases above:
    C++20 allows a captureless lambda closure as a non-type template
    argument, and its body is a full, self-contained statement grammar --
    a ``>``/``<`` inside it is not required to be parenthesized the way a
    bare comparison directly in the template-argument-list is, because it
    is not at that grammar production at all. Confirmed directly against
    real clang: ``operator B<[]{ return N > M; }>()`` (a lambda-typed
    conversion target) compiles under ``-std=c++20`` and is pretty-printed
    verbatim, unparenthesized comparison included, sometimes spanning
    multiple lines (Codex review, fresh evidence). Unlike the angle-bracket
    cases, this needs no heuristic at all: braces always balance
    unconditionally in valid C++ (no ambiguity like ``<``/``>`` ever
    applies to them), so once a brace opens, every character up to its
    matching close -- regardless of what it looks like -- is treated as
    fully opaque interior, untouched by any other counter in this
    function.

    Bracket (``[``/``]``) nesting is tracked as a FOURTH independent
    counter, for the same reason: a subscript expression used inside a
    non-type template argument (e.g. ``operator B<A[N > M]>()``, confirmed
    to compile) carries a ``>`` that needs no parenthesization either --
    ``]``, not ``>``, closes the subscript, so it carries none of the
    top-level template-argument ambiguity a bare ``>`` would (Codex review,
    fresh evidence). Treated exactly like a brace: once a bracket opens,
    its entire interior is opaque, and this needs no heuristic either,
    since ``[``/``]`` always balance unconditionally in valid C++ too.

    A lambda's trailing-return-type arrow (``[]() -> bool { ... }``,
    confirmed to compile and pretty-print verbatim as a non-type template
    argument) needs its own check, unrelated to brace/bracket tracking:
    the ``->`` sits in the lambda's OWN declarator, between its parameter
    list and its body, so it is not inside any brace/bracket this function
    already tracks as opaque. Unlike every other ``>`` case above, this
    one needs no heuristic and no depth-awareness at all: by the C++
    lexical grammar's own maximal-munch rule, a ``-`` immediately adjacent
    to a ``>`` can ONLY ever tokenize as the single ``->`` token, never as
    two separate ``-`` and ``>`` tokens -- if the source meant a
    subtraction immediately followed by a separate closing ``>`` with
    zero characters between them, the compiler's own lexer would already
    have misread that as ``->`` too, so this exact adjacency cannot
    represent two separate tokens in any valid, compiled C++ program
    (Codex review, fresh evidence). A ``>`` immediately preceded by ``-``
    is therefore always skipped as part of ``->``, unconditionally.

    Known, accepted limitation (Codex review, fresh evidence): the brace/
    bracket "opaque interior" scan above is a raw character count, not a
    real tokenizer -- it does not skip over string/char-literal content or
    comments, so a brace, bracket, paren, or angle-bracket CHARACTER
    embedded inside a string literal within a lambda body (e.g. ``operator
    B<[]{ return sizeof("}"); }>()``, confirmed to compile and to be
    pretty-printed verbatim by clang) desynchronizes the corresponding
    counter and this function rejects otherwise-valid input. Closing this
    for real needs an actual lexical scanner for the brace/bracket
    interior -- string/char-literal quoting and escape-sequence handling
    (including raw string literals, ``R"delim(...)delim"``, whose
    terminator is itself data-dependent), plus line (``//``) and block
    (``/* */``) comment recognition -- which is a materially different,
    larger piece of work than "track one more independently-balancing
    bracket kind" (the pattern every fix in this function's history above
    has been). Deliberately not attempted here: this is the sixth
    consecutive real-but-increasingly-exotic C++ grammar shape found in
    this function across as many review rounds, and a string/char literal
    containing bracket-like characters *inside a lambda body used as a
    conversion-operator's own non-type template argument* is deep into
    adversarially-constructed territory -- vanishingly unlikely to appear
    in any real-world header this tool would actually be pointed at,
    unlike every shape fixed above (each was a plain, if less common,
    construct a real codebase could plausibly contain). Per this
    codebase's own "known gaps over risky reactive patches" convention:
    the input this function was built to defend against in the first
    place is a genuinely MALFORMED synthetic key, and no valid, real-world
    header-tier declaration this codebase has ever actually needed to
    parse has required this. A caller reaching this gap gets the existing,
    documented conservative fallback (``None``, no namespace-move pairing
    for that one declaration) -- a missed roll-up for one input, not a
    wrong one.
    """
    if not qualified:
        return None
    marker = "::operator "
    angle_depth = 0
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    i = 0
    n = len(qualified)
    marker_idx = -1
    while i < n:
        ch = qualified[i]
        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                return None
            i += 1
            continue
        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return None
            i += 1
            continue
        if brace_depth > 0 or bracket_depth > 0:
            # Opaque interior of a brace-delimited lambda body (a legal
            # C++20 non-type template argument, e.g. "B<[]{ return N > M;
            # }>") or a bracketed subscript expression (e.g. "B<A[N >
            # M]>", confirmed to compile: a ">" nested inside "[...]" is
            # unambiguous to the parser -- "]", not ">", closes the
            # subscript, so it carries none of the top-level
            # template-argument ambiguity a bare ">" would). Both are a
            # full expression/statement grammar unrelated to the
            # enclosing template-argument-list's own bracket balance. See
            # this function's own docstring for why braces/brackets need
            # no whitespace heuristic, unlike angle brackets.
            i += 1
            continue
        if ch == ">" and i > 0 and qualified[i - 1] == "-":
            # A lambda's trailing-return-type arrow ("[]() -> bool {...}",
            # confirmed to compile as a non-type template argument and be
            # pretty-printed verbatim) -- unlike the other ">" cases, this
            # needs no heuristic or brace/bracket-depth awareness at all:
            # by the C++ lexical grammar's own maximal-munch rule, a "-"
            # character immediately adjacent to a ">" can ONLY ever
            # tokenize as the single "->" token, never as two separate
            # "-" and ">" tokens -- if the source meant a subtraction
            # immediately followed by a separate closing ">" with zero
            # characters between them, the compiler's own lexer would
            # already have misread THAT as "->" too, so this adjacency
            # cannot represent two separate tokens in any valid, compiled
            # C++ program (Codex review, fresh evidence).
            i += 1
            continue
        if ch in "<>" and _operator_keyword_precedes(qualified, i):
            tok_len = _operator_angle_token_len(qualified, i)
            if tok_len:
                i += tok_len
                continue
        if ch == "<":
            lt_tok_len = _less_than_led_operator_token_len(qualified, i)
            if lt_tok_len:
                i += lt_tok_len
                continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth < 0:
                return None
        elif (
            ch == "<" and paren_depth == 0 and _is_template_opening_angle(qualified, i)
        ):
            angle_depth += 1
        elif ch == ">" and paren_depth == 0:
            angle_depth -= 1
            if angle_depth < 0:
                return None
        elif (
            angle_depth == 0
            and paren_depth == 0
            and marker_idx == -1
            and qualified[i : i + len(marker)] == marker
        ):
            marker_idx = i
        i += 1
    if angle_depth != 0 or paren_depth != 0 or brace_depth != 0 or bracket_depth != 0:
        return None
    if marker_idx != -1:
        head = qualified[:marker_idx]
        leaf = qualified[marker_idx + 2 :]  # keep the "operator ..." target whole
        head_comps = qualified_name_scope_components(head)
        if head_comps is None:
            return None
        return [*head_comps, leaf]
    if qualified.startswith("operator "):
        # A bare-recorded conversion operator with no owning-class prefix at
        # all (no "::operator " marker to find) can still carry a qualified
        # target ("operator ns::Bar") whose own "::" is not a scope
        # separator -- the same shape owner_class_of's docstring documents.
        # There is no scope to substitute here regardless, so treat the
        # whole thing as one leaf rather than guessing at a split.
        return [qualified]
    comps: list[str] = []
    angle_depth = 0
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    start = 0
    i = 0
    while i < n:
        ch = qualified[i]
        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                return None
            i += 1
            continue
        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return None
            i += 1
            continue
        if brace_depth > 0 or bracket_depth > 0:
            i += 1
            continue
        if ch == ">" and i > 0 and qualified[i - 1] == "-":
            # A lambda's trailing-return-type arrow -- see this function's
            # own docstring / the sibling scan above for why this needs
            # no heuristic at all.
            i += 1
            continue
        if ch in "<>" and _operator_keyword_precedes(qualified, i):
            tok_len = _operator_angle_token_len(qualified, i)
            if tok_len:
                i += tok_len
                continue
        if ch == "<":
            lt_tok_len = _less_than_led_operator_token_len(qualified, i)
            if lt_tok_len:
                i += lt_tok_len
                continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth < 0:
                return None
        elif (
            ch == "<" and paren_depth == 0 and _is_template_opening_angle(qualified, i)
        ):
            angle_depth += 1
        elif ch == ">" and paren_depth == 0:
            angle_depth -= 1
            if angle_depth < 0:
                return None
        elif angle_depth == 0 and paren_depth == 0 and qualified[i : i + 2] == "::":
            comps.append(qualified[start:i])
            i += 2
            start = i
            continue
        i += 1
    if angle_depth != 0 or paren_depth != 0 or brace_depth != 0 or bracket_depth != 0:
        return None
    comps.append(qualified[start:])
    if any(not c for c in comps):
        return None
    return comps


def strip_trailing_top_level_parameter_list(text: str) -> str:
    """Strip a trailing ``(...)`` parameter list, at TOP-LEVEL template
    nesting only.

    A synthesized constructor key (``__abicheck_ctor__<scope>(<params>)``)
    needs its parameter-list suffix removed before the ``<scope>`` prefix is
    handed to :func:`qualified_name_scope_components` — but a naive
    ``text.find("(")`` matches the FIRST ``(`` anywhere, including one
    belonging to a function-type template argument nested inside the scope
    itself (e.g. ``ns::Holder<void(int)>``), truncating the scope at that
    inner paren instead of the real, top-level parameter list (Codex/
    CodeRabbit review, fresh evidence). Tracks ``<``/``>`` nesting depth —
    mirroring :func:`qualified_name_scope_components`'s own concern — and
    only treats a ``(`` at depth 0 as the parameter list's start::

        "ns::Holder<void(int)>(int)" -> "ns::Holder<void(int)>"
        "ns::graph"                  -> "ns::graph"              (no paren at all)

    Returns *text* unchanged when no top-level ``(`` is found (e.g. unbalanced
    nesting, or genuinely no parameter list) rather than guessing.

    Angle-bracket depth is only tracked while no paren is currently open —
    the identical concern :func:`qualified_name_scope_components` documents
    for the same reason: a non-type template argument can legitimately
    contain a parenthesized ``<``/``>`` comparison (``Holder<(A > B),
    void(int)>``), and the C++ grammar itself guarantees a ``>``-bearing
    comparison must be parenthesized wherever it appears as a template
    argument. A ``>`` seen while a paren is open is therefore guaranteed to
    be part of that expression, never a real template delimiter, so folding
    it into the angle-bracket counter would close the enclosing template
    one character too early and let a later, still-nested ``(`` (a
    function-type template argument's own parameter list, not the real
    trailing one) be mistaken for the top-level split point. That grammar
    guarantee does NOT cover ``<``, though -- an unparenthesized ``<``
    comparison is legal C++ (e.g. a class template's own scope carrying an
    uninstantiated ``Holder<N < M>``), so a ``<`` is only counted as a real
    template opener via :func:`_is_template_opening_angle`'s spacing signal,
    the same one :func:`qualified_name_scope_components` uses.
    """
    depth = 0
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            i += 1
            continue
        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue
        if brace_depth > 0 or bracket_depth > 0:
            # Opaque lambda-body/subscript interior -- see
            # qualified_name_scope_components's identical concern.
            i += 1
            continue
        if ch == ">" and i > 0 and text[i - 1] == "-":
            # A lambda's trailing-return-type arrow -- see
            # qualified_name_scope_components's identical concern.
            i += 1
            continue
        if ch == "<":
            lt_tok_len = _less_than_led_operator_token_len(text, i)
            if lt_tok_len:
                i += lt_tok_len
                continue
        if ch == "(":
            if depth == 0 and paren_depth == 0:
                return text[:i]
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "<" and paren_depth == 0 and _is_template_opening_angle(text, i):
            depth += 1
        elif ch == ">" and paren_depth == 0:
            depth = max(0, depth - 1)
        i += 1
    return text


def owner_class_of(f: Function) -> str | None:
    """The enclosing class/struct of a method.

    Prefer the (already scope-qualified) display name; fall back to the mangled
    name when the dumper recorded an unqualified leaf (CastXML records the bare
    ``bar`` rather than ``C::bar``). ``Foo::bar`` → ``Foo``;
    ``ns::Foo::bar`` → ``ns::Foo``; a free function → ``None``.

    A conversion operator's own target type can itself carry ``"::"`` (e.g.
    ``"Foo::operator ns::Bar"`` for `operator ns::Bar()`, confirmed against a
    real compiled+demangled symbol) — naively splitting at the *last* ``"::"``
    would then wrongly treat that target's own qualification as the
    owner/member boundary, producing ``"Foo::operator ns"`` instead of
    ``"Foo"`` (Codex review, fresh evidence). A real demangled conversion
    operator always renders as ``"<owner>::operator <target>"`` with exactly
    one space after the ``operator`` keyword (never present for a symbol
    operator like ``operator+``/``operator[]``, which has no target type to
    separate from the keyword), so the true boundary is the ``"::"``
    immediately before that literal ``"::operator "`` marker when present.

    A direct-clang MSVC (``clang-cl``) snapshot preserves ``mangledName`` in
    the Microsoft mangling scheme while still recording a bare AST name for a
    member (the same "CastXML records the bare leaf" situation as above, just
    with a different mangled-name dialect) — the Itanium-only mangled-name
    fallback below left this owner unresolved (Codex review, fresh evidence).
    Tried second, after Itanium: the two schemes are mutually exclusive by
    their leading-byte convention (``_Z``/``__Z`` vs. ``?``), so trying both
    in sequence is unambiguous and costs nothing on the common Itanium path.

    A bare-recorded conversion operator (no owning-class prefix at all, the
    shape CastXML/direct-clang actually produce — confirmed elsewhere in this
    module) can still carry a qualified *target* type with its own ``"::"``
    (e.g. ``"operator ns::Bar"``, no ``"Foo::"`` prefix) when the underlying
    ``name`` attribute itself preserves that qualification (CodeRabbit
    review): the ``"::operator "`` marker isn't present (there's no owner
    before ``"operator"``), so the naive ``rsplit`` fallback would wrongly
    treat the target's own ``"::"`` as the owner/member boundary, returning
    junk like ``"operator ns"``. Detected the same way the bare, unqualified
    ``"operator Bar"`` case already is — checking for the ``"operator "``
    prefix — so both fall through to the mangled-name recovery below instead.
    """
    if "::" in f.name:
        marker_idx = f.name.find("::operator ")
        if marker_idx != -1:
            return f.name[:marker_idx]
        if not f.name.startswith("operator "):
            return f.name.rsplit("::", 1)[0]
        # Bare-recorded conversion operator with a qualified target
        # ("operator ns::Bar") -- the only "::" belongs to the target type,
        # not an owner/member boundary; fall through to mangled-name
        # recovery for the real owner.
    comps = itanium_scope_components(f.mangled) or msvc_scope_components(f.mangled)
    if not comps or len(comps) < 2:
        return None
    return "::".join(comps[:-1])


def _resolve_owner_type(
    owner: str, types: Mapping[str, RecordType], known_owners: set[str]
) -> RecordType | None:
    """Look up the owner's record, tolerating qualified-vs-leaf naming.

    DWARF records a class under its qualified name (``kde::View``); the CastXML
    dumper records it under the leaf (``View``). The owner derived from a mangled
    symbol is always qualified, so when the qualified key misses, fall back to
    the leaf component — but only when ``owner`` is a *known* qualified owner
    (i.e. the old surface actually had a symbol scoped to it). Without that
    corroboration a bare-leaf match could wrongly attach a brand-new
    ``kde::View`` to an unrelated existing ``foo::View`` that the dumper also
    recorded as ``View``.
    """
    t = types.get(owner)
    if t is not None:
        return t
    if owner not in known_owners:
        return None
    leaf = owner.rsplit("::", 1)[-1]
    return types.get(leaf) if leaf != owner else None


def virtual_signature_key(f: Function) -> str:
    """A signature identity for a (virtual) method: ``leaf(params)cv-ref``.

    Two methods share a vtable slot only when their name *and* signature match
    (covariant return aside), so this — not the bare leaf — is what tells an
    inherited-virtual *override* (reuses a slot) apart from a same-named virtual
    with different parameters (adds a slot). Parameter types are compared by
    their recorded spelling, which is sufficient here.
    """
    leaf = f.name.rsplit("::", 1)[-1]
    params = ",".join(p.type for p in f.params)
    quals = (
        ("c" if f.is_const else "") + ("v" if f.is_volatile else "") + f.ref_qualifier
    )
    return f"{leaf}({params}){quals}"


def _owner_descends_from(
    owner: str, ancestor: str, types: Mapping[str, RecordType]
) -> bool:
    """True if *owner* names *ancestor* itself, or a transitive base of it in *types*.

    Tolerant of qualified-vs-leaf naming the same way ``_transitive_bases``
    resolves base names (CastXML base lists are leaf-only project-wide; DWARF
    records the qualified form) — but only when at least one side IS a bare
    leaf, the genuine source of that ambiguity, AND the qualified side has no
    separately-resolvable type record of its own. Two *fully-qualified* names
    that merely share a leaf component (``ns1::Base`` vs ``ns2::Base``) are
    unrelated classes in different namespaces, not the same class recorded
    two ways, and must not be treated as equal; the same is true of a bare
    global name (``Base``) against a namespaced one (``ns::Base``) when
    ``ns::Base`` resolves to its own record in *types* -- that record's very
    presence proves this snapshot retains namespace fidelity, so the bare
    name can no longer be assumed to mean the same class.

    The identical corroboration applies when checking *ancestor* against
    *owner*'s declared bases below: a leaf-only base entry (e.g. ``owner``'s
    record lists a bare ``Base``) matches *any* same-leaf ``ancestor``
    unless that ``ancestor`` has its own resolvable qualified record too --
    otherwise a mixed snapshot where both ``ns1::Base`` and ``ns2::Base``
    exist could match a base list that only ever meant one specific one.
    """
    if owner == ancestor:
        return True
    leaf_owner = owner.rsplit("::", 1)[-1]
    leaf_ancestor = ancestor.rsplit("::", 1)[-1]
    owner_is_leaf = leaf_owner == owner
    ancestor_is_leaf = leaf_ancestor == ancestor

    def _leaf_match_trustworthy(qualified: str) -> bool:
        if qualified in types:
            return False
        # `types` is keyed by RecordType.name, which stays bare even for a
        # namespaced record (model.py: qualified_name is a separate field so
        # both backends key the same way) -- so a castxml-style record for
        # `ns::Base` lives at types["Base"] with qualified_name="ns::Base",
        # never at types["ns::Base"]. The key-only check above can therefore
        # never see it; without this, castxml snapshots would always treat
        # the qualified spelling as unresolvable and wrongly trust the leaf
        # match. Check qualified_name too so a record entered *only* that
        # way still corroborates and rejects the ambiguous leaf match.
        return not any(t.qualified_name == qualified for t in types.values())

    def _leaf_has_qualified_alternative(leaf: str, *excludes: str) -> bool:
        """True if some *other* record's qualified spelling shares this leaf.

        Used when matching a bare ``ancestor`` against a leaf-only ``bases``
        entry: ``_leaf_match_trustworthy`` above can only ask "does this
        specific qualified spelling have its own record", which needs a
        qualified string to check -- but a bare ancestor has none. Here the
        question is the mirror image: does some *differently*-qualified
        record (e.g. ``ns::Base``) exist for this same leaf, proving the
        base list's bare, unqualified entry could plausibly mean that one
        instead of the literal bare ``exclude`` spelling.

        Checks both ``RecordType.qualified_name`` (CastXML: ``name`` stays
        bare, the namespaced spelling lives in this separate field) and
        ``RecordType.name`` itself (DWARF: ``dwarf_snapshot.py`` stores the
        already-qualified spelling directly as ``name``, leaving
        ``qualified_name`` unset) -- checking only one field misses whichever
        backend produced the competing record.

        ``excludes`` must cover both the literal ``ancestor`` spelling AND
        the owning record's own identity (``owner``): a class that inherits
        from a global type sharing its own leaf (e.g. ``ns::Base : ::Base``)
        has a ``name``/``qualified_name`` that itself matches this leaf --
        that's the record whose base list is being interpreted, not a
        competing alternative, and excluding only the bare ancestor would
        wrongly treat the owner's own qualified identity as proof of
        ambiguity, hiding a genuine override-slot reuse.
        """
        for t in types.values():
            for candidate in (t.name, t.qualified_name):
                if (
                    candidate
                    and candidate not in excludes
                    and candidate.rsplit("::", 1)[-1] == leaf
                ):
                    return True
        return False

    if leaf_owner == leaf_ancestor and (owner_is_leaf or ancestor_is_leaf):
        # Reaching here with BOTH sides bare would mean owner == ancestor
        # (each equals its own leaf), already handled by the equality check
        # above -- so exactly one side is the qualified one to corroborate.
        qualified = ancestor if not ancestor_is_leaf else owner
        if _leaf_match_trustworthy(qualified):
            return True
    t = types.get(owner) or (types.get(leaf_owner) if leaf_owner != owner else None)
    if t is None:
        return False
    # This call site's own evidence-gap handling belongs to the dedicated
    # vtable/vptr_offset_bits slice (ADR-063 Phase 5B) `_owner_descends_from`
    # feeds via `vtable_slot_is_override_reuse` — unchanged here, only the
    # completeness flag `virtual_method_addition` added is discarded.
    bases, _walk_complete = _transitive_bases(t, types)
    # A qualified `ancestor` matching a `bases` entry exactly is unambiguous
    # (both are fully-qualified spellings of the same string). But when
    # `ancestor` is a bare leaf, an exact match against `bases` is NOT
    # automatically trustworthy: CastXML's base lists are leaf-only, so a
    # `bases` entry literally "Base" could equally have come from an
    # unrelated `ns::Base` recorded without its namespace -- that's exactly
    # the ambiguity `leaf_ancestor in bases` below already corroborates via
    # `_leaf_match_trustworthy`, and a bare `ancestor` hits that identical
    # string, so route it through the same corroboration rather than
    # short-circuiting past it here.
    if not ancestor_is_leaf and ancestor in bases:
        return True
    if leaf_ancestor not in bases:
        return False
    if ancestor_is_leaf:
        return not _leaf_has_qualified_alternative(leaf_ancestor, ancestor, owner)
    return _leaf_match_trustworthy(ancestor)


def vtable_slot_is_override_reuse(
    old_entry: str,
    new_entry: str,
    old_funcs: dict[str, Function],
    new_funcs: dict[str, Function],
    old_types: Mapping[str, RecordType],
    new_types: Mapping[str, RecordType],
) -> bool:
    """True if a vtable slot's mangled entry changed only because a derived
    class overrode the inherited virtual that occupied it, reusing the same
    slot rather than growing the vtable (case185).

    ``virtual_method_addition()`` already withholds ``VIRTUAL_METHOD_ADDED``
    for exactly this situation — a same-signature override of an inherited
    virtual — by comparing ``virtual_signature_key``. The per-type vtable diff
    (``diff_types_vtable._diff_type_vtable``) independently compares each class's
    raw vtable entry list, so without this check it disagrees with that
    exemption: the slot's mangled name textually changes (``Base::paint`` ->
    ``Derived::paint``) even though the slot index, order, and call signature
    are identical, and it would report ``TYPE_VTABLE_CHANGED`` for a change
    the other detector already deemed compatible. Mirroring the same
    signature-key comparison here keeps the two detectors in agreement.

    A signature match alone is not sufficient: two *unrelated* classes could
    each declare an unrelated virtual that happens to share a leaf name and
    parameter list, and a class could switch which one occupies a slot
    without genuinely overriding anything. It is also not enough that both
    owners are merely *somewhere* in the diffed class's combined old+new base
    set — for a class with sibling bases (``Derived : Base1, Base2``), or one
    whose base list itself changed (``Derived : Base1`` -> ``Derived :
    Base2``), a slot swapping from ``Base1::foo()`` to an unrelated
    ``Base2::foo()`` of the same signature would satisfy that looser test
    without either genuinely overriding the other. The real requirement is an
    actual override edge: the new entry's owner must be the old entry's
    owner itself, or genuinely descend from it (``_owner_descends_from``,
    checked against both new_types and old_types in case only one side's
    snapshot fully resolves the ancestor's own base list).
    """
    if old_entry == new_entry:
        return True
    f_old = old_funcs.get(old_entry)
    f_new = new_funcs.get(new_entry)
    if f_old is None or f_new is None or not f_old.is_virtual or not f_new.is_virtual:
        return False
    if virtual_signature_key(f_old) != virtual_signature_key(f_new):
        return False
    old_owner = owner_class_of(f_old)
    new_owner = owner_class_of(f_new)
    if old_owner is None or new_owner is None:
        return False
    return _owner_descends_from(
        new_owner, old_owner, new_types
    ) or _owner_descends_from(new_owner, old_owner, old_types)


def old_virtual_signatures(functions: Iterable[Function]) -> dict[str, set[str]]:
    """Per-class virtual-method signature keys for override detection.

    Keyed by *both* the qualified owner and its leaf, so a base name in either
    form (DWARF qualified, CastXML leaf) resolves. See ``virtual_method_addition``.
    """
    sigs: dict[str, set[str]] = {}
    for f in functions:
        if not f.is_virtual:
            continue
        owner = owner_class_of(f)
        if owner is None:
            continue
        sig = virtual_signature_key(f)
        sigs.setdefault(owner, set()).add(sig)
        leaf = owner.rsplit("::", 1)[-1]
        if leaf != owner:
            sigs.setdefault(leaf, set()).add(sig)
    return sigs


def _fact_str_list(fact: Fact[list[str]] | None) -> list[str]:
    """Read a ``Fact[list[str]]`` sibling the owning dataclass's own
    ``__post_init__`` guarantees is never ``None`` (ADR-063 Phase 0 —
    ``RecordType.bases_fact``/``virtual_bases_fact``/``vtable_fact``, see
    ``model/fact.py``'s ``bridge_legacy_and_fact``). The ``assert`` states
    that runtime invariant for mypy, which cannot see it through the
    dataclass field's declared ``Fact[...] | None`` type; the trailing
    ``or []`` only removes the *type-level* ``None`` mypy still carries for
    ``.value`` (a real ``PRESENT`` fact for these fields is never
    constructed with a ``None`` value) and is a no-op for an actually-empty
    list.
    """
    assert fact is not None
    value = fact.value if fact.is_present else []
    return value or []


def _fact_str_list_confirmed(fact: Fact[list[str]] | None) -> tuple[list[str], bool]:
    """Like :func:`_fact_str_list`, plus whether the value is safe to treat
    as the *complete* base-class list (ADR-063 Phase 5B).

    The value itself is preserved for both ``PRESENT`` and ``PARTIAL`` —
    exactly :func:`_fact_str_list`'s own value-preserving read — since
    :func:`_owner_descends_from` also calls :func:`_transitive_bases` and
    only ever reads this function's *set* of names, discarding the
    completeness flag entirely (that call site's own evidence-gap handling
    is scoped to the separate vtable/vptr_offset_bits slice). Dropping a
    ``PARTIAL`` fact's known entries here, rather than just refusing to
    call them *complete*, would silently lose a real ``Derived -> Base``
    relationship `_owner_descends_from` used to see via `_fact_str_list`
    before this function existed (Codex review on this PR: a same-signature
    override slot rename with `PARTIAL` `bases_fact` evidence stopped
    resolving through `vtable_slot_is_override_reuse`, fabricating a
    `TYPE_VTABLE_CHANGED` for what may be a compatible override).

    Only the completeness flag treats ``PARTIAL`` as unsafe, and only a
    ``PRESENT`` status earns ``True`` there — the same discipline
    :func:`abicheck.compare.base_class_diff.diff_bases` applies to this
    identical field pair: this is a full-list *membership* question (does
    some transitive base declare a matching virtual signature?), and a
    ``PARTIAL`` fact's uncovered remainder could hold exactly the base that
    would have proven an override. :func:`virtual_method_addition` — the
    one caller that actually reads this flag — declines to trust the walk
    when it is ``False``, rather than trusting a truncated value; it does
    not need this function to also truncate the value for it.
    """
    assert fact is not None
    value = fact.value if fact.is_present else []
    return (value or []), fact.status is FactStatus.PRESENT


def _transitive_bases(
    start: RecordType | None, types: Mapping[str, RecordType]
) -> tuple[set[str], bool]:
    """All (transitive) base-class names reachable from record ``start``,
    plus whether the walk saw only confirmed-complete ``bases``/
    ``virtual_bases`` evidence at every node it visited.

    Walks ``bases`` / ``virtual_bases``, resolving each base name through the
    record map with a leaf-name fallback (CastXML records and base names are
    leaf-only, while DWARF uses qualified names). Tolerant of missing records.

    The second return value is ``False`` as soon as *any* visited record's
    ``bases_fact``/``virtual_bases_fact`` is not ``PRESENT`` (ADR-063 Phase
    5B) — an incomplete evidence gap anywhere along the walk means a real
    base this record actually has may be missing from the result, which
    :func:`virtual_method_addition` must not silently read as "no override
    exists here" (the same "decline rather than fabricate" default
    ``diff_types_vtable._vtable_transition_is_evidenced`` and
    :func:`~abicheck.compare.base_class_diff.diff_bases` already apply to
    their own evidence gaps).
    """
    seen: set[str] = set()
    complete = True
    if start is None:
        return seen, complete
    start_bases, ok1 = _fact_str_list_confirmed(start.bases_fact)
    start_virtual_bases, ok2 = _fact_str_list_confirmed(start.virtual_bases_fact)
    complete = complete and ok1 and ok2
    stack = [*start_bases, *start_virtual_bases]
    while stack:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        rec = types.get(b) or types.get(b.rsplit("::", 1)[-1])
        if rec is not None:
            rec_bases, ok1 = _fact_str_list_confirmed(rec.bases_fact)
            rec_virtual_bases, ok2 = _fact_str_list_confirmed(rec.virtual_bases_fact)
            complete = complete and ok1 and ok2
            stack.extend((*rec_bases, *rec_virtual_bases))
    return seen, complete


def virtual_method_addition(
    f_new: Function,
    old_owner_classes: set[str],
    old_types: Mapping[str, RecordType],
    new_types: Mapping[str, RecordType],
    old_virtual_sigs: dict[str, set[str]],
    old_funcs: Mapping[str, Function],
    new_funcs: Mapping[str, Function],
    *,
    vtable_facts_reliable: bool = True,
) -> Change | None:
    """A new *virtual* method on a class that already exists across versions.

    Returns a ``VIRTUAL_METHOD_ADDED`` change, or ``None`` if this added symbol
    is not a virtual added to a pre-existing type. Scoped to the genuine blind
    spot: when ``diff_types_vtable``'s own ``TYPE_VTABLE_CHANGED`` detector
    would not fire for this owner (its raw ``vtable`` array is identical on
    both sides, or the difference has no positive evidence behind it), this is
    the only signal. When ``TYPE_VTABLE_CHANGED`` genuinely would fire, defer
    to it to avoid a duplicate finding.

    The owner's record must be present on both sides (the DWARF blind spot this
    targets). ``old_owner_classes`` — the set of *scope-qualified* owners of the
    old snapshot's public functions — authorizes the leaf-name fallback in
    ``_resolve_owner_type``: a qualified owner (``kde::View``) is unambiguous,
    but CastXML record names are leaf-only, so a bare-leaf match is only trusted
    when a sibling symbol confirms that exact qualified owner existed before.

    ``old_virtual_sigs`` maps each class (keyed by both its qualified name and
    its leaf) to the *signature keys* of its virtual methods in the *old*
    snapshot; it is used to skip *overrides* of an inherited virtual (e.g.
    ``Derived::paint() override``), which reuse the base's existing vtable slot
    rather than adding one — those are ABI-compatible, not breaks. Matching is by
    full signature, so a same-named virtual with *different* parameters (a new
    slot, e.g. ``Derived::paint(double)`` over ``Base::paint(int)``) still fires.

    ``old_funcs``/``new_funcs`` are each snapshot's full function map
    (``AbiSnapshot.function_map``, mangled name -> ``Function``, including
    non-public/hidden entries) — needed only to consult the shared vtable-
    evidence predicate below, not for this function's own override check.
    ``vtable_facts_reliable`` mirrors ``diff_types.py``'s own
    ``old.clang_vtable_facts_reliable and new.clang_vtable_facts_reliable``
    computation for the identical pair of snapshots; a caller that does not
    have both snapshots at hand (unusual — every real caller does) may leave
    it at its conservative default (never declines to defer on account of
    reliability alone).

    ADR-063 Track 2, 5B closure. Previously this function's ``old_vtable !=
    new_vtable`` branch deferred to ``TYPE_VTABLE_CHANGED`` unconditionally,
    on nothing but a docstring's word that the sibling detector's own
    ``_vtable_transition_is_evidenced`` heuristic "still finds real evidence"
    in the one shape this blind spot actually needs covered (one side's
    ``vtable_fact`` uncollected, the other genuinely populated) — the two
    detectors were coupled only by prose, in two separate files, with no
    executable link between them. Anywhere that heuristic does *not* find
    evidence (e.g. the class's own virtual-function set, size, and virtual
    bases all read identically on both sides — the array difference is
    capture noise the sibling detector correctly ignores), ``TYPE_VTABLE_
    CHANGED`` was never going to fire, and this function was deferring to a
    detector that stays silent — silently dropping the one coverage it exists
    to provide.

    Fixed by making the coupling a real function call: both detectors now
    share one predicate, ``compare.vtable_evidence.vtable_transition_is_
    evidenced`` — a leaf module below both of them (it depends on ``model``
    only, taking ``owner_class_of``/the namespace-suffix matcher as injected
    callables rather than importing either), closing exactly the "needs an
    import this leaf module's own no-cycle constraint does not allow without
    further restructuring" gap this docstring and ``diff_types_vtable.py``'s
    own module docstring used to record. When the predicate says the
    difference is genuinely evidenced (or ``vtable_facts_reliable`` is
    ``False``, in which case ``diff_types_vtable`` declines before ever
    reaching the predicate — see ``_diff_type_vtable``), this function defers.
    Otherwise it falls through to its own signature-based override check
    below, exactly as it already does when the raw arrays are equal outright
    — the only remaining signal, and no longer a blind assumption that some
    other module will catch it.
    """
    if not f_new.is_virtual:
        return None
    owner = owner_class_of(f_new)
    if owner is None:
        return None
    t_old = _resolve_owner_type(owner, old_types, old_owner_classes)
    t_new = _resolve_owner_type(owner, new_types, old_owner_classes)
    if t_old is None or t_new is None:
        return None  # no pre-existing record on both sides → compatible / out of scope
    old_vtable = _fact_str_list(t_old.vtable_fact)
    new_vtable = _fact_str_list(t_new.vtable_fact)
    if old_vtable != new_vtable and vtable_facts_reliable:
        if vtable_transition_is_evidenced(
            owner,
            t_old,
            t_new,
            old_funcs,
            new_funcs,
            owner_class_of=owner_class_of,
            namespace_suffix_spellings=_namespace_suffix_spellings,
        ):
            return None  # TYPE_VTABLE_CHANGED covers this case -- verified, not assumed
        # No evidence behind the raw array difference (or `TYPE_VTABLE_
        # CHANGED` would decline for the identical reason `diff_types_vtable`
        # already checks) -- the sibling detector will stay silent, so this
        # function is the only remaining signal for this owner. Fall through
        # to the same override check used when the arrays are equal outright.
    # An override of an inherited virtual reuses that base's slot — no new slot,
    # no relayout. If any transitive base already declared a virtual with the
    # *same signature* (not just the same name), treat the addition as an
    # override and stay silent; a different-signature same-name virtual is a new
    # slot and still fires.
    sig = virtual_signature_key(f_new)
    new_bases, new_complete = _transitive_bases(t_new, new_types)
    old_bases, old_complete = _transitive_bases(t_old, old_types)
    if not (new_complete and old_complete):
        # ADR-063 Phase 5B: an incomplete bases_fact/virtual_bases_fact
        # anywhere along either walk means a real override-providing base
        # may simply be missing from `bases` — the walk cannot rule out an
        # override, so this must decline rather than fabricate a
        # VIRTUAL_METHOD_ADDED against what may really be a plain override.
        return None
    bases = new_bases | old_bases
    if any(sig in old_virtual_sigs.get(b, ()) for b in bases):
        return None
    return make_change(
        ChangeKind.VIRTUAL_METHOD_ADDED,
        symbol=f_new.mangled,
        detail=owner,
        new=f_new.name,
        entity_id=f_new.entity_id,
    )
