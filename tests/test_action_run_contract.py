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

"""Contract test: every CLI flag the composite Action passes must be a real
option of the subcommand it targets.

Why this exists: a bug shipped where ``action/run.sh`` forwarded the
``build-config`` input as ``--build-config`` to ``abicheck scan``, but ``scan``
only accepts ``--config`` — so any scan run with a config hard-failed with
exit 64. It slipped through because *no* action test exercised scan with a
config input. This test is the generalized guard: it parses the command-assembly
region of ``run.sh``, groups the long flags by the subcommand each mode builds,
and asserts every one is present in that subcommand's ``--help``. It fails on any
action→CLI flag drift, for every mode, not just the one that broke.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

# Long options that are VALID but hidden from `--help` (deprecated, kept working)
# — the action may still forward them. Keyed by the subcommand path.
_HIDDEN_VALID = {
    "scan": {"--mode", "--source-method"},  # deprecated → --depth (ADR-037 D5)
}

# add_flag "--x" / add_single_flag "--x"  and  CMD+=(--x ...)
_ADD_FLAG_RE = re.compile(r'add(?:_single)?_flag\s+"(--[a-z0-9-]+)"')
_CMD_FLAG_RE = re.compile(r'CMD\+=\((--[a-z0-9-]+)')
# The subcommand a branch builds: CMD+=(dump) / CMD+=(deps tree) / CMD+=(deps
# compare) — capture one or two bare words (never a "$VAR" or a --flag).
_CMD_SUBCMD_RE = re.compile(r'CMD\+=\(([a-z][a-z-]*(?:\s+[a-z][a-z-]*)?)\)')
_KNOWN_SUBCOMMANDS = {
    "dump", "compare", "appcompat", "deps tree", "deps compare", "scan", "merge",
}


def _flags_by_subcommand() -> dict[str, set[str]]:
    """Parse run.sh's assembly region → {subcommand-path: {--flags it passes}}.

    The subcommand key is the full `CMD+=(...)` path (e.g. ``deps tree``), so a
    group command is checked against the right leaf's ``--help``.
    """
    assert RUN_SH.is_file(), f"missing {RUN_SH}"
    current_sub: str | None = None
    by_sub: dict[str, set[str]] = {}
    # run.sh carries UTF-8 box-drawing chars; force UTF-8 so the default Windows
    # cp1252 codec doesn't raise UnicodeDecodeError.
    for line in RUN_SH.read_text(encoding="utf-8").splitlines():
        m_sub = _CMD_SUBCMD_RE.search(line)
        if m_sub and m_sub.group(1) in _KNOWN_SUBCOMMANDS:
            current_sub = m_sub.group(1)
            by_sub.setdefault(current_sub, set())
        if current_sub is None:
            continue
        for rx in (_ADD_FLAG_RE, _CMD_FLAG_RE):
            for fm in rx.finditer(line):
                by_sub[current_sub].add(fm.group(1))
    return by_sub


def _valid_flags(subcommand: str) -> set[str]:
    """The set of long options the real CLI accepts for *subcommand*."""
    parts = subcommand.split()  # e.g. "stack-check" stays one token
    # rich-click renders help with UTF-8 box-drawing chars. On Windows the child
    # would otherwise encode stdout as cp1252 and crash (exit 1) on those chars,
    # so force UTF-8 in the child too — not just in our decode.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run(
        [sys.executable, "-m", "abicheck", *parts, "--help"],
        capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", env=env,
    ).stdout
    # rich-click wraps help lines, so a flag can be split across the box; join
    # first, then scoop every "--flag" token.
    joined = out.replace("\n", " ")
    return set(re.findall(r"--[a-z0-9][a-z0-9-]+", joined))


def test_run_sh_parses_into_known_subcommands() -> None:
    by_sub = _flags_by_subcommand()
    # Sanity: we actually found the command branches (not a broken parse).
    assert {"dump", "compare", "scan", "merge", "appcompat", "deps tree"} <= set(by_sub)
    assert by_sub["scan"], "no flags parsed for scan — parser drifted"


@pytest.mark.parametrize("subcommand", sorted(_KNOWN_SUBCOMMANDS))
def test_action_flags_are_real_cli_options(subcommand: str) -> None:
    """Every --flag the action passes for a mode is accepted by its subcommand.

    This would have caught the ``scan --build-config`` regression (a flag scan
    does not define) and fails on any future action↔CLI drift, per subcommand.
    """
    by_sub = _flags_by_subcommand()
    used = by_sub.get(subcommand, set())
    if not used:
        pytest.skip(f"no flags collected for {subcommand}")
    valid = _valid_flags(subcommand) | _HIDDEN_VALID.get(subcommand, set())
    always_ok = {"--help", "--verbose", "--version"}
    unknown = {f for f in used if f not in valid and f not in always_ok}
    assert not unknown, (
        f"action/run.sh passes {sorted(unknown)} to `abicheck {subcommand}`, "
        f"which does not accept them (would exit 64). Valid options include: "
        f"{sorted(valid)[:12]}…"
    )
