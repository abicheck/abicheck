# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Primitive-level property tests for :func:`abicheck.report.markdown_text.md_cell`.

AGENTS.md's "Primitive-level property tests" rule: ``md_cell`` is a
reusable, general-purpose helper two renderers now share
(``report/comparison_scope.py``'s scope tables and
``report/no_baseline.py``'s audit findings tables), so its contract is
stated here as invariants over generated input rather than only through
either caller's domain tests. The contract is exactly:

1. A row assembled from *n* escaped cells always parses back to *n* cells.
2. The result is one line -- no control character survives.
3. No code span can be closed from inside a value.

Invariant 1 is what a hand-written example missed for as long as this
helper existed: the input ``\\|`` (a raw backslash before a pipe) escaped
to ``\\\\|``, which GFM reads as an escaped backslash plus a *live* cell
separator. It then falsified the first fix for that too -- escaping only
the run's last backslash, which merely moved the failure to ``\\\\|`` --
which is the whole reason the contract is stated over generated runs rather
than the one input that started it. The oracle below counts separators off the rendered text
independently, never by re-running the helper's own escaping.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.report.markdown_text import md_cell

#: Deliberately dense in the characters that carry meaning to a Markdown
#: table parser, so a generated example is far more likely to be hostile
#: than a uniform text strategy would make it.
HOSTILE = st.text(
    alphabet=st.sampled_from(
        ["|", "\\", "`", "\n", "\r", "\t", "\x00", "\x7f", "#", "-", " ", "a", "—"]
    ),
    max_size=24,
)


def parse_row(row: str) -> list[str]:
    """Split a rendered Markdown table *row* into cells, GFM-style.

    Independent oracle: it applies the rule a GFM parser applies -- a
    backslash escapes the next character, and only an *unescaped* ``|``
    separates cells -- to the already-rendered text. It never calls
    ``md_cell``, so a bug in the helper cannot make this agree with it.
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


@settings(max_examples=300, deadline=None)
@given(values=st.lists(HOSTILE, min_size=1, max_size=6))
def test_a_row_of_escaped_cells_parses_back_to_the_same_cell_count(
    values: list[str],
) -> None:
    """Invariant 1: escaping preserves the row's arity, for any values."""
    row = "| " + " | ".join(md_cell(v) for v in values) + " |"
    assert len(parse_row(row)) == len(values), (row, values)


@settings(max_examples=300, deadline=None)
@given(value=HOSTILE)
def test_the_result_is_always_a_single_line_without_control_characters(
    value: str,
) -> None:
    """Invariant 2: no control character survives, so no row can be split."""
    out = md_cell(value)
    assert "\n" not in out and "\r" not in out
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in out), repr(out)


@settings(max_examples=300, deadline=None)
@given(value=HOSTILE)
def test_a_value_can_never_close_a_code_span(value: str) -> None:
    """Invariant 3: the callers wrap some cells in backticks -- a value that
    could emit one would close that span and let the rest of the row render
    as prose (or leave every later span inverted)."""
    assert "`" not in md_cell(value)
