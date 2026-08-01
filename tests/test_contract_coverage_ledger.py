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

"""ADR-049 Phase 5's unsuppressible contract-coverage ledger.

Definition-of-done item 6 is two claims: suppression is explicit, and
coverage failures are unsuppressible. The first was already true (the
ADR-013 audit trail). These test the second, from both directions -- the
ledger says the right thing, *and* a `--suppress` rule written specifically
to reach one cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.contract_coverage_ledger import (
    REQUIRED_PROVIDERS,
    CoverageFailure,
    coverage_exit_contribution,
    coverage_failures,
    coverage_failures_for_context,
    suppression_reaches_coverage_failures,
)
from abicheck.contract_evidence import (
    ContractEvidenceBlock,
    EvidenceCompleteness,
    EvidenceProviderStatus,
    EvidenceSearchRecord,
    ProviderEvidenceEntry,
)
from abicheck.contract_relevance_types import ContractMode
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _pair_without_export_table() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A header-only pair: `public` can close, `exports` cannot.

    The whole point of the ledger is that these are different answers about
    the same observations, so the fixture has to be one where they differ.
    """
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        from_headers=True,
        functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        from_headers=True,
        functions=[_fn("pub_a", "_Z5pub_av")],
    )
    return old, new


def _record(provider: str, side: str, **kwargs) -> ProviderEvidenceEntry:
    base = {
        "id": f"{provider}:{side}",
        "provider": provider,
        "side": side,
        "domain_kind": "declared_public",
        "status": EvidenceProviderStatus.AVAILABLE,
        "completeness": EvidenceCompleteness.COMPLETE,
        "identity_coverage": EvidenceCompleteness.COMPLETE,
    }
    base.update(kwargs)
    return ProviderEvidenceEntry(record=EvidenceSearchRecord(**base))


def _block(*entries: ProviderEvidenceEntry) -> ContractEvidenceBlock:
    return ContractEvidenceBlock(providers=entries)


class TestDerivation:
    def test_a_complete_available_provider_is_not_a_failure(self) -> None:
        block = _block(_record("public_header", "old"), _record("public_header", "new"))
        assert coverage_failures(block, ContractMode.PUBLIC) == ()
        assert coverage_exit_contribution(()) == 0

    @pytest.mark.parametrize(
        "status,expected",
        [
            (EvidenceProviderStatus.UNAVAILABLE, "provider_unavailable"),
            (EvidenceProviderStatus.FAILED, "provider_failed"),
            (EvidenceProviderStatus.UNSUPPORTED, "provider_unsupported"),
            (EvidenceProviderStatus.STALE, "provider_stale"),
        ],
    )
    def test_each_non_available_status_is_its_own_reason(
        self, status, expected: str
    ) -> None:
        """Four different ways of not having the fact, four different fixes --
        collapsing them to a boolean would defeat the ledger's own purpose
        (saying *why* a domain did not close)."""
        block = _block(_record("public_header", "old", status=status))
        (failure,) = coverage_failures(block, ContractMode.PUBLIC)
        assert failure.reason == expected
        assert failure.status == status.value

    def test_an_available_but_partial_search_is_a_failure(self) -> None:
        block = _block(
            _record("public_header", "old", completeness=EvidenceCompleteness.PARTIAL)
        )
        (failure,) = coverage_failures(block, ContractMode.PUBLIC)
        assert failure.reason == "search_incomplete"

    def test_partial_identity_coverage_alone_is_a_failure(self) -> None:
        """Section 4.2 requires identity coverage separately from overall
        completeness: a complete search whose identity coverage is partial
        cannot support a proven exclusion for the ambiguous spellings."""
        block = _block(
            _record(
                "public_header",
                "old",
                identity_coverage=EvidenceCompleteness.PARTIAL,
            )
        )
        (failure,) = coverage_failures(block, ContractMode.PUBLIC)
        assert failure.reason == "identity_coverage_incomplete"

    def test_not_started_configuration_coverage_is_not_a_failure(self) -> None:
        """Every record this build produces has `configuration_coverage:
        NOT_STARTED` (no variant source exists yet). Treating that as a
        coverage failure would report every run as failing for a facet
        nothing can satisfy."""
        block = _block(_record("public_header", "old"))
        assert coverage_failures(block, ContractMode.PUBLIC) == ()

    def test_the_same_record_is_a_failure_in_one_domain_and_not_another(self) -> None:
        """Section 7's rule, which is why this is derived per-mode rather
        than recorded at collection time: under `exports`,
        "public-header/manifest/consumer failures are unrelated and
        advisory"."""
        block = _block(
            _record("public_header", "old", status=EvidenceProviderStatus.UNAVAILABLE)
        )
        assert coverage_failures(block, ContractMode.PUBLIC)
        assert coverage_failures(block, ContractMode.EXPORTS) == ()

    def test_all_mode_can_never_have_a_coverage_failure(self) -> None:
        """`all` requires no root or closure evidence, so no provider failure
        can stop it closing -- and it correspondingly has no unresolved
        findings either."""
        block = _block(
            _record("public_header", "old", status=EvidenceProviderStatus.FAILED),
            _record("export_table", "old", status=EvidenceProviderStatus.FAILED),
        )
        assert coverage_failures(block, ContractMode.ALL) == ()
        assert REQUIRED_PROVIDERS[ContractMode.ALL] == frozenset()

    def test_a_string_mode_resolves_the_same_as_the_enum(self) -> None:
        block = _block(
            _record("export_table", "old", status=EvidenceProviderStatus.FAILED)
        )
        assert coverage_failures(block, "exports") == coverage_failures(
            block, ContractMode.EXPORTS
        )

    def test_no_evidence_is_no_claim_rather_than_a_failure(self) -> None:
        """A run that computed no context made no coverage claim; that is not
        the same as one whose providers failed."""
        assert coverage_failures(None, ContractMode.PUBLIC) == ()
        assert coverage_failures_for_context(None) == ()

    def test_the_ledger_order_does_not_depend_on_traversal_order(self) -> None:
        entries = [
            _record("export_table", "new", status=EvidenceProviderStatus.FAILED),
            _record("export_table", "old", status=EvidenceProviderStatus.FAILED),
        ]
        forward = coverage_failures(_block(*entries), ContractMode.EXPORTS)
        reverse = coverage_failures(_block(*reversed(entries)), ContractMode.EXPORTS)
        assert forward == reverse
        assert [f.side for f in forward] == ["new", "old"]

    def test_a_failure_states_its_own_unsuppressibility(self) -> None:
        block = _block(
            _record("export_table", "old", status=EvidenceProviderStatus.FAILED)
        )
        (failure,) = coverage_failures(block, ContractMode.EXPORTS)
        assert failure.suppressible is False
        assert failure.to_dict()["suppressible"] is False
        assert failure.to_dict()["mode"] == "exports"


class TestUnsuppressibility:
    """The half of Definition-of-done item 6 that had no implementation."""

    def _failure(self) -> CoverageFailure:
        block = _block(
            _record("export_table", "old", status=EvidenceProviderStatus.FAILED)
        )
        (failure,) = coverage_failures(block, ContractMode.EXPORTS)
        return failure

    def test_a_rule_written_to_match_a_failure_still_cannot_reach_it(
        self, tmp_path: Path
    ) -> None:
        """The direct proof. These rules are deliberately broad -- a wildcard
        symbol selector that matches every real finding -- and are handed to
        the same predicate `checker._filter_suppressed_changes` consults."""
        from abicheck.suppression import SuppressionList

        rules = tmp_path / "s.yaml"
        rules.write_text(
            "version: 1\nsuppressions:\n"
            "  - symbol: '.*'\n"
            "    reason: 'silence everything'\n",
            encoding="utf-8",
        )
        suppression = SuppressionList.load(rules)
        assert (
            suppression_reaches_coverage_failures([self._failure()], suppression) == ()
        )

    def test_no_suppression_reaches_nothing(self) -> None:
        assert suppression_reaches_coverage_failures([self._failure()], None) == ()

    def test_a_coverage_failure_is_structurally_not_a_change(self) -> None:
        """*Why* the rule holds, rather than that it happens to. Suppression
        is applied in exactly one place, to `Change` objects; a
        `CoverageFailure` has none of the attributes that selects on, so it
        cannot be a candidate."""
        failure = self._failure()
        for change_attr in ("kind", "symbol", "description", "source_location"):
            assert not hasattr(failure, change_attr), change_attr


class TestReportIntegration:
    def _report(self, tmp_path: Path, mode: str) -> dict:
        old, new = _pair_without_export_table()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        out = tmp_path / f"report-{mode}.json"
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--contract-evaluation",
                "--contract",
                mode,
                "-o",
                str(out),
            ],
        )
        assert res.exit_code in (0, 2, 4), res.output
        return json.loads(out.read_text(encoding="utf-8"))

    def test_a_missing_export_table_is_a_coverage_failure_under_exports(
        self, tmp_path: Path
    ) -> None:
        report = self._report(tmp_path, "exports")
        failures = report["contract_coverage_failures"]
        assert {f["side"] for f in failures} == {"old", "new"}
        assert {f["provider"] for f in failures} == {"export_table"}
        assert all(f["reason"] == "provider_unavailable" for f in failures)
        assert report["contract_coverage_exit_contribution"] == 1

    @pytest.mark.parametrize("mode", ["public", "all"])
    def test_the_same_run_has_no_failures_in_the_other_domains(
        self, tmp_path: Path, mode: str
    ) -> None:
        report = self._report(tmp_path, mode)
        assert report["contract_coverage_failures"] == []
        assert report["contract_coverage_exit_contribution"] == 0

    def test_an_empty_ledger_is_emitted_rather_than_omitted(
        self, tmp_path: Path
    ) -> None:
        """`[]` is the checkable answer "this domain closed"; an absent key
        could not be told apart from "not computed"."""
        report = self._report(tmp_path, "public")
        assert "contract_coverage_failures" in report

    def test_no_ledger_at_all_without_contract_evaluation(self, tmp_path: Path) -> None:
        old, new = _pair_without_export_table()
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        out = tmp_path / "plain.json"
        res = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json", "-o", str(out)],
        )
        assert res.exit_code == 4, res.output
        report = json.loads(out.read_text(encoding="utf-8"))
        assert "contract_coverage_failures" not in report

    def test_the_ledger_does_not_change_the_verdict_or_exit_code(
        self, tmp_path: Path
    ) -> None:
        """Advisory, like the rest of ADR-049 Phase 3-6: the independent
        coverage exit is Phase 7's. A run with a non-zero *stated*
        contribution must still exit on its compatibility verdict alone."""
        report = self._report(tmp_path, "exports")
        assert report["contract_coverage_exit_contribution"] == 1
        assert report["verdict"] == "BREAKING"

    def test_the_ledger_matches_what_the_python_api_derives(
        self, tmp_path: Path
    ) -> None:
        """The report is a view of the derivation, not a second one."""
        old, new = _pair_without_export_table()
        result = compare(
            old,
            new,
            scope_to_public_surface=True,
            contract_evaluation=True,
            contract_mode="exports",
        )
        derived = coverage_failures_for_context(result.contract_context)
        report = self._report(tmp_path, "exports")
        assert [f.to_dict() for f in derived] == report["contract_coverage_failures"]
