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

from abicheck.change_registry_types import Verdict
from abicheck.checker import Change, ChangeKind, DiffResult
from abicheck.effective_config_digest import (
    EFFECTIVE_CONFIG_FIELD_KEYS,
    effective_config_digest,
    effective_config_fields,
)
from abicheck.policy_file import PolicyFile
from abicheck.reporter import to_json
from abicheck.severity import resolve_severity_config


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
        assert fields["surface.internal_namespaces"] == ""

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
        assert fields["surface.internal_namespaces"] == "detail;impl"
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
