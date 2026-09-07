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

"""ADR-066 D4/S2: the ``versioning:`` namespace end to end -- ``policy_file.py``'s
YAML parsing, the ADR-049 D7 resolver wiring
(``compatibility_evaluation_wiring.resolve_versioning_policy``), and the
whole-object frontend resolver (``compatibility_evaluation_frontend.
resolve_compatibility_evaluation_config``). Mirrors the existing
``surface.internal_namespaces`` test shapes in
``tests/test_compatibility_evaluation_wiring.py``, the precedent this
namespace's own wiring follows."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.compatibility_evaluation_frontend import (
    ExplicitCompatibilityInputs,
    resolve_compatibility_evaluation_config,
)
from abicheck.compatibility_evaluation_versioning_wiring import (
    VERSIONING_POLICY_FIELD,
    resolve_versioning_policy,
    versioning_policy_candidate,
)
from abicheck.contract_relevance_types import SelectorLayer
from abicheck.errors import PolicyError
from abicheck.policy.versioning_policy import (
    CompatibilityPromise,
    VersioningEnforcement,
    VersioningPolicy,
    VersioningScheme,
    built_in_default_versioning_policy,
)
from abicheck.policy_file import PolicyFile


class TestPolicyFileParsesVersioning:
    def test_no_versioning_key_means_built_in_default(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("base_policy: strict_abi\n")
        pf = PolicyFile.load(path)
        assert pf.versioning_stated is False
        assert pf.versioning == built_in_default_versioning_policy()

    def test_full_versioning_block_parses(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text(
            textwrap.dedent(
                """
                versioning:
                  scheme: relaxed_semver
                  promise: abi_within_major
                  support_window:
                    kind: last_n_minors
                    last_n: 2
                  deprecation_window:
                    min_releases: 1
                  enforcement: block
                """
            )
        )
        pf = PolicyFile.load(path)
        assert pf.versioning_stated is True
        assert pf.versioning.scheme is VersioningScheme.RELAXED_SEMVER
        assert pf.versioning.promise is CompatibilityPromise.ABI_WITHIN_MAJOR
        assert pf.versioning.support_window.kind == "last_n_minors"
        assert pf.versioning.support_window.last_n == 2
        assert pf.versioning.deprecation_window.min_releases == 1
        assert pf.versioning.enforcement is VersioningEnforcement.BLOCK

    def test_partial_versioning_block_falls_back_per_field(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "policy.yml"
        path.write_text(
            textwrap.dedent(
                """
                versioning:
                  enforcement: block
                """
            )
        )
        pf = PolicyFile.load(path)
        assert pf.versioning_stated is True
        assert pf.versioning.enforcement is VersioningEnforcement.BLOCK
        # Everything else falls back to the built-in default.
        assert pf.versioning.scheme is VersioningScheme.STRICT_SEMVER
        assert pf.versioning.promise is CompatibilityPromise.NONE

    def test_unknown_scheme_is_a_hard_load_error(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("versioning:\n  scheme: made_up_scheme\n")
        with pytest.raises(PolicyError):
            PolicyFile.load(path)

    def test_unknown_promise_is_a_hard_load_error(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("versioning:\n  promise: made_up_promise\n")
        with pytest.raises(PolicyError):
            PolicyFile.load(path)

    def test_unknown_enforcement_is_a_hard_load_error(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("versioning:\n  enforcement: made_up\n")
        with pytest.raises(PolicyError):
            PolicyFile.load(path)

    def test_versioning_must_be_a_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("versioning: not_a_mapping\n")
        with pytest.raises(PolicyError):
            PolicyFile.load(path)

    def test_deprecation_window_must_be_a_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yml"
        path.write_text("versioning:\n  deprecation_window: not_a_mapping\n")
        with pytest.raises(PolicyError):
            PolicyFile.load(path)


class TestResolveVersioningPolicy:
    def test_no_policy_file_falls_back_to_built_in_default(self) -> None:
        policy, prov = resolve_versioning_policy(policy_file=None)
        assert policy == built_in_default_versioning_policy()
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_stated_versioning_resolves_to_explicit_cli(self) -> None:
        pf = PolicyFile(
            versioning=VersioningPolicy(enforcement=VersioningEnforcement.BLOCK),
            versioning_stated=True,
        )
        policy, prov = resolve_versioning_policy(policy_file=pf)
        assert policy.enforcement is VersioningEnforcement.BLOCK
        assert prov.layer is SelectorLayer.EXPLICIT_CLI
        assert prov.source_kind == "policy_file"

    def test_unstated_versioning_falls_back_to_default_even_if_populated(
        self,
    ) -> None:
        # A directly-constructed PolicyFile with versioning=... but
        # versioning_stated=False (the dataclass default) never claims a
        # statement it never made -- mirrors internal_namespaces' own rule.
        pf = PolicyFile(
            versioning=VersioningPolicy(enforcement=VersioningEnforcement.BLOCK)
        )
        policy, prov = resolve_versioning_policy(policy_file=pf)
        assert policy == built_in_default_versioning_policy()
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_provenance_records_the_policy_file_source(self) -> None:
        pf = PolicyFile(
            versioning=VersioningPolicy(enforcement=VersioningEnforcement.BLOCK),
            versioning_stated=True,
            source_path=Path("policy.yml"),
        )
        _, prov = resolve_versioning_policy(policy_file=pf)
        assert prov.path == "policy.yml"
        assert prov.selected_by[0].option == "--policy"

    def test_candidate_is_none_without_a_stated_versioning_block(self) -> None:
        assert versioning_policy_candidate(policy_file=None) is None
        assert (
            versioning_policy_candidate(policy_file=PolicyFile(versioning_stated=False))
            is None
        )


class TestFrontendResolvesVersioning:
    def test_default_run_gets_built_in_default_versioning_policy(self) -> None:
        cfg = resolve_compatibility_evaluation_config()
        assert cfg.versioning == built_in_default_versioning_policy()
        assert (
            cfg.provenance[VERSIONING_POLICY_FIELD].layer
            is SelectorLayer.BUILT_IN_DEFAULT
        )

    def test_policy_file_versioning_reaches_the_effective_config(self) -> None:
        pf = PolicyFile(
            versioning=VersioningPolicy(
                promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
                enforcement=VersioningEnforcement.BLOCK,
            ),
            versioning_stated=True,
        )
        cfg = resolve_compatibility_evaluation_config(
            explicit=ExplicitCompatibilityInputs(policy_file=pf)
        )
        assert cfg.versioning.promise is CompatibilityPromise.ABI_WITHIN_MAJOR
        assert cfg.versioning.enforcement is VersioningEnforcement.BLOCK
        assert (
            cfg.provenance[VERSIONING_POLICY_FIELD].layer is SelectorLayer.EXPLICIT_CLI
        )

    def test_no_versioning_key_leaves_config_at_built_in_default(self) -> None:
        # A --policy-file that sets other things (e.g. base_policy) but never
        # mentions versioning: must not accidentally pin anything.
        pf = PolicyFile(base_policy="strict_abi")
        cfg = resolve_compatibility_evaluation_config(
            explicit=ExplicitCompatibilityInputs(policy_file=pf)
        )
        assert cfg.versioning == built_in_default_versioning_policy()
