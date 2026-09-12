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
    def test_bundle_kind_accepts_analysis_assurance_complete(
        self, tmp_path: Path
    ) -> None:
        """ADR-071: the rejection this used to pin is gone.

        `analysis-assurance-complete` is supported for a bundle now -- the
        release fan-out folds every compared member's own
        `analysis_assurance` with `max` into the same exit axis a single
        library check uses, so there is no unsupported combination left to
        reject. Inverted rather than deleted: the input must still be
        *accepted* here, and a silent regression back to exit 64 would
        otherwise be invisible.
        """
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert "analysis-assurance-complete is not supported" not in (
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


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(),
    reason="actions/check-target/validate-inputs.sh not found",
)
class TestAssuranceOverlayEscapesWorkflowCommandInjection:
    """P2 finding (Codex review, PR #1222): ``_fail`` in
    ``actions/check-target/validate-inputs.sh`` interpolated its caller's
    raw message straight into ``echo "::error::$1"`` with no escaping --
    the same ``trust_boundary.shell_workflow_injection`` class already
    fixed twice on this branch (``action/run.sh``'s own ``_gha_escape``
    helper, and ``actions/check-target/action.yml``'s Python-heredoc
    ``_gha_escape``). Since the enum-validation guard above rejects an
    unrecognized ``analysis-assurance-complete`` value by echoing it back
    verbatim in the error message, a direct ``check-target`` caller (this
    input is a plain composite-action input, fully attacker-controlled per
    ``action/AGENTS.md``'s "treat every INPUT_*/GITHUB_* as untrusted"
    rule) could pass a multiline value embedding a smuggled
    ``::add-mask::``/``::error::`` line, which GitHub's line-delimited
    workflow-command parser would then treat as a second, attacker-authored
    command. Fixed by adding a ``_gha_escape`` helper to this script
    (``%``->``%25``, CR->``%0D``, LF->``%0A``, identical to the other two
    fixes' scheme) and routing ``_fail`` through it via ``printf`` instead
    of raw ``echo``."""

    def test_newline_in_assurance_value_does_not_smuggle_a_command(
        self, tmp_path: Path
    ) -> None:
        evil_value = "true\n::add-mask::pwned"
        result = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_BASELINE_PATH": "./b",
                "INPUT_ANALYSIS_ASSURANCE_COMPLETE": evil_value,
            },
            tmp_path,
        )
        assert result.returncode == 64
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        # The raw newline the malicious input value contained must never
        # reach the annotation stream unescaped -- every line but the
        # first must NOT itself start a new `::`-prefixed workflow
        # command (a real second `::error::`/`::add-mask::` line would
        # mean the injection landed).
        lines = combined.splitlines()
        assert not any(line.startswith("::") for line in lines[1:])
        assert "%0A::add-mask::pwned" in combined
