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

"""``--report-mode root-cause`` Markdown must preserve a
``scoped_only_changes`` entry's ``EvidenceStatus.CONSUMER_PROVEN`` override,
not re-derive its evidence status from ``DiffResult.evidence_tiers`` like an
ordinary comparison finding.

Split out of ``tests/test_reporter.py`` (a ``no_growth``-tracked legacy test
module, per ``architecture/debt.yaml``) rather than raising that module's
baseline -- this covers a distinct axis (scoped-only/consumer-proven
evidence in the root-cause Markdown view specifically), the same split this
repo's test-file debt entries call for over growing an already-oversized
module (Codex review, fresh evidence).
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.reporter import to_markdown


def _result(verdict: Verdict, changes=None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes or [],
        verdict=verdict,
    )


class TestRootCauseScopedOnlyEvidence:
    def test_scoped_only_finding_keeps_consumer_proven_impact_text(self) -> None:
        """Codex review, fresh evidence: a scoped_only_changes entry is
        proven by the supplied consumer's own import table (JSON/SARIF
        stamp it EvidenceStatus.CONSUMER_PROVEN unconditionally), but the
        root-cause Markdown path used to re-derive its evidence status from
        result.evidence_tiers like an ordinary comparison finding -- so an
        UNATTRIBUTED-tiered run demoted a consumer-proven finding to
        "plausible, not confirmed" even though nothing about the consumer
        evidence itself was in question.
        """
        scoped_only = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            "pub_entry",
            "required by consumer",
        )
        r = _result(Verdict.BREAKING, changes=[])
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        # binary never examined -> UNATTRIBUTED if re-derived
        r.evidence_tiers = ("header",)
        md = to_markdown(r, report_mode="root-cause")
        assert "consumer_required_symbol_removed" in md
        assert "Evidence note" not in md

    def test_ordinary_finding_still_gets_the_evidence_caveat(self) -> None:
        """Negative control for the fix above: an ordinary (non-scoped_only)
        finding in the same root-cause run must still be qualified from
        result.evidence_tiers as before -- the CONSUMER_PROVEN override is
        scoped_only-specific, not a blanket exemption for every finding in
        a run that also happens to carry a scoped_only sibling.
        """
        root = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        scoped_only = Change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            "pub_entry",
            "required by consumer",
            caused_by_type="ns::internal::helper",
        )
        r = _result(Verdict.BREAKING, changes=[root])
        r.scoped_only_changes = (scoped_only,)  # type: ignore[attr-defined]
        r.evidence_tiers = ("header",)
        md = to_markdown(r, report_mode="root-cause")
        assert "Evidence note" in md
