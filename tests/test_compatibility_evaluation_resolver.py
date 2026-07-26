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

from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_config import ImmutableIdentity, ValueProvenance
from abicheck.compatibility_evaluation_resolver import (
    ConflictingFieldValuesError,
    FieldCandidate,
    LegacyAliasConflictError,
    PackConflictError,
    detect_pack_conflicts,
    resolve_field,
)
from abicheck.contract_relevance_types import ContractMode, SelectorLayer


def _pack(id: str, version: int = 1, sha256: str = "test-digest") -> ImmutableIdentity:
    return ImmutableIdentity(id=id, version=version, sha256=sha256)


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


class TestDetectPackConflicts:
    # ADR-049 D8: "Two selected packs that assign incompatible values to the
    # same field or ChangeKind are a usage error until an explicit final
    # override resolves the conflict. Pack order never decides semantics."
    def test_no_packs_is_fine(self):
        assert detect_pack_conflicts([]) is None

    def test_single_pack_never_conflicts_with_itself(self):
        result = detect_pack_conflicts(
            [(_pack("rust_c_ffi"), {"func_removed": Verdict.BREAKING})]
        )
        assert result is None

    def test_two_packs_agreeing_on_a_kind_do_not_conflict(self):
        result = detect_pack_conflicts(
            [
                (_pack("rust_c_ffi"), {"func_removed": Verdict.BREAKING}),
                (_pack("qt_kde_cpp"), {"func_removed": Verdict.BREAKING}),
            ]
        )
        assert result is None

    def test_two_packs_disagreeing_on_a_kind_raises(self):
        with pytest.raises(PackConflictError) as exc_info:
            detect_pack_conflicts(
                [
                    (_pack("rust_c_ffi"), {"func_removed": Verdict.BREAKING}),
                    (_pack("qt_kde_cpp"), {"func_removed": Verdict.COMPATIBLE}),
                ]
            )
        assert exc_info.value.field_name == "func_removed"
        assert len(exc_info.value.contributors) == 2

    def test_conflict_report_is_order_independent(self):
        # "Pack order never decides semantics" -- forward and reversed input
        # both raise on the same kind, deterministically.
        a = (_pack("rust_c_ffi"), {"func_removed": Verdict.BREAKING})
        b = (_pack("qt_kde_cpp"), {"func_removed": Verdict.COMPATIBLE})
        for ordering in ([a, b], [b, a]):
            with pytest.raises(PackConflictError) as exc_info:
                detect_pack_conflicts(ordering)
            assert exc_info.value.field_name == "func_removed"

    def test_explicit_override_resolves_the_conflict(self):
        # D8 composition order: explicit override > selected packs > base
        # policy -- a kind already covered by policy.overrides is exempt.
        result = detect_pack_conflicts(
            [
                (_pack("rust_c_ffi"), {"func_removed": Verdict.BREAKING}),
                (_pack("qt_kde_cpp"), {"func_removed": Verdict.COMPATIBLE}),
            ],
            explicit_overrides={"func_removed": Verdict.API_BREAK},
        )
        assert result is None

    def test_conflict_on_one_kind_does_not_hide_agreement_on_another(self):
        result = detect_pack_conflicts(
            [
                (
                    _pack("rust_c_ffi"),
                    {"func_removed": Verdict.BREAKING, "func_added": Verdict.COMPATIBLE},
                ),
                (
                    _pack("qt_kde_cpp"),
                    {"func_added": Verdict.COMPATIBLE},
                ),
            ]
        )
        assert result is None

    def test_only_the_conflicting_kind_is_reported(self):
        with pytest.raises(PackConflictError) as exc_info:
            detect_pack_conflicts(
                [
                    (
                        _pack("rust_c_ffi"),
                        {
                            "func_removed": Verdict.BREAKING,
                            "func_added": Verdict.COMPATIBLE,
                        },
                    ),
                    (
                        _pack("qt_kde_cpp"),
                        {
                            "func_removed": Verdict.COMPATIBLE,
                            "func_added": Verdict.COMPATIBLE,
                        },
                    ),
                ]
            )
        assert exc_info.value.field_name == "func_removed"

    def test_conflicts_on_non_changekind_fields_are_detected_too(self):
        # D8's rule is "the same field OR ChangeKind" -- a contract/gate
        # pack's own field assignments conflict the same way a policy
        # pack's ChangeKind overrides do; this function doesn't special-case
        # ChangeKind slugs, so any string-keyed field works identically.
        with pytest.raises(PackConflictError) as exc_info:
            detect_pack_conflicts(
                [
                    (_pack("security_hardening"), {"exit_code_scheme": "severity"}),
                    (_pack("release_governance"), {"exit_code_scheme": "legacy"}),
                ]
            )
        assert exc_info.value.field_name == "exit_code_scheme"
        assert len(exc_info.value.contributors) == 2

    def test_explicit_override_resolves_a_non_changekind_conflict_too(self):
        result = detect_pack_conflicts(
            [
                (_pack("security_hardening"), {"exit_code_scheme": "severity"}),
                (_pack("release_governance"), {"exit_code_scheme": "legacy"}),
            ],
            explicit_overrides={"exit_code_scheme": "severity"},
        )
        assert result is None
