"""macro_graph's conditional-region scan and the pattern-facts scan read
comment state from one lexer (``pattern_facts_lexer``).

macro_graph kept its own copy of the comment/string state machine, which did
not model raw strings, line splices or digit separators. Each case below is a
header where that copy disagreed with what the preprocessor sees; the
expected regions are written from the preprocessor's reading, not from either
scanner.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.macro_graph import scan_conditional_regions
from abicheck.buildsource.pattern_facts_lexer import (
    _blank_comments_and_strings,
    lines_starting_in_block_comment,
)

_GUARD = "#ifdef A\nint f();\n#endif\n"


def _regions(text: str) -> list[tuple[str, int, int]]:
    return [(r.macro, r.start_line, r.end_line) for r in scan_conditional_regions(text)]


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        # A raw string holding a quote and "/*" is one literal; no comment opens.
        pytest.param('const char *p = R"x("/*)x";\n', [("A", 3, 3)], id="raw-string"),
        # A spliced string continues onto the next line, so its "/*" is text.
        pytest.param('#define S "a\\\n/*"\n', [("A", 4, 4)], id="spliced-string"),
        # 1'0 is a digit separator, so the "/*" after it opens a real comment
        # that hides the guard.
        pytest.param("int x = 1'0; /*\n", [], id="digit-separator"),
        # A backslash before the newline splices even after an escaped "\\":
        # the literal goes on, so its "/*" is text (LF and CRLF).
        pytest.param('const char *s = "x\\\\\nn/*";\n', [("A", 4, 4)], id="splice-lf"),
        pytest.param(
            'const char *s = "x\\\\\r\nn/*";\n', [("A", 4, 4)], id="splice-crlf"
        ),
        # Controls both scanners always got right.
        pytest.param('const char *p = "/*";\n', [("A", 3, 3)], id="plain-string"),
        pytest.param("/*\n", [], id="open-comment"),
        pytest.param("int y; /* c */\n", [("A", 3, 3)], id="closed-comment"),
    ],
)
def test_guard_regions_follow_the_preprocessors_comment_state(
    prefix: str, expected: list[tuple[str, int, int]]
) -> None:
    closer = "*/\n" if expected == [] else ""
    assert _regions(prefix + _GUARD + closer) == expected


def test_an_unmatched_quote_ends_at_the_line() -> None:
    """``#error don't``: the apostrophe opens no literal past its line, so
    the code below stays live (it used to be blanked to the next quote)."""
    text = "#error don't\nint x; // c\n"
    assert _blank_comments_and_strings(text).splitlines()[1].strip() == "int x;"
    assert lines_starting_in_block_comment("#error don't\n/*\nx\n*/\ny\n") == [
        False,
        False,
        True,
        True,
        False,
    ]


def test_line_states_align_with_splitlines() -> None:
    text = "a /*\r\nb\r\n*/ c\rd"
    assert len(lines_starting_in_block_comment(text)) == len(text.splitlines())
    assert lines_starting_in_block_comment(text) == [False, True, True, False]
