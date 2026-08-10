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

"""Root composite Action (``action.yml``/``action/run.sh``) ``dump`` mode's
``snapshot-compression`` input (ADR-059).

Extracts ``run.sh``'s real dump-mode block verbatim (same "parse the real
file, don't hand-copy it" discipline as
``test_action_compile_context_parity.py``) and asserts the input maps to
``dump``'s ``--compression`` flag, mirroring the CLI option
(``cli_options.snapshot_compression_option``) it forwards to directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_DUMP_MODE_START = 'if [[ "$MODE" == "dump" ]]; then'
_DUMP_MODE_END = 'elif [[ "$MODE" == "compare" ]]; then'

_IS_RELEASE_STYLE_OPERAND_START = "_is_release_style_operand() {"
_IS_RELEASE_STYLE_OPERAND_END = "\n}\n"


def _is_release_style_operand_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_IS_RELEASE_STYLE_OPERAND_START)
    end = text.index(_IS_RELEASE_STYLE_OPERAND_END, start) + len(
        _IS_RELEASE_STYLE_OPERAND_END
    )
    return text[start:end]


def _dump_mode_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_DUMP_MODE_START)
    end = text.index(_DUMP_MODE_END, start)
    # The real file's if/elif chain continues past dump's own block with no
    # closing "fi" of its own -- cutting right before the "elif" leaves the
    # "if" unterminated, so the extracted fragment needs one added back.
    return text[start:end] + "fi\n"


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale (GitHub windows-latest runners resolve a bare "bash" to a
    non-functional WSL stub ahead of Git for Windows' real bash).
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


def _run_dump_region(env_extra: dict[str, str]) -> list[str]:
    # add_flag/add_single_flag are defined earlier in run.sh; redefine
    # minimal equivalents here since only the dump-mode region is
    # extracted, not the whole file (keeps the harness self-contained and
    # fast, same approach test_action_compile_context_parity.py uses).
    harness = (
        'add_flag() { local f="$1" v="$2"; [[ -n "$v" ]] && CMD+=("$f" "$v"); }\n'
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        + _is_release_style_operand_source()
        + "\nCMD=()\n"
    )
    script = harness + _dump_mode_region() + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    env = {**os.environ, "MODE": "dump", **env_extra}
    out = subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return out.stdout.splitlines()


class TestDumpSnapshotCompressionForwarding:
    def test_unset_input_omits_the_flag(self) -> None:
        """No snapshot-compression input set -> no --compression flag, so
        the CLI's own 'auto' default (infer from -o's suffix) applies,
        matching pre-ADR-059 behavior exactly."""
        cmd = _run_dump_region({"INPUT_NEW_LIBRARY": "old.so"})
        assert "--compression" not in cmd

    def test_explicit_value_forwarded(self) -> None:
        cmd = _run_dump_region(
            {
                "INPUT_NEW_LIBRARY": "old.so",
                "INPUT_SNAPSHOT_COMPRESSION": "zstd",
            }
        )
        assert "--compression" in cmd
        assert cmd[cmd.index("--compression") + 1] == "zstd"

    def test_gzip_value_forwarded(self) -> None:
        cmd = _run_dump_region(
            {
                "INPUT_NEW_LIBRARY": "old.so",
                "INPUT_SNAPSHOT_COMPRESSION": "gzip",
            }
        )
        assert "--compression" in cmd
        assert cmd[cmd.index("--compression") + 1] == "gzip"

    def test_dry_run_omits_output_file_and_compression(self) -> None:
        """dry-run performs no analysis and writes nothing, so -o/--output
        (and therefore --compression, which requires -o) are both skipped
        entirely -- mirroring the existing -o handling right above it."""
        cmd = _run_dump_region(
            {
                "INPUT_NEW_LIBRARY": "old.so",
                "INPUT_SNAPSHOT_COMPRESSION": "zstd",
                "INPUT_DRY_RUN": "true",
            }
        )
        assert "-o" not in cmd
        assert "--compression" not in cmd
