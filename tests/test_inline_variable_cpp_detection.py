"""A C++17 inline variable makes a header C++ (bug class
``extraction.value_bearing_form_silently_skipped``).

``inline int x = 1;`` is rejected by a C parse ("'inline' can only appear on
functions"). Before this, a header whose only C++ signal was such a variable
was auto-detected as C, both frontends failed, and the variable's value was
never read. Oracle: the C standard (C99/C11 allow ``inline`` on functions only),
checked here by the function declarator that a valid C header must carry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.headers.ast_config import _CPP_ONLY_PATTERNS, _detect_cpp_headers
from abicheck.extract.headers.toolchain import _resolve_force_cpp


def _header(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "api.h"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "line",
    [
        "inline int iv_var = 1;",
        "inline const int icv = 3;",
        "static inline int sv = 5;",
        "extern inline int ev;",
        "inline int table[4];",
        "inline constexpr auto k = 4;",
    ],
)
def test_inline_variable_declaration_is_cpp(tmp_path: Path, line: str) -> None:
    header = _header(tmp_path, line + "\n")
    assert _detect_cpp_headers([header], _CPP_ONLY_PATTERNS) is True
    assert _resolve_force_cpp(None, [header], None, (), frozenset()) is True


@pytest.mark.parametrize(
    "text",
    [
        # Inline functions are valid C99: a parenthesis makes it a declarator.
        "inline int f(int x) { return x; }\n",
        "static inline void g(void);\n",
        # A C identifier that merely contains the word.
        "int inline_count = 0;\n",
        "int inlined = 0;\n",
        # Plain C with a comment mentioning inline.
        "/* inline int old_style = 1; */\nint plain = 2;\n",
    ],
)
def test_valid_c_header_stays_c(tmp_path: Path, text: str) -> None:
    header = _header(tmp_path, text)
    assert _detect_cpp_headers([header], _CPP_ONLY_PATTERNS) is False
    assert _resolve_force_cpp(None, [header], None, (), frozenset()) is False


@pytest.mark.parametrize(
    ("line", "name", "expected"),
    [
        ("inline int iv_var = 1;", "iv_var", True),
        ("static inline const int icv = 3;", "icv", True),
        ("int plain = 2;", "plain", False),
        # The keyword must precede the name on the declaring line.
        ("int x = 1; inline int y = 2;", "x", False),
        # A comment does not count.
        ("int z = 3; // inline int z", "z", False),
        # A name that merely contains the other name is not that name.
        ("inline int iv_var_extra = 1;", "iv_var", False),
    ],
)
def test_declared_inline_reads_the_declaring_line(
    tmp_path: Path, line: str, name: str, expected: bool
) -> None:
    from abicheck.extract.headers.castxml.inline_variables import declared_inline

    header = _header(tmp_path, "int before = 0;\n" + line + "\n")
    assert declared_inline(f"{header}:2", name) is expected


def test_declared_inline_is_false_for_missing_or_bad_locations(tmp_path: Path) -> None:
    from abicheck.extract.headers.castxml.inline_variables import declared_inline

    header = _header(tmp_path, "inline int v = 1;\n")
    assert declared_inline(None, "v") is False
    assert declared_inline(f"{header}:99", "v") is False
    assert declared_inline(f"{header}:x", "v") is False
    assert declared_inline(str(tmp_path / "missing.h") + ":1", "v") is False
