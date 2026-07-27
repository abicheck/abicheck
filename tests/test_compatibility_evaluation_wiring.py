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

"""ADR-049 Phase 1: tests for the legacy-scope-flag -> contract.mode wiring
and the --policy-file -> surface.internal_namespaces wiring."""

from __future__ import annotations

from abicheck.compatibility_evaluation_wiring import (
    resolve_internal_namespaces,
    resolve_legacy_contract_mode,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.policy_file import PolicyFile


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
    def test_policy_file_with_namespaces_resolves_to_project_config(self):
        pf = PolicyFile(internal_namespaces=["detail", "impl"])
        namespaces, prov = resolve_internal_namespaces(policy_file=pf)
        assert namespaces == ("detail", "impl")
        assert prov.layer is SelectorLayer.PROJECT_CONFIG

    def test_policy_file_with_empty_list_resolves_to_default_layer(self):
        # An explicit empty list is indistinguishable, once parsed, from the
        # key never being set at all -- both must fall through to the
        # built-in default, not be treated as a real PROJECT_CONFIG selection.
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
