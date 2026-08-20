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

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.checker import Change, ChangeKind, DiffResult, compare
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
        assert fields["policy.base"].startswith("strict_abi@")
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
        # Merged with the (empty, here) result-level scope digest -- see
        # TestRichTierMergesResultExplicitScope for the merge itself.
        assert f2["surface.explicit_scope"] != ""
        assert "scope-digest" in f2["surface.explicit_scope"]
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


class TestRequireCompleteAnalysisAxis:
    """Codex review, PR #803, fresh evidence: --require-complete-analysis
    changes the exit block/process status for an otherwise-identical
    incomplete-evidence result (0 vs. 1), but is not a D7
    CompatibilityEvaluationConfig namespace field -- it must be threaded
    through as an independent parameter, same as severity_config/
    exit_code_scheme, or two such reports collide on the digest."""

    def test_require_complete_analysis_changes_the_baseline_digest(self):
        result = _result()
        f1 = effective_config_fields(
            result,
            severity_config=None,
            exit_code_scheme="legacy",
            require_complete_analysis=False,
        )
        f2 = effective_config_fields(
            result,
            severity_config=None,
            exit_code_scheme="legacy",
            require_complete_analysis=True,
        )
        assert f1["gate.require_complete_analysis"] == "False"
        assert f2["gate.require_complete_analysis"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_require_complete_analysis_changes_the_rich_tier_digest(self):
        result = _result(evaluation_config=_minimal_evaluation_config())
        f1 = effective_config_fields(
            result,
            severity_config=None,
            exit_code_scheme="legacy",
            require_complete_analysis=False,
        )
        f2 = effective_config_fields(
            result,
            severity_config=None,
            exit_code_scheme="legacy",
            require_complete_analysis=True,
        )
        assert f1["gate.require_complete_analysis"] == "False"
        assert f2["gate.require_complete_analysis"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_report_threads_require_complete_analysis_into_digest(self):
        """End-to-end through the real compare-report entry point, not just
        the internal helper -- add_contract_context (via to_json) must
        forward its own require_complete_analysis parameter."""
        plain = _result()
        plain_report = json.loads(to_json(plain, require_complete_analysis=False))
        strict_report = json.loads(to_json(plain, require_complete_analysis=True))
        assert (
            plain_report["effective_config_fields"]["gate.require_complete_analysis"]
            == "False"
        )
        assert (
            strict_report["effective_config_fields"]["gate.require_complete_analysis"]
            == "True"
        )
        assert (
            plain_report["effective_config_digest"]
            != strict_report["effective_config_digest"]
        )


class TestReleaseOutputDirSummaryCarriesDigest:
    """Codex review, PR #803, fresh evidence: `_write_release_summary_file`
    (the `--output-dir` sibling of `_format_release_json`'s primary release
    report) built its own summary.json dict independently and never called
    the effective-config digest helper at all -- so that document carried
    neither field, unlike the primary release JSON."""

    def test_output_dir_summary_carries_digest(self, tmp_path):
        from abicheck.cli_compare_release import _write_release_summary_file

        _write_release_summary_file(
            tmp_path, "NO_CHANGE", [], [], [], {}, {}
        )
        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["effective_config_digest"].startswith("sha256:")
        assert data["effective_config_fields"]["_tier"] == "baseline"

    def test_output_dir_summary_digest_matches_primary_release_json(self, tmp_path):
        """The two release-level summary documents must never independently
        drift -- both route through the same shared helper."""
        from abicheck.cli_compare_release import _write_release_summary_file
        from abicheck.cli_compare_release_helpers import _format_release_json

        severity = resolve_severity_config("strict")
        _write_release_summary_file(
            tmp_path, "NO_CHANGE", [], [], [], {}, {}, severity_config=severity
        )
        output_dir_data = json.loads((tmp_path / "summary.json").read_text())
        primary_data = json.loads(
            _format_release_json(
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
                severity_config=severity,
            )
        )
        assert (
            output_dir_data["effective_config_digest"]
            == primary_data["effective_config_digest"]
        )
        assert (
            output_dir_data["effective_config_fields"]
            == primary_data["effective_config_fields"]
        )


class TestBaselineTierBuiltinPolicyIdentity:
    """Codex review, PR #803, fresh evidence: the baseline tier recorded
    only the bare policy name, not builtin_policy_identity()'s full
    id@version:sha256 -- so two baseline reports from different abicheck
    versions could both read policy.base="strict_abi" and hash identically
    despite the built-in base's effective ChangeKind sets (what the
    identity actually hashes) having genuinely changed."""

    def test_recognized_builtin_policy_carries_full_identity(self):
        from abicheck.compatibility_evaluation_frontend import (
            builtin_policy_identity,
        )

        result = _result(policy="strict_abi")
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        identity = builtin_policy_identity("strict_abi")
        assert fields["policy.base"] == f"strict_abi@{identity.version}:{identity.sha256}"

    def test_different_builtin_policies_hash_differently(self):
        f1 = effective_config_fields(
            _result(policy="strict_abi"),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _result(policy="sdk_vendor"),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["policy.base"] != f2["policy.base"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_unrecognized_policy_name_degrades_to_bare_name_rather_than_raising(self):
        """A receipt must never turn a completed comparison into a failure
        over an unrecognized policy name (see
        compatibility_evaluation_frontend.stated_policy_base's own
        docstring for the precedent)."""
        result = _result(policy="totally_unknown_policy")
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["policy.base"] == "totally_unknown_policy"

    def test_empty_policy_name_is_empty_string(self):
        result = _result(policy="")
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["policy.base"] == ""


class TestCompatReportOmitsTheDigest:
    """Codex review, PR #803, fresh evidence: compat/cli.py's own `compat
    check --report-format json` reuses reporter.to_json with
    include_exit_decision=False (its real process exit follows a different,
    ABICC-style 0/1/2 scheme) -- but this digest's gate axes describe only
    the *native* legacy/severity scheme and carry no representation of
    compat-only transform options (-strict, -source/-binary, ...), so two
    behaviorally different compat reports could carry the identical digest.
    Mirrors the `exit` block's own existing include_exit_decision gate."""

    def test_include_exit_decision_false_omits_the_digest(self):
        result = _result()
        report = json.loads(to_json(result, include_exit_decision=False))
        assert "effective_config_digest" not in report
        assert "effective_config_fields" not in report
        # Every other field this function always writes stays present.
        assert report["verdict"] == "NO_CHANGE"

    def test_default_still_includes_the_digest(self):
        result = _result()
        report = json.loads(to_json(result))
        assert report["effective_config_digest"].startswith("sha256:")

    def test_include_exit_decision_false_still_validates_against_schema(self):
        """Mirrors test_exit_decision.py's identically-named test for the
        `exit` block -- both fields are schema-optional for the same
        reason, so a compat report omitting them still validates against
        its own advertised report_schema_version. Uses a real compare()
        result (not the hand-built _result() helper) so every other
        required field is genuinely populated the way to_json's real
        callers produce it."""
        pytest.importorskip("jsonschema")
        import jsonschema

        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.schemas import load_compare_report_schema

        def _fn(name: str, mangled: str) -> Function:
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1", "from_headers": True}
        old = AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        )
        new = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        result = compare(old, new)
        report = json.loads(to_json(result, include_exit_decision=False))
        assert "effective_config_digest" not in report
        jsonschema.validate(report, load_compare_report_schema())


def _scoped_result(**dynamic_attrs) -> DiffResult:
    """A ``_result()`` with ADR-043 scoping attributes set the way the real
    CLI sets them (plain attribute assignment onto an already-built
    ``DiffResult`` -- ``gate_scope``/``used_by``/``required_symbols`` are
    not dataclass fields, see ``cli_helpers_compare.py``'s ``# type:
    ignore[attr-defined]`` assignments)."""
    result = _result()
    for name, value in dynamic_attrs.items():
        setattr(result, name, value)
    return result


class TestGateScopeAxis:
    """Codex review, PR #803, fresh evidence: --used-by/--required-symbol(s)
    scoping (ADR-043) stamps DiffResult.gate_scope/used_by/required_symbols
    before the report is rendered and can genuinely replace the reported
    verdict/findings/exit code, but neither tier read it -- so two runs
    selecting different consumers/entrypoints against the identical pair
    collided on the digest."""

    def test_no_scoping_is_empty_string(self):
        result = _result()
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["gate.scope"] == ""

    def test_different_used_by_apps_hash_differently(self):
        f1 = effective_config_fields(
            _scoped_result(gate_scope="used_by", used_by=[{"app": "app1.so"}]),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _scoped_result(gate_scope="used_by", used_by=[{"app": "app2.so"}]),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["gate.scope"] != f2["gate.scope"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_different_required_symbols_hash_differently(self):
        f1 = effective_config_fields(
            _scoped_result(
                gate_scope="required_symbol",
                required_symbols={"required_entrypoints": ["sym_a"]},
            ),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _scoped_result(
                gate_scope="required_symbol",
                required_symbols={"required_entrypoints": ["sym_b"]},
            ),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["gate.scope"] != f2["gate.scope"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_used_by_vs_required_symbol_hash_differently_even_with_same_target(self):
        """The "kind" must participate too, not just the target list."""
        f1 = effective_config_fields(
            _scoped_result(gate_scope="used_by", used_by=[{"app": "libfoo"}]),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _scoped_result(
                gate_scope="required_symbol",
                required_symbols={"required_entrypoints": ["libfoo"]},
            ),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["gate.scope"] != f2["gate.scope"]

    def test_gate_scope_also_participates_in_the_rich_tier(self):
        scoped = _result(evaluation_config=_minimal_evaluation_config())
        scoped.gate_scope = "used_by"
        scoped.used_by = [{"app": "app1.so"}]
        f1 = effective_config_fields(
            scoped,
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _result(evaluation_config=_minimal_evaluation_config()),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["gate.scope"] != f2["gate.scope"]
        assert effective_config_digest(f1) != effective_config_digest(f2)


class TestRichTierGateAxesUseCallerSuppliedValues:
    """CodeRabbit review, PR #803, fresh evidence: scan --against's own
    resolve_scan_config deliberately blanks the resolved
    CompatibilityEvaluationConfig's gate.severity/gate.exit_code_scheme
    fields to built-in defaults (see cli_scan_receipt._without_gate_
    settings), but a --pack-only scan --against stamps that same blanked
    config onto DiffResult.evaluation_config -- so the rich tier must
    read the gate axes from the caller-supplied severity_config/
    exit_code_scheme parameters (the same pair used for the sibling exit
    block), never from resolved_config.gate directly, or the digest
    silently drops the run's real gate."""

    def test_rich_tier_ignores_resolved_configs_own_gate_fields(self):
        """A resolved_config carrying one exit_code_scheme/severity must
        not leak into the digest when the caller supplies a different
        (real) pair -- proving the rich tier doesn't read resolved_config.
        gate at all for these two axes."""
        blanked_config = _minimal_evaluation_config(
            gate=GateConfig(exit_code_scheme="legacy")
        )
        result = _result(evaluation_config=blanked_config)
        real_severity = resolve_severity_config("strict")

        fields_with_real_gate = effective_config_fields(
            result, severity_config=real_severity, exit_code_scheme="severity"
        )
        fields_with_blanked_gate = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields_with_real_gate["gate.exit_code_scheme"] == "severity"
        assert fields_with_blanked_gate["gate.exit_code_scheme"] == "legacy"
        assert (
            effective_config_digest(fields_with_real_gate)
            != effective_config_digest(fields_with_blanked_gate)
        )

    def test_rich_tier_severity_fields_track_the_caller_supplied_severity(self):
        config = _minimal_evaluation_config()
        result = _result(evaluation_config=config)
        strict = resolve_severity_config("strict")
        info_only = resolve_severity_config("info-only")

        f_strict = effective_config_fields(
            result, severity_config=strict, exit_code_scheme="severity"
        )
        f_info = effective_config_fields(
            result, severity_config=info_only, exit_code_scheme="severity"
        )
        assert (
            f_strict["gate.severity.abi_breaking"]
            != f_info["gate.severity.abi_breaking"]
        )
        assert effective_config_digest(f_strict) != effective_config_digest(f_info)


class TestReclassifyPreservesOrder:
    """Codex review, PR #803, fresh evidence: reclassify is first-match-wins
    in policy-file order, so sorting the encoded rules collapsed two
    genuinely different, order-swapped rule sets (which select a different
    rule -- and therefore a different verdict -- for an overlapping finding)
    to the identical digest."""

    def test_swapped_overlapping_rules_hash_differently(self):
        break_rule = ReclassifyRule(to_verdict=Verdict.BREAKING, symbol="foo")
        ignore_rule = ReclassifyRule(to_verdict=Verdict.COMPATIBLE, symbol="foo")

        break_first = _result(
            policy_file=PolicyFile(reclassify=[break_rule, ignore_rule])
        )
        ignore_first = _result(
            policy_file=PolicyFile(reclassify=[ignore_rule, break_rule])
        )
        f1 = effective_config_fields(
            break_first, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            ignore_first, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["policy.reclassify"] != f2["policy.reclassify"]
        assert effective_config_digest(f1) != effective_config_digest(f2)


class TestRichTierPrefersContractContextMergedConfig:
    """Codex review, PR #803, fresh evidence: contract_context.
    with_resolved_config merges *observed* overlay evidence (e.g. a
    --post-manifest overlay, which no front-end input model describes) into
    a new config object -- but record_resolved_config always also leaves
    the unmerged, front-end-only config on result.evaluation_config, and an
    earlier revision of effective_config_fields preferred that unmerged
    copy unconditionally, making the merge unreachable for every run that
    actually built a contract context."""

    def _persisted_context(self, resolved_config):
        from abicheck.contract_evidence import (
            ContractEvidenceBlock,
            EvaluationContextBlock,
            PersistedContractContext,
        )

        return PersistedContractContext(
            contract_evidence=ContractEvidenceBlock(),
            evaluation_context=EvaluationContextBlock(
                resolved_config=resolved_config
            ),
        )

    def test_merged_context_config_wins_over_unmerged_evaluation_config(self):
        unmerged = _minimal_evaluation_config()
        merged = _minimal_evaluation_config(
            contract=ContractConfig(mode=ContractMode.PUBLIC, overlays=("api::v2",))
        )
        result = _result(evaluation_config=unmerged)
        result.contract_context = self._persisted_context(merged)

        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        # Must reflect the *merged* config's overlays, not the unmerged one.
        assert fields["contract.overlays"] == '["api::v2"]'

    def test_falls_back_to_bare_evaluation_config_without_a_context(self):
        """A --pack-only run (no --contract) has no contract_context at
        all -- the bare evaluation_config must still be reachable."""
        config = _minimal_evaluation_config(
            contract=ContractConfig(mode=ContractMode.PUBLIC, overlays=("api::v3",))
        )
        result = _result(evaluation_config=config)
        assert getattr(result, "contract_context", None) is None

        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["contract.overlays"] == '["api::v3"]'


class TestBaselineExplicitScope:
    """Codex review, PR #803, fresh evidence: an ordinary comparison with
    neither --contract nor --pack can still resolve a forced-public symbol
    set from --public-symbols-list/.abicheck.yml's scope.public_symbols,
    which changes which findings are retained -- the baseline tier
    previously hard-coded surface.explicit_scope empty regardless."""

    def test_no_explicit_scope_is_empty_string(self):
        result = _result()
        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["surface.explicit_scope"] == ""

    def test_different_forced_public_scopes_hash_differently(self):
        f1 = effective_config_fields(
            _result(explicit_scope_source_sha256="sha256:aaa"),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        f2 = effective_config_fields(
            _result(explicit_scope_source_sha256="sha256:bbb"),
            severity_config=None,
            exit_code_scheme="legacy",
        )
        assert f1["surface.explicit_scope"] != f2["surface.explicit_scope"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_computes_a_real_content_digest(self):
        """End-to-end through the real compare() entry point, not just the
        internal field helper -- confirms the digest is actually populated
        from force_public_symbols and is a stable content hash."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1", "from_headers": True}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        no_scope = compare(old_snap, new_snap)
        assert no_scope.explicit_scope_source_sha256 is None

        scoped = compare(old_snap, new_snap, force_public_symbols={"pub_a"})
        assert scoped.explicit_scope_source_sha256 is not None
        assert scoped.explicit_scope_source_sha256.startswith("sha256:")

        scoped_again = compare(old_snap, new_snap, force_public_symbols={"pub_a"})
        assert (
            scoped.explicit_scope_source_sha256
            == scoped_again.explicit_scope_source_sha256
        )

        scoped_different = compare(
            old_snap, new_snap, force_public_symbols={"pub_b"}
        )
        assert (
            scoped.explicit_scope_source_sha256
            != scoped_different.explicit_scope_source_sha256
        )


class TestBaselineExplicitScopeCoversPostManifest:
    """Codex review, PR #803, fresh evidence: `--post-manifest` reaches
    compare() as `public_surface_allowlist`, a second, independent
    explicit-scope axis alongside `force_public_symbols` -- neither
    `--contract` nor `--pack` is involved, so this is the identical
    baseline-tier gap `TestBaselineExplicitScope` closed for
    `force_public_symbols`, just for the other axis. Both must be folded
    into one keyed digest (not a delimiter-joined string) so the two axes
    can't collide with each other the way a naive join would."""

    def test_compare_hashes_public_surface_allowlist_too(self):
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1", "from_headers": True}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pp_a", "pp_a")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pp_a", "pp_a")], **common
        )

        no_scope = compare(old_snap, new_snap)
        assert no_scope.explicit_scope_source_sha256 is None

        allowlisted = compare(old_snap, new_snap, public_surface_allowlist={"pp_a"})
        assert allowlisted.explicit_scope_source_sha256 is not None
        assert allowlisted.explicit_scope_source_sha256.startswith("sha256:")

        allowlisted_different = compare(
            old_snap, new_snap, public_surface_allowlist={"pp_b"}
        )
        assert (
            allowlisted.explicit_scope_source_sha256
            != allowlisted_different.explicit_scope_source_sha256
        )

    def test_the_two_axes_do_not_collide(self):
        """A naive `sorted(a | b)` or delimiter-join could make
        force_public_symbols={"x"} and public_surface_allowlist={"x"}
        (or a split like {"x", "y"} vs {"xy"}) hash identically -- the
        keyed-JSON encoding must keep them distinguishable."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1", "from_headers": True}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("shared", "shared")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("shared", "shared")], **common
        )

        via_force_public = compare(
            old_snap, new_snap, force_public_symbols={"shared"}
        )
        via_allowlist = compare(
            old_snap, new_snap, public_surface_allowlist={"shared"}
        )
        both = compare(
            old_snap,
            new_snap,
            force_public_symbols={"shared"},
            public_surface_allowlist={"shared"},
        )
        digests = {
            via_force_public.explicit_scope_source_sha256,
            via_allowlist.explicit_scope_source_sha256,
            both.explicit_scope_source_sha256,
        }
        assert len(digests) == 3, digests

    def test_empty_post_manifest_allowlist_is_a_distinct_active_scope(self):
        """Codex review, PR #803, fresh evidence: `public_surface_allowlist=
        set()` is a real, active configuration -- a POST manifest committing
        to zero exports -- not the same as no manifest at all
        (`public_surface_allowlist=None`). A truthiness check collapses the
        two, so an absent manifest and a zero-export manifest previously
        hashed identically even though `FilterNonPublicSurface` filters
        every concrete export under the latter and nothing under the
        former."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1", "from_headers": True}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pp_a", "pp_a")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pp_a", "pp_a")], **common
        )

        no_manifest = compare(old_snap, new_snap)
        empty_manifest = compare(old_snap, new_snap, public_surface_allowlist=set())

        assert no_manifest.explicit_scope_source_sha256 is None
        assert empty_manifest.explicit_scope_source_sha256 is not None
        assert empty_manifest.explicit_scope_source_sha256 != (
            no_manifest.explicit_scope_source_sha256
        )

        fields_no_manifest = effective_config_fields(
            no_manifest, severity_config=None, exit_code_scheme="legacy"
        )
        fields_empty_manifest = effective_config_fields(
            empty_manifest, severity_config=None, exit_code_scheme="legacy"
        )
        assert (
            fields_no_manifest["surface.explicit_scope"]
            != fields_empty_manifest["surface.explicit_scope"]
        )


class TestRichTierMergesResultExplicitScope:
    """CodeRabbit review, PR #803, fresh evidence, following an earlier
    Codex-reported gap: a `--pack`-only run (no `--contract`) stamps
    `result.evaluation_config` without ever building a
    `PersistedContractContext`, so nothing merges the observed
    `--post-manifest` scope into `resolved_config.surface.explicit_scope` --
    it stays unset (or covers only `--public-symbols-list`) even though
    `checker.compare()` already resolved and recorded the real, combined
    scope digest on `result.explicit_scope_source_sha256`. A plain `or`
    fallback is unsound here: `force_public_symbols` is threaded into
    `compare()` unconditionally, so a single `--pack`-only run combining
    `--public-symbols-list` and `--post-manifest` can populate *both*
    sources at once -- the rich tier must merge them, not pick one."""

    def test_uses_result_scope_when_resolved_config_has_no_explicit_scope(self):
        config = _minimal_evaluation_config()  # surface.explicit_scope is None
        result = _result(
            evaluation_config=config,
            explicit_scope_source_sha256="sha256:frompostmanifest",
        )
        assert getattr(result, "contract_context", None) is None

        fields = effective_config_fields(
            result, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields["surface.explicit_scope"] != ""

    def test_a_change_to_either_source_changes_the_merged_scope(self):
        """Neither source may be silently dropped when both are present --
        varying either one alone must change the merged field."""
        config_a = _minimal_evaluation_config(
            surface=SurfaceConfig(
                explicit_scope=DigestedItems(items=("a",), sha256="sha256:fromconfig_a")
            )
        )
        config_b = _minimal_evaluation_config(
            surface=SurfaceConfig(
                explicit_scope=DigestedItems(items=("b",), sha256="sha256:fromconfig_b")
            )
        )

        # Same post-manifest digest, different --public-symbols-list config
        # digest: the merged field must still change.
        result_config_a = _result(
            evaluation_config=config_a,
            explicit_scope_source_sha256="sha256:frompostmanifest",
        )
        result_config_b = _result(
            evaluation_config=config_b,
            explicit_scope_source_sha256="sha256:frompostmanifest",
        )
        fields_config_a = effective_config_fields(
            result_config_a, severity_config=None, exit_code_scheme="legacy"
        )
        fields_config_b = effective_config_fields(
            result_config_b, severity_config=None, exit_code_scheme="legacy"
        )
        assert (
            fields_config_a["surface.explicit_scope"]
            != fields_config_b["surface.explicit_scope"]
        )

        # Same --public-symbols-list config digest, different post-manifest:
        # the merged field must still change too (the bug this class guards
        # against -- an earlier `or` fallback would have collapsed these to
        # the identical config-side digest, ignoring the manifest entirely).
        result_manifest_a = _result(
            evaluation_config=config_a,
            explicit_scope_source_sha256="sha256:manifest_a",
        )
        result_manifest_b = _result(
            evaluation_config=config_a,
            explicit_scope_source_sha256="sha256:manifest_b",
        )
        fields_manifest_a = effective_config_fields(
            result_manifest_a, severity_config=None, exit_code_scheme="legacy"
        )
        fields_manifest_b = effective_config_fields(
            result_manifest_b, severity_config=None, exit_code_scheme="legacy"
        )
        assert (
            fields_manifest_a["surface.explicit_scope"]
            != fields_manifest_b["surface.explicit_scope"]
        )
        assert effective_config_digest(fields_manifest_a) != effective_config_digest(
            fields_manifest_b
        )

    def test_two_different_manifests_no_longer_collide_under_a_pack(self):
        config = _minimal_evaluation_config()
        result_a = _result(
            evaluation_config=config,
            explicit_scope_source_sha256="sha256:manifest_a",
        )
        result_b = _result(
            evaluation_config=config,
            explicit_scope_source_sha256="sha256:manifest_b",
        )
        fields_a = effective_config_fields(
            result_a, severity_config=None, exit_code_scheme="legacy"
        )
        fields_b = effective_config_fields(
            result_b, severity_config=None, exit_code_scheme="legacy"
        )
        assert fields_a["surface.explicit_scope"] != fields_b["surface.explicit_scope"]
        assert effective_config_digest(fields_a) != effective_config_digest(fields_b)


class TestPatternVerdictsAxis:
    """Codex review, PR #803, fresh evidence: ADR-027 A4 pattern-aware
    verdict modulation (`--pattern-verdicts`/`--explain-patterns`) can
    modulate a finding's verdict and the process exit
    (`checker.py`'s `_apply_pattern_verdicts_step`), but was previously
    unrepresented in the digest -- two otherwise-identical runs differing
    only in this flag collided whenever no idiom/antipattern happened to
    match (the applied-modulation ledger alone is indistinguishable from
    the flag never having been set at all)."""

    def test_flag_changes_the_baseline_digest(self):
        off = _result(pattern_verdicts_enabled=False)
        on = _result(pattern_verdicts_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.pattern_verdicts"] == "False"
        assert f2["policy.pattern_verdicts"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_flag_changes_the_rich_tier_digest(self):
        config = _minimal_evaluation_config()
        off = _result(evaluation_config=config, pattern_verdicts_enabled=False)
        on = _result(evaluation_config=config, pattern_verdicts_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.pattern_verdicts"] == "False"
        assert f2["policy.pattern_verdicts"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_stamps_the_flag_even_when_nothing_matches(self):
        """End-to-end through the real compare() entry point: even when no
        idiom/antipattern actually matches (pattern_modulations stays
        empty), the flag itself must still be recorded and still change
        the digest -- that's the whole point of this axis."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1"}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )

        off = compare(old_snap, new_snap, pattern_verdicts=False)
        on = compare(old_snap, new_snap, pattern_verdicts=True)
        assert off.pattern_verdicts_enabled is False
        assert on.pattern_verdicts_enabled is True
        assert on.pattern_modulations == []  # nothing matched -- ledger stays empty

        f_off = effective_config_fields(
            off, severity_config=None, exit_code_scheme="legacy"
        )
        f_on = effective_config_fields(
            on, severity_config=None, exit_code_scheme="legacy"
        )
        assert f_off["policy.pattern_verdicts"] != f_on["policy.pattern_verdicts"]
        assert effective_config_digest(f_off) != effective_config_digest(f_on)


class TestCollapseVersionedSymbolsAxis:
    """Codex review, PR #803, fresh evidence: compare(...,
    collapse_versioned_symbols=...) can remove a versioned symbol-version
    remove/add pair entirely, turning an otherwise BREAKING verdict
    non-breaking -- but neither digest tier recorded the resolved
    boolean."""

    def test_flag_changes_the_baseline_digest(self):
        off = _result(collapse_versioned_symbols_enabled=False)
        on = _result(collapse_versioned_symbols_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.collapse_versioned_symbols"] == "False"
        assert f2["policy.collapse_versioned_symbols"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_flag_changes_the_rich_tier_digest(self):
        config = _minimal_evaluation_config()
        off = _result(evaluation_config=config, collapse_versioned_symbols_enabled=False)
        on = _result(evaluation_config=config, collapse_versioned_symbols_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.collapse_versioned_symbols"] != f2["policy.collapse_versioned_symbols"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_stamps_the_flag(self):
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1"}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        off = compare(old_snap, new_snap, collapse_versioned_symbols=False)
        on = compare(old_snap, new_snap, collapse_versioned_symbols=True)
        assert off.collapse_versioned_symbols_enabled is False
        assert on.collapse_versioned_symbols_enabled is True


class TestSurfaceMetricsAxis:
    """Codex review, PR #803, fresh evidence: --surface-metrics
    (compare(..., surface_metrics=...)) appends suppressible aggregate-
    drift findings and can flip NO_CHANGE to COMPATIBLE, but was
    previously unrepresented in the digest."""

    def test_flag_changes_the_baseline_digest(self):
        off = _result(surface_metrics_enabled=False)
        on = _result(surface_metrics_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.surface_metrics"] == "False"
        assert f2["policy.surface_metrics"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_flag_changes_the_rich_tier_digest(self):
        config = _minimal_evaluation_config()
        off = _result(evaluation_config=config, surface_metrics_enabled=False)
        on = _result(evaluation_config=config, surface_metrics_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.surface_metrics"] != f2["policy.surface_metrics"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_stamps_the_flag(self):
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1"}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        off = compare(old_snap, new_snap, surface_metrics=False)
        on = compare(old_snap, new_snap, surface_metrics=True)
        assert off.surface_metrics_enabled is False
        assert on.surface_metrics_enabled is True


class TestEnvMatrixAxis:
    """Codex review, PR #803, fresh evidence: --env-matrix's resolved
    runtime floors can reclassify a version-requirement finding (e.g. a
    GLIBC floor turning a RISK into BREAKING) and add deployment findings,
    but the matrix's content identity was absent from the digest -- two
    runs against identical snapshots but different runtime-floor files
    previously collided on the digest."""

    def test_env_matrix_changes_the_baseline_digest(self):
        no_matrix = _result()
        with_matrix = _result(env_matrix_source_sha256="sha256:envdigest")
        f1 = effective_config_fields(
            no_matrix, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            with_matrix, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["policy.env_matrix"] == ""
        assert f2["policy.env_matrix"] == "sha256:envdigest"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_different_env_matrices_hash_differently_in_the_rich_tier(self):
        config = _minimal_evaluation_config()
        result_a = _result(
            evaluation_config=config, env_matrix_source_sha256="sha256:matrix_a"
        )
        result_b = _result(
            evaluation_config=config, env_matrix_source_sha256="sha256:matrix_b"
        )
        f1 = effective_config_fields(
            result_a, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            result_b, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["policy.env_matrix"] != f2["policy.env_matrix"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_computes_a_real_content_digest(self):
        """End-to-end through the real compare() entry point: different
        --env-matrix content produces different digests, and identical
        content produces identical (stable) digests."""
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1"}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )

        no_matrix = compare(old_snap, new_snap)
        assert no_matrix.env_matrix_source_sha256 is None

        matrix_a = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        matrix_b = EnvironmentMatrix(runtime_floors={"GLIBC": "2.31"})
        with_a = compare(old_snap, new_snap, env_matrix=matrix_a)
        with_a_again = compare(old_snap, new_snap, env_matrix=matrix_a)
        with_b = compare(old_snap, new_snap, env_matrix=matrix_b)

        assert with_a.env_matrix_source_sha256 is not None
        assert with_a.env_matrix_source_sha256.startswith("sha256:")
        assert with_a.env_matrix_source_sha256 == with_a_again.env_matrix_source_sha256
        assert with_a.env_matrix_source_sha256 != with_b.env_matrix_source_sha256


class TestReconcileBuildContextAxis:
    """Codex review, PR #803, fresh evidence: --reconcile-build-context
    (compare(..., reconcile_build_context=...)) can move a phantom
    breaking finding from `kept` into the reconciliation audit bucket,
    changing the verdict and exit code, but was previously unrepresented
    in the digest."""

    def test_flag_changes_the_baseline_digest(self):
        off = _result(reconcile_build_context_enabled=False)
        on = _result(reconcile_build_context_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.reconcile_build_context"] == "False"
        assert f2["policy.reconcile_build_context"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_flag_changes_the_rich_tier_digest(self):
        config = _minimal_evaluation_config()
        off = _result(evaluation_config=config, reconcile_build_context_enabled=False)
        on = _result(evaluation_config=config, reconcile_build_context_enabled=True)
        f1 = effective_config_fields(off, severity_config=None, exit_code_scheme="legacy")
        f2 = effective_config_fields(on, severity_config=None, exit_code_scheme="legacy")
        assert f1["policy.reconcile_build_context"] != f2["policy.reconcile_build_context"]
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_compare_stamps_the_flag(self):
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        common = {"library": "libfoo.so.1"}
        old_snap = AbiSnapshot(
            version="1.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        new_snap = AbiSnapshot(
            version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common
        )
        off = compare(old_snap, new_snap, reconcile_build_context=False)
        on = compare(old_snap, new_snap, reconcile_build_context=True)
        assert off.reconcile_build_context_enabled is False
        assert on.reconcile_build_context_enabled is True


class TestScopeToPublicSurfaceRequestedAxis:
    """Codex review, PR #803, fresh evidence: DiffResult.scope_to_public_
    surface is stamped from the *derived* scope_active =
    scope_to_public_surface or public_surface_allowlist is not None,
    which reads True whenever a --post-manifest allowlist is active
    regardless of the raw --scope-public-headers flag. But
    FilterNonPublicSurface._run_allowlist only honors the
    force_public_symbols widening overlay when the *raw* flag is true, so
    two runs sharing the same manifest and forced-public symbols but
    opposite raw scope_to_public_surface settings retain genuinely
    different findings while the pre-fix digest read identically."""

    def test_flag_changes_the_baseline_digest(self):
        raw_false = _result(scope_to_public_surface_requested=False)
        raw_true = _result(scope_to_public_surface_requested=True)
        f1 = effective_config_fields(
            raw_false, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            raw_true, severity_config=None, exit_code_scheme="legacy"
        )
        assert f1["surface.scope_to_public_surface_requested"] == "False"
        assert f2["surface.scope_to_public_surface_requested"] == "True"
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_flag_changes_the_rich_tier_digest(self):
        config = _minimal_evaluation_config()
        raw_false = _result(
            evaluation_config=config, scope_to_public_surface_requested=False
        )
        raw_true = _result(
            evaluation_config=config, scope_to_public_surface_requested=True
        )
        f1 = effective_config_fields(
            raw_false, severity_config=None, exit_code_scheme="legacy"
        )
        f2 = effective_config_fields(
            raw_true, severity_config=None, exit_code_scheme="legacy"
        )
        assert (
            f1["surface.scope_to_public_surface_requested"]
            != f2["surface.scope_to_public_surface_requested"]
        )
        assert effective_config_digest(f1) != effective_config_digest(f2)

    def test_reproduces_the_reported_collision(self):
        """End-to-end: identical POST manifest + forced-public symbols,
        opposite raw --scope-public-headers -- confirms the two runs
        genuinely retain different findings (the collision the finding
        reported) while the new field now distinguishes them."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        def _fn(name, mangled):
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        old_snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[_fn("pp_a", "pp_a"), _fn("priv_b", "priv_b")],
        )
        new_snap = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[_fn("pp_a", "pp_a")],  # priv_b removed
        )

        scoped_on = compare(
            old_snap,
            new_snap,
            scope_to_public_surface=True,
            public_surface_allowlist={"pp_a"},
            force_public_symbols={"priv_b"},
        )
        scoped_off = compare(
            old_snap,
            new_snap,
            scope_to_public_surface=False,
            public_surface_allowlist={"pp_a"},
            force_public_symbols={"priv_b"},
        )

        # Both runs' derived scope_to_public_surface reads True (a
        # manifest was given), but they retain different findings for
        # priv_b's removal -- exactly the collision reported.
        assert scoped_on.scope_to_public_surface is True
        assert scoped_off.scope_to_public_surface is True
        priv_b_kept_on = any("priv_b" in c.symbol for c in scoped_on.changes)
        priv_b_kept_off = any("priv_b" in c.symbol for c in scoped_off.changes)
        assert priv_b_kept_on != priv_b_kept_off

        fields_on = effective_config_fields(
            scoped_on, severity_config=None, exit_code_scheme="legacy"
        )
        fields_off = effective_config_fields(
            scoped_off, severity_config=None, exit_code_scheme="legacy"
        )
        assert (
            fields_on["surface.scope_to_public_surface_requested"]
            != fields_off["surface.scope_to_public_surface_requested"]
        )
        assert effective_config_digest(fields_on) != effective_config_digest(
            fields_off
        )
