# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""AST cache-key, language detection, and CastXML command helpers."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ._compiler_options import has_explicit_std
from .header_utils import iter_cache_header_files


def _cache_key(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    backend: str = "castxml",
    system_includes: tuple[str, ...] = (),
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_identity: str = "",
    compiler_identity: str = "",
) -> str:
    h = hashlib.sha256()
    h.update(f"backend={backend}".encode())
    h.update(f"frontend_identity={frontend_identity}".encode())
    h.update(f"compiler_identity={compiler_identity}".encode())
    for p in sorted(str(x.resolve()) for x in headers):
        h.update(p.encode())
        try:
            h.update(str(os.path.getmtime(p)).encode())
        except OSError:
            pass
    # Also hash mtimes of files in the include dirs (catches most transitive
    # changes). extra_hash_dirs are dirs searched via *deferred* -isystem tokens
    # (the inferred -H roots when a build context is present) rather than -I, so
    # their contents must be folded in here too — otherwise an edit to a header
    # transitively included from such a root would reuse a stale AST (Codex).
    for inc_dir in sorted(str(x) for x in (*extra_includes, *extra_hash_dirs)):
        inc_path = Path(inc_dir)
        h.update(inc_dir.encode())
        if inc_path.is_dir():
            # Hash every header-like file (incl. .inl/.tcc template bodies, not
            # just .h/.hpp) so any transitive include edit busts the key (#454).
            for f in iter_cache_header_files(inc_path):
                try:
                    h.update(str(f).encode())
                    h.update(str(f.stat().st_mtime).encode())
                except OSError:
                    pass
    h.update(compiler.encode())
    # Include toolchain parameters so different cross-compilation configs
    # produce distinct cache entries
    h.update(f"gcc_path={gcc_path or ''}".encode())
    h.update(f"gcc_prefix={gcc_prefix or ''}".encode())
    h.update(f"gcc_options={gcc_options or ''}".encode())
    h.update(f"gcc_option_tokens={chr(0).join(gcc_option_tokens)}".encode())
    h.update(f"sysroot={sysroot or ''}".encode())
    h.update(f"nostdinc={nostdinc}".encode())
    h.update(f"lang={lang or ''}".encode())
    # Auto-probed system include dirs (castxml↔clang parity): a host-toolchain
    # change must invalidate a cached clang dump (the resolved libstdc++ moved).
    h.update(f"system_includes={chr(0).join(system_includes)}".encode())
    return h.hexdigest()


# C++ file extensions that unambiguously indicate C++ content.
_CPP_EXTENSIONS = frozenset({".hpp", ".hxx", ".hh", ".h++", ".tpp"})

# ``extern "C"`` is special: it appears in *valid C* headers (guarded by
# ``#ifdef __cplusplus``), so its presence means "castxml parses in C++ mode" but
# does NOT mean the header *requires* C++. It is kept out of _CPP_ONLY_PATTERNS so
# the C→C++ retry (G16/A3) is never triggered by it — a guarded ``extern "C"``
# header that fails in C mode failed for a real reason, and retrying as C++ would
# skip the ``#ifndef __cplusplus`` branches and mask that error (Codex review).
_EXTERN_C_PATTERN = re.compile(rb'^\s*extern\s+"C"')

# Genuinely C++-only constructs: a *valid C* header cannot contain these, so they
# are a reliable signal that ``--lang c`` was mis-specified and a C++ retry is the
# right degrade. Match actual declarations, not keywords in comments (applied
# line-by-line to non-comment lines).
_CPP_ONLY_PATTERNS = [
    re.compile(rb"^\s*class\s+\w+\s*[:{]"),  # class Foo { / class Foo :
    re.compile(rb"^\s*namespace\s+\w+"),  # namespace ns
    re.compile(rb"^\s*template\s*<"),  # template<...>
    re.compile(rb"^\s*using\s+\w+\s*="),  # using alias = ...
    re.compile(rb"^\s*public\s*:"),  # public:
    re.compile(rb"^\s*private\s*:"),  # private:
    re.compile(rb"^\s*protected\s*:"),  # protected:
    # C++ keywords that can appear anywhere in a line (not just at start)
    re.compile(rb"\bvirtual\s+"),  # virtual member functions
    re.compile(rb"(?<!\w)~\w+\s*\("),  # destructor ~Foo()
    re.compile(rb":\s*public\s+\w+"),  # struct Derived : public Base
    re.compile(rb":\s*private\s+\w+"),  # : private Base
    re.compile(rb":\s*protected\s+\w+"),  # : protected Base
    re.compile(rb"\bclass\s+\w+\s*[{;]"),  # class anywhere (forward decl or def)
    re.compile(rb"\bconst\s+\w[\w:]*\s*&"),  # const Type& reference (C++ idiom)
    re.compile(rb"\bstatic_cast\b"),  # C++ cast
    re.compile(rb"\bconstexpr\b"),  # C++ constexpr
    re.compile(rb"\bnullptr\b"),  # C++ nullptr
    re.compile(rb"\bnoexcept\b"),  # C++ noexcept
    re.compile(rb"\boverride\b"),  # C++ override specifier
]

# Full set used for auto language-mode detection (lang unspecified) and the
# failure hint: here ``extern "C"`` *does* count, because castxml always parses in
# a C++-ish mode, so an aggregate including an extern "C" header is built as .hpp.
_CPP_PATTERNS = [_EXTERN_C_PATTERN, *_CPP_ONLY_PATTERNS]


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
# an ordinary type name anywhere in the header.
_CONCEPT_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class)\s+concept\b|\busing\s+concept\s*=|\btypedef\b[^;]*\bconcept\s*;"
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
    rb"\b(?:struct|class)\s+consteval\b|\busing\s+consteval\s*=|\btypedef\b[^;]*\bconsteval\s*;"
)
_CONSTINIT_AS_TYPE_NAME_PATTERN = re.compile(
    rb"\b(?:struct|class)\s+constinit\b|\busing\s+constinit\s*=|\btypedef\b[^;]*\bconstinit\s*;"
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


def _has_abbreviated_unconstrained_auto_param(lookahead: bytes) -> bool:
    """True if *lookahead* contains a bare (unconstrained) ``auto`` used
    directly as an ordinary function's parameter type (``void f(auto
    x);``) — the C++20 abbreviated function template form, distinct from
    the *constrained* form (``std::integral auto x``, handled separately
    by :func:`_has_constrained_param_syntax`), from a generic lambda's
    ``auto`` parameter (``[](auto x) { ... }``), which has been valid
    since C++14 and is excluded via :func:`_is_lambda_param_list_open_paren`
    (Codex review), and from ``decltype(auto)`` (also valid since C++14,
    excluded via :func:`_is_decltype_open_paren` — Codex review, second
    round). Only matches when nothing but an optional cv-qualifier and/or
    attribute-specifier-seq (``[[maybe_unused]] auto x`` — Codex review,
    third round) separates ``auto`` from its enclosing ``(``/``,`` — that
    position is unambiguous: a bare ``auto`` can never be a parameter's
    default-argument expression or any other operand there, only its
    type."""
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
        if not _is_lambda_param_list_open_paren(
            lookahead, open_pos
        ) and not _is_decltype_open_paren(lookahead, open_pos):
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
    lookahead: bytes, match_start: int, prev_nonblank_code: bytes
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


_PP_IF_OPEN_PATTERN = re.compile(rb"^[ \t]*#[ \t]*(?:if|ifdef|ifndef)\b")
_PP_IF_ZERO_PATTERN = re.compile(rb"^[ \t]*#[ \t]*if[ \t]+(?:0+|false)[ \t]*$")
_PP_ELSE_OR_ELIF_PATTERN = re.compile(rb"^[ \t]*#[ \t]*(?:else|elif)\b")
_PP_ENDIF_PATTERN = re.compile(rb"^[ \t]*#[ \t]*endif\b")


def _strip_inactive_if_zero_blocks(content: bytes) -> bytes:
    """Blank out ``#if 0``/``#if false`` regions, nested directives included.

    A disabled compatibility stub (``#if 0\\nstruct consteval {};\\n#endif``)
    must not shadow a genuine ``consteval``/``constinit``/``concept`` keyword
    used as an identifier elsewhere in the header (Codex review) — the
    ``*_type_shadowed`` scan otherwise sees the inactive type definition and
    wrongly treats a real C++20 declaration as ambiguous. Line count is
    preserved so this can be composed with the rest of the shadow scan.

    Only the ``#if 0``/``#if false`` arm itself is masked: an ``#else`` at
    the same nesting level is unconditionally reachable (the guard is
    unconditionally false), and an ``#elif`` there has a condition this
    heuristic can't evaluate — both stop the masking from that line on
    (Codex review) rather than blanking all the way to the matching
    ``#endif``, which previously hid a genuine construct written in the
    active arm of a permanently-false guard. Treating an unevaluated
    ``#elif`` as possibly-active is the conservative direction here: it can
    only cause C++20 mode to be requested when not strictly needed, never
    the reverse (a real C++20 header parsed without it and failing).
    """
    lines = content.split(b"\n")
    out: list[bytes] = []
    depth = 0
    for raw_line in lines:
        # A CRLF source (or a CRLF-normalizing text-mode write, e.g. the
        # test suite's Path.write_text() on Windows) leaves a trailing "\r"
        # on every line after splitting on "\n" alone — which
        # _PP_IF_ZERO_PATTERN's trailing "$" then fails to match, since "\r"
        # isn't in its "[ \t]*" tail (Windows CI regression). Strip it before
        # matching; harmless on genuine LF input.
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
        if depth:
            if _PP_IF_OPEN_PATTERN.match(line):
                depth += 1
                out.append(b"")
                continue
            if _PP_ENDIF_PATTERN.match(line):
                depth -= 1
                out.append(b"")
                continue
            if depth == 1 and _PP_ELSE_OR_ELIF_PATTERN.match(line):
                depth = 0
                out.append(line)
                continue
            out.append(b"")
            continue
        if _PP_IF_ZERO_PATTERN.match(line):
            depth = 1
            out.append(b"")
            continue
        out.append(line)
    return b"\n".join(out)


@dataclass(frozen=True)
class Cpp20Requirement:
    """A single structural C++20 construct found while scanning headers."""

    reason: Literal[
        "concept-declaration",
        "requires-expression",
        "requires-clause",
        "constrained-template-parameter",
        "abbreviated-function-template-parameter",
        "consteval-declaration",
        "constinit-declaration",
    ]
    path: str
    line: int


def _find_cpp20_requirements(header_paths: list[Path]) -> list[Cpp20Requirement]:
    """Scan *header_paths* for structural C++20 syntax, with reasons/locations.

    Conservative and directive/literal/comment-aware: only definition-site
    syntax in actual code counts, never the same keywords appearing inside a
    preprocessor diagnostic message, a string/char literal, or a comment.
    """
    found: list[Cpp20Requirement] = []
    for p in header_paths:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Blank raw string literals first — their body can contain arbitrary
        # quotes/backslashes that would otherwise confuse the ordinary
        # string-literal stripper below.
        content = _strip_raw_strings(content)
        # Blank string/char literals first so a literal containing comment-like
        # text ("/* not a comment */") is never mistaken for a real comment.
        content = _strip_literals(content)
        # Strip real block comments, but preserve the embedded newline count so
        # later-reported line numbers stay accurate for code following a
        # multi-line comment (CodeRabbit review).
        content = re.sub(
            rb"/\*.*?\*/",
            lambda m: b"\n" * m.group(0).count(b"\n"),
            content,
            flags=re.DOTALL,
        )
        # Whether this header defines "concept" as an ordinary type name
        # anywhere (Codex review, fourth round) — a pre-C++20 header can
        # declare a type literally named "concept" and initialize a
        # variable template of that type via *any* expression convertible
        # to it (not just a brace-init-list, which only covered the
        # aggregate-init case), so no per-initializer-shape check can be
        # complete. Once "concept" is confirmed to name a real type in
        # this header, every bare "concept NAME = ..." match in it is
        # ambiguous and treated as non-genuine — see
        # ``_looks_like_genuine_concept``. Checked against a *separate*,
        # additionally "//"-line-comment-stripped copy of the content
        # (raw strings/literals/block comments are already blanked in
        # ``content`` itself at this point, but not "//" comments, which
        # are only stripped per-logical-line further below) — a
        # "// struct concept {};" comment must never make a *real*
        # concept declaration elsewhere in the header look ambiguous
        # (Codex review, fifth round).
        # Also strip out #if 0/#if false regions before any of the three
        # shadow scans below — a disabled compatibility stub must not shadow
        # a genuine keyword used elsewhere in the header (Codex review).
        _content_no_line_comments = _strip_inactive_if_zero_blocks(
            re.sub(rb"//[^\n]*", b"", content)
        )
        concept_type_shadowed = bool(
            _CONCEPT_AS_TYPE_NAME_PATTERN.search(_content_no_line_comments)
        )
        # Same reasoning and "//"-comment-stripped-copy caveat as
        # concept_type_shadowed above, for "consteval"/"constinit" used as
        # an ordinary pre-C++20 type name (Codex review, third round).
        consteval_type_shadowed = bool(
            _CONSTEVAL_AS_TYPE_NAME_PATTERN.search(_content_no_line_comments)
        )
        constinit_type_shadowed = bool(
            _CONSTINIT_AS_TYPE_NAME_PATTERN.search(_content_no_line_comments)
        )
        # Scan the same #if-0-stripped content the shadow checks above use —
        # a genuine consteval/constinit/concept/requires construct written
        # only inside a disabled #if 0 block must not itself mark the header
        # as needing C++20 (Codex review): it's never actually compiled.
        logical_lines = _iter_logical_lines(_content_no_line_comments)
        n = len(logical_lines)
        # Last non-blank line's own (un-extended) code, tracked across
        # iterations — lets a concept-declaration candidate look backward
        # for its template<...> header when "concept" itself starts a line
        # (see _looks_like_genuine_concept).
        prev_nonblank_code = b""
        for i, (start_no, logical) in enumerate(logical_lines):
            if _is_preprocessor_directive(logical):
                continue
            code = _strip_literals_joined(logical)
            code = code.split(b"//")[0]
            # A bare "requires"/"concept"/"consteval"/"constinit" trailing at
            # the end of a line (no parameter list/brace/name yet, or for
            # consteval/constinit no declarator following at all) means the
            # construct's continuation landed on a following physical line
            # with no backslash join in between — the per-line scan
            # otherwise never sees the two halves together (Codex review;
            # the same gap applies symmetrically to "concept" split before
            # its name, not just "requires" split before its "("/"{"/
            # constraint, and equally to consteval/constinit split before
            # their own declarator, e.g. ``consteval\nint f();`` — Codex
            # review, second round). Pull in subsequent non-directive lines
            # (bounded, so a stray trailing keyword in unrelated code can't
            # scan unboundedly) until the bare-trailing condition no longer
            # holds.
            lookahead = code
            j = i
            lookahead_budget = 5
            while (
                lookahead_budget > 0
                and re.search(
                    rb"\b(?:requires|concept|consteval|constinit)\s*$",
                    lookahead.rstrip(),
                )
                and j + 1 < n
                and not _is_preprocessor_directive(logical_lines[j + 1][1])
            ):
                j += 1
                nxt = _strip_literals_joined(logical_lines[j][1]).split(b"//")[0]
                lookahead += b"\n" + nxt
                lookahead_budget -= 1
            concept_match = _CPP20_CONCEPT_PATTERN.search(lookahead)
            requires_expr_match = _CPP20_REQUIRES_EXPR_PATTERN.search(lookahead)
            if concept_match and _looks_like_genuine_concept(
                lookahead, concept_match, prev_nonblank_code, concept_type_shadowed
            ):
                found.append(Cpp20Requirement("concept-declaration", str(p), start_no))
            elif requires_expr_match and not _looks_like_requires_declarator(
                lookahead, requires_expr_match, prev_nonblank_code
            ):
                found.append(Cpp20Requirement("requires-expression", str(p), start_no))
            elif (
                clause_match := _CPP20_REQUIRES_CLAUSE_PATTERN.search(lookahead)
            ) and _looks_like_genuine_requires_clause(
                lookahead, clause_match.start(), prev_nonblank_code
            ):
                found.append(Cpp20Requirement("requires-clause", str(p), start_no))
            elif _has_constrained_param_syntax(lookahead):
                found.append(
                    Cpp20Requirement("constrained-template-parameter", str(p), start_no)
                )
            elif _has_abbreviated_unconstrained_auto_param(lookahead):
                found.append(
                    Cpp20Requirement(
                        "abbreviated-function-template-parameter", str(p), start_no
                    )
                )
            elif not consteval_type_shadowed and _CPP20_CONSTEVAL_PATTERN.search(
                lookahead
            ):
                found.append(
                    Cpp20Requirement("consteval-declaration", str(p), start_no)
                )
            elif not constinit_type_shadowed and _CPP20_CONSTINIT_PATTERN.search(
                lookahead
            ):
                found.append(
                    Cpp20Requirement("constinit-declaration", str(p), start_no)
                )
            if code.strip():
                prev_nonblank_code = code
    return found


def _detect_cpp20_headers(header_paths: list[Path]) -> bool:
    """Return True if any header contains structural C++20-only syntax
    (concept/requires, a constrained or abbreviated function template
    parameter, ``consteval``, or ``constinit``).

    Used to decide whether to pass ``-std=gnu++20`` to castxml. castxml's
    default standard is whatever the underlying compiler defaults to
    (usually C++17 on modern gcc), which does not accept ``concept``
    declarations. This detection is conservative: only definition-site
    syntax counts, not the keyword in arbitrary text — see
    ``_find_cpp20_requirements`` for the directive/literal/comment-aware scan.
    """
    return bool(_find_cpp20_requirements(header_paths))


def _detect_cpp_headers(
    header_paths: list[Path], patterns: list[re.Pattern[bytes]] = _CPP_PATTERNS
) -> bool:
    """Auto-detect whether headers require C++ compilation mode (FIX-A).

    Returns True if any header has a C++ extension or contains structural
    C++ syntax (class/namespace/template declarations on non-comment lines).

    With the default *patterns* (``_CPP_PATTERNS``) ``extern "C"`` counts as a
    C++ indicator, because castxml always parses in a C++-ish mode and the
    aggregate header must then be built as ``.hpp``. Pass ``_CPP_ONLY_PATTERNS``
    to require a *genuinely C++-only* construct (excluding ``extern "C"``) — used
    by the C→C++ retry so a valid C header is never re-parsed as C++ and have its
    real C-mode error masked (Codex review).
    """
    for p in header_paths:
        if p.suffix.lower() in _CPP_EXTENSIONS:
            return True
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Strip C-style block comments to reduce false positives
        content = re.sub(rb"/\*.*?\*/", b"", content, flags=re.DOTALL)
        for line in content.split(b"\n"):
            # Skip C++ line comments
            stripped = line.split(b"//")[0]
            if any(pat.search(stripped) for pat in patterns):
                return True
    return False


def _resolve_compiler_binary(
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
) -> tuple[str, str]:
    """Resolve the compiler binary and dialect (gnu/msvc) for castxml.

    Returns (cc_bin, cc_id) where cc_id is "gnu" or "msvc".
    """
    _cc_map = {
        "c++": "g++",
        "cc": "gcc",
        "g++": "g++",
        "gcc": "gcc",
        "clang++": "clang++",
        "clang": "clang",
    }

    if gcc_path:
        cc_bin = gcc_path
    elif gcc_prefix:
        suffix = "g++" if compiler in ("c++", "g++", "clang++") else "gcc"
        cc_bin = f"{gcc_prefix}{suffix}"
    else:
        cc_bin = _cc_map.get(compiler, compiler)

    exe_name = Path(cc_bin).name.lower()
    cc_id = "msvc" if exe_name in ("cl", "cl.exe") else "gnu"
    return cc_bin, cc_id


def _build_castxml_command(
    cc_bin: str,
    cc_id: str,
    extra_includes: list[Path],
    out_xml: Path,
    agg_path: Path,
    *,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool = False,
    force_cpp20: bool = False,
    castxml_bin: str = "castxml",
) -> list[str]:
    """Build the castxml command line."""
    # CastXML needs its language-specific compiler-emulation id in C mode.
    # ``gnu`` + ``-x c`` can inject C++ _Float* approximations into C;
    # ``gnu-c`` avoids that. Parentheses preserve an explicit g++ path/prefix.
    castxml_cc_id = "gnu-c" if not force_cpp and cc_id == "gnu" else cc_id
    compiler_command = (
        ["(", cc_bin, "-x", "c", ")"] if castxml_cc_id == "gnu-c" else [cc_bin]
    )
    cmd = [
        castxml_bin,
        "--castxml-output=1",
        f"--castxml-cc-{castxml_cc_id}",
        *compiler_command,
    ]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]

    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += shlex.split(gcc_options, posix=os.name != "nt")
    # Repeatable --gcc-option: each value is one literal compiler argument,
    # appended verbatim (no shlex split) so a flag whose value contains
    # whitespace survives intact and identically on POSIX and Windows.
    cmd += list(gcc_option_tokens)

    explicit_std = has_explicit_std(gcc_options, gcc_option_tokens)
    # Workaround: castxml with --castxml-cc-gnu gcc auto-injects -std=gnu++17
    # which is rejected when parsing a .h file in C mode. Force C mode, but only
    # impose gnu11 when the user did not request a C standard via --gcc-option(s)
    # — otherwise their -std=gnu17/c99 would be overridden by a later flag.
    if not force_cpp and cc_id == "gnu":
        cmd += ["-x", "c"]
        if not explicit_std:
            cmd += ["-std=gnu11"]
    elif force_cpp20 and not explicit_std:
        # Headers contain C++20-only syntax (concept / requires-expression).
        # Castxml's default standard is whatever the host compiler picks
        # (usually C++17 on modern gcc / MSVC), which rejects concepts.
        # Force C++20 unless the caller already supplied an explicit -std=.
        # MSVC uses /std:c++20; gcc/clang use -std=gnu++20.
        if cc_id == "msvc":
            cmd += ["/std:c++20"]
        else:
            cmd += ["-x", "c++", "-std=gnu++20"]

    cmd += ["-o", str(out_xml), str(agg_path)]
    return cmd
