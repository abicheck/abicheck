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

"""Workflow-command injection defense for `action/validate-inputs.sh`
(bug class `trust_boundary.shell_workflow_injection`).

Split out of `test_action_validate_inputs.py` — that module tests which
mode/input *combinations* the validator accepts or rejects; this one tests
that no input value can escape the annotation it is printed inside,
whatever the combination. A different question about the same script, and
the reason for the split is `architecture/debt.yaml`'s `no_growth` baseline
on that file (AGENTS.md "Files that are large": move responsibility out to
a properly-owned module, never trim the file to fit).

Every message the validator emits interpolates a workflow-controlled
`INPUT_*` value into a GitHub annotation, which is line-delimited: a value
carrying a newline ends the annotation, and what follows is parsed as a
*new* workflow command. Verified as a live defect before the fix —
`INPUT_JOBS="1\n::error::PWNED"` really did emit a spoofed `::error::` line
of its own, and the pre-existing `build-info` message had the same shape,
so this was reachable before the PR that fixed it.

These tests execute the attack and inspect the emitted lines, rather than
asserting the script's *text* contains a sanitizer. That distinction is the
whole reason this bug class exists: #705 shipped a text-asserted defense and
#758 had to add the executing test.
"""

from __future__ import annotations

import pytest

from test_action_validate_inputs import VALIDATE_SH, _run_validate

_INJECTION_PAYLOADS = (
    "1\n::error::PWNED",
    "1\r\n::error::PWNED",
    "1\n::set-output name=pwned::yes",
    "1\n::add-mask::secret",
    "1\n::notice::PWNED",
)


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestAnnotationInjection:
    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    @pytest.mark.parametrize(
        "var", ["INPUT_JOBS", "INPUT_BUNDLE_SYSTEM_PROVIDERS", "INPUT_BUILD_INFO"]
    )
    def test_an_input_value_cannot_forge_a_workflow_command(
        self, var: str, payload: str
    ) -> None:
        """No INPUT_* value may create a workflow command of its own.

        Covers the two tombstones this change adds *and* a pre-existing
        interpolating input (``build-info``), because the fix is in the
        shared ``_warn``/``_fail`` helpers -- a defense that only held for
        the newly-added sites would be the narrow patch, not the fix.
        """
        env = {"INPUT_MODE": "compare", var: payload}
        if var == "INPUT_BUILD_INFO":
            # build-info's own message only fires on the scan-mode conflict.
            env = {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_BUILD_INFO": payload,
                "INPUT_COMPILE_DB": "compile_commands.json",
            }
        result = _run_validate(env)

        emitted = [ln for ln in result.stdout.splitlines() if ln.startswith("::")]
        # Exactly one annotation: the script's own. The payload's commands
        # must be inert text inside it, never lines of their own.
        assert len(emitted) == 1, result.stdout
        for forged in ("::error::PWNED", "::set-output", "::add-mask", "::notice::"):
            assert not any(
                ln.startswith(forged) for ln in result.stdout.splitlines()
            ), f"{var} payload forged a {forged} workflow command:\n{result.stdout}"

    def test_the_sanitizer_preserves_the_value_for_the_reader(self) -> None:
        """Neutralizing the payload must not blank the value out: the
        annotation still has to tell the caller *what* they set, or the
        tombstone stops being actionable."""
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_JOBS": "1\n2"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "jobs ('1 2')" in result.stdout
