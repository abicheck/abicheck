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

"""ADR-049 Phase 4: tests for the contract_evidence / evaluation_context /
decision_receipt snapshot block shapes."""

from __future__ import annotations

import pytest

from abicheck.compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    SurfaceConfig,
)
from abicheck.contract_evidence import (
    ContractEvidenceBlock,
    DecisionReceiptBlock,
    EvaluationContextBlock,
    EvidenceSearchRecord,
    InputIdentity,
    PersistedContractContext,
    ProviderEvidenceEntry,
    TypeGraphSnapshot,
    UnsupportedSchemaVersionError,
    check_persisted_context_versions_supported,
    check_schema_version_supported,
)
from abicheck.contract_relevance_types import (
    CONTRACT_EVIDENCE_SCHEMA_VERSION,
    EVALUATION_CONTEXT_SCHEMA_VERSION,
    EVALUATOR_VERSION,
    IDENTITY_ALGORITHM_VERSION,
    ContractMode,
    ContractRelevance,
    EvidenceCompleteness,
    EvidenceProviderStatus,
)


def _minimal_config() -> CompatibilityEvaluationConfig:
    return CompatibilityEvaluationConfig(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(
            base=ImmutableIdentity(id="strict_abi", version=1, sha256="digest")
        ),
        gate=GateConfig(),
    )


def _record(**overrides) -> EvidenceSearchRecord:
    fields = dict(
        id="rec-1",
        provider="public_header",
        side="old",
        domain_kind="public_headers",
        status=EvidenceProviderStatus.AVAILABLE,
        completeness=EvidenceCompleteness.COMPLETE,
    )
    fields.update(overrides)
    return EvidenceSearchRecord(**fields)


class TestInputIdentity:
    def test_minimal_construction(self):
        ident = InputIdentity(sha256="abc123")
        assert ident.sha256 == "abc123"
        assert ident.path is None

    def test_with_path(self):
        ident = InputIdentity(sha256="abc123", path="include/foo.h")
        assert ident.path == "include/foo.h"

    def test_empty_sha256_rejected(self):
        with pytest.raises(ValueError):
            InputIdentity(sha256="")

    def test_non_str_sha256_rejected(self):
        with pytest.raises(TypeError):
            InputIdentity(sha256=123)  # type: ignore[arg-type]

    def test_non_str_path_rejected(self):
        with pytest.raises(TypeError):
            InputIdentity(sha256="abc", path=123)  # type: ignore[arg-type]


class TestTypeGraphSnapshot:
    def test_defaults_empty(self):
        g = TypeGraphSnapshot()
        assert g.nodes == ()
        assert g.edges == ()

    def test_nodes_and_edges_frozen_as_tuples(self):
        g = TypeGraphSnapshot(nodes=["a", "b"], edges=[("a", "b")])
        assert g.nodes == ("a", "b")
        assert g.edges == (("a", "b"),)

    def test_bad_node_type_rejected(self):
        with pytest.raises(TypeError):
            TypeGraphSnapshot(nodes=[1, 2])  # type: ignore[list-item]

    def test_bad_edge_shape_rejected(self):
        with pytest.raises(TypeError):
            TypeGraphSnapshot(edges=[("a", "b", "c")])  # type: ignore[list-item]

    def test_edge_with_non_str_element_rejected(self):
        with pytest.raises(TypeError):
            TypeGraphSnapshot(edges=[(1, "b")])  # type: ignore[list-item]

    def test_bare_str_edges_rejected(self):
        with pytest.raises(TypeError):
            TypeGraphSnapshot(edges="ab")  # type: ignore[arg-type]

    def test_decoded_list_edges_accepted_and_frozen_to_tuples(self):
        # Regression (Codex review, fresh evidence): a real JSON/YAML
        # decoder has no tuple type, so a persisted-then-decoded edge is
        # always a list (e.g. ["a", "b"]), not a tuple. The exact-tuple
        # check previously rejected this normal decode-to-dataclass round
        # trip; any non-string two-element sequence must be accepted and
        # normalized to a tuple.
        g = TypeGraphSnapshot(nodes=["a", "b"], edges=[["a", "b"]])
        assert g.edges == (("a", "b"),)
        assert isinstance(g.edges[0], tuple)

    def test_edge_list_with_wrong_length_rejected(self):
        with pytest.raises(TypeError):
            TypeGraphSnapshot(edges=[["a"]])


class TestEvidenceSearchRecord:
    def test_minimal_construction(self):
        rec = _record()
        assert rec.id == "rec-1"
        assert rec.side == "old"
        assert rec.status is EvidenceProviderStatus.AVAILABLE
        assert rec.completeness is EvidenceCompleteness.COMPLETE
        assert rec.identity_coverage is EvidenceCompleteness.NOT_STARTED
        assert rec.configuration_coverage is EvidenceCompleteness.NOT_STARTED

    def test_side_must_be_old_or_new(self):
        with pytest.raises(ValueError):
            _record(side="both")

    @pytest.mark.parametrize("field_name", ["id", "provider", "side", "domain_kind"])
    def test_required_str_fields_reject_empty(self, field_name):
        with pytest.raises(ValueError):
            _record(**{field_name: ""})

    def test_status_coerces_from_raw_value(self):
        rec = _record(status="failed")
        assert rec.status is EvidenceProviderStatus.FAILED

    def test_status_rejects_unknown_value(self):
        with pytest.raises(ValueError):
            _record(status="not-a-real-status")

    def test_completeness_fields_coerce_from_raw_value(self):
        rec = _record(
            completeness="partial",
            identity_coverage="complete",
            configuration_coverage="not_started",
        )
        assert rec.completeness is EvidenceCompleteness.PARTIAL
        assert rec.identity_coverage is EvidenceCompleteness.COMPLETE
        assert rec.configuration_coverage is EvidenceCompleteness.NOT_STARTED

    def test_requested_and_searched_scope_frozen(self):
        rec = _record(requested_scope=["include/"], searched_scope=["include/"])
        assert rec.requested_scope == ("include/",)
        assert rec.searched_scope == ("include/",)

    def test_optional_str_fields_accept_none(self):
        rec = _record(
            entity_class=None, entity_scope=None, domain_identity=None, reason_code=None
        )
        assert rec.entity_class is None

    def test_optional_str_fields_reject_non_str(self):
        with pytest.raises(TypeError):
            _record(entity_class=123)  # type: ignore[arg-type]

    def test_input_identity_type_checked(self):
        with pytest.raises(TypeError):
            _record(input_identity={"sha256": "abc"})  # type: ignore[arg-type]

    def test_input_identity_accepted(self):
        rec = _record(input_identity=InputIdentity(sha256="abc"))
        assert rec.input_identity is not None
        assert rec.input_identity.sha256 == "abc"


class TestProviderEvidenceEntry:
    def test_minimal_construction(self):
        entry = ProviderEvidenceEntry(record=_record())
        assert entry.declarations == ()
        assert entry.manifests == ()
        assert isinstance(entry.type_graph, TypeGraphSnapshot)

    def test_record_type_checked(self):
        with pytest.raises(TypeError):
            ProviderEvidenceEntry(record="not-a-record")  # type: ignore[arg-type]

    def test_declarations_and_manifests_frozen(self):
        entry = ProviderEvidenceEntry(
            record=_record(), declarations=["decl1"], manifests=["man1"]
        )
        assert entry.declarations == ("decl1",)
        assert entry.manifests == ("man1",)

    def test_type_graph_type_checked(self):
        with pytest.raises(TypeError):
            ProviderEvidenceEntry(record=_record(), type_graph={"nodes": []})  # type: ignore[arg-type]


class TestContractEvidenceBlock:
    def test_defaults(self):
        block = ContractEvidenceBlock()
        assert block.providers == ()
        assert block.schema_version == CONTRACT_EVIDENCE_SCHEMA_VERSION
        assert block.identity_algorithm_version == IDENTITY_ALGORITHM_VERSION

    def test_providers_frozen_and_type_checked(self):
        entry = ProviderEvidenceEntry(record=_record())
        block = ContractEvidenceBlock(providers=[entry])
        assert block.providers == (entry,)

    def test_providers_rejects_wrong_element_type(self):
        with pytest.raises(TypeError):
            ContractEvidenceBlock(providers=["not-an-entry"])  # type: ignore[list-item]

    def test_providers_canonicalized_by_stable_identity_not_visit_order(self):
        # Regression (Codex review, fresh evidence): two collectors visiting
        # the same providers in a different order previously produced
        # unequal ContractEvidenceBlock instances, contradicting this
        # phase's own byte/order-independent round-trip gate. Providers
        # must be canonicalized by their own stable (side, id) identity,
        # not left in visit order.
        entry_a = ProviderEvidenceEntry(record=_record(id="rec-a", side="old"))
        entry_b = ProviderEvidenceEntry(record=_record(id="rec-b", side="old"))
        block1 = ContractEvidenceBlock(providers=[entry_a, entry_b])
        block2 = ContractEvidenceBlock(providers=[entry_b, entry_a])
        assert block1 == block2
        assert block1.providers == (entry_a, entry_b)
        assert block2.providers == (entry_a, entry_b)

    def test_providers_sort_key_includes_provider_not_just_side_and_id(self):
        # Regression (Codex review, fresh evidence): record.id is only
        # documented as unique to the record it labels, not globally unique
        # across providers -- two different providers reusing the same
        # local id previously sorted equal under (side, id) alone and fell
        # back to Python's stable-sort tie-break on original visit
        # position, silently reintroducing order-dependence for that case.
        entry_x = ProviderEvidenceEntry(
            record=_record(id="rec-1", side="old", provider="x_provider")
        )
        entry_y = ProviderEvidenceEntry(
            record=_record(id="rec-1", side="old", provider="y_provider")
        )
        block1 = ContractEvidenceBlock(providers=[entry_x, entry_y])
        block2 = ContractEvidenceBlock(providers=[entry_y, entry_x])
        assert block1 == block2
        assert block1.providers == (entry_x, entry_y)
        assert block2.providers == (entry_x, entry_y)

    def test_schema_version_must_be_positive_int(self):
        with pytest.raises(ValueError):
            ContractEvidenceBlock(schema_version=0)

    def test_schema_version_rejects_bool(self):
        with pytest.raises(TypeError):
            ContractEvidenceBlock(schema_version=True)  # type: ignore[arg-type]


class TestEvaluationContextBlock:
    def test_minimal_construction(self):
        cfg = _minimal_config()
        ctx = EvaluationContextBlock(resolved_config=cfg)
        assert ctx.resolved_config is cfg
        assert ctx.schema_version == EVALUATION_CONTEXT_SCHEMA_VERSION
        assert ctx.evaluator_version == EVALUATOR_VERSION
        assert ctx.identity_algorithm_version == IDENTITY_ALGORITHM_VERSION

    def test_resolved_config_type_checked(self):
        with pytest.raises(TypeError):
            EvaluationContextBlock(resolved_config="not-a-config")  # type: ignore[arg-type]

    def test_field_provenance_aliases_resolved_config_provenance(self):
        cfg = _minimal_config()
        ctx = EvaluationContextBlock(resolved_config=cfg)
        assert ctx.field_provenance is cfg.provenance

    def test_version_fields_reject_non_positive(self):
        cfg = _minimal_config()
        with pytest.raises(ValueError):
            EvaluationContextBlock(resolved_config=cfg, evaluator_version=0)


class TestDecisionReceiptBlock:
    def test_defaults_empty(self):
        receipt = DecisionReceiptBlock()
        assert receipt.evaluated_contract_roots == ()
        assert receipt.evaluated_type_closure == ()
        assert receipt.relevance_by_finding == {}

    def test_roots_and_closure_frozen(self):
        receipt = DecisionReceiptBlock(
            evaluated_contract_roots=["a"], evaluated_type_closure=["b"]
        )
        assert receipt.evaluated_contract_roots == ("a",)
        assert receipt.evaluated_type_closure == ("b",)

    def test_relevance_by_finding_coerces_raw_values(self):
        receipt = DecisionReceiptBlock(relevance_by_finding={"f1": "IN_CONTRACT"})
        assert receipt.relevance_by_finding["f1"] is ContractRelevance.IN_CONTRACT

    def test_relevance_by_finding_rejects_unknown_value(self):
        with pytest.raises(ValueError):
            DecisionReceiptBlock(relevance_by_finding={"f1": "NOT_A_REAL_VALUE"})

    def test_relevance_by_finding_rejects_non_str_key(self):
        with pytest.raises(TypeError):
            DecisionReceiptBlock(relevance_by_finding={1: "IN_CONTRACT"})  # type: ignore[dict-item]

    def test_relevance_by_finding_rejects_non_mapping(self):
        with pytest.raises(TypeError):
            DecisionReceiptBlock(relevance_by_finding=["not-a-mapping"])  # type: ignore[arg-type]

    def test_relevance_by_finding_is_immutable_mapping(self):
        receipt = DecisionReceiptBlock(relevance_by_finding={"f1": "IN_CONTRACT"})
        with pytest.raises(TypeError):
            receipt.relevance_by_finding["f2"] = ContractRelevance.NOT_APPLICABLE  # type: ignore[index]


class TestPersistedContractContext:
    def test_minimal_construction(self):
        ctx = PersistedContractContext(
            contract_evidence=ContractEvidenceBlock(),
            evaluation_context=EvaluationContextBlock(
                resolved_config=_minimal_config()
            ),
        )
        assert isinstance(ctx.decision_receipt, DecisionReceiptBlock)
        assert ctx.decision_receipt.relevance_by_finding == {}

    def test_contract_evidence_type_checked(self):
        with pytest.raises(TypeError):
            PersistedContractContext(
                contract_evidence="nope",  # type: ignore[arg-type]
                evaluation_context=EvaluationContextBlock(
                    resolved_config=_minimal_config()
                ),
            )

    def test_evaluation_context_type_checked(self):
        with pytest.raises(TypeError):
            PersistedContractContext(
                contract_evidence=ContractEvidenceBlock(),
                evaluation_context="nope",  # type: ignore[arg-type]
            )

    def test_decision_receipt_type_checked(self):
        with pytest.raises(TypeError):
            PersistedContractContext(
                contract_evidence=ContractEvidenceBlock(),
                evaluation_context=EvaluationContextBlock(
                    resolved_config=_minimal_config()
                ),
                decision_receipt="nope",  # type: ignore[arg-type]
            )


def _persisted_context(
    *,
    contract_evidence_schema_version: int = CONTRACT_EVIDENCE_SCHEMA_VERSION,
    contract_evidence_identity_version: int = IDENTITY_ALGORITHM_VERSION,
    evaluation_context_schema_version: int = EVALUATION_CONTEXT_SCHEMA_VERSION,
    evaluator_version: int = EVALUATOR_VERSION,
    evaluation_context_identity_version: int = IDENTITY_ALGORITHM_VERSION,
) -> PersistedContractContext:
    return PersistedContractContext(
        contract_evidence=ContractEvidenceBlock(
            schema_version=contract_evidence_schema_version,
            identity_algorithm_version=contract_evidence_identity_version,
        ),
        evaluation_context=EvaluationContextBlock(
            resolved_config=_minimal_config(),
            schema_version=evaluation_context_schema_version,
            evaluator_version=evaluator_version,
            identity_algorithm_version=evaluation_context_identity_version,
        ),
    )


class TestCheckSchemaVersionSupported:
    def test_equal_version_is_accepted(self):
        check_schema_version_supported(
            block_name="x", observed_version=1, supported_version=1
        )

    def test_older_version_is_accepted(self):
        check_schema_version_supported(
            block_name="x", observed_version=1, supported_version=2
        )

    def test_newer_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_schema_version_supported(
                block_name="x", observed_version=2, supported_version=1
            )
        assert excinfo.value.block_name == "x"
        assert excinfo.value.observed_version == 2
        assert excinfo.value.supported_version == 1

    def test_non_int_observed_version_rejected(self):
        with pytest.raises(TypeError):
            check_schema_version_supported(
                block_name="x",
                observed_version="1",
                supported_version=1,  # type: ignore[arg-type]
            )

    def test_bool_observed_version_rejected(self):
        with pytest.raises(TypeError):
            check_schema_version_supported(
                block_name="x",
                observed_version=True,
                supported_version=1,  # type: ignore[arg-type]
            )


class TestCheckPersistedContextVersionsSupported:
    def test_current_versions_pass(self):
        check_persisted_context_versions_supported(_persisted_context())

    def test_older_contract_evidence_with_current_evaluation_context_passes(self):
        # The documented re-evaluation case: old observations, a newly
        # resolved context -- not itself an error.
        check_persisted_context_versions_supported(
            _persisted_context(contract_evidence_schema_version=1)
        )

    def test_future_contract_evidence_schema_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_persisted_context_versions_supported(
                _persisted_context(
                    contract_evidence_schema_version=CONTRACT_EVIDENCE_SCHEMA_VERSION
                    + 1
                )
            )
        assert excinfo.value.block_name == "contract_evidence.schema_version"

    def test_future_contract_evidence_identity_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_persisted_context_versions_supported(
                _persisted_context(
                    contract_evidence_identity_version=IDENTITY_ALGORITHM_VERSION + 1
                )
            )
        assert (
            excinfo.value.block_name == "contract_evidence.identity_algorithm_version"
        )

    def test_future_evaluation_context_schema_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_persisted_context_versions_supported(
                _persisted_context(
                    evaluation_context_schema_version=EVALUATION_CONTEXT_SCHEMA_VERSION
                    + 1
                )
            )
        assert excinfo.value.block_name == "evaluation_context.schema_version"

    def test_future_evaluator_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_persisted_context_versions_supported(
                _persisted_context(evaluator_version=EVALUATOR_VERSION + 1)
            )
        assert excinfo.value.block_name == "evaluation_context.evaluator_version"

    def test_future_evaluation_context_identity_version_raises(self):
        with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
            check_persisted_context_versions_supported(
                _persisted_context(
                    evaluation_context_identity_version=IDENTITY_ALGORITHM_VERSION + 1
                )
            )
        assert (
            excinfo.value.block_name == "evaluation_context.identity_algorithm_version"
        )

    def test_non_context_argument_rejected(self):
        with pytest.raises(TypeError):
            check_persisted_context_versions_supported("not-a-context")  # type: ignore[arg-type]


class TestRoundTripEquality:
    def test_equal_construction_from_independent_calls(self):
        # Order/identity-independent equality: two independently constructed
        # blocks carrying the same logical content compare equal, the same
        # guarantee CompatibilityEvaluationConfig's own tests already check
        # for -- necessary for "equivalent semantic input resolves to an
        # equal object" (ADR-049 D7) to extend to these persisted blocks.
        rec_a = _record(requested_scope=["a", "b"])
        rec_b = _record(requested_scope=["a", "b"])
        assert rec_a == rec_b

        block_a = ContractEvidenceBlock(providers=[ProviderEvidenceEntry(record=rec_a)])
        block_b = ContractEvidenceBlock(providers=[ProviderEvidenceEntry(record=rec_b)])
        assert block_a == block_b

    def test_relevance_by_finding_mapping_order_does_not_affect_equality(self):
        receipt_a = DecisionReceiptBlock(
            relevance_by_finding={"f1": "IN_CONTRACT", "f2": "NOT_APPLICABLE"}
        )
        receipt_b = DecisionReceiptBlock(
            relevance_by_finding={"f2": "NOT_APPLICABLE", "f1": "IN_CONTRACT"}
        )
        assert receipt_a == receipt_b
