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
