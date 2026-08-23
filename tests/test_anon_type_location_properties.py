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
# excluding ":" so the generated path can never accidentally assemble
# something that looks like a second, competing "at ...:line:col)" match.
_PATH_CHARS = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    whitelist_characters=" /_.-\\()",
)
_PATH = st.text(alphabet=_PATH_CHARS, min_size=1, max_size=40)
_LINE_COL = st.tuples(
    st.integers(min_value=0, max_value=99999), st.integers(min_value=0, max_value=999)
)
_KIND = st.sampled_from(["lambda", "unnamed struct", "unnamed enum", "unnamed union"])


def _spelling(kind: str, path: str, line: int, col: int) -> str:
    marker = "lambda" if kind == "lambda" else kind
    return f"prefix<({marker} at {path}:{line}:{col})>"


@given(kind=_KIND, path_a=_PATH, path_b=_PATH, line_col=_LINE_COL)
@settings(max_examples=300)
def test_same_declaration_different_checkout_paths_match(
    kind: str, path_a: str, path_b: str, line_col: tuple[int, int]
) -> None:
    """The actual bug this whole function exists to fix, as a property: the
    SAME declaration (same kind, same :line:col) compiled from two
    DIFFERENT checkout paths -- including a path containing spaces or a
    literal ")", the two character classes the \\S+? -> [^)]*? -> .*?
    fixes each restore support for in turn -- must strip down to the
    identical identity, regardless of what either path contains. Stronger
    than (and replaces) a plain "doesn't contain the injected path" check:
    that check is vulnerable to a generated path coincidentally colliding
    with the fixed template text around it (e.g. a single-character path
    "x" trivially substring-matching "prefix"), whereas this formulation
    only compares two independently-generated outputs against each other."""
    line, col = line_col
    a = strip_anonymous_type_location(_spelling(kind, path_a, line, col))
    b = strip_anonymous_type_location(_spelling(kind, path_b, line, col))
    assert a == b


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
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "P")),
        min_size=1,
        max_size=60,
    )
)
@settings(max_examples=300)
def test_ordinary_name_with_no_anonymous_marker_is_unaffected(text: str) -> None:
    """A type name that never contains the "(kind at path:line:col)" shape
    at all must pass through completely unchanged. Restricted to an
    already-whitespace-free alphabet: the function unconditionally collapses
    internal whitespace and strips the ends (its own normalization step,
    independent of whether a location was actually found), so a text
    containing whitespace is not "unaffected" in the literal sense this
    property checks — that normalization is real, documented behavior, not
    the property under test here."""
    if " at " not in text or ":" not in text:
        assert strip_anonymous_type_location(text) == text
