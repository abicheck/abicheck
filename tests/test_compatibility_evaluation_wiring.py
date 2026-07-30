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

"""ADR-049 Phase 1: tests for the legacy-scope-flag -> contract.mode wiring,
the --policy-file -> surface.internal_namespaces wiring, and the
--pack-paths -> contract.packs/policy.packs/gate.packs wiring."""

from __future__ import annotations

import textwrap

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_packs import PackKind
from abicheck.compatibility_evaluation_resolver import PackConflictError
from abicheck.compatibility_evaluation_wiring import (
    resolve_internal_namespaces,
    resolve_legacy_contract_mode,
    resolve_policy_pack_overrides,
    resolve_selected_packs,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.errors import PackManifestError
from abicheck.policy_file import PolicyFile


def _write_pack(path, *, pack_id, kind, assignments_yaml, version=1):
    path.write_text(
        textwrap.dedent(f"""\
            id: {pack_id}
            version: {version}
            kind: {kind}
            assignments:
{textwrap.indent(assignments_yaml.strip(), "              ")}
            """)
    )
    return path


class TestUntouchedFlag:
    # scope_public_headers_is_explicit=False means the user never typed
    # either spelling of the flag -- resolution must fall through to the
    # built-in default regardless of the (click-default) boolean value.
    def test_default_true_value_resolves_to_default_layer(self):
        _mode, prov = resolve_legacy_contract_mode(
            scope_public_headers=True, scope_public_headers_is_explicit=False
        )
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_untouched_default_matches_todays_real_cli_default(self):
        # cli_options.py's scope_options default is scope_public_headers=True,
        # which LEGACY_SCOPE_FLAG_CONTRACT_MODE maps to PUBLIC -- accepting
        # ADR-049 must not silently change today's real default behavior.
        mode, _ = resolve_legacy_contract_mode(
            scope_public_headers=True, scope_public_headers_is_explicit=False
        )
        assert mode is ContractMode.PUBLIC


class TestExplicitFlag:
    def test_explicit_scope_public_headers_resolves_to_public(self):
        mode, prov = resolve_legacy_contract_mode(
            scope_public_headers=True, scope_public_headers_is_explicit=True
        )
        assert mode is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.LEGACY_ALIAS

    def test_explicit_no_scope_public_headers_resolves_to_all(self):
        mode, prov = resolve_legacy_contract_mode(
            scope_public_headers=False, scope_public_headers_is_explicit=True
        )
        assert mode is ContractMode.ALL
        assert prov.layer is SelectorLayer.LEGACY_ALIAS

    def test_provenance_records_the_option_spelling_used(self):
        _, prov_on = resolve_legacy_contract_mode(
            scope_public_headers=True, scope_public_headers_is_explicit=True
        )
        _, prov_off = resolve_legacy_contract_mode(
            scope_public_headers=False, scope_public_headers_is_explicit=True
        )
        assert prov_on.selected_by[0].option == "--scope-public-headers"
        assert prov_off.selected_by[0].option == "--no-scope-public-headers"

    def test_provenance_reference_matches_the_legacy_flag_slug(self):
        _, prov = resolve_legacy_contract_mode(
            scope_public_headers=False, scope_public_headers_is_explicit=True
        )
        assert prov.reference == "no_scope_public_headers"


class TestNoPolicyFile:
    def test_none_policy_file_resolves_to_default_layer(self):
        namespaces, prov = resolve_internal_namespaces(policy_file=None)
        assert namespaces == ()
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_untouched_default_matches_todays_real_behavior(self):
        # No --policy-file at all means no PolicyFile is even constructed --
        # every consuming step already falls back to its own
        # DEFAULT_INTERNAL_NAMESPACES today; accepting ADR-049 must not
        # silently change that.
        namespaces, _ = resolve_internal_namespaces(policy_file=None)
        assert namespaces == ()


class TestPolicyFileSetsNamespaces:
    def test_policy_file_with_namespaces_resolves_to_explicit_cli(self):
        # --policy-file is a flag the user explicitly passed on this
        # invocation -- EXPLICIT_CLI tier, not PROJECT_CONFIG (which is for
        # an implicitly-discovered project file), so it isn't silently
        # outranked by a lower-precedence-by-mechanism candidate (Codex
        # review, fresh evidence).
        pf = PolicyFile(internal_namespaces=["detail", "impl"])
        namespaces, prov = resolve_internal_namespaces(policy_file=pf)
        assert namespaces == ("detail", "impl")
        assert prov.layer is SelectorLayer.EXPLICIT_CLI

    def test_policy_file_with_empty_list_resolves_to_default_layer(self):
        # An explicit empty list is indistinguishable, once parsed, from the
        # key never being set at all -- both must fall through to the
        # built-in default, not be treated as a real EXPLICIT_CLI selection.
        pf = PolicyFile(internal_namespaces=[])
        namespaces, prov = resolve_internal_namespaces(policy_file=pf)
        assert namespaces == ()
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_namespace_order_is_canonicalized(self):
        # D7: equivalent semantic inputs must resolve to an equivalent
        # object -- two policy files listing the same set in a different
        # order must resolve identically, mirroring SurfaceConfig's own
        # canonicalization of this exact field.
        pf_a = PolicyFile(internal_namespaces=["impl", "detail"])
        pf_b = PolicyFile(internal_namespaces=["detail", "impl"])
        namespaces_a, _ = resolve_internal_namespaces(policy_file=pf_a)
        namespaces_b, _ = resolve_internal_namespaces(policy_file=pf_b)
        assert namespaces_a == namespaces_b == ("detail", "impl")

    def test_duplicate_namespaces_are_deduped(self):
        pf = PolicyFile(internal_namespaces=["detail", "detail", "impl"])
        namespaces, _ = resolve_internal_namespaces(policy_file=pf)
        assert namespaces == ("detail", "impl")

    def test_provenance_records_the_policy_file_source(self):
        from pathlib import Path

        pf = PolicyFile(internal_namespaces=["detail"], source_path=Path("policy.yml"))
        _, prov = resolve_internal_namespaces(policy_file=pf)
        assert prov.source_kind == "policy_file"
        assert prov.path == "policy.yml"
        assert prov.selected_by[0].option == "--policy-file"
        assert prov.selected_by[0].path == "policy.yml"


class TestNoPackPaths:
    def test_empty_paths_resolve_every_field_to_default_layer(self):
        result = resolve_selected_packs([])
        assert set(result) == {"contract.packs", "policy.packs", "gate.packs"}
        for value, prov in result.values():
            assert value == ()
            assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT


class TestSelectedPacksByNamespace:
    def test_one_pack_per_namespace_resolves_independently(self, tmp_path):
        contract_pack = _write_pack(
            tmp_path / "contract.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        gate_pack = _write_pack(
            tmp_path / "gate.yml",
            pack_id="security",
            kind="gate",
            assignments_yaml="gate.preset: security_hardening",
        )

        result = resolve_selected_packs([str(contract_pack), str(gate_pack)])

        (contract_ids, contract_prov) = result["contract.packs"]
        assert [i.id for i in contract_ids] == ["rust_c_ffi"]
        assert contract_prov.layer is SelectorLayer.EXPLICIT_CLI

        (gate_ids, gate_prov) = result["gate.packs"]
        assert [i.id for i in gate_ids] == ["security"]
        assert gate_prov.layer is SelectorLayer.EXPLICIT_CLI

        # No policy-kind pack was selected -- falls through to the default.
        (policy_ids, policy_prov) = result["policy.packs"]
        assert policy_ids == ()
        assert policy_prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_provenance_selected_by_records_the_pack_path(self, tmp_path):
        pack = _write_pack(
            tmp_path / "a.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        (_, prov) = resolve_selected_packs([str(pack)])["contract.packs"]
        assert prov.selected_by[0].option == "--pack"
        assert prov.selected_by[0].path == str(pack)

    def test_pack_order_does_not_affect_the_resolved_value(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="surface.internal_namespaces: [detail]",
        )
        forward = resolve_selected_packs([str(pack_a), str(pack_b)])["contract.packs"][
            0
        ]
        backward = resolve_selected_packs([str(pack_b), str(pack_a)])["contract.packs"][
            0
        ]
        assert forward == backward

    def test_identical_manifest_named_twice_is_deduplicated(self, tmp_path):
        pack = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        (ids, _) = resolve_selected_packs([str(pack), str(pack)])["contract.packs"]
        assert len(ids) == 1


class TestSelectedPacksConflicts:
    def test_two_packs_disagreeing_on_the_same_field_raises(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: public",
        )
        with pytest.raises(PackConflictError):
            resolve_selected_packs([str(pack_a), str(pack_b)])

    def test_conflict_in_one_namespace_does_not_affect_another(self, tmp_path):
        # A gate-namespace conflict must not block an unrelated,
        # non-conflicting contract-namespace pack from resolving --
        # detect_pack_conflicts is called once per namespace, independently.
        contract_pack = _write_pack(
            tmp_path / "contract.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        gate_a = _write_pack(
            tmp_path / "gate_a.yml",
            pack_id="gate_a",
            kind="gate",
            assignments_yaml="gate.preset: default",
        )
        gate_b = _write_pack(
            tmp_path / "gate_b.yml",
            pack_id="gate_b",
            kind="gate",
            assignments_yaml="gate.preset: security_hardening",
        )
        with pytest.raises(PackConflictError):
            resolve_selected_packs([str(contract_pack), str(gate_a), str(gate_b)])

    def test_two_packs_agreeing_on_the_same_field_do_not_conflict(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        (ids, _) = resolve_selected_packs([str(pack_a), str(pack_b)])["contract.packs"]
        assert {i.id for i in ids} == {"pack_a", "pack_b"}


class TestSelectedPacksInvalidManifest:
    def test_malformed_manifest_raises_pack_manifest_error(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("not: a valid pack manifest\n")
        with pytest.raises(PackManifestError):
            resolve_selected_packs([str(bad)])


class TestSelectedPacksExplicitOverrides:
    # Regression (Codex review, fresh evidence): D8's composition order is
    # "explicit per-kind override > selected packs > base policy" -- a field
    # already covered by an explicit override must be exempt from pack-vs-
    # pack conflict detection, mirroring detect_pack_conflicts's own
    # explicit_overrides parameter.

    def test_disagreeing_field_covered_by_override_does_not_raise(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: public",
        )
        result = resolve_selected_packs(
            [str(pack_a), str(pack_b)],
            explicit_overrides={PackKind.CONTRACT: {"contract.mode": "all"}},
        )
        (ids, _) = result["contract.packs"]
        assert {i.id for i in ids} == {"pack_a", "pack_b"}

    def test_override_scoped_to_its_own_namespace_only(self, tmp_path):
        # An override for the gate namespace must not exempt an unrelated
        # contract-namespace disagreement on a same-named field.
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: public",
        )
        with pytest.raises(PackConflictError):
            resolve_selected_packs(
                [str(pack_a), str(pack_b)],
                explicit_overrides={PackKind.GATE: {"contract.mode": "all"}},
            )

    def test_override_for_a_different_field_does_not_exempt_this_one(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: public",
        )
        with pytest.raises(PackConflictError):
            resolve_selected_packs(
                [str(pack_a), str(pack_b)],
                explicit_overrides={
                    PackKind.CONTRACT: {"surface.internal_namespaces": ("detail",)}
                },
            )

    def test_no_overrides_argument_preserves_todays_unconditional_conflict(
        self, tmp_path
    ):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="contract",
            assignments_yaml="contract.mode: public",
        )
        with pytest.raises(PackConflictError):
            resolve_selected_packs([str(pack_a), str(pack_b)])


class TestResolvePolicyPackOverrides:
    """``resolve_policy_pack_overrides`` -- Phase 1's remaining gap: pack
    *selection* (``resolve_selected_packs``) resolves which packs apply but
    never folds their content into ``CompatibilityPolicyConfig.overrides``."""

    def test_no_pack_paths_returns_empty_mapping(self):
        assert resolve_policy_pack_overrides([]) == {}

    def test_one_policy_pack_folds_its_assignments(self, tmp_path):
        pack = _write_pack(
            tmp_path / "p.yml",
            pack_id="qt_kde_cpp",
            kind="policy",
            assignments_yaml="func_removed: break\nvar_removed: risk",
        )
        overrides = resolve_policy_pack_overrides([str(pack)])
        assert overrides == {
            "func_removed": Verdict.BREAKING,
            "var_removed": Verdict.COMPATIBLE_WITH_RISK,
        }

    def test_non_policy_packs_in_the_same_list_are_ignored(self, tmp_path):
        policy_pack = _write_pack(
            tmp_path / "policy.yml",
            pack_id="qt_kde_cpp",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        contract_pack = _write_pack(
            tmp_path / "contract.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments_yaml="contract.mode: exports",
        )
        overrides = resolve_policy_pack_overrides(
            [str(policy_pack), str(contract_pack)]
        )
        assert overrides == {"func_removed": Verdict.BREAKING}

    def test_two_packs_agreeing_on_the_same_kind_merge_cleanly(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="policy",
            assignments_yaml="func_removed: break\nvar_removed: risk",
        )
        overrides = resolve_policy_pack_overrides([str(pack_a), str(pack_b)])
        assert overrides == {
            "func_removed": Verdict.BREAKING,
            "var_removed": Verdict.COMPATIBLE_WITH_RISK,
        }

    def test_two_packs_disagreeing_on_the_same_kind_raises(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="policy",
            assignments_yaml="func_removed: warn",
        )
        with pytest.raises(PackConflictError):
            resolve_policy_pack_overrides([str(pack_a), str(pack_b)])

    def test_explicit_override_resolves_a_disagreement_and_wins(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="policy",
            assignments_yaml="func_removed: warn",
        )
        overrides = resolve_policy_pack_overrides(
            [str(pack_a), str(pack_b)],
            explicit_overrides={"func_removed": Verdict.COMPATIBLE_WITH_RISK},
        )
        assert overrides == {"func_removed": Verdict.COMPATIBLE_WITH_RISK}

    def test_explicit_override_wins_even_without_any_pack_disagreement(
        self, tmp_path
    ):
        pack = _write_pack(
            tmp_path / "p.yml",
            pack_id="pack_a",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        overrides = resolve_policy_pack_overrides(
            [str(pack)], explicit_overrides={"func_removed": Verdict.COMPATIBLE}
        )
        assert overrides == {"func_removed": Verdict.COMPATIBLE}

    def test_pack_order_does_not_affect_the_resolved_value(self, tmp_path):
        pack_a = _write_pack(
            tmp_path / "a.yml",
            pack_id="pack_a",
            kind="policy",
            assignments_yaml="func_removed: break",
        )
        pack_b = _write_pack(
            tmp_path / "b.yml",
            pack_id="pack_b",
            kind="policy",
            assignments_yaml="var_removed: risk",
        )
        forward = resolve_policy_pack_overrides([str(pack_a), str(pack_b)])
        backward = resolve_policy_pack_overrides([str(pack_b), str(pack_a)])
        assert forward == backward

    def test_malformed_manifest_raises_pack_manifest_error(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("not: a valid pack manifest\n")
        with pytest.raises(PackManifestError):
            resolve_policy_pack_overrides([str(bad)])
