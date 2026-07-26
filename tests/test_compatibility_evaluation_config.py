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
    DigestedItems,
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


def _identity(
    id: str, version: int = 1, sha256: str = "test-digest"
) -> ImmutableIdentity:
    return ImmutableIdentity(id=id, version=version, sha256=sha256)


def _minimal_config(**overrides) -> CompatibilityEvaluationConfig:
    fields = dict(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(base=_identity("strict_abi")),
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
                base=_identity("strict_abi"),
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
            base=_identity("strict_abi"), overrides=overrides
        )
        overrides["symbol_removed"] = Verdict.COMPATIBLE
        assert "symbol_removed" not in policy.overrides

    def test_mutating_caller_list_after_construction_does_not_leak_in(self):
        packs = [_identity("rust_c_ffi")]
        contract = ContractConfig(mode=ContractMode.PUBLIC, packs=packs)
        packs.append(_identity("another_pack"))
        assert contract.packs == (_identity("rust_c_ffi"),)

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

    def test_version_distinguishes_manifest_revisions_sharing_a_reference(self):
        # ADR-049 D7: "path, digest, manifest identity/version, and field
        # location identify the actual definition used for exact replay" --
        # a manifest can be revised under the same reference/name.
        v1 = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI, reference="security", version=1
        )
        v2 = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI, reference="security", version=2
        )
        assert v1.reference == v2.reference
        assert v1 != v2


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


class TestImmutableIdentityRequiresDigest:
    # ADR-049 D6/D7: every worked example shows a populated digest alongside
    # id/version ({id: strict_abi, version: 1, sha256: "..."}) -- an identity
    # with no digest cannot detect drift if the same id/version is
    # redefined, so sha256 is a required field, not optional.
    def test_missing_sha256_is_a_type_error(self):
        with pytest.raises(TypeError):
            ImmutableIdentity(id="strict_abi", version=1)  # type: ignore[call-arg]

    def test_sha256_round_trips(self):
        identity = _identity("strict_abi", sha256="abc123")
        assert identity.sha256 == "abc123"


class TestPackVersionedIdentity:
    # ADR-049 D6: "every selected provider/base/preset/pack or rule set
    # carries an immutable identity/version/digest" -- packs must be able to
    # distinguish two revisions of a pack sharing the same name, for exact
    # replay of a persisted decision.
    def test_contract_packs_carry_versioned_identity(self):
        contract = ContractConfig(
            mode=ContractMode.PUBLIC,
            packs=[_identity("rust_c_ffi", version=2, sha256="deadbeef")],
        )
        assert contract.packs[0].version == 2
        assert contract.packs[0].sha256 == "deadbeef"

    def test_policy_packs_carry_versioned_identity(self):
        policy = CompatibilityPolicyConfig(
            base=_identity("strict_abi"),
            packs=[_identity("qt_kde_cpp", version=3)],
        )
        assert policy.packs[0].id == "qt_kde_cpp"
        assert policy.packs[0].version == 3

    def test_gate_packs_carry_versioned_identity(self):
        gate = GateConfig(packs=[_identity("security_hardening")])
        assert gate.packs[0].id == "security_hardening"

    def test_two_revisions_of_same_named_pack_are_distinguishable(self):
        v1 = _identity("rust_c_ffi", version=1, sha256="aaa")
        v2 = _identity("rust_c_ffi", version=2, sha256="bbb")
        assert v1 != v2
        contract_v1 = ContractConfig(mode=ContractMode.PUBLIC, packs=[v1])
        contract_v2 = ContractConfig(mode=ContractMode.PUBLIC, packs=[v2])
        assert contract_v1 != contract_v2


class TestGateSeverityDefault:
    def test_default_gate_severity_matches_existing_defaults(self):
        # GateConfig.severity should default to the same SeverityConfig
        # defaults already used elsewhere, not a second set of defaults.
        gate = GateConfig()
        assert gate.severity == SeverityConfig()


class TestDigestedItems:
    # ADR-049 D6: variants/explicit_scope are persisted as {items, sha256},
    # not bare lists -- the digest identifies the *external source* that
    # produced the items, so a definition change is detectable even when
    # the item names stay identical. sha256 is unconditionally required,
    # including for an explicitly-selected-but-empty source: "a source was
    # selected and resolved to zero items" and "no source was selected at
    # all" are different facts, and only the latter is representable without
    # a digest -- as DigestedItems | None = None at the container level.
    def test_missing_sha256_is_a_type_error(self):
        with pytest.raises(TypeError):
            DigestedItems(items=["linux-x86_64"])  # type: ignore[call-arg]

    def test_empty_items_still_requires_a_digest(self):
        # An explicitly selected source that resolves to zero items is not
        # the same as no source at all -- it still needs its digest so a
        # later change from empty to populated is detectable on replay.
        selected_but_empty = DigestedItems(sha256="digest-of-empty-variant-file")
        assert selected_but_empty.items == ()
        assert selected_but_empty.sha256 == "digest-of-empty-variant-file"

    def test_nonempty_items_with_digest_constructs(self):
        variants = DigestedItems(items=["linux-x86_64", "windows-msvc"], sha256="abc")
        assert variants.items == ("linux-x86_64", "windows-msvc")
        assert variants.sha256 == "abc"

    def test_same_item_names_different_digest_are_distinguishable(self):
        # The whole point: identical item names, different source content.
        a = DigestedItems(items=["linux-x86_64"], sha256="digest-v1")
        b = DigestedItems(items=["linux-x86_64"], sha256="digest-v2")
        assert a != b

    def test_evidence_config_variants_defaults_to_no_source_selected(self):
        evidence = EvidenceConfig()
        assert evidence.variants is None

    def test_evidence_config_distinguishes_no_source_from_selected_empty(self):
        no_source = EvidenceConfig()
        selected_empty = EvidenceConfig(variants=DigestedItems(sha256="empty-digest"))
        assert no_source.variants is None
        assert selected_empty.variants is not None
        assert no_source != selected_empty

    def test_surface_config_explicit_scope_defaults_to_no_source_selected(self):
        surface = SurfaceConfig()
        assert surface.explicit_scope is None

    def test_surface_config_internal_namespaces_stay_a_plain_tuple(self):
        # ADR-049 D6's hints: {internal_namespaces: []} carries no digest,
        # unlike explicit_scope -- hints are advisory (D8), not replay-exact.
        surface = SurfaceConfig(internal_namespaces=["detail"])
        assert surface.internal_namespaces == ("detail",)
        assert not isinstance(surface.internal_namespaces, DigestedItems)


class TestSuppressionConfigDigest:
    def test_empty_rules_need_no_digest(self):
        suppressions = SuppressionConfig()
        assert suppressions.rules == ()
        assert suppressions.sha256 is None

    def test_nonempty_rules_without_digest_is_a_value_error(self):
        with pytest.raises(ValueError, match="sha256 is required"):
            SuppressionConfig(rules=["ignore-internal-symbol"])

    def test_nonempty_rules_with_digest_constructs(self):
        suppressions = SuppressionConfig(rules=["ignore-internal-symbol"], sha256="xyz")
        assert suppressions.rules == ("ignore-internal-symbol",)
        assert suppressions.sha256 == "xyz"
