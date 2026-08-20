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

"""CLI cleanup phase two, PR B: the effective-configuration digest.

Covers the digest module directly (both tiers), plus the three real report
paths (`compare`'s ``add_contract_context``, ``scan --against``'s
``_baseline_summary``/``_run_baseline_compare``) to confirm they all emit
the same field via the same function -- the "one effective configuration
... shared by compare / release / scan" invariant this module exists for.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.change_registry_types import Verdict
from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    DigestedItems,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    SuppressionConfig,
    SurfaceConfig,
)
from abicheck.contract_relevance_types import ContractMode
from abicheck.effective_config_digest import (
    EFFECTIVE_CONFIG_FIELD_KEYS,
    effective_config_digest,
    effective_config_fields,
)
from abicheck.policy_file import PolicyFile
from abicheck.reclassify import ReclassifyRule
from abicheck.reporter import to_json
from abicheck.severity import resolve_severity_config


def _identity(
    identity_id: str, version: int = 1, sha256: str = "digest"
) -> ImmutableIdentity:
    return ImmutableIdentity(id=identity_id, version=version, sha256=sha256)


def _minimal_evaluation_config(**overrides) -> CompatibilityEvaluationConfig:
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


def _result(**overrides) -> DiffResult:
    base = dict(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        verdict=Verdict.NO_CHANGE,
    )
    base.update(overrides)
    return DiffResult(**base)


class TestEffectiveConfigFields:
    def test_baseline_tier_with_no_configuration(self):
        result = _result()
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["_tier"] == "baseline"
        assert fields["gate.exit_code_scheme"] == "legacy"
        assert fields["policy.overrides"] == ""
        assert fields["surface.internal_namespaces"] == "[]"

    def test_baseline_tier_reads_policy_file_and_severity(self):
        policy_file = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.API_BREAK},
            internal_namespaces=["detail", "impl"],
        )
        result = _result(policy="strict_abi", policy_file=policy_file)
        severity = resolve_severity_config("strict")
        fields = effective_config_fields(
            result, severity_config=severity, exit_code_scheme="severity"
        )
        assert fields["_tier"] == "baseline"
        assert fields["policy.base"] == "strict_abi"
        assert fields["policy.overrides"] == "func_removed=API_BREAK"
        assert fields["surface.internal_namespaces"] == '["detail","impl"]'
        assert fields["gate.exit_code_scheme"] == "severity"
        assert fields["gate.severity.abi_breaking"] == "error"

    def test_all_declared_keys_are_present(self):
        result = _result()
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        for key in EFFECTIVE_CONFIG_FIELD_KEYS:
            assert key in fields, key


class TestEffectiveConfigDigest:
    def test_digest_is_a_sha256_uri(self):
        fields = effective_config_fields(
            _result(), severity_config=None, exit_code_scheme="legacy"
        )
        digest = effective_config_digest(fields)
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_deterministic_for_equal_fields(self):
        a = effective_config_fields(
            _result(), severity_config=None, exit_code_scheme="legacy"
        )
        b = effective_config_fields(
            _result(), severity_config=None, exit_code_scheme="legacy"
        )
        assert effective_config_digest(a) == effective_config_digest(b)

    def test_differs_when_exit_code_scheme_differs(self):
        legacy = effective_config_fields(
            _result(), severity_config=None, exit_code_scheme="legacy"
        )
        severity_cfg = resolve_severity_config("default")
        severity = effective_config_fields(
            _result(), severity_config=severity_cfg, exit_code_scheme="severity"
        )
        assert effective_config_digest(legacy) != effective_config_digest(severity)

    def test_differs_when_policy_overrides_differ(self):
        plain = effective_config_fields(
            _result(), severity_config=None, exit_code_scheme="legacy"
        )
        policy_file = PolicyFile(
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE}
        )
        overridden = effective_config_fields(
            _result(policy_file=policy_file),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert effective_config_digest(plain) != effective_config_digest(overridden)


class TestCompareReportCarriesDigest:
    def test_no_change_report_carries_digest(self):
        d = json.loads(to_json(_result()))
        assert d["effective_config_digest"].startswith("sha256:")
        assert d["effective_config_fields"]["_tier"] == "baseline"

    def test_two_reports_with_same_config_have_the_same_digest(self):
        c = Change(ChangeKind.FUNC_ADDED, "_Z3foov", "added: foo")
        r1 = to_json(_result(verdict=Verdict.COMPATIBLE, changes=[c]))
        r2 = to_json(
            _result(
                verdict=Verdict.COMPATIBLE,
                changes=[c],
                library="a-differently-named-library.so",
            )
        )
        d1, d2 = json.loads(r1), json.loads(r2)
        # Two runs resolving the identical gate/policy configuration produce
        # the identical digest, even though the comparisons themselves
        # (library name, findings) differ -- the digest fingerprints
        # *configuration*, not outcome.
        assert d1["effective_config_digest"] == d2["effective_config_digest"]

    def test_policy_override_changes_the_digest(self):
        plain = json.loads(to_json(_result(verdict=Verdict.COMPATIBLE)))
        policy_file = PolicyFile(
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE}
        )
        overridden = json.loads(
            to_json(_result(verdict=Verdict.COMPATIBLE, policy_file=policy_file))
        )
        assert plain["effective_config_digest"] != overridden["effective_config_digest"]


class TestScanReportCarriesTheSameDigestAsCompare:
    """`scan --against`'s summary must fingerprint identically to `compare`'s
    report for the same resolved configuration -- the whole point of
    computing both through one shared function."""

    def test_scan_and_compare_agree_for_equivalent_configuration(self):
        result = _result(verdict=Verdict.COMPATIBLE)
        compare_fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        # `_run_baseline_compare` calls the identical function with the same
        # (diff, severity_config, exit_code_scheme) shape it resolves its own
        # `exit` block from -- reproduced directly here rather than driving
        # the full scan CLI pipeline, which needs real binaries.
        scan_fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert effective_config_digest(compare_fields) == effective_config_digest(
            scan_fields
        )


class TestRichTierFromEvaluationConfig:
    """`DiffResult.evaluation_config` (Codex review, PR #803): a `--pack`-only
    run resolves a real `CompatibilityEvaluationConfig` even without
    `--contract`, and the digest must use it -- not just fall back to
    `contract_context`, which stays unset in that case."""

    def test_pack_only_run_reaches_the_rich_tier(self):
        config = _minimal_evaluation_config(
            policy=CompatibilityPolicyConfig(
                base=_identity("strict_abi"), packs=(_identity("my_pack", version=2),)
            )
        )
        result = _result(evaluation_config=config)
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["_tier"] == "contract"
        assert "my_pack@2:digest" in fields["packs"]

    def test_a_hand_built_stand_in_does_not_fool_the_rich_tier(self):
        # A test double that merely sets an `evaluation_config` attribute to
        # something that isn't a real `CompatibilityEvaluationConfig` must
        # not be read as one.
        result = _result(evaluation_config="not-a-real-config")
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["_tier"] == "baseline"

    def test_pack_identity_change_changes_the_digest(self):
        base = _result(
            evaluation_config=_minimal_evaluation_config(
                policy=CompatibilityPolicyConfig(
                    base=_identity("strict_abi"), packs=(_identity("p", version=1),)
                )
            )
        )
        bumped = _result(
            evaluation_config=_minimal_evaluation_config(
                policy=CompatibilityPolicyConfig(
                    base=_identity("strict_abi"), packs=(_identity("p", version=2),)
                )
            )
        )
        f1 = effective_config_fields(
            base, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            bumped, severity_config=None, exit_code_scheme="legacy"
        )
        assert effective_config_digest(f1) != effective_config_digest(f2)


class TestAdditionalAxes:
    """Codex/CodeRabbit review, PR #803: axes the first cut of this module
    omitted, each capable of changing what a comparison actually scores
    while leaving the digest unchanged."""

    def test_scope_to_public_surface_changes_the_baseline_digest(self):
        unscoped = _result(scope_to_public_surface=False)
        scoped = _result(scope_to_public_surface=True)
        f1 = effective_config_fields(
            unscoped, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            scoped, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["surface.scope_to_public_surface"] == "False"
        assert f2["surface.scope_to_public_surface"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_active_reclassify_rule_changes_the_baseline_digest(self):
        plain = _result()
        rule = ReclassifyRule(to_verdict=Verdict.COMPATIBLE_WITH_RISK, symbol="foo")
        reclassified = _result(policy_file=PolicyFile(reclassify=[rule]))
        f1 = effective_config_fields(
            plain, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            reclassified, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["policy.reclassify"] == "[]"
        assert f2["policy.reclassify"] != ""
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_suppressions_digest_changes_the_rich_tier_digest(self):
        no_suppressions = _result(
            evaluation_config=_minimal_evaluation_config(suppressions=None)
        )
        with_suppressions = _result(
            evaluation_config=_minimal_evaluation_config(
                suppressions=SuppressionConfig(sha256="suppress-digest")
            )
        )
        f1 = effective_config_fields(
            no_suppressions, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            with_suppressions, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["suppressions"] == ""
        assert f2["suppressions"] == "suppress-digest"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_explicit_scope_digest_changes_the_rich_tier_digest(self):
        no_scope = _result(evaluation_config=_minimal_evaluation_config())
        with_scope = _result(
            evaluation_config=_minimal_evaluation_config(
                surface=SurfaceConfig(
                    explicit_scope=DigestedItems(items=(), sha256="scope-digest")
                )
            )
        )
        f1 = effective_config_fields(
            no_scope, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            with_scope, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["surface.explicit_scope"] == ""
        assert f2["surface.explicit_scope"] == "scope-digest"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_contract_overlays_change_the_rich_tier_digest(self):
        no_overlays = _result(evaluation_config=_minimal_evaluation_config())
        with_overlays = _result(
            evaluation_config=_minimal_evaluation_config(
                contract=ContractConfig(mode=ContractMode.PUBLIC, overlays=("api::v2",))
            )
        )
        f1 = effective_config_fields(
            no_overlays, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            with_overlays, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["contract.overlays"] == "[]"
        assert f2["contract.overlays"] == '["api::v2"]'
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_scope_to_public_surface_changes_the_rich_tier_digest(self):
        """Codex review, PR #803, fresh evidence: an earlier revision hard-
        coded this field empty for the rich tier, so two --contract runs
        differing only in --scope-public-headers collided."""
        unscoped = _result(
            evaluation_config=_minimal_evaluation_config(),
            scope_to_public_surface=False,
        )
        scoped = _result(
            evaluation_config=_minimal_evaluation_config(),
            scope_to_public_surface=True,
        )
        f1 = effective_config_fields(
            unscoped, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            scoped, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["surface.scope_to_public_surface"] == "False"
        assert f2["surface.scope_to_public_surface"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_suppress_file_changes_the_baseline_digest(self):
        """Codex review, PR #803, fresh evidence: an ordinary `compare
        --suppress FILE` (no --contract, no --pack) resolves no
        CompatibilityEvaluationConfig, so the baseline tier's `suppressions`
        field must read DiffResult.suppression_source_sha256 directly."""
        no_suppress = _result()
        with_suppress = _result(suppression_source_sha256="sha256:abc123")
        f1 = effective_config_fields(
            no_suppress, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            with_suppress, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["suppressions"] == ""
        assert f2["suppressions"] == "sha256:abc123"
        assert effective_config_digest(f1) != effective_config_digest(f2)


class TestReleaseSummaryCarriesDigest:
    """Codex review, PR #803, fresh evidence: the release-level *summary*
    JSON (the primary release report, and what `--output-dir` writes) never
    emitted either effective-config field at all -- only the optional
    per-library sidecar files did."""

    def _release_json(self, **kwargs):
        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "NO_CHANGE",
            Path("/o"),
            Path("/n"),
            [],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
            **kwargs,
        )
        return json.loads(out)

    def test_release_summary_carries_digest(self):
        data = self._release_json()
        assert data["effective_config_digest"].startswith("sha256:")
        assert data["effective_config_fields"]["_tier"] == "baseline"

    def test_severity_config_changes_the_release_summary_digest(self):
        plain = self._release_json()
        severity = self._release_json(severity_config=resolve_severity_config("strict"))
        assert plain["effective_config_digest"] != severity["effective_config_digest"]


class TestSuppressionsWithNoSourceDigest:
    """Codex review, PR #803, fresh evidence: a SuppressionList built without
    a source file (the public constructor, ABICC's -skip-symbols lists, or
    SuppressionList.merge(), which drops both inputs' digests even when each
    half came from a file) has no source_sha256, but is still a genuinely
    active, content-distinct rule set -- checker.compare() must fall back to
    a content digest of its rules rather than recording nothing."""

    def test_digest_less_but_active_suppression_list_is_still_hashed(self):
        from abicheck.suppression import Suppression, SuppressionList

        empty = _result(suppression_source_sha256=None)
        rules = SuppressionList([Suppression(symbol="foo")])
        assert rules.source_sha256 is None  # the digest-less shape under test

        from abicheck.contract_context import suppression_config_for

        config = suppression_config_for(rules)
        active = _result(suppression_source_sha256=config.sha256)

        f1 = effective_config_fields(
            empty, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            active, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["suppressions"] == ""
        assert f2["suppressions"] != ""
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_two_distinct_digest_less_rule_sets_hash_differently(self):
        from abicheck.contract_context import suppression_config_for
        from abicheck.suppression import Suppression, SuppressionList

        rules_a = SuppressionList([Suppression(symbol="foo")])
        rules_b = SuppressionList([Suppression(symbol="bar")])
        digest_a = suppression_config_for(rules_a).sha256
        digest_b = suppression_config_for(rules_b).sha256
        assert digest_a != digest_b


class TestPolicyFrozenNamespaces:
    """Codex review, PR #803, fresh evidence: PolicyFile.frozen_namespaces
    exempts findings from override downgrades (checker.py), so two --policy
    documents differing only in a frozen namespace can produce different
    classifications while carrying an identical digest without this field."""

    def test_frozen_namespaces_change_the_baseline_digest(self):
        plain = _result()
        frozen = _result(policy_file=PolicyFile(frozen_namespaces=["detail::impl"]))
        f1 = effective_config_fields(plain, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(
            frozen, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["policy.frozen_namespaces"] == "[]"
        assert f2["policy.frozen_namespaces"] == '["detail::impl"]'
        assert effective_config_digest(f1) != effective_config_digest(f2)
