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

"""ADR-049 Phase 1 slice 1: tests for CompatibilityEvaluationConfig."""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    EvidenceConfig,
    EvidenceProviderRequirement,
    GateConfig,
    ImmutableIdentity,
    SelectedByEntry,
    SuppressionConfig,
    SurfaceConfig,
    ValueProvenance,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.severity import SeverityConfig, SeverityLevel


def _minimal_config(**overrides) -> CompatibilityEvaluationConfig:
    fields = dict(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(
            base=ImmutableIdentity(id="strict_abi", version=1)
        ),
        gate=GateConfig(),
        suppressions=SuppressionConfig(),
    )
    fields.update(overrides)
    return CompatibilityEvaluationConfig(**fields)


class TestConstruction:
    def test_minimal_config_constructs(self):
        cfg = _minimal_config()
        assert cfg.contract.mode is ContractMode.PUBLIC
        assert cfg.policy.base.id == "strict_abi"
        assert cfg.gate.exit_code_scheme == "severity"

    def test_gate_reuses_existing_severity_config_type(self):
        cfg = _minimal_config(
            gate=GateConfig(severity=SeverityConfig(addition=SeverityLevel.ERROR))
        )
        assert isinstance(cfg.gate.severity, SeverityConfig)
        assert cfg.gate.severity.addition is SeverityLevel.ERROR

    def test_policy_overrides_use_verdict_enum(self):
        cfg = _minimal_config(
            policy=CompatibilityPolicyConfig(
                base=ImmutableIdentity(id="strict_abi", version=1),
                overrides={"soname_bump_recommended": Verdict.BREAKING},
            )
        )
        assert cfg.policy.overrides["soname_bump_recommended"] is Verdict.BREAKING


class TestImmutability:
    def test_top_level_is_frozen(self):
        cfg = _minimal_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.contract = ContractConfig(mode=ContractMode.ALL)

    def test_sub_configs_are_frozen(self):
        contract = ContractConfig(mode=ContractMode.PUBLIC)
        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.mode = ContractMode.ALL

    def test_mapping_fields_are_immutable_proxies(self):
        cfg = _minimal_config()
        assert isinstance(cfg.provenance, MappingProxyType)
        with pytest.raises(TypeError):
            cfg.provenance["contract.mode"] = ValueProvenance(
                layer=SelectorLayer.EXPLICIT_CLI
            )

    def test_mutating_caller_dict_after_construction_does_not_leak_in(self):
        overrides = {"soname_bump_recommended": Verdict.BREAKING}
        policy = CompatibilityPolicyConfig(
            base=ImmutableIdentity(id="strict_abi", version=1), overrides=overrides
        )
        overrides["symbol_removed"] = Verdict.COMPATIBLE
        assert "symbol_removed" not in policy.overrides

    def test_mutating_caller_list_after_construction_does_not_leak_in(self):
        packs = ["rust_c_ffi"]
        contract = ContractConfig(mode=ContractMode.PUBLIC, packs=packs)
        packs.append("another_pack")
        assert contract.packs == ("rust_c_ffi",)

    def test_sequence_fields_are_tuples(self):
        cfg = _minimal_config(surface=SurfaceConfig(internal_namespaces=["detail"]))
        assert cfg.surface.internal_namespaces == ("detail",)
        assert isinstance(cfg.surface.internal_namespaces, tuple)


class TestEquality:
    def test_equivalent_semantic_input_is_an_equal_object(self):
        # ADR-049 D7: "Equivalent semantic inputs must resolve to an
        # equivalent object."
        a = _minimal_config()
        b = _minimal_config()
        assert a == b

    def test_different_contract_mode_is_not_equal(self):
        a = _minimal_config()
        b = _minimal_config(contract=ContractConfig(mode=ContractMode.ALL))
        assert a != b

    def test_provenance_map_equality_is_order_independent(self):
        p1 = ValueProvenance(layer=SelectorLayer.RUN_RECIPE, reference="public-library")
        p2 = ValueProvenance(
            layer=SelectorLayer.PROJECT_CONFIG, reference=".abicheck.yml"
        )
        a = _minimal_config(
            provenance={"contract.mode": p1, "gate.exit_code_scheme": p2}
        )
        b = _minimal_config(
            provenance={"gate.exit_code_scheme": p2, "contract.mode": p1}
        )
        assert a == b


class TestValueProvenance:
    def test_selected_by_chain_round_trips(self):
        prov = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI,
            source_kind="policy_manifest",
            reference="security",
            path="/project/abi-policy.yml",
            field_location="gate.packs[0]",
            selected_by=(
                SelectedByEntry(
                    layer=SelectorLayer.EXPLICIT_CLI,
                    option="--policy-file",
                    argument_index=4,
                ),
            ),
        )
        assert prov.selected_by[0].option == "--policy-file"
        assert isinstance(prov.selected_by, tuple)

    def test_manifest_selected_via_project_config_layer(self):
        # ADR-049 D7: the same manifest referenced by .abicheck.yml inherits
        # project_config precedence, not a precedence layer of its own.
        prov = ValueProvenance(layer=SelectorLayer.PROJECT_CONFIG, path=".abicheck.yml")
        assert prov.layer is SelectorLayer.PROJECT_CONFIG


class TestEvidenceProviderRequirement:
    def test_required_capability_with_pinned_implementation(self):
        req = EvidenceProviderRequirement(
            capability="guarded_declaration_index",
            required=True,
            implementation=ImmutableIdentity(
                id="guarded_index", version=1, sha256="abc123"
            ),
        )
        assert req.required is True
        assert req.implementation.id == "guarded_index"

    def test_providers_tuple_is_frozen(self):
        evidence = EvidenceConfig(
            providers=[
                EvidenceProviderRequirement(capability="active_ast", required=True),
            ]
        )
        assert isinstance(evidence.providers, tuple)
        assert evidence.providers[0].capability == "active_ast"


class TestGateSeverityDefault:
    def test_default_gate_severity_matches_existing_defaults(self):
        # GateConfig.severity should default to the same SeverityConfig
        # defaults already used elsewhere, not a second set of defaults.
        gate = GateConfig()
        assert gate.severity == SeverityConfig()
