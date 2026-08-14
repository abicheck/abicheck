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

"""Tests for ``finding_identity_ctor_dtor.py``'s ctor/dtor synthetic-key
format-drift reconciliation (napetrov/abicheck-bazel-lab PR #11's
``Calculator`` constructor false-positive; PR #582 changed
``dumper_castxml.py``'s synthetic-ctor/dtor key scope from a bare class name
to a namespace-qualified one).

Uses synthetic ``AbiSnapshot``/``Function`` fixtures -- no compiler or
castxml needed, matching the style of ``test_explicit_ctor.py`` and
``test_func_deleted.py``.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_symbols import _diff_access_levels, _diff_functions
from abicheck.finding_identity_ctor_dtor import (
    CtorDtorCanonicalKey,
    canonicalize_synthetic_ctor_dtor_key,
    find_ctor_dtor_key_drift_matches,
    iter_matched_function_pairs,
)
from abicheck.model import AbiSnapshot, AccessLevel, Function, Param, Visibility


def _snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        variables=[],
        types=[],
    )


def _func(
    name: str,
    mangled: str,
    params: list[Param] | None = None,
    *,
    access: AccessLevel = AccessLevel.PUBLIC,
    is_inline: bool = False,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=Visibility.PUBLIC,
        access=access,
        is_inline=is_inline,
    )


class TestCanonicalizeSyntheticCtorDtorKey:
    def test_non_synthetic_key_is_none(self) -> None:
        assert canonicalize_synthetic_ctor_dtor_key("_ZN3FooC1Ev") is None
        assert canonicalize_synthetic_ctor_dtor_key("plain_c_function") is None

    def test_bare_and_qualified_ctor_scope_canonicalize_equal(self) -> None:
        bare = canonicalize_synthetic_ctor_dtor_key("__abicheck_ctor__Calculator()")
        qualified = canonicalize_synthetic_ctor_dtor_key(
            "__abicheck_ctor__abicheck_lab::Calculator()"
        )
        assert (
            bare == qualified == CtorDtorCanonicalKey(owner="Calculator", kind="ctor")
        )

    def test_bare_and_qualified_dtor_scope_canonicalize_equal(self) -> None:
        bare = canonicalize_synthetic_ctor_dtor_key("~Calculator")
        qualified = canonicalize_synthetic_ctor_dtor_key("~abicheck_lab::Calculator")
        assert (
            bare == qualified == CtorDtorCanonicalKey(owner="Calculator", kind="dtor")
        )

    def test_ctor_params_distinguish_overloads(self) -> None:
        default = canonicalize_synthetic_ctor_dtor_key("__abicheck_ctor__Calculator()")
        copy = canonicalize_synthetic_ctor_dtor_key(
            "__abicheck_ctor__Calculator(const Calculator&)"
        )
        move = canonicalize_synthetic_ctor_dtor_key(
            "__abicheck_ctor__Calculator(Calculator&&)"
        )
        converting = canonicalize_synthetic_ctor_dtor_key(
            "__abicheck_ctor__Calculator(int)"
        )
        forms = {default, copy, move, converting}
        assert len(forms) == 4  # every overload canonicalizes distinctly
        assert default is not None and default.params == ()
        assert copy is not None and copy.params == ("Calculator const &",)
        assert move is not None and move.params == ("Calculator & &",)
        assert converting is not None and converting.params == ("int",)

    def test_template_argument_namespace_not_mistaken_for_outer_scope(self) -> None:
        """A naive ``rsplit("::", 1)`` on the scope would corrupt
        ``Wrapper<ns::Tag>`` into ``Tag>`` -- this module reuses
        ``type_reachability``'s depth-aware stripping instead."""
        key = canonicalize_synthetic_ctor_dtor_key(
            "__abicheck_ctor__ns::Wrapper<dep::Tag>()"
        )
        assert key == CtorDtorCanonicalKey(owner="Wrapper<dep::Tag>", kind="ctor")


class TestFindCtorDtorKeyDriftMatches:
    def test_unique_pair_matches(self) -> None:
        old = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "x")}
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "y"
            )
        }
        matches = find_ctor_dtor_key_drift_matches(old, new)
        assert len(matches) == 1
        assert matches[0].old_key == "__abicheck_ctor__Calculator()"
        assert matches[0].new_key == "__abicheck_ctor__abicheck_lab::Calculator()"

    def test_ambiguous_new_side_blocks_the_merge(self) -> None:
        """Two distinct classes sharing a bare name, each independently
        gaining an unrelated same-signature constructor, must never
        cross-merge: 2+ new-side candidates for one canonical form refuses
        the match entirely, on both candidates."""
        old = {"__abicheck_ctor__Foo(int)": _func("Foo::Foo", "old")}
        new = {
            "__abicheck_ctor__ns1::Foo(int)": _func("Foo::Foo", "ns1"),
            "__abicheck_ctor__ns2::Foo(int)": _func("Foo::Foo", "ns2"),
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_ambiguous_old_side_blocks_the_merge(self) -> None:
        """Mirror of the new-side case: two distinctly-spelled old keys
        (two namespaces, both stripping to the same bare owner) sharing one
        canonical form must also refuse the match."""
        old = {
            "__abicheck_ctor__ns1::Foo(int)": _func("Foo::Foo", "old1"),
            "__abicheck_ctor__ns2::Foo(int)": _func("Foo::Foo", "old2"),
        }
        new = {"__abicheck_ctor__Foo(int)": _func("Foo::Foo", "new")}
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_real_param_change_does_not_match(self) -> None:
        old = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "old")}
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator(int)": _func(
                "Calculator::Calculator", "new"
            )
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_default_and_copy_ctor_match_independently(self) -> None:
        old = {
            "__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "d_old"),
            "__abicheck_ctor__Calculator(const Calculator&)": _func(
                "Calculator::Calculator", "c_old"
            ),
        }
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "d_new"
            ),
            "__abicheck_ctor__abicheck_lab::Calculator(const Calculator&)": _func(
                "Calculator::Calculator", "c_new"
            ),
        }
        matches = find_ctor_dtor_key_drift_matches(old, new)
        assert len(matches) == 2
        pairs = {(m.old_key, m.new_key) for m in matches}
        assert pairs == {
            (
                "__abicheck_ctor__Calculator()",
                "__abicheck_ctor__abicheck_lab::Calculator()",
            ),
            (
                "__abicheck_ctor__Calculator(const Calculator&)",
                "__abicheck_ctor__abicheck_lab::Calculator(const Calculator&)",
            ),
        }

    def test_never_applies_to_real_mangled_names(self) -> None:
        old = {"_ZN10CalculatorC1Ev": _func("Calculator::Calculator", "old")}
        new = {
            "_ZN13abicheck_lab10CalculatorC1Ev": _func("Calculator::Calculator", "new")
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    # -- Codex review, PR #761 finding 1: restrict the fallback to a
    # demonstrable legacy-bare/current-qualified pair -----------------------

    def test_a_old_bare_new_qualified_still_merges(self) -> None:
        """(a) Must not regress the original fix: legacy-bare old side,
        current-qualified new side, same class -- still merges."""
        old = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "old")}
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "new"
            )
        }
        matches = find_ctor_dtor_key_drift_matches(old, new)
        assert len(matches) == 1
        assert matches[0].old_key == "__abicheck_ctor__Calculator()"
        assert matches[0].new_key == "__abicheck_ctor__abicheck_lab::Calculator()"

    def test_b_two_already_qualified_owners_do_not_merge(self) -> None:
        """(b) A genuine namespace move: both sides are already
        namespace-qualified (``ns1::Foo`` vs ``ns2::Foo``). Must NOT merge --
        this is a real, breaking namespace change, not key-format drift."""
        old = {"__abicheck_ctor__ns1::Foo()": _func("Foo::Foo", "old")}
        new = {"__abicheck_ctor__ns2::Foo()": _func("Foo::Foo", "new")}
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_c_new_bare_old_qualified_still_merges(self) -> None:
        """(c) Reverse direction: legacy-bare NEW side, current-qualified
        OLD side (order must not matter) -- still merges."""
        old = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "old"
            )
        }
        new = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "new")}
        matches = find_ctor_dtor_key_drift_matches(old, new)
        assert len(matches) == 1
        assert matches[0].old_key == "__abicheck_ctor__abicheck_lab::Calculator()"
        assert matches[0].new_key == "__abicheck_ctor__Calculator()"

    def test_d_two_identical_bare_keys_never_reach_this_module(self) -> None:
        """(d) Two identical bare keys on both sides join on the
        pre-existing exact-key path in ``diff_symbols._diff_functions`` --
        confirmed end to end, since that path never routes a key present on
        both sides through ``reconcile_ctor_dtor_key_drift``'s "unmatched"
        narrowing at all, so it is silent (``NO_CHANGE``) without ever
        exercising this module's own asymmetry check.

        Separately (in case the two dicts were somehow both passed as
        "unmatched" candidates anyway): two identical bare owners must
        still be refused by :func:`find_ctor_dtor_key_drift_matches`
        itself -- both are bare, not a legacy-bare/current-qualified pair
        -- which is the same guard that makes (b) above refuse two
        already-qualified owners."""
        old = _snap(
            "1.0", [_func("Calculator::Calculator", "__abicheck_ctor__Calculator()")]
        )
        new = _snap(
            "2.0", [_func("Calculator::Calculator", "__abicheck_ctor__Calculator()")]
        )
        assert _diff_functions(old, new) == []
        same = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "x")}
        assert find_ctor_dtor_key_drift_matches(same, same) == []


class TestDiffFunctionsCtorDtorKeyDrift:
    """End-to-end through ``diff_symbols._diff_functions`` (and, for (a),
    through the public ``compare()`` entry point) -- the motivating
    napetrov/abicheck-bazel-lab ``Calculator`` scenario."""

    def test_a_unchanged_ctor_across_key_format_drift_is_silent(self) -> None:
        old = _snap(
            "1.0", [_func("Calculator::Calculator", "__abicheck_ctor__Calculator()")]
        )
        new = _snap(
            "2.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator()",
                )
            ],
        )
        assert _diff_functions(old, new) == []
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []

    def test_b_cross_namespace_collision_not_merged(self) -> None:
        old = _snap("1.0", [_func("Foo::Foo", "__abicheck_ctor__Foo(int)")])
        new = _snap(
            "2.0",
            [
                _func("Foo::Foo", "__abicheck_ctor__ns1::Foo(int)"),
                _func("Foo::Foo", "__abicheck_ctor__ns2::Foo(int)"),
            ],
        )
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert kinds.count(ChangeKind.FUNC_REMOVED) == 1
        assert kinds.count(ChangeKind.FUNC_ADDED) == 2

    def test_c_real_param_change_still_reported(self) -> None:
        old = _snap(
            "1.0", [_func("Calculator::Calculator", "__abicheck_ctor__Calculator()")]
        )
        new = _snap(
            "2.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator(int)",
                    params=[Param(name="x", type="int")],
                )
            ],
        )
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_ADDED in kinds

    def test_d_default_and_copy_ctor_each_merge_independently(self) -> None:
        old = _snap(
            "1.0",
            [
                _func("Calculator::Calculator", "__abicheck_ctor__Calculator()"),
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__Calculator(const Calculator&)",
                    params=[Param(name="other", type="const Calculator&")],
                ),
            ],
        )
        new = _snap(
            "2.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator()",
                ),
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator(const Calculator&)",
                    params=[Param(name="other", type="const Calculator&")],
                ),
            ],
        )
        assert _diff_functions(old, new) == []

    def test_e_destructor_key_drift_is_silent(self) -> None:
        old = _snap("1.0", [_func("Calculator::~Calculator", "~Calculator")])
        new = _snap(
            "2.0", [_func("Calculator::~Calculator", "~abicheck_lab::Calculator")]
        )
        assert _diff_functions(old, new) == []

    def test_f_real_namespace_move_between_two_qualified_owners_reported(
        self,
    ) -> None:
        """Codex review, PR #761 finding 1: two already-qualified owners
        (``ns1::Foo`` vs ``ns2::Foo``) must be reported as a real
        removed+added, not silently merged into ``NO_CHANGE``."""
        old = _snap("1.0", [_func("Foo::Foo", "__abicheck_ctor__ns1::Foo()")])
        new = _snap("2.0", [_func("Foo::Foo", "__abicheck_ctor__ns2::Foo()")])
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert kinds.count(ChangeKind.FUNC_REMOVED) == 1
        assert kinds.count(ChangeKind.FUNC_ADDED) == 1


class TestIterMatchedFunctionPairsExposesCtorDtorReconciliation:
    """Codex review, PR #761 finding 2: a reconciled ctor/dtor pair must
    also be visible to detectors OTHER than ``_check_function_signature`` --
    otherwise a real, non-key-format-drift property change on that same
    pair (access narrowing, inline transition, ...) silently disappears."""

    def test_iter_matched_function_pairs_includes_reconciled_pair(self) -> None:
        old_map = {
            "__abicheck_ctor__Calculator()": _func(
                "Calculator::Calculator", "__abicheck_ctor__Calculator()"
            )
        }
        new_map = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator",
                "__abicheck_ctor__abicheck_lab::Calculator()",
            )
        }
        pairs = list(iter_matched_function_pairs(old_map, new_map))
        assert len(pairs) == 1
        key, f_old, f_new = pairs[0]
        assert key == "__abicheck_ctor__abicheck_lab::Calculator()"
        assert f_old is old_map["__abicheck_ctor__Calculator()"]
        assert f_new is new_map["__abicheck_ctor__abicheck_lab::Calculator()"]

    def test_iter_matched_function_pairs_still_yields_exact_matches(self) -> None:
        f_old = _func("plain", "_ZN5plainEv")
        f_new = _func("plain", "_ZN5plainEv")
        old_map = {"_ZN5plainEv": f_old}
        new_map = {"_ZN5plainEv": f_new}
        assert list(iter_matched_function_pairs(old_map, new_map)) == [
            ("_ZN5plainEv", f_old, f_new)
        ]

    def test_ctor_going_public_to_private_across_key_drift_is_reported(
        self,
    ) -> None:
        """The motivating case: an old-bare/new-qualified ctor pair whose
        new side ALSO went public -> private. Must still report
        ``METHOD_ACCESS_CHANGED`` -- this is exactly what silently
        vanished before ``iter_matched_function_pairs`` existed, since
        ``_diff_access_levels`` builds its own fresh exact-key join and
        the reconciled pair's old/new keys never intersect it."""
        old = _snap(
            "1.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__Calculator()",
                    access=AccessLevel.PUBLIC,
                )
            ],
        )
        new = _snap(
            "2.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator()",
                    access=AccessLevel.PRIVATE,
                )
            ],
        )
        changes = _diff_access_levels(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.METHOD_ACCESS_CHANGED in kinds

    def test_ctor_inline_transition_across_key_drift_is_reported(self) -> None:
        """Same shape, for ``FUNC_BECAME_INLINE`` via
        ``_check_inline_transitions`` -- run from inside ``_diff_functions``
        itself, over the SAME ``old_map``/``new_map``
        ``reconcile_ctor_dtor_key_drift`` already consulted (this is the
        specific case that regressed when reconciliation used to mutate
        ``old_map`` in place -- see ``reconcile_ctor_dtor_key_drift``'s own
        docstring)."""
        old = _snap(
            "1.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__Calculator()",
                    is_inline=False,
                )
            ],
        )
        new = _snap(
            "2.0",
            [
                _func(
                    "Calculator::Calculator",
                    "__abicheck_ctor__abicheck_lab::Calculator()",
                    is_inline=True,
                )
            ],
        )
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.FUNC_BECAME_INLINE in kinds
        assert ChangeKind.FUNC_REMOVED not in kinds
        assert ChangeKind.FUNC_ADDED not in kinds


# -- Property tests (AGENTS.md "Primitive-level property tests") -----------

_scope_component = st.text(
    alphabet=string.ascii_lowercase, min_size=1, max_size=8
).filter(lambda s: s[0].isalpha())


def _ctor_key(scope: str) -> str:
    return f"__abicheck_ctor__{scope}()"


class TestCanonicalizationProperties:
    @given(scope=_scope_component)
    def test_idempotent(self, scope: str) -> None:
        key = _ctor_key(scope)
        first = canonicalize_synthetic_ctor_dtor_key(key)
        second = canonicalize_synthetic_ctor_dtor_key(key)
        assert first == second

    @given(ns=_scope_component, cls=_scope_component)
    def test_invariant_to_namespace_qualification(self, ns: str, cls: str) -> None:
        bare = canonicalize_synthetic_ctor_dtor_key(_ctor_key(cls))
        qualified = canonicalize_synthetic_ctor_dtor_key(_ctor_key(f"{ns}::{cls}"))
        assert bare == qualified
        assert bare is not None and bare.owner == cls

    @given(
        ns_a=_scope_component,
        cls_a=_scope_component,
        ns_b=_scope_component,
        cls_b=_scope_component,
    )
    def test_distinct_bare_classes_never_collide(
        self, ns_a: str, cls_a: str, ns_b: str, cls_b: str
    ) -> None:
        if cls_a == cls_b:
            return
        key_a = canonicalize_synthetic_ctor_dtor_key(_ctor_key(f"{ns_a}::{cls_a}"))
        key_b = canonicalize_synthetic_ctor_dtor_key(_ctor_key(f"{ns_b}::{cls_b}"))
        assert key_a != key_b

    @given(scope=_scope_component)
    def test_non_synthetic_key_never_canonicalizes(self, scope: str) -> None:
        # A real Itanium mangled name never starts with the synthetic
        # prefixes, so it must always canonicalize to None -- this module
        # must never be applied to a real mangled symbol (see its own
        # module docstring's scope note).
        assert canonicalize_synthetic_ctor_dtor_key(f"_ZN{len(scope)}{scope}Ev") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
