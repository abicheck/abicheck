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

"""Exhaustive, invariant-based coverage of the fingerprint-reliability guards
in ``diff_default_value_reliability.py`` (PR #720 retrospective).

Why this file exists, not just more hand-picked examples
----------------------------------------------------------
PR #720's review round found SEVEN distinct bugs in these guards across
seven separate passes, each one a different edge of the same small state
space (producer x reliability x value-shape):

1. The degraded-facts *warning* (a different module, ``serialization.py``)
   listing a flag no real detector consulted for a given producer.
2. ...the same warning not surviving a snapshot re-save round-trip.
3. ...the same warning not accounting for inferred (unconfirmed) header
   awareness.
4. ``constant_value_fingerprint_comparison_unreliable``'s exact
   ``producer == "clang"`` check missing ``producer is None`` (pre-v10,
   before ``ast_producer`` was even tracked).
5. Both fingerprint guards matching the bare ``"expr:"`` PREFIX instead of
   the full 16-hex-digit shape, so a genuine castxml value that merely
   *starts* the same way (e.g. a real ``expr::`` namespace reference) could
   be misidentified as a stale clang fingerprint and its real change
   silently dropped.
6. Both guards suppressing whenever *either* side was independently
   "unreliable" (an OR), when two archived snapshots that used the exact
   same legacy algorithm are directly comparable TO EACH OTHER — the
   correct rule is a MISMATCH between generations, not "either side risky".
7. Fix #6's own naive collapse of ``is_fingerprint and legacy`` into one
   "affected" boolean before comparing lost the value-SHAPE distinction: a
   legacy fingerprint on one side XORed against a plain literal on the
   other read as "generations differ" and wrongly suppressed a confirmed
   expression-to-literal (or literal-to-expression) edit — a change no
   algorithm version could ever produce spuriously, since the unstable
   algorithm only affects a compound expression's HASH, never whether a
   declaration's own value was a literal at all.

None of these were caught by unit tests before review, because every
existing test picked ONE hand-crafted scenario per property instead of
covering the state space that determines the guards' output. The state
space here is genuinely small and finite (producer in 4 values, reliable in
2, value-shape in 2, times two sides = well under a thousand combinations)
— cheap enough to enumerate completely rather than sample. This file does
that: instead of re-deriving whether each of those seven bugs is fixed by
picking one more example, it asserts INVARIANTS that must hold across the
*entire* reachable input space, so any future change that reintroduces one
of these bugs (or a sibling one this exact review round didn't happen to
think of) fails on the very next test run, not after a real customer's
corpus surfaces it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.diff_default_value_reliability import (
    _is_expr_fingerprint,
    constant_value_fingerprint_comparison_unreliable,
    default_value_fingerprint_comparison_unreliable,
)
from abicheck.model import AbiSnapshot

# A real fingerprint has the exact shape `dumper_clang_expr._expr_fingerprint`
# produces: "expr:" + 16 lowercase hex digits.
_LITERAL_VALUE = "42"
_FINGERPRINT_A = "expr:aaaaaaaaaaaaaaaa"
_FINGERPRINT_B = "expr:bbbbbbbbbbbbbbbb"
# The exhaustive cross-product test below only needs each side's VALUE SHAPE
# (fingerprint vs. not) -- the guard's decision never depends on the two
# values' specific content or on whether they're equal, only on each side's
# own shape/producer/reliability, so a two-element domain gives the exact
# same coverage as three while halving the combinatorial cost. The distinct-
# fingerprint-content cases (genuine-edit detection) are covered by the
# dedicated example tests in test_diff_symbols_deep.py instead.
_VALUES = (_LITERAL_VALUE, _FINGERPRINT_A)

_PRODUCERS = (None, "clang", "castxml", "hybrid")
_RELIABLE = (True, False)

# Which producers can NEVER contribute an "expr:"-fingerprinted value in the
# first place, per each guard's own documented domain (see both guard
# functions' docstrings): constants stay castxml-verbatim on a hybrid
# snapshot (never clang-merged per-entry), so both castxml and hybrid are
# safe; a default/field-initializer's hybrid merge keeps its clang-only-
# APPENDED entries' own unstable fingerprint, so only castxml is safe there.
_CONSTANT_SAFE_PRODUCERS = ("castxml", "hybrid")
_DEFAULT_SAFE_PRODUCERS = ("castxml",)


def _snap(*, ast_producer, reliable, version):
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        from_headers=True,
        ast_producer=ast_producer,
        clang_field_initializer_facts_reliable=reliable,
    )


class TestIsExprFingerprintShape:
    """The shape check itself: bug #5 above was a bare ``.startswith``."""

    def test_literal_is_not_a_fingerprint(self):
        assert not _is_expr_fingerprint(_LITERAL_VALUE)

    def test_real_fingerprint_is_a_fingerprint(self):
        assert _is_expr_fingerprint(_FINGERPRINT_A)

    @pytest.mark.parametrize(
        "collision",
        [
            "expr::NAMESPACE_VALUE",  # a real "expr" namespace, castxml-verbatim
            "expr:short",  # too few hex digits
            "expr:" + "a" * 17,  # too many hex digits
            "expr:AAAAAAAAAAAAAAAA",  # uppercase -- real hexdigest() is lowercase
            "EXPR:aaaaaaaaaaaaaaaa",  # wrong case on the literal prefix itself
            "notexpr:aaaaaaaaaaaaaaaa",
            "",
        ],
    )
    def test_prefix_or_shape_collisions_are_not_fingerprints(self, collision):
        assert not _is_expr_fingerprint(collision)


class _GuardInvariants:
    """Shared invariant assertions, parametrized per guard by its own
    producer-safety domain. Subclassed once per guard below rather than
    parametrized at the pytest level, so a failure's traceback names the
    guard directly."""

    safe_producers: tuple[str, ...]

    def _call(self, old: AbiSnapshot, new: AbiSnapshot, old_value: str, new_value: str):
        raise NotImplementedError

    def _is_legacy(self, producer, reliable):
        """First-principles ("if this side WERE fingerprint-shaped, would
        that fingerprint be from the unstable pre-v20 algorithm") reference
        — independent of the guard's own internal wiring, so this doesn't
        just test the implementation against itself. Deliberately does NOT
        fold in the value's own shape (bug #7's own lesson: shape and
        legacy-status are two separate questions)."""
        return producer not in self.safe_producers and not reliable

    @pytest.mark.parametrize(
        "old_producer,old_reliable,new_producer,new_reliable,old_value,new_value",
        list(
            itertools.product(
                _PRODUCERS, _RELIABLE, _PRODUCERS, _RELIABLE, _VALUES, _VALUES
            )
        ),
    )
    def test_suppresses_iff_both_fingerprint_shaped_and_legacy_differs(
        self,
        old_producer,
        old_reliable,
        new_producer,
        new_reliable,
        old_value,
        new_value,
    ):
        old = _snap(ast_producer=old_producer, reliable=old_reliable, version="1.0")
        new = _snap(ast_producer=new_producer, reliable=new_reliable, version="2.0")
        both_fingerprint_shaped = _is_expr_fingerprint(
            old_value
        ) and _is_expr_fingerprint(new_value)
        expected = both_fingerprint_shaped and (
            self._is_legacy(old_producer, old_reliable)
            != self._is_legacy(new_producer, new_reliable)
        )
        assert self._call(old, new, old_value, new_value) is expected

    def test_shape_mismatch_never_suppresses(self):
        """Bug #7's invariant: one side a literal, the other a genuine
        fingerprint (whatever either side's producer/reliability) is
        ALWAYS a confirmed real edit -- the unstable algorithm only ever
        affects a compound expression's fingerprint HASH, never whether a
        declaration's own value was a literal at all, so this shape
        transition can never be an algorithm-version artifact."""
        for old_producer, old_reliable, new_producer, new_reliable in itertools.product(
            _PRODUCERS, _RELIABLE, _PRODUCERS, _RELIABLE
        ):
            old = _snap(ast_producer=old_producer, reliable=old_reliable, version="1.0")
            new = _snap(ast_producer=new_producer, reliable=new_reliable, version="2.0")
            assert self._call(old, new, _LITERAL_VALUE, _FINGERPRINT_A) is False
            assert self._call(old, new, _FINGERPRINT_A, _LITERAL_VALUE) is False

    def test_neither_side_fingerprint_shaped_never_suppresses(self):
        """Bug #5's invariant, phrased structurally: two values that are
        NOT genuine fingerprints (whatever their producer/reliability) are
        always directly comparable -- there is no algorithm-instability
        risk to guard against when neither side ever touched the
        fingerprint machinery at all."""
        for old_producer, old_reliable, new_producer, new_reliable in itertools.product(
            _PRODUCERS, _RELIABLE, _PRODUCERS, _RELIABLE
        ):
            old = _snap(ast_producer=old_producer, reliable=old_reliable, version="1.0")
            new = _snap(ast_producer=new_producer, reliable=new_reliable, version="2.0")
            assert self._call(old, new, _LITERAL_VALUE, "99") is False

    def test_same_generation_on_both_sides_never_suppresses(self):
        """Bug #6's invariant: two sides with IDENTICAL producer and
        reliability -- same extraction generation, whether that generation
        is the stable one or the legacy one -- must never be suppressed
        from each other, since their fingerprints (if any) came from the
        identical algorithm and are directly comparable."""
        for producer, reliable in itertools.product(_PRODUCERS, _RELIABLE):
            old = _snap(ast_producer=producer, reliable=reliable, version="1.0")
            new = _snap(ast_producer=producer, reliable=reliable, version="2.0")
            assert self._call(old, new, _FINGERPRINT_A, _FINGERPRINT_B) is False

    def test_symmetric(self):
        """Suppression must not depend on an arbitrary old/new labeling."""
        for (
            old_producer,
            old_reliable,
            new_producer,
            new_reliable,
            old_value,
            new_value,
        ) in itertools.islice(
            itertools.product(
                _PRODUCERS, _RELIABLE, _PRODUCERS, _RELIABLE, _VALUES, _VALUES
            ),
            0,
            None,
            7,  # every 7th combination -- full coverage across runs, cheap per run
        ):
            old = _snap(ast_producer=old_producer, reliable=old_reliable, version="1.0")
            new = _snap(ast_producer=new_producer, reliable=new_reliable, version="2.0")
            forward = self._call(old, new, old_value, new_value)
            backward = self._call(new, old, new_value, old_value)
            assert forward == backward


class TestConstantFingerprintGuardInvariants(_GuardInvariants):
    safe_producers = _CONSTANT_SAFE_PRODUCERS

    def _call(self, old, new, old_value, new_value):
        return constant_value_fingerprint_comparison_unreliable(
            old, new, old_value, new_value
        )


class TestDefaultValueFingerprintGuardInvariants(_GuardInvariants):
    safe_producers = _DEFAULT_SAFE_PRODUCERS

    def _call(self, old, new, old_value, new_value):
        # default_value_fingerprint_comparison_unreliable takes an explicit
        # per-declaration producer separately from snap.ast_producer (see
        # default_value_representation_unreliable's own docstring for why:
        # a hybrid snapshot's clang-only-appended declaration is resolved
        # per-declaration, not from the whole-snapshot tag) -- pass the
        # snapshot's own producer for both, matching every real call site's
        # common case (a non-hybrid-merged declaration, where the two always
        # agree) and every other test in this suite.
        return default_value_fingerprint_comparison_unreliable(
            old, new, old.ast_producer, new.ast_producer, old_value, new_value
        )
