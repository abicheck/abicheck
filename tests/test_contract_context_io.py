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

"""ADR-049 Phase 4: byte/order-independent round-trip of the persisted blocks.

This file covers the *serialization* half of Phase 4's gate; the replay and
re-evaluation procedures are in ``test_contract_replay.py``.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
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
from abicheck.contract_context import build_persisted_context
from abicheck.contract_context_io import (
    persisted_context_from_dict,
    persisted_context_to_dict,
    resolved_config_from_dict,
    resolved_config_to_dict,
)
from abicheck.contract_evidence import ContractEvidenceBlock, EvaluationContextBlock
from abicheck.contract_evidence_collect import collect_contract_evidence
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.severity import SeverityConfig, SeverityLevel
from abicheck.surface import compute_public_surface


def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    widget = RecordType(
        name="Widget",
        kind="struct",
        size_bits=64,
        fields=[TypeField(name="x", type="int")],
    )
    old = AbiSnapshot(
        library="libdemo.so.1",
        version="1",
        functions=[
            Function(
                name="api",
                mangled="api",
                return_type="int",
                params=[Param(name="w", type="Widget *")],
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        types=[widget],
    )
    new = AbiSnapshot(library="libdemo.so.1", version="2", types=[widget])
    return old, new


def _context():
    old, new = _pair()
    result = compare(old, new, contract_evaluation=True)
    assert result.contract_context is not None
    return result.contract_context


class TestPersistedContextRoundTrip:
    def test_round_trip_is_the_identity(self) -> None:
        ctx = _context()
        assert persisted_context_from_dict(persisted_context_to_dict(ctx)) == ctx

    def test_round_trip_survives_real_json(self) -> None:
        """Not just dict-level: a JSON decoder turns every tuple into a list.

        ``TypeGraphSnapshot.edges`` is the field that actually notices --
        an edge decodes as a two-element list, not a tuple.
        """
        ctx = _context()
        decoded = json.loads(json.dumps(persisted_context_to_dict(ctx)))
        assert persisted_context_from_dict(decoded) == ctx

    def test_serialization_is_byte_stable(self) -> None:
        ctx = _context()
        first = json.dumps(persisted_context_to_dict(ctx), sort_keys=True)
        again = json.dumps(
            persisted_context_to_dict(persisted_context_from_dict(json.loads(first))),
            sort_keys=True,
        )
        assert first == again

    def test_serialization_is_order_independent(self) -> None:
        """Two collectors visiting providers in a different order agree.

        Phase 4's gate says *byte*-independent, so comparing the decoded
        objects is not enough -- a consumer diffing two persisted reports
        would otherwise see churn that means nothing.
        """
        old, new = _pair()
        surf_old, surf_new = compute_public_surface(old), compute_public_surface(new)
        forward = collect_contract_evidence(old, new, surf_old, surf_new)
        # Same observations, opposite provider-traversal order: the block
        # canonicalizes on (side, provider, id), so the two must serialize
        # to identical bytes rather than merely to equal objects.
        backward = ContractEvidenceBlock(providers=tuple(reversed(forward.providers)))
        one = build_persisted_context(forward, mode=ContractMode.PUBLIC)
        two = build_persisted_context(backward, mode=ContractMode.PUBLIC)
        assert json.dumps(persisted_context_to_dict(one), sort_keys=True) == json.dumps(
            persisted_context_to_dict(two), sort_keys=True
        )

    def test_version_counters_survive_verbatim(self) -> None:
        """A decoded block keeps the versions it was written with.

        Re-stamping with this build's constants would erase exactly the
        mixed-version evidence D6 requires a reader to act on.
        """
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        raw["contract_evidence"]["schema_version"] = 1
        raw["evaluation_context"]["evaluator_version"] = 1
        decoded = persisted_context_from_dict(raw)
        assert decoded.contract_evidence.schema_version == 1
        assert decoded.evaluation_context.evaluator_version == 1

    def test_reading_does_not_enforce_the_version_ceiling(self) -> None:
        """Inspecting a newer block must be possible; *using* it must not.

        ``contract_replay.load_replayable_context`` is what fails closed --
        keeping the check out of the decoder is what lets a tool report the
        mismatch instead of being unable to read the block at all.
        """
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        raw["evaluation_context"]["evaluator_version"] = 999
        decoded = persisted_context_from_dict(raw)
        assert decoded.evaluation_context.evaluator_version == 999


class TestResolvedConfigRoundTrip:
    def _full_config(self) -> CompatibilityEvaluationConfig:
        identity = ImmutableIdentity(id="strict_abi", version=1, sha256="a" * 8)
        return CompatibilityEvaluationConfig(
            contract=ContractConfig(
                mode=ContractMode.EXPORTS,
                unresolved="warn",
                overlays=("api::",),
                packs=(ImmutableIdentity(id="rust_c_ffi", version=2, sha256="b" * 8),),
            ),
            evidence=EvidenceConfig(
                providers=(
                    EvidenceProviderRequirement(
                        capability="active_ast",
                        required=True,
                        implementation=ImmutableIdentity(
                            id="clang_ast", version=1, sha256="c" * 8
                        ),
                    ),
                ),
                variants=DigestedItems(items=("debug", "release"), sha256="d" * 8),
            ),
            surface=SurfaceConfig(
                explicit_scope=DigestedItems(items=("include/",), sha256="e" * 8),
                internal_namespaces=("detail", "impl"),
            ),
            assurance=AssuranceConfig(require_evidence=False),
            policy=CompatibilityPolicyConfig(base=identity),
            gate=GateConfig(
                exit_code_scheme="legacy",
                preset=ImmutableIdentity(id="default", version=1, sha256="f" * 8),
                severity=SeverityConfig(
                    abi_breaking=SeverityLevel.ERROR,
                    potential_breaking=SeverityLevel.INFO,
                    quality_issues=SeverityLevel.INFO,
                    addition=SeverityLevel.WARNING,
                ),
            ),
            suppressions=SuppressionConfig(sha256="9" * 8, rules=("rule-a",)),
            provenance={
                "contract.mode": ValueProvenance(
                    layer=SelectorLayer.RUN_RECIPE,
                    reference="public-library",
                    version=3,
                    selected_by=(
                        SelectedByEntry(
                            layer=SelectorLayer.EXPLICIT_CLI, option="--recipe"
                        ),
                    ),
                )
            },
        )

    def test_every_namespace_round_trips(self) -> None:
        config = self._full_config()
        decoded = resolved_config_from_dict(
            resolved_config_to_dict(config), config.provenance
        )
        assert decoded == config

    def test_provenance_round_trips_through_the_context_block(self) -> None:
        from abicheck.contract_context_io import (
            evaluation_context_from_dict,
            evaluation_context_to_dict,
        )

        block = EvaluationContextBlock(resolved_config=self._full_config())
        decoded = evaluation_context_from_dict(evaluation_context_to_dict(block))
        assert decoded == block
        assert decoded.field_provenance["contract.mode"].reference == "public-library"

    def test_absent_suppression_source_stays_absent(self) -> None:
        """``None`` and an empty rule list are different facts.

        ``SuppressionConfig`` exists to distinguish "no suppression source
        selected" from "one was selected and resolved to zero rules"; a
        lossy default on read would collapse them.
        """
        config = CompatibilityEvaluationConfig(
            contract=ContractConfig(mode=ContractMode.PUBLIC),
            evidence=EvidenceConfig(),
            surface=SurfaceConfig(),
            assurance=AssuranceConfig(),
            policy=CompatibilityPolicyConfig(
                base=ImmutableIdentity(id="strict_abi", version=1, sha256="a" * 8)
            ),
            gate=GateConfig(),
        )
        decoded = resolved_config_from_dict(resolved_config_to_dict(config))
        assert decoded.suppressions is None
        assert decoded.evidence.variants is None
        assert decoded.surface.explicit_scope is None

    def test_malformed_payload_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            resolved_config_from_dict(["not", "a", "mapping"])


class TestResolvedContextContent:
    """What the persisted context says about the run that produced it."""

    def test_policy_file_overrides_reach_the_persisted_config(self) -> None:
        """A ``--policy-file`` override affected the comparison, so it is recorded.

        Persisting only the base-policy name would make the "resolved"
        configuration wrong in exactly the way an audit consumer would act on
        (Codex review, fresh evidence).
        """
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.policy_file import PolicyFile

        old, new = _pair()
        policy_file = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        result = compare(old, new, contract_evaluation=True, policy_file=policy_file)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert dict(config.policy.overrides) == {
            ChangeKind.FUNC_REMOVED.value: Verdict.COMPATIBLE
        }
        assert "policy.overrides" in config.provenance
        # And it survives the round-trip, like every other resolved field.
        decoded = persisted_context_from_dict(
            persisted_context_to_dict(result.contract_context)
        )
        assert decoded.evaluation_context.resolved_config == config

    def test_a_policy_files_base_names_the_file_not_the_api_argument(self) -> None:
        """``base_policy`` *replaces* the ``policy`` argument, so say so.

        ``checker.compare`` resolves ``effective_policy`` to the policy
        file's own ``base_policy`` whenever one is supplied, ignoring the
        ``policy`` argument entirely -- yet the receipt attributed the
        resolved value to that ignored argument (Codex review, fresh
        evidence).
        """
        from abicheck.policy_file import PolicyFile

        old, new = _pair()
        result = compare(
            old,
            new,
            contract_evaluation=True,
            policy="strict_abi",
            policy_file=PolicyFile(base_policy="sdk_vendor"),
        )
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.policy.base.id == "sdk_vendor"
        assert config.provenance["policy.base"].field_location == "policy_file"
        # ...and with no file, the argument really is what selected it.
        plain = compare(old, new, contract_evaluation=True, policy="sdk_vendor")
        assert plain.contract_context is not None
        assert (
            plain.contract_context.evaluation_context.resolved_config.provenance[
                "policy.base"
            ].field_location
            == "policy"
        )

    def test_policy_file_internal_namespaces_carry_their_provenance(self) -> None:
        """A resolved field with no recorded source is an audit gap.

        ``surface.internal_namespaces`` reached the resolved config from the
        same ``--policy-file`` as ``policy.overrides``, but only the latter
        got a provenance entry (Codex review).
        """
        from abicheck.policy_file import PolicyFile

        old, new = _pair()
        policy_file = PolicyFile(
            base_policy="strict_abi", internal_namespaces=["detail"]
        )
        result = compare(old, new, contract_evaluation=True, policy_file=policy_file)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.surface.internal_namespaces == ("detail",)
        assert "surface.internal_namespaces" in config.provenance

    def test_unstated_internal_namespaces_claim_no_source(self) -> None:
        """With no policy file, nothing selected the value, so claim nothing."""
        old, new = _pair()
        result = compare(old, new, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.surface.internal_namespaces == ()
        assert "surface.internal_namespaces" not in config.provenance

    def test_an_explicitly_empty_namespace_list_keeps_its_provenance(self) -> None:
        """``internal_namespaces: []`` is a stated value, not an absent one.

        Both resolve to ``()``, so the tuple alone cannot tell them apart --
        ``PolicyFile.internal_namespaces_stated`` is forwarded precisely so
        the receipt still records which one this was (CodeRabbit review).
        """
        from abicheck.policy_file import PolicyFile

        old, new = _pair()
        policy_file = PolicyFile(
            base_policy="strict_abi",
            internal_namespaces=[],
            internal_namespaces_stated=True,
        )
        result = compare(old, new, contract_evaluation=True, policy_file=policy_file)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.surface.internal_namespaces == ()
        assert "surface.internal_namespaces" in config.provenance

    def test_suppression_source_reaches_the_persisted_config(self, tmp_path) -> None:
        """A file-loaded suppression list shaped the run, so it is recorded.

        Leaving ``suppressions`` at ``None`` claimed no source was selected
        at all, which an audit consumer would read as "nothing was
        suppressed" (Codex review, fresh evidence).
        """
        from abicheck.suppression import SuppressionList

        rules = tmp_path / "suppress.yaml"
        rules.write_text(
            "version: 1\nsuppressions:\n  - symbol: gone\n    reason: known removal\n",
            encoding="utf-8",
        )
        suppression = SuppressionList.load(rules)
        old, new = _pair()
        result = compare(old, new, suppression=suppression, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.suppressions is not None
        assert config.suppressions.sha256 == suppression.source_sha256
        assert config.suppressions.rules == suppression.rule_identities()
        assert "suppressions" in config.provenance

    def test_no_suppression_source_stays_none(self) -> None:
        """``None`` and "a source that matched nothing" are different facts."""
        old, new = _pair()
        result = compare(old, new, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.suppressions is None

    def test_a_digestless_suppression_list_is_still_a_selected_source(self) -> None:
        """A programmatically built list has no ``source_sha256`` -- and still
        suppresses findings, so recording ``None`` said no source was selected
        (Codex review, fresh evidence).

        ``compat/_helpers.py`` builds every ABICC ``-skip-*`` list this way.
        The digest falls back to a content digest of the rule identities the
        same block persists, which is what the run actually applied.
        """
        from abicheck.suppression import Suppression, SuppressionList

        suppression = SuppressionList([Suppression(symbol="gone")])
        assert suppression.source_sha256 is None
        old, new = _pair()
        result = compare(old, new, suppression=suppression, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.suppressions is not None
        assert config.suppressions.rules == suppression.rule_identities()
        assert config.suppressions.sha256
        assert "suppressions" in config.provenance

    def test_a_merged_suppression_list_keeps_its_rules(self, tmp_path) -> None:
        """``SuppressionList.merge`` drops both halves' digests even when each
        was file-loaded, so the merged list hit the same digest-less path."""
        from abicheck.suppression import Suppression, SuppressionList

        merged = SuppressionList.merge(
            SuppressionList([Suppression(symbol="gone")], source_sha256="a" * 64),
            SuppressionList([Suppression(symbol="other")], source_sha256="b" * 64),
        )
        assert merged.source_sha256 is None
        old, new = _pair()
        result = compare(old, new, suppression=merged, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.suppressions is not None
        assert config.suppressions.rules == merged.rule_identities()
        assert len(config.suppressions.rules) == 2

    def test_explicit_overlays_reach_the_persisted_config(self) -> None:
        """``--public-symbol``/``--post-manifest`` decide membership, so the
        resolved configuration records them (Codex review, fresh evidence).

        ADR-049 D2 counts overlay-selected roots as part of the ``public``
        domain's root set; a context leaving ``contract.overlays`` and
        ``surface.explicit_scope`` empty described a run that never happened.
        """
        old, new = _pair()
        result = compare(
            old,
            new,
            contract_evaluation=True,
            force_public_symbols={"api"},
            public_surface_allowlist={"api", "other"},
        )
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.contract.overlays == ("forced_public_symbols", "post_manifest")
        assert config.surface.explicit_scope is not None
        assert config.surface.explicit_scope.items == ("api", "other")
        assert config.surface.explicit_scope.sha256
        assert "contract.overlays" in config.provenance
        assert "surface.explicit_scope" in config.provenance
        decoded = persisted_context_from_dict(
            persisted_context_to_dict(result.contract_context)
        )
        assert decoded.evaluation_context.resolved_config == config

    def test_an_empty_post_manifest_is_still_a_selected_source(self) -> None:
        """A manifest committing to *no* exports is not an absent manifest.

        ``post_manifest.parse_manifest()`` explicitly supports an empty ABI
        surface, and that allowlist actively scopes every concrete export out
        -- so collapsing it to "no overlay" made the persisted configuration
        describe a run that never happened, and its exclusions cite header
        evidence instead of the manifest that caused them (Codex review,
        fresh evidence).
        """
        old, new = _pair()
        result = compare(
            old, new, contract_evaluation=True, public_surface_allowlist=set()
        )
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.contract.overlays == ("post_manifest",)
        assert config.surface.explicit_scope is not None
        assert config.surface.explicit_scope.items == ()
        assert sorted(
            e.record.side
            for e in result.contract_context.contract_evidence.providers
            if e.record.provider == "post_manifest"
        ) == ["new", "old"]

    def test_no_overlay_leaves_the_explicit_scope_unselected(self) -> None:
        """No overlay is ``None``, not an empty digested list -- the same
        selected-vs-empty distinction ``suppressions`` keeps."""
        old, new = _pair()
        result = compare(old, new, contract_evaluation=True)
        assert result.contract_context is not None
        config = result.contract_context.evaluation_context.resolved_config
        assert config.contract.overlays == ()
        assert config.surface.explicit_scope is None


class TestMalformedPayloads:
    """One failure type for a malformed persisted payload.

    ``persisted_context_from_dict`` reads report JSON a consumer may not
    control, so a caller should not have to catch three unrelated exception
    types to handle a corrupt block (CodeRabbit review).
    """

    def test_missing_required_key_raises_type_error(self) -> None:
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        del raw["evaluation_context"]["resolved_config"]["policy"]["base"]
        with pytest.raises(TypeError, match="required key"):
            persisted_context_from_dict(raw)

    def test_missing_top_level_block_raises_type_error(self) -> None:
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        del raw["contract_evidence"]
        with pytest.raises(TypeError, match="required key"):
            persisted_context_from_dict(raw)

    def test_missing_relevance_map_raises_type_error(self) -> None:
        """A truncated receipt must not read as "recorded no decisions".

        Defaulting the map to ``{}`` made a lost audit block indistinguishable
        from a real, decision-free comparison (Codex review, fresh evidence).
        """
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        del raw["decision_receipt"]["relevance_by_finding"]
        with pytest.raises(TypeError, match="required key"):
            persisted_context_from_dict(raw)

    def test_non_object_block_raises_type_error(self) -> None:
        ctx = _context()
        raw = persisted_context_to_dict(ctx)
        raw["decision_receipt"] = ["not", "a", "mapping"]
        with pytest.raises(TypeError):
            persisted_context_from_dict(raw)
