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

"""Persistent hygiene debt is reported, counted -- and not scored.

The two limits that keep the exclusion safe, stated as tests: it applies
only to findings whose resolved category is ``COMPATIBLE_WITH_RISK``, and
never to a kind the project's own policy document speaks about.

Split out of ``tests/test_cross_source_evolution.py``, which these pushed
past the 1200-line test maximum.
"""

from __future__ import annotations

from abicheck.buildsource import cross_source_checks as _crosschecks
from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change
from abicheck.policy.persistent_hygiene import drop_persistent_hygiene
from abicheck.policy_file import PolicyFile

#: Every cross-source hygiene check's ``ChangeKind``, read off the check
#: registry rather than hand-listed, so a newly registered check joins the
#: category-scoping tests below automatically.
_CROSS_SOURCE_KINDS = tuple(
    ChangeKind(name)
    for name in sorted(
        v
        for k, v in vars(_crosschecks).items()
        if k.startswith("CHECK_") and isinstance(v, str)
    )
)


class TestPersistentHygieneVerdictExclusionIsCategoryScoped:
    """The persistent-hygiene verdict exclusion must be scoped to findings
    that resolve to ``COMPATIBLE_WITH_RISK``, and must not be reachable for
    anything a project deliberately gated on.

    Stated against ``excluded_from_verdict_as_persistent_hygiene`` directly
    rather than only through ``compare()``: two of the eleven cross-source
    checks default to ``API_BREAK``, and the predicate is the one place the
    category condition lives. A version of this fix that excluded every
    PERSISTENT finding would hide a persistent ODR violation -- a real
    defect *in the candidate*, which being pre-existing does not make less
    true -- and no ``compare()``-level test over the RISK-kind checks would
    have noticed.
    """

    @staticmethod
    def _persistent(kind: ChangeKind, **kw: object) -> Change:
        return Change(
            kind=kind,
            symbol="sym",
            description="",
            cross_source_evolution=CrossSourceEvolution.PERSISTENT,
            **kw,  # type: ignore[arg-type]
        )

    def test_risk_kinds_are_excluded(self) -> None:
        from abicheck.policy.classification import (
            excluded_from_verdict_as_persistent_hygiene,
            policy_kind_sets,
        )

        sets = policy_kind_sets("strict_abi")
        # Every cross-source check whose kind carries COMPATIBLE_WITH_RISK,
        # enumerated from the registry rather than hand-listed so a newly
        # added hygiene check is covered without editing this test.
        risk_checks = [k for k in _CROSS_SOURCE_KINDS if k in sets[3]]
        assert risk_checks, "no RISK-category cross-source kinds found"
        for kind in risk_checks:
            assert excluded_from_verdict_as_persistent_hygiene(
                self._persistent(kind), *sets
            ), kind

    def test_api_break_kinds_are_not_excluded(self) -> None:
        """A persistent ``odr_type_variant`` / ``header_build_context_
        mismatch`` still drives the verdict: it names a defect the candidate
        has, not bookkeeping about what changed."""
        from abicheck.policy.classification import (
            excluded_from_verdict_as_persistent_hygiene,
            policy_kind_sets,
        )

        sets = policy_kind_sets("strict_abi")
        non_risk = [k for k in _CROSS_SOURCE_KINDS if k not in sets[3]]
        assert non_risk, (
            "expected at least one cross-source check outside the RISK "
            "category -- if every check became RISK this test is vacuous "
            "and the guard it protects is untested"
        )
        for kind in non_risk:
            assert not excluded_from_verdict_as_persistent_hygiene(
                self._persistent(kind), *sets
            ), kind

    def test_an_explicit_promotion_wins_over_the_exclusion(self) -> None:
        """A per-finding ``effective_verdict`` -- what a policy's
        modulation/reclassification sets -- outranks the exclusion. A project
        that has said it wants to be gated on a hygiene kind is gated on it,
        persistent or not."""
        from abicheck.policy.classification import (
            excluded_from_verdict_as_persistent_hygiene,
            policy_kind_sets,
        )

        sets = policy_kind_sets("strict_abi")
        promoted = self._persistent(
            ChangeKind.EXPORTED_NOT_PUBLIC, effective_verdict=Verdict.BREAKING
        )
        assert not excluded_from_verdict_as_persistent_hygiene(promoted, *sets)

    def test_other_evolution_states_are_untouched(self) -> None:
        """Only PERSISTENT. An INTRODUCED finding is this release's doing and
        must still drive the verdict; an unstamped finding (every non-
        cross-source finding in the codebase) must be unaffected, which is
        what keeps this change inert for runs that never ran a hygiene
        check."""
        from abicheck.policy.classification import (
            excluded_from_verdict_as_persistent_hygiene,
            policy_kind_sets,
        )

        sets = policy_kind_sets("strict_abi")
        for state in (
            CrossSourceEvolution.INTRODUCED,
            CrossSourceEvolution.RESOLVED,
            CrossSourceEvolution.NOT_EVALUATED,
            None,
        ):
            change = Change(
                kind=ChangeKind.EXPORTED_NOT_PUBLIC,
                symbol="sym",
                description="",
                cross_source_evolution=state,
            )
            assert not excluded_from_verdict_as_persistent_hygiene(change, *sets), state


class TestPersistentHygieneRespectsAnExplicitPolicy:
    """A kind the project's policy document speaks about is never excluded.

    Found by *executing* the case rather than reasoning about it. An earlier
    revision of ``policy.persistent_hygiene.drop_persistent_hygiene`` carried a docstring
    asserting this "fails in the safe direction" -- it did not: a project
    that deliberately set ``exported_not_public: breaking`` had every
    persistent instance silently dropped from its own verdict, the exact
    opposite of what it asked for. ``overrides:``/``reclassify:`` are applied
    by ``PolicyFile.compute_verdict``, not by the base profile's kind sets,
    so the category the exclusion resolves cannot see them.
    """

    @staticmethod
    def _persistent() -> Change:
        return Change(
            kind=ChangeKind.EXPORTED_NOT_PUBLIC,
            symbol="undeclared",
            description="",
            cross_source_evolution=CrossSourceEvolution.PERSISTENT,
        )

    def test_an_explicit_promotion_keeps_the_finding_in_the_verdict(self) -> None:
        from abicheck.checker_policy import Verdict

        policy = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.EXPORTED_NOT_PUBLIC: Verdict.BREAKING},
        )
        scored = drop_persistent_hygiene([self._persistent()], "strict_abi", policy)
        assert len(scored) == 1
        # And it really reaches the verdict, not merely the population.
        assert policy.compute_verdict(scored) == Verdict.BREAKING

    def test_an_explicit_demotion_also_keeps_it(self) -> None:
        """Withheld for *any* mention, not only a promotion: a project that
        has written the kind into its policy is managing that kind itself,
        and quietly removing its findings is not the exclusion's call."""
        from abicheck.checker_policy import Verdict

        policy = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.EXPORTED_NOT_PUBLIC: Verdict.COMPATIBLE},
        )
        assert (
            len(drop_persistent_hygiene([self._persistent()], "strict_abi", policy))
            == 1
        )

    def test_a_reclassify_rule_keeps_its_matched_finding(self) -> None:
        """The `reclassify:` namespace, not just `overrides:`.

        Found in review. The first version mined rules for
        `rule.kinds`/`rule.kind` -- neither of which exists; the field is
        `change_kind` -- so every reclassify rule was silently ignored and a
        rule like `{kind: exported_not_public, symbol: x, to: break}` had
        its finding dropped before `PolicyFile.compute_verdict` ever saw it,
        turning a deliberate `break` into `NO_CHANGE`. Asking the rule
        whether it matches also covers a rule with no kind selector at all,
        which mining kind names could never have done.
        """
        from abicheck.checker_policy import Verdict
        from abicheck.policy.reclassify import ReclassifyRule

        matched = self._persistent()
        unmatched = Change(
            kind=ChangeKind.EXPORTED_NOT_PUBLIC,
            symbol="something_else",
            description="",
            cross_source_evolution=CrossSourceEvolution.PERSISTENT,
        )
        policy = PolicyFile(
            base_policy="strict_abi",
            reclassify=[
                ReclassifyRule(
                    to_verdict=Verdict.BREAKING,
                    to="break",
                    change_kind="exported_not_public",
                    symbol="undeclared",
                )
            ],
        )
        scored = drop_persistent_hygiene([matched, unmatched], "strict_abi", policy)
        # Only the rule's own target is retained -- the exclusion stays in
        # force for every finding the rule does not name.
        assert [c.symbol for c in scored] == ["undeclared"]
        assert policy.compute_verdict(scored) == Verdict.BREAKING

    def test_a_kindless_reclassify_rule_is_honoured_too(self) -> None:
        """A rule scoped only by symbol or namespace legitimately applies to
        a hygiene finding, and no amount of collecting *kind* names would
        ever have found it."""
        from abicheck.checker_policy import Verdict
        from abicheck.policy.reclassify import ReclassifyRule

        policy = PolicyFile(
            base_policy="strict_abi",
            reclassify=[
                ReclassifyRule(
                    to_verdict=Verdict.BREAKING, to="break", symbol="undeclared"
                )
            ],
        )
        assert (
            len(drop_persistent_hygiene([self._persistent()], "strict_abi", policy))
            == 1
        )

    def test_an_unrelated_override_does_not_disable_the_exclusion(self) -> None:
        """Vacuity guard. If the guard were "any policy document at all", the
        exclusion would stop working for every project that has a policy
        file -- which is most of them -- and the two tests above would still
        pass."""
        from abicheck.checker_policy import Verdict

        policy = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        assert drop_persistent_hygiene([self._persistent()], "strict_abi", policy) == []

    def test_no_policy_document_still_excludes(self) -> None:

        assert drop_persistent_hygiene([self._persistent()], "strict_abi", None) == []
