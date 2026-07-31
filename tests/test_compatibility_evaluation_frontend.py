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

"""ADR-049 Phase 1: the whole-object front-end resolver.

Covers the per-field precedence cross-product (explicit CLI/API > legacy
alias > project config > selected pack > built-in default), the two D7
compatibility exceptions, pack composition into the typed contract/gate
fields, and Phase 1's own gate: a CLI run and the equivalent typed
``CompareRequest`` resolve to an equal configuration and receipt.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, InputSpec
from abicheck.buildsource.inline import load_build_config
from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import VALID_BASE_POLICIES
from abicheck.compatibility_evaluation_frontend import (
    CONTRACT_MODE_FIELD,
    EXIT_CODE_SCHEME_FIELD,
    EXPLICIT_SCOPE_FIELD,
    GATE_PRESET_FIELD,
    INTERNAL_NAMESPACES_FIELD,
    POLICY_BASE_FIELD,
    POLICY_OVERRIDES_FIELD,
    REQUIRE_EVIDENCE_FIELD,
    SUPPRESSIONS_FIELD,
    ExplicitCompatibilityInputs,
    FrontEnd,
    ProjectCompatibilityInputs,
    SuppressionSource,
    builtin_policy_identity,
    compare_cli_inputs,
    compare_request_inputs,
    compatibility_config_from_compare_request,
    cross_front_end_differences,
    cross_front_end_equivalent,
    resolve_compatibility_evaluation_config,
    severity_preset_identity,
)
from abicheck.compatibility_evaluation_packs import PackKind
from abicheck.compatibility_evaluation_resolver import (
    ConflictingFieldValuesError,
    PackConflictError,
)
from abicheck.compatibility_evaluation_wiring import resolve_pack_field_assignments
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.errors import PackManifestError
from abicheck.policy_file import PolicyFile
from abicheck.severity import SeverityLevel, resolve_severity_config


def _write_pack(path: Path, *, pack_id: str, kind: str, assignments: str) -> Path:
    path.write_text(
        textwrap.dedent(f"""\
            id: {pack_id}
            version: 1
            kind: {kind}
            assignments:
{textwrap.indent(assignments.strip(), "              ")}
            """)
    )
    return path


def _policy_file(tmp_path: Path, body: str) -> PolicyFile:
    path = tmp_path / "policy.yml"
    path.write_text(textwrap.dedent(body))
    return PolicyFile.load(path)


def _resolve(**kwargs):
    return resolve_compatibility_evaluation_config(**kwargs)


class TestDefaults:
    """With no input at all, every field resolves to its built-in default."""

    def test_all_fields_resolve_to_built_in_default_layer(self):
        cfg = _resolve()
        assert all(
            prov.layer is SelectorLayer.BUILT_IN_DEFAULT
            for prov in cfg.provenance.values()
        )

    def test_defaults_match_todays_real_behavior(self):
        cfg = _resolve()
        # --scope-public-headers defaults to True, i.e. contract=public (D2).
        assert cfg.contract.mode is ContractMode.PUBLIC
        assert cfg.contract.unresolved == "not_checkable"
        assert cfg.policy.base.id == "strict_abi"
        assert cfg.policy.overrides == {}
        # No severity setting in effect -> `auto` resolves to legacy exit codes.
        assert cfg.gate.exit_code_scheme == "legacy"
        assert cfg.gate.preset is None
        assert cfg.surface.explicit_scope is None
        assert cfg.suppressions is None
        assert cfg.assurance.require_evidence is True

    def test_every_resolved_field_has_a_receipt_entry(self):
        cfg = _resolve()
        assert CONTRACT_MODE_FIELD in cfg.provenance
        assert POLICY_BASE_FIELD in cfg.provenance
        assert EXIT_CODE_SCHEME_FIELD in cfg.provenance
        assert SUPPRESSIONS_FIELD in cfg.provenance
        assert len(cfg.provenance) == 18


class TestContractModePrecedence:
    def test_legacy_flag_alone_selects_the_aliased_mode(self):
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(scope_public_headers=False))
        assert cfg.contract.mode is ContractMode.ALL
        assert cfg.provenance[CONTRACT_MODE_FIELD].layer is SelectorLayer.LEGACY_ALIAS

    def test_explicit_contract_outranks_the_legacy_alias(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                contract_mode="exports", scope_public_headers=False
            )
        )
        assert cfg.contract.mode is ContractMode.EXPORTS
        prov = cfg.provenance[CONTRACT_MODE_FIELD]
        assert prov.layer is SelectorLayer.EXPLICIT_CLI

    def test_shadowed_legacy_alias_is_retained_for_replay(self):
        # D7's compatibility exception shape: the explicit value wins, and the
        # disagreeing legacy input stays in the receipt rather than becoming a
        # usage error (the live CLI accepts this pair today).
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                contract_mode="all", scope_public_headers=True
            )
        )
        shadowed = cfg.provenance[CONTRACT_MODE_FIELD].shadowed_legacy
        assert shadowed is not None
        assert shadowed.reference == "scope_public_headers"

    def test_project_config_scope_public_contributes_at_its_own_tier(self):
        cfg = _resolve(project=ProjectCompatibilityInputs(scope_public=False))
        assert cfg.contract.mode is ContractMode.ALL
        assert cfg.provenance[CONTRACT_MODE_FIELD].layer is SelectorLayer.PROJECT_CONFIG

    def test_legacy_cli_flag_outranks_project_config(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(scope_public_headers=True),
            project=ProjectCompatibilityInputs(scope_public=False),
        )
        assert cfg.contract.mode is ContractMode.PUBLIC
        assert cfg.provenance[CONTRACT_MODE_FIELD].layer is SelectorLayer.LEGACY_ALIAS

    def test_untouched_flag_contributes_nothing(self):
        cfg = _resolve(explicit=ExplicitCompatibilityInputs())
        assert (
            cfg.provenance[CONTRACT_MODE_FIELD].layer is SelectorLayer.BUILT_IN_DEFAULT
        )


class TestPolicyBase:
    def test_policy_flag_alone_selects_the_base(self):
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(policy_base="sdk_vendor"))
        assert cfg.policy.base.id == "sdk_vendor"

    def test_policy_file_wins_over_policy_flag_and_records_the_shadowed_input(
        self, tmp_path
    ):
        # D7, verbatim: "when both --policy and --policy-file are supplied,
        # --policy-file keeps winning as documented and tested today;
        # provenance records the effective file-selected base plus the
        # shadowed --policy input."
        pf = _policy_file(tmp_path, "base_policy: plugin_abi\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_base="sdk_vendor", policy_file=pf
            )
        )
        assert cfg.policy.base.id == "plugin_abi"
        prov = cfg.provenance[POLICY_BASE_FIELD]
        assert prov.layer is SelectorLayer.EXPLICIT_CLI
        assert prov.path == str(tmp_path / "policy.yml")
        assert prov.shadowed_legacy is not None
        assert prov.shadowed_legacy.reference == "sdk_vendor"

    def test_agreeing_policy_and_policy_file_leave_no_shadowed_entry(self, tmp_path):
        pf = _policy_file(tmp_path, "base_policy: sdk_vendor\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_base="sdk_vendor", policy_file=pf
            )
        )
        assert cfg.provenance[POLICY_BASE_FIELD].shadowed_legacy is None

    @pytest.mark.parametrize("name", sorted(VALID_BASE_POLICIES))
    def test_every_built_in_base_has_a_distinct_digest(self, name):
        identity = builtin_policy_identity(name)
        assert identity.id == name
        assert len(identity.sha256) == 64
        others = {
            builtin_policy_identity(other).sha256
            for other in VALID_BASE_POLICIES
            if other != name
        }
        assert identity.sha256 not in others

    def test_unknown_base_is_rejected_rather_than_silently_strict_abi(self):
        # policy_kind_sets() falls back to strict_abi for an unknown name --
        # right for classification, wrong for an identity.
        with pytest.raises(ValueError, match="unknown base policy"):
            builtin_policy_identity("sdk_vendr")


class TestPolicyOverrides:
    def test_policy_file_overrides_reach_the_typed_config(self, tmp_path):
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: warn
            """,
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        assert cfg.policy.overrides["soname_changed"] is Verdict.API_BREAK
        assert (
            cfg.provenance[POLICY_OVERRIDES_FIELD].layer is SelectorLayer.EXPLICIT_CLI
        )

    def test_policy_pack_folds_in_and_explicit_override_still_wins(self, tmp_path):
        pack = _write_pack(
            tmp_path / "p.yml",
            pack_id="ecosystem",
            kind="policy",
            assignments="soname_changed: break\nfunc_added: warn\n",
        )
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: ignore
            """,
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, pack_paths=(str(pack),)
            )
        )
        # D8 composition: explicit per-kind override > selected packs.
        assert cfg.policy.overrides["soname_changed"] is Verdict.COMPATIBLE
        assert cfg.policy.overrides["func_added"] is Verdict.API_BREAK


class TestSeverityAndExitScheme:
    def test_preset_supplies_the_default_level_of_every_category(self):
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(severity_preset="strict"))
        assert cfg.gate.preset is not None
        assert cfg.gate.preset.id == "strict"
        assert cfg.gate.severity.addition is SeverityLevel.ERROR

    def test_per_category_flag_overrides_the_preset(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                severity_preset="strict", severity_addition="info"
            )
        )
        assert cfg.gate.severity.addition is SeverityLevel.INFO
        assert cfg.gate.severity.abi_breaking is SeverityLevel.ERROR

    def test_cli_category_outranks_project_config(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(severity_quality_issues="error"),
            project=ProjectCompatibilityInputs(severity_quality_issues="info"),
        )
        assert cfg.gate.severity.quality_issues is SeverityLevel.ERROR

    @pytest.mark.parametrize(
        "preset,abi,addition",
        [
            (None, None, None),
            ("strict", None, None),
            ("info-only", "error", None),
            (None, None, "warning"),
        ],
    )
    def test_resolved_severity_matches_the_existing_resolver(
        self, preset, abi, addition
    ):
        # The one severity vocabulary: this resolver must agree field-for-field
        # with severity.resolve_severity_config, which every live command uses.
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                severity_preset=preset,
                severity_abi_breaking=abi,
                severity_addition=addition,
            )
        )
        assert cfg.gate.severity == resolve_severity_config(
            preset=preset, abi_breaking=abi, addition=addition
        )

    def test_auto_scheme_resolves_to_severity_when_a_setting_is_in_effect(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                exit_code_scheme="auto", severity_preset="strict"
            )
        )
        assert cfg.gate.exit_code_scheme == "severity"
        # `auto` is a resolution-time choice, never a resolved value.
        assert (
            cfg.provenance[EXIT_CODE_SCHEME_FIELD].layer
            is SelectorLayer.BUILT_IN_DEFAULT
        )

    def test_explicit_scheme_wins_over_severity_activity(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                exit_code_scheme="legacy", severity_preset="strict"
            )
        )
        assert cfg.gate.exit_code_scheme == "legacy"

    def test_project_scheme_applies_when_no_flag_was_given(self):
        cfg = _resolve(project=ProjectCompatibilityInputs(exit_code_scheme="severity"))
        assert cfg.gate.exit_code_scheme == "severity"
        assert (
            cfg.provenance[EXIT_CODE_SCHEME_FIELD].layer is SelectorLayer.PROJECT_CONFIG
        )

    def test_preset_alias_spellings_resolve_to_one_identity(self):
        assert severity_preset_identity("info_only") == severity_preset_identity(
            "info-only"
        )


class TestExplicitScopeOverlay:
    def test_cli_and_project_symbol_lists_merge(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(public_symbols=("bar",)),
            project=ProjectCompatibilityInputs(public_symbols=("foo",)),
        )
        assert cfg.surface.explicit_scope is not None
        assert cfg.surface.explicit_scope.items == ("bar", "foo")
        prov = cfg.provenance[EXPLICIT_SCOPE_FIELD]
        assert prov.layer is SelectorLayer.EXPLICIT_CLI
        assert len(prov.selected_by) == 2

    def test_symbol_order_does_not_change_the_resolved_value(self):
        a = _resolve(explicit=ExplicitCompatibilityInputs(public_symbols=("a", "b")))
        b = _resolve(explicit=ExplicitCompatibilityInputs(public_symbols=("b", "a")))
        assert a.surface.explicit_scope == b.surface.explicit_scope

    def test_no_symbols_means_no_source_was_selected(self):
        assert _resolve().surface.explicit_scope is None


class TestInternalNamespaces:
    def test_policy_file_namespaces_reach_the_surface_config(self, tmp_path):
        pf = _policy_file(
            tmp_path,
            """\
            internal_namespaces:
              - detail
              - impl
            """,
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        assert cfg.surface.internal_namespaces == ("detail", "impl")
        assert (
            cfg.provenance[INTERNAL_NAMESPACES_FIELD].layer
            is SelectorLayer.EXPLICIT_CLI
        )


class TestSuppressions:
    def test_selected_source_is_digested_and_summarized(self, tmp_path):
        path = tmp_path / "suppress.yml"
        path.write_text(
            textwrap.dedent("""\
                version: 1
                suppressions:
                  - symbol: _ZN3foo3barEv
                    reason: intentional
                """)
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                suppression=SuppressionSource.from_file(path)
            )
        )
        assert cfg.suppressions is not None
        assert cfg.suppressions.rules == ("symbol=_ZN3foo3barEv",)
        assert len(cfg.suppressions.sha256) == 64
        assert cfg.provenance[SUPPRESSIONS_FIELD].path == str(path)

    def test_an_empty_but_selected_source_is_not_the_same_as_none(self, tmp_path):
        path = tmp_path / "suppress.yml"
        path.write_text("version: 1\nsuppressions: []\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                suppression=SuppressionSource.from_file(path)
            )
        )
        assert cfg.suppressions is not None
        assert cfg.suppressions.rules == ()


class TestPackComposition:
    def test_contract_pack_fields_reach_their_typed_targets(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments=(
                "contract.unresolved: warn\n"
                "contract.overlays: [ffi]\n"
                "assurance.require_evidence: false\n"
                "surface.internal_namespaces: [detail]\n"
            ),
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        assert cfg.contract.unresolved == "warn"
        assert cfg.contract.overlays == ("ffi",)
        assert cfg.assurance.require_evidence is False
        assert cfg.surface.internal_namespaces == ("detail",)
        assert cfg.provenance[REQUIRE_EVIDENCE_FIELD].source_kind == "pack_manifest"

    def test_gate_pack_fields_reach_their_typed_targets(self, tmp_path):
        pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="security_hardening",
            kind="gate",
            assignments=(
                "gate.exit_code_scheme: severity\ngate.severity.addition: error\n"
            ),
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        assert cfg.gate.exit_code_scheme == "severity"
        assert cfg.gate.severity.addition is SeverityLevel.ERROR

    def test_a_stated_value_outranks_a_pack_assignment(self, tmp_path):
        pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="security_hardening",
            kind="gate",
            assignments="gate.severity.addition: error\n",
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                severity_addition="info", pack_paths=(str(pack),)
            )
        )
        assert cfg.gate.severity.addition is SeverityLevel.INFO

    def test_project_config_also_outranks_a_pack_assignment(self, tmp_path):
        pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="security_hardening",
            kind="gate",
            assignments="gate.exit_code_scheme: severity\n",
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)),
            project=ProjectCompatibilityInputs(exit_code_scheme="legacy"),
        )
        assert cfg.gate.exit_code_scheme == "legacy"

    def test_selected_packs_are_recorded_per_namespace(self, tmp_path):
        contract_pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        gate_pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="security",
            kind="gate",
            assignments="gate.exit_code_scheme: severity\n",
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                pack_paths=(str(contract_pack), str(gate_pack))
            )
        )
        assert [p.id for p in cfg.contract.packs] == ["rust_c_ffi"]
        assert [p.id for p in cfg.gate.packs] == ["security"]
        assert cfg.policy.packs == ()

    def test_pack_order_does_not_change_the_resolved_config(self, tmp_path):
        first = _write_pack(
            tmp_path / "a.yml",
            pack_id="a",
            kind="gate",
            assignments="gate.severity.addition: error\n",
        )
        second = _write_pack(
            tmp_path / "b.yml",
            pack_id="b",
            kind="gate",
            assignments="gate.severity.quality_issues: error\n",
        )
        forward = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(first), str(second)))
        )
        reverse = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(second), str(first)))
        )
        assert forward == reverse

    def test_two_gate_packs_disagreeing_on_a_field_is_a_usage_error(self, tmp_path):
        first = _write_pack(
            tmp_path / "a.yml",
            pack_id="a",
            kind="gate",
            assignments="gate.exit_code_scheme: severity\n",
        )
        second = _write_pack(
            tmp_path / "b.yml",
            pack_id="b",
            kind="gate",
            assignments="gate.exit_code_scheme: legacy\n",
        )
        with pytest.raises(PackConflictError):
            _resolve(
                explicit=ExplicitCompatibilityInputs(
                    pack_paths=(str(first), str(second))
                )
            )

    def test_an_explicit_value_resolves_a_pack_conflict(self, tmp_path):
        first = _write_pack(
            tmp_path / "a.yml",
            pack_id="a",
            kind="gate",
            assignments="gate.exit_code_scheme: severity\n",
        )
        second = _write_pack(
            tmp_path / "b.yml",
            pack_id="b",
            kind="gate",
            assignments="gate.exit_code_scheme: legacy\n",
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                exit_code_scheme="legacy", pack_paths=(str(first), str(second))
            )
        )
        assert cfg.gate.exit_code_scheme == "legacy"

    def test_a_pack_may_not_assign_a_field_outside_its_namespace(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="sneaky",
            kind="contract",
            assignments="gate.exit_code_scheme: severity\n",
        )
        with pytest.raises(PackManifestError, match="may not assign"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))

    def test_a_contract_pack_may_not_set_the_run_s_contract_mode(self, tmp_path):
        # D3: no hidden preset may switch which evidence domain a run judges
        # against -- that stays the user's own per-run selection.
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="sneaky",
            kind="contract",
            assignments="contract.mode: all\n",
        )
        with pytest.raises(PackManifestError, match="may not assign"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))

    def test_a_bad_value_for_a_routable_field_is_a_hard_error(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments="contract.unresolved: maybe\n",
        )
        with pytest.raises(PackManifestError, match="contract.unresolved"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))


class TestSameTierConflicts:
    def test_two_explicit_values_for_one_field_are_a_usage_error(self):
        # Reached through the resolver's own same-tier rule: a front end that
        # hands the same field two different explicit values (a malformed
        # adapter) must fail loudly rather than pick one.
        from abicheck.compatibility_evaluation_config import ValueProvenance
        from abicheck.compatibility_evaluation_resolver import (
            FieldCandidate,
            resolve_field,
        )

        candidates = [
            FieldCandidate(
                provenance=ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI),
                value=ContractMode.PUBLIC,
            ),
            FieldCandidate(
                provenance=ValueProvenance(layer=SelectorLayer.EXPLICIT_CLI),
                value=ContractMode.ALL,
            ),
        ]
        with pytest.raises(ConflictingFieldValuesError):
            resolve_field(
                CONTRACT_MODE_FIELD,
                candidates,
                default=FieldCandidate(
                    provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
                    value=ContractMode.PUBLIC,
                ),
            )


class TestCliAdapter:
    def test_defaulted_options_are_read_only_when_actually_typed(self):
        kwargs = {"policy": "strict_abi", "scope_public_headers": True}
        untyped = compare_cli_inputs(kwargs)
        assert untyped.policy_base is None
        assert untyped.scope_public_headers is None

        typed = compare_cli_inputs(
            kwargs, explicit_parameters={"policy", "scope_public_headers"}
        )
        assert typed.policy_base == "strict_abi"
        assert typed.scope_public_headers is True

    def test_none_valued_options_are_read_directly(self):
        inputs = compare_cli_inputs(
            {
                "contract_mode": "exports",
                "severity_preset": "strict",
                "public_symbols": ("foo",),
                "exit_code_scheme": None,
            }
        )
        assert inputs.contract_mode == "exports"
        assert inputs.severity_preset == "strict"
        assert inputs.public_symbols == ("foo",)
        assert inputs.exit_code_scheme is None

    def test_untyped_defaults_resolve_exactly_like_no_input_at_all(self):
        # A `compare` run where the user typed none of these must not look
        # different from one with no inputs -- that is what makes the
        # explicitness set load-bearing rather than cosmetic.
        cfg = _resolve(
            explicit=compare_cli_inputs(
                {"policy": "strict_abi", "scope_public_headers": True}
            )
        )
        assert cfg == _resolve()


class TestApiAdapter:
    def _request(self, tmp_path: Path, **kwargs) -> CompareRequest:
        old = InputSpec(path=tmp_path / "old.so")
        new = InputSpec(path=tmp_path / "new.so")
        return CompareRequest(old=old, new=new, **kwargs)

    def test_request_fields_are_read_as_stated(self, tmp_path):
        inputs = compare_request_inputs(
            self._request(tmp_path, policy="sdk_vendor", scope_public=False)
        )
        assert inputs.policy_base == "sdk_vendor"
        assert inputs.scope_public_headers is False

    def test_one_call_adapter_resolves_a_whole_config(self, tmp_path):
        cfg = compatibility_config_from_compare_request(
            self._request(tmp_path, policy="plugin_abi", scope_public=False)
        )
        assert cfg.contract.mode is ContractMode.ALL
        assert cfg.policy.base.id == "plugin_abi"
        assert cfg.provenance[POLICY_BASE_FIELD].layer is SelectorLayer.LEGACY_ALIAS


class TestPhase1Gate:
    """ "Every front end resolves equivalent semantic input to an equal
    CompatibilityEvaluationConfig and provenance receipt." """

    def _sides(self, tmp_path: Path, *, cli_kwargs, request_kwargs, **shared):
        cli = _resolve(
            front_end=FrontEnd.CLI,
            explicit=compare_cli_inputs(
                cli_kwargs,
                explicit_parameters=set(cli_kwargs),
                **shared,
            ),
        )
        old = InputSpec(path=tmp_path / "old.so")
        new = InputSpec(path=tmp_path / "new.so")
        api = compatibility_config_from_compare_request(
            CompareRequest(old=old, new=new, **request_kwargs), **shared
        )
        return cli, api

    def test_equivalent_plain_runs_resolve_equally(self, tmp_path):
        cli, api = self._sides(
            tmp_path,
            cli_kwargs={"policy": "sdk_vendor", "scope_public_headers": False},
            request_kwargs={"policy": "sdk_vendor", "scope_public": False},
        )
        assert cross_front_end_differences(cli, api) == []

    def test_equivalent_runs_with_every_shared_field_resolve_equally(self, tmp_path):
        pf = _policy_file(
            tmp_path,
            """\
            base_policy: plugin_abi
            internal_namespaces:
              - detail
            overrides:
              soname_changed: warn
            """,
        )
        suppress = tmp_path / "s.yml"
        suppress.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n"
        )
        source = SuppressionSource.from_file(suppress)
        cli, api = self._sides(
            tmp_path,
            cli_kwargs={
                "policy": "sdk_vendor",
                "scope_public_headers": True,
                "contract_mode": "exports",
                "public_symbols": ("sym_a", "sym_b"),
            },
            request_kwargs={
                "policy": "sdk_vendor",
                "scope_public": True,
                "contract_mode": "exports",
                "contract_evaluation": True,
                "force_public_symbols": frozenset({"sym_b", "sym_a"}),
            },
            policy_file=pf,
            suppression=source,
        )
        assert cross_front_end_differences(cli, api) == []
        assert cli.contract.mode is ContractMode.EXPORTS
        assert cli.policy.base.id == "plugin_abi"

    def test_the_comparison_still_reports_a_real_divergence(self, tmp_path):
        cli, api = self._sides(
            tmp_path,
            cli_kwargs={"policy": "sdk_vendor", "scope_public_headers": True},
            request_kwargs={"policy": "plugin_abi", "scope_public": True},
        )
        differences = cross_front_end_differences(cli, api)
        assert differences
        assert any("policy" in d for d in differences)

    def test_front_end_layer_alone_is_not_a_difference(self, tmp_path):
        explicit = ExplicitCompatibilityInputs(policy_base="sdk_vendor")
        cli = _resolve(front_end=FrontEnd.CLI, explicit=explicit)
        api = _resolve(front_end=FrontEnd.API, explicit=explicit)
        assert cross_front_end_differences(cli, api) == []
        # ... but the receipt still records which front end it was.
        assert cli.provenance[POLICY_BASE_FIELD].layer is SelectorLayer.LEGACY_ALIAS
        assert api.provenance[POLICY_BASE_FIELD].layer is SelectorLayer.LEGACY_ALIAS
        cli_mode = _resolve(
            front_end=FrontEnd.CLI,
            explicit=ExplicitCompatibilityInputs(contract_mode="all"),
        )
        api_mode = _resolve(
            front_end=FrontEnd.API,
            explicit=ExplicitCompatibilityInputs(contract_mode="all"),
        )
        assert cli_mode.provenance[CONTRACT_MODE_FIELD].layer is (
            SelectorLayer.EXPLICIT_CLI
        )
        assert api_mode.provenance[CONTRACT_MODE_FIELD].layer is (
            SelectorLayer.API_REQUEST
        )
        assert cross_front_end_differences(cli_mode, api_mode) == []


class TestProjectConfigProjection:
    def test_build_config_projects_onto_the_fields_this_resolver_owns(self, tmp_path):
        cfg_path = tmp_path / ".abicheck.yml"
        cfg_path.write_text(
            textwrap.dedent("""\
                scope:
                  public: false
                  public_symbols: [foo]
                severity:
                  preset: strict
                exit_code_scheme: severity
                """)
        )
        project = ProjectCompatibilityInputs.from_build_config(
            load_build_config(cfg_path), path=cfg_path
        )
        assert project is not None
        resolved = _resolve(project=project)
        assert resolved.contract.mode is ContractMode.ALL
        assert resolved.surface.explicit_scope is not None
        assert resolved.surface.explicit_scope.items == ("foo",)
        assert resolved.gate.preset is not None
        assert resolved.gate.preset.id == "strict"
        assert resolved.gate.exit_code_scheme == "severity"
        assert resolved.provenance[GATE_PRESET_FIELD].path == str(cfg_path)

    def test_absent_config_projects_to_none(self):
        assert ProjectCompatibilityInputs.from_build_config(None) is None


class TestMalformedInput:
    """Every route validates its own value, and every bad input fails loudly."""

    def test_unknown_severity_preset_is_rejected(self):
        with pytest.raises(ValueError, match="unknown severity preset"):
            severity_preset_identity("stricter")

    def test_a_gate_pack_severity_must_name_a_real_level(self, tmp_path):
        pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="g",
            kind="gate",
            assignments="gate.severity.addition: critical\n",
        )
        with pytest.raises(PackManifestError, match="gate.severity.addition"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))

    def test_a_contract_pack_bool_field_must_be_a_bool(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="c",
            kind="contract",
            assignments="assurance.require_evidence: yes-please\n",
        )
        with pytest.raises(PackManifestError, match="must be a bool"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))

    def test_a_contract_pack_list_field_must_hold_strings(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="c",
            kind="contract",
            assignments="contract.overlays: 7\n",
        )
        with pytest.raises(PackManifestError, match="must be a list of str"):
            _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))

    def test_a_bare_scalar_is_accepted_for_a_list_field(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="c",
            kind="contract",
            assignments="contract.overlays: ffi\n",
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        assert cfg.contract.overlays == ("ffi",)

    def test_policy_packs_do_not_go_through_the_field_router(self, tmp_path):
        # Its assignments are ChangeKind slugs, a different vocabulary that
        # resolve_policy_pack_overrides owns.
        with pytest.raises(ValueError, match="does not handle PackKind.POLICY"):
            resolve_pack_field_assignments([], PackKind.POLICY)


class TestDifferenceReporting:
    def test_a_differing_suppression_source_is_reported(self, tmp_path):
        path = tmp_path / "s.yml"
        path.write_text("version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n")
        with_source = _resolve(
            explicit=ExplicitCompatibilityInputs(
                suppression=SuppressionSource.from_file(path)
            )
        )
        without = _resolve()
        differences = cross_front_end_differences(with_source, without)
        assert any(d.startswith("suppressions:") for d in differences)

    def test_a_receipt_key_present_on_only_one_side_is_reported(self):
        from dataclasses import replace

        cfg = _resolve()
        trimmed = replace(
            cfg,
            provenance={
                k: v for k, v in cfg.provenance.items() if k != CONTRACT_MODE_FIELD
            },
        )
        differences = cross_front_end_differences(cfg, trimmed)
        assert any("present on only one side" in d for d in differences)

    def test_a_differing_receipt_entry_is_reported(self):
        # Same resolved value, different layer that isn't the permitted
        # explicit_cli/api_request pair: a real receipt divergence.
        by_flag = _resolve(
            explicit=ExplicitCompatibilityInputs(scope_public_headers=True)
        )
        by_project = _resolve(project=ProjectCompatibilityInputs(scope_public=True))
        assert by_flag.contract == by_project.contract
        differences = cross_front_end_differences(by_flag, by_project)
        assert any(CONTRACT_MODE_FIELD in d for d in differences)

    def test_equivalence_helper_agrees_with_the_difference_list(self):
        assert cross_front_end_equivalent(_resolve(), _resolve())


class TestPackSuppliedSeverityActivatesAuto:
    def test_a_gate_pack_severity_counts_as_a_severity_setting_in_effect(
        self, tmp_path
    ):
        # `auto` means "severity when a severity setting is in effect" -- a
        # pack-supplied category is no less in effect than a config-supplied
        # one, and nothing else here states a scheme.
        pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="g",
            kind="gate",
            assignments="gate.severity.addition: error\n",
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        assert cfg.gate.severity.addition is SeverityLevel.ERROR
        assert cfg.gate.exit_code_scheme == "severity"

    def test_a_project_config_category_also_counts(self):
        cfg = _resolve(
            project=ProjectCompatibilityInputs(severity_quality_issues="error")
        )
        assert cfg.gate.severity.quality_issues is SeverityLevel.ERROR
        assert cfg.gate.exit_code_scheme == "severity"
