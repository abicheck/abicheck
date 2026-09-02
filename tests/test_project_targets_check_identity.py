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

"""G42 "Explicit check identifiers" tests for ``CheckSpec.id``/
``CheckSpec.analysis_*`` -- split out of ``test_project_targets.py`` (that
file sits at the AI-readiness 2000-line hard cap and carries a
``no_growth`` debt-baseline entry, per this repo's own ``file-size`` gate/
``architecture/debt.yaml`` convention: grow via a new sibling test file, not
by extending the file at its cap).

Covers ``checks[].id``/``checks[].analysis: {evidence, policy, assurance}``
parsing, round-tripping, and the "parsing alone isn't validation" split
this module's own convention documents: structural validity (mapping
shape, known keys, non-empty strings) raises directly from ``CheckSpec.
from_dict``, while the identifier-charset check is deferred to
``validate_project_targets`` (``_check_issues``), same as every other
identifier this module validates. See ``docs/contribute/plans/
g42-check-identity-environments-and-provider-resolution.md``.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.project_targets import (
    CheckSpec,
    ProjectTargetsConfig,
    validate_project_targets,
)


def test_check_id_and_analysis_round_trip() -> None:
    check = CheckSpec.from_dict(
        {
            "channel": "accepted-main",
            "depth": "source",
            "id": "l4-plugin-rhel8",
            "analysis": {
                "evidence": "clang-plugin",
                "policy": "strict-abi",
                "assurance": "complete",
            },
        },
        where="targets.libfoo.checks[0]",
    )
    assert check.id == "l4-plugin-rhel8"
    assert check.analysis_evidence == "clang-plugin"
    assert check.analysis_policy == "strict-abi"
    assert check.analysis_assurance == "complete"
    d = check.to_dict()
    assert d["id"] == "l4-plugin-rhel8"
    assert d["analysis"] == {
        "evidence": "clang-plugin",
        "policy": "strict-abi",
        "assurance": "complete",
    }
    round_tripped = CheckSpec.from_dict(d, where="targets.libfoo.checks[0]")
    assert round_tripped == check


def test_check_id_and_analysis_are_optional() -> None:
    """Every existing checks[] entry that predates id:/analysis: parses
    unchanged -- both default to empty, and to_dict() omits them entirely."""
    check = CheckSpec.from_dict(
        {"channel": "accepted-main", "depth": "headers"},
        where="targets.libfoo.checks[0]",
    )
    assert check.id == ""
    assert check.analysis_evidence == check.analysis_policy == check.analysis_assurance
    assert check.analysis_evidence == ""
    d = check.to_dict()
    assert "id" not in d
    assert "analysis" not in d


def test_check_id_must_be_a_valid_identifier() -> None:
    """id:'s charset is validated by validate_project_targets (deferred),
    not raised at parse time -- same split every other identifier in this
    module follows."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [{"channel": "none", "depth": "headers", "id": "bad id"}],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("id 'bad id' is not a valid identifier" in e for e in report.errors)


def test_check_analysis_must_be_a_mapping() -> None:
    with pytest.raises(ValueError, match="analysis must be a mapping"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": "clang-plugin"},
            where="targets.libfoo.checks[0]",
        )


def test_check_analysis_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": {"bogus": "x"}},
            where="targets.libfoo.checks[0]",
        )


def test_check_analysis_field_must_be_a_valid_identifier() -> None:
    """Same deferred-validation split as id: above."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "none",
                            "depth": "headers",
                            "analysis": {"evidence": "bad evidence"},
                        }
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "analysis.evidence 'bad evidence' is not a valid identifier" in e
        for e in report.errors
    )


def test_check_analysis_empty_string_field_is_rejected_structurally() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": {"evidence": ""}},
            where="targets.libfoo.checks[0]",
        )


def test_check_id_empty_string_is_rejected_structurally() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "id": ""},
            where="targets.libfoo.checks[0]",
        )
