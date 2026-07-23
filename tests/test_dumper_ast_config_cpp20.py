# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regressions for the directive/literal/comment-aware C++20 detector.

``_detect_cpp20_headers`` decides whether castxml needs ``-std=gnu++20``.
The previous implementation matched ``requires``/``concept`` as a naive
per-line regex after only stripping ``/* */`` and ``//`` comments, so it
mistook a ``#error Foo requires Base`` preprocessor diagnostic (or the same
text inside a string literal) for a genuine C++20 requires-clause. See
AGENTS.md task "P0: fix false-positive C++20 auto-detection".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.dumper_ast_config import _detect_cpp20_headers, _find_cpp20_requirements


def _write(tmp_path: Path, name: str, content: str) -> list[Path]:
    p = tmp_path / name
    p.write_text(content)
    return [p]


def test_cpp20_detector_ignores_error_message_requires(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "#error Foo requires Base\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_define_with_requires_in_string(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        '#define MESSAGE "Foo requires Base"\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_string_literal_requires(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        'const char* text = "requires Concept<T>";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_line_comment_requires(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "// requires Concept<T>\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_block_comment_requires(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "/*\n  requires Concept<T>\n*/\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_multiline_directive_continuation(tmp_path):
    # A backslash-continued #define must be treated as one directive, not
    # leak "requires Base" on the continuation line into code scanning.
    headers = _write(
        tmp_path,
        "a.h",
        '#define ASSERT_FOO(x) \\\n    static_assert(x, "requires Base")\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_accepts_template_requires_clause(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> requires Concept<T>\nvoid f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_trailing_requires_clause_after_declarator(
    tmp_path,
):
    """Regression (Codex review): a *trailing* requires-clause following a
    function's declarator (``template<class T> void f(T) requires
    std::integral<T>;``) has its prefix end in the parameter list's
    closing ``)``, not a template header's ``>`` — this is unambiguous,
    since nothing but a trailing specifier can follow a function
    declarator's ``)`` before the terminating ``;``/``{`` in any C++
    grammar, pre-C++20 included."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\ntemplate<class T> void f(T) requires std::integral<T>;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_trailing_requires_clause_on_own_line(
    tmp_path,
):
    """Companion: the trailing-clause form split onto its own line after
    the declarator (empty same-line prefix, falling back to
    prev_nonblank_code ending in ")")."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\n"
        "template<class T>\nvoid f(T)\nrequires std::integral<T>;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_qualified_trailing_requires_clause(tmp_path):
    """Regression (Codex review, second round): a trailing requires-clause
    can follow any number of cv/ref-qualifiers and specifiers (``const``,
    ``noexcept``, ...) between the declarator's ``)`` and the clause
    itself (``void f(T) const noexcept requires std::integral<T>;``) —
    the plain ")"-ending check alone missed these."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\n"
        "template<class T> void f(T) const requires std::integral<T>;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_noexcept_qualified_trailing_requires_clause(
    tmp_path,
):
    """Companion: ``noexcept`` specifically, and a non-template function."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\nvoid f(int) noexcept requires std::integral<int>;\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_accepts_multiline_qualifiers_before_trailing_clause(
    tmp_path,
):
    """Companion: the qualifiers can sit on the same line as the clause
    while the declarator itself is on the previous line — same-line
    prefix strips to empty, falling back to prev_nonblank_code."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\nvoid f(int)\nconst noexcept requires std::integral<int>;\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_accepts_parenthesized_trailing_clause_after_declarator(
    tmp_path,
):
    """Regression (Codex review, second round): a *parenthesized*
    trailing requires-clause (``void f(T) requires (sizeof(T) > 4);``)
    matches the requires-*expression* pattern, not the clause pattern —
    its declarator-check previously only recognized a preceding
    template<...> header (">" ), not a function declarator's ")",  so
    the body-check fallback wrongly rejected it (a clause has no body)."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> void f(T) requires (sizeof(T) > 4);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_accepts_qualified_parenthesized_trailing_clause(
    tmp_path,
):
    """Companion: qualifiers between the declarator and a parenthesized
    trailing clause."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> void f(T) const noexcept requires (sizeof(T) > 4);\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_accepts_trailing_requires_clause_after_return_type(
    tmp_path,
):
    """Regression (Codex review, third round): a trailing requires-clause
    after a trailing return type (``auto f(T) -> int requires
    std::integral<T>;``) has its prefix end in the return-type token, not
    the declarator's ")"/">" directly. The return-type expression itself
    can't be bounded generically, but its mere presence right after a
    "->" is enough: a bare "requires IDENTIFIER" directly following any
    token with no separator is only ever valid pre-C++20 as a two-
    identifier "Type Name;" declaration, and "->" can't itself be that
    type name."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\n"
        "template<class T> auto f(T) -> int requires std::integral<T>;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_parenthesized_clause_after_return_type(
    tmp_path,
):
    """Companion: the parenthesized trailing-clause form after a trailing
    return type."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> auto f(T) -> int requires (sizeof(T) > 4);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_ignores_requires_declaration_after_unrelated_arrow_same_line(
    tmp_path,
):
    """Regression (Codex review, fourth round): a bare trailing-clause
    candidate (``requires value;``) that follows an *unrelated* earlier
    statement containing "->" on the same physical line (a member access,
    not a declarator) must not be classified genuine just because "->"
    appears somewhere earlier on the line. "requires" here is a pre-C++20
    two-identifier declaration ("value" of type "requires"), following a
    statement-terminating ";" — a real trailing clause can never follow a
    statement boundary."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct Foo { int m; };\n"
        "void g(Foo* p) {\n"
        "    auto x = p->m; requires value;\n"
        "}\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_declaration_after_unrelated_arrow_prev_line(
    tmp_path,
):
    """Companion: the same unrelated "->" sits on the *previous* logical
    line (with its own trailing statement after it), and "requires" starts
    fresh with nothing before it on its own line — the fallback to
    ``prev_nonblank_code`` must apply the same statement-boundary check."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct Foo { int m; };\n"
        "void g(Foo* p) {\n"
        "    auto x = p->m; foo();\n"
        "    requires value;\n"
        "}\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_call_after_unrelated_arrow_prev_line(
    tmp_path,
):
    """Companion for the requires-*expression*/declarator path
    (``_looks_like_requires_declarator``): a plain pre-C++20 call to a
    function literally named "requires" starts fresh on its own line, with
    an unrelated "->" on the previous line. With no body and no genuine
    template-header continuation, this must not be classified as a C++20
    requires-expression."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct Foo { int m; };\n"
        "void g(Foo* p) {\n"
        "    auto x = p->m;\n"
        "    requires(1);\n"
        "}\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_call_after_unrelated_arrow_same_statement(
    tmp_path,
):
    """Regression (Codex review, fifth round): an unrelated "->" earlier
    in the *same* statement/expression (not a different one — the
    statement-boundary check does not apply here) must not be mistaken
    for a trailing-return-type arrow either. ``int requires(int);`` is a
    plain pre-C++20 declaration of a function named "requires"; ``return
    p->m + requires(1);`` is an ordinary call to it, added to an unrelated
    member access — the "->" in "p->m" is not adjacent to any function
    declarator's closing ")", so it must not force -std=gnu++20."""
    headers = _write(
        tmp_path,
        "a.h",
        "int requires(int);\nbool g(int* p) {\n    return p->m + requires(1);\n}\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_accepts_trailing_return_with_nested_arrow_in_type(tmp_path):
    """Companion: a genuine trailing-return-type clause whose *return
    type itself* contains a nested arrow (a ``decltype(a->b)*`` return
    type, deliberately not ending in ")" so the plain endswith(")") check
    can't trivially catch it) must still be recognized — the declarator-
    adjacency check walks every "->" occurrence right-to-left rather than
    only the rightmost one (which here belongs to the nested decltype,
    not the genuine trailing-return-type arrow)."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\n"
        "template<class T>\n"
        "auto f(T x) -> decltype(x.p->b)* requires std::integral<T>;\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_accepts_requires_expression(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nconcept HasFoo = requires(T a, T b) { a.foo(b); };\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_accepts_parameterless_requires_expression(tmp_path):
    """Regression (Codex review): a requires-expression with no parameter
    list (``requires { ... }``, as opposed to ``requires(T a) { ... }``)
    matched neither the requires-expression pattern (which required a `(`)
    nor the requires-clause pattern (which required a `\\w` immediately after
    "requires", not a bare `{`) — so headers using only this form were never
    detected as needing ``-std=gnu++20``."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\n"
        "constexpr bool has_value_type = requires { typename T::value_type; };\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_accepts_requires_expression_split_across_lines(tmp_path):
    """Regression (Codex review): the per-logical-line scan never joined
    physical lines that lacked a backslash continuation, so a parameterless
    requires-expression with "requires" at the end of one line and its "{"
    starting the next was invisible to both the requires-expression and
    requires-clause patterns."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\n"
        "constexpr bool has_value_type = requires\n"
        "{ typename T::value_type; };\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_accepts_requires_clause_split_across_lines(tmp_path):
    """Same line-split gap for the requires-*clause* form (a named
    constraint, not a brace-delimited expression)."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nrequires\nConcept<T>\nvoid f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_accepts_parenthesized_requires_clause_on_own_line(tmp_path):
    """Regression (Codex review, fourth round): a requires-clause with a
    parenthesized constraint, starting its own line right after a
    template<...> header (``template<class T>\\nrequires (sizeof(T) > 4)\\n
    void f(T);``), was misclassified as a pre-C++20 call/declarator — the
    same-line prefix is empty (nothing precedes "requires" on its own
    line), which previously always meant "bare call-as-statement" without
    considering the previous line's template context."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nrequires (sizeof(T) > 4)\nvoid f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_accepts_parenthesized_requires_clause_on_same_line(tmp_path):
    """Regression (Codex review, third round): a requires-clause with a
    parenthesized constraint on the *same* line as its ``template<...>``
    header (``template<class T> requires (sizeof(T) > 4) void f(T);``) has
    no trailing ``{`` body — a clause is not an expression — so the
    body-check fallback used for ambiguous operand contexts previously
    misjudged it as a plain pre-C++20 call, and the requires-clause regex
    (``requires\\s+\\w``) doesn't match either since the next token is
    ``(`` rather than a word. Both the requires-expression body-check path
    and the requires-clause path missed this common formatting."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> requires (sizeof(T) > 4) void f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_pointer_member_call_ending_in_angle_bracket(tmp_path):
    """Regression guard: the same-line parenthesized-clause fix (checking
    whether the prefix ends in ``>``) must not fire for ``->`` — the
    pointer-member-access operator also ends in ``>`` and must still be
    excluded by the earlier member-access check, not misread as a
    template header's closing angle bracket."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct S { void requires(int); };\nvoid f(S* x) { x->requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_text_in_raw_string(tmp_path):
    """Regression (Codex review): a C++11 raw string literal (``R"(...)"``)
    is not recognized by ``_strip_literals`` (only ordinary ``"..."``), so
    its body was scanned as ordinary code. Text merely resembling a
    requires-expression inside a raw string must not force -std=gnu++20."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* msg = R"(this text requires\n{ nothing, really })";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_text_in_delimited_raw_string(tmp_path):
    """Same as above, but with an explicit (non-empty) raw-string
    delimiter."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* msg = R"tag(this text requires\n{ nothing })tag";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_accepts_concept_declaration_split_across_lines(tmp_path):
    """Symmetric line-split gap for "concept": a bare "concept" keyword at
    the end of a line, with its name/definition starting the next line and
    no backslash continuation in between, was never joined by the
    lookahead — which previously only triggered on a trailing "requires"."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nconcept\nHasFoo = true;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_ignores_requires_text_in_punctuation_delimited_raw_string(
    tmp_path,
):
    """Regression (Codex review): a raw-string delimiter (d-char-sequence)
    may contain any basic-source character except whitespace, parentheses,
    and backslash — not just identifier characters. An earlier version of
    the raw-string pattern restricted the delimiter to ``[A-Za-z0-9_]``,
    missing a delimiter like ``tag-``."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* msg = R"tag-(this text requires\n{ nothing })tag-";\n',
    )
    assert _detect_cpp20_headers(headers) is False


@pytest.mark.parametrize("prefix", ["u8", "u", "U", "L"])
def test_cpp20_detector_ignores_requires_text_in_prefixed_raw_string(tmp_path, prefix):
    """Regression (Codex review): the raw-string pattern's ``\\bR"`` never
    matched after an encoding prefix (``u8``/``u``/``U``/``L``) since both
    the prefix's last character and "R" are word characters — no boundary
    between them — leaving a prefixed raw string like ``u8R"(...)"``
    completely unstripped."""
    headers = _write(
        tmp_path,
        "a.h",
        f'const char* msg = {prefix}R"(this text requires\n{{ nothing }})";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_real_construct_after_raw_string(tmp_path):
    """A raw string earlier in the file must not swallow a genuine C++20
    construct that follows it."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* msg = R"(just text)";\ntemplate<class T> concept C = true;\n',
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" and r.line == 2 for r in reqs)


def test_cpp20_detector_ignores_requires_text_in_backslash_continued_string(tmp_path):
    """Regression (Codex review): an ordinary string literal continued with
    a real backslash-newline (a valid, if archaic, C/C++ feature) arrives
    at the per-line scan with its backslash already spliced away by
    ``_iter_logical_lines`` and a literal newline in its place. The plain
    string-literal pattern deliberately refuses to match across a newline
    (bounding an unterminated-literal mismatch to one line), so this left
    the continued literal's body — including "requires" and "{" on either
    side of the embedded newline — completely unstripped and visible to the
    requires-expression pattern."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* s = "requires \\\n{ typename T::value_type; }";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_lookahead_stops_at_preprocessor_directive(tmp_path):
    """The line-join lookahead for a bare trailing "requires" must stop at a
    preprocessor directive rather than pulling its text into the scan
    window — the directive line is not a real continuation of the
    requires-expression/clause."""
    headers = _write(
        tmp_path,
        "a.h",
        "bool ok = requires\n#define X 1\n(T a) { a.foo(); };\n",
    )
    reqs = _find_cpp20_requirements(headers)
    assert not any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_ignores_requires_used_as_pre_cxx20_identifier(tmp_path):
    """Regression (Codex review): "requires" only became a reserved keyword
    in C++20 — any earlier standard allows it as an ordinary identifier,
    e.g. a function literally named "requires". Forcing -std=gnu++20 on
    such a header would break it, since the identifier is no longer usable
    there."""
    headers = _write(
        tmp_path,
        "a.h",
        "inline bool requires(int x) { return x > 0; }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_call_to_pre_cxx20_requires_function(tmp_path):
    """Regression (Codex review, second round): a statement-level *call* to
    a pre-C++20 function named "requires" (e.g. ``requires(1);`` inside a
    function body) was still misdetected — the earlier fix only excluded
    "requires(" preceded by a bare identifier (the declaration case), not
    preceded by nothing but a statement boundary like "{" (the call-as-
    statement case)."""
    headers = _write(
        tmp_path,
        "a.h",
        "void requires(int);\ninline void f() { requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_member_call_to_pre_cxx20_requires(tmp_path):
    """Regression (Codex review, third round): a member call to a pre-C++20
    method named "requires" (``x.requires(1);``) was still misdetected —
    "requires" the C++20 keyword is never looked up via member access."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct S { void requires(int); };\nvoid f(S x) { x.requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_pointer_member_call_to_pre_cxx20_requires(tmp_path):
    """Same as above, via ``->`` instead of ``.``."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct S { void requires(int); };\nvoid f(S* x) { x->requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_qualified_call_to_pre_cxx20_requires(tmp_path):
    """Regression (Codex review, third round): a qualified call to a
    pre-C++20 function named "requires" (``ns::requires(1);``) was still
    misdetected — "requires" the C++20 keyword is never qualified this
    way."""
    headers = _write(
        tmp_path,
        "a.h",
        "namespace ns { void requires(int); }\nvoid f() { ns::requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_accepts_return_requires_expression(tmp_path):
    """Non-regression: "return requires(...)" — a requires-expression used
    directly as a return value — must stay detected. "return" is one of the
    few keywords that can legitimately introduce a requires-expression as a
    bare preceding word, unlike an ordinary declarator identifier."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nbool check() { return requires(T t) { t.foo(); }; }\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-expression" for r in reqs)


def test_cpp20_detector_ignores_return_call_to_pre_cxx20_requires(tmp_path):
    """Regression (Codex review, fifth round): "return"/"throw"/"co_return"
    are necessary but not sufficient — ``return requires(1);`` (a plain
    call to a pre-C++20 "requires" function) is just as syntactically
    valid there as a genuine ``return requires(T t) { t.foo(); };``. Only
    the latter carries a requirements body, so the safe-word exception
    must confirm one before accepting."""
    headers = _write(
        tmp_path,
        "a.h",
        "inline bool requires(int);\ninline bool f() { return requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_throw_call_to_pre_cxx20_requires(tmp_path):
    """Same as above, via "throw" instead of "return"."""
    headers = _write(
        tmp_path,
        "a.h",
        "inline bool requires(int);\ninline void f() { throw requires(1); }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_operand_context_call_to_pre_cxx20_requires(tmp_path):
    """Regression (Codex review, sixth round): a call to a pre-C++20
    function named "requires" used as an operand — ``if (requires(1))
    ...`` — has no bare identifier directly preceding it (it's preceded by
    ``(`` instead), which previously made the detector assume genuine
    C++20 syntax unconditionally. A plain call is just as syntactically
    valid as an operand there as a real requires-expression, so this case
    must also fall back to the requirements-body check rather than being
    accepted on the strength of "no declarator identifier precedes it"."""
    headers = _write(
        tmp_path,
        "a.h",
        "inline bool requires(int);\n"
        "inline bool f() { if (requires(1)) return true; return false; }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_accepts_genuine_requires_expression_as_operand(tmp_path):
    """Companion to the above: a genuine requires-expression used as an
    operand (here, assigned to a variable) must still be detected — the
    body-check fallback must not blanket-reject every operand-context use."""
    headers = _write(
        tmp_path,
        "a.h",
        "bool ok = requires (typename T) { typename T::value_type; };\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_bare_declaration_of_type_named_requires(tmp_path):
    """Regression (Codex review, fourth round): "requires" only became a
    reserved keyword in C++20 — a variable declaration using a type
    literally named "requires" (``struct requires {}; requires value;``)
    has the identical bare ``requires\\s+\\w`` shape as a genuine
    requires-clause, but the clause branch previously had no declarator
    check at all — unlike the parenthesized/brace requires-expression
    form. A genuine clause is always preceded by its own template<...>
    header; this declaration is not."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct requires {};\nrequires value;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_std_concept_constrained_template_parameter(tmp_path):
    """Regression (Codex review): a constrained template parameter using a
    standard-library concept in place of typename/class (the abbreviated
    ``template <std::integral T>`` form) is genuine C++20 syntax with no
    "concept"/"requires" keyword anywhere at the use site, so neither
    existing pattern detected it. Scoped to the fixed, well-known
    <concepts>/<iterator>/<ranges> name list rather than any identifier,
    since an arbitrary identifier there is routinely a valid pre-C++20
    non-type template parameter's type."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\ntemplate <std::integral T> void f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_indirectly_readable_constrained_parameter(tmp_path):
    """Regression (Codex review): the standard concept name list omitted
    the ``<iterator>`` ``indirectly_*`` family (``indirectly_readable``,
    ``indirectly_writable``, ``indirectly_swappable``,
    ``indirectly_movable[_storable]``, ``indirectly_copyable[_storable]``,
    ``indirectly_comparable``, ``indirectly_unary_invocable``) and
    ``sized_sentinel_for`` — a header whose only C++20 signal is one of
    these (a `template` constrained on it, no ``concept``/``requires``
    keyword anywhere) was parsed in C++ mode via the bare ``template``
    keyword but without ``-std=gnu++20``, so the concept itself was
    unavailable on a C++17-default toolchain and the L2 scan failed."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <iterator>\ntemplate <std::indirectly_readable I> void f(I);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_std_ranges_concept_constrained_template_parameter(
    tmp_path,
):
    """Regression (Codex review, second round): the constrained-template
    probe only matched bare ``std::`` names, missing the equally common
    ``std::ranges::`` concepts from `<ranges>` (e.g.
    ``template <std::ranges::range R> void f(R&&);``) — a distinct
    namespace from the plain `<concepts>`/`<iterator>` names, not folded
    into the same pattern."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <ranges>\ntemplate <std::ranges::range R> void f(R&&);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_abbreviated_constrained_parameter(tmp_path):
    """Regression (Codex review, third round): a constrained parameter can
    appear directly in a function's parameter list with no
    ``template<...>`` header at all — ``void f(std::integral auto x);`` is
    exactly equivalent to ``template<std::integral T> void f(T x);`` — so
    neither the template-parameter-list pattern (which requires a trailing
    ","/">") nor any other existing probe matched it."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\nvoid f(std::integral auto x);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_abbreviated_ranges_constrained_parameter(
    tmp_path,
):
    """Companion: the abbreviated form also applies to std::ranges::
    concepts (``void f(std::ranges::range auto&& r);``)."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <ranges>\nvoid f(std::ranges::range auto&& r);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_ignores_plain_auto_parameter(tmp_path):
    """Companion: a plain (unconstrained) ``auto`` parameter — valid,
    ordinary C++20 syntax on its own, but not what this specific pattern
    targets — must not be confused with the std::-concept-constrained
    form. (Bare ``auto`` parameters are C++20-only regardless, but this
    guards the abbreviated-constraint pattern's specificity.)"""
    headers = _write(tmp_path, "a.h", "void f(auto x);\n")
    reqs = _find_cpp20_requirements(headers)
    assert not any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_nested_concept_argument(tmp_path):
    """Regression (Codex review, fourth round): the concept argument list
    can itself contain a nested template-id (``std::same_as<std::vector
    <int>>``), which a naive single-level ``(?:<[^<>]*>)?`` cannot match
    since its excluded-character class stops at the first inner ``<``/
    ``>``. The matcher must tolerate arbitrary nesting depth, the same
    way ``_find_matching_close_paren`` already does for parenthesized
    requires-expressions."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\n#include <vector>\n"
        "template <std::same_as<std::vector<int>> T> void f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_ignores_custom_nttp_type_resembling_constrained_param(
    tmp_path,
):
    """Companion to the above: an ordinary non-type template parameter
    using a custom (non-std::) type — a perfectly valid pre-C++20
    construct — must not be mistaken for a constrained template
    parameter just because it has the same "identifier identifier"
    shape."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<ns::Traits::value_type V> void f();\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_constrained_param_with_default(tmp_path):
    """Regression (Codex review): a constrained template parameter can
    carry a default argument (``template <std::integral T = int>``), which
    the bare ``\\w+\\s*[,>]`` tail-check missed entirely since "T" is
    followed by " = int>", not directly by ","/">"."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\ntemplate <std::integral T = int> void f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_detects_constrained_param_pack(tmp_path):
    """Companion: a constrained template parameter *pack*
    (``template <std::integral... Ts>``) — the concept name is followed
    by "..." before the (optional) parameter name, not directly by a
    bare identifier."""
    headers = _write(
        tmp_path,
        "a.h",
        "#include <concepts>\ntemplate <std::integral... Ts> void f(Ts...);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constrained-template-parameter" for r in reqs)


def test_cpp20_detector_ignores_ordinary_nttp_with_default(tmp_path):
    """Companion: an ordinary (non-std::) non-type template parameter with
    a default value — valid pre-C++20 code — must not be mistaken for a
    constrained parameter just because it also has an "=" tail."""
    headers = _write(tmp_path, "a.h", "template<int N = 5> void f();\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_qualified_concept_used_as_pre_cxx20_type(tmp_path):
    """Regression (Codex review): "concept" only became a reserved keyword
    in C++20 — a qualified reference to a type literally named "concept"
    (e.g. ``ns::concept C = {};``) is valid pre-C++20 code. A concept-name
    is always declared bare, directly after its own template<...> header,
    so requiring that positive signal (rather than merely excluding a
    "::" prefix) correctly excludes this qualified case too."""
    headers = _write(
        tmp_path,
        "a.h",
        "namespace ns { struct concept {}; }\nns::concept C = {};\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_unqualified_concept_used_as_pre_cxx20_type(tmp_path):
    """Regression (Codex review, second round): excluding only a "::"
    prefix still missed a plain, unqualified pre-C++20 use of "concept" as
    an identifier, e.g. ``static concept C = {};`` with no template<...>
    anywhere before it — a bare exclusion list can't cover every non-C++20
    context, so the fix requires positive evidence of a preceding
    template<...> header instead."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct concept {};\nstatic concept C = {};\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_variable_template_of_type_named_concept(
    tmp_path,
):
    """Regression (Codex review, third round): even with a preceding
    template<...> header, "concept" only became a reserved keyword in
    C++20 — a pre-C++20 header can declare a type literally named
    "concept" and use it in an ordinary variable template
    (``template<class T> concept C = {};``, valid since C++14), which has
    the identical textual shape as a genuine concept definition. The two
    are distinguishable: a concept's constraint-expression is never just
    a bare brace-init-list."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct concept {};\ntemplate<class T> concept C = {};\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_concept_typed_variable_template_any_initializer(
    tmp_path,
):
    """Regression (Codex review, fourth round): the brace-only exclusion
    above only covered aggregate-initialized variable templates — a type
    literally named "concept" can equally be initialized via *any* other
    expression convertible to it, e.g. a converting constructor
    (``struct concept { concept(int); }; template<class T> concept C =
    1;``). No per-initializer-shape check can be complete, so the
    detector now instead checks whether "concept" is defined as a real
    type *anywhere* in the header and, if so, rejects every bare
    "concept NAME = ..." match in it outright."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct concept { concept(int); };\ntemplate<class T> concept C = 1;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_commented_out_concept_type_shadow(tmp_path):
    """Regression (Codex review, fifth round): the concept-type-shadow
    check was computed before "//" line comments are stripped (only
    raw strings/literals/block comments are blanked at that point) — a
    "// struct concept {};" comment must never make a *real* concept
    declaration elsewhere in the header look ambiguous and get rejected."""
    headers = _write(
        tmp_path,
        "a.h",
        "// struct concept {};\ntemplate<class T> concept C = true;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_still_accepts_template_concept_after_qualified_check(
    tmp_path,
):
    """Non-regression: a genuine concept declaration (preceded by its
    template<...> header on a separate line, not by "::") must stay
    detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T>\nconcept HasFoo = requires(T a, T b) { a.foo(b); };\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_accepts_concept_after_multiline_template_header(tmp_path):
    """Non-regression: a template<...> header wrapped across several
    physical lines (a common formatting style for long parameter lists,
    with no backslash continuation) must still satisfy the preceding-">"
    check on whichever line the header's closing bracket lands on."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<\n    class T\n>\nconcept C = true;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_accepts_inline_concept_declaration(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> concept C = true;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_reports_source_location(tmp_path):
    headers = _write(
        tmp_path,
        "a.h",
        "int x;\ntemplate<class T> concept C = true;\n",
    )
    reqs = _find_cpp20_requirements(headers)
    assert len(reqs) == 1
    assert reqs[0].line == 2
    assert reqs[0].path.endswith("a.h")


def test_cpp20_detector_reports_correct_line_after_multiline_comment(tmp_path):
    """Regression (CodeRabbit review): stripping /* */ comments before line
    splitting used to delete their embedded newlines too, so any reported
    line number after a multi-line block comment was too low relative to
    the real file."""
    headers = _write(
        tmp_path,
        "a.h",
        "/*\n"
        "  a multi-line\n"
        "  block comment\n"
        "*/\n"
        "template<class T> concept C = true;\n",
    )
    reqs = _find_cpp20_requirements(headers)
    assert len(reqs) == 1
    assert reqs[0].line == 5


def test_cpp20_detector_ignores_comment_like_text_in_string(tmp_path):
    """A string literal containing comment-like text ("/* ... */") must not
    be mistaken for a real comment and blindly stripped before the
    literal-aware requires/concept check runs (CodeRabbit review)."""
    headers = _write(
        tmp_path,
        "a.h",
        'const char* s = "/* not a comment */ requires Concept<T>";\n',
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_consteval(tmp_path):
    """Regression (Codex review): a header whose only C++20 signal is a
    ``consteval`` function (no concept/requires anywhere) was previously
    parsed under the pre-C++20 default dialect, where ``consteval`` is not
    a keyword — rejecting an otherwise-valid header."""
    headers = _write(tmp_path, "a.h", "consteval int f() { return 1; }\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_detects_constinit(tmp_path):
    """Companion: ``constinit``."""
    headers = _write(tmp_path, "a.h", "constinit extern int x;\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constinit-declaration" for r in reqs)


def test_cpp20_detector_ignores_consteval_as_ordinary_identifier(tmp_path):
    """Regression (Codex review, second round): neither ``consteval`` nor
    ``constinit`` was a reserved word before C++20, so a pre-C++20 header
    can legally declare a variable literally named "consteval"
    (``int consteval;``). Forcing -std=gnu++20 on such a header breaks it,
    since the identifier is no longer usable there — this is a real
    correctness risk, unlike the deliberately unconditional
    `constexpr`/`noexcept`/`nullptr`/`override` entries in
    `_CPP_ONLY_PATTERNS`, which only ever decide "must be C++", never
    "must be C++20"."""
    headers = _write(tmp_path, "a.h", "int consteval;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_constinit_as_ordinary_identifier(tmp_path):
    """Companion: ``constinit`` as an ordinary identifier."""
    headers = _write(tmp_path, "a.h", "int constinit;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_consteval_as_initialized_identifier(tmp_path):
    """Companion: the identifier form with an initializer
    (``int consteval = 5;``) must not be mistaken for the specifier form
    either — "consteval" here is directly followed by "=", not another
    decl-specifier/declarator token."""
    headers = _write(tmp_path, "a.h", "int consteval = 5;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_consteval_as_parameter_name(tmp_path):
    """Companion: ``consteval`` as an ordinary parameter name
    (``void f(int consteval);``)."""
    headers = _write(tmp_path, "a.h", "void f(int consteval);\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_consteval_with_auto_return_type(tmp_path):
    """Companion: ``consteval`` followed by ``auto`` (rather than a
    concrete type) must still be recognized as genuine — the positive
    lookahead only needs *some* identifier-starting token to follow, not
    a specific one."""
    headers = _write(tmp_path, "a.h", "consteval auto f() { return 1; }\n")
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_consteval_as_shadowed_type_name(tmp_path):
    """Regression (Codex review, third round): the "followed by an
    identifier-starting token" lookahead alone can't distinguish a genuine
    specifier from a pre-C++20 header that instead declares a *type*
    literally named "consteval" (``struct consteval {};``) and later
    references it followed by another decl-specifier or cv-qualifier
    (``consteval const *p;`` — legal pre-C++20: decl-specifier order is
    flexible, so this means the same as ``const consteval *p;``) — the
    textual shape is identical to a genuine ``consteval <type> <name>``
    declaration. Once the header is confirmed to declare "consteval" as a
    type anywhere, every bare occurrence must be treated as ambiguous,
    mirroring the existing "concept" type-shadow check."""
    headers = _write(tmp_path, "a.h", "struct consteval {};\nconsteval const *p;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_constinit_as_shadowed_type_name(tmp_path):
    """Companion: ``constinit`` shadowed as a type name via ``using``."""
    headers = _write(tmp_path, "a.h", "using constinit = int;\nconstinit long x;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_consteval_as_shadowed_typedef_type_name(tmp_path):
    """Companion: shadowed via ``typedef`` rather than ``struct``/``using``."""
    headers = _write(tmp_path, "a.h", "typedef int consteval;\nconsteval unsigned x;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_commented_out_consteval_type_shadow(tmp_path):
    """Companion: a *commented-out* ``struct consteval {};`` must not
    suppress a genuine ``consteval`` declaration elsewhere in the same
    header — mirrors the "concept" shadow check's identical comment
    exclusion."""
    headers = _write(
        tmp_path,
        "a.h",
        "// struct consteval {};\nconsteval int f() { return 1; }\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_inactive_if_zero_consteval_type_shadow(tmp_path):
    """Regression (Codex review): a compatibility stub disabled via
    ``#if 0``/``#endif`` must not suppress a genuine ``consteval``
    declaration elsewhere in the header — mirrors the commented-out-shadow
    exclusion above, but for a preprocessor-inactive region rather than a
    ``//`` comment."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nstruct consteval {};\n#endif\nconsteval int f() { return 1; }\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_inactive_if_zero_consteval_type_shadow_crlf(tmp_path):
    """Regression (Windows CI): the same disabled ``#if 0`` stub, but with
    CRLF line endings (as produced by a CRLF source file, or by a
    text-mode write on Windows). The stripped-region matching must not
    depend on a bare "\\n" terminator — a trailing "\\r" left over from
    splitting on "\\n" alone previously defeated ``_PP_IF_ZERO_PATTERN``'s
    end-anchor, so the stub was never recognized as inactive."""
    p = tmp_path / "a.h"
    p.write_bytes(
        b"#if 0\r\nstruct consteval {};\r\n#endif\r\nconsteval int f() { return 1; }\r\n"
    )
    headers = [p]
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_consteval_declared_only_in_if_zero_block(tmp_path):
    """Regression (Codex review): a genuine ``consteval`` *declaration*
    written only inside a disabled ``#if 0`` block must not itself mark the
    header as needing C++20 — it's never actually compiled. The active code
    outside the block uses "consteval" the pre-C++20 way (an ordinary
    identifier), so the header is really C/pre-C++20 throughout."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_consteval_alongside_if_zero_block(tmp_path):
    """Companion: a genuine *active* ``consteval`` declaration elsewhere in
    the same header must still be detected even when an unrelated ``#if 0``
    block is also present, confirming the stripping doesn't over-suppress."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nconsteval int f();\n#endif\nconsteval int g();\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" and r.line == 4 for r in reqs)


def test_cpp20_detector_still_shadows_active_if_zero_consteval_type(tmp_path):
    """Companion: an *active* (non-``#if 0``) ``struct consteval {};`` must
    still shadow, confirming the ``#if 0`` stripping doesn't over-strip."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\nstruct consteval {};\n#endif\nconsteval const *p;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_active_else_arm_of_if_zero(tmp_path):
    """Regression (Codex review): ``#if 0``'s guard is unconditionally
    false, so its ``#else`` arm is unconditionally reachable — a genuine
    C++20 construct written only there must still be detected. Masking the
    whole ``#if 0``...``#endif`` span (including the active ``#else`` arm)
    previously hid it."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint dummy;\n#else\ntemplate<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" and r.line == 4 for r in reqs)


def test_cpp20_detector_detects_construct_in_else_after_elif(tmp_path):
    """Companion: the same reachable-``#else`` reasoning holds when a
    permanently-false ``#elif 0`` sits between the ``#if 0`` and the
    ``#else`` — the construct in ``#else`` must still be found."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nint b;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_still_ignores_truly_inactive_if_zero_without_else(tmp_path):
    """Companion: an ``#if 0`` block with *no* ``#else`` stays fully masked
    — nothing in it is reachable, so it must not itself trigger detection."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\ntemplate<class T> concept C = true;\n#endif\nint x;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_in_permanently_false_elif(tmp_path):
    """Regression (Codex review, second round): a ``#elif 0``/``#elif
    false`` arm is itself permanently unreachable, exactly like the
    ``#if 0`` guard before it — a construct written only there must stay
    masked, not treated as reachable the way a genuinely unevaluable
    ``#elif <macro>`` condition is. Only the trailing pre-C++20 ``int
    consteval;`` outside the conditional is active."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_elif_after_permanently_false_elif(
    tmp_path,
):
    """Companion: a genuinely unevaluable ``#elif`` after a permanently-false
    ``#elif 0`` must still stop masking and be scanned — the "keep masking
    for elif-0" rule must not swallow a later, truly unknown arm."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nint b;\n#elif SOME_MACRO\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_construct_in_dead_arm_after_elif_true(tmp_path):
    """Regression (Codex review, third round): once an ``#elif 1``/``#elif
    true`` arm fires, it is unconditionally reachable in *every* build
    configuration, which makes every later sibling arm in the same chain
    unconditionally *unreachable* — masking must resume for them, unlike
    after a merely-unevaluated ``#elif <macro>`` condition. A construct
    written only in the dead ``#else`` here must not be detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 1\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_in_dead_arm_after_elif_true_word(tmp_path):
    """Companion: same as above but with the ``true`` spelling."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif true\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_elif_true_arm_itself(tmp_path):
    """Companion: the ``#elif 1`` arm's own content is definitely reachable
    and must still be scanned normally."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 1\nconsteval int f();\n#else\nint consteval;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_still_detects_construct_after_unevaluable_elif_then_else(
    tmp_path,
):
    """Companion: a merely-*unevaluable* ``#elif <macro>`` (as opposed to a
    provably-true ``#elif 1``) must NOT settle the chain — a construct in
    the ``#else`` that follows it is still possibly reachable (the macro
    might be false) and must stay detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif SOME_MACRO\nint b;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_nested_if_zero_constinit_type_shadow(tmp_path):
    """Companion: a nested ``#ifdef`` inside a disabled ``#if 0`` region
    must not confuse the matching-``#endif`` depth tracking and re-activate
    the stub early."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\n#ifdef FOO\nstruct constinit {};\n#endif\n#endif\n"
        "constinit int x = 1;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constinit-declaration" for r in reqs)


def test_cpp20_detector_detects_consteval_split_across_lines(tmp_path):
    """Regression (Codex review, second round): a bare ``consteval``
    trailing at the end of a line, with its declarator on the following
    physical line (``consteval\\nint f();``), was never joined into the
    same lookahead the way a trailing ``requires``/``concept`` already is
    — the per-line scan only ever saw the two halves separately, neither
    of which alone satisfies the "followed by an identifier" check."""
    headers = _write(tmp_path, "a.h", "consteval\nint f() { return 1; }\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_detects_constinit_split_across_lines(tmp_path):
    """Companion: ``constinit`` split across lines."""
    headers = _write(tmp_path, "a.h", "constinit\nextern int x;\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constinit-declaration" for r in reqs)


def test_cpp20_detector_detects_abbreviated_unconstrained_function_template(tmp_path):
    """Regression (Codex review): a bare (unconstrained) ``auto`` used
    directly as an ordinary function's parameter type (``void f(auto
    x);`` — the C++20 abbreviated function template form) has no
    ``concept``/``requires``/constrained-parameter syntax at all, so none
    of the existing checks caught it."""
    headers = _write(tmp_path, "a.h", "void f(auto x);\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "abbreviated-function-template-parameter" for r in reqs)


def test_cpp20_detector_detects_abbreviated_param_with_cv_qualifier(tmp_path):
    """Companion: a cv-qualifier between the enclosing ``(``/``,`` and the
    bare ``auto`` (``void f(const auto& x);``) must not block detection —
    only the qualifier separates them, not an unrelated expression."""
    headers = _write(tmp_path, "a.h", "void f(const auto& x);\n")
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_generic_lambda_auto_param(tmp_path):
    """A generic lambda's ``auto`` parameter (``[](auto x) { ... }``) has
    been valid since C++14 — it must never be mistaken for the C++20-only
    abbreviated *function* template form just because both use a bare
    ``auto`` directly in a parameter list."""
    headers = _write(
        tmp_path,
        "a.h",
        "auto g() { return [](auto x) { return x; }; }\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_nested_lambda_default_argument(tmp_path):
    """A generic lambda nested inside an ordinary function's default
    argument must still be recognized as a lambda parameter list, not the
    enclosing function's own parameter list."""
    headers = _write(
        tmp_path,
        "a.h",
        "void f(int x = []( auto y){ return y; }(0));\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_auto_variable_declaration(tmp_path):
    """Ordinary ``auto`` type deduction for a variable (C++11+) must not be
    mistaken for the abbreviated function template parameter form."""
    headers = _write(tmp_path, "a.h", "void f() { auto x = 5; }\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_decltype_auto(tmp_path):
    """Regression (Codex review): ``decltype(auto)`` (valid since C++14)
    puts the bare keyword ``auto`` directly inside a ``(`` — the identical
    textual position as a genuine abbreviated parameter's enclosing ``(``
    — but it is decltype's own argument, not a parameter list at all.
    Without excluding it, a header whose only other C++20-looking
    ingredient is an otherwise-harmless ``concept``-as-type-name shadow
    (``struct concept {};``) still got force-parsed as C++20, where
    "concept" is a keyword and the header fails."""
    headers = _write(tmp_path, "a.h", "struct concept {};\ndecltype(auto) f();\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_genuine_param_alongside_decltype_auto(tmp_path):
    """Companion: a genuine abbreviated parameter must still be detected
    even when the same declaration also uses ``decltype(auto)`` as its
    return type — only the ``decltype(...)``'s own ``auto`` is excluded,
    not every ``auto`` occurrence in the line."""
    headers = _write(tmp_path, "a.h", "decltype(auto) f(auto x);\n")
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_trailing_return_type_auto(tmp_path):
    """A trailing-return-type ``auto`` (C++11+, ``auto f() -> int;``) must
    not be mistaken for a parameter's bare ``auto`` type."""
    headers = _write(tmp_path, "a.h", "auto f() -> int;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_abbreviated_param_with_attribute(tmp_path):
    """Regression (Codex review, second round): a standard attribute
    directly before the abbreviated parameter's bare ``auto``
    (``void f([[maybe_unused]] auto x);``) leaves the prefix ending in the
    attribute's closing ``]]`` instead of the enclosing ``(``/``,``, which
    the plain cv-qualifier strip alone did not account for."""
    headers = _write(tmp_path, "a.h", "void f([[maybe_unused]] auto x);\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "abbreviated-function-template-parameter" for r in reqs)


def test_cpp20_detector_detects_abbreviated_param_with_attribute_and_args(tmp_path):
    """Companion: an attribute carrying an argument list
    (``[[deprecated("msg")]]``) must be stripped as a whole, not just a
    bare ``[[name]]``."""
    headers = _write(tmp_path, "a.h", 'void f([[deprecated("msg")]] auto x);\n')
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_detects_abbreviated_param_with_attribute_and_cv(tmp_path):
    """Companion: an attribute followed by a cv-qualifier before ``auto``
    (``[[maybe_unused]] const auto& x``) must strip both, in order."""
    headers = _write(tmp_path, "a.h", "void f([[maybe_unused]] const auto& x);\n")
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_generic_lambda_auto_param_with_attribute(tmp_path):
    """A generic lambda's ``auto`` parameter with an attribute must still
    be excluded — the attribute strip must not defeat the lambda-capture-
    list exclusion."""
    headers = _write(
        tmp_path,
        "a.h",
        "auto g() { return [](  [[maybe_unused]] auto x) { return x; }; }\n",
    )
    assert _detect_cpp20_headers(headers) is False
