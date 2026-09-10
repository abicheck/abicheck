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

"""Codex review, round 9: ``cli_compare_release_matrix._collect_matrix_result``
built its own ``PolicyFile`` by hand (``_load_suppression_and_policy`` plus a
manual pack fold) instead of calling
``pack_application.resolve_bundle_policy_file`` -- the shared resolver its
own sibling bundle-result call already used -- so a build-configuration
matrix finding (``CXX_STANDARD_FLOOR_RAISED`` and friends) never honored a
project-config ``.abicheck.yml`` ``policy.overrides`` entry, unlike the
per-library and bundle-result findings in the same release run.

Direct-call test (not a full ``compare-release`` CLI invocation, which would
need real ``--probe-matrix old=/new=`` snapshot files and compiled
libraries) -- ``_load_probe_matrix_changes`` is monkeypatched to return one
synthetic matrix finding, isolating the fix to exactly the code path it
touches.
"""

from __future__ import annotations

import abicheck.frontends.cli.runtime as runtime_mod
from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.cli_compare_release_matrix import _collect_matrix_result


def test_project_config_policy_override_reaches_matrix_findings(
    monkeypatch: object,
) -> None:
    matrix_change = Change(
        kind=ChangeKind.CXX_STANDARD_FLOOR_RAISED,
        symbol="<matrix>",
        description="C++ standard floor raised",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        runtime_mod, "_load_probe_matrix_changes", lambda *a, **k: [matrix_change]
    )

    result, worst_verdict = _collect_matrix_result(
        "old",  # type: ignore[arg-type]
        "new",  # type: ignore[arg-type]
        "strict_abi",
        "NO_CHANGE",
        project_policy_overrides={
            ChangeKind.CXX_STANDARD_FLOOR_RAISED: Verdict.COMPATIBLE
        },
    )

    assert result is not None
    assert result.verdict is Verdict.COMPATIBLE
    assert worst_verdict != "ERROR"


def test_no_project_config_still_scores_the_matrix_finding(
    monkeypatch: object,
) -> None:
    """Negative control: with no project override, the identical matrix
    finding is still scored at its default (breaking-ish) verdict -- this
    is not a test that matrix findings stop mattering."""
    matrix_change = Change(
        kind=ChangeKind.CXX_STANDARD_FLOOR_RAISED,
        symbol="<matrix>",
        description="C++ standard floor raised",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        runtime_mod, "_load_probe_matrix_changes", lambda *a, **k: [matrix_change]
    )

    result, worst_verdict = _collect_matrix_result(
        "old",  # type: ignore[arg-type]
        "new",  # type: ignore[arg-type]
        "strict_abi",
        "NO_CHANGE",
        project_policy_overrides=None,
    )

    assert result is not None
    assert any(c.kind is ChangeKind.CXX_STANDARD_FLOOR_RAISED for c in result.changes)
    assert worst_verdict != "NO_CHANGE"
