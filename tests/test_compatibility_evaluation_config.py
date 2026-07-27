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
    identity_id: str, version: int = 1, sha256: str = "test-digest"
) -> ImmutableIdentity:
    return ImmutableIdentity(id=identity_id, version=version, sha256=sha256)


def _minimal_config(**overrides) -> CompatibilityEvaluationConfig:
    fields = dict(
        contract=ContractConfig(mode=ContractMode.PUBLIC),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(base=_identity("strict_abi")),
        gate=GateConfig(),
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

    def test_unknown_change_kind_slug_in_overrides_is_rejected(self):
        # ADR-049 D8: an unknown ChangeKind is a hard load error regardless
        # of which front end constructs the override -- not only
        # policy_file.py's YAML path.
        with pytest.raises(ValueError, match="totally_made_up_kind"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"),
                overrides={"totally_made_up_kind": Verdict.BREAKING},
            )

    def test_non_string_override_key_is_rejected(self):
        # Codex review: a non-str key mixed with a real str key (e.g.
        # overrides={123: Verdict.BREAKING, "bogus": Verdict.BREAKING})
        # previously reached `sorted(set(self.overrides) -
        # _VALID_CHANGE_KIND_SLUGS)` unvalidated, crashing with
        # "TypeError: '<' not supported between instances of 'str' and
        # 'int'" instead of the deliberate configuration error this
        # constructor exists to produce.
        with pytest.raises(TypeError, match=r"CompatibilityPolicyConfig\.overrides"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"),
                overrides={123: Verdict.BREAKING, "bogus": Verdict.BREAKING},  # type: ignore[dict-item]
            )

    def test_non_mapping_overrides_is_rejected(self):
        # CodeRabbit review: a non-Mapping overrides (e.g. a list of valid
        # slugs) previously sailed through the key/value checks -- which
        # only iterate -- then crashed with "AttributeError: 'list' object
        # has no attribute 'items'" at the `.items()` call further down,
        # instead of the deliberate configuration error this constructor
        # exists to produce.
        with pytest.raises(TypeError, match=r"CompatibilityPolicyConfig\.overrides"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"),
                overrides=["func_removed"],  # type: ignore[arg-type]
            )

    def test_raw_string_override_value_is_rejected(self):
        # The Mapping[str, Verdict] annotation isn't runtime-enforced -- an
        # untyped adapter passing the enum's own string value must still be
        # rejected, since only real Verdict members are accepted downstream.
        with pytest.raises(TypeError, match="func_removed"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"),
                overrides={"func_removed": "BREAKING"},
            )

    def test_yaml_facing_severity_spelling_override_value_is_rejected(self):
        # "break"/"warn"/"risk"/"ignore" are policy_file.py's YAML-facing
        # severity spellings, not Verdict values -- policy_file.py already
        # normalizes them via _SEVERITY_MAP before constructing this config,
        # so a caller passing the raw YAML spelling through directly must
        # still be rejected here rather than silently freezing an unusable
        # override.
        with pytest.raises(TypeError, match="func_removed"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"),
                overrides={"func_removed": "break"},
            )

    def test_raw_string_base_is_rejected(self):
        # base: ImmutableIdentity isn't runtime-enforced -- an untyped
        # service/manifest adapter passing a bare slug through would freeze
        # a config that can't support exact replay.
        with pytest.raises(TypeError, match=r"CompatibilityPolicyConfig\.base"):
            CompatibilityPolicyConfig(base="strict_abi")  # type: ignore[arg-type]

    def test_raw_string_pack_element_is_rejected(self):
        # Same class of gap as ContractConfig.packs, for the policy pack
        # tuple: a bare slug would otherwise crash with AttributeError
        # inside _pack_sort_key during canonicalization.
        with pytest.raises(TypeError, match="ImmutableIdentity"):
            CompatibilityPolicyConfig(
                base=_identity("strict_abi"), packs=("rust_c_ffi",)
            )


class TestContractConfigUnresolvedBehavior:
    # ADR-049 D9: unresolved_behavior is a closed two-value vocabulary
    # ("not_checkable" default, "warn" the only opt-out) -- unlike
    # ValueProvenance.source_kind, this field's domain is fully owned by
    # this module, so a typo must be rejected rather than silently accepted.
    def test_default_is_not_checkable(self):
        contract = ContractConfig(mode=ContractMode.PUBLIC)
        assert contract.unresolved == "not_checkable"

    def test_warn_is_accepted(self):
        contract = ContractConfig(mode=ContractMode.PUBLIC, unresolved="warn")
        assert contract.unresolved == "warn"

    def test_unsupported_value_is_a_value_error(self):
        with pytest.raises(ValueError, match="unresolved"):
            ContractConfig(mode=ContractMode.PUBLIC, unresolved="warning")

    def test_raw_string_mode_is_rejected(self):
        # The `mode: ContractMode` annotation isn't runtime-enforced -- an
        # untyped adapter passing a typo'd string (or the enum's own string
        # value) must still be rejected, matching the CompatibilityPolicyConfig
        # overrides Verdict check.
        with pytest.raises(TypeError, match=r"ContractConfig\.mode"):
            ContractConfig(mode="public")  # type: ignore[arg-type]

    def test_overlays_order_does_not_affect_equality(self):
        # ADR-049 D2: overlays contribute to a set of selected public
        # roots -- like packs/providers, an unordered selection, so two
        # equivalent selections in different orders must compare equal
        # (D7's equivalent-input equality guarantee).
        forward = ContractConfig(mode=ContractMode.PUBLIC, overlays=("a", "b"))
        backward = ContractConfig(mode=ContractMode.PUBLIC, overlays=("b", "a"))
        assert forward == backward
        assert forward.overlays == ("a", "b")

    def test_overlays_duplicates_are_collapsed(self):
        contract = ContractConfig(mode=ContractMode.PUBLIC, overlays=("a", "a", "b"))
        assert contract.overlays == ("a", "b")

    def test_generator_overlays_is_not_silently_emptied(self):
        # Codex review: same _canonical_tuple gap as DigestedItems.items --
        # a one-shot generator was fully consumed by the validation pass
        # before sorted(s, ...) ran, silently discarding the caller's
        # actual overlays.
        contract = ContractConfig(
            mode=ContractMode.PUBLIC, overlays=(x for x in ["b", "a"])
        )
        assert contract.overlays == ("a", "b")

    def test_raw_string_pack_element_is_rejected(self):
        # packs: tuple[ImmutableIdentity, ...] isn't runtime-enforced per
        # element -- a bare slug would otherwise crash with AttributeError
        # inside _pack_sort_key during canonicalization instead of failing
        # validation cleanly at construction (Codex review).
        with pytest.raises(TypeError, match="ImmutableIdentity"):
            ContractConfig(mode=ContractMode.PUBLIC, packs=("rust_c_ffi",))

    def test_scalar_string_overlays_is_rejected(self):
        # Codex review: a bare str passed where a collection is expected
        # (overlays="api") iterates into characters ("a", "p", "i") instead
        # of raising, silently selecting the wrong contract roots.
        with pytest.raises(TypeError, match="bare str"):
            ContractConfig(mode=ContractMode.PUBLIC, overlays="api")

    def test_non_string_overlays_element_is_rejected(self):
        # Codex review: the scalar-string guard alone doesn't validate
        # individual elements -- overlays=(123,) previously constructed
        # successfully, silently installing an invalid root.
        with pytest.raises(TypeError, match="str"):
            ContractConfig(mode=ContractMode.PUBLIC, overlays=(123,))


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


class TestSelectedByEntry:
    # Codex review: ValueProvenance.__post_init__ only checks that each
    # selected_by element *is* a SelectedByEntry -- it never validated that
    # a SelectedByEntry's own fields are well-typed. SelectedByEntry(layer=
    # "explicit_cli") previously constructed successfully and would freeze
    # into the typed effective configuration's receipt provenance, so a
    # later consumer expecting a real SelectorLayer (e.g. `.value`) would
    # fail instead of the malformed input being rejected at construction.
    def test_real_fields_construct(self):
        entry = SelectedByEntry(
            layer=SelectorLayer.EXPLICIT_CLI, option="--policy-file", argument_index=4
        )
        assert entry.layer is SelectorLayer.EXPLICIT_CLI
        assert entry.option == "--policy-file"
        assert entry.argument_index == 4

    def test_raw_string_layer_is_rejected(self):
        with pytest.raises(TypeError, match=r"SelectedByEntry\.layer"):
            SelectedByEntry(layer="explicit_cli")  # type: ignore[arg-type]

    def test_non_string_option_is_rejected(self):
        with pytest.raises(TypeError, match=r"SelectedByEntry\.option"):
            SelectedByEntry(layer=SelectorLayer.EXPLICIT_CLI, option=123)  # type: ignore[arg-type]

    def test_non_int_argument_index_is_rejected(self):
        with pytest.raises(TypeError, match=r"SelectedByEntry\.argument_index"):
            SelectedByEntry(layer=SelectorLayer.EXPLICIT_CLI, argument_index="4")  # type: ignore[arg-type]

    def test_bool_argument_index_is_rejected(self):
        with pytest.raises(TypeError, match=r"SelectedByEntry\.argument_index"):
            SelectedByEntry(layer=SelectorLayer.EXPLICIT_CLI, argument_index=True)  # type: ignore[arg-type]

    def test_non_string_path_is_rejected(self):
        with pytest.raises(TypeError, match=r"SelectedByEntry\.path"):
            SelectedByEntry(layer=SelectorLayer.EXPLICIT_CLI, path=123)  # type: ignore[arg-type]


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

    def test_shadowed_legacy_defaults_to_none(self):
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI)
        assert prov.shadowed_legacy is None

    def test_shadowed_legacy_round_trips(self):
        # ADR-049 D7's --policy/--policy-file exception: "provenance records
        # the file-selected effective base and the shadowed --policy input."
        legacy = ValueProvenance(layer=SelectorLayer.LEGACY_ALIAS, path="legacy.yml")
        winner = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI,
            path="policy-file.yml",
            shadowed_legacy=legacy,
        )
        assert winner.shadowed_legacy is not None
        assert winner.shadowed_legacy.layer is SelectorLayer.LEGACY_ALIAS
        assert winner.shadowed_legacy.path == "legacy.yml"

    def test_raw_mapping_shadowed_legacy_is_rejected(self):
        # Codex review: only selected_by was validated in __post_init__, so
        # shadowed_legacy={} previously constructed successfully -- the
        # enclosing ValueProvenance would then freeze into
        # CompatibilityEvaluationConfig.provenance as a valid entry while
        # retaining a caller-owned mutable dict, and a receipt consumer
        # expecting shadowed_legacy.layer would get the wrong interface.
        with pytest.raises(TypeError, match=r"ValueProvenance\.shadowed_legacy"):
            ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, shadowed_legacy={})  # type: ignore[arg-type]

    def test_raw_string_layer_is_rejected(self):
        # Codex review: only shadowed_legacy/selected_by were validated --
        # ValueProvenance's own scalar fields, including layer itself, were
        # not, so a typo'd string could freeze into a receipt a consumer
        # expects to call SelectorLayer attributes (e.g. .value) on.
        with pytest.raises(TypeError, match=r"ValueProvenance\.layer"):
            ValueProvenance(layer="explicit_cli")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field_name", ["source_kind", "reference", "path", "sha256", "field_location"]
    )
    def test_non_string_optional_field_is_rejected(self, field_name):
        with pytest.raises(TypeError, match=rf"ValueProvenance\.{field_name}"):
            ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, **{field_name: []})

    def test_non_int_version_is_rejected(self):
        with pytest.raises(TypeError, match=r"ValueProvenance\.version"):
            ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, version="1")  # type: ignore[arg-type]

    def test_bool_version_is_rejected(self):
        with pytest.raises(TypeError, match=r"ValueProvenance\.version"):
            ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, version=True)  # type: ignore[arg-type]

    def test_empty_string_sha256_is_rejected(self):
        # Codex review: an empty sha256 is exactly as unable to detect
        # content drift on replay as no digest at all -- None already means
        # "no digest," so "" (a digest that proves nothing) must not be a
        # separate silently-accepted state.
        with pytest.raises(ValueError, match=r"ValueProvenance\.sha256"):
            ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, sha256="")

    def test_none_sha256_still_constructs(self):
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI, sha256=None)
        assert prov.sha256 is None

    def test_none_optional_fields_construct(self):
        prov = ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI)
        assert prov.source_kind is None
        assert prov.reference is None
        assert prov.version is None
        assert prov.path is None
        assert prov.sha256 is None
        assert prov.field_location is None


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

    def test_required_without_implementation_is_a_type_error(self):
        # ADR-049 D6: a required capability with no pinned implementation
        # cannot be replayed exactly.
        with pytest.raises(TypeError):
            EvidenceProviderRequirement(capability="active_ast", required=True)  # type: ignore[call-arg]

    def test_optional_capability_still_requires_implementation(self):
        # ADR-049 D6: "every selected provider ... carries an immutable
        # identity/version/digest" applies regardless of required/optional --
        # a provider not selected at all is represented by omitting its
        # entry from EvidenceConfig.providers, not by implementation=None.
        with pytest.raises(TypeError):
            EvidenceProviderRequirement(capability="active_ast", required=False)  # type: ignore[call-arg]

    def test_optional_capability_with_pinned_implementation(self):
        req = EvidenceProviderRequirement(
            capability="active_ast",
            required=False,
            implementation=ImmutableIdentity(id="clang_ast", version=1, sha256="abc"),
        )
        assert req.required is False
        assert req.implementation.id == "clang_ast"

    def test_raw_string_implementation_is_rejected(self):
        # implementation: ImmutableIdentity isn't runtime-enforced -- an
        # untyped adapter passing a bare slug would otherwise crash with
        # AttributeError in _provider_sort_key's canonicalization instead
        # of failing validation cleanly at construction.
        with pytest.raises(
            TypeError, match=r"EvidenceProviderRequirement\.implementation"
        ):
            EvidenceProviderRequirement(
                capability="headers",
                required=True,
                implementation="castxml",  # type: ignore[arg-type]
            )

    def test_raw_string_required_is_rejected(self):
        # required: bool isn't runtime-enforced -- an untyped adapter
        # passing a truthy non-bool string (e.g. "false") would otherwise be
        # silently accepted and could raise TypeError when mixed with real
        # bool entries during _provider_sort_key's canonical sort, instead
        # of failing validation cleanly at construction.
        with pytest.raises(TypeError, match=r"EvidenceProviderRequirement\.required"):
            EvidenceProviderRequirement(
                capability="headers",
                required="false",  # type: ignore[arg-type]
                implementation=ImmutableIdentity(
                    id="castxml", version=1, sha256="a" * 64
                ),
            )

    def test_non_string_capability_is_rejected(self):
        # Codex review: capability=123 previously passed through unnoticed;
        # combined with a normal string-named provider it crashes
        # _provider_sort_key's canonical sort comparing int and str.
        with pytest.raises(TypeError, match=r"EvidenceProviderRequirement\.capability"):
            EvidenceProviderRequirement(
                capability=123,  # type: ignore[arg-type]
                required=True,
                implementation=ImmutableIdentity(
                    id="castxml", version=1, sha256="a" * 64
                ),
            )

    def test_empty_string_capability_is_rejected(self):
        with pytest.raises(TypeError, match=r"EvidenceProviderRequirement\.capability"):
            EvidenceProviderRequirement(
                capability="",
                required=True,
                implementation=ImmutableIdentity(
                    id="castxml", version=1, sha256="a" * 64
                ),
            )

    def test_raw_string_provider_element_is_rejected(self):
        # EvidenceConfig.providers: tuple[EvidenceProviderRequirement, ...]
        # isn't runtime-enforced per element -- a bare object would
        # otherwise crash with AttributeError inside _provider_sort_key
        # during canonicalization instead of failing validation cleanly.
        with pytest.raises(TypeError, match="EvidenceProviderRequirement"):
            EvidenceConfig(providers=("castxml",))  # type: ignore[arg-type]

    def test_providers_tuple_is_frozen(self):
        evidence = EvidenceConfig(
            providers=[
                EvidenceProviderRequirement(
                    capability="active_ast",
                    required=True,
                    implementation=ImmutableIdentity(
                        id="clang_ast", version=1, sha256="abc123"
                    ),
                ),
            ]
        )
        assert isinstance(evidence.providers, tuple)
        assert evidence.providers[0].capability == "active_ast"


class TestAssuranceConfig:
    def test_default_requires_evidence(self):
        assert AssuranceConfig().require_evidence is True

    def test_raw_string_require_evidence_is_rejected(self):
        # require_evidence: bool isn't runtime-enforced -- an untyped
        # manifest/API adapter passing a truthy non-bool string (e.g.
        # "false") would otherwise be silently accepted, and the assurance
        # evaluator would treat an explicitly disabled requirement as
        # enabled.
        with pytest.raises(TypeError, match=r"AssuranceConfig\.require_evidence"):
            AssuranceConfig(require_evidence="false")  # type: ignore[arg-type]


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

    def test_empty_string_sha256_is_rejected(self):
        # A present-but-empty digest is exactly as useless as a missing one.
        with pytest.raises(ValueError, match="non-empty digest"):
            ImmutableIdentity(id="strict_abi", version=1, sha256="")

    def test_empty_string_id_is_rejected(self):
        # An identity with no name can't say what its digest represents --
        # the same replay-exactness guarantee sha256 already carries.
        with pytest.raises(ValueError, match=r"ImmutableIdentity\.id"):
            ImmutableIdentity(id="", version=1, sha256="abc123")

    def test_non_string_id_is_rejected(self):
        # Codex review: construction previously checked only truthiness, so
        # a non-str id was silently accepted -- combining it with a
        # correctly typed sibling revision of the same pack later crashes
        # _pack_sort_key's canonical sort comparing incompatible types.
        with pytest.raises(TypeError, match=r"ImmutableIdentity\.id"):
            ImmutableIdentity(id=123, version=1, sha256="abc123")  # type: ignore[arg-type]

    def test_non_int_version_is_rejected(self):
        with pytest.raises(TypeError, match=r"ImmutableIdentity\.version"):
            ImmutableIdentity(id="strict_abi", version="1", sha256="abc123")  # type: ignore[arg-type]

    def test_bool_version_is_rejected(self):
        # bool is a subclass of int -- must still be rejected, since a
        # version of True/False has no meaningful ordering against a real
        # integer version.
        with pytest.raises(TypeError, match=r"ImmutableIdentity\.version"):
            ImmutableIdentity(id="strict_abi", version=True, sha256="abc123")  # type: ignore[arg-type]

    def test_non_string_sha256_is_rejected(self):
        with pytest.raises(TypeError, match=r"ImmutableIdentity\.sha256"):
            ImmutableIdentity(id="strict_abi", version=1, sha256=123)  # type: ignore[arg-type]


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


class TestPackAndProviderOrderIndependence:
    # ADR-049 D8: "Pack order never decides semantics"; D7: "equivalent
    # semantic inputs must resolve to an equivalent object" -- selecting
    # the same packs/providers in a different order must not change
    # equality.
    def test_contract_packs_order_independent(self):
        a = _identity("rust_c_ffi")
        b = _identity("qt_kde_cpp")
        forward = ContractConfig(mode=ContractMode.PUBLIC, packs=[a, b])
        backward = ContractConfig(mode=ContractMode.PUBLIC, packs=[b, a])
        assert forward == backward
        assert forward.packs == backward.packs

    def test_policy_packs_order_independent(self):
        a = _identity("qt_kde_cpp")
        b = _identity("glibc_symbol_versioned")
        forward = CompatibilityPolicyConfig(base=_identity("strict_abi"), packs=[a, b])
        backward = CompatibilityPolicyConfig(base=_identity("strict_abi"), packs=[b, a])
        assert forward == backward

    def test_gate_packs_order_independent(self):
        a = _identity("security_hardening")
        b = _identity("release_governance")
        forward = GateConfig(packs=[a, b])
        backward = GateConfig(packs=[b, a])
        assert forward == backward

    def test_evidence_providers_order_independent(self):
        active_ast = EvidenceProviderRequirement(
            capability="active_ast",
            required=True,
            implementation=_identity("clang_ast"),
        )
        guarded_index = EvidenceProviderRequirement(
            capability="guarded_declaration_index",
            required=True,
            implementation=_identity("guarded_index"),
        )
        forward = EvidenceConfig(providers=[active_ast, guarded_index])
        backward = EvidenceConfig(providers=[guarded_index, active_ast])
        assert forward == backward

    def test_packs_sharing_id_and_version_but_different_digest_stay_order_independent(
        self,
    ):
        # Same (id, version), different content -- a sort key that stops at
        # (id, version) ties and a stable sort would then just preserve
        # input order, so reversing the input would produce an unequal
        # config. The digest must break the tie.
        conflicting_a = _identity("rust_c_ffi", sha256="content-a")
        conflicting_b = _identity("rust_c_ffi", sha256="content-b")
        forward = ContractConfig(
            mode=ContractMode.PUBLIC, packs=[conflicting_a, conflicting_b]
        )
        backward = ContractConfig(
            mode=ContractMode.PUBLIC, packs=[conflicting_b, conflicting_a]
        )
        assert forward == backward

    def test_providers_sharing_impl_id_and_version_but_different_digest_stay_order_independent(
        self,
    ):
        impl_a = _identity("clang_ast", sha256="content-a")
        impl_b = _identity("clang_ast", sha256="content-b")
        req_a = EvidenceProviderRequirement(
            capability="active_ast", required=True, implementation=impl_a
        )
        req_b = EvidenceProviderRequirement(
            capability="active_ast", required=True, implementation=impl_b
        )
        forward = EvidenceConfig(providers=[req_a, req_b])
        backward = EvidenceConfig(providers=[req_b, req_a])
        assert forward == backward


class TestPackAndProviderDeduplication:
    # ADR-049 D7: "Equivalent duplicate values are accepted" -- the same
    # pack/provider selected twice (e.g. once via a recipe default, once
    # via an explicit CLI flag) must resolve the same as selecting it once.
    def test_duplicate_pack_collapses_to_one(self):
        pack = _identity("rust_c_ffi")
        once = ContractConfig(mode=ContractMode.PUBLIC, packs=[pack])
        twice = ContractConfig(mode=ContractMode.PUBLIC, packs=[pack, pack])
        assert once == twice
        assert twice.packs == (pack,)

    def test_duplicate_pack_among_others_collapses(self):
        a = _identity("rust_c_ffi")
        b = _identity("qt_kde_cpp")  # sorts before "rust_c_ffi"
        deduped = ContractConfig(mode=ContractMode.PUBLIC, packs=[a, b, a])
        assert deduped.packs == (b, a)

    def test_duplicate_provider_collapses_to_one(self):
        req = EvidenceProviderRequirement(
            capability="active_ast",
            required=True,
            implementation=_identity("clang_ast"),
        )
        once = EvidenceConfig(providers=[req])
        twice = EvidenceConfig(providers=[req, req])
        assert once == twice
        assert twice.providers == (req,)


class TestGateSeverityDefault:
    def test_default_gate_severity_matches_existing_defaults(self):
        # GateConfig.severity should default to the same SeverityConfig
        # defaults already used elsewhere, not a second set of defaults.
        gate = GateConfig()
        assert gate.severity == SeverityConfig()


class TestGateConfigExitCodeScheme:
    # ADR-037 D12's CLI-facing --exit-code-scheme choice is {auto, legacy,
    # severity}, but "auto" is a resolution-time choice already resolved to
    # legacy/severity by the time an effective GateConfig is constructed
    # (cli.py's _announce_exit_scheme docstring) -- so it's excluded here.
    def test_default_is_severity(self):
        gate = GateConfig()
        assert gate.exit_code_scheme == "severity"

    def test_legacy_is_accepted(self):
        gate = GateConfig(exit_code_scheme="legacy")
        assert gate.exit_code_scheme == "legacy"

    def test_auto_is_rejected(self):
        with pytest.raises(ValueError, match="exit_code_scheme"):
            GateConfig(exit_code_scheme="auto")

    def test_unsupported_value_is_a_value_error(self):
        with pytest.raises(ValueError, match="exit_code_scheme"):
            GateConfig(exit_code_scheme="severty")

    def test_none_preset_is_accepted(self):
        gate = GateConfig()
        assert gate.preset is None

    def test_real_identity_preset_is_accepted(self):
        gate = GateConfig(preset=_identity("strict_gate"))
        assert gate.preset == _identity("strict_gate")

    def test_raw_string_preset_is_rejected(self):
        # Same class of gap as CompatibilityPolicyConfig.base: preset:
        # ImmutableIdentity | None isn't runtime-enforced.
        with pytest.raises(TypeError, match=r"GateConfig\.preset"):
            GateConfig(preset="strict_gate")  # type: ignore[arg-type]

    def test_raw_mapping_severity_is_rejected(self):
        # severity: SeverityConfig isn't runtime-enforced -- an untyped
        # adapter passing a raw mapping/string would freeze a config that
        # downstream gate evaluation expects to call
        # level_for_kind()/has_errors() on.
        with pytest.raises(TypeError, match=r"GateConfig\.severity"):
            GateConfig(severity={"addition": "error"})  # type: ignore[arg-type]

    def test_raw_string_pack_element_is_rejected(self):
        # Same class of gap as ContractConfig.packs, for the gate pack
        # tuple: a bare slug would otherwise crash with AttributeError
        # inside _pack_sort_key during canonicalization.
        with pytest.raises(TypeError, match="ImmutableIdentity"):
            GateConfig(packs=("strict_gate_pack",))


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

    def test_empty_string_sha256_is_rejected(self):
        # A present-but-empty digest is exactly as useless as a missing one.
        with pytest.raises(ValueError, match="non-empty digest"):
            DigestedItems(sha256="", items=["linux-x86_64"])

    def test_non_string_sha256_is_rejected(self):
        # Codex review: a non-str, truthy sha256 previously passed the
        # truthiness-only check outright, claiming a SHA-256 identity while
        # actually serializing a number.
        with pytest.raises(TypeError, match=r"DigestedItems\.sha256"):
            DigestedItems(sha256=123, items=["linux-x86_64"])  # type: ignore[arg-type]

    def test_scalar_string_items_is_rejected(self):
        # Codex review: a bare str (items="linux-x86_64") would otherwise
        # iterate into characters instead of raising, causing later
        # evaluation to search the wrong variants.
        with pytest.raises(TypeError, match="bare str"):
            DigestedItems(sha256="abc", items="linux-x86_64")  # type: ignore[arg-type]

    def test_non_string_item_element_is_rejected(self):
        with pytest.raises(TypeError, match="str"):
            DigestedItems(sha256="abc", items=[123])  # type: ignore[list-item]

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

    def test_generator_items_is_not_silently_emptied(self):
        # Codex review: the validation comprehension previously consumed a
        # one-shot iterable (e.g. a generator expression) in full, so the
        # subsequent tuple(s)/sorted(s, ...) build ran against an already-
        # exhausted iterator and silently returned no items instead of the
        # caller's actual ones.
        variants = DigestedItems(sha256="abc", items=(x for x in ["a", "b"]))
        assert variants.items == ("a", "b")

    def test_same_item_names_different_digest_are_distinguishable(self):
        # The whole point: identical item names, different source content.
        a = DigestedItems(items=["linux-x86_64"], sha256="digest-v1")
        b = DigestedItems(items=["linux-x86_64"], sha256="digest-v2")
        assert a != b

    def test_evidence_config_variants_defaults_to_no_source_selected(self):
        evidence = EvidenceConfig()
        assert evidence.variants is None

    def test_raw_mapping_variants_is_rejected(self):
        # Codex review: an untyped manifest adapter supplying the decoded
        # variant block as a raw mapping was previously accepted and
        # retained as-is instead of a DigestedItems -- the supposedly
        # immutable config could then change when the caller mutates that
        # mapping, and consumers expecting .sha256/.items attribute access
        # would get the wrong interface.
        with pytest.raises(TypeError, match=r"EvidenceConfig\.variants"):
            EvidenceConfig(variants={"items": ["linux-x86_64"], "sha256": "abc"})  # type: ignore[arg-type]

    def test_evidence_config_distinguishes_no_source_from_selected_empty(self):
        no_source = EvidenceConfig()
        selected_empty = EvidenceConfig(variants=DigestedItems(sha256="empty-digest"))
        assert no_source.variants is None
        assert selected_empty.variants is not None
        assert no_source != selected_empty

    def test_surface_config_explicit_scope_defaults_to_no_source_selected(self):
        surface = SurfaceConfig()
        assert surface.explicit_scope is None

    def test_raw_mapping_explicit_scope_is_rejected(self):
        # Same class of gap as EvidenceConfig.variants -- explicit_scope
        # has the identical DigestedItems | None shape.
        with pytest.raises(TypeError, match=r"SurfaceConfig\.explicit_scope"):
            SurfaceConfig(explicit_scope={"items": ["Foo::bar"], "sha256": "abc"})  # type: ignore[arg-type]

    def test_surface_config_internal_namespaces_stay_a_plain_tuple(self):
        # ADR-049 D6's hints: {internal_namespaces: []} carries no digest,
        # unlike explicit_scope -- hints are advisory (D8), not replay-exact.
        surface = SurfaceConfig(internal_namespaces=["detail"])
        assert surface.internal_namespaces == ("detail",)
        assert not isinstance(surface.internal_namespaces, DigestedItems)

    def test_internal_namespaces_order_does_not_affect_equality(self):
        # internal_leak.py consumes these set-wise (is_internal_type builds
        # set(internal_namespaces)) -- two equivalent selections in
        # different orders must compare equal (D7's equivalent-input
        # equality guarantee), same as overlays/packs.
        forward = SurfaceConfig(internal_namespaces=("detail", "impl"))
        backward = SurfaceConfig(internal_namespaces=("impl", "detail"))
        assert forward == backward
        assert forward.internal_namespaces == ("detail", "impl")

    def test_scalar_string_internal_namespaces_is_rejected(self):
        # Same class of gap as ContractConfig.overlays: a bare str
        # (internal_namespaces="detail") would otherwise iterate into
        # characters instead of raising.
        with pytest.raises(TypeError, match="bare str"):
            SurfaceConfig(internal_namespaces="detail")

    def test_non_string_internal_namespaces_element_is_rejected(self):
        # Codex review: internal_namespaces=(123,) previously constructed
        # successfully -- the integer would simply never match in
        # internal_leak.is_internal_type instead of failing validation.
        with pytest.raises(TypeError, match="str"):
            SurfaceConfig(internal_namespaces=(123,))


class TestSuppressionConfigDigest:
    # ADR-049 D6/D7 review follow-up: suppressions now distinguish "no
    # source selected" (CompatibilityEvaluationConfig.suppressions is None)
    # from "a source was selected" (a SuppressionConfig, empty rules or
    # not), the same way DigestedItems does for variants/explicit_scope --
    # sha256 is unconditionally required whenever a SuppressionConfig
    # exists at all.
    def test_missing_sha256_is_a_type_error(self):
        with pytest.raises(TypeError):
            SuppressionConfig(rules=["ignore-internal-symbol"])  # type: ignore[call-arg]

    def test_empty_string_sha256_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty digest"):
            SuppressionConfig(sha256="", rules=["ignore-internal-symbol"])

    def test_non_string_sha256_is_rejected(self):
        with pytest.raises(TypeError, match=r"SuppressionConfig\.sha256"):
            SuppressionConfig(sha256=123, rules=["ignore-internal-symbol"])  # type: ignore[arg-type]

    def test_scalar_string_rules_is_rejected(self):
        with pytest.raises(TypeError, match="bare str"):
            SuppressionConfig(sha256="abc", rules="ignore-internal-symbol")  # type: ignore[arg-type]

    def test_selected_but_empty_rules_still_requires_a_digest(self):
        selected_but_empty = SuppressionConfig(
            sha256="digest-of-empty-suppression-file"
        )
        assert selected_but_empty.rules == ()
        assert selected_but_empty.sha256 == "digest-of-empty-suppression-file"

    def test_nonempty_rules_with_digest_constructs(self):
        suppressions = SuppressionConfig(rules=["ignore-internal-symbol"], sha256="xyz")
        assert suppressions.rules == ("ignore-internal-symbol",)
        assert suppressions.sha256 == "xyz"

    def test_config_defaults_to_no_suppression_source_selected(self):
        cfg = _minimal_config()
        assert cfg.suppressions is None

    def test_config_distinguishes_no_source_from_selected_empty(self):
        no_source = _minimal_config()
        selected_empty = _minimal_config(
            suppressions=SuppressionConfig(sha256="empty-digest")
        )
        assert no_source.suppressions is None
        assert selected_empty.suppressions is not None
        assert no_source != selected_empty

    def test_raw_mapping_suppressions_is_rejected(self):
        # Codex review: an untyped manifest/API adapter supplying the
        # decoded suppression block as a raw mapping was previously
        # accepted and retained as-is instead of a SuppressionConfig -- the
        # supposedly immutable config could then change when the caller
        # mutates the mapping, and an evaluator expecting .rules/.sha256
        # attribute access would get the wrong interface. Same class of
        # gap as EvidenceConfig.variants/SurfaceConfig.explicit_scope.
        with pytest.raises(
            TypeError, match=r"CompatibilityEvaluationConfig\.suppressions"
        ):
            _minimal_config(
                suppressions={"rules": ["ignore-internal-symbol"], "sha256": "abc"}  # type: ignore[arg-type]
            )


class TestCompatibilityEvaluationConfigSectionValidation:
    # Codex review: an untyped manifest/API adapter passing a decoded
    # mapping for a required section (e.g. contract={"mode": "public"})
    # previously constructed successfully -- the supposedly-typed, frozen
    # config would retain a caller-owned mutable dict, and a consumer using
    # cfg.contract.mode would fail with AttributeError instead of a clear
    # construction-time error.
    @pytest.mark.parametrize(
        "section_name",
        ["contract", "evidence", "surface", "assurance", "policy", "gate"],
    )
    def test_raw_mapping_section_is_rejected(self, section_name: str) -> None:
        with pytest.raises(
            TypeError, match=rf"CompatibilityEvaluationConfig\.{section_name}"
        ):
            _minimal_config(**{section_name: {}})

    def test_all_real_sections_construct(self) -> None:
        cfg = _minimal_config()
        assert isinstance(cfg.contract, ContractConfig)
        assert isinstance(cfg.evidence, EvidenceConfig)
        assert isinstance(cfg.surface, SurfaceConfig)
        assert isinstance(cfg.assurance, AssuranceConfig)
        assert isinstance(cfg.policy, CompatibilityPolicyConfig)
        assert isinstance(cfg.gate, GateConfig)


class TestCompatibilityEvaluationConfigProvenanceValidation:
    # Codex review: _frozen_mapping only protects the outer mapping --
    # provenance={"contract.mode": {}} previously constructed successfully,
    # leaving a non-ValueProvenance entry that a receipt consumer expecting
    # .layer/.reference attribute access would fail on.
    def test_non_value_provenance_entry_is_rejected(self) -> None:
        with pytest.raises(
            TypeError, match=r"CompatibilityEvaluationConfig\.provenance values"
        ):
            _minimal_config(provenance={"contract.mode": {}})

    def test_non_string_provenance_key_is_rejected(self) -> None:
        with pytest.raises(
            TypeError, match=r"CompatibilityEvaluationConfig\.provenance keys"
        ):
            _minimal_config(
                provenance={123: ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI)}
            )

    def test_real_provenance_entries_construct(self) -> None:
        cfg = _minimal_config(
            provenance={
                "contract.mode": ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI)
            }
        )
        assert cfg.provenance["contract.mode"].layer is SelectorLayer.EXPLICIT_CLI

    def test_non_mapping_provenance_is_rejected(self) -> None:
        # CodeRabbit review: a non-Mapping provenance (e.g. a list of
        # ValueProvenance) previously reached `.items()` unvalidated,
        # crashing with "AttributeError: 'list' object has no attribute
        # 'items'" instead of the deliberate configuration error this
        # constructor exists to produce.
        with pytest.raises(
            TypeError, match=r"CompatibilityEvaluationConfig\.provenance"
        ):
            _minimal_config(
                provenance=[ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI)]
            )
