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

"""ADR-044 P2 item 3 worked examples: reachability-aware suppression's
headline scenario end to end (case192), and its deliberate counter-example
(case193).

Both cases ship committed ``AbiSnapshot`` pairs with an embedded L5 source/
call graph instead of a compilable v1/v2 pair (see
``scripts/gen_reachability_examples.py``) -- reproducing the scenario for
real would require a genuine build with ``--sources``/``--build-info``
evidence. Compiler-free, mirrors ``tests/test_environment_drift.py``'s
``TestCase170Example`` validation pattern.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.serialization import snapshot_from_dict
from abicheck.suppression import SuppressionList

_EXAMPLES = Path(__file__).parent.parent / "examples"
_GT = json.loads((_EXAMPLES / "ground_truth.json").read_text())["verdicts"]


def _snapshots(case_name: str):
    case_dir = _EXAMPLES / case_name
    old = snapshot_from_dict(json.loads((case_dir / "old.abi.json").read_text()))
    new = snapshot_from_dict(json.loads((case_dir / "new.abi.json").read_text()))
    return old, new


class TestCase192CallGraphBreakSurvivesSuppression:
    CASE = "case192_call_graph_break_survives_suppression"

    @pytest.fixture()
    def snapshots(self):
        return _snapshots(self.CASE)

    def test_matches_ground_truth(self, snapshots) -> None:
        gt = _GT[self.CASE]
        result = compare(*snapshots)
        assert result.verdict.value == gt["expected"]
        kinds = {c.kind.value for c in result.changes}
        assert kinds == set(gt["expected_kinds"])

    def test_reachability_tagged_with_proof_path(self, snapshots) -> None:
        # MarkReachability only runs the (expensive) public-surface walk when
        # a suppression rule could actually consult the tag
        # (SuppressionList.needs_reachability_evidence) -- a bare compare()
        # with no suppression at all skips it, matching the "nothing to
        # gate" precedent. Use the refused-suppression file, the realistic
        # scenario where the tag is actually read.
        old, new = snapshots
        suppression = SuppressionList.load(
            _EXAMPLES / self.CASE / "suppress-refused.yaml"
        )
        result = compare(old, new, suppression=suppression)
        removed = next(
            c for c in result.changes if c.kind is ChangeKind.FUNC_REMOVED
        )
        assert removed.public_reachable is True
        assert removed.reachability_kind == "symbol_availability"
        assert removed.reachability_proof_path == (
            "demo::compute --[DECL_CALLS_DECL]--> demo::detail::compute_avx2"
        )

    def test_broad_suppression_refused_without_override(self, snapshots) -> None:
        old, new = snapshots
        suppression = SuppressionList.load(
            _EXAMPLES / self.CASE / "suppress-refused.yaml"
        )
        result = compare(old, new, suppression=suppression)
        assert result.verdict == Verdict.BREAKING
        assert any(
            c.kind is ChangeKind.FUNC_REMOVED for c in result.changes
        )
        assert any(
            c.kind is ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
            for c in result.changes
        )

    def test_broad_suppression_applies_with_allow_public_break(
        self, snapshots
    ) -> None:
        old, new = snapshots
        suppression = SuppressionList.load(
            _EXAMPLES / self.CASE / "suppress-acknowledged.yaml"
        )
        result = compare(old, new, suppression=suppression)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []


class TestCase193OrdinaryExportedFnCallNotReachable:
    CASE = "case193_ordinary_exported_fn_call_not_reachable"

    @pytest.fixture()
    def snapshots(self):
        return _snapshots(self.CASE)

    def test_matches_ground_truth(self, snapshots) -> None:
        gt = _GT[self.CASE]
        result = compare(*snapshots)
        assert result.verdict.value == gt["expected"]
        kinds = {c.kind.value for c in result.changes}
        assert kinds == set(gt["expected_kinds"])

    def test_no_false_reachability_claim_with_walk_actually_run(
        self, snapshots
    ) -> None:
        """The ordinary out-of-line api()'s call to detail::log_context()
        must not be tagged reachable when MarkReachability's walk genuinely
        runs (a broad-selector suppression rule, even one that doesn't match
        this change, trips SuppressionList.needs_reachability_evidence()) --
        an unrelated rule is used here (rather than case193's own matching
        suppress.yaml) so func_removed survives to inspect its tag directly;
        the matching-suppression path is covered separately by
        test_broad_suppression_applies_cleanly_no_diagnostic below. The graph
        stays quiet on the common case (case193) while it stays loud on the
        rare one (case192)."""
        from abicheck.suppression import Suppression

        old, new = snapshots
        suppression = SuppressionList(
            [Suppression(namespace="totally::unrelated::**", reason="unrelated")]
        )
        result = compare(old, new, suppression=suppression)
        removed = next(
            c for c in result.changes if c.kind is ChangeKind.FUNC_REMOVED
        )
        assert removed.public_reachable is False
        assert not any(
            c.kind is ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
            for c in result.changes
        )

    def test_broad_suppression_applies_cleanly_no_diagnostic(self, snapshots) -> None:
        old, new = snapshots
        suppression = SuppressionList.load(_EXAMPLES / self.CASE / "suppress.yaml")
        result = compare(old, new, suppression=suppression)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []
