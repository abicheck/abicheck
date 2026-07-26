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

"""ADR-049 Phase 1 slice 2: tests for the field-level precedence resolver."""

from __future__ import annotations

import pytest

from abicheck.compatibility_evaluation_config import ValueProvenance
from abicheck.compatibility_evaluation_resolver import (
    ConflictingFieldValuesError,
    FieldCandidate,
    LegacyAliasConflictError,
    resolve_field,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer


def _candidate(layer: SelectorLayer, value, **provenance_kwargs) -> FieldCandidate:
    return FieldCandidate(
        provenance=ValueProvenance(layer=layer, **provenance_kwargs), value=value
    )


def _default(value=ContractMode.ALL) -> FieldCandidate:
    return _candidate(SelectorLayer.BUILT_IN_DEFAULT, value)


class TestPrecedenceOrder:
    def test_default_wins_when_nothing_else_present(self):
        value, prov = resolve_field("contract.mode", [], default=_default())
        assert value is ContractMode.ALL
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_project_config_beats_default(self):
        candidates = [_candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC)]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.PROJECT_CONFIG

    def test_run_profile_beats_project_config(self):
        candidates = [
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC),
            _candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.EXPORTS
        assert prov.layer is SelectorLayer.RUN_PROFILE

    def test_run_recipe_beats_run_profile(self):
        candidates = [
            _candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS),
            _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.RUN_RECIPE

    def test_legacy_alias_beats_run_recipe(self):
        candidates = [
            _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC),
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.ALL),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.ALL
        assert prov.layer is SelectorLayer.LEGACY_ALIAS

    def test_explicit_cli_beats_legacy_alias_when_they_agree(self):
        candidates = [
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.ALL),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.ALL),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.ALL
        assert prov.layer is SelectorLayer.EXPLICIT_CLI

    def test_api_request_is_the_same_tier_as_explicit_cli(self):
        candidates = [_candidate(SelectorLayer.API_REQUEST, ContractMode.PUBLIC)]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.API_REQUEST


class TestEquivalentDuplicates:
    # ADR-049 D7: "Equivalent duplicate values are accepted."
    def test_two_equal_candidates_at_the_same_tier_do_not_conflict(self):
        candidates = [
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC, path="a.yml"),
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC, path="b.yml"),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        # Deterministic: the first candidate in input order is of record.
        assert prov.path == "a.yml"


class TestConflictingFieldValues:
    def test_two_different_candidates_at_the_same_tier_raise(self):
        candidates = [
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.ALL),
        ]
        with pytest.raises(ConflictingFieldValuesError) as exc_info:
            resolve_field("contract.mode", candidates, default=_default())
        assert exc_info.value.field_name == "contract.mode"
        assert len(exc_info.value.candidates) == 2

    def test_conflict_in_a_shadowed_lower_tier_still_raises(self):
        # ADR-049 D7's same-layer-conflict rule isn't scoped to only the
        # winning tier: a run_recipe value resolves the field, but the
        # conflicting project_config candidates underneath it must still be
        # caught now, not silently exposed later if run_recipe is removed.
        candidates = [
            _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC),
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.ALL),
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.EXPORTS),
        ]
        with pytest.raises(ConflictingFieldValuesError) as exc_info:
            resolve_field("contract.mode", candidates, default=_default())
        assert exc_info.value.field_name == "contract.mode"
        assert {c.layer for c in exc_info.value.candidates} == {
            SelectorLayer.PROJECT_CONFIG
        }

    def test_equivalent_duplicates_in_a_shadowed_lower_tier_do_not_raise(self):
        # A shadowed tier with equal (not conflicting) values is fine.
        candidates = [
            _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC),
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.ALL),
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.ALL),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.RUN_RECIPE


class TestLegacyAliasConflict:
    def test_disagreement_raises_by_default(self):
        candidates = [
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.ALL),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
        ]
        with pytest.raises(LegacyAliasConflictError) as exc_info:
            resolve_field("contract.mode", candidates, default=_default())
        assert exc_info.value.field_name == "contract.mode"
        assert exc_info.value.explicit.value is ContractMode.PUBLIC
        assert exc_info.value.legacy.value is ContractMode.ALL

    def test_disagreement_is_tolerated_when_agreement_not_required(self):
        # ADR-049 D7: the --policy/--policy-file compatibility exception --
        # --policy-file (explicit_cli) keeps winning over a disagreeing
        # --policy (legacy_alias) input.
        candidates = [
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.ALL),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
        ]
        value, prov = resolve_field(
            "policy.base",
            candidates,
            default=_default(),
            require_legacy_alias_agreement=False,
        )
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.EXPLICIT_CLI

    def test_agreement_never_raises_regardless_of_flag(self):
        candidates = [
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.PUBLIC),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
        ]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.EXPLICIT_CLI


class TestDefaultCandidateValidation:
    def test_default_must_use_built_in_default_layer(self):
        bad_default = _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.ALL)
        with pytest.raises(ValueError, match="BUILT_IN_DEFAULT"):
            resolve_field("contract.mode", [], default=bad_default)


class TestFieldCandidateLayerProperty:
    def test_layer_reads_through_to_provenance(self):
        candidate = _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC)
        assert candidate.layer is SelectorLayer.RUN_RECIPE
