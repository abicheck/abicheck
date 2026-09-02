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

import dataclasses
import string

import pytest
from hypothesis import given, strategies as st

import abicheck.finding_identity_ctor_dtor as ctor_dtor_mod
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_symbols import (
    _detect_newly_deleted_functions,
    _diff_access_levels,
    _diff_func_deprecated,
    _diff_functions,
    _diff_param_defaults,
)
from abicheck.fact_provenance import func_fact_key
from abicheck.finding_identity_ctor_dtor import (
    CtorDtorCanonicalKey,
    canonicalize_synthetic_ctor_dtor_key,
    find_ctor_dtor_key_drift_matches,
    iter_matched_function_pairs,
)
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    Visibility,
    replace_with_fact_sync,
)


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
    def test_unique_bare_vs_qualified_pair_no_longer_matches(self) -> None:
        """A unique canonical-form pair alone is not enough to merge --
        PR #761 finding 1: a bare-old/qualified-new pair is indistinguishable
        from a real global-to-namespace move, so the bare-vs-qualified
        fallback is disabled regardless of uniqueness -- see the
        bare-vs-qualified test group further down in this class."""
        old = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "x")}
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "y"
            )
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

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

    def test_default_and_copy_ctor_bare_vs_qualified_no_longer_match(self) -> None:
        """Same shape as above, over two independent overloads at once --
        neither merges now that the bare-vs-qualified fallback is disabled."""
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
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_never_applies_to_real_mangled_names(self) -> None:
        old = {"_ZN10CalculatorC1Ev": _func("Calculator::Calculator", "old")}
        new = {
            "_ZN13abicheck_lab10CalculatorC1Ev": _func("Calculator::Calculator", "new")
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    # -- Codex review, PR #761 finding 1: the bare-vs-qualified fallback is
    # permanently disabled -- investigated and found structurally unsound.
    # A bare owner scope on a CURRENT-format snapshot means "no enclosing
    # namespace at extraction time" just as validly as it means "predates
    # PR #582" -- the two are indistinguishable from the keys alone, and no
    # independent per-snapshot evidence exists to disambiguate them (see
    # ``finding_identity_ctor_dtor.py``'s module docstring for the full
    # investigation). So a bare/qualified pair -- in EITHER direction -- no
    # longer merges, full stop: this is the safe "prefer under-merging"
    # direction, not a regression in disguise. ------------------------------

    def test_a_old_bare_new_qualified_no_longer_merges(self) -> None:
        """(a) A bare-old/qualified-new pair -- indistinguishable from a
        real global-to-namespace move -- must NOT merge."""
        old = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "old")}
        new = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "new"
            )
        }
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_b_two_already_qualified_owners_do_not_merge(self) -> None:
        """(b) A genuine namespace move: both sides are already
        namespace-qualified (``ns1::Foo`` vs ``ns2::Foo``). Must NOT merge --
        this is a real, breaking namespace change, not key-format drift."""
        old = {"__abicheck_ctor__ns1::Foo()": _func("Foo::Foo", "old")}
        new = {"__abicheck_ctor__ns2::Foo()": _func("Foo::Foo", "new")}
        assert find_ctor_dtor_key_drift_matches(old, new) == []

    def test_c_new_bare_old_qualified_no_longer_merges(self) -> None:
        """(c) Reverse direction: qualified-old/bare-new. Must also NOT
        merge, for the identical reason as (a) -- direction doesn't matter,
        the ambiguity is symmetric."""
        old = {
            "__abicheck_ctor__abicheck_lab::Calculator()": _func(
                "Calculator::Calculator", "old"
            )
        }
        new = {"__abicheck_ctor__Calculator()": _func("Calculator::Calculator", "new")}
        assert find_ctor_dtor_key_drift_matches(old, new) == []

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

    def test_a_bare_vs_qualified_ctor_drift_is_reported_not_merged(self) -> None:
        """PR #761 finding 1: the bare-vs-qualified fallback is disabled, so
        this now reports a removed+added pair rather than merging to
        ``NO_CHANGE`` -- the safe "under-merge" direction, since abicheck
        cannot tell this apart from a real global-to-namespace move (see
        ``finding_identity_ctor_dtor.py``'s module docstring)."""
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
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert kinds.count(ChangeKind.FUNC_REMOVED) == 1
        assert kinds.count(ChangeKind.FUNC_ADDED) == 1
        result = compare(old, new)
        assert result.verdict != Verdict.NO_CHANGE

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

    def test_d_default_and_copy_ctor_bare_vs_qualified_both_reported(self) -> None:
        """Same shape as (a), for two independent overloads: neither
        merges now that the fallback is disabled."""
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
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert kinds.count(ChangeKind.FUNC_REMOVED) == 2
        assert kinds.count(ChangeKind.FUNC_ADDED) == 2

    def test_e_destructor_bare_vs_qualified_drift_is_reported(self) -> None:
        old = _snap("1.0", [_func("Calculator::~Calculator", "~Calculator")])
        new = _snap(
            "2.0", [_func("Calculator::~Calculator", "~abicheck_lab::Calculator")]
        )
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert kinds.count(ChangeKind.FUNC_REMOVED) == 1
        assert kinds.count(ChangeKind.FUNC_ADDED) == 1

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
    pair (access narrowing, inline transition, ...) silently disappears.

    Finding 1 (above) permanently disables the bare-vs-qualified predicate
    that used to be the only PRODUCER of such a reconciled pair, so these
    tests force it back on via monkeypatch to exercise this ``iter_matched_
    function_pairs`` WIRING itself, independent of that decision -- see
    ``TestCtorDtorReconciliationConsumersWithForcedMatch``'s docstring for
    the same reasoning applied to findings 2/3's other consumers.
    """

    @pytest.fixture(autouse=True)
    def _force_reconciliation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ctor_dtor_mod,
            "_is_legacy_qualification_drift_pair",
            lambda old_key, new_key: True,
        )

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


class TestCtorDtorReconciliationConsumersWithForcedMatch:
    """Codex review, PR #761 findings 2 and 3: fix the WIRING that consumes
    an already-resolved ctor/dtor synthetic-key drift match, independent of
    whether the bare-vs-qualified predicate that used to PRODUCE such a
    match is itself enabled. It no longer is (finding 1, permanently
    disabled -- see ``TestFindCtorDtorKeyDriftMatches``'s
    "finding 1" tests above and ``finding_identity_ctor_dtor.py``'s module
    docstring), so these tests force
    ``_is_legacy_qualification_drift_pair`` back on via monkeypatch purely
    to exercise the two DOWNSTREAM consumers this fixes
    (``_detect_newly_deleted_functions``, ``_diff_func_deprecated``,
    ``_diff_param_defaults``) against a real reconciled pair -- the fix
    itself lives entirely in how each consumer looks a reconciled pair up,
    not in whether reconciliation currently fires in production.
    """

    @pytest.fixture(autouse=True)
    def _force_reconciliation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ctor_dtor_mod,
            "_is_legacy_qualification_drift_pair",
            lambda old_key, new_key: True,
        )

    def test_finding_2_reconciled_pair_deletion_is_detected(self) -> None:
        """A legacy-key constructor gains ``= delete`` while the new
        snapshot already uses the qualified key. Before the fix,
        ``_detect_newly_deleted_functions``'s ``old_all.get(mangled)`` used
        the NEW key to probe the OLD function map and found nothing, so the
        deletion silently read as ``NO_CHANGE``."""
        old_ctor = _func("Calculator::Calculator", "__abicheck_ctor__Calculator()")
        new_ctor = dataclasses.replace(
            _func(
                "Calculator::Calculator",
                "__abicheck_ctor__abicheck_lab::Calculator()",
            ),
            is_deleted=True,
        )
        old_all = {old_ctor.mangled: old_ctor}
        new_all = {new_ctor.mangled: new_ctor}
        old_snap = _snap("1.0", [old_ctor])
        new_snap = _snap("2.0", [new_ctor])

        changes = _detect_newly_deleted_functions(old_all, new_all, old_snap, new_snap)
        kinds = [c.kind for c in changes]
        assert ChangeKind.FUNC_DELETED in kinds

    def test_finding_2_end_to_end_through_diff_functions(self) -> None:
        """Same scenario, through the full ``_diff_functions`` detector."""
        old_ctor = _func("Calculator::Calculator", "__abicheck_ctor__Calculator()")
        new_ctor = dataclasses.replace(
            _func(
                "Calculator::Calculator",
                "__abicheck_ctor__abicheck_lab::Calculator()",
            ),
            is_deleted=True,
        )
        old = _snap("1.0", [old_ctor])
        new = _snap("2.0", [new_ctor])
        changes = _diff_functions(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.FUNC_DELETED in kinds

    def _hybrid_snap(
        self, version: str, functions: list[Function], provenance: dict[str, str]
    ) -> AbiSnapshot:
        return AbiSnapshot(
            library="libtest.so.1",
            version=version,
            functions=functions,
            variables=[],
            types=[],
            from_headers=True,
            ast_producer="hybrid",
            fact_provenance=provenance,
        )

    def test_finding_3_deprecated_transition_across_reconciled_pair_is_reported(
        self,
    ) -> None:
        """A reconciled ctor/dtor pair whose new side gains
        ``[[deprecated]]``, on a hybrid snapshot where provenance is
        recorded separately under each side's own key. Before the fix,
        looking both sides up under the single (new-side) ``mangled`` key
        found the OLD snapshot's provenance entry missing, read the old
        side as an unknown producer, and silently suppressed the
        transition."""
        old_key = "__abicheck_ctor__Calculator()"
        new_key = "__abicheck_ctor__abicheck_lab::Calculator()"
        old_ctor = _func("Calculator::Calculator", old_key)
        # replace_with_fact_sync, not dataclasses.replace: `deprecated` is
        # Fact[...]-bridged (ADR-063 Phase 5), and a bare replace() lets the
        # stale sibling win under __post_init__'s "explicit Fact wins" rule.
        new_ctor = replace_with_fact_sync(
            _func("Calculator::Calculator", new_key), deprecated="use Bar instead"
        )
        old = self._hybrid_snap(
            "1.0", [old_ctor], {func_fact_key(old_key, "deprecated"): "castxml"}
        )
        new = self._hybrid_snap(
            "2.0", [new_ctor], {func_fact_key(new_key, "deprecated"): "castxml"}
        )

        changes = _diff_func_deprecated(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.FUNC_DEPRECATED_ADDED in kinds

    def test_finding_3_param_defaults_provenance_looked_up_per_side(self) -> None:
        """Sibling fix in ``_diff_param_defaults`` -- the identical
        single-shared-key bug (``key = func_fact_key(mangled,
        "param_defaults")`` probing both sides), caught while fixing
        finding 3's own named site (root-cause fix, not a one-off patch,
        per ``AGENTS.md``'s "Fix the cause, not the instance"). A removed
        default value across a reconciled pair, on a hybrid snapshot, must
        be reported."""
        old_key = "__abicheck_ctor__Calculator()"
        new_key = "__abicheck_ctor__abicheck_lab::Calculator()"
        old_ctor = _func(
            "Calculator::Calculator",
            old_key,
            params=[Param(name="x", type="int", default="0")],
        )
        new_ctor = _func(
            "Calculator::Calculator",
            new_key,
            params=[Param(name="x", type="int", default=None)],
        )
        old = self._hybrid_snap(
            "1.0", [old_ctor], {func_fact_key(old_key, "param_defaults"): "castxml"}
        )
        new = self._hybrid_snap(
            "2.0", [new_ctor], {func_fact_key(new_key, "param_defaults"): "castxml"}
        )

        changes = _diff_param_defaults(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.PARAM_DEFAULT_VALUE_REMOVED in kinds


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
