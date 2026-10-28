# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Primitive-level property tests for :func:`abicheck.report.markdown_text.md_text`.

AGENTS.md's "Primitive-level property tests" rule: ``md_text`` is the one
escaping helper every Markdown renderer shares, so its contract is stated
here as invariants over generated input rather than only through a caller's
domain tests. The contract, in its three shapes:

1. Table cells: a row assembled from *n* escaped cells always parses back to
   *n* cells (GFM's backslash pairing and pipe splitting, applied by an
   independent oracle).
2. Every shape is one line: no control character survives.
3. Code spans: ``md_text(v, code=True)`` is a code span whose content, read
   back by a CommonMark parser, is exactly ``v`` (whitespace-collapsed). No
   value can close its own span.
4. Prose: ``md_text(v)`` reads back, as CommonMark text, exactly as ``v``.
   This is the invariant a ``<name>`` in prose or a backtick in a symbol
   violated before the helper escaped them.

The oracles never call ``md_text``: they re-parse the rendered text with
``markdown-it`` (CommonMark) or with the GFM cell rule below.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st
from markdown_it import MarkdownIt

from abicheck.report.markdown_text import md_text

_COMMONMARK = MarkdownIt("commonmark")

#: Deliberately dense in the characters that carry meaning to a Markdown
#: parser, so a generated example is far more likely to be hostile than a
#: uniform text strategy would make it.
HOSTILE = st.lists(
    st.sampled_from(
        [
            "|",
            "\\",
            "`",
            "\n",
            "\r",
            "\t",
            "\x00",
            "\x7f",
            "#",
            "-",
            " ",
            "a",
            "—",
            "<",
            ">",
            "*",
            "_",
            "[",
            "]",
            "&",
            "~",
            "&amp;",
            "<b>",
            "é",
        ]
    ),
    max_size=12,
).map("".join)


def parse_row(row: str) -> list[str]:
    """Split a rendered Markdown table *row* into cells, GFM-style.

    Independent oracle: a backslash escapes the next character, and only an
    *unescaped* ``|`` separates cells. It never calls ``md_text``.
    """
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in row.strip().strip("|"):
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(ch)
    cells.append("".join(current))
    return cells


def _flattened(value: str) -> str:
    """The input with control characters as spaces, runs collapsed (oracle)."""
    spaced = "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in value)
    return " ".join(spaced.split())


def _commonmark_inline(text: str) -> list[tuple[str, str]]:
    """``(type, content)`` for the inline children of one paragraph."""
    tokens = _COMMONMARK.parse(text)
    inline = [t for t in tokens if t.type == "inline"]
    assert len(inline) == 1, (text, [t.type for t in tokens])
    return [(c.type, c.content) for c in inline[0].children or []]


@settings(max_examples=300, deadline=None)
@given(values=st.lists(HOSTILE, min_size=1, max_size=6))
def test_a_row_of_escaped_table_cells_parses_back_to_the_same_cell_count(
    values: list[str],
) -> None:
    """Invariant 1: escaping preserves the row's arity, prose or code cells."""
    cells = [
        md_text(v, table=True) if i % 2 == 0 else md_text(v, code=True, table=True)
        for i, v in enumerate(values)
    ]
    row = "| " + " | ".join(cells) + " |"
    assert len(parse_row(row)) == len(values), (row, values)


@settings(max_examples=300, deadline=None)
@given(value=HOSTILE)
def test_every_shape_is_a_single_line_without_control_characters(value: str) -> None:
    """Invariant 2: no control character survives, so no row or bullet splits."""
    for out in (
        md_text(value),
        md_text(value, table=True),
        md_text(value, code=True),
        md_text(value, code=True, table=True),
    ):
        assert "\n" not in out and "\r" not in out
        assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in out), repr(out)


@settings(max_examples=300, deadline=None)
@given(value=HOSTILE)
def test_a_code_span_reads_back_as_the_value(value: str) -> None:
    """Invariant 3: the fence is longer than any backtick run in the value, so
    a CommonMark reader gets exactly one code span holding the value."""
    expected = _flattened(value) or " "
    out = md_text(value, code=True)
    assert _commonmark_inline(out) == [("code_inline", expected)], (out, value)


@settings(max_examples=300, deadline=None)
@given(value=HOSTILE)
def test_prose_reads_back_as_the_value(value: str) -> None:
    """Invariant 4: escaped prose is shown exactly as written: no tag is
    dropped, no span opens, no entity is decoded, no emphasis forms."""
    expected = _flattened(value)
    out = md_text(value)
    if not expected:
        return
    # A value is always mid-line in a bullet or a cell, never at a block start,
    # so the oracle parses it after a leading word: "#" alone is a heading.
    children = _commonmark_inline("x " + out)
    assert all(kind == "text" for kind, _ in children), (out, children)
    assert "".join(content for _, content in children) == "x " + expected, (out, value)


def test_the_symbol_that_broke_the_renderers_reads_back_intact() -> None:
    """The concrete defect this helper closes, as an explicit oracle."""
    symbol = 'weird<name>&"`*_x'
    assert _commonmark_inline(md_text(symbol)) == [("text", symbol)]
    assert _commonmark_inline(md_text(symbol, code=True)) == [("code_inline", symbol)]
    assert md_text(symbol, code=True) == '``weird<name>&"`*_x``'


#: A demangled template name and a demangled operator name whose spelling
#: contains a pipe. The demangler is replaced by a fixed mapping, so the tests
#: need neither ``c++filt`` nor ``cxxfilt`` and cannot silently skip.
_DEMANGLED_NAMES = {
    "_ZNSt6vectorIiSaIiEE9push_backERKi": (
        "std::vector<int, std::allocator<int> >::push_back(int const&)"
    ),
    "_ZN3FooorERKS_": "Foo::operator|(Foo const&)",
}


@pytest.fixture
def fixed_demangler(monkeypatch: pytest.MonkeyPatch) -> None:
    import abicheck.demangle as demangle_module

    monkeypatch.setattr(
        demangle_module,
        "demangle_batch",
        lambda tokens, **_: {
            t: _DEMANGLED_NAMES[t] for t in tokens if t in _DEMANGLED_NAMES
        },
    )


@pytest.mark.parametrize("mangled", sorted(_DEMANGLED_NAMES))
def test_a_demangled_name_in_prose_reads_back_as_the_demangled_spelling(
    mangled: str, fixed_demangler: None
) -> None:
    """Invariant 4 for the demangle pass: a template argument such as ``<int>``
    that a renderer inserts after demangling must read back, not vanish as HTML."""
    from abicheck.demangle import demangle_text

    demangled = _DEMANGLED_NAMES[mangled]
    rendered = demangle_text(f"Public function removed: {mangled}", markdown=md_text)
    expected = f"Public function removed: {demangled} [{mangled}]"
    children = _commonmark_inline("x " + rendered)
    assert all(kind == "text" for kind, _ in children), (rendered, children)
    assert "".join(content for _, content in children) == "x " + expected, rendered


@pytest.mark.parametrize("mangled", sorted(_DEMANGLED_NAMES))
def test_a_demangled_name_in_a_code_span_keeps_its_span(
    mangled: str, fixed_demangler: None
) -> None:
    """Inside a code span the name is literal: no backslash escapes appear, and
    the span still reads back as one code span holding the annotated name."""
    from abicheck.demangle import demangle_text

    demangled = _DEMANGLED_NAMES[mangled]
    annotated = f"{demangled} [{mangled}]"
    rendered = demangle_text(f"- `{mangled}` removed", markdown=md_text)
    assert rendered == f"- `{annotated}` removed"
    assert ("code_inline", annotated) in _commonmark_inline("x " + rendered)


def test_a_demangled_pipe_in_a_table_row_keeps_the_column_count(
    fixed_demangler: None,
) -> None:
    """Invariant 1 for the demangle pass: ``operator|`` in a table row must not
    add a column, whether the token sits in a code span or in a plain cell."""
    from abicheck.demangle import demangle_text

    mangled = "_ZN3FooorERKS_"
    for row in (f"| {mangled} | b |", f"| `{mangled}` | b |"):
        rendered = demangle_text(row, escape_table_pipes=True, markdown=md_text)
        assert len(parse_row(rendered)) == 2, rendered
