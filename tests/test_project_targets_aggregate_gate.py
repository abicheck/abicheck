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

"""Tests for ``buildsource.project_targets``'s ``aggregate: gate:`` block
(CLI cleanup phase two, PR 2 follow-up) -- split out of
``test_project_targets.py`` to keep that file under the AI-readiness
file-size hard cap, the same ``_lib``-adjacent-sibling pattern this repo
already uses for e.g. ``test_project_targets_compile_frontend.py``.

The durable, project-owned home for ``abicheck aggregate``'s
missing-required/unexpected-target gate policy: `project plan` reads this
block and stamps its values onto the generated ``run-plan.json``'s own
``gate`` block, replacing the former ``--gate-missing-required``/
``--gate-unexpected-target`` flags (removed, no CLI alias). See
``tests/test_run_plan.py``'s ``TestRunPlanGenerateCli`` for the
end-to-end CLI coverage of that projection.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.project_targets import (
    AggregateGateSpec,
    ProjectTargetsConfig,
    validate_project_targets,
)

# A minimal, valid single-target config -- enough to exercise
# validate_project_targets() without pulling in test_project_targets.py's
# full PVXS-shaped fixture.
_MINIMAL_VALID_RAW = {
    "targets": {
        "libfoo": {
            "kind": "library",
            "binary_pattern": "lib/libfoo.so*",
        },
    },
}


def test_aggregate_gate_round_trips() -> None:
    raw = {
        "aggregate": {"gate": {"missing_required": "warn", "unexpected_target": "fail"}}
    }
    config = ProjectTargetsConfig.from_dict(raw)
    assert config.aggregate_gate == AggregateGateSpec(
        missing_required="warn", unexpected_target="fail"
    )
    assert ProjectTargetsConfig.from_dict(config.to_dict()) == config


def test_aggregate_gate_partial_block_round_trips() -> None:
    raw = {"aggregate": {"gate": {"missing_required": "warn"}}}
    config = ProjectTargetsConfig.from_dict(raw)
    assert config.aggregate_gate == AggregateGateSpec(missing_required="warn")
    assert config.to_dict()["aggregate"] == {"gate": {"missing_required": "warn"}}


def test_aggregate_gate_partial_block_unexpected_target_only_round_trips() -> None:
    """The other half of the partial-block case above: `missing_required`
    unset, only `unexpected_target` set -- `AggregateGateSpec.to_dict()`'s
    two `is not None` checks are independent, so a test covering only one
    ordering leaves the other branch unexercised."""
    raw = {"aggregate": {"gate": {"unexpected_target": "fail"}}}
    config = ProjectTargetsConfig.from_dict(raw)
    assert config.aggregate_gate == AggregateGateSpec(unexpected_target="fail")
    assert config.to_dict()["aggregate"] == {"gate": {"unexpected_target": "fail"}}


def test_no_aggregate_block_leaves_aggregate_gate_none() -> None:
    config = ProjectTargetsConfig.from_dict({})
    assert config.aggregate_gate is None
    assert "aggregate" not in config.to_dict()


def test_aggregate_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match=r"aggregate: unknown key"):
        ProjectTargetsConfig.from_dict({"aggregate": {"bogus": {}}})


def test_aggregate_gate_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict({"aggregate": {"gate": {"bogus": 1}}})


def test_aggregate_gate_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict({"aggregate": {"gate": "not-a-mapping"}})


@pytest.mark.parametrize("key", ["missing_required", "unexpected_target"])
def test_aggregate_gate_invalid_value_raises(key: str) -> None:
    with pytest.raises(ValueError, match=r"must be one of"):
        ProjectTargetsConfig.from_dict({"aggregate": {"gate": {key: "bogus"}}})


@pytest.mark.parametrize("key", ["missing_required", "unexpected_target"])
def test_aggregate_gate_explicit_null_value_raises(key: str) -> None:
    """An explicit YAML ``null`` for a `gate:` sub-key is a hard error, not
    silently treated the same as the key being absent -- a malformed
    ``aggregate: gate: {missing_required: null}`` block must not silently
    fall back to the hard-coded 'fail'/'include' defaults (Codex review)."""
    with pytest.raises(ValueError, match="must not be null"):
        ProjectTargetsConfig.from_dict({"aggregate": {"gate": {key: None}}})


def test_aggregate_valid_raw_config_has_no_validation_errors() -> None:
    raw = dict(_MINIMAL_VALID_RAW)
    raw["aggregate"] = {"gate": {"missing_required": "fail"}}
    config = ProjectTargetsConfig.from_dict(raw)
    report = validate_project_targets(config)
    assert report.ok, report.errors
