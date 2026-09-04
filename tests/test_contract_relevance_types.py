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

"""ADR-049 Phase 0: tests for the reserved contract-relevance vocabulary."""

from __future__ import annotations

from abicheck.contract_relevance_types import (
    CONTRACT_EVIDENCE_SCHEMA_VERSION,
    CONTRACT_REASON_CODES,
    EVALUATED_RELEVANCE,
    EVALUATION_CONTEXT_SCHEMA_VERSION,
    EVALUATOR_VERSION,
    IDENTITY_ALGORITHM_VERSION,
    LEGACY_SCOPE_FLAG_CONTRACT_MODE,
    NON_ENTITY_RELEVANCE,
    AnalysisStatus,
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractMode,
    ContractRelevance,
    EvidenceCompleteness,
    EvidenceProviderStatus,
    LegacyScopeFlag,
    SelectorLayer,
)


class TestContractMode:
    def test_exactly_three_modes(self):
        # ADR-049 D3: no hidden fourth "public_contract" mode/preset.
        assert {m.value for m in ContractMode} == {"public", "exports", "all"}

    def test_exports_is_not_all(self):
        # ADR-049 "Rejected alternatives": exports must not collapse into all.
        assert ContractMode.EXPORTS != ContractMode.ALL


class TestLegacyScopeFlagAlias:
    def test_no_scope_public_headers_is_exact_alias_for_all(self):
        assert (
            LEGACY_SCOPE_FLAG_CONTRACT_MODE[LegacyScopeFlag.NO_SCOPE_PUBLIC_HEADERS]
            is ContractMode.ALL
        )

    def test_scope_public_headers_is_stricter_alias_for_public_not_exports(self):
        mode = LEGACY_SCOPE_FLAG_CONTRACT_MODE[LegacyScopeFlag.SCOPE_PUBLIC_HEADERS]
        assert mode is ContractMode.PUBLIC
        assert mode is not ContractMode.EXPORTS

    def test_covers_both_legacy_flags(self):
        assert set(LEGACY_SCOPE_FLAG_CONTRACT_MODE) == set(LegacyScopeFlag)


class TestContractRelevance:
    def test_exactly_five_values(self):
        assert {r.value for r in ContractRelevance} == {
            "IN_CONTRACT",
            "PROVEN_OUT_OF_CONTRACT",
            "UNKNOWN_UNPROVEN",
            "UNKNOWN_UNRESOLVED",
            "NOT_APPLICABLE",
        }

    def test_no_bare_private_value(self):
        # ADR-049 D1: PROVEN_OUT_OF_CONTRACT deliberately replaces PRIVATE.
        assert "PRIVATE" not in {r.value for r in ContractRelevance}
        assert "PRIVATE" not in {r.name for r in ContractRelevance}

    def test_non_entity_relevance_is_not_applicable(self):
        assert NON_ENTITY_RELEVANCE is ContractRelevance.NOT_APPLICABLE


class TestCompatibilityEvaluationStatus:
    def test_evaluated_relevance_is_exactly_in_contract_and_not_applicable(self):
        assert EVALUATED_RELEVANCE == {
            ContractRelevance.IN_CONTRACT,
            ContractRelevance.NOT_APPLICABLE,
        }

    def test_unresolved_and_unproven_and_out_of_contract_are_not_evaluated(self):
        not_evaluated = set(ContractRelevance) - EVALUATED_RELEVANCE
        assert not_evaluated == {
            ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            ContractRelevance.UNKNOWN_UNPROVEN,
            ContractRelevance.UNKNOWN_UNRESOLVED,
        }

    def test_status_enum_has_two_values(self):
        assert {s.value for s in CompatibilityEvaluationStatus} == {
            "EVALUATED",
            "NOT_EVALUATED",
        }


class TestAnalysisStatus:
    def test_two_values(self):
        assert {s.value for s in AnalysisStatus} == {"CHECKABLE", "NOT_CHECKABLE"}


class TestContractAssurance:
    def test_three_values(self):
        assert {a.value for a in ContractAssurance} == {
            "complete",
            "partial",
            "unavailable",
        }


class TestEvidenceSearchRecordEnums:
    def test_provider_status_values(self):
        assert {s.value for s in EvidenceProviderStatus} == {
            "available",
            "unavailable",
            "failed",
            "unsupported",
            "stale",
        }

    def test_completeness_values(self):
        assert {c.value for c in EvidenceCompleteness} == {
            "complete",
            "partial",
            "not_started",
        }


class TestSelectorLayer:
    def test_required_layers_present(self):
        # ADR-049 D7: required selector layers. The set is explicitly
        # extensible (a future adapter may add a layer), so this asserts
        # the seven required members are present, not that they're the only
        # ones -- a subset check, not exact-set equality, so a later
        # extension doesn't spuriously break this test.
        required = {
            "explicit_cli",
            "api_request",
            "legacy_alias",
            "run_recipe",
            "run_profile",
            "project_config",
            "built_in_default",
        }
        assert required <= {layer.value for layer in SelectorLayer}


class TestContractReasonCodes:
    def test_every_code_has_non_empty_description(self):
        assert CONTRACT_REASON_CODES
        for code, description in CONTRACT_REASON_CODES.items():
            assert isinstance(code, str) and code
            assert isinstance(description, str) and description.strip()

    def test_codes_are_snake_case_identifiers(self):
        for code in CONTRACT_REASON_CODES:
            assert code == code.lower()
            assert " " not in code

    def test_legacy_alias_reasons_are_distinguishable(self):
        # The two legacy-alias reason codes must not describe the same thing
        # (all vs public are different aliasing strengths, ADR-049 D2).
        assert (
            CONTRACT_REASON_CODES["legacy_alias_all"]
            != CONTRACT_REASON_CODES["legacy_alias_public"]
        )


class TestSchemaVersionStrategy:
    def test_each_counter_pins_its_current_value(self):
        # Not "they all start at one" any more: `IDENTITY_ALGORITHM_VERSION`
        # bumped on its own (the qualified-typedef tail fix changed the
        # produced type graph without changing any block's shape), and
        # `EVALUATION_CONTEXT_SCHEMA_VERSION` bumped separately (GateConfig
        # gained require_complete_analysis/scope, changing the persisted
        # evaluation_context.gate shape; later bumped again to 3 when CLI
        # cleanup phase two PR G2 removed field_provenance["gate.
        # exit_code_scheme"] from every persisted context) -- which is the
        # whole point of keeping four counters. Pin each one so a future
        # change to a persisted format or algorithm has to state which
        # concern it moved.
        assert CONTRACT_EVIDENCE_SCHEMA_VERSION == 1
        assert EVALUATION_CONTEXT_SCHEMA_VERSION == 3
        assert EVALUATOR_VERSION == 1
        assert IDENTITY_ALGORITHM_VERSION == 2

    def test_version_counters_are_independent_objects(self):
        # Each concern must be able to bump independently -- guard against a
        # future refactor collapsing them into one shared constant. The
        # identity counter having actually diverged from the other three is
        # the executable proof of that, not just the key count.
        counters = {
            "contract_evidence": CONTRACT_EVIDENCE_SCHEMA_VERSION,
            "evaluation_context": EVALUATION_CONTEXT_SCHEMA_VERSION,
            "evaluator": EVALUATOR_VERSION,
            "identity_algorithm": IDENTITY_ALGORITHM_VERSION,
        }
        assert len(counters) == 4
        assert counters["identity_algorithm"] != counters["contract_evidence"]
