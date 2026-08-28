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

"""Phase 2 of ``docs/contribute/plans/bug-class-regression-testing.md``:
the ``extraction.ast_wrapper_chain_traversal`` bug class, generalized past
``tests/test_dumper_clang_enum_value_properties.py``'s single, enum-value-
scoped primitive (``dumper_clang._evaluated_int_value``) to every "unwrap
until X" helper this codebase carries.

Direct-clang extraction has THREE independently-written implementations of
"descend through a chain of single-child wrapper expressions":

* ``dumper_clang._evaluated_int_value`` -- checks every node along the
  chain for a folded ``value`` (the #839 fix itself, already covered by the
  sibling property suite named above).
* ``dumper_clang_expr._unwrap_expr`` / ``._initializer_value`` -- feeds
  ``TypeField.default``/``Param.default`` fingerprinting.
* ``abicheck.buildsource.source_extractors.clang_nodes._unwrap_expr`` /
  ``._expr_value`` -- feeds the L4 source-ABI extractor's own default-value
  fingerprinting.

The latter two are near-identical copies (same ``_WRAPPER_EXPR_KINDS``
literal, same descend-while-single-child algorithm) maintained independently
in two different modules specifically to avoid an import cycle (see
``dumper_clang_expr.py``'s own module docstring) -- exactly the shape where
one copy could be fixed for a future wrapper-chain bug and the other
silently left behind. This module's job is closing that generalization gap:
one shared generator (``tests/_wrapper_chain_gen.py``), one invariant,
checked against all three real implementations plus a cross-module
agreement property that would fail the moment the two ``_unwrap_expr``
copies (or their ``_WRAPPER_EXPR_KINDS`` vocabularies) drift apart.

Oracle: every expected answer here is known BY CONSTRUCTION (the generator
places the leaf/value node and hands the test its object identity or
literal value directly) -- never recomputed from the same helper under
test, matching this plan's "independent oracles, not the production
formula restated" rule. The malformed-input properties are the Phase 2
"negative control": a malformed/ambiguous tree must produce a typed
incomplete-analysis result (``None``, or a clean stop at the ambiguous
node) -- never a fabricated value and never a raised exception.

**Scope of the wrapper-invariance invariant (Codex review, PR #888):** it
holds for ``_unwrap_expr``'s reached leaf, ``_evaluated_int_value``'s
located value, and ``_initializer_value``/``_expr_value``'s LITERAL
detection (all fully unwrap before deciding) -- but deliberately NOT for
``_initializer_value``/``_expr_value``'s FINGERPRINT fallback on a
non-literal expression, which hashes the whole subtree including every
wrapper node's own ``kind`` (per ``dumper_clang_expr.py``'s own docstring:
"any compound expression is fingerprinted as a whole"). Two independently-
generated wrapper chains around the identical non-literal leaf therefore
produce DIFFERENT fingerprints today, by design -- confirmed empirically,
not assumed. This is pinned explicitly (not just asserted as "not None")
so a future reader doesn't mistake it for an oversight this suite's own
invariant should have caught.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck import dumper_clang, dumper_clang_expr
from abicheck.buildsource.source_extractors import clang_nodes
from tests._wrapper_chain_gen import (
    NON_LITERAL_LEAF_KIND,
    add_irrelevant_metadata,
    build_wrapper_chain,
    chain_with_ambiguous_branch,
    chain_with_missing_inner,
    chain_with_non_dict_child,
    chain_with_non_list_inner,
)

pytestmark = pytest.mark.slow

#: The real clang wrapper-expression kinds this codebase's three
#: ``_WRAPPER_EXPR_KINDS`` copies are meant to encode (see each module's own
#: definition) -- pinned HERE independently of any of those copies, not
#: derived from one of them. Deriving the generated vocabulary from the
#: production set under test would mean a kind silently dropped from ALL
#: THREE copies at once (a coordinated but wrong edit) still generates no
#: chains containing it and the cross-module equality check below still
#: agrees -- so the loss would go undetected (Codex review, PR #888).
#: Pinning it independently means such a drop shows up as a mismatch
#: against THIS set, not just as disagreement between the three copies.
_EXPECTED_WRAPPER_KINDS = frozenset(
    {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "CXXStaticCastExpr",
        "ConstantExpr",
        "ExprWithCleanups",
        "ParenExpr",
        "CXXFunctionalCastExpr",
        "MaterializeTemporaryExpr",
    }
)
_WRAPPER_KINDS = sorted(_EXPECTED_WRAPPER_KINDS)

#: The six literal node kinds ``_initializer_value``/``_expr_value`` are
#: each meant to recognize (see each module's own ``_LITERAL_NODE_KINDS``)
#: -- pinned independently for the identical reason
#: ``_EXPECTED_WRAPPER_KINDS`` above is: if either copy silently dropped one
#: of these, an initializer of that kind would silently change from its
#: readable value to a structural fingerprint, and a suite that only ever
#: generated ``IntegerLiteral`` leaves would never notice (Codex review,
#: PR #888).
_EXPECTED_LITERAL_LEAF_KINDS = frozenset(
    {
        "IntegerLiteral",
        "FloatingLiteral",
        "CharacterLiteral",
        "StringLiteral",
        "CXXBoolLiteralExpr",
        "FixedPointLiteral",
    }
)
_LITERAL_LEAF_KINDS = sorted(_EXPECTED_LITERAL_LEAF_KINDS)

_kinds_strategy = st.lists(st.sampled_from(_WRAPPER_KINDS), min_size=0, max_size=6)
_nonempty_kinds_strategy = st.lists(
    st.sampled_from(_WRAPPER_KINDS), min_size=1, max_size=6
)

#: A real clang AST node carries volatile bookkeeping fields (a compile-
#: time-only pointer ``id``, source ``loc``/``range`` offsets) alongside
#: the structural fields this generator's chains build -- irrelevant noise
#: no traversal/value-extraction primitive here is meant to depend on, per
#: the Phase 2 generator contract's "extra irrelevant metadata" clause
#: (Codex review, PR #888: every chain this suite generated omitted it
#: entirely, so a regression accidentally keying off one of these volatile
#: fields would have passed unnoticed).
_IRRELEVANT_METADATA_STRATEGY = st.fixed_dictionaries(
    {
        "id": st.text(alphabet="0123456789abcdef", min_size=4, max_size=12),
        "loc": st.fixed_dictionaries(
            {
                "line": st.integers(min_value=1, max_value=100_000),
                "col": st.integers(min_value=1, max_value=200),
            }
        ),
        "range": st.fixed_dictionaries(
            {
                "begin": st.integers(min_value=0, max_value=1_000_000),
                "end": st.integers(min_value=0, max_value=1_000_000),
            }
        ),
    }
)


def test_wrapper_kind_vocabularies_agree_across_all_three_copies() -> None:
    """``_WRAPPER_EXPR_KINDS`` is hand-maintained separately in three
    modules (an import-cycle constraint, per ``dumper_clang_expr.py``'s own
    docstring) -- exactly the shape #753->#759 already showed can silently
    drift with nothing failing. A wrapper kind added to only one copy would
    make that module see a value collapsed onto that node where the other
    two stop early, silently.

    Each copy is also checked against ``_EXPECTED_WRAPPER_KINDS`` above,
    independently pinned rather than derived from any of the three -- so a
    kind dropped from all three copies at once (which the copies-agree-
    with-each-other checks alone cannot see) still fails here.
    """
    assert dumper_clang._WRAPPER_EXPR_KINDS == dumper_clang_expr._WRAPPER_EXPR_KINDS
    assert dumper_clang._WRAPPER_EXPR_KINDS == clang_nodes._WRAPPER_EXPR_KINDS
    assert dumper_clang._WRAPPER_EXPR_KINDS == _EXPECTED_WRAPPER_KINDS
    assert dumper_clang_expr._WRAPPER_EXPR_KINDS == _EXPECTED_WRAPPER_KINDS
    assert clang_nodes._WRAPPER_EXPR_KINDS == _EXPECTED_WRAPPER_KINDS


def test_literal_kind_vocabularies_agree_across_both_copies() -> None:
    """``_LITERAL_NODE_KINDS`` (the six literal kinds
    ``_initializer_value``/``_expr_value`` recognize before falling back to
    a structural fingerprint) is hand-maintained separately in
    ``dumper_clang_expr.py`` and ``clang_nodes.py`` -- the same drift risk
    ``_WRAPPER_EXPR_KINDS`` has, checked against ``_EXPECTED_LITERAL_LEAF_
    KINDS`` above rather than against each other alone, for the identical
    reason (Codex review, PR #888)."""
    assert dumper_clang_expr._LITERAL_NODE_KINDS == clang_nodes._LITERAL_NODE_KINDS
    assert dumper_clang_expr._LITERAL_NODE_KINDS == _EXPECTED_LITERAL_LEAF_KINDS
    assert clang_nodes._LITERAL_NODE_KINDS == _EXPECTED_LITERAL_LEAF_KINDS


# --------------------------------------------------------------------------
# `_unwrap_expr`: both copies reach the identical, known-by-construction leaf
# --------------------------------------------------------------------------


@given(kinds=_kinds_strategy)
@settings(max_examples=300)
def test_unwrap_expr_reaches_the_known_leaf_on_both_copies(kinds: list[str]) -> None:
    root, leaf = build_wrapper_chain(kinds, leaf_kind=NON_LITERAL_LEAF_KIND)
    assert dumper_clang_expr._unwrap_expr(root) is leaf
    assert clang_nodes._unwrap_expr(root) is leaf


@given(kinds=_kinds_strategy)
@settings(max_examples=100)
def test_unwrap_expr_is_a_no_op_on_a_bare_leaf(kinds: list[str]) -> None:
    """A node whose own kind is never a wrapper kind (the terminal leaf
    itself, handed straight in) is returned unchanged -- covers the
    zero-wrapper case for both copies explicitly, not just as kinds=[]
    inside the generated suite above."""
    leaf = {"kind": NON_LITERAL_LEAF_KIND}
    assert dumper_clang_expr._unwrap_expr(leaf) is leaf
    assert clang_nodes._unwrap_expr(leaf) is leaf


@given(kinds=_kinds_strategy, metadata=_IRRELEVANT_METADATA_STRATEGY)
@settings(max_examples=150)
def test_unwrap_expr_ignores_irrelevant_metadata(
    kinds: list[str], metadata: dict[str, Any]
) -> None:
    """Per the Phase 2 generator contract's "extra irrelevant metadata"
    clause (Codex review, PR #888): every chain this suite generated
    previously omitted the volatile bookkeeping fields (``id``/``loc``/
    ``range``) a real clang AST node always carries, so a regression that
    accidentally keyed traversal off one of them would have passed
    unnoticed."""
    root, leaf = build_wrapper_chain(kinds, leaf_kind=NON_LITERAL_LEAF_KIND)
    add_irrelevant_metadata(root, metadata)
    assert dumper_clang_expr._unwrap_expr(root) is leaf
    assert clang_nodes._unwrap_expr(root) is leaf


# --------------------------------------------------------------------------
# Negative controls: ambiguous/malformed input stops cleanly, never raises,
# never fabricates a value by guessing which branch to follow.
# --------------------------------------------------------------------------


@given(kinds=_nonempty_kinds_strategy, data=st.data())
@settings(max_examples=200)
def test_unwrap_expr_stops_at_an_ambiguous_branch(kinds: list[str], data: Any) -> None:
    branch_at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    root, stopping_node = chain_with_ambiguous_branch(kinds, branch_at)
    assert dumper_clang_expr._unwrap_expr(root) is stopping_node
    assert clang_nodes._unwrap_expr(root) is stopping_node


@given(kinds=_nonempty_kinds_strategy, data=st.data())
@settings(max_examples=200)
def test_unwrap_expr_stops_when_inner_is_missing(kinds: list[str], data: Any) -> None:
    missing_at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    root, stopping_node = chain_with_missing_inner(kinds, missing_at)
    assert dumper_clang_expr._unwrap_expr(root) is stopping_node
    assert clang_nodes._unwrap_expr(root) is stopping_node


@given(kinds=_nonempty_kinds_strategy, data=st.data())
@settings(max_examples=200)
def test_unwrap_expr_stops_on_a_non_dict_child(kinds: list[str], data: Any) -> None:
    bad_at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    root, stopping_node = chain_with_non_dict_child(kinds, bad_at)
    # Neither copy may raise (AttributeError from calling .get() on a str,
    # for instance) -- reaching the assertion at all is part of what's
    # being checked, not just the returned identity.
    assert dumper_clang_expr._unwrap_expr(root) is stopping_node
    assert clang_nodes._unwrap_expr(root) is stopping_node


#: Values a real ``inner`` key must never actually hold (clang's JSON AST
#: always emits a list when the key is present at all), but which a
#: malformed/adversarial AST fragment could -- a shape distinct from a
#: MISSING ``inner`` (that's ``None``/absent; this is present but the wrong
#: TYPE). Confirmed this previously raised ``TypeError`` on all three
#: production copies before this suite's own review found it (Codex
#: review, PR #888).
_BAD_INNER_VALUES = (1, "x", {"kind": "Foo"}, True, 3.5)


@given(
    kinds=_nonempty_kinds_strategy,
    bad_inner=st.sampled_from(_BAD_INNER_VALUES),
    data=st.data(),
)
@settings(max_examples=200)
def test_unwrap_expr_stops_on_a_non_list_inner(
    kinds: list[str], bad_inner: Any, data: Any
) -> None:
    bad_at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    root, stopping_node = chain_with_non_list_inner(kinds, bad_at, bad_inner)
    assert dumper_clang_expr._unwrap_expr(root) is stopping_node
    assert clang_nodes._unwrap_expr(root) is stopping_node


@given(
    kinds=_nonempty_kinds_strategy,
    bad_inner=st.sampled_from(_BAD_INNER_VALUES),
    data=st.data(),
)
@settings(max_examples=150)
def test_evaluated_int_value_survives_a_non_list_inner(
    kinds: list[str], bad_inner: Any, data: Any
) -> None:
    at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    root, _stopping_node = chain_with_non_list_inner(kinds, at, bad_inner)
    assert dumper_clang._evaluated_int_value(root) is None


@given(kinds=_nonempty_kinds_strategy, data=st.data())
@settings(max_examples=150)
def test_evaluated_int_value_survives_the_same_malformed_shapes(
    kinds: list[str], data: Any
) -> None:
    """`_evaluated_int_value` has its OWN, independently-maintained
    traversal loop (not built on top of either `_unwrap_expr` copy) --
    the three ambiguous/malformed shapes above must degrade the same way
    on it too (Codex review, PR #888): none of these malformed builders
    previously reached this primitive at all, so a regression indexing a
    missing child or calling ``.get()`` on a non-dict child here could
    have escaped this suite entirely."""
    at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    for build in (
        chain_with_ambiguous_branch,
        chain_with_missing_inner,
        chain_with_non_dict_child,
    ):
        root, _stopping_node = build(kinds, at)
        # No value was ever folded onto any node in these chains, so a
        # correct traversal that degrades cleanly at the malformed point
        # must report None -- never raise, never fabricate a value.
        assert dumper_clang._evaluated_int_value(root) is None


# --------------------------------------------------------------------------
# `_evaluated_int_value`: the actual #839 mechanism -- a value folded at ANY
# position along the chain must be found, not just the endpoints.
# --------------------------------------------------------------------------


def _int_value_encodings(value: int) -> list[str]:
    """Every string encoding ``_evaluated_int_value`` must accept for
    *value*, per its own ``int(str(val), 0)`` base-0 parsing -- not just
    the decimal spelling. A regression narrowing that to decimal-only
    parsing should fail here, not just against the one encoding every
    other test in this module happens to use."""
    return [str(value), hex(value), oct(value), bin(value)]


#: Draws (value, one-of-its-valid-encodings) together, so a test can build
#: the chain from the encoded string while asserting against the real int.
_int_with_encoding_strategy = st.integers(min_value=-1000, max_value=1000).flatmap(
    lambda v: st.sampled_from(_int_value_encodings(v)).map(lambda s: (v, s))
)


@given(
    kinds=_kinds_strategy,
    value_and_encoding=_int_with_encoding_strategy,
    data=st.data(),
)
@settings(max_examples=300)
def test_evaluated_int_value_finds_a_value_folded_at_any_position(
    kinds: list[str], value_and_encoding: tuple[int, str], data: Any
) -> None:
    value, encoding = value_and_encoding
    value_at = data.draw(st.integers(min_value=0, max_value=len(kinds)))
    root, _leaf = build_wrapper_chain(kinds, value_at=value_at, value=encoding)
    assert dumper_clang._evaluated_int_value(root) == value


@given(
    kinds=_kinds_strategy,
    value_and_encoding=_int_with_encoding_strategy,
    metadata=_IRRELEVANT_METADATA_STRATEGY,
    data=st.data(),
)
@settings(max_examples=150)
def test_evaluated_int_value_ignores_irrelevant_metadata(
    kinds: list[str],
    value_and_encoding: tuple[int, str],
    metadata: dict[str, Any],
    data: Any,
) -> None:
    value, encoding = value_and_encoding
    value_at = data.draw(st.integers(min_value=0, max_value=len(kinds)))
    root, _leaf = build_wrapper_chain(kinds, value_at=value_at, value=encoding)
    add_irrelevant_metadata(root, metadata)
    assert dumper_clang._evaluated_int_value(root) == value


@given(kinds=_kinds_strategy)
@settings(max_examples=100)
def test_evaluated_int_value_none_when_nothing_folded(kinds: list[str]) -> None:
    root, _leaf = build_wrapper_chain(kinds)
    assert dumper_clang._evaluated_int_value(root) is None


@given(
    kinds=_kinds_strategy,
    bad_value=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        min_size=1,
        max_size=8,
    ).filter(lambda s: not _looks_intlike(s)),
    data=st.data(),
)
@settings(max_examples=150)
def test_evaluated_int_value_skips_unparseable_values_instead_of_raising(
    kinds: list[str], bad_value: str, data: Any
) -> None:
    """A non-numeric ``value`` folded partway down the chain (malformed/
    adversarial AST input) must be skipped, not raise, and must not be
    treated as a fabricated 0 or any other guess -- the walk keeps
    descending and, if nothing further down is parseable either, the whole
    call reports ``None``."""
    value_at = data.draw(st.integers(min_value=0, max_value=len(kinds)))
    root, _leaf = build_wrapper_chain(kinds, value_at=value_at, value=bad_value)
    assert dumper_clang._evaluated_int_value(root) is None


@given(
    kinds=st.lists(st.sampled_from(_WRAPPER_KINDS), min_size=2, max_size=6),
    bad_value=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        min_size=1,
        max_size=8,
    ).filter(lambda s: not _looks_intlike(s)),
    good_value_and_encoding=_int_with_encoding_strategy,
    data=st.data(),
)
@settings(max_examples=150)
def test_evaluated_int_value_recovers_a_valid_value_past_an_unparseable_one(
    kinds: list[str],
    bad_value: str,
    good_value_and_encoding: tuple[int, str],
    data: Any,
) -> None:
    """The 'skip, don't raise' contract above only proves the walk doesn't
    crash when NOTHING further down is parseable either -- it doesn't by
    itself prove the walk actually CONTINUES rather than stopping outright
    (Codex review, PR #888): a regression that returns ``None`` immediately
    on the first unparseable value, instead of continuing to descend,
    would still pass that test. Fold a real value onto a DEEPER node than
    the malformed one and assert it's still found."""
    good_value, good_encoding = good_value_and_encoding
    bad_at = data.draw(st.integers(min_value=0, max_value=len(kinds) - 1))
    good_at = data.draw(st.integers(min_value=bad_at + 1, max_value=len(kinds)))
    root, _leaf = build_wrapper_chain(kinds, value_at=good_at, value=good_encoding)
    ancestor = root
    for _ in range(bad_at):
        ancestor = ancestor["inner"][0]
    ancestor["value"] = bad_value
    assert dumper_clang._evaluated_int_value(root) == good_value


def _looks_intlike(s: str) -> bool:
    """Whether Hypothesis-generated text would accidentally parse as an int
    via ``int(s, 0)`` (e.g. a plain digit string, or a valid ``0x...``/
    ``0o...``/``0b...`` literal) -- filtered out so this property's inputs
    are genuinely unparseable, not accidental true positives."""
    try:
        int(s, 0)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# Mutant-killing: the ORIGINAL #839 bug, reintroduced, must be rejected by
# this suite's own generator for at least one generated shape (per this
# plan's "Killing known-bad mutants" design principle).
# --------------------------------------------------------------------------


def _pre_839_evaluated_int_value(node: dict[str, Any]) -> int | None:
    """The known-bad implementation #839 fixed: check only the original
    node and the FULLY-unwrapped leaf, never an intermediate wrapper."""
    for candidate in (node, dumper_clang_expr._unwrap_expr(node)):
        val = candidate.get("value") if isinstance(candidate, dict) else None
        if val is not None:
            try:
                return int(str(val), 0)
            except ValueError:
                continue
    return None


@given(
    kinds=_nonempty_kinds_strategy,
    value_and_encoding=_int_with_encoding_strategy,
    data=st.data(),
)
@settings(max_examples=300)
def test_suite_kills_the_original_endpoints_only_mutant(
    kinds: list[str], value_and_encoding: tuple[int, str], data: Any
) -> None:
    """For a value folded strictly BETWEEN the outermost node and the fully
    -unwrapped leaf, the pre-#839 mutant must disagree with the fixed
    implementation -- demonstrating this generator actually reaches the
    input shape the historical bug needed, not merely inputs both
    implementations already handle alike."""
    value, encoding = value_and_encoding
    if len(kinds) < 2:
        return
    value_at = data.draw(st.integers(min_value=1, max_value=len(kinds) - 1))
    root, _leaf = build_wrapper_chain(kinds, value_at=value_at, value=encoding)
    assert dumper_clang._evaluated_int_value(root) == value
    assert _pre_839_evaluated_int_value(root) is None
    assert dumper_clang._evaluated_int_value(root) != _pre_839_evaluated_int_value(root)


# --------------------------------------------------------------------------
# `_initializer_value` / `_expr_value`: the fingerprinting layer built on
# top of `_unwrap_expr`, exercised through both real call shapes (a decl
# node for `_initializer_value`, a bare expr node for `_expr_value`).
# --------------------------------------------------------------------------


def _decl_wrapping(init_expr: dict[str, Any]) -> dict[str, Any]:
    """A minimal Var/Field-decl-shaped node whose initializer is *init_expr*
    -- an irrelevant leading ``Attr`` child included, matching `_init_expr`'s
    real "last non-Decl/Attr/Comment child" contract rather than assuming
    the initializer is the only child."""
    return {
        "kind": "VarDecl",
        "inner": [{"kind": "AlignedAttr"}, init_expr],
    }


@given(
    kinds=_kinds_strategy,
    value=st.integers(min_value=-1000, max_value=1000),
    leaf_kind=st.sampled_from(_LITERAL_LEAF_KINDS),
)
@settings(max_examples=200)
def test_initializer_value_and_expr_value_agree_on_a_literal_at_any_depth(
    kinds: list[str], value: int, leaf_kind: str
) -> None:
    root, _leaf = build_wrapper_chain(
        kinds, value_at=len(kinds), value=str(value), leaf_kind=leaf_kind
    )
    assert dumper_clang_expr._initializer_value(_decl_wrapping(root)) == str(value)
    assert clang_nodes._expr_value(root) == str(value)


@given(
    kinds=_kinds_strategy,
    value=st.integers(min_value=-1000, max_value=1000),
    leaf_kind=st.sampled_from(_LITERAL_LEAF_KINDS),
    metadata=_IRRELEVANT_METADATA_STRATEGY,
)
@settings(max_examples=150)
def test_initializer_value_and_expr_value_ignore_irrelevant_metadata(
    kinds: list[str], value: int, leaf_kind: str, metadata: dict[str, Any]
) -> None:
    root, _leaf = build_wrapper_chain(
        kinds, value_at=len(kinds), value=str(value), leaf_kind=leaf_kind
    )
    add_irrelevant_metadata(root, metadata)
    assert dumper_clang_expr._initializer_value(_decl_wrapping(root)) == str(value)
    assert clang_nodes._expr_value(root) == str(value)


@given(kinds=_kinds_strategy)
@settings(max_examples=100)
def test_initializer_value_and_expr_value_fingerprint_a_non_literal_leaf(
    kinds: list[str],
) -> None:
    """A chain ending in a non-literal leaf (an alias reference, not a
    constant) never reads as a literal value on either implementation --
    both must fall through to their own fingerprint instead of fabricating
    a value from an unrelated wrapper kind string."""
    root, _leaf = build_wrapper_chain(kinds, leaf_kind=NON_LITERAL_LEAF_KIND)
    init_value = dumper_clang_expr._initializer_value(_decl_wrapping(root))
    expr_value = clang_nodes._expr_value(root)
    assert init_value is not None
    assert not init_value.isdigit() and not init_value.lstrip("-").isdigit()
    assert expr_value is not None


@given(kinds_a=_kinds_strategy, kinds_b=_kinds_strategy)
@settings(max_examples=150)
def test_fingerprint_is_sensitive_to_wrapper_shape_by_design(
    kinds_a: list[str], kinds_b: list[str]
) -> None:
    """Pins the scope carve-out documented in this module's own docstring
    (Codex review, PR #888): the SAME non-literal leaf content, wrapped in
    two INDEPENDENTLY generated wrapper chains, produces the SAME
    fingerprint through both ``_initializer_value`` and ``_expr_value``
    only when the chains' own kind sequences match, and a DIFFERENT one
    otherwise -- a different wrapper shape always changes at least one
    ``kind`` in the hashed subtree, so equal fingerprints for unequal
    chains would mean a real hash/structural collision, not a false
    positive this suite should tolerate. This makes the "fingerprint is
    NOT wrapper-invariant" half of the class explicit and executable,
    rather than only a claim in prose."""
    leaf_kind = "SharedLeafKind"
    root_a, _ = build_wrapper_chain(kinds_a, leaf_kind=leaf_kind)
    root_b, _ = build_wrapper_chain(kinds_b, leaf_kind=leaf_kind)

    init_a = dumper_clang_expr._initializer_value(_decl_wrapping(root_a))
    init_b = dumper_clang_expr._initializer_value(_decl_wrapping(root_b))
    expr_a = clang_nodes._expr_value(root_a)
    expr_b = clang_nodes._expr_value(root_b)

    if kinds_a == kinds_b:
        assert init_a == init_b
        assert expr_a == expr_b
    else:
        assert init_a != init_b
        assert expr_a != expr_b


@given(
    kinds=_kinds_strategy,
    a=st.integers(min_value=-1000, max_value=1000),
    b=st.integers(min_value=-1000, max_value=1000),
)
@settings(max_examples=150)
def test_expr_value_fingerprint_is_deterministic_and_distinguishes_content(
    kinds: list[str], a: int, b: int
) -> None:
    """Same chain shape, different underlying leaf content -> different
    fingerprints (injectivity for semantically distinct expressions); same
    input twice -> the same fingerprint (determinism)."""
    # A non-literal leaf kind forces the fingerprint (hashing) path rather
    # than the literal short-circuit, so this exercises the branch this
    # property is actually about.
    root_a, _ = build_wrapper_chain(kinds, leaf_kind=f"Kind{a}")
    root_a_again, _ = build_wrapper_chain(kinds, leaf_kind=f"Kind{a}")
    root_b, _ = build_wrapper_chain(kinds, leaf_kind=f"Kind{b}")

    fp_a = clang_nodes._expr_value(root_a)
    fp_a_again = clang_nodes._expr_value(root_a_again)
    fp_b = clang_nodes._expr_value(root_b)

    assert fp_a == fp_a_again
    if a != b:
        assert fp_a != fp_b
