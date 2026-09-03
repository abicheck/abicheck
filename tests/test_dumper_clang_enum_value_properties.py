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

"""Property-based tests for ``dumper_clang._evaluated_int_value``.

The bug this primitive fixes: clang's JSON AST folds a constant's evaluated
value onto exactly one node along a chain of single-child "wrapper"
expressions (``ImplicitCastExpr``, ``ConstantExpr``, ``ParenExpr``, ...) --
which node varies by what the initializer actually is (a literal wraps the
value close to the leaf; an enumerator-alias initializer like ``tag_x =
tag_a`` folds it onto an *intermediate* ``ConstantExpr``, ahead of the
``DeclRefExpr`` leaf that names the aliased enumerator and carries no value
of its own). The pre-fix implementation checked only the original node and
the fully-unwrapped leaf -- correct for the common "value near the leaf"
shape, silently wrong for the intermediate-node shape.

test_dumper_clang.py pins that exact intermediate-node example (and the
existing "outermost ConstantExpr wrapper" example it was already correct
for). This module states the actual contract as an invariant instead: for a
value folded onto ANY position along the wrapper chain, `_evaluated_int_value`
must find it -- not just the two shapes anyone happened to think to test.

Chain construction is delegated to ``tests/_wrapper_chain_gen.py``, the same
shared generator ``tests/test_ast_wrapper_chain_properties.py`` (Phase 2 of
``docs/contribute/plans/bug-class-regression-testing.md``) uses for every
other "unwrap until X" primitive -- a fix to the generator's own shape
reaches this module's coverage too, rather than needing a second,
independently-drifting copy (Codex review, PR #888).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.dumper_clang import _WRAPPER_EXPR_KINDS, _evaluated_int_value
from tests._wrapper_chain_gen import build_wrapper_chain

pytestmark = pytest.mark.slow

_WRAPPER_KINDS = sorted(_WRAPPER_EXPR_KINDS)


def _int_value_encodings(value: int) -> list[str]:
    """Every string encoding ``_evaluated_int_value`` must accept for
    *value* -- it parses via ``int(str(val), 0)``, base-0, not decimal-only
    (mirrors ``tests/test_ast_wrapper_chain_properties.py``'s identical
    helper, Codex review, PR #888)."""
    return [str(value), hex(value), oct(value), bin(value)]


_int_with_encoding_strategy = st.integers(min_value=-1000, max_value=1000).flatmap(
    lambda v: st.sampled_from(_int_value_encodings(v)).map(lambda s: (v, s))
)


@given(
    kinds=st.lists(st.sampled_from(_WRAPPER_KINDS), min_size=0, max_size=6),
    value_and_encoding=_int_with_encoding_strategy,
    data=st.data(),
)
@settings(max_examples=300)
def test_finds_a_value_folded_at_any_position_along_the_chain(
    kinds, value_and_encoding, data
) -> None:
    value, encoding = value_and_encoding
    value_at = data.draw(st.integers(min_value=0, max_value=len(kinds)))
    node, _leaf = build_wrapper_chain(kinds, value_at=value_at, value=encoding)
    assert _evaluated_int_value(node) == value


@given(kinds=st.lists(st.sampled_from(_WRAPPER_KINDS), min_size=0, max_size=6))
@settings(max_examples=200)
def test_no_value_anywhere_returns_none(kinds) -> None:
    """No node along the chain carries a folded value (the pure-alias case,
    e.g. a bare DeclRefExpr leaf with nothing evaluated) -> None, not a
    fabricated positional guess. Auto-increment fallback is the caller's
    job (dumper_clang.parse_enums), not this primitive's."""
    node, _leaf = build_wrapper_chain(kinds)  # no value_at -> nothing folded
    assert _evaluated_int_value(node) is None


@given(
    kinds=st.lists(st.sampled_from(_WRAPPER_KINDS), min_size=1, max_size=6),
    outer_value=st.integers(min_value=-1000, max_value=1000),
    inner_value=st.integers(min_value=-1000, max_value=1000),
)
@settings(max_examples=200)
def test_outermost_folded_value_wins_over_a_deeper_one(
    kinds, outer_value, inner_value
) -> None:
    """When more than one node along the chain carries a value (not a real
    clang shape, but the walk's own tie-break rule should still be
    deterministic and documented): the outermost one wins, matching the
    top-down walk order _evaluated_int_value actually implements."""
    if outer_value == inner_value:
        return
    node, _leaf = build_wrapper_chain(kinds, value_at=0, value=str(outer_value))
    # Fold a second, different value onto the innermost wrapper too (or the
    # leaf, if there are no wrapper kinds at all).
    cur = node
    while isinstance(cur.get("inner"), list) and cur["inner"]:
        cur = cur["inner"][0]
    cur["value"] = str(inner_value)
    assert _evaluated_int_value(node) == outer_value
