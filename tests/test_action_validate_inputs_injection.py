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

#: Escape sequences spelled with *literal* backslashes -- no real newline
#: anywhere, so the CR/LF collapse has nothing to remove. These are inert
#: under a plain `echo` and become real lines under one with `xpg_echo`
#: enabled, which is a build-time default on some bash builds and reachable
#: through `BASHOPTS`/`BASH_ENV`. `printf '%s\\n'` is what makes them data
#: under every shell option (Codex review, PR #1165).
_ESCAPE_PAYLOADS = (
    r"1\n::error::PWNED",
    r"1\r\n::error::PWNED",
    r"1\n::set-output name=pwned::yes",
    r"1\x0a::error::PWNED",
)

#: Percent-encoded line breaks. The runner *decodes* a workflow command's
#: message data, so these hold no CR/LF for the collapse to find and no
#: backslash escape for `echo` to expand -- the runner itself supplies the
#: line break, after this script is done. Escaping `%` to `%25` is what
#: makes the decode round-trip back to a literal `%` (CodeRabbit review,
#: CWE-117). A third independent path into the same defense, after real
#: newlines and `xpg_echo` backslash escapes.
_PERCENT_PAYLOADS = (
    "1%0A::error::PWNED",
    "1%0D%0A::error::PWNED",
    "1%0a::set-output name=pwned::yes",
    "1%0A::add-mask::secret",
    "1%25%30A::error::PWNED",
)

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

    @pytest.mark.parametrize("payload", _ESCAPE_PAYLOADS)
    @pytest.mark.parametrize("xpg_echo", [False, True])
    def test_a_literal_escape_sequence_cannot_become_a_new_line(
        self, payload: str, xpg_echo: bool
    ) -> None:
        """A value holding the *literal* characters ``\\n::error::`` must stay
        one line under a shell whose ``echo`` expands backslash escapes.

        The CR/LF collapse cannot defend this on its own -- there is no real
        newline in the value for it to remove; the emitter would create one.
        Running the real script under ``bash -O xpg_echo`` reproduced exactly
        that before the emitters moved from ``echo`` to ``printf '%s\\n'``,
        which is why this is parametrized over both shell modes rather than
        asserting only the default one: the default-mode case alone passed
        before the fix too.
        """
        result = _run_validate(
            {"INPUT_MODE": "compare", "INPUT_JOBS": payload},
            bash_options=["-O", "xpg_echo"] if xpg_echo else [],
        )

        assert result.returncode == 0, result.stdout + result.stderr
        emitted = [ln for ln in result.stdout.splitlines() if ln.startswith("::")]
        assert len(emitted) == 1, (
            f"payload {payload!r} produced {len(emitted)} annotation lines "
            f"under xpg_echo={xpg_echo}:\n{result.stdout}"
        )
        for line in result.stdout.splitlines():
            assert not line.startswith(("::error::", "::set-output")), (
                f"payload {payload!r} forged a workflow command under "
                f"xpg_echo={xpg_echo}:\n{result.stdout}"
            )

    @pytest.mark.parametrize("payload", _PERCENT_PAYLOADS)
    def test_a_percent_encoded_line_break_cannot_become_a_new_line(
        self, payload: str
    ) -> None:
        """A percent-encoded CR/LF must not survive into the annotation.

        The runner decodes `%0A`/`%0D` in a workflow command's message, so
        the line break is created *after* this script finishes -- neither
        the CR/LF collapse nor `printf` can see it. Escaping `%` to `%25`
        first is what defeats it, and it must happen before the collapse so
        an escape introduced here is not itself re-escaped
        (`actions/toolkit`'s own `escapeData` order).
        """
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_JOBS": payload})

        assert result.returncode == 0, result.stdout + result.stderr
        emitted = [ln for ln in result.stdout.splitlines() if ln.startswith("::")]
        assert len(emitted) == 1, result.stdout
        # No raw `%0A`/`%0D` may reach the runner: every `%` is escaped, so
        # the only percent sequence present is the escape itself.
        body = emitted[0]
        assert "%0A" not in body.upper().replace("%250A", ""), body
        assert "%0D" not in body.upper().replace("%250D", ""), body
        assert "%25" in body, f"the `%` escape did not fire at all:\n{body}"

    def test_the_percent_escape_precedes_the_newline_collapse(self) -> None:
        """Order is load-bearing, so pin it rather than trusting the code
        reads that way: a real newline *and* a literal `%` in one value must
        both be neutralized, with the collapse's replacement never carrying
        a `%` the escape has already passed over."""
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_JOBS": "a%b\nc"})

        assert result.returncode == 0, result.stdout + result.stderr
        emitted = [ln for ln in result.stdout.splitlines() if ln.startswith("::")]
        assert len(emitted) == 1, result.stdout
        assert "a%25b" in emitted[0], emitted[0]
