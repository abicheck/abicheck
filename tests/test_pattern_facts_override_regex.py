# SPDX-License-Identifier: Apache-2.0
"""The override-only VIRTUAL_METHOD rule's atomic-whitespace rewrite is exact.

Oracle: the rule's original pattern text (leading ``\\s*`` backtrackable),
kept here verbatim. Generated fragments mix indentation, blank lines,
delimiters, parentheses, ``virtual``, cv/ref/noexcept/trailing-return
suffixes and ``override``/``final``, so every backtracking split the
original could explore is exercised.
"""

from __future__ import annotations

import re

from hypothesis import given, settings, strategies as st

from abicheck.buildsource import pattern_facts as pf

_ORIGINAL = re.compile(
    r"(?m)(?:^|[;{}])\s*(?![^\n;{}]*\bvirtual\b)"
    r"[^\n;{}()]*\([^;\n{}]*\)\s*"
    r"(?:const\s*)?(?:volatile\s*)?(?:&{1,2}\s*)?"
    r"(?:noexcept(?:\s*\([^)]*\))?\s*)?"
    r"(?:->\s*[^;\n{}]*?\s*)?"
    r"(?:\b(?:override|final)\b\s*){1,2}(?=[=;{])"
)
_RULE = next(
    r.regex
    for r in pf._RULES
    if r.kind is pf.PatternKind.VIRTUAL_METHOD and r.regex.pattern.startswith("(?m)")
)

_token = st.sampled_from(
    [
        " ",
        "  ",
        "\t",
        "\n",
        "\n\n",
        "\r\n",
        ";",
        "{",
        "}",
        "(",
        ")",
        "()",
        "(int)",
        "void",
        "f",
        "virtual",
        "override",
        "final",
        "const",
        "volatile",
        "&",
        "&&",
        "noexcept",
        "noexcept(true)",
        "->",
        "int",
        "= 0",
        "=",
        "struct D",
        "x.y",
    ]
)


@settings(max_examples=2000, deadline=None)
@given(st.lists(_token, max_size=30).map("".join))
def test_rewrite_matches_original_spans(text: str) -> None:
    assert [m.span() for m in _RULE.finditer(text)] == [
        m.span() for m in _ORIGINAL.finditer(text)
    ]


def test_detects_override_and_rejects_virtual_duplicate() -> None:
    text = "struct D : B {\n    void f() override;\n    virtual void g() override;\n  int h() const noexcept final;\n};"
    spans = [m.group() for m in _RULE.finditer(text)]
    assert len(spans) == 2
    assert all("virtual" not in s for s in spans)
