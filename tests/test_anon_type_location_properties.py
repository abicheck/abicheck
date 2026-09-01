# Copyright 2026 Nikolay Petrov
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

"""Property-based tests for ``name_classification.strip_anonymous_type_location``.

The example-based tests in test_castxml_anonymous_type_location.py pin the
specific bug this function fixes, but the function itself went through three
real review-round corrections on the same PR: the first cut dropped the
``:line:col`` discriminator entirely (collapsing two distinct lambdas in one
header to one identity), the second used ``\\S+?`` for the path group (which
silently failed to strip a path containing whitespace -- a real checkout
directory name, or a Windows ``Program Files`` component), and the third used
``[^)]*?`` (which silently failed to strip a path containing a literal ``)``
-- e.g. ``C:\\release (old)\\foo.hpp``). Each round's own examples passed the
code that introduced the next round's gap. These properties state the
function's actual contract as invariants over the input space instead.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.name_classification import strip_anonymous_type_location

pytestmark = pytest.mark.slow

# Path text: printable characters INCLUDING spaces and ")" (the two
# character classes the \S+? and [^)]*? regressions each missed in turn) but
# excluding ":" and "/" so the generated path can never accidentally
# assemble something that looks like a second, competing "at
# ...:line:col)" match, and so it stays a pure basename (no directory
# separator of its own) when used as one below.
_PATH_CHARS = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    whitelist_characters=" _.-\\()",
)
_PATH = st.text(alphabet=_PATH_CHARS, min_size=1, max_size=40)
#: A checkout-root prefix -- deliberately excludes "/" too, joined onto a
#: basename with an explicit "/" below, so root text can never itself
#: masquerade as an extra path segment/basename of its own.
_ROOT = st.text(alphabet=_PATH_CHARS, min_size=0, max_size=30)
_LINE_COL = st.tuples(
    st.integers(min_value=0, max_value=99999), st.integers(min_value=0, max_value=999)
)
_KIND = st.sampled_from(
    [
        "lambda",
        "unnamed struct",
        "unnamed enum",
        "unnamed union",
        # "anonymous <kind>" is a distinct real-world vocabulary from
        # "unnamed <kind>" (observed directly in a real L5 graph node
        # identity) that the regex's alternation originally omitted,
        # leaving its checkout-dependent path unstripped.
        "anonymous struct",
        "anonymous enum",
        "anonymous union",
    ]
)


def _spelling(kind: str, path: str, line: int, col: int) -> str:
    marker = "lambda" if kind == "lambda" else kind
    return f"prefix<({marker} at {path}:{line}:{col})>"


def _rooted_path(root: str, basename: str) -> str:
    return f"{root}/{basename}" if root else basename


@given(kind=_KIND, root_a=_ROOT, root_b=_ROOT, basename=_PATH, line_col=_LINE_COL)
@settings(max_examples=300)
def test_same_declaration_different_checkout_roots_match(
    kind: str, root_a: str, root_b: str, basename: str, line_col: tuple[int, int]
) -> None:
    """The actual bug this whole function exists to fix, as a property: the
    SAME declaration (same kind, same basename, same :line:col) compiled
    from two DIFFERENT checkout roots -- including a root containing
    spaces or a literal ")", the two character classes the \\S+? ->
    [^)]*? -> .*? fixes each restore support for in turn -- must strip
    down to the identical identity, regardless of what either root
    contains. Only the checkout-dependent ROOT varies here; the
    declaring header's own basename (now part of the identity, round 8)
    is held fixed, since that's the actual same-file-different-checkout
    scenario this function exists to normalize -- see the sibling
    ``test_different_headers_same_position_stay_distinct`` for the
    complementary case where the basename itself differs."""
    line, col = line_col
    a = strip_anonymous_type_location(
        _spelling(kind, _rooted_path(root_a, basename), line, col)
    )
    b = strip_anonymous_type_location(
        _spelling(kind, _rooted_path(root_b, basename), line, col)
    )
    assert a == b


@given(
    kind=_KIND,
    basename_a=_PATH,
    basename_b=_PATH,
    line_col=_LINE_COL,
)
@settings(max_examples=300)
def test_different_headers_same_position_stay_distinct(
    kind: str, basename_a: str, basename_b: str, line_col: tuple[int, int]
) -> None:
    """Round-8 review finding, as a property: two declarations of the same
    kind at the IDENTICAL :line:col but in two DIFFERENTLY-NAMED headers
    must never strip down to the same identity -- :line:col alone cannot
    tell them apart, since two distinct files can each legitimately
    declare their own anonymous/lambda type at the same coordinates."""
    if basename_a == basename_b:
        return  # not the case under test
    line, col = line_col
    a = strip_anonymous_type_location(_spelling(kind, basename_a, line, col))
    b = strip_anonymous_type_location(_spelling(kind, basename_b, line, col))
    assert a != b


@given(
    kind=_KIND,
    path=_PATH,
    line_col_a=_LINE_COL,
    line_col_b=_LINE_COL,
)
@settings(max_examples=300)
def test_different_positions_in_one_header_stay_distinct(
    kind: str,
    path: str,
    line_col_a: tuple[int, int],
    line_col_b: tuple[int, int],
) -> None:
    """The type-identity-collision fix, as a property: two declarations of
    the same kind at DIFFERENT :line:col positions (simulating two distinct
    lambdas/anonymous tags in one header) must never strip down to the same
    identity."""
    if line_col_a == line_col_b:
        return  # not the case under test
    line_a, col_a = line_col_a
    line_b, col_b = line_col_b
    a = strip_anonymous_type_location(_spelling(kind, path, line_a, col_a))
    b = strip_anonymous_type_location(_spelling(kind, path, line_b, col_b))
    assert a != b


@given(kind=_KIND, path=_PATH, line_col=_LINE_COL)
@settings(max_examples=300)
def test_idempotent(kind: str, path: str, line_col: tuple[int, int]) -> None:
    """Stripping an already-stripped spelling must be a no-op -- a
    defensive property for any caller that might (directly or via a shared
    helper) apply this more than once to the same value."""
    line, col = line_col
    once = strip_anonymous_type_location(_spelling(kind, path, line, col))
    twice = strip_anonymous_type_location(once)
    assert once == twice


@given(
    text=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "P", "Zs")),
        min_size=1,
        max_size=60,
    )
)
@settings(max_examples=300)
def test_ordinary_name_with_no_anonymous_marker_is_unaffected(text: str) -> None:
    """A type name that never contains the "(kind at path:line:col)" shape
    at all must pass through completely unchanged -- byte-for-byte,
    including any internal whitespace it happens to carry. This is the
    round-3 review fix (Codex, fresh evidence): the whitespace-collapse
    step below used to run unconditionally, which would also rewrite
    meaningful whitespace inside an ordinary name (e.g. a C++20
    fixed-string NTTP template argument, ``Tag<"a  b">`` vs. ``Tag<"a
    b">``) even when no anonymous-location marker was present at all --
    now it only runs when the marker substitution actually fired, so an
    ordinary name (whitespace included) is genuinely a no-op, not just
    "unaffected modulo normalization"."""
    if " at " not in text or ":" not in text:
        assert strip_anonymous_type_location(text) == text
