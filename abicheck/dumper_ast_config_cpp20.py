# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""C++20 structural-syntax detection: concept/requires/consteval/constinit
and constrained/abbreviated function-template parameters.

Split out of ``dumper_ast_config.py`` (which crossed the AI-readiness
2000-line hard cap) — this module owns the directive/literal/comment-aware
scan that decides whether a header set needs ``-std=gnu++20`` and, for the
one caller that needs it, whether an auto-detected header should be treated
as C++ at all. See ``dumper_ast_config.py`` for the AST cache-key and
general (non-C++20) C/C++ language-mode detection this module doesn't own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# The preprocessor-chain reachability pass, split into its own module (this
# file is at the AI-readiness file-size cap). Re-exported by name so both this
# module's own callers and `tests/test_dumper_ast_config_cpp20_pp_guards.py`
# keep importing it from here.
from .dumper_ast_config_cpp20_chains import (
    _strip_inactive_if_zero_blocks as _strip_inactive_if_zero_blocks,
)

# Structural C++20 patterns — concepts and requires-expressions. When any
# of these appears in a header, castxml must be invoked with a C++20-aware
# `-std=` flag or it will fail to parse the file. The patterns target the
# definition site (`concept X = ...`, `requires(...) {`, `template <Foo T>`-
# style constrained template parameters) rather than uses, so we don't
# over-trigger. Matching is applied only to *code* text (see
# ``_find_cpp20_requirements``): preprocessor directive lines, string/char
# literal contents, and comments are stripped first, so "requires" appearing
# in a `#error`/`#define` message or a string literal is never mistaken for
# the C++20 keyword (Codex/false-positive report).
_CPP20_CONCEPT_PATTERN = re.compile(rb"\bconcept\s+\w+\s*=")  # concept Addable = ...
# "concept" only became a reserved keyword in C++20 — a pre-C++20 header can
# legally declare a type literally named "concept" (Codex review, four
# rounds: a qualified use, an unqualified use, a brace-initialized variable
# template, and — the general case this pattern exists to catch — a
# variable template initialized via *any* other expression convertible to
# that type, e.g. through a converting constructor: ``struct concept {
# concept(int); }; template<class T> concept C = 1;``). No per-initializer
# check can ever be complete, so this instead detects the one thing that
# makes every one of those variants possible: a definition of "concept" as
# an ordinary type name anywhere in the header. Covers ``union``/``enum``
# as well as ``struct``/``class`` (Codex review, sixth round) — a
# ``union concept { ... };`` or ``enum concept { ... };`` shadow is just as
# legal pre-C++20 as the class-key form and was previously invisible here.
_CONCEPT_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class|union|enum)\s+concept\b|\busing\s+concept\s*=|\btypedef\b[^;]*\bconcept\s*;"
)
# "requires" only became a reserved keyword in C++20 too — a pre-C++20 header
# can legally declare a type literally named "requires" and reference it in a
# variable-template declaration (``template<class T> requires value = {};``),
# whose bare ``requires\s+\w`` shape directly after a template header's
# closing ``>`` is textually identical to a genuine requires-clause (Codex
# review). Mirrors ``_CONCEPT_AS_TYPE_NAME_PATTERN``/``concept_type_shadowed``
# exactly.
_REQUIRES_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class|union|enum)\s+requires\b|\busing\s+requires\s*=|\btypedef\b[^;]*\brequires\s*;"
)
_CPP20_REQUIRES_EXPR_PATTERN = re.compile(
    rb"\brequires\s*[(\{]"
)  # requires(T a, T b) { ... }  OR the parameterless requires { ... } form
# (Codex review: the parameterless form has no parenthesized parameter list —
# `requires { typename T::value_type; }` — and was previously missed
# entirely, since the requires-clause pattern below also requires a \w
# immediately after "requires", which a bare "{" is not.)
_CPP20_REQUIRES_CLAUSE_PATTERN = re.compile(
    rb"\brequires\s+\w"
)  # template<T> requires Foo<T>

# ``consteval``/``constinit`` are new C++20 declaration specifiers, but
# unlike "concept"/"requires" this is not just a style-vs-risk trade-off:
# neither was a reserved word before C++20, so a pre-C++20 header can
# legally use either as an ordinary identifier (``int consteval;``, ``int
# constinit;`` — declaring a variable with that name). An unconditional
# bare-keyword match (unlike the deliberately unconditional
# `_CPP_ONLY_PATTERNS` entries for `constexpr`/`noexcept`/`nullptr`/
# `override`, which only ever decide "must be C++", not "must be C++20")
# would force -std=gnu++20 on such a header, where the identifier is no
# longer usable — actively breaking a header that previously parsed fine
# (Codex review, second round). Requiring a positive lookahead for
# whitespace then another identifier-starting character distinguishes a
# genuine specifier (``consteval int f();``, ``constinit extern int
# x;`` — always followed by more decl-specifier/declarator content) from
# the ordinary-identifier shape, where the keyword is the last token of
# its (simple) declarator, directly followed by ``;``/``,``/``=``/``)``/
# ``[`` instead.
#
# That lookahead alone still can't tell a genuine specifier from a
# pre-C++20 header that instead declares a *type* literally named
# "consteval"/"constinit" (``struct consteval {};``) and later
# references it followed by another decl-specifier or cv-qualifier
# (``consteval const *p;`` — legal pre-C++20: decl-specifier order is
# flexible, so this means the same as ``const consteval *p;``) — the
# textual shape is identical to a genuine ``consteval <type> <name>``
# declaration (Codex review, third round). Mirrors
# ``_CONCEPT_AS_TYPE_NAME_PATTERN``/``concept_type_shadowed`` exactly:
# once a header is confirmed to declare "consteval"/"constinit" as an
# ordinary type name anywhere, every bare occurrence in that header is
# ambiguous and treated as non-genuine.
_CPP20_CONSTEVAL_PATTERN = re.compile(rb"\bconsteval\b(?=\s+[A-Za-z_])")
_CPP20_CONSTINIT_PATTERN = re.compile(rb"\bconstinit\b(?=\s+[A-Za-z_])")
_CONSTEVAL_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class|union|enum)\s+consteval\b|\busing\s+consteval\s*=|\btypedef\b[^;]*\bconsteval\s*;"
)
_CONSTINIT_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class|union|enum)\s+constinit\b|\busing\s+constinit\s*=|\btypedef\b[^;]*\bconstinit\s*;"
)

# Constrained template parameters using a *standard-library* concept name in
# place of ``typename``/``class`` (``template <std::integral T> void f(T);``)
# — the abbreviated-constraint form the module docstring above already
# describes but never actually matched (Codex review). Deliberately scoped
# to the fixed, well-known set of concepts in <concepts>/<iterator>/<ranges>
# rather than "any bare or qualified identifier in a template parameter
# list": an arbitrary identifier there is *routinely* a valid pre-C++20
# non-type template parameter's type (``template<MyEnum E>``,
# ``template<Traits::value_type V>``), so matching on identifier shape alone
# would trade this false-negative for a much broader false-positive risk.
# A `std::`-qualified name from this exact, finite standard list used
# immediately before a bare template-parameter identifier has no such
# ambiguity — it is never a plausible NTTP type spelling. <ranges> concepts
# (Codex review, second round) live under the distinct ``std::ranges::``
# namespace, matched by a separate pattern below rather than folded into
# the bare ``std::`` one, since the two prefixes are not interchangeable.
_CPP20_STD_CONCEPT_NAMES = (
    rb"same_as|derived_from|convertible_to|common_reference_with|common_with|"
    rb"integral|signed_integral|unsigned_integral|floating_point|"
    rb"assignable_from|swappable_with|swappable|destructible|"
    rb"constructible_from|default_initializable|move_constructible|"
    rb"copy_constructible|equality_comparable_with|equality_comparable|"
    rb"totally_ordered_with|totally_ordered|movable|copyable|semiregular|"
    rb"regular_invocable|regular|invocable|predicate|relation|"
    rb"strict_weak_order|sortable|mergeable|permutable|indirect_unary_predicate|"
    rb"indirect_binary_predicate|indirect_equivalence_relation|"
    rb"indirect_strict_weak_order|indirectly_regular_unary_invocable|"
    rb"indirectly_unary_invocable|indirectly_readable|indirectly_writable|"
    rb"indirectly_swappable|indirectly_movable_storable|indirectly_movable|"
    rb"indirectly_copyable_storable|indirectly_copyable|indirectly_comparable|"
    rb"weakly_incrementable|incrementable|input_or_output_iterator|"
    rb"sentinel_for|sized_sentinel_for|input_iterator|output_iterator|"
    rb"forward_iterator|bidirectional_iterator|random_access_iterator|"
    rb"contiguous_iterator"
)
_CPP20_STD_RANGES_CONCEPT_NAMES = (
    rb"range|borrowed_range|sized_range|view|input_range|output_range|"
    rb"forward_range|bidirectional_range|random_access_range|"
    rb"contiguous_range|common_range|viewable_range|constant_range"
)
# Matches just the qualified concept name itself (``std::integral`` /
# ``std::ranges::range``) — the optional ``<...>`` argument list and what
# follows it are handled separately by :func:`_has_constrained_param_syntax`,
# since a naive single-level ``(?:<[^<>]*>)?`` here cannot match a concept
# argument that itself contains a template-id, e.g.
# ``std::same_as<std::vector<int>>`` (Codex review, third round).
_CPP20_CONSTRAINED_PARAM_CONCEPT_PATTERN = re.compile(
    rb"\bstd::(?:ranges::(?:"
    + _CPP20_STD_RANGES_CONCEPT_NAMES
    + rb")|(?:"
    + _CPP20_STD_CONCEPT_NAMES
    + rb"))\b"
)
# What can follow a constrained template parameter's (optional) concept
# argument list, before the enclosing template parameter list's next ","
# or its own closing ">": an optional pack ellipsis, an optional parameter
# name (anonymous constrained parameters are legal too), and an optional
# default argument (Codex review — a bare trailing "\w+\s*[,>]" missed
# both ``template <std::integral T = int>`` and ``template
# <std::integral... Ts>``). The default's own value is not validated
# beyond excluding ","/";"/"{"/"}" — a default containing a raw,
# bracket-free comma (e.g. a function-pointer type) is the one shape this
# doesn't cover, an acceptable narrow gap given how rare that spelling is
# in a template-parameter default specifically.
_CONSTRAINED_PARAM_TAIL_PATTERN = re.compile(
    rb"(?:\.\.\.)?\s*\w*\s*(?:=\s*[^,;{}]*)?\s*[,>]"
)


def _find_matching_close_angle(text: bytes, open_angle_pos: int) -> int | None:
    """Return the index of the ``>`` matching the ``<`` at *open_angle_pos*
    in *text* (tracking nesting), or ``None`` if unbalanced/not found.
    Mirrors :func:`_find_matching_close_paren` — regex alone cannot match
    an arbitrarily-nested template-id argument list."""
    depth = 0
    for idx in range(open_angle_pos, len(text)):
        ch = text[idx : idx + 1]
        if ch == b"<":
            depth += 1
        elif ch == b">":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _has_constrained_param_syntax(lookahead: bytes) -> bool:
    """True if *lookahead* contains a constrained template parameter
    (``template <std::integral T>``, with the concept argument list — if
    any — tolerating arbitrary nesting, e.g.
    ``std::same_as<std::vector<int>>``) or an abbreviated constrained
    function parameter (``std::integral auto x`` — Codex review: a
    concept name directly followed by the literal keyword ``auto`` has no
    other valid pre-C++20 reading, so no disambiguation is needed there).
    Deliberately scoped to the fixed, well-known set of concepts in
    <concepts>/<iterator>/<ranges> rather than "any bare or qualified
    identifier in a template parameter list": an arbitrary identifier
    there is *routinely* a valid pre-C++20 non-type template parameter's
    type (``template<MyEnum E>``, ``template<Traits::value_type V>``), so
    matching on identifier shape alone would trade this false-negative
    for a much broader false-positive risk. A `std::`-qualified name from
    this exact, finite standard list has no such ambiguity."""
    for m in _CPP20_CONSTRAINED_PARAM_CONCEPT_PATTERN.finditer(lookahead):
        pos = m.end()
        ws = re.match(rb"\s*", lookahead[pos:])
        if ws:
            pos += ws.end()
        if lookahead[pos : pos + 1] == b"<":
            close = _find_matching_close_angle(lookahead, pos)
            if close is None:
                continue
            pos = close + 1
            ws = re.match(rb"\s*", lookahead[pos:])
            if ws:
                pos += ws.end()
        # Whitespace before this point was already consumed above (either
        # directly after the concept name, or after its <...> argument
        # list), so what remains needs no further leading \s+ — only the
        # \b at the concept-name pattern's own end guarantees *some*
        # non-word separator existed there in the first place.
        rest = lookahead[pos:]
        if re.match(rb"auto\b", rest) or _CONSTRAINED_PARAM_TAIL_PATTERN.match(rest):
            return True
    return False


def _find_enclosing_open_paren(text: bytes, pos: int) -> int | None:
    """Return the index of the nearest unmatched ``(`` to the left of
    *pos* in *text*, skipping balanced ``()`` pairs — mirrors
    :func:`_find_matching_close_paren`'s forward scan, just backward."""
    depth = 0
    idx = pos - 1
    while idx >= 0:
        ch = text[idx : idx + 1]
        if ch == b")":
            depth += 1
        elif ch == b"(":
            if depth == 0:
                return idx
            depth -= 1
        idx -= 1
    return None


_TRAILING_ATTRIBUTE_PATTERN = re.compile(rb"\[\[[^\[\]]*\]\]\s*\Z")


def _strip_trailing_attributes(prefix: bytes) -> bytes:
    """Strip zero or more trailing ``[[attr]]``/``[[attr(args)]]``
    attribute-specifier-seq entries (``[[maybe_unused]]``,
    ``[[deprecated("msg")]]``, ...) from *prefix* — a standard attribute
    can precede a parameter's type (``void f([[maybe_unused]] auto x);``),
    which otherwise leaves the prefix ending in ``]`` instead of the
    enclosing ``(``/``,`` (Codex review). Narrow gap, accepted: an
    attribute argument containing a literal ``[``/``]`` (e.g. inside a
    string) is not unwrapped — vanishingly rare in practice."""
    prefix = prefix.rstrip()
    while True:
        m = _TRAILING_ATTRIBUTE_PATTERN.search(prefix)
        if not m:
            return prefix
        prefix = prefix[: m.start()].rstrip()


_LEADING_DECL_SPECIFIER_WORDS = frozenset(
    {b"inline", b"static", b"virtual", b"explicit", b"friend", b"extern", b"constexpr"}
)


def _strip_trailing_leading_decl_specifier_words(prefix: bytes) -> bytes:
    """Strip zero or more trailing decl-specifier-seq keywords that can
    precede a declaration's return type (``inline``, ``static``,
    ``virtual``, ``explicit``, ``friend``, ``extern``, ``constexpr``) from
    *prefix* — Codex review: a constrained placeholder *return type*
    (``MyConcept auto f();``) routinely has one or more of these directly
    before it (``inline MyConcept auto f();``), which otherwise leaves the
    prefix ending mid-keyword instead of the genuine statement boundary
    the caller checks for next. Uses :data:`_TRAILING_IDENTIFIER_PATTERN`,
    defined further below alongside this file's other trailing-identifier
    matchers."""
    prefix = prefix.rstrip()
    while True:
        m = _TRAILING_IDENTIFIER_PATTERN.search(prefix)
        if not m or m.group(1) not in _LEADING_DECL_SPECIFIER_WORDS:
            return prefix
        prefix = prefix[: m.start()].rstrip()


def _is_lambda_param_list_open_paren(text: bytes, open_paren_pos: int) -> bool:
    """True if the ``(`` at *open_paren_pos* opens a lambda's parameter
    list — immediately preceded (skipping whitespace) by the ``]`` that
    closes a lambda capture list. A generic lambda's ``auto`` parameter
    (``[](auto x) { ... }``) has been valid since C++14 and must never be
    mistaken for the C++20-only abbreviated *function* template form."""
    idx = open_paren_pos - 1
    while idx >= 0 and text[idx : idx + 1] in b" \t\r\n":
        idx -= 1
    return idx >= 0 and text[idx : idx + 1] == b"]"


def _is_decltype_open_paren(text: bytes, open_paren_pos: int) -> bool:
    """True if the ``(`` at *open_paren_pos* is a ``decltype`` specifier's
    own parentheses — immediately preceded (skipping whitespace) by the
    keyword ``decltype``. ``decltype(auto)`` (valid since C++14) puts the
    bare keyword ``auto`` directly inside this ``(``, the identical
    textual position as a genuine abbreviated parameter's enclosing
    ``(`` — but it is decltype's own argument, not a parameter list at
    all (Codex review)."""
    idx = open_paren_pos - 1
    while idx >= 0 and text[idx : idx + 1] in b" \t\r\n":
        idx -= 1
    end = idx + 1
    while idx >= 0 and (text[idx : idx + 1].isalnum() or text[idx : idx + 1] == b"_"):
        idx -= 1
    return text[idx + 1 : end] == b"decltype"


_CONTROL_FLOW_KEYWORDS = frozenset({b"for", b"if", b"while", b"switch"})


def _is_control_flow_open_paren(text: bytes, open_paren_pos: int) -> bool:
    """True if the ``(`` at *open_paren_pos* is a control-flow statement's
    own parenthesized condition/init-statement/range-declaration —
    immediately preceded (skipping whitespace) by the keyword ``for``,
    ``if``, ``while``, or ``switch`` — rather than a function's parameter
    list (Codex review). A bare ``auto`` there names an ordinary
    variable's declared type, not a parameter: ``for (auto x : xs)`` (a
    range-based for loop, valid since C++11) and ``if (auto x = f())``/
    ``while (auto x = f())``/``switch (auto x = f())`` (a condition or
    init-statement declaring a variable, valid since C++17) are the
    *identical* textual shape — bare ``auto`` immediately inside a ``(``
    — as a genuine abbreviated function-template parameter, but neither
    is one."""
    idx = open_paren_pos - 1
    while idx >= 0 and text[idx : idx + 1] in b" \t\r\n":
        idx -= 1
    end = idx + 1
    while idx >= 0 and (text[idx : idx + 1].isalnum() or text[idx : idx + 1] == b"_"):
        idx -= 1
    return text[idx + 1 : end] in _CONTROL_FLOW_KEYWORDS


def _has_abbreviated_unconstrained_auto_param(lookahead: bytes) -> bool:
    """True if *lookahead* contains a bare (unconstrained) ``auto`` used
    directly as an ordinary function's parameter type (``void f(auto
    x);``) — the C++20 abbreviated function template form, distinct from
    the *constrained* form (``std::integral auto x``, handled separately
    by :func:`_has_constrained_param_syntax`), from a generic lambda's
    ``auto`` parameter (``[](auto x) { ... }``), which has been valid
    since C++14 and is excluded via :func:`_is_lambda_param_list_open_paren`
    (Codex review), from ``decltype(auto)`` (also valid since C++14,
    excluded via :func:`_is_decltype_open_paren` — Codex review, second
    round), and from a control-flow statement's own parenthesized
    condition/init-statement/range-declaration (``for``/``if``/``while``/
    ``switch``, excluded via :func:`_is_control_flow_open_paren` — Codex
    review, further round: unlike the *constrained* form, which C++20
    genuinely permits in a for-range-declaration or init-statement too, a
    bare ``auto`` there is always just an ordinary variable's type, never
    a parameter). Only matches when nothing but an optional
    cv-qualifier and/or attribute-specifier-seq (``[[maybe_unused]] auto
    x`` — Codex review, third round) separates ``auto`` from its
    enclosing ``(``/``,`` — that position is unambiguous: a bare ``auto``
    can never be a parameter's default-argument expression or any other
    operand there, only its type."""
    for m in re.finditer(rb"\bauto\b", lookahead):
        prefix = _strip_trailing_declarator_specifiers(lookahead[: m.start()])
        prefix = _strip_trailing_attributes(prefix)
        if not prefix:
            continue
        last = prefix[-1:]
        open_pos: int | None
        if last == b"(":
            open_pos = len(prefix) - 1
        elif last == b",":
            open_pos = _find_enclosing_open_paren(lookahead, len(prefix))
            if open_pos is None:
                continue
        else:
            continue
        if (
            not _is_lambda_param_list_open_paren(lookahead, open_pos)
            and not _is_decltype_open_paren(lookahead, open_pos)
            and not _is_control_flow_open_paren(lookahead, open_pos)
        ):
            return True
    return False


_QUALIFIED_IDENTIFIER_TAIL_PATTERN = re.compile(rb"(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*\Z")


def _has_custom_constrained_auto_param(
    lookahead: bytes, prev_nonblank_code: bytes = b""
) -> bool:
    """True if *lookahead* contains an abbreviated function parameter
    constrained by a project-defined (non-``std::``) concept
    (``void f(MyConcept auto x);``) — Codex review. Unlike a bare
    identifier directly inside a ``template<...>`` parameter list
    (routinely a valid pre-C++20 non-type template parameter's type, e.g.
    ``template<MyEnum E>`` — see :func:`_has_constrained_param_syntax`'s
    docstring for why that shape stays scoped to the finite ``std::``
    list), an identifier immediately followed by the keyword ``auto`` in a
    parameter's decl-specifier position has no valid pre-C++20 reading at
    all: a decl-specifier-seq can carry at most one type-determining
    specifier, and ``auto`` — always a keyword, never usable as an
    ordinary identifier the way ``concept``/``requires``/``consteval``/
    ``constinit`` are pre-C++20 — is itself one, so juxtaposing it with an
    unrelated type name is exclusively the C++20 constrained-placeholder
    syntax regardless of which concept is named. Reuses the same
    unambiguous-boundary check as :func:`_has_abbreviated_unconstrained_auto_param`
    (nothing but the identifier separates ``auto`` from its enclosing
    ``(``/``,``); a leading cv-qualifier before the identifier
    (``const MyConcept auto& x``, Codex review) is stripped via
    :func:`_strip_trailing_declarator_specifiers` first — reused here for
    its trailing-word-popping loop, even though this call site's prefix
    isn't itself a trailing-specifier position — so the boundary check
    that follows (``(``/``,``, or a genuine statement boundary for the
    return-type shape) still sees the real enclosing punctuation once
    ``const``/``volatile`` is out of the way.

    Unlike :func:`_has_abbreviated_unconstrained_auto_param`, a lambda's
    parameter list is deliberately *not* excluded here (Codex review):
    a *bare* ``auto`` lambda parameter (``[](auto x) {}``) has been valid
    since C++14, but a lambda parameter *constrained* by a concept
    (``[](MyConcept auto x) {}``) is exactly as C++20-only as the
    equivalent ordinary-function form — lambdas' call operators gained
    the same abbreviated-function-template treatment in C++20. Excluding
    lambda parameter lists here would silently miss the only C++20 signal
    in a header whose lone use of the feature is a constrained generic
    lambda.

    A constrained placeholder *return type* (``MyConcept auto f();``,
    Codex review) gets the identical unambiguous decl-specifier-seq
    reasoning — it counts whenever nothing but whitespace separates the
    identifier from a genuine statement boundary: either earlier content
    on the same logical line ending in ``;``/``{``/``}``, or (when the
    identifier opens the line entirely) *prev_nonblank_code*'s last
    non-blank code ending the same way, or no preceding code at all (the
    very first declaration in the scanned content). *prev_nonblank_code*
    defaults to empty for callers that don't track it (matching this
    module's other lookahead-only helpers) — the return-type check simply
    never fires for a caller that doesn't pass it, the same conservative
    default as any other unattempted shape here. Ordinary leading
    decl-specifier keywords (``inline``, ``static``, ``virtual``, ...)
    and attributes routinely precede a return type too (``inline
    MyConcept auto f();``, ``[[nodiscard]] MyConcept auto f();`` — Codex
    review), so they're stripped in the same fixed-point loop, in any
    order, before the statement-boundary check runs."""
    for m in re.finditer(rb"\bauto\b", lookahead):
        prefix = _strip_trailing_declarator_specifiers(lookahead[: m.start()])
        prefix = _strip_trailing_attributes(prefix)
        ident_match = _QUALIFIED_IDENTIFIER_TAIL_PATTERN.search(prefix)
        if not ident_match:
            continue
        before_ident = prefix[: ident_match.start()].rstrip()
        while True:
            stripped = _strip_trailing_declarator_specifiers(before_ident)
            stripped = _strip_trailing_attributes(stripped)
            stripped = _strip_trailing_leading_decl_specifier_words(stripped)
            if stripped == before_ident:
                break
            before_ident = stripped
        last = before_ident[-1:]
        open_pos: int | None
        if last == b"(":
            open_pos = len(before_ident) - 1
        elif last == b",":
            open_pos = _find_enclosing_open_paren(lookahead, len(before_ident))
            if open_pos is None:
                continue
        else:
            open_pos = None
        if open_pos is not None:
            if not _is_decltype_open_paren(lookahead, open_pos):
                return True
            continue
        # Not inside a parameter list at all — only a genuine statement
        # boundary right before the identifier (on this line, or on the
        # last preceding non-blank code when the identifier opens the
        # line), or a trailing-return-type arrow directly following a
        # function declarator's closing ")" (the ``auto f() -> MyConcept
        # auto;`` shape, Codex review), makes this a declaration-start
        # return-type position rather than some other expression context.
        if before_ident:
            if last not in _REQUIRES_STATEMENT_BOUNDARY_CHARS and not (
                last == b">" and _has_declarator_adjacent_trailing_arrow(before_ident)
            ):
                continue
        else:
            prev = _strip_trailing_declarator_specifiers(prev_nonblank_code.rstrip())
            if prev and prev[-1:] not in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
                continue
        return True
    return False


# "requires" only became a reserved keyword in C++20 — any earlier standard
# allows it as an ordinary identifier, e.g. ``bool requires(int x) { ... }``
# (a declaration) or ``requires(1);`` (a call), both real uses of a
# function literally named "requires". Forcing -std=gnu++20 on such a
# header would break it, since the identifier is no longer usable there
# (Codex review, two rounds: the declaration case, then the call/
# expression-statement case). The only way "requires(" is preceded by a
# bare word (just whitespace, no operator) in *genuine* C++20 usage is a
# handful of expression-introducing keywords (return/throw/co_return);
# every other preceding identifier can only be a declaration/call using
# "requires" as a plain pre-C++20 name — juxtaposing two bare identifiers
# with nothing but whitespace between them is not valid C++ in any other
# production. Likewise, "requires(" at the very start of a statement (
# preceded by nothing, or by a statement-boundary "{"/"}"/";") can only be
# a call-as-statement using the plain pre-C++20 name — a genuine
# requires-expression or requires-clause is always itself a sub-expression
# (an operand), never a bare statement by construction of this detector's
# own trigger (there is no standalone top-level "requires ...;" construct
# in C++20 outside of being part of a larger declaration/expression).
_REQUIRES_EXPR_SAFE_PRECEDING_WORDS = frozenset({b"return", b"throw", b"co_return"})
_REQUIRES_STATEMENT_BOUNDARY_CHARS = frozenset({b"{", b"}", b";"})
_TRAILING_IDENTIFIER_PATTERN = re.compile(rb"([A-Za-z_]\w*)\Z")


def _find_matching_close_paren(text: bytes, open_paren_pos: int) -> int | None:
    """Return the index of the ``)`` matching the ``(`` at *open_paren_pos*
    in *text* (tracking nesting), or ``None`` if unbalanced/not found."""
    depth = 0
    for idx in range(open_paren_pos, len(text)):
        ch = text[idx : idx + 1]
        if ch == b"(":
            depth += 1
        elif ch == b")":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _requires_match_has_body(lookahead: bytes, match: re.Match[bytes]) -> bool:
    """True if the requires(...)/requires{ *match* is confirmed to carry a
    requirements body: the parameterless form always does (the matched
    ``{`` **is** the body); the parenthesized form only does when its
    matching ``)`` is immediately followed by ``{``. A plain call to a
    pre-C++20 "requires" function has no such body — just ``;`` or another
    token after the closing paren."""
    matched_char = lookahead[match.end() - 1 : match.end()]
    if matched_char == b"{":
        return True
    close = _find_matching_close_paren(lookahead, match.end() - 1)
    if close is None:
        return False
    return lookahead[close + 1 :].lstrip().startswith(b"{")


_TRAILING_DECLARATOR_SPECIFIER_WORDS = frozenset(
    {b"const", b"volatile", b"noexcept", b"override", b"final"}
)


def _strip_trailing_declarator_specifiers(prefix: bytes) -> bytes:
    """Strip zero or more trailing cv/ref-qualifiers and specifiers
    (``const``, ``volatile``, ``noexcept``, ``override``, ``final``,
    ``&``, ``&&``) from *prefix* (Codex review). A trailing requires-clause
    can follow any number of these after a function's declarator (
    ``void f(T) const noexcept requires C<T>;``) — stripping them lets the
    caller still trace the prefix back to the declarator's own closing
    ``)`` regardless of how many specifiers sit in between. Safe to strip
    unconditionally: none of these words/operators can appear as a bare
    trailing token in *any* other C++ construct without being part of a
    declarator's trailing specifier sequence."""
    prefix = prefix.rstrip()
    changed = True
    while changed:
        changed = False
        if prefix.endswith(b"&&"):
            prefix = prefix[:-2].rstrip()
            changed = True
        elif prefix.endswith(b"&"):
            prefix = prefix[:-1].rstrip()
            changed = True
        else:
            m = _TRAILING_IDENTIFIER_PATTERN.search(prefix)
            if m is not None and m.group(1) in _TRAILING_DECLARATOR_SPECIFIER_WORDS:
                prefix = prefix[: m.start()].rstrip()
                changed = True
    return prefix


def _has_declarator_adjacent_trailing_arrow(prefix: bytes) -> bool:
    """True if *prefix* contains a trailing-return-type arrow (``->``)
    that itself directly follows (after stripping cv/ref/noexcept
    specifiers) a function declarator's closing ``)`` — the ``->
    ReturnType`` shape of ``auto f(T) -> ReturnType``.

    A bare substring search for ``->`` anywhere in *prefix* also matches
    an unrelated member-access expression earlier in the *same*
    statement/expression, not just a different one (the statement-
    boundary check catches that case, but not this one): ``int
    requires(int); return p->m + requires(1);`` — a plain pre-C++20 call
    to a function named "requires", added to a member-access result —
    was wrongly classified genuine because of the ``->`` in ``p->m``, with
    no statement boundary between it and "requires" (Codex review, fifth
    round). Walking every ``->`` occurrence right-to-left (rather than
    just checking substring membership) also correctly finds a nested
    arrow inside the return type itself (a rare ``decltype(a->b)`` return
    type) even when it is not the rightmost occurrence.

    Residual, accepted ambiguity: a function *call* immediately followed
    by member access (``getObj()->m``) has the identical ``...)  ->...``
    shape as a genuine declarator, and this check cannot tell them apart
    without real parsing — the same "impossible to bound generically"
    trade-off already accepted for the return-type expression itself."""
    for m in re.finditer(re.escape(b"->"), prefix):
        before = _strip_trailing_declarator_specifiers(prefix[: m.start()])
        if before.endswith(b")"):
            return True
    return False


def _looks_like_requires_declarator(
    lookahead: bytes, match: re.Match[bytes], prev_nonblank_code: bytes
) -> bool:
    """True if the requires-expression candidate at *match* in *lookahead*
    looks like an ordinary pre-C++20 use of "requires" as a plain
    identifier — either immediately preceded (skipping only whitespace) by
    a bare identifier that isn't one of the few keywords that can
    legitimately introduce a requires-expression as an operand (the
    declaration/call-with-preceding-name case), preceded by nothing but a
    statement boundary (the bare call-as-statement case), or preceded by
    ``.``/``->``/``::`` (a member/qualified-name access — "requires" the
    C++20 keyword is never looked up that way) — rather than the C++20
    keyword.

    When nothing at all precedes the candidate on its own logical line, a
    genuine parenthesized requires-clause continuing a ``template<...>``
    header from the *previous* line (``template<class T>\\nrequires
    (sizeof(T) > 4)\\nvoid f(T);``) looks identical to a bare call-as-
    statement at this point, so that case falls back to *prev_nonblank_code*
    the same way :func:`_looks_like_genuine_concept` does — but only after
    confirming *prev_nonblank_code* is not itself a *different*, unrelated
    statement (a leading statement-boundary character in it means the
    genuine-continuation shape above cannot apply at all): otherwise a
    stray ``->``/``)``/``>`` anywhere earlier in that previous logical
    line — which can hold more than one statement — was picked up by the
    same unscoped-substring bug as :func:`_looks_like_genuine_requires_clause`
    (Codex review, fourth round).

    A safe preceding word (return/throw/co_return) is necessary but not
    sufficient: ``return requires(1);`` — a plain call to a pre-C++20
    "requires" function — is just as syntactically valid there as a real
    ``return requires(T t) { t.foo(); };``. Only the latter carries a
    requirements body, so that case additionally confirms one before
    accepting (Codex review). The same ambiguity applies whenever
    "requires" is preceded by an operator/punctuation rather than a bare
    identifier at all — ``if (requires(1)) ...``, ``!requires(1)``,
    ``x = requires(1);`` — a plain call is just as valid there as a genuine
    requires-expression used as an operand, so that case also falls back to
    the body check rather than assuming genuine (Codex review, second
    round).

    A requires-*clause* with a parenthesized constraint directly
    continuing its own ``template<...>`` header on the *same* line
    (``template<class T> requires (sizeof(T) > 4) void f(T);``) has no
    trailing ``{`` body — a clause is not an expression — so without this
    check it would be misjudged as a plain pre-C++20 call by the body-check
    fallback below, the same way a bare trailing ``requires`` on its own
    line already falls back to *prev_nonblank_code* for this. Checked only
    *after* the member-access exclusion, since ``->`` itself ends in ``>``
    and must not be mistaken for a template header's closing angle bracket
    (Codex review, third round; regression caught locally before commit).
    Mirrors :func:`_looks_like_genuine_concept`'s identical same-line
    check.

    A parenthesized requires-clause can equally *trail* a function's
    declarator (``void f(T) requires (sizeof(T) > 4);``, or after cv/ref/
    ``noexcept`` specifiers — ``void f(T) const noexcept requires
    (sizeof(T) > 4);``, or a trailing return type — ``auto f(T) -> int
    requires (sizeof(T) > 4);``), which the body-check fallback below
    cannot recognize on its own — a clause has no body to confirm (Codex
    review, two rounds). :func:`_strip_trailing_declarator_specifiers`
    traces the prefix back through cv/ref/``noexcept`` specifiers to the
    parameter list's own closing ``)``; a ``->`` anywhere in what remains
    signals a trailing return type instead — the same unambiguous
    positional signals already used in
    :func:`_looks_like_genuine_requires_clause`."""
    prefix = lookahead[: match.start()].rstrip()
    if not prefix:
        prev = _strip_trailing_declarator_specifiers(prev_nonblank_code.rstrip())
        if prev[-1:] in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
            return True
        return not (
            prev.endswith(b">")
            or prev.endswith(b")")
            or _has_declarator_adjacent_trailing_arrow(prev)
        )
    if prefix[-1:] in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
        return True
    if prefix.endswith(b".") or prefix.endswith(b"->") or prefix.endswith(b"::"):
        return True
    stripped = _strip_trailing_declarator_specifiers(prefix)
    if (
        stripped.endswith(b">")
        or stripped.endswith(b")")
        or _has_declarator_adjacent_trailing_arrow(stripped)
    ):
        return False
    m = _TRAILING_IDENTIFIER_PATTERN.search(prefix)
    if m is not None and m.group(1) not in _REQUIRES_EXPR_SAFE_PRECEDING_WORDS:
        return True
    return not _requires_match_has_body(lookahead, match)


def _looks_like_genuine_concept(
    lookahead: bytes,
    match: re.Match[bytes],
    prev_nonblank_code: bytes,
    concept_type_shadowed: bool,
) -> bool:
    """True only if the concept-declaration candidate is actually preceded
    by a ``template<...>`` header's closing ``>`` — either earlier on the
    same (possibly lookahead-joined) line, or as the last thing on the
    previous non-blank code line when "concept" itself starts this one. A
    concept-name is always declared bare, directly after its own
    ``template<...>`` header, so requiring this positive signal (rather
    than merely excluding a ``::`` prefix) is what actually distinguishes a
    genuine declaration from "concept" being used as an ordinary pre-C++20
    identifier anywhere else in a statement (Codex review: excluding only
    ``::`` still missed a plain, unqualified pre-C++20 use like
    ``static concept C = {};``).

    Even with a preceding template header, "concept" only became a
    reserved keyword in C++20 — a pre-C++20 header can legally declare a
    type literally named "concept" and use it in an ordinary *variable
    template* (``template<class T> concept C = {};``, valid since
    C++14), which has the identical textual shape as a genuine concept
    definition (Codex review, several rounds). No per-initializer-shape
    check can be complete — the variable template's initializer can be
    *any* expression convertible to the shadowing type, not just a
    brace-init-list (e.g. ``struct concept { concept(int); }; ...
    concept C = 1;`` via a converting constructor) — so *whenever this
    header defines "concept" as a real type anywhere*
    (``concept_type_shadowed``), every bare ``concept NAME = ...`` match
    in it is ambiguous and rejected outright, regardless of what follows
    "="."""
    if concept_type_shadowed:
        return False
    same_line_prefix = lookahead[: match.start()].rstrip()
    if same_line_prefix.endswith(b">"):
        return True
    if not same_line_prefix:
        return prev_nonblank_code.rstrip().endswith(b">")
    return False


def _looks_like_genuine_requires_clause(
    lookahead: bytes,
    match_start: int,
    prev_nonblank_code: bytes,
    requires_type_shadowed: bool,
) -> bool:
    """True only if the requires-*clause* candidate (the bare, non-
    parenthesized ``requires Foo<T>`` form matched by
    ``_CPP20_REQUIRES_CLAUSE_PATTERN``) is actually preceded by a
    ``template<...>`` header's closing ``>`` — mirrors
    :func:`_looks_like_genuine_concept` exactly, for the same reason: a
    plain pre-C++20 declaration using "requires" as an ordinary type/
    variable name (``struct requires {}; requires value;`` — declaring a
    variable of type "requires") has the identical bare
    ``requires\\s+\\w`` shape as a genuine clause, and was previously
    accepted unconditionally by this branch with no declarator check at
    all (Codex review). Unlike the parenthesized/brace-delimited
    requires-expression form (:func:`_looks_like_requires_declarator`), a
    clause has no body to confirm, so the *only* positive signal
    available is the preceding template header — exactly the same
    positive-signal-required design already used for ``concept``.

    That template-header check alone still can't tell a genuine clause
    from a pre-C++20 header that declares a *type* literally named
    "requires" and uses it as a variable template's type
    (``template<class T> requires value = {};`` — the template header's
    closing ``>`` directly precedes "requires" either way) — Codex
    review, sixth round. Once *requires_type_shadowed* confirms "requires"
    names a real type anywhere in the header, every candidate here is
    ambiguous and treated as non-genuine, mirroring
    ``concept_type_shadowed``.

    A *trailing* requires-clause following a function's declarator (
    ``template<class T> void f(T) requires std::integral<T>;``) is
    equally genuine, signaled by the prefix ending in the parameter
    list's closing ``)`` instead — Codex review. This is unambiguous:
    nothing but a trailing specifier (cv/ref-qualifier, ``noexcept``, a
    requires-clause, ...) can follow a function declarator's ``)``
    before the terminating ``;``/``{`` in *any* C++ grammar, pre-C++20
    included — there is no production for a second, unrelated statement
    beginning right there with no separator. Any number of such
    specifiers (``void f(T) const noexcept requires C<T>;``) can sit
    between the ``)`` and the clause — Codex review, second round —
    traced back via :func:`_strip_trailing_declarator_specifiers`.

    A trailing return type (``auto f(T) -> int requires C<T>;``) is the
    same shape once more removed: whatever sits between ``->`` and
    ``requires`` is the return-type expression, itself impossible to
    bound generically, but its mere presence is enough — a bare
    ``requires IDENTIFIER`` directly following *any* token with no
    separator is, by the same invariant as the ``)``/``>`` cases, only
    ever valid pre-C++20 as a two-identifier ``Type Name;`` declaration,
    and that shape requires "requires" to be preceded by *nothing but*
    the type name — never by a ``->`` (which only ever introduces a
    trailing return type or a member access, neither of which can
    itself be the "type name" half of such a declaration) — Codex
    review, third round.

    The ``->`` check must stay scoped to the *current* statement: a bare
    substring search across the whole same-line prefix picks up an
    unrelated earlier statement's ``->`` too (``auto x = p->m; requires
    value;`` — an ordinary pre-C++20 declaration of ``value`` with type
    "requires" — was wrongly classified genuine by an arrow belonging to
    the *previous* statement, forcing ``-std=gnu++20`` and rejecting the
    otherwise-valid header). A statement boundary (``;``/``{``/``}``)
    directly preceding "requires" can never be a genuine clause's
    continuation, so it is excluded first, mirroring
    :func:`_looks_like_requires_declarator`'s identical check — Codex
    review, fourth round. That statement-boundary check alone still
    leaves the *same*-statement case open — an unrelated ``->`` earlier
    in the same expression (``return p->m + requires(1);``, no statement
    boundary between them) — so the arrow check itself now requires the
    ``->`` to be declarator-adjacent via
    :func:`_has_declarator_adjacent_trailing_arrow` rather than a bare
    substring search — Codex review, fifth round."""
    if requires_type_shadowed:
        return False
    same_line_prefix = _strip_trailing_declarator_specifiers(
        lookahead[:match_start].rstrip()
    )
    if same_line_prefix[-1:] in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
        return False
    if (
        same_line_prefix.endswith(b">")
        or same_line_prefix.endswith(b")")
        or _has_declarator_adjacent_trailing_arrow(same_line_prefix)
    ):
        return True
    if not same_line_prefix:
        prev = _strip_trailing_declarator_specifiers(prev_nonblank_code.rstrip())
        if prev[-1:] in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
            return False
        return (
            prev.endswith(b">")
            or prev.endswith(b")")
            or _has_declarator_adjacent_trailing_arrow(prev)
        )
    return False


_STRING_LITERAL_PATTERN = re.compile(rb'"(?:\\.|[^"\\\n])*"')
_CHAR_LITERAL_PATTERN = re.compile(rb"'(?:\\.|[^'\\\n])*'")
# C++11 raw string literal: [prefix]R"delim(...)delim" — the standard
# permits an optional encoding prefix (u8, u, U, L) directly before the R,
# e.g. u8R"(...)"; without it, "\bR" never matches after "u8"/"u"/"U"/"L"
# since both characters are \w (no boundary between them), leaving a
# prefixed raw string completely unstripped (Codex review). The delimiter
# (d-char-sequence) grammar permits any basic-source character except
# whitespace, parentheses, and backslash — not just identifier characters
# (a delimiter like "tag-" is valid and was missed by an earlier,
# identifier-only version of this pattern (Codex review) — matching the
# exclusion directly is simpler and more complete than enumerating every
# permitted punctuation character. Not handled by _STRING_LITERAL_PATTERN
# (only ordinary "..." literals) or by the plain-comment stripper, so its
# body was otherwise scanned as ordinary code: text that merely *looks*
# like a requires-expression/concept inside a raw string would force
# -std=gnu++20 unnecessarily — worse once a multi-line construct can span
# into a raw string's later lines too. DOTALL so the (non-greedy) body can
# span newlines.
_RAW_STRING_LITERAL_PATTERN = re.compile(
    rb'\b(?:u8|u|U|L)?R"([^\s()\\]{0,16})\((?:.*?)\)\1"', re.DOTALL
)


def _strip_raw_strings(content: bytes) -> bytes:
    """Blank C++11 raw string literals entirely (delimiter and body alike),
    preserving embedded newlines so line numbers reported for code after a
    multi-line raw string stay accurate (mirrors the block-comment
    stripper's newline-preserving approach)."""
    return _RAW_STRING_LITERAL_PATTERN.sub(
        lambda m: b"\n" * m.group(0).count(b"\n"), content
    )


def _strip_literals(line: bytes) -> bytes:
    """Blank out string/char literal contents.

    Prevents a keyword that only appears *inside* a string (e.g. an error
    message like ``"Foo requires Base"``) from being mistaken for C++
    structural syntax.
    """
    line = _STRING_LITERAL_PATTERN.sub(b'""', line)
    line = _CHAR_LITERAL_PATTERN.sub(b"''", line)
    return line


# Newline-tolerant variants of the two patterns above, for use ONLY on a
# chunk that has already been through _iter_logical_lines: that step splices
# away a backslash-newline continuation, embedding a literal "\n" exactly
# where the continuation was — so an ordinary string literal like
# ``"requires \`` + newline + ``{ ... }"`` (a real, if archaic, C/C++
# feature) arrives with the keyword and brace on either side of an embedded
# newline the plain patterns above deliberately refuse to cross (bounding an
# unterminated-literal mismatch to one line is the whole point there). A
# single already-joined logical line has no such risk — any embedded
# newline in it is a genuine continuation, not a boundary into unrelated
# code — so it is safe to let ``.`` span it here (Codex review: this is what
# let requires/concept text trapped inside a continued string literal reach
# the structural pattern match).
_JOINED_STRING_LITERAL_PATTERN = re.compile(rb'"(?:\\.|[^"\\])*"', re.DOTALL)
_JOINED_CHAR_LITERAL_PATTERN = re.compile(rb"'(?:\\.|[^'\\])*'", re.DOTALL)


def _strip_literals_joined(line: bytes) -> bytes:
    """Like :func:`_strip_literals`, but tolerant of an embedded newline —
    use only on output from :func:`_iter_logical_lines`."""
    line = _JOINED_STRING_LITERAL_PATTERN.sub(b'""', line)
    line = _JOINED_CHAR_LITERAL_PATTERN.sub(b"''", line)
    return line


def _strip_literals_crossing_continuations(content: bytes) -> bytes:
    """Like :func:`_strip_literals`, but — unlike it — a literal that spans a
    backslash-newline continuation is still fully blanked, not left behind
    because the plain patterns refuse to cross the embedded newline.

    Safe to call directly on whole-file *content* (unlike
    :func:`_strip_literals_joined`, which additionally tolerates crossing an
    *unrelated* later line and so is only safe on a single already-joined
    logical line): ``\\.`` under ``re.DOTALL`` already consumes a
    continuation's ``\\<newline>`` pair as one escaped character, so the
    match still ends at the literal's real closing quote rather than
    wandering into unrelated later lines. Each replacement preserves the
    literal's embedded newline count (mirrors :func:`_strip_raw_strings`) so
    line numbers reported for code that follows a continued literal stay
    accurate — the plain ``_strip_literals_joined`` replacement (a bare
    ``""``/``''``) would otherwise silently swallow those newlines (Codex
    review: a shadow-name scan run before comment-stripping needs a
    continuation-spanning literal fully blanked, or a fake type name like
    ``struct concept {};`` trapped inside one leaks through and wrongly
    shadows a genuine C++20 declaration elsewhere in the header).
    """
    content = _JOINED_STRING_LITERAL_PATTERN.sub(
        lambda m: b'""' + b"\n" * m.group(0).count(b"\n"), content
    )
    content = _JOINED_CHAR_LITERAL_PATTERN.sub(
        lambda m: b"''" + b"\n" * m.group(0).count(b"\n"), content
    )
    return content


def _iter_logical_lines(content: bytes) -> list[tuple[int, bytes]]:
    """Split *content* into ``(1-based start line, logical line)`` pairs.

    Backslash-newline continuations are joined into a single logical line so
    a ``#define``/``#error`` directive spanning multiple physical lines is
    classified as one directive rather than leaking its continuation lines
    into ordinary code scanning.
    """
    physical = content.split(b"\n")
    logical: list[tuple[int, bytes]] = []
    start_no = 1
    buf: list[bytes] = []
    for i, raw in enumerate(physical, start=1):
        line = raw.rstrip(b"\r")
        if not buf:
            start_no = i
        if line.endswith(b"\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        logical.append((start_no, b"\n".join(buf)))
        buf = []
    if buf:
        logical.append((start_no, b"\n".join(buf)))
    return logical


def _is_preprocessor_directive(line: bytes) -> bool:
    return re.match(rb"^\s*#", line) is not None


#: The construct a :class:`Cpp20Requirement` names. Aliased so
#: :func:`_requirement_kind`, which classifies a line into exactly one of
#: these, is checked against the same closed set the dataclass accepts.
Cpp20RequirementReason = Literal[
    "concept-declaration",
    "requires-expression",
    "requires-clause",
    "constrained-template-parameter",
    "custom-constrained-auto-parameter",
    "abbreviated-function-template-parameter",
    "consteval-declaration",
    "constinit-declaration",
]


@dataclass(frozen=True)
class Cpp20Requirement:
    """A single structural C++20 construct found while scanning headers."""

    reason: Cpp20RequirementReason
    path: str
    line: int


_QUOTED_INCLUDE_PATTERN = re.compile(
    rb'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', re.MULTILINE
)


def _expand_with_quoted_includes(
    header_paths: list[Path], *, for_language_mode_decision: bool = False
) -> list[Path]:
    """Expand *header_paths* with files reachable via a quoted ``#include
    "..."``, resolved relative to the including file's own directory
    (Codex review): a caller often designates just one umbrella entry
    point (``#include "concepts.hpp"`` and nothing else) whose only C++20
    signal actually lives in the included file, not the umbrella file
    itself — castxml/clang parse the transitive include as part of the
    same translation unit regardless of whether it was named directly, so
    the dialect decision must see it too.

    Deliberately narrow, matching this file's incremental-per-reported-
    case scope: only the quoted spelling is followed (an angle-bracket
    ``#include <...>`` is typically a system/toolchain header resolved via
    ``-I`` search paths this heuristic doesn't have access to, not a
    project header); resolution checks only the including file's own
    directory (the first location the standard's quoted-include search
    always checks), not any ``-I`` search path. Cycle-safe (visited by
    resolved absolute path) and silently skips an unreadable file or an
    include that doesn't resolve to a real file on disk — the same
    conservative, best-effort spirit as the rest of this scan.

    The ``#include`` line itself is only followed if it is *reachable*
    under the same preprocessor-guard reasoning the rest of this file
    already applies (Codex review, twenty-seventh round): a naive raw-text
    scan for the directive would follow it even when it sits inside an
    inactive or (for ``for_language_mode_decision``) C++-only guard --
    e.g. an otherwise-C header wrapping ``#include "cxx20.hpp"`` in
    ``#ifdef __cplusplus`` -- wrongly pulling the included file's C++20
    syntax into scope for a decision where that guard is actually false.
    ``for_language_mode_decision`` is forwarded unchanged to
    ``_strip_inactive_if_zero_blocks`` (matching the polarity the caller
    uses for its own reachability scan) before searching for the
    directive, so an include line blanked as unreachable there is never
    followed here either.

    Two more Codex-review fixes narrow this scan further, in opposite
    directions: a backslash-newline-continued directive (``#include``
    followed by a trailing backslash, then the filename on the next
    physical line -- valid C/C++, spliced away in translation phase 2
    before the preprocessor ever sees an ``#include`` token split across
    two lines) previously wasn't recognized at all, since the pattern only
    matches within one physical line -- continuations are spliced away
    first so the directive is joined back onto one line before matching.
    And a raw string literal's
    body is blanked before matching (unlike an *ordinary* string literal,
    deliberately left alone above so a genuine include's own argument
    survives) so a ``#include "..."``-looking line trapped inside one
    (never a real directive) is never mistaken for one.

    Continuations are spliced *before* comments are stripped, matching
    genuine translation-phase order (phase 2 splicing precedes phase 3
    comment recognition) -- Codex review, further round: a ``//`` line
    comment ending in a trailing backslash extends over its continuation
    too, so a ``// comment`` with a trailing backslash followed by
    ``#include "cxx20.hpp"`` on the next physical line is really one
    comment covering both lines, and the include inside it is never
    live. Stripping comments first (only within one physical line,
    unaware of the continuation) would instead
    leave that second line looking like a live, unmasked directive.
    """
    seen: set[Path] = set()
    expanded: list[Path] = []
    stack = list(header_paths)
    while stack:
        p = stack.pop(0)
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        expanded.append(p)
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Splice backslash-newline continuations first (Codex review):
        # joins a directive spanning one, e.g. ``#include \`` followed by
        # ``"concepts.hpp"`` on the next physical line, back onto a single
        # physical line the pattern below can match -- and, done before
        # comment stripping, correctly extends a ``//`` comment ending in
        # a trailing backslash over its continuation line too.
        content = re.sub(rb"\\\r?\n", b"", content)
        # Raw string literals only -- NOT ordinary string/char literals,
        # unlike the main scan's preprocessing pipeline: an ordinary
        # literal has the exact same lexical shape (a bare double-quote)
        # as a genuine #include's argument, so blanking it here would
        # destroy the include target this loop needs to read next. A raw
        # string's delimiter (``R"delim(...)delim"``) is unambiguous and
        # never collides with ``#include "..."``, so it's safe to strip
        # -- and necessary, so a fake ``#include "x"``-looking line
        # trapped inside a raw string's body is never mistaken for a real
        # directive (Codex review).
        content = _strip_raw_strings(content)
        # Comments next.
        content = re.sub(
            rb"/\*.*?\*/",
            lambda m: b"\n" * m.group(0).count(b"\n"),
            content,
            flags=re.DOTALL,
        )
        content = re.sub(rb"//[^\n]*", b"", content)
        reachable_content = _strip_inactive_if_zero_blocks(
            content, mask_cplusplus_defined_guards=for_language_mode_decision
        )
        for m in _QUOTED_INCLUDE_PATTERN.finditer(reachable_content):
            name = m.group(1).decode("utf-8", "surrogateescape")
            included = p.parent / name
            if included.is_file():
                stack.append(included)
    return expanded


@dataclass(frozen=True)
class _Cpp20ShadowFlags:
    """Whether each C++20 keyword also names an ordinary type in the aggregate.

    ORed across *every* header, not computed per file: *header_paths* is the
    whole aggregate castxml/clang parses as a single translation unit, so a
    pre-C++20 compatibility type shadowing one of these keywords can live in a
    shared ``compat.hpp`` while the ambiguous bare use sits in ``api.hpp`` — a
    per-file check would see only the latter and wrongly force C++20 mode on
    the whole aggregate (Codex review, sixth round).
    """

    concept: bool = False
    requires: bool = False
    consteval: bool = False
    constinit: bool = False


def _preprocessed_header_content(
    path: Path, *, for_language_mode_decision: bool
) -> tuple[bytes, bytes] | None:
    """``(scan_content, shadow_scan_content)`` for one header, or ``None``.

    Raw string literals are blanked first — their body can contain arbitrary
    quotes/backslashes that would otherwise confuse the ordinary string-literal
    stripper. Then string/char literals, so a literal containing comment-like
    text (``"/* not a comment */"``) is never mistaken for a real comment;
    that pass is backslash-newline-continuation-tolerant (Codex review) so a
    literal split across a continuation cannot leave its trapped text — e.g. a
    fake ``struct concept {};`` inside an error message — visible to the shadow
    scan. Block comments are replaced by their own newline count so
    later-reported line numbers stay accurate (CodeRabbit review).

    The two returned copies differ only in how dialect-fallback guards are
    masked, and that difference is load-bearing (Codex review, nineteenth
    round). The shadow scan asks "does ``concept`` name an ordinary type in
    code still reachable *if C++20 were chosen*", so a ``struct concept {};``
    shim confined to ``#if __cplusplus < 202002L`` — content that goes away
    once C++20 is chosen — must not count; for the requirements scan that same
    guarded arm is the unconditionally-relevant one.
    """
    try:
        content = path.read_bytes()
    except OSError:
        return None
    content = _strip_raw_strings(content)
    content = _strip_literals_crossing_continuations(content)
    content = re.sub(
        rb"/\*.*?\*/",
        lambda m: b"\n" * m.group(0).count(b"\n"),
        content,
        flags=re.DOTALL,
    )
    # A separate, additionally "//"-line-comment-stripped copy: raw
    # strings/literals/block comments are already blanked above, but "//"
    # comments are only stripped per-logical-line further down, and a
    # "// struct concept {};" comment must never make a *real* concept
    # declaration elsewhere look ambiguous (Codex review, fifth round).
    # #if 0 / #if false regions go too — a disabled compatibility stub must not
    # shadow a genuine keyword used elsewhere (Codex review).
    no_double_slash = re.sub(rb"//[^\n]*", b"", content)
    scan_content = _strip_inactive_if_zero_blocks(
        no_double_slash, mask_cplusplus_defined_guards=for_language_mode_decision
    )
    shadow_content = _strip_inactive_if_zero_blocks(
        no_double_slash, invert_dialect_fallback_guards=False
    )
    return scan_content, shadow_content


def _preprocess_headers(
    header_paths: list[Path], *, for_language_mode_decision: bool
) -> tuple[list[tuple[Path, bytes]], _Cpp20ShadowFlags]:
    """First pass: preprocess every header and OR the shadow flags across all.

    The second pass reuses the returned content rather than re-reading, so the
    per-line scan runs against aggregate-wide flags.
    """
    per_file: list[tuple[Path, bytes]] = []
    concept = requires = consteval = constinit = False
    for path in header_paths:
        prepared = _preprocessed_header_content(
            path, for_language_mode_decision=for_language_mode_decision
        )
        if prepared is None:
            continue
        scan_content, shadow_content = prepared
        per_file.append((path, scan_content))
        concept = concept or bool(
            _CONCEPT_AS_TYPE_NAME_PATTERN.search(shadow_content)
        )
        requires = requires or bool(
            _REQUIRES_AS_TYPE_NAME_PATTERN.search(shadow_content)
        )
        consteval = consteval or bool(
            _CONSTEVAL_AS_TYPE_NAME_PATTERN.search(shadow_content)
        )
        constinit = constinit or bool(
            _CONSTINIT_AS_TYPE_NAME_PATTERN.search(shadow_content)
        )
    return per_file, _Cpp20ShadowFlags(concept, requires, consteval, constinit)


def _joined_lookahead(
    logical_lines: list[tuple[int, bytes]], i: int, code: bytes
) -> bytes:
    """*code* plus any following lines a bare trailing keyword continues onto.

    A bare ``requires``/``concept``/``consteval``/``constinit`` at end of line
    (no parameter list/brace/name yet, or for consteval/constinit no declarator
    at all) means the construct's continuation landed on a following physical
    line with no backslash join — the per-line scan otherwise never sees the
    two halves together (Codex review; the gap applies symmetrically to
    ``concept`` split before its name, not just ``requires`` split before its
    ``(``/``{``/constraint, and equally to consteval/constinit split before
    their own declarator, e.g. ``consteval\nint f();`` — Codex review, second
    round). Bounded, so a stray trailing keyword in unrelated code cannot scan
    unboundedly.
    """
    lookahead = code
    j = i
    budget = 5
    n = len(logical_lines)
    while (
        budget > 0
        and re.search(
            rb"\b(?:requires|concept|consteval|constinit)\s*$", lookahead.rstrip()
        )
        and j + 1 < n
        and not _is_preprocessor_directive(logical_lines[j + 1][1])
    ):
        j += 1
        nxt = _strip_literals_joined(logical_lines[j][1]).split(b"//")[0]
        lookahead += b"\n" + nxt
        budget -= 1
    return lookahead


def _requirement_kind(
    lookahead: bytes, prev_nonblank_code: bytes, shadows: _Cpp20ShadowFlags
) -> Cpp20RequirementReason | None:
    """The C++20 construct this line requires, or ``None``. First match wins."""
    concept_match = _CPP20_CONCEPT_PATTERN.search(lookahead)
    if concept_match and _looks_like_genuine_concept(
        lookahead, concept_match, prev_nonblank_code, shadows.concept
    ):
        return "concept-declaration"
    requires_expr_match = _CPP20_REQUIRES_EXPR_PATTERN.search(lookahead)
    if requires_expr_match and not _looks_like_requires_declarator(
        lookahead, requires_expr_match, prev_nonblank_code
    ):
        return "requires-expression"
    clause_match = _CPP20_REQUIRES_CLAUSE_PATTERN.search(lookahead)
    if clause_match and _looks_like_genuine_requires_clause(
        lookahead, clause_match.start(), prev_nonblank_code, shadows.requires
    ):
        return "requires-clause"
    if _has_constrained_param_syntax(lookahead):
        return "constrained-template-parameter"
    if _has_custom_constrained_auto_param(lookahead, prev_nonblank_code):
        return "custom-constrained-auto-parameter"
    if _has_abbreviated_unconstrained_auto_param(lookahead):
        return "abbreviated-function-template-parameter"
    if not shadows.consteval and _CPP20_CONSTEVAL_PATTERN.search(lookahead):
        return "consteval-declaration"
    if not shadows.constinit and _CPP20_CONSTINIT_PATTERN.search(lookahead):
        return "constinit-declaration"
    return None


def _scan_header_for_requirements(
    path: Path, content: bytes, shadows: _Cpp20ShadowFlags
) -> list[Cpp20Requirement]:
    """Second pass: the per-logical-line scan of one already-preprocessed header.

    Scans the same ``#if 0``-stripped content the shadow checks use — a genuine
    consteval/constinit/concept/requires construct written only inside a
    disabled ``#if 0`` block must not mark the header as needing C++20 (Codex
    review): it is never actually compiled.
    """
    found: list[Cpp20Requirement] = []
    logical_lines = _iter_logical_lines(content)
    # Last non-blank line's own (un-extended) code, tracked across iterations --
    # lets a concept-declaration candidate look backward for its template<...>
    # header when "concept" itself starts a line (see
    # _looks_like_genuine_concept).
    prev_nonblank_code = b""
    for i, (start_no, logical) in enumerate(logical_lines):
        if _is_preprocessor_directive(logical):
            continue
        code = _strip_literals_joined(logical).split(b"//")[0]
        kind = _requirement_kind(
            _joined_lookahead(logical_lines, i, code), prev_nonblank_code, shadows
        )
        if kind is not None:
            found.append(Cpp20Requirement(kind, str(path), start_no))
        if code.strip():
            prev_nonblank_code = code
    return found


def _find_cpp20_requirements(
    header_paths: list[Path], *, for_language_mode_decision: bool = False
) -> list[Cpp20Requirement]:
    """Scan *header_paths* for structural C++20 syntax, with reasons/locations.

    Conservative and directive/literal/comment-aware: only definition-site
    syntax in actual code counts, never the same keywords appearing inside a
    preprocessor diagnostic message, a string/char literal, or a comment.

    Two passes over *header_paths*, not one: *header_paths* is the whole
    aggregate header set castxml/clang parses together as a single
    translation unit, so a pre-C++20 compatibility type shadowing
    "concept"/"requires"/"consteval"/"constinit" (Codex review) can live in
    one file (a shared ``compat.hpp``) while the ambiguous bare use sits in
    another (``api.hpp``) — a per-file shadow check would see only
    ``api.hpp``'s own content and miss it, wrongly forcing C++20 mode on
    the whole aggregate (Codex review, sixth round). The first pass reads
    and preprocesses every file and ORs each shadow flag across all of
    them; the second reuses that same preprocessed content to run the
    per-line scan against the now aggregate-wide flags.

    ``for_language_mode_decision`` (default ``False``) must be set by the
    one caller that uses this scan's result to decide whether an
    auto-detected header is C or C++ **at all**, not just which C++
    dialect (Codex review, twenty-first round): it masks
    ``#if __cplusplus``/``#ifdef __cplusplus``/``defined(__cplusplus)``
    -guarded content (see ``_strip_inactive_if_zero_blocks``'s
    ``mask_cplusplus_defined_guards``) instead of treating it as an
    always-true, unmasked signal — that assumption only holds once C++
    mode is already chosen, and is exactly backwards for the decision of
    whether to choose it in the first place.

    *header_paths* is expanded with any file reachable via a quoted
    ``#include "..."`` before scanning (Codex review, twenty-sixth
    round) — see :func:`_expand_with_quoted_includes`. castxml/clang
    parse a transitively-included header as part of the same
    translation unit regardless of whether the caller named it directly,
    so a C++20 signal that lives only in an included file (a common
    umbrella-header shape) must count too. The expansion is given the
    same ``for_language_mode_decision`` polarity (Codex review,
    twenty-seventh round) so an ``#include`` confined to a guard that is
    unreachable under *this* decision's reasoning (e.g. ``#ifdef
    __cplusplus`` in a C-mode-decision scan) is not followed either.
    """
    per_file, shadows = _preprocess_headers(
        _expand_with_quoted_includes(
            header_paths, for_language_mode_decision=for_language_mode_decision
        ),
        for_language_mode_decision=for_language_mode_decision,
    )
    found: list[Cpp20Requirement] = []
    for path, content in per_file:
        found.extend(_scan_header_for_requirements(path, content, shadows))
    return found


def _detect_cpp20_headers(
    header_paths: list[Path], *, for_language_mode_decision: bool = False
) -> bool:
    """Return True if any header contains structural C++20-only syntax
    (concept/requires, a constrained or abbreviated function template
    parameter, ``consteval``, or ``constinit``).

    Used to decide whether to pass ``-std=gnu++20`` to castxml. castxml's
    default standard is whatever the underlying compiler defaults to
    (usually C++17 on modern gcc), which does not accept ``concept``
    declarations. This detection is conservative: only definition-site
    syntax counts, not the keyword in arbitrary text — see
    ``_find_cpp20_requirements`` for the directive/literal/comment-aware scan.

    ``for_language_mode_decision`` — see ``_find_cpp20_requirements``, to
    which it's passed through unchanged. Must be set by the one caller
    that uses this result to decide C vs. C++ auto-detection itself, not
    just the C++ dialect.
    """
    return bool(
        _find_cpp20_requirements(
            header_paths, for_language_mode_decision=for_language_mode_decision
        )
    )
