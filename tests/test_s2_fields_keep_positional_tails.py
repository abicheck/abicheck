# Copyright 2025 abicheck contributors
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
# SPDX-License-Identifier: Apache-2.0
"""ADR-065 S2 added fields to three published/compatibility-path dataclasses.
Each is appended *after* every pre-existing field, so a positional caller
written against the earlier shape keeps binding the older tail instead of
silently feeding it into a new field (Codex review, twenty-second round).
The invariant is stated over ``dataclasses.fields`` order, not one example,
so any later reordering fails here."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from abicheck.bundle_models import BundleDiffResult
from abicheck.policy.exit_decision import ExitDecision
from abicheck.policy_file import PolicyFile
from abicheck.workflows.aggregate.contracts import TargetReport

_S2_EXIT_FIELDS = (
    "incomplete_scope_contribution",
    "no_comparison_completed_contribution",
    # ADR-068 D6 / one-comparison-product.md Phase 8: appended after the S2
    # fields for the identical reason -- a positional caller written before
    # this field existed keeps binding the older tail rather than silently
    # feeding it into this one.
    "loadability_contribution",
)
_S2_BUNDLE_FIELDS = (
    "scope_record",
    "extraction_failures",
    "not_comparable_members",
    # ADR-068's `.abicheck.yml` `deployment:` env-matrix demotion: appended
    # after the S2 fields for the identical reason -- a positional caller
    # written before this field existed keeps binding the older tail rather
    # than silently feeding it into this one.
    "env_matrix_source_sha256",
)
_S2_TARGET_FIELDS = (
    "scope_completeness_exit",
    "scope_completeness_incomplete",
    # ADR-067 C-S2: appended after the S2 fields for the identical reason --
    # a positional caller written before this field existed keeps binding
    # the older tail rather than silently feeding it into this one.
    "disposition_audit",
    # ADR-068 D2's no-baseline audit shape: appended after `disposition_audit`
    # for the identical reason -- a positional caller written before this
    # field existed keeps binding the older tail rather than silently
    # feeding it into this one.
    "completed_without_compatibility_verdict",
)


def _names(cls: type) -> list[str]:
    return [f.name for f in fields(cls)]


def test_the_s2_fields_are_the_tail_of_each_type() -> None:
    assert _names(ExitDecision)[-len(_S2_EXIT_FIELDS) :] == list(_S2_EXIT_FIELDS)
    # `-len(...)`, not a hardcoded `-3`: the literal count is what made this
    # one assertion fail on a correctly-appended field (`env_matrix_source_
    # sha256`) while its two siblings, already written this way, simply
    # needed their tuple extended. The invariant is "these fields are the
    # tail", never "the tail is exactly three long".
    assert _names(BundleDiffResult)[-len(_S2_BUNDLE_FIELDS) :] == list(
        _S2_BUNDLE_FIELDS
    )
    assert _names(TargetReport)[-len(_S2_TARGET_FIELDS) :] == list(_S2_TARGET_FIELDS)


def test_exit_decision_positional_tail_binds_the_older_fields() -> None:
    # The pre-S2 positional order: ... operational_error, evidence_contract
    # _error, budget_overflow, not_comparable, removed_required_library.
    pre = [f.name for f in fields(ExitDecision) if f.name not in _S2_EXIT_FIELDS]
    values = {"code": 7, "reasons": ()}
    ints = [n for n in pre if n.endswith("_contribution")]
    for i, name in enumerate(ints, start=1):
        values[name] = i
    decision = ExitDecision(*[values[n] for n in pre])
    for name in ints:
        assert getattr(decision, name) == values[name]
    assert decision.incomplete_scope_contribution == 0
    assert decision.no_comparison_completed_contribution == 0
    assert decision.loadability_contribution == 0


def test_bundle_result_seventh_positional_argument_is_still_the_policy_file() -> None:
    policy_file = PolicyFile()
    result = BundleDiffResult(
        Path("/old"), Path("/new"), [], [], "strict_abi", [], policy_file
    )
    assert result.policy_file is policy_file
    assert result.scope_record is None
    assert result.extraction_failures == {}
    assert result.not_comparable_members == {}


def test_target_report_last_pre_s2_positional_argument_is_still_the_digest() -> None:
    from dataclasses import MISSING

    pre = [f for f in fields(TargetReport) if f.name not in _S2_TARGET_FIELDS]
    assert pre[-1].name == "effective_config_digest"
    args: list[object] = []
    for f in pre:
        if f.name == "effective_config_digest":
            args.append("sha256:digest")
        elif f.default is not MISSING:
            args.append(f.default)
        elif f.default_factory is not MISSING:
            args.append(f.default_factory())
        else:
            args.append("x")
    report = TargetReport(*args)
    assert report.effective_config_digest == "sha256:digest"
    assert report.scope_completeness_exit == 0
    assert report.scope_completeness_incomplete is False
