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

"""``action/run.sh``'s ``mode: scan`` reimplementation as ``abicheck
compare`` (ADR-068 Phase 4 item 1,
``docs/contribute/plans/one-comparison-product.md``).

The Action's own ``mode: scan`` input keeps accepting exactly the same
documented values (ADR-068 D8) -- only the CLI subcommand ``run.sh``
assembles under the hood changes, and only for invocations that use none of
the still-scan-only capabilities (``new-library-set``/``budget``/
``crosscheck``/``risk-rules``/``build-target``, an audit-only run with no
``against`` resolved, a directory/package ``against``, or any ``format``
other than ``json``, all named in ``run.sh``'s own gate comment). Those
cases keep invoking ``abicheck scan`` directly and are already covered by
the existing scan-mode test modules (``test_action_run_sh_build_target.py``,
``test_action_run_sh_artifact_set.py``,
``test_action_run_sh_public_header_dir_scan_scope.py``, ...) -- none of
which combine ``against`` with ``format: json`` and no other caveat, so the
migrated path itself was previously uncovered. This module closes that gap.

Extracts the full mode-branch region of ``run.sh`` verbatim -- same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_build_target.py``/``test_action_run_sh_artifact_set.py``
-- and runs it with a harness that sets the relevant ``INPUT_*`` env vars,
capturing the resulting ``CMD`` array.
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
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
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
    script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _base_env(**extra: str) -> dict[str, str]:
    return {
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": "lib.so",
        "INPUT_AGAINST": "baseline.json",
        "INPUT_FORMAT": "json",
        **extra,
    }


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanMigratesToCompare:
    """A single-artifact ``scan --against`` run in ``format: json``, with
    none of the still-scan-only capabilities set, now assembles a real
    ``abicheck compare OLD NEW`` invocation instead of ``abicheck scan``."""

    def test_invokes_compare_not_scan(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare", cmd
        assert "scan" not in cmd, cmd

    def test_positional_operands_are_old_then_new(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare"
        assert cmd[2] == "baseline.json"
        assert cmd[3] == "lib.so"

    def test_format_json_forwarded(self) -> None:
        cmd = _run_cmd(_base_env())
        idx = cmd.index("--format")
        assert cmd[idx + 1] == "json"

    def test_no_against_flag_no_scan_subcommand_token(self) -> None:
        # `--against` is scan's own flag; `compare` has no such option --
        # the resolved baseline becomes the OLD positional instead.
        cmd = _run_cmd(_base_env())
        assert "--against" not in cmd, cmd

    def test_header_include_forwarded_sided(self) -> None:
        cmd = _run_cmd(
            _base_env(
                INPUT_HEADER="both.h",
                INPUT_OLD_HEADER="old.h",
                INPUT_NEW_HEADER="new.h",
                INPUT_INCLUDE="both_inc",
                INPUT_OLD_INCLUDE="old_inc",
                INPUT_NEW_INCLUDE="new_inc",
            )
        )
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        i_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-I"]
        assert "both.h" in h_pairs
        assert "old=old.h" in h_pairs
        assert "new=new.h" in h_pairs
        assert "both_inc" in i_pairs
        assert "old=old_inc" in i_pairs
        assert "new=new_inc" in i_pairs

    def test_public_header_dir_forwarded_as_sided_new_h(self) -> None:
        # `compare` has no dedicated --public-header-dir flag at all --
        # forwarded as a sided `-H new=...` root instead (matching real
        # `mode: compare`'s own identical treatment of this input).
        cmd = _run_cmd(_base_env(INPUT_PUBLIC_HEADER_DIR="pub"))
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "new=pub" in h_pairs
        assert "pub" not in h_pairs
        assert "--public-header-dir" not in cmd

    def test_sources_and_build_info_scoped_to_new(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_SOURCES="src", INPUT_BUILD_INFO="build"))
        sources_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "--sources"]
        build_info_pairs = [
            cmd[j + 1] for j, v in enumerate(cmd) if v == "--build-info"
        ]
        assert sources_pairs == ["new=src"]
        assert build_info_pairs == ["new=build"]

    def test_policy_and_suppress_forwarded_unconditionally(self) -> None:
        # Unlike scan's own legacy branch (which only forwards these with a
        # resolved baseline), a real baseline is always present on this
        # path, so no gating condition is needed -- matches real
        # `mode: compare`'s own unconditional forwarding.
        cmd = _run_cmd(_base_env(INPUT_POLICY="security", INPUT_SUPPRESS="supp.yml"))
        idx = cmd.index("--policy")
        assert cmd[idx + 1] == "security"
        idx = cmd.index("--suppress")
        assert cmd[idx + 1] == "supp.yml"

    def test_require_complete_analysis_forwarded(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_REQUIRE_COMPLETE_ANALYSIS="true"))
        assert "--require-complete-analysis" in cmd

    def test_depth_since_changed_path_forwarded(self) -> None:
        cmd = _run_cmd(
            _base_env(
                INPUT_DEPTH="source",
                INPUT_SINCE="origin/main",
                INPUT_CHANGED_PATH="src/foo.c",
            )
        )
        idx = cmd.index("--depth")
        assert cmd[idx + 1] == "source"
        idx = cmd.index("--since")
        assert cmd[idx + 1] == "origin/main"
        idx = cmd.index("--changed-path")
        assert cmd[idx + 1] == "src/foo.c"

    def test_no_write_sidecar_injected_for_json_primary(self) -> None:
        # The primary format is already json, so there is nothing to
        # inject -- matches real `mode: compare`'s own identical guard.
        cmd = _run_cmd(_base_env(INPUT_PR_COMMENT="true"))
        assert "--write" not in cmd, cmd

    def test_dry_run_maps_to_dry_run_flag_no_output(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_DRY_RUN="true", INPUT_OUTPUT_FILE="out.json"))
        assert "--dry-run" in cmd
        assert "-o" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForUnmigratedCapabilities:
    """Every capability `compare` cannot reach yet keeps `mode: scan`
    invoking `abicheck scan` directly, unchanged."""

    def test_new_library_set_stays_on_scan(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "libs/",
                "INPUT_FORMAT": "json",
            }
        )
        assert cmd[1] == "scan", cmd

    def test_budget_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_BUDGET="15m"))
        assert cmd[1] == "scan", cmd

    def test_crosscheck_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_CROSSCHECK="private_header_leak=error"))
        assert cmd[1] == "scan", cmd

    def test_risk_rules_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_RISK_RULES="rules.yml"))
        assert cmd[1] == "scan", cmd

    def test_build_target_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_BUILD_TARGET="//:math"))
        assert cmd[1] == "scan", cmd

    def test_audit_only_no_against_stays_on_scan(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_FORMAT": "json",
            }
        )
        assert cmd[1] == "scan", cmd

    def test_audit_alias_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_AUDIT="true"))
        assert cmd[1] == "scan", cmd

    def test_non_json_format_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_FORMAT="text"))
        assert cmd[1] == "scan", cmd

    def test_default_format_stays_on_scan(self) -> None:
        env = _base_env()
        del env["INPUT_FORMAT"]
        cmd = _run_cmd(env)
        assert cmd[1] == "scan", cmd

    def test_directory_style_against_stays_on_scan(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "baseline_pkg"
        pkg_dir.mkdir()
        cmd = _run_cmd(_base_env(INPUT_AGAINST=str(pkg_dir)))
        assert cmd[1] == "scan", cmd
