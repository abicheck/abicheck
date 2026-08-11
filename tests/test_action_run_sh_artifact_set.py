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

"""Behavioral tests for ``action/run.sh``'s ``new-library-set``/
``bundle-system-providers`` forwarding (ADR-056, G34).

Extracts the full mode-branch region of ``run.sh`` (helper functions
through the end of the compare/dump/scan/deps-tree/deps-compare if/elif
chain) verbatim -- the same "parse the real file, don't hand-copy it"
discipline as ``test_action_run_sh_helpers.py`` -- and runs it with a
harness that sets the relevant ``INPUT_*`` env vars, capturing the
resulting ``CMD`` array.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    """Helper functions + the full compare/dump/scan/.../else mode chain,
    extracted verbatim from run.sh (everything up to the shared -v/extra-args
    tail that follows every branch)."""
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    """Source the real mode-branch region with *env_extra* set, return CMD."""
    script = (
        _mode_branches_region()
        + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _run_raw(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Source the real mode-branch region with *env_extra* set, returning
    the raw completed process (exit code + stdout/stderr) instead of
    asserting success -- for cases expected to reject the input."""
    script = _mode_branches_region()
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanArtifactSetForwarding:
    def test_new_library_set_maps_to_artifact_set_flag(self) -> None:
        cmd = _run_cmd(
            {"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY_SET": "a.so,b.so"}
        )
        assert "scan" in cmd
        i = cmd.index("--artifact-set")
        assert cmd[i + 1] == "a.so,b.so"
        # The positional single-artifact form must NOT also be present.
        assert "a.so,b.so" not in cmd[:i]

    def test_new_library_set_forwards_bundle_system_providers(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_BUNDLE_SYSTEM_PROVIDERS": "libvendor.so.1",
            }
        )
        i = cmd.index("--bundle-system-providers")
        assert cmd[i + 1] == "libvendor.so.1"

    def test_new_library_set_maps_new_header_to_bare_flag(self) -> None:
        # P2 regression (Codex review): _run_artifact_set rejects old=/new=
        # header scoping outright (no old side for a set) -- new-header must
        # map to a bare -H, not "-H new=...".
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_NEW_HEADER": "include/foo.h",
            }
        )
        i = cmd.index("-H")
        assert cmd[i + 1] == "include/foo.h"
        assert "new=include/foo.h" not in cmd

    def test_new_library_set_maps_new_include_to_bare_flag(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_NEW_INCLUDE": "include/",
            }
        )
        i = cmd.index("-I")
        assert cmd[i + 1] == "include/"
        assert "new=include/" not in cmd

    def test_new_library_set_rejects_old_header(self) -> None:
        result = _run_raw(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_OLD_HEADER": "old/include/foo.h",
            }
        )
        assert result.returncode != 0
        assert "old-header" in result.stdout

    def test_bare_new_library_still_uses_positional_form(self) -> None:
        cmd = _run_cmd({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": "new.so"})
        assert "scan" in cmd
        assert "new.so" in cmd
        assert "--artifact-set" not in cmd

    def test_new_library_set_rejects_against(self) -> None:
        # P2 regression (Codex review): validate-inputs.sh already rejects
        # this combination before run.sh ever executes in the composite
        # Action, but a direct run.sh invocation (bypassing that step) must
        # reject it too, loudly -- not silently downgrade to an audit-only
        # scan by dropping --against.
        result = _run_raw(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_AGAINST": "old.so",
            }
        )
        assert result.returncode != 0
        assert "against" in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanPolicyFlagsOmittedWithoutBaseline:
    """P1 regression (Codex review): cli_scan.py's
    ``_reject_comparison_only_flags()`` hard-rejects ``--policy``/
    ``--policy-file``/``--suppress`` on a scan with no ``--against``
    baseline. ``action.yml``'s ``policy`` input has a non-empty default
    (``strict_abi``), so it is *always* present in ``INPUT_POLICY`` — an
    unconditional forward would break every existing audit-only scan step
    with a usage error. These three flags must only be forwarded when a
    baseline is actually present.
    """

    def test_audit_only_scan_omits_default_policy_flag(self) -> None:
        # Simulates exactly how the real composite Action invokes an
        # audit-only scan step: action.yml's own default fills INPUT_POLICY
        # with "strict_abi" even though the user never set `policy:`.
        cmd = _run_cmd({
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_POLICY": "strict_abi",
        })
        assert "--policy" not in cmd

    def test_audit_only_scan_omits_explicit_policy_file_and_suppress(self) -> None:
        # Unlike `policy`, these two have no action.yml default — but an
        # audit-only step that explicitly sets them must still omit the
        # flags (the CLI has nothing to apply them to without a baseline).
        cmd = _run_cmd({
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_POLICY_FILE": "policy.yml",
            "INPUT_SUPPRESS": "suppress.yml",
        })
        assert "--policy-file" not in cmd
        assert "--suppress" not in cmd

    def test_new_library_set_audit_only_omits_default_policy_flag(self) -> None:
        # The --artifact-set path is unconditionally audit-only (ADR-056).
        cmd = _run_cmd({
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
            "INPUT_POLICY": "strict_abi",
        })
        assert "--policy" not in cmd

    def test_scan_with_baseline_forwards_policy_flags(self) -> None:
        cmd = _run_cmd({
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_AGAINST": "old.so",
            "INPUT_POLICY": "sdk_vendor",
            "INPUT_POLICY_FILE": "policy.yml",
            "INPUT_SUPPRESS": "suppress.yml",
        })
        i = cmd.index("--policy")
        assert cmd[i + 1] == "sdk_vendor"
        i = cmd.index("--policy-file")
        assert cmd[i + 1] == "policy.yml"
        i = cmd.index("--suppress")
        assert cmd[i + 1] == "suppress.yml"

    def test_audit_true_alias_omits_default_policy_flag_even_with_against(self) -> None:
        # The deprecated `audit: true` back-compat alias forces audit-only
        # by skipping --against outright even when it resolved to a value
        # elsewhere — the policy flags must follow the same rule.
        cmd = _run_cmd({
            "INPUT_MODE": "scan",
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_AGAINST": "old.so",
            "INPUT_AUDIT": "true",
            "INPUT_POLICY": "strict_abi",
        })
        assert "--against" not in cmd
        assert "--policy" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompareBundleSystemProvidersForwarding:
    def test_forwarded_for_single_pair_compare(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_BUNDLE_SYSTEM_PROVIDERS": "libvendor.so.1",
            }
        )
        i = cmd.index("--bundle-system-providers")
        assert cmd[i + 1] == "libvendor.so.1"

    def test_absent_when_unset(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
            }
        )
        assert "--bundle-system-providers" not in cmd
