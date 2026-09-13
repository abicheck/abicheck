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

"""ADR-067 C-S2: the release/bundle fan-out's own raw-versus-effective
disposition audit -- the same conserved ledger reconciliation scalar
`compare` has (C-S1, ``tests/test_disposition_audit.py``), folded across
every library into one release-level total.

Registered bug class: ``policy.disposition_conservation``
(``tests/regressions/manifest.py``) -- C-S1's own "100 removals detected,
100 suppressed" fixture proved the scalar ledger conserves; this file proves
the release-level *fold* of N such per-library ledgers still conserves, and
that a library which never reached a real comparison contributes nothing
rather than a silent zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.cli_compare_receipt import release_disposition_audit_block
from abicheck.cli_compare_release_helpers import _format_release_json
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.suppression import Suppression, SuppressionList


def _removed_and_suppressed(n: int) -> DiffResult:
    """A `DiffResult` with *n* function removals, all hidden by one wildcard
    suppression rule -- the per-library shape C-S1's own fixture uses."""
    changes = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"_Z{i}fv", description="removed")
        for i in range(n)
    ]
    rules = SuppressionList(
        [Suppression(symbol_pattern=".*", reason="bulk internal churn", label="waiver")]
    )
    # Building the DiffResult directly and running it through the same
    # ledger-closing primitive `compare()` uses, since this fixture's whole
    # point is the *disposition* pipeline, not symbol diffing itself.
    from abicheck.policy.disposition_close import finalize_ledger
    from abicheck.policy.disposition_ledger import (
        DispositionLedger,
        record_suppressed_change,
    )

    ledger = DispositionLedger()
    kept: list[Change] = []
    for change in changes:
        outcome = rules.evaluate(change)
        if outcome.matched_rule is not None:
            record_suppressed_change(
                ledger,
                change,
                rule=outcome.matched_rule,
                application_point="apply_suppression",
                suppression=rules,
            )
        else:
            kept.append(change)
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="libfoo.so",
        changes=kept,
        suppressed_changes=[c for c in changes if c not in kept],
    )
    finalize_ledger(ledger, result)
    result.disposition_ledger = ledger
    return result


class TestReleaseFanOutFoldsPerLibraryAudits:
    """A release with several libraries, each independently suppressed,
    folds into one release-level ``disposition_audit`` whose totals sum the
    per-library ones -- D3's conservation invariant applied one level up."""

    def _library_entries(self) -> list[dict[str, object]]:
        from abicheck.report.disposition_audit import compute_disposition_audit

        entries = []
        for i, n in enumerate((100, 50, 25)):
            result = _removed_and_suppressed(n)
            entries.append(
                {
                    "library": f"lib{i}.so",
                    "verdict": "NO_CHANGE",
                    "disposition_audit": compute_disposition_audit(result).to_dict(),
                }
            )
        return entries

    def test_totals_sum_across_libraries(self) -> None:
        entries = self._library_entries()
        folded = release_disposition_audit_block(entries)
        assert folded["detected_total"] == 175
        assert folded["effective_total"] == 0
        assert folded["counts"]["suppressed"] == 175

    def test_rule_tally_is_merged_by_identity_not_duplicated(self) -> None:
        # Every library used the *same* suppression rule (identical
        # selector/reason/label) -- the fold must tally it as one rule
        # matching 175 findings, not three separate rows.
        entries = self._library_entries()
        folded = release_disposition_audit_block(entries)
        assert len(folded["rules"]) == 1
        assert folded["rules"][0]["matched_count"] == 175

    def test_a_library_with_no_disposition_audit_contributes_nothing(self) -> None:
        # An operational-error/not_comparable library never reached a real
        # comparison and carries no `disposition_audit` key at all -- it
        # must not silently count as zero *detections* that then look like
        # a clean, fully-audited library.
        entries = self._library_entries()
        entries.append({"library": "broken.so", "verdict": "ERROR", "error": "boom"})
        folded = release_disposition_audit_block(entries)
        assert folded["detected_total"] == 175

    def test_zero_libraries_yields_the_conserved_zero_audit(self) -> None:
        folded = release_disposition_audit_block([])
        assert folded["detected_total"] == 0
        assert folded["effective_total"] == 0
        assert sum(folded["counts"].values()) == 0

    def test_format_release_json_carries_the_folded_block(self) -> None:
        entries = self._library_entries()
        out = _format_release_json(
            "NO_CHANGE",
            Path("/o"),
            Path("/n"),
            entries,
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(out)
        assert data["disposition_audit"]["detected_total"] == 175
        assert data["disposition_audit"]["counts"]["suppressed"] == 175


class TestReleaseFanOutFoldsBundleFindings:
    """Codex review, fresh evidence ("Include bundle findings in the
    release disposition audit"): a release whose only change is a
    cross-library ``BUNDLE_*`` finding (no per-library change at all) used
    to report that finding in ``bundle_findings`` while the release-level
    ``disposition_audit`` showed ``detected_total: 0``/``effective_total:
    0`` -- the finding was observed and gated (the release's own exit code
    and ``bundle_verdict`` reflect it), but never accounted for in D3's
    conservation ledger."""

    def _bundle_result(self, n: int = 1):
        from abicheck.bundle_models import BundleDiffResult, BundleFinding

        return BundleDiffResult(
            old_root=Path("/o"),
            new_root=Path("/n"),
            bundle_findings=[
                BundleFinding(
                    kind=ChangeKind.BUNDLE_LIBRARY_REMOVED,
                    symbol=f"lib{i}.so",
                    description="library removed from the bundle",
                )
                for i in range(n)
            ],
        )

    def test_bundle_findings_are_folded_into_the_release_audit(self) -> None:
        folded = release_disposition_audit_block([], None, None, self._bundle_result(3))
        assert folded["detected_total"] == 3
        assert folded["effective_total"] == 3

    def test_bundle_findings_sum_alongside_per_library_ones(self) -> None:
        entries = TestReleaseFanOutFoldsPerLibraryAudits()._library_entries()
        folded = release_disposition_audit_block(
            entries, None, None, self._bundle_result(2)
        )
        # 175 per-library (all suppressed, per _library_entries) + 2 bundle
        # (unsuppressed) -- both populations must be visible in one total.
        assert folded["detected_total"] == 177
        assert folded["effective_total"] == 2

    def test_no_bundle_result_contributes_nothing(self) -> None:
        folded = release_disposition_audit_block([], None, None, None)
        assert folded["detected_total"] == 0

    def test_empty_bundle_findings_contributes_nothing(self) -> None:
        from abicheck.bundle_models import BundleDiffResult

        empty = BundleDiffResult(old_root=Path("/o"), new_root=Path("/n"))
        folded = release_disposition_audit_block([], None, None, empty)
        assert folded["detected_total"] == 0

    def test_format_release_json_carries_the_bundle_contribution(self) -> None:
        out = _format_release_json(
            "BREAKING",
            Path("/o"),
            Path("/n"),
            [],
            [],
            [],
            {},
            {},
            [],
            self._bundle_result(1),
            None,
        )
        data = json.loads(out)
        assert data["disposition_audit"]["detected_total"] == 1
        assert data["disposition_audit"]["effective_total"] == 1


class TestDispositionAuditRoundTripsThroughRelease:
    """`DispositionAudit.from_dict`/`.to_dict()` round-trip the exact shape
    `compute_disposition_audit` produces for a real per-library `DiffResult`
    -- the fold's whole mechanism depends on this holding."""

    def test_round_trip(self) -> None:
        from abicheck.report.disposition_audit import compute_disposition_audit

        result = _removed_and_suppressed(10)
        audit = compute_disposition_audit(result)
        rebuilt = DispositionAudit.from_dict(audit.to_dict())
        assert rebuilt == audit
