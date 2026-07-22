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
