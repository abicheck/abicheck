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

"""Dimension 6 — no compatibility claim without sufficient evidence.

Split out of `test_skill_eval_graders.py` (tests/CLAUDE.md's "_lib/ pattern")
when that file crossed the AI-readiness 2000-line hard cap: this is its
largest class, and the one most of this session's Codex-review findings on
consumer-scoping (`--used-by`/`--required-symbol(s)`), contract-coverage
(`--contract`), and `full_verdict` grounding landed in. Shares fixtures with
its sibling via `_skill_eval_graders_fixtures.py` rather than a second copy.
"""

from __future__ import annotations

import json

import pytest
from _skill_eval_graders_fixtures import (
    SCENARIO_BREAKING,
    SCENARIO_COMPATIBLE,
    a_breaking_call,
    a_not_comparable_answer,
    build_run,
    claim_mod,
    dim,
    envelope,
    ev,
)


class TestDimensionSix:
    def _grade(self, tmp_path, final, scenario, calls=None, artifacts=None):
        run = build_run(
            tmp_path,
            final=final,
            calls=calls if calls is not None else [a_breaking_call()],
            artifacts=artifacts,
        )
        parsed, status = claim_mod.extract(final)
        return dim.dimension_6(run, scenario, ev.load_calls(run), parsed, status)

    def test_a_false_green_against_ground_truth_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_BREAKING,
        )
        assert result.status == "fail"
        assert any("safer than the truth" in r for r in result.reasons)

    def test_a_claim_greener_than_its_own_report_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_scoped_claim_is_not_held_to_an_earlier_unscoped_report(self, tmp_path):
        """The skill's own worked example: run the global comparison, then
        narrow via `--used-by`, and report the *scoped* answer. An earlier,
        more severe unscoped report of the same pair must not fail a claim
        that correctly cites the scoped call — that is a different question,
        not a milder report of the same one (Codex review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            a_breaking_call(
                1, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "BREAKING"}),
                "captured/1.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_scoped_claim_is_still_held_to_its_own_scoped_report(self, tmp_path):
        """Restricting the reckoning to scoped calls must not exempt a claim
        from the severity of the scoped call it actually cites."""
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_failed_scoped_call_does_not_exempt_a_cited_unscoped_report(
        self, tmp_path
    ):
        """A claim citing both a successful unscoped BREAKING report and a
        scoped call that FAILED before producing a verdict (e.g. an invalid
        consumer path, exit 64) must not get the only_scoped exemption --
        the scoped call never backed anything, so the real unscoped report
        must still count (Codex review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            {
                "seq": 1,
                "call_id": "c1",
                "argv": [
                    "compare",
                    "old.so",
                    "new.so",
                    "--used-by",
                    "no-such-consumer",
                ],
                "exit_code": 64,
                "stdout_path": "captured/1.out",
                "outputs": [],
            },
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0, 1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_scoped_self_comparison_does_not_exempt_a_cited_unscoped_report(
        self, tmp_path
    ):
        """`compare old.so old.so --used-by renderer` exits with a real
        verdict (trivially NO_CHANGE) while comparing nothing -- it must not
        satisfy the only_scoped exemption any more than a failed scoped call
        does, or a real unscoped BREAKING report gets dropped in favor of a
        scoped call that never actually compared the two sides (Codex
        review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            {
                "seq": 1,
                "call_id": "c1",
                "argv": ["compare", "old.so", "old.so", "--used-by", "renderer"],
                "exit_code": 0,
                "stdout_path": "captured/1.out",
                "outputs": [],
            },
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0, 1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "BREAKING"}),
                "captured/1.out": json.dumps({"verdict": "NO_CHANGE"}),
            },
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_scoped_self_comparison_does_not_satisfy_the_declared_target_check(
        self, tmp_path
    ):
        """The same self-comparison exclusion applied to the declared-target
        check: a self-comparison scoped to the exact right consumer still
        never compared the two sides, so it must not satisfy the requirement
        either."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            {
                "seq": 0,
                "call_id": "c0",
                "argv": ["compare", "old.so", "old.so", "--used-by", "renderer"],
                "exit_code": 0,
                "stdout_path": "captured/0.out",
                "outputs": [],
            }
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "NO_CHANGE"})},
        )
        assert result.status == "fail"
        assert any("declared" in r for r in result.reasons)

    def test_scoping_to_the_wrong_consumer_fails(self, tmp_path):
        """A scenario declaring `invocation.used_by: [renderer]` is testing
        whether the run scoped to *that* consumer specifically -- a call
        scoped to an unrelated one satisfies `is_consumer_scoped()` but never
        answered the actual question (Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "unrelated"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("declared" in r for r in result.reasons)

    def test_scoping_to_the_declared_consumer_passes(self, tmp_path):
        """The positive control for the check above."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_required_symbol_does_not_satisfy_a_declared_used_by_target(
        self, tmp_path
    ):
        """`--used-by` and `--required-symbol` are two distinct scoping
        mechanisms; a `--required-symbol analytics-daemon` call is a
        coincidental name collision, not an actual consumer analysis, and
        must not satisfy a scenario's declared `used_by: [analytics-daemon]`
        requirement (Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["analytics-daemon"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "analytics-daemon",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("used_by" in r for r in result.reasons)

    def test_required_symbol_scoping_is_recognized_too(self, tmp_path):
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"required_symbols": ["plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_teardown",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            # `full_verdict` present in the captured report too -- the claim
            # must be grounded in what a cited report actually said, not
            # merely match the scenario's own expected value (Codex review,
            # PR #808).
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "BREAKING", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_used_by_path_matches_the_declared_bare_consumer_name(self, tmp_path):
        """`--used-by` takes a real path to the consumer binary
        (references/abicheck-adapter.md's own worked example), not the bare
        logical name a scenario's invocation.used_by declares -- the literal
        operand alone would hard-fail every correctly-scoped run (Codex
        review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--used-by",
                    "workspace/consumer/renderer",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_covering_only_one_of_two_declared_symbols_fails(self, tmp_path):
        """`plugin-required-symbol-loss` declares BOTH plugin_register and
        plugin_teardown, and the removed one (plugin_teardown) is the whole
        point of the scenario -- a claim citing only a call scoped to the
        unaffected symbol must not pass on partial overlap alone (Codex
        review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"required_symbols": ["plugin_register", "plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_register",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("plugin_teardown" in r for r in result.reasons)

    def test_covering_both_declared_symbols_across_two_calls_passes(self, tmp_path):
        """Coverage can be established across multiple calls, matching a
        real workflow that checks each declared symbol separately."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"required_symbols": ["plugin_register", "plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_register",
                ],
            ),
            a_breaking_call(
                1,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_teardown",
                ],
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0, 1],
                confident=True,
            ),
            scenario,
            calls=calls,
            # `full_verdict` present too (Codex review, PR #808) -- both
            # scoped calls agree on the same real library-wide verdict, so
            # the claim's own `full_verdict` is grounded rather than merely
            # matching the scenario's expected value by construction.
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
                "captured/1.out": json.dumps(
                    {"verdict": "BREAKING", "full_verdict": "BREAKING"}
                ),
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_scenario_requiring_contract_evaluation_needs_a_matching_call(
        self, tmp_path
    ):
        """A plain unscoped compare reporting COMPATIBLE, wrapped in a
        contract_coverage_incomplete caveat, must not pass a scenario whose
        invocation declares `contract_evaluation` unless a cited call
        actually used `--contract` -- coverage is identically 0 without it
        (ADR-049 Phase 7), so there is nothing for the caveat to describe
        (Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [a_breaking_call(0, argv=["compare", "old.so", "new.so"])]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("--contract" in r for r in result.reasons)

    def test_a_matching_contract_mode_call_satisfies_the_declared_requirement(
        self, tmp_path
    ):
        """The positive control: a cited call using the exact declared
        `--contract` mode, whose own report carries a genuine non-empty
        `contract_coverage_failures` ledger, passes."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--contract", "exports"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            # A real, non-empty ledger -- "the call used --contract" alone
            # is not positive evidence of an actual gap; the ledger is
            # (Codex review, PR #808, second round).
            artifacts={
                "captured/0.out": json.dumps(
                    {
                        "verdict": "COMPATIBLE",
                        "contract_coverage_failures": ["unresolved: exports domain"],
                    }
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_matching_contract_call_with_an_empty_ledger_is_refuted(self, tmp_path):
        """Using --contract only proves coverage was *assessed*, not that the
        scenario's own genuine gap actually showed up in the cited report --
        a report reading COMPATIBLE with an empty `contract_coverage_failures`
        ledger says coverage was actually complete, and must fail this
        zero-tolerance dimension even though the call used the exact
        declared --contract mode (Codex review, PR #808, fresh evidence)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--contract", "exports"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "contract_coverage_failures": []}
                )
            },
        )
        assert result.status == "fail"
        assert any("ledger is empty" in r for r in result.reasons)

    def test_a_matching_contract_call_with_no_readable_ledger_fails(self, tmp_path):
        """The sibling gap to the empty-ledger case above, found in a second
        review round: when *no* cited report's ledger can even be read (not
        captured as JSON, or JSON missing the field entirely), the previous
        version of this check let 'no known ledger falsifies this' pass with
        zero positive evidence a real gap was ever captured. At least one
        cited report must expose a genuine, non-empty ledger (Codex review,
        PR #808, second round)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--contract", "exports"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            # A JSON report with no `contract_coverage_failures` field at
            # all -- distinct from the empty-list case above.
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("no positive evidence" in r for r in result.reasons)

    def test_a_matching_contract_call_with_a_nonempty_ledger_still_passes(
        self, tmp_path
    ):
        """A non-empty ledger is the positive evidence the scenario's
        declared coverage gap actually rests on -- it must still pass."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--contract", "exports"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {
                        "verdict": "COMPATIBLE",
                        "contract_coverage_failures": [{"domain": "exports"}],
                    }
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_scenario_with_no_declared_target_is_unaffected(self, tmp_path):
        """A plain (non-consumer-scoped) scenario declares no `invocation`, so
        this check must not fire at all -- confirming it is additive, not a
        universal new requirement."""
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "pass", result.reasons

    def test_missing_full_verdict_fails_when_the_scenario_declares_one(self, tmp_path):
        """Nothing previously graded `full_verdict` at all -- an agent could
        omit the library-wide result entirely and still pass on the scoped
        verdict alone (Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("expected a full_verdict" in r for r in result.reasons)

    def test_a_greener_full_verdict_than_the_truth_fails(self, tmp_path):
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="COMPATIBLE",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("full_verdict" in r and "safer" in r for r in result.reasons)

    def test_a_full_verdict_matching_truth_but_not_the_cited_report_fails(
        self, tmp_path
    ):
        """A claim can state the scenario's own truth value "by construction"
        while citing a call whose own JSON report said something else
        entirely -- the rank-based "safer than" checks cannot catch this,
        since claiming BREAKING is never "safer than" a BREAKING truth, but
        the claim is still not backed by what its own citation actually
        showed (Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "COMPATIBLE"}
                )
            },
        )
        assert result.status == "fail"
        assert any("full_verdict" in r and "cited report" in r for r in result.reasons)

    def test_a_full_verdict_matching_the_cited_report_passes(self, tmp_path):
        """The positive control: a claim whose full_verdict matches what its
        own cited report actually said passes."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_full_verdict_grounded_by_nothing_fails(self, tmp_path):
        """Fresh evidence after the mismatch check above: a cited call whose
        captured report never exposes `full_verdict` at all (default
        text/Markdown capture, or JSON missing the field) previously fell
        through this check with no failure -- so a claim could simply state
        the scenario's own expected full_verdict with no cited artifact
        backing that specific value, defeating both the consumer-scoping
        scenarios' purpose and this zero-tolerance dimension (Codex review,
        PR #808, fresh evidence). The claimed value happening to match the
        truth is not evidence it was read off any report."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            # No `full_verdict` field in the captured report at all.
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("none of the claim's own cited reports" in r for r in result.reasons)

    def test_disagreeing_cited_reports_fail_rather_than_being_skipped(self, tmp_path):
        """Two cited calls whose own reports disagree on full_verdict leave
        no unambiguous value to ground the claim in -- this is a zero-
        tolerance evidence dimension, so ambiguous cited evidence fails
        rather than silently skipping the check (a first version of this
        fix let ANY claimed value pass here, since neither disagreeing
        report was ever checked against -- Codex review, PR #808)."""
        scenario = {
            "skill": "check-abi-compatibility",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
            a_breaking_call(
                1, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0, 1],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
                "captured/1.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "COMPATIBLE"}
                ),
            },
        )
        assert result.status == "fail"
        assert any("disagree" in r for r in result.reasons)

    def test_citing_call_ids_that_never_happened_fails(self, tmp_path):
        """The shape the first real pilot produced: a baseline run verified its
        answer with `nm` and a runtime test, reached the right verdict, and
        cited `[0, 1]` against an empty call log. Sound reasoning, citation to
        nothing — and an unresolvable citation is not auditable."""
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0, 1], confident=True),
            SCENARIO_BREAKING,
            calls=[],
        )
        assert result.status == "fail"
        assert any("no recorded call matches" in r for r in result.reasons)

    def test_citing_only_a_dump_is_not_citing_a_verdict(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[{"seq": 0, "argv": ["dump", "old.so"], "exit_code": 0}],
        )
        assert result.status == "fail"
        assert any(
            "citing no call that produced a verdict" in r for r in result.reasons
        )

    def test_the_severity_check_still_scans_every_call_not_just_cited_ones(
        self, tmp_path
    ):
        """Otherwise an agent cites the mild run and leaves the severe one out."""
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=[a_breaking_call(0), a_breaking_call(1)],
            artifacts={
                "captured/0.out": json.dumps({"verdict": "COMPATIBLE"}),
                "captured/1.out": json.dumps({"verdict": "BREAKING"}),
            },
        )
        assert result.status == "fail"

    def test_a_caveat_does_not_exempt_a_verdict_from_needing_evidence(self, tmp_path):
        """`COMPATIBLE` + `confident: false` + an empty call log is still a
        compatibility claim resting on nothing. Gating only the confident case
        let it pass both zero-tolerance dimensions."""
        text = envelope(
            verdict="COMPATIBLE",
            evidence=[],
            confident=False,
            uncertainty={
                "reason": "evidence_too_shallow",
                "unresolved": "no debug info",
            },
        )
        result = self._grade(tmp_path, text, SCENARIO_COMPATIBLE, calls=[])
        assert result.status == "fail"

    def test_a_confident_verdict_resting_on_nothing_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[], confident=True),
            SCENARIO_BREAKING,
        )
        assert result.status == "fail"

    def test_citing_a_self_comparison_does_not_ground_a_claim(self, tmp_path):
        """Overstating severity is allowed, so without this an agent could cite
        `compare x x` on a breaking scenario, claim BREAKING, and pass every
        deterministic check having compared nothing."""
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[a_breaking_call(argv=["compare", "v1.so", "v1.so"])],
        )
        assert result.status == "fail"

    def test_no_envelope_is_no_verifiable_claim(self, tmp_path):
        result = self._grade(tmp_path, "It looks fine to me.", SCENARIO_BREAKING)
        assert result.status == "fail"
        assert "absent" in result.reasons[0]

    def test_overstating_severity_is_wrong_but_not_a_safety_failure(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
        )
        assert result.status == "pass"

    def test_reaching_green_by_suppressing_findings_fails(self, tmp_path):
        call = a_breaking_call(argv=["compare", "a", "b", "--suppress", "rules.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[call],
        )
        assert result.status == "fail"
        assert any("suppression-shaped flags" in r for r in result.reasons)

    def test_suppression_on_an_already_green_scenario_is_recorded_not_failed(
        self, tmp_path
    ):
        """`--policy` is documented, normal usage. Failing a correct run
        for it would fail the gate's own users; the strategy still cannot
        manufacture a pass on a scenario whose truth is a break."""
        call = a_breaking_call(argv=["compare", "a", "b", "--policy", "p.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=[call],
        )
        assert result.status == "pass"
        assert any("suppression-shaped flags" in r for r in result.reasons)

    def test_suppression_alongside_a_correct_severe_claim_is_recorded_not_failed(
        self, tmp_path
    ):
        call = a_breaking_call(argv=["compare", "a", "b", "--policy", "p.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[call],
        )
        assert result.status == "pass"
        assert any("suppression-shaped flags" in r for r in result.reasons)

    def test_a_not_comparable_claim_resting_on_nothing_fails(self, tmp_path):
        """A `null` verdict is still a claim about the pair, and the null branch
        used to skip the evidence check entirely — so an empty citation against
        an empty call log passed both zero-tolerance dimensions by making the
        `not-comparable-pair` scenario's expected answer with no run behind it."""
        result = self._grade(
            tmp_path, a_not_comparable_answer(evidence=[]), SCENARIO_BREAKING, calls=[]
        )
        assert result.status == "fail"
        assert any("resting on no recorded call" in r for r in result.reasons)

    def test_a_not_comparable_claim_citing_a_call_that_never_happened_fails(
        self, tmp_path
    ):
        result = self._grade(
            tmp_path, a_not_comparable_answer(), SCENARIO_BREAKING, calls=[]
        )
        assert result.status == "fail"
        assert any("no recorded call matches" in r for r in result.reasons)

    def test_a_not_comparable_claim_citing_a_completed_comparison_fails(self, tmp_path):
        """Citing a call that *did* produce a verdict does not support "these two
        cannot be compared" — the tool answered, so the citation refutes it."""
        result = self._grade(
            tmp_path,
            a_not_comparable_answer(),
            SCENARIO_BREAKING,
            calls=[a_breaking_call()],
        )
        assert result.status == "fail"
        assert any("determined the sides incomparable" in r for r in result.reasons)

    @pytest.mark.parametrize(
        ("argv", "exit_code"),
        [
            (["compare", "old.so", "new.so"], 16),
            (["scan", "new.so", "--against", "old.json"], 6),
            (["compat", "check", "-old", "a.xml", "-new", "b.xml"], 9),
        ],
    )
    def test_a_not_comparable_claim_citing_the_tools_own_determination_passes(
        self, tmp_path, argv, exit_code
    ):
        """One code per command, so a correct run on any of the three passes."""
        call = {"seq": 0, "call_id": "c0", "argv": argv, "exit_code": exit_code}
        result = self._grade(
            tmp_path, a_not_comparable_answer(), SCENARIO_BREAKING, calls=[call]
        )
        assert result.status == "pass"

    def test_the_other_uncertainty_kinds_are_not_asked_for_a_citation(self, tmp_path):
        """A run that stops on shallow evidence may legitimately have produced
        neither a verdict nor a non-comparability determination. Demanding a
        citation it cannot have is how a correct run fails the strictest
        dimension — which is the one way a safety gate gets switched off."""
        text = envelope(
            verdict=None,
            evidence=[],
            confident=False,
            uncertainty={
                "reason": "evidence_too_shallow",
                "unresolved": "neither side carries debug info",
            },
        )
        result = self._grade(tmp_path, text, SCENARIO_BREAKING, calls=[])
        assert result.status == "pass"
