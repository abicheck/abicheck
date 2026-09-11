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

"""``actions/check-target/validate-inputs.sh``'s own
``analysis-assurance-complete`` x ``kind: bundle`` input-validation guard.

Split out of ``tests/test_action_check_target.py`` (its ``TestValidateInputs``
class) purely to keep that file under its ``architecture/debt.yaml``
``no_growth`` baseline (PR #1222) -- the same "move responsibility, don't
trim to fit" convention the root ``AGENTS.md`` states. Shares the same
execution helpers (``tests/_check_target_exec.py``) that file's own
``TestValidateInputs`` class uses, unchanged.

A caller invoking check-target directly (bypassing
``check-project.yml``/``project_targets.py``'s own run-plan validation)
could otherwise pair ``kind: bundle`` with ``analysis-assurance-complete:
true`` and reach a late, confusing operational failure deep inside
``cli_compare_options.py``'s ``_reject_set_input_flags`` instead of an
immediate, clear input-validation error (Codex review).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _check_target_exec import _BASE_IDENTITY, VALIDATE_SH, _run


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(),
    reason="actions/check-target/validate-inputs.sh not found",
)
class TestValidateInputsAssuranceBundleInteraction:
    def test_bundle_kind_rejects_analysis_assurance_complete(
        self, tmp_path: Path
    ) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_REQUESTED_DEPTH": "binary",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": "true",
            },
            tmp_path,
        )
        assert result.returncode == 64
        assert "analysis-assurance-complete is not supported for kind: bundle" in (
            result.stdout + result.stderr
        )

    def test_bundle_kind_allows_analysis_assurance_complete_false(
        self, tmp_path: Path
    ) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "bundle",
                "INPUT_REQUESTED_DEPTH": "binary",
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_BUNDLE_MEMBERS": '["libpvxs", "libpvxsIoc"]',
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(),
    reason="actions/check-target/validate-inputs.sh not found",
)
class TestValidateInputsAnalysisAssuranceCompleteEnumValidation:
    """P2 finding (Codex review, fresh evidence, PR #1222 fourth round):
    ``analysis-assurance-complete``'s truthiness check in action.yml's own
    "Generate assurance-overlay config" step (``if:
    inputs.analysis-assurance-complete == 'true'``) only recognizes the
    exact lowercase string 'true' -- ANY other value (a stray 'True',
    'yes', or a typo) is silently treated as false, SKIPPING that step
    entirely rather than failing, so a caller who clearly intended to
    enable the assurance floor instead gets a normal, unenforced analysis
    run with no diagnostic at all. Unlike its sibling boolean-like input
    ``allow-new-target`` (validated by the ``case "$ALLOW_NEW_TARGET" in
    true | false) ;; *) _fail ...`` pattern immediately above this input in
    ``validate-inputs.sh``), ``analysis-assurance-complete`` had no such
    validation at all before this fix -- added here, matching that exact
    existing pattern rather than inventing a new style."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param("True", id="capital-T"),
            pytest.param("yes", id="yes"),
            pytest.param("complete", id="typo-word"),
            pytest.param("1", id="numeric-one"),
        ],
    )
    def test_unrecognized_value_fails_loud(
        self, tmp_path: Path, bad_value: str
    ) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": bad_value,
            },
            tmp_path,
        )
        assert result.returncode == 64
        assert f"analysis-assurance-complete '{bad_value}' is not recognized" in (
            result.stdout + result.stderr
        )

    def test_true_still_passes(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": "true",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr

    def test_false_still_passes(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stderr

    def test_omitted_defaults_to_false_and_still_passes(self, tmp_path: Path) -> None:
        result = _run(
            VALIDATE_SH,
            {**_BASE_IDENTITY, "INPUT_BASELINE_PATH": "./b"},
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
