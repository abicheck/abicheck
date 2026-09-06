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

"""ADR-067 C-S2 branch coverage: the paths the release-fanout/reclassification
test files above don't reach on their own -- render-function branches for
reclassification/scope-reason content, ``fold_disposition_audits`` merging
duplicate rule/reclassification/reason identities across members, the
``DispositionRecord.to_dict()`` wire shape, ``disposition_axis``'s
no-block case, and ``resolve_reclassifications`` being idempotent on an
already-resolved record.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.policy.disposition_close import (
    ledger_for,
    reclassifications,
    scope_reasons,
)
from abicheck.policy.disposition_ledger import Disposition, RuleProvenance
from abicheck.policy.disposition_types import DispositionRecord
from abicheck.report.disposition_audit import (
    DispositionAudit,
    NotEvaluatedDetector,
    fold_disposition_audits,
    render_disposition_audit_comment_lines,
    render_disposition_audit_lines,
    render_disposition_audit_note,
)
from abicheck.workflows.aggregate.disposition_axis import disposition_audit_block


def _audit(**kwargs: object) -> DispositionAudit:
    base: dict[str, object] = {
        "detected_total": 0,
        "effective_total": 0,
        "counts": (),
        "rules": (),
        "not_evaluated_detectors": (),
    }
    base.update(kwargs)
    return DispositionAudit(**base)  # type: ignore[arg-type]


class TestDispositionRecordToDict:
    def test_minimal_record(self) -> None:
        record = DispositionRecord(
            kind="func_removed",
            symbol="foo",
            disposition=Disposition.GATING,
            application_point="verdict",
        )
        d = record.to_dict()
        assert d == {
            "kind": "func_removed",
            "symbol": "foo",
            "disposition": "gating",
            "application_point": "verdict",
        }

    def test_full_record(self) -> None:
        rule = RuleProvenance(rule_id="r1", reason="waived")
        record = DispositionRecord(
            kind="func_removed",
            symbol="foo",
            disposition=Disposition.SUPPRESSED,
            application_point="apply_suppression",
            verdict_class="breaking",
            rule=rule,
            reclassified_by="my rule",
            reason_code="proven_out_of_contract",
        )
        d = record.to_dict()
        assert d["verdict_class"] == "breaking"
        assert d["rule"] == rule.to_dict()
        assert d["reclassified_by"] == "my rule"
        assert d["reason_code"] == "proven_out_of_contract"


class TestFoldMergesDuplicateIdentitiesAcrossMembers:
    def test_reclassification_and_scope_reason_tallies_merge(self) -> None:
        det = NotEvaluatedDetector(name="dwarf", reason="no debug info")
        a = _audit(
            detected_total=2,
            effective_total=0,
            counts=(("suppressed", 2),),
            reclassified_total=1,
            reclassifications=(("r1", 1),),
            scope_reasons=(("proven_out_of_contract", 1),),
            not_evaluated_detectors=(det,),
        )
        b = _audit(
            detected_total=3,
            effective_total=0,
            counts=(("suppressed", 3),),
            reclassified_total=2,
            reclassifications=(("r1", 2),),
            scope_reasons=(("proven_out_of_contract", 3),),
            not_evaluated_detectors=(det,),
        )
        folded = fold_disposition_audits([a, b])
        assert folded.reclassified_total == 3
        assert folded.reclassifications == (("r1", 3),)
        assert folded.scope_reasons == (("proven_out_of_contract", 4),)
        # Deduplicated by name, not doubled.
        assert folded.not_evaluated_detectors == (det,)


class TestRenderReclassificationAndScopeReasons:
    def test_note_states_reclassified_count(self) -> None:
        audit = _audit(detected_total=1, effective_total=0, reclassified_total=1)
        note = render_disposition_audit_note(audit)
        assert "1 reclassified" in note

    def test_note_nonempty_when_only_reclassified(self) -> None:
        # Zero detected, zero not-evaluated, zero overlays, but a
        # reclassified count -- the note must still state it (D3: never
        # silently drop a nonzero fact).
        audit = _audit(detected_total=0, effective_total=0, reclassified_total=1)
        assert render_disposition_audit_note(audit) != ""

    def test_lines_render_reclassification_and_scope_reason_rows(self) -> None:
        audit = _audit(
            detected_total=2,
            effective_total=0,
            counts=(("suppressed", 1), ("out_of_contract", 1)),
            reclassified_total=1,
            reclassifications=(("my-rule", 1),),
            scope_reasons=(("proven_out_of_contract", 1),),
        )
        lines = "\n".join(render_disposition_audit_lines(audit))
        assert "**Reclassified:** 1" in lines
        assert "`my-rule` — 1 finding(s)" in lines
        assert "**Out-of-contract/scope reasons:**" in lines
        assert "`proven_out_of_contract` — 1 finding(s)" in lines

    def test_comment_lines_state_reclassified_count(self) -> None:
        audit = _audit(detected_total=1, effective_total=0, reclassified_total=1)
        lines = "\n".join(render_disposition_audit_comment_lines(audit))
        assert "1 reclassified" in lines


class TestDispositionCloseReclassificationsAndScopeReasonsDedup:
    def test_two_records_sharing_one_reclassify_rule_tally_together(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            "reclassify:\n"
            "  - kind: func_removed\n"
            "    symbol_pattern: '.*'\n"
            "    to: ignore\n"
            '    reason: "bulk"\n',
            encoding="utf-8",
        )
        from abicheck.policy_file import PolicyFile

        pf = PolicyFile.load(p)
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x"),
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="bar", description="x"),
        ]
        diff = DiffResult(
            changes=changes,
            old_version="1",
            new_version="2",
            library="l",
            policy_file=pf,
        )
        ledger = ledger_for(diff)
        assert reclassifications(ledger) == (("bulk", 2),)

    def test_two_out_of_contract_records_sharing_one_reason_tally_together(
        self,
    ) -> None:
        from abicheck.policy.disposition_ledger import DispositionLedger

        ledger = DispositionLedger()
        for symbol in ("foo", "bar"):
            change = Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=symbol,
                description="x",
                contract_reason_code="not_in_public_headers",
            )
            ledger.record(
                change,
                Disposition.OUT_OF_CONTRACT,
                application_point="surface_scope",
            )
        assert scope_reasons(ledger) == (("not_in_public_headers", 2),)

    def test_a_suppressed_record_is_excluded_from_scope_reasons(self) -> None:
        from abicheck.policy.disposition_ledger import DispositionLedger

        ledger = DispositionLedger()
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="x",
            contract_reason_code="not_in_public_headers",
        )
        ledger.record(
            change, Disposition.SUPPRESSED, application_point="apply_suppression"
        )
        assert scope_reasons(ledger) == ()


class TestResolveReclassificationsIsIdempotent:
    def test_running_it_twice_does_not_re_resolve_an_already_stamped_record(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            "reclassify:\n"
            "  - kind: func_removed\n"
            "    symbol: foo\n"
            "    to: ignore\n"
            '    reason: "first"\n',
            encoding="utf-8",
        )
        from abicheck.policy_file import PolicyFile

        pf = PolicyFile.load(p)
        change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
        diff = DiffResult(
            changes=[change],
            old_version="1",
            new_version="2",
            library="l",
            policy_file=pf,
        )
        ledger = ledger_for(diff)
        assert reclassifications(ledger) == (("first", 1),)
        # A second pass (mirroring `close_consumer_scope`'s re-close) must
        # leave the already-resolved record alone.
        ledger.resolve_reclassifications(diff)
        assert reclassifications(ledger) == (("first", 1),)


class TestDispositionAuditBlockAbsent:
    def test_no_disposition_audit_key_reads_as_none(self) -> None:
        assert disposition_audit_block({"verdict": "COMPATIBLE"}) is None

    def test_disposition_audit_present(self) -> None:
        block = {"disposition_audit": {"detected_total": 1}}
        assert disposition_audit_block(block) == {"detected_total": 1}


class TestReleaseDispositionAuditBlockWithMatrixResult:
    def test_matrix_result_contributes_its_own_audit(self) -> None:
        from abicheck.cli_compare_receipt import release_disposition_audit_block

        matrix_result = DiffResult(
            changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="m", description="x")],
            old_version="1",
            new_version="2",
            library="matrix",
        )
        folded = release_disposition_audit_block([], matrix_result)
        assert folded["detected_total"] == 1


class TestTargetReportToDictCarriesDispositionAudit:
    def test_present_when_set(self) -> None:
        from abicheck.workflows.aggregate.contracts import TargetReport

        report = TargetReport(
            target_id="t1",
            required=True,
            compatibility_verdict=Verdict.COMPATIBLE,
            disposition_audit={"detected_total": 3},
        )
        assert report.to_dict()["disposition_audit"] == {"detected_total": 3}

    def test_absent_when_none(self) -> None:
        from abicheck.workflows.aggregate.contracts import TargetReport

        report = TargetReport(
            target_id="t1",
            required=True,
            compatibility_verdict=Verdict.COMPATIBLE,
        )
        assert "disposition_audit" not in report.to_dict()


class TestReleaseSummarySidecarCarriesDispositionAudit:
    def test_output_dir_summary_carries_disposition_audit(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release import _write_release_summary_file

        entries: list[dict[str, object]] = [
            {
                "library": "libfoo.so",
                "verdict": "NO_CHANGE",
                "disposition_audit": {
                    "detected_total": 1,
                    "effective_total": 0,
                    "counts": {"suppressed": 1},
                    "rules": [],
                },
            }
        ]
        import json

        _write_release_summary_file(tmp_path, "NO_CHANGE", entries, [], [], {}, {})
        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["disposition_audit"]["detected_total"] == 1
