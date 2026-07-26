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
        # ADR-049 D7 scopes RUN_PROFILE precedence to execution fields
        # (depth, format, budget, workflow) -- "scan.depth" stands in for one
        # here, with allow_run_profile=True opting the field in. See
        # TestRunProfileFieldScoping for the semantic-field rejection case.
        candidates = [
            _candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC),
            _candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS),
        ]
        value, prov = resolve_field(
            "scan.depth", candidates, default=_default(), allow_run_profile=True
        )
        assert value is ContractMode.EXPORTS
        assert prov.layer is SelectorLayer.RUN_PROFILE

    def test_run_recipe_beats_run_profile(self):
        candidates = [
            _candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS),
            _candidate(SelectorLayer.RUN_RECIPE, ContractMode.PUBLIC),
        ]
        value, prov = resolve_field(
            "scan.depth", candidates, default=_default(), allow_run_profile=True
        )
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

    def test_tolerated_disagreement_preserves_shadowed_legacy_provenance(self):
        # ADR-049 D7: "provenance records the file-selected effective base
        # and the shadowed --policy input" -- the suppressed legacy
        # candidate's own provenance must survive on the winner's record,
        # not just its value being discarded.
        legacy = _candidate(
            SelectorLayer.LEGACY_ALIAS, ContractMode.ALL, path="legacy-policy.yml"
        )
        explicit = _candidate(
            SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC, path="policy-file.yml"
        )
        value, prov = resolve_field(
            "policy.base",
            [legacy, explicit],
            default=_default(),
            require_legacy_alias_agreement=False,
        )
        assert value is ContractMode.PUBLIC
        assert prov.path == "policy-file.yml"
        assert prov.shadowed_legacy is not None
        assert prov.shadowed_legacy.layer is SelectorLayer.LEGACY_ALIAS
        assert prov.shadowed_legacy.path == "legacy-policy.yml"

    def test_agreeing_values_leave_shadowed_legacy_unset(self):
        candidates = [
            _candidate(SelectorLayer.LEGACY_ALIAS, ContractMode.PUBLIC),
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
        ]
        _, prov = resolve_field(
            "policy.base",
            candidates,
            default=_default(),
            require_legacy_alias_agreement=False,
        )
        assert prov.shadowed_legacy is None

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


class TestRunProfileFieldScoping:
    # ADR-049 D7 scopes RUN_PROFILE precedence to "execution fields only"
    # (depth, format, budget, workflow) -- semantic fields like
    # contract.mode/policy.base don't participate. allow_run_profile
    # defaults to False, so a RUN_PROFILE candidate for a field that hasn't
    # opted in is a caller bug, not a legitimate input.
    def test_run_profile_candidate_rejected_by_default(self):
        candidates = [_candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS)]
        with pytest.raises(ValueError, match="execution fields"):
            resolve_field("contract.mode", candidates, default=_default())

    def test_run_profile_candidate_rejected_even_when_shadowed(self):
        # The check runs on every candidate up front, not just the winner --
        # a shadowed RUN_PROFILE candidate is still a caller bug.
        candidates = [
            _candidate(SelectorLayer.EXPLICIT_CLI, ContractMode.PUBLIC),
            _candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS),
        ]
        with pytest.raises(ValueError, match="execution fields"):
            resolve_field("contract.mode", candidates, default=_default())

    def test_run_profile_candidate_allowed_with_opt_in(self):
        candidates = [_candidate(SelectorLayer.RUN_PROFILE, ContractMode.EXPORTS)]
        value, prov = resolve_field(
            "scan.depth", candidates, default=_default(), allow_run_profile=True
        )
        assert value is ContractMode.EXPORTS
        assert prov.layer is SelectorLayer.RUN_PROFILE

    def test_fields_without_run_profile_candidates_are_unaffected_by_default(self):
        # allow_run_profile's default only matters when a RUN_PROFILE
        # candidate is actually present -- everything else resolves as before.
        candidates = [_candidate(SelectorLayer.PROJECT_CONFIG, ContractMode.PUBLIC)]
        value, prov = resolve_field("contract.mode", candidates, default=_default())
        assert value is ContractMode.PUBLIC
        assert prov.layer is SelectorLayer.PROJECT_CONFIG


class TestUnknownSelectorLayer:
    # SelectorLayer is documented as extensible; a new member added there
    # without a matching _PRECEDENCE_TIERS update must fail loudly rather
    # than silently vanish from resolution (BUILT_IN_DEFAULT always matches
    # some tier, so the unknown-layer candidate would otherwise just be
    # dropped with no error).
    class _FutureLayer:
        name = "FUTURE_ADAPTER"
        value = "future_adapter"

    def test_candidate_outside_all_precedence_tiers_raises(self):
        bad_candidate = FieldCandidate(
            provenance=ValueProvenance(layer=self._FutureLayer()),  # type: ignore[arg-type]
            value=ContractMode.PUBLIC,
        )
        with pytest.raises(ValueError, match="not represented in any"):
            resolve_field("contract.mode", [bad_candidate], default=_default())
