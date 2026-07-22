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
    rb"weakly_incrementable|incrementable|input_or_output_iterator|"
    rb"sentinel_for|input_iterator|output_iterator|forward_iterator|"
    rb"bidirectional_iterator|random_access_iterator|contiguous_iterator"
)
_CPP20_STD_RANGES_CONCEPT_NAMES = (
    rb"range|borrowed_range|sized_range|view|input_range|output_range|"
    rb"forward_range|bidirectional_range|random_access_range|"
    rb"contiguous_range|common_range|viewable_range|constant_range"
)
_CPP20_CONSTRAINED_TEMPLATE_PARAM_PATTERN = re.compile(
    rb"\bstd::(?:" + _CPP20_STD_CONCEPT_NAMES + rb")\b\s*(?:<[^<>]*>)?\s+\w+\s*[,>]"
)  # template <std::integral T>  /  template <std::convertible_to<int> T, ...>
_CPP20_CONSTRAINED_TEMPLATE_PARAM_RANGES_PATTERN = re.compile(
    rb"\bstd::ranges::(?:"
    + _CPP20_STD_RANGES_CONCEPT_NAMES
    + rb")\b\s*(?:<[^<>]*>)?\s+\w+\s*[,>]"
)  # template <std::ranges::range R>

# Abbreviated function templates (Codex review): a constrained parameter can
# also appear directly in a function's *parameter list* with no
# ``template<...>`` header at all — ``void f(std::integral auto x);`` is
# exactly equivalent to ``template<std::integral T> void f(T x);``. Unlike
# the template-parameter-list form above (which needs the trailing
# ","/">" disambiguation since a bare identifier there could be an NTTP
# name), a concept name directly followed by the literal keyword ``auto``
# has no other valid pre-C++20 reading at all — "TypeName auto" is not
# parseable pre-C++20 in any other construct — so no further
# disambiguation is needed here.
_CPP20_ABBREVIATED_CONSTRAINED_PARAM_PATTERN = re.compile(
    rb"\bstd::(?:"
    + _CPP20_STD_CONCEPT_NAMES
    + rb"|ranges::(?:"
    + _CPP20_STD_RANGES_CONCEPT_NAMES
    + rb"))\b\s*(?:<[^<>]*>)?\s+auto\b"
)  # void f(std::integral auto x)  /  void f(std::ranges::range auto&& r)

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
    the same way :func:`_looks_like_genuine_concept` does.

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
    check."""
    prefix = lookahead[: match.start()].rstrip()
    if not prefix:
        return not prev_nonblank_code.rstrip().endswith(b">")
    if prefix[-1:] in _REQUIRES_STATEMENT_BOUNDARY_CHARS:
        return True
    if prefix.endswith(b".") or prefix.endswith(b"->") or prefix.endswith(b"::"):
        return True
    if prefix.endswith(b">"):
        return False
    m = _TRAILING_IDENTIFIER_PATTERN.search(prefix)
    if m is not None and m.group(1) not in _REQUIRES_EXPR_SAFE_PRECEDING_WORDS:
        return True
    return not _requires_match_has_body(lookahead, match)


def _looks_like_genuine_concept(
    lookahead: bytes, match_start: int, prev_nonblank_code: bytes
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
    ``static concept C = {};``)."""
    same_line_prefix = lookahead[:match_start].rstrip()
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
    positive-signal-required design already used for ``concept``."""
    same_line_prefix = lookahead[:match_start].rstrip()
    if same_line_prefix.endswith(b">"):
        return True
    if not same_line_prefix:
        return prev_nonblank_code.rstrip().endswith(b">")
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


@dataclass(frozen=True)
class Cpp20Requirement:
    """A single structural C++20 construct found while scanning headers."""

    reason: Literal[
        "concept-declaration",
        "requires-expression",
        "requires-clause",
        "constrained-template-parameter",
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
        logical_lines = _iter_logical_lines(content)
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
            # A bare "requires" or "concept" trailing at the end of a line
            # (no parameter list/brace/name yet) means the construct's
            # continuation landed on a following physical line with no
            # backslash join in between — the per-line scan otherwise never
            # sees the two halves together (Codex review; the same gap
            # applies symmetrically to "concept" split before its name, not
            # just "requires" split before its "("/"{"/constraint). Pull in
            # subsequent non-directive lines (bounded, so a stray trailing
            # keyword in unrelated code can't scan unboundedly) until the
            # bare-trailing condition no longer holds.
            lookahead = code
            j = i
            lookahead_budget = 5
            while (
                lookahead_budget > 0
                and re.search(rb"\b(?:requires|concept)\s*$", lookahead.rstrip())
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
                lookahead, concept_match.start(), prev_nonblank_code
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
            elif (
                _CPP20_CONSTRAINED_TEMPLATE_PARAM_PATTERN.search(lookahead)
                or _CPP20_CONSTRAINED_TEMPLATE_PARAM_RANGES_PATTERN.search(lookahead)
                or _CPP20_ABBREVIATED_CONSTRAINED_PARAM_PATTERN.search(lookahead)
            ):
                found.append(
                    Cpp20Requirement("constrained-template-parameter", str(p), start_no)
                )
            if code.strip():
                prev_nonblank_code = code
    return found


def _detect_cpp20_headers(header_paths: list[Path]) -> bool:
    """Return True if any header contains C++20-only syntax (concept/requires).

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
