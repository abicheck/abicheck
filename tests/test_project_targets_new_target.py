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

"""Tests for ``checks[].allow_new_target`` (a target genuinely new to a
baseline-set, e.g. a library's first release) — split into its own sibling
file alongside ``test_project_targets_compile_frontend.py``/
``test_project_targets_consumer_compile.py``, since ``test_project_targets.py``
itself sits at the AI-readiness file-size hard cap.

Covers the schema side: ``CheckSpec.allow_new_target``'s default/round-trip/
type-validation, a `kind: library` target check accepting it, and a bundle
check rejecting it outright (``abicheck.buildsource.baseline_set.
resolve_bundle`` never returns ``new_target`` — see its own docstring).
Resolution-layer behavior (``resolve_target(allow_new_target=...)``) is
covered in ``tests/test_baseline_set.py``; the run-plan projection
(``RunPlanCheck.allow_new_target``) in ``tests/test_run_plan.py``.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.project_targets import (
    CheckSpec,
    ProjectTargetsConfig,
    validate_project_targets,
)


def test_check_spec_allow_new_target_defaults_false() -> None:
    check = CheckSpec.from_dict(
        {"channel": "accepted-main", "depth": "headers"}, where="x"
    )
    assert check.allow_new_target is False
    assert "allow_new_target" not in check.to_dict()


def test_check_spec_allow_new_target_true_round_trips() -> None:
    check = CheckSpec.from_dict(
        {"channel": "accepted-main", "depth": "headers", "allow_new_target": True},
        where="x",
    )
    assert check.allow_new_target is True
    assert check.to_dict()["allow_new_target"] is True
    round_tripped = CheckSpec.from_dict(check.to_dict(), where="x")
    assert round_tripped == check


def test_check_spec_allow_new_target_wrong_type_raises() -> None:
    with pytest.raises(ValueError, match="allow_new_target must be a boolean"):
        CheckSpec.from_dict(
            {
                "channel": "accepted-main",
                "depth": "headers",
                "allow_new_target": "true",
            },
            where="x",
        )


def test_library_check_allow_new_target_is_accepted() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libnew": {
                    "kind": "library",
                    "binary_pattern": "libnew.so",
                    "checks": [
                        {
                            "channel": "release-contract",
                            "depth": "headers",
                            "required": False,
                            "allow_new_target": True,
                        }
                    ],
                }
            },
            "baseline": {"channels": {"release-contract": {"source": "git"}}},
        }
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors
    assert config.targets["libnew"].checks[0].allow_new_target is True


def test_bundle_check_allow_new_target_is_rejected() -> None:
    """A bundle comparison needs one coherent release where every member
    already coexisted -- resolve_bundle() never returns new_target, so a
    bundle check declaring allow_new_target: true can never be satisfied
    and must be rejected at config-validation time (mirrors the
    channel: none rejection ``test_bundle_check_with_channel_none_is_rejected``
    in ``test_project_targets.py`` already pins for a different field)."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "a": {"kind": "library", "binary_pattern": "a.so", "bundle": "rel"},
                "b": {"kind": "library", "binary_pattern": "b.so", "bundle": "rel"},
            },
            "bundles": {
                "rel": {
                    "targets": ["a", "b"],
                    "checks": [
                        {
                            "channel": "release",
                            "depth": "binary",
                            "allow_new_target": True,
                        }
                    ],
                }
            },
            "baseline": {"channels": {"release": {"source": "git"}}},
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "allow_new_target is not supported for a bundle check" in e
        for e in report.errors
    )
