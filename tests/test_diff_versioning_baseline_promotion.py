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

"""``diff_versioning.promote_baseline_violation_findings`` (Codex review,
PR #1221): the shared promotion helper that hardens the two standalone
declared-runtime-floor/wheel-packaging checks whose catalog default verdict
is RISK (``PLATFORM_BASELINE_FLOOR_RAISED``/``MACOS_DEPLOYMENT_TARGET_RAISED``)
to BREAKING wherever either the two-sided ``compare()`` path or the
``--no-baseline`` audit path produces one -- plus the two follow-up findings
this same review round produced: ``WHEEL_RPATH_NOT_PORTABLE`` must stay
unpromoted (its own heuristic, "almost always" a build artifact, is not
proof of an unresolvable dependency), and the promotion must run before
``_filter_suppressed_changes`` disposes of a match, so a suppressed
occurrence still *records* the correct BREAKING verdict (root ``AGENTS.md``'s
"Record before disposing").

Split out of ``tests/test_environment_drift.py`` rather than grown in place
(architecture/debt.yaml no-growth review, Codex P1 finding on PR #1221): that
module's own baseline was already carrying every one of this PR's other
env-matrix/runtime-floor test additions, and these three classes are a
self-contained unit testing one new function (and its two immediate
corrections) rather than the drift-report/DT_RELR/hash-style/time64
coverage the parent module's own docstring describes -- a genuinely
separable behavior axis, not an unavoidable addition to the parent file.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_types import Change
from abicheck.diff_helpers import make_change
from abicheck.diff_versioning import promote_baseline_violation_findings
from abicheck.elf_metadata import ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.model import AbiSnapshot


def _elf(**kwargs) -> ElfMetadata:
    """ElfMetadata with the parse-generation markers the detectors gate on."""
    kwargs.setdefault("machine", "EM_X86_64")
    kwargs.setdefault("hash_styles", frozenset({"gnu"}))
    return ElfMetadata(**kwargs)


def _snap(elf: ElfMetadata, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        elf=elf,
        elf_only_mode=True,
        platform="elf",
        **kwargs,
    )


def _kinds(changes) -> set[ChangeKind]:
    return {c.kind for c in changes}


class TestPromoteBaselineViolationFindings:
    """``promote_baseline_violation_findings`` (Codex review, P1): the two
    standalone checks whose catalog default verdict is RISK
    (``PLATFORM_BASELINE_FLOOR_RAISED``/``MACOS_DEPLOYMENT_TARGET_RAISED``)
    each only ever emit a finding when the candidate's own requirement
    already exceeds the declared floor -- so any occurrence of one of these
    two kinds must be unconditionally promoted to BREAKING, regardless of
    which check produced it, while every other kind (including the three
    standalone checks that already default to BREAKING, and
    ``WHEEL_RPATH_NOT_PORTABLE``, whose own heuristic nature keeps it at
    RISK -- see the class below) is left untouched. States the invariant
    generally, over every member of the promoted set, rather than pinning
    one example kind."""

    _PROMOTED_KINDS = (
        ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED,
        ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED,
    )

    #: Sibling standalone-check kinds that already default to BREAKING in
    #: the catalog and must not be touched by this promotion at all (their
    #: ``effective_verdict`` must stay unset -- the catalog default already
    #: does the right thing without a modulation).
    _UNPROMOTED_KINDS = (
        ChangeKind.MUSLLINUX_GLIBC_DEPENDENCY_DETECTED,
        ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH,
        ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION,
        # WHEEL_RPATH_NOT_PORTABLE deliberately stays at its catalog-default
        # RISK severity (see TestWheelRpathNotPortableStaysAtRisk below): a
        # non-$ORIGIN-relative RPATH entry is, per
        # check_wheel_rpath_not_portable's own docstring, "almost always" a
        # build artifact, not proof the dependency it names is actually
        # unresolvable -- a separate closure/reachability check would be
        # needed to prove that. Unconditionally promoting this heuristic
        # finding to BREAKING would manufacture a hard break from what is
        # genuinely only portability-RISK evidence.
        ChangeKind.WHEEL_RPATH_NOT_PORTABLE,
    )

    def _change(self, kind: ChangeKind) -> Change:
        return make_change(kind, symbol="<platform-baseline>", name="libfoo.so")

    @pytest.mark.parametrize("kind", _PROMOTED_KINDS)
    def test_every_promoted_kind_becomes_breaking(self, kind: ChangeKind) -> None:
        change = self._change(kind)
        assert change.effective_verdict is None
        (result,) = promote_baseline_violation_findings([change])
        assert result is change
        assert result.effective_verdict is Verdict.BREAKING
        assert result.modulation_rule == "baseline_violation_always_breaking"

    @pytest.mark.parametrize("kind", _UNPROMOTED_KINDS)
    def test_unrelated_breaking_default_kinds_are_left_alone(
        self, kind: ChangeKind
    ) -> None:
        change = self._change(kind)
        promote_baseline_violation_findings([change])
        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_a_kind_outside_either_set_is_left_alone(self) -> None:
        # Negative control unrelated to runtime-floor/wheel-packaging
        # entirely -- the function must not touch it.
        change = self._change(ChangeKind.RUNTIME_FLOOR_RAISED)
        promote_baseline_violation_findings([change])
        assert change.effective_verdict is None

    @pytest.mark.parametrize("kind", _PROMOTED_KINDS)
    def test_an_existing_modulation_is_never_overwritten(
        self, kind: ChangeKind
    ) -> None:
        # Matches apply_runtime_floor_contract's own convention: a finding
        # some earlier hook already modulated is left exactly as that hook
        # left it.
        change = self._change(kind)
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = "some_earlier_hook"
        promote_baseline_violation_findings([change])
        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "some_earlier_hook"

    def test_mutates_and_returns_the_same_list(self) -> None:
        changes = [self._change(k) for k in self._PROMOTED_KINDS]
        result = promote_baseline_violation_findings(changes)
        assert result is changes
        assert all(c.effective_verdict is Verdict.BREAKING for c in changes)

    def test_empty_list_is_a_no_op(self) -> None:
        assert promote_baseline_violation_findings([]) == []

    def test_two_sided_and_no_baseline_paths_agree_for_every_promoted_kind(
        self,
    ) -> None:
        """The generalized sibling of ``TestRunNoBaselineCompareEnvMatrix``
        in ``tests/test_no_baseline_compare.py`` (which pins one example,
        GLIBC/PLATFORM_BASELINE_FLOOR_RAISED): for every one of the two
        promoted kinds, a two-sided ``compare()`` of a floor-violating
        candidate against itself and a ``--no-baseline`` audit of the
        identical candidate must reach the identical BREAKING verdict.
        ``WHEEL_RPATH_NOT_PORTABLE`` is deliberately not a case here any
        more -- it is no longer promoted at all, see
        ``TestWheelRpathNotPortableStaysAtRisk`` below for its own
        two-sided/no-baseline parity coverage at RISK severity."""
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        cases = {
            ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED: (
                _elf(
                    needed=["libc.so.6"],
                    versions_required={"libc.so.6": ["GLIBC_2.34"]},
                ),
                {"GLIBC": "2.28"},
            ),
            ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED: (
                None,
                {"MACOS_DEPLOYMENT_TARGET": "10.14"},
            ),
        }
        for kind, (elf, floors) in cases.items():
            if elf is None:
                continue  # macOS case needs MachoMetadata; covered directly
                # by tests/test_diff_wheel_deployment.py's own end-to-end
                # class instead of being duplicated here with a second
                # synthetic-fixture builder.
            matrix = EnvironmentMatrix(runtime_floors=floors)
            candidate = _snap(elf)
            two_sided = compare(candidate, candidate, env_matrix=matrix)
            no_baseline = run_no_baseline_compare(candidate, env_matrix=matrix)
            assert kind in _kinds(two_sided.changes), kind
            assert kind in {c.kind for c in no_baseline.findings}, kind
            assert two_sided.verdict is Verdict.BREAKING, kind
            assert no_baseline.diff.verdict == two_sided.verdict, kind


class TestWheelRpathNotPortableStaysAtRisk:
    """Codex review (P1): unlike ``PLATFORM_BASELINE_FLOOR_RAISED``/
    ``MACOS_DEPLOYMENT_TARGET_RAISED``, ``WHEEL_RPATH_NOT_PORTABLE`` is not
    promoted to BREAKING by ``promote_baseline_violation_findings`` and must
    keep its catalog-default RISK (``COMPATIBLE_WITH_RISK``) verdict on both
    the two-sided ``compare()`` path and the ``--no-baseline`` audit path --
    ``check_wheel_rpath_not_portable``'s own docstring states this is a
    heuristic ("almost always" a build artifact), not proof the named
    dependency is actually unresolvable, so unconditionally hardening it to
    BREAKING would manufacture a break from portability-RISK evidence
    alone."""

    def test_not_in_promoted_kinds(self) -> None:
        assert (
            ChangeKind.WHEEL_RPATH_NOT_PORTABLE
            not in TestPromoteBaselineViolationFindings._PROMOTED_KINDS
        )

    def test_promote_baseline_violation_findings_leaves_it_alone(self) -> None:
        change = make_change(
            ChangeKind.WHEEL_RPATH_NOT_PORTABLE,
            symbol="<platform-baseline>",
            name="libfoo.so",
        )
        promote_baseline_violation_findings([change])
        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_two_sided_and_no_baseline_paths_both_stay_at_risk(self) -> None:
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        elf = _elf(rpath="/usr/local/lib")
        matrix = EnvironmentMatrix(runtime_floors={"WHEEL_CONTEXT": "1"})
        candidate = _snap(elf)
        two_sided = compare(candidate, candidate, env_matrix=matrix)
        no_baseline = run_no_baseline_compare(candidate, env_matrix=matrix)
        assert ChangeKind.WHEEL_RPATH_NOT_PORTABLE in _kinds(two_sided.changes)
        assert ChangeKind.WHEEL_RPATH_NOT_PORTABLE in {
            c.kind for c in no_baseline.findings
        }
        assert two_sided.verdict is not Verdict.BREAKING
        assert no_baseline.diff.verdict == two_sided.verdict


class TestPromotionRunsBeforeSuppressionRecording:
    """Codex review (P2): ``promote_baseline_violation_findings`` must run
    over each check's full ``check_changes`` list *before*
    ``_filter_suppressed_changes`` disposes of a match into ``suppressed``
    -- not only over the already-filtered ``visible`` subset. Otherwise a
    suppression rule that happens to match a genuine
    ``PLATFORM_BASELINE_FLOOR_RAISED``/``MACOS_DEPLOYMENT_TARGET_RAISED``
    floor violation records that occurrence at the catalog's unpromoted
    RISK default rather than the correctly-promoted BREAKING verdict a
    *visible* (unsuppressed) occurrence of the identical violation gets --
    a direct violation of root ``AGENTS.md``'s "Record before disposing"
    principle: a suppressed finding's own record must still show what it
    actually was, with disposition (rule + reason) recorded separately, not
    manifested as a silently different severity."""

    def test_suppressed_floor_violation_still_records_breaking(self) -> None:
        from abicheck.suppression import Suppression, SuppressionList

        elf = _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.34"]},
        )
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        candidate = _snap(elf)
        suppression = SuppressionList(
            [
                Suppression(
                    symbol="<platform-baseline>",
                    change_kind="platform_baseline_floor_raised",
                    reason="acknowledged, tracked separately",
                )
            ]
        )

        # Unsuppressed control: the same violation, no suppression rule.
        unsuppressed = compare(candidate, candidate, env_matrix=matrix)
        control = [
            c
            for c in unsuppressed.changes
            if c.kind is ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED
        ]
        assert len(control) == 1
        assert control[0].effective_verdict is Verdict.BREAKING

        # Suppressed case: the identical violation, now matched by the rule
        # above. It must be recorded in `suppressed_changes` at the SAME
        # promoted BREAKING verdict as the control -- not the unpromoted
        # catalog-default RISK.
        result = compare(
            candidate, candidate, suppression=suppression, env_matrix=matrix
        )
        assert ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED not in _kinds(result.changes)
        suppressed = [
            c
            for c in result.suppressed_changes
            if c.kind is ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED
        ]
        assert len(suppressed) == 1
        assert suppressed[0].effective_verdict is Verdict.BREAKING
        assert suppressed[0].modulation_rule == "baseline_violation_always_breaking"
        assert suppressed[0].suppression_rule is not None
