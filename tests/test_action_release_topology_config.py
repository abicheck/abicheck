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

"""``action/run.sh``'s ``add_release_topology_config_flags`` (Phase 7d,
one-comparison-product.md §4.1, ADR-068 D5).

``compare``'s ``--dso-only``/``--include-private-dso``/
``--fail-on-removed-library`` are gone from the CLI entirely (CONFIG class,
no surviving override -- ``.abicheck.yml``'s ``release:``/``gate:`` blocks
are their only source now). This Action's own dso-only/include-private-dso/
fail-on-removed-library inputs still exist, so ``run.sh`` now synthesizes a
config overlay and forwards it via ``--config`` instead of the removed
flags -- the same pattern ``add_compile_context_flags`` already established
for the ``compile:`` block (``test_action_compile_context_parity.py``).

This is a narrower harness than that file's: it exercises the one new
function in isolation (extracted verbatim from the real script, not
hand-copied) rather than rebuilding the whole compile-context parity
matrix, since the new function's own contract is self-contained --
it reads only ``INPUT_DSO_ONLY``/``INPUT_INCLUDE_PRIVATE_DSO``/
``INPUT_FAIL_ON_REMOVED_LIBRARY``/``INPUT_BUILD_CONFIG`` and appends to a
bash ``CMD`` array, with no dependency on ``_is_release_style_operand`` or
any other run.sh state (the real call site already gates it to a
release-style operand before calling it).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_FN_START = "add_release_topology_config_flags() {"
_FN_END = "\n}\n"


def _add_release_topology_config_flags_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_FN_START)
    end = text.index(_FN_END, start) + len(_FN_END)
    return text[start:end]


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


def _run_bash_script(
    script: str, env: dict[str, str] | None = None, *, check: bool = False
) -> subprocess.CompletedProcess[Any]:
    """Run *script* via a real bash from a temp file (Windows argv-quoting
    safety, matching test_action_compile_context_parity.py's own
    _run_bash_script -- see that module's docstring for the exact reason)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            env=env,
            check=check,
            timeout=30,
        )
    finally:
        os.unlink(script_path)


def _harness(overlay_marker: Path) -> str:
    """A minimal ``run.sh``-shaped script: the real function body, a stub
    ``mktemp`` redirected to a caller-known path (so the test can read back
    the synthesized overlay without parsing bash array output), and a
    trailing call plus a dump of the resulting ``CMD`` array."""
    fn_source = _add_release_topology_config_flags_source()
    return f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare)
mktemp() {{ echo "{overlay_marker}"; }}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""


class TestReleaseTopologyOverlay:
    def test_no_inputs_leaves_cmd_untouched(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(_harness(overlay), env={})
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]
        assert not overlay.exists()

    def test_dso_only_synthesizes_release_block_and_config_flag(
        self, tmp_path: Path
    ) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay), env={"INPUT_DSO_ONLY": "true"}
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare", "--config", str(overlay)]
        doc = json.loads(overlay.read_text(encoding="utf-8"))
        assert doc == {"release": {"dso_only": True}}

    def test_include_private_dso_synthesizes_release_block(
        self, tmp_path: Path
    ) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay), env={"INPUT_INCLUDE_PRIVATE_DSO": "true"}
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(overlay.read_text(encoding="utf-8"))
        assert doc == {"release": {"include_private_dso": True}}

    def test_fail_on_removed_library_synthesizes_gate_block(
        self, tmp_path: Path
    ) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay), env={"INPUT_FAIL_ON_REMOVED_LIBRARY": "true"}
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(overlay.read_text(encoding="utf-8"))
        assert doc == {"gate": {"fail_on_removed_library": True}}

    def test_all_three_synthesize_one_combined_overlay(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay),
            env={
                "INPUT_DSO_ONLY": "true",
                "INPUT_INCLUDE_PRIVATE_DSO": "true",
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        doc = json.loads(overlay.read_text(encoding="utf-8"))
        assert doc == {
            "release": {"dso_only": True, "include_private_dso": True},
            "gate": {"fail_on_removed_library": True},
        }
        # Exactly one --config flag -- not one per synthesized block.
        assert result.stdout.count("--config") == 1

    def test_build_config_together_with_dso_only_fails_loud(
        self, tmp_path: Path
    ) -> None:
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay),
            env={"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": "/repo/.abicheck.yml"},
        )
        assert result.returncode == 1
        assert "cannot combine" in result.stdout
        assert "release:/gate:" in result.stdout
        assert not overlay.exists()

    def test_build_config_alone_is_fine(self, tmp_path: Path) -> None:
        """The mutual-exclusivity guard only fires when a release-topology
        input is also set -- build-config alone (no dso-only/
        include-private-dso/fail-on-removed-library) is the ordinary case
        every ADR-037 D4 project config already exercises."""
        overlay = tmp_path / "overlay.yml"
        result = _run_bash_script(
            _harness(overlay), env={"INPUT_BUILD_CONFIG": "/repo/.abicheck.yml"}
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]
        assert not overlay.exists()

    def test_double_config_bug_guard_fires_loud_not_silent(
        self, tmp_path: Path
    ) -> None:
        """A defensive check inside the function itself: if CMD already
        carries --config (a caller bug, not a user input problem), the
        function refuses to add a second one rather than emitting an
        invalid two---config command line silently."""
        overlay = tmp_path / "overlay.yml"
        fn_source = _add_release_topology_config_flags_source()
        script = f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare --config /already/there.yml)
mktemp() {{ echo "{overlay}"; }}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""
        result = _run_bash_script(script, env={"INPUT_DSO_ONLY": "true"})
        assert result.returncode == 1
        assert "already added to the command line" in result.stdout
