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

"""Workflow-command injection defense for `action/run.sh`
(bug class `trust_boundary.shell_workflow_injection`).

The sibling module `test_action_validate_inputs_injection.py` closed this
class for `action/validate-inputs.sh`, whose messages all route through one
`_fail`/`_warn` pair. `run.sh` was never given the same treatment: six of
its own `::error::` messages interpolated a workflow-controlled `INPUT_*`
value straight into an annotation. A GitHub annotation is line-delimited,
so a value carrying a newline ends it and everything after is parsed as a
*new* workflow command.

Verified as a live defect before the fix, not inferred from reading:

    INPUT_MODE=dump INPUT_REQUIRE_COMPLETE_ANALYSIS=$'true\\n::warning::forged' \\
        bash action/run.sh
    ::error::require-complete-analysis ('true
    ::warning::forged') was removed and is no longer forwarded — ...

The second line is a real, runner-parsed workflow command supplied by the
input's value. Five of the six sites predate this PR; the sixth is the
`require-complete-analysis` guard it moved out of the compare branch, which
widened that one's reach from one mode to all four. All six are fixed
together, since they are one defect with six instances rather than six
defects (AGENTS.md, "Fix the cause, not the instance").

These tests execute the attack and inspect the emitted lines rather than
asserting that the script's *text* contains a sanitizer. That distinction
is why this bug class exists at all in this repository: #705 shipped a
text-asserted defense and #758 had to add the executing test.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = REPO_ROOT / "action" / "run.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file(),
    reason="needs action/run.sh and a POSIX shell",
)

#: Real line breaks -- the direct form of the attack.
_REAL_BREAK_PAYLOADS = (
    "x\n::error::PWNED",
    "x\r\n::error::PWNED",
    "x\n::set-output name=pwned::yes",
    "x\n::add-mask::secret",
)

#: Percent-encoded breaks. The runner *decodes* a workflow command's message
#: data, so these carry no CR/LF for the collapse to find; escaping `%` to
#: `%25` is what stops the decode from producing one (CWE-117).
_PERCENT_PAYLOADS = (
    "x%0A::error::PWNED",
    "x%0D%0A::error::PWNED",
    "x%0a::add-mask::secret",
    "x%25%30A::error::PWNED",
)

#: Literal backslash escapes: inert under a plain `echo`, but real lines
#: under one with `xpg_echo` enabled (a build-time default on some bash
#: builds, and reachable via `BASHOPTS`/`BASH_ENV`). This is what makes
#: `printf '%s\n'` rather than `echo` part of the defense.
_ESCAPE_PAYLOADS = (
    r"x\n::error::PWNED",
    r"x\r\n::error::PWNED",
    r"x\x0a::error::PWNED",
)

_ALL_PAYLOADS = _REAL_BREAK_PAYLOADS + _PERCENT_PAYLOADS + _ESCAPE_PAYLOADS

#: One reachable invocation per interpolating site, so the sweep covers all
#: six rather than the one that prompted the review. Each maps the attacked
#: variable to the surrounding inputs needed to reach its guard.
_SITES = {
    "INPUT_REQUIRE_COMPLETE_ANALYSIS": {
        "INPUT_MODE": "dump",
        "INPUT_NEW_LIBRARY": "libfoo.so",
    },
    "INPUT_BASELINE_PROFILE": {
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": "libfoo.so",
        "INPUT_OLD_LIBRARY": "libold.so",
    },
    "INPUT_BASELINE_TARGET": {
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": "libfoo.so",
        "INPUT_OLD_LIBRARY": "libold.so",
    },
    "INPUT_BASELINE_GENERATION": {
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": "libfoo.so",
        # `abi-baseline` is required to get this far: the baseline-set
        # fallback is only reached while resolving one, so without it the
        # run bails on an earlier guard and never evaluates
        # baseline-generation at all. The vacuity test below is what caught
        # that -- the injection sweep alone passed, having proved nothing.
        "INPUT_ABI_BASELINE": "latest-release",
        "INPUT_BASELINE_PROFILE": "p",
        "INPUT_BASELINE_TARGET": "t",
    },
}

_FORGEABLE_PREFIXES = (
    "::error::",
    "::warning::",
    "::notice::",
    "::set-output",
    "::add-mask",
    "::add-path",
    "::save-state",
)


def _run(env_extra: dict[str, str], bash_options: list[str] | None = None):
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "GITHUB_STEP_SUMMARY": os.devnull,
            "GITHUB_OUTPUT": os.devnull,
            "GITHUB_ENV": os.devnull,
        }
    )
    env.update(env_extra)
    return subprocess.run(
        [bash_executable(), *(bash_options or []), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=REPO_ROOT,
    )


def _forged_lines(output: str) -> list[str]:
    """Lines that are themselves workflow commands, minus the script's own.

    A payload's text appearing *inside* the script's single annotation is
    fine -- that is the value being quoted back to the user. What must never
    happen is a line that the runner will parse as a command of its own.
    """
    lines = output.splitlines()
    forged: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith(_FORGEABLE_PREFIXES):
            continue
        # The script's own annotation is the one it emitted deliberately;
        # every other command line came from the payload.
        if index == 0 and line.startswith("::error::"):
            continue
        forged.append(line)
    return forged


@pytest.mark.parametrize("payload", _ALL_PAYLOADS)
@pytest.mark.parametrize("var", sorted(_SITES))
def test_no_input_value_can_forge_a_workflow_command(var: str, payload: str) -> None:
    """No `INPUT_*` value may create a workflow command of its own.

    Swept over every interpolating site, not only the one a reviewer
    happened to find: the fix is the shared `_error_annotation` helper, and
    a defense that held only at the reported site would be the narrow patch
    this repository's own guidance rejects.
    """
    result = _run({**_SITES[var], var: payload})
    combined = result.stdout + result.stderr
    forged = _forged_lines(combined)
    assert not forged, (
        f"{var} payload {payload!r} forged workflow command line(s) {forged}:\n"
        f"{combined}"
    )


@pytest.mark.parametrize("var", sorted(_SITES))
def test_the_attack_reaches_the_guard_it_targets(var: str) -> None:
    """Vacuity guard, and the one this module would be worthless without.

    Every assertion above is an *absence*, so an invocation that never
    reached its guard -- a typo in the surrounding inputs, a mode that
    bails earlier -- would pass while testing nothing. This asserts the
    positive: with a benign value, the run really does emit that site's own
    error naming the input.
    """
    flag = var.removeprefix("INPUT_").lower().replace("_", "-")
    result = _run({**_SITES[var], var: "benign-value"})
    combined = result.stdout + result.stderr
    assert flag in combined, (
        f"the invocation for {var} never reached its own guard, so the "
        f"injection sweep above proves nothing for it:\n{combined}"
    )


@pytest.mark.parametrize("payload", _ESCAPE_PAYLOADS)
def test_backslash_escapes_stay_inert_under_xpg_echo(payload: str) -> None:
    """`xpg_echo` makes a plain `echo` expand `\\n` into a real line, which
    is why the emit uses `printf '%s\\n'`. Run the same attack under a shell
    configured that way -- the option is a build-time default on some bash
    builds, so this is a real caller configuration, not a contrived one.
    """
    result = _run(
        {
            **_SITES["INPUT_REQUIRE_COMPLETE_ANALYSIS"],
            "INPUT_REQUIRE_COMPLETE_ANALYSIS": payload,
        },
        bash_options=["-O", "xpg_echo"],
    )
    combined = result.stdout + result.stderr
    forged = _forged_lines(combined)
    assert not forged, (
        f"payload {payload!r} forged {forged} under xpg_echo:\n{combined}"
    )


def test_no_run_sh_annotation_interpolates_an_input_unsanitized() -> None:
    """The exhaustiveness half: a *new* interpolating site added later must
    route through the helper too.

    A static scan, deliberately paired with the executing tests above rather
    than replacing them -- text alone is what #705 asserted and #758 had to
    fix. Its job is only to catch the site the sweep does not know about
    yet, since the sweep can only cover invocations someone has mapped.
    """
    offenders = [
        line.strip()
        for line in RUN_SH.read_text(encoding="utf-8").splitlines()
        if re.search(r'echo\s+"::(error|warning|notice)::.*\$\{INPUT_', line)
    ]
    assert not offenders, (
        "these action/run.sh annotations interpolate a workflow-controlled "
        "INPUT_* value with a bare `echo`, so a value carrying a newline "
        "forges a workflow command -- emit them with `_error_annotation` "
        "instead:\n" + "\n".join(offenders)
    )
