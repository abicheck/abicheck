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
