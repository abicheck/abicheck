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
import yaml

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"

# add_flag "--x" / add_single_flag "--x"  and  CMD+=(--x ...)
_ADD_FLAG_RE = re.compile(r'add(?:_single)?_flag\s+"(--[a-z0-9-]+)"')
_CMD_FLAG_RE = re.compile(r'CMD\+=\((--[a-z0-9-]+)')
# The subcommand a branch builds: CMD+=(dump) / CMD+=(deps tree) / CMD+=(deps
# compare) — capture one or two bare words (never a "$VAR" or a --flag).
_CMD_SUBCMD_RE = re.compile(r'CMD\+=\(([a-z][a-z-]*(?:\s+[a-z][a-z-]*)?)\)')
_KNOWN_SUBCOMMANDS = {
    "dump", "compare", "deps tree", "deps compare", "scan",
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
    # `compare --help` only shows its curated common subset (G21.8 collapse
    # M2); `--help-all` is the full surface and is what this test needs to
    # validate action/run.sh's flags against. Other subcommands don't have
    # the curated/full split, so they keep plain --help.
    help_flag = "--help-all" if subcommand == "compare" else "--help"
    out = subprocess.run(
        [sys.executable, "-m", "abicheck", *parts, help_flag],
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
    # `merge`/`appcompat` modes are gone (ADR-043: folded into compare
    # --used-by / automatic dump/compare ingestion); `deps tree` covers the
    # dump mode's stack-check/deps dispatch.
    assert {"dump", "compare", "scan", "deps tree"} <= set(by_sub)
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
    valid = _valid_flags(subcommand)
    always_ok = {"--help", "--verbose", "--version"}
    unknown = {f for f in used if f not in valid and f not in always_ok}
    assert not unknown, (
        f"action/run.sh passes {sorted(unknown)} to `abicheck {subcommand}`, "
        f"which does not accept them (would exit 64). Valid options include: "
        f"{sorted(valid)[:12]}…"
    )


# ─────────────────────────────────────────────────────────────────────────
# action.yml `inputs:` ↔ "Run abicheck" step env ↔ run.sh `INPUT_*` wiring
#
# The whole action↔CLI bridge is stringly-typed by construction (a YAML
# input name → an env var name → a bash variable read), and nothing in
# GitHub Actions itself checks that the three spellings stay in sync: a
# renamed/typo'd input silently stops reaching run.sh (the flag is just
# never set, no error), and a stale INPUT_* read in run.sh silently never
# fires. Three inputs (python-version, install-deps, upload-sarif) are
# legitimately consumed by *other* steps in action.yml, not by run.sh — they
# are the documented exception, not a gap.
# ─────────────────────────────────────────────────────────────────────────

_ENV_TO_INPUT_RE = re.compile(
    r"^\s*(INPUT_[A-Z0-9_]+|GH_TOKEN):\s*\$\{\{\s*inputs\.([a-zA-Z0-9_-]+)\s*\}\}",
    re.MULTILINE,
)
# Declared inputs consumed by a step other than "Run abicheck" (setup-python,
# the conditional install-deps.sh step, the conditional upload-sarif step) —
# these have no INPUT_* counterpart in run.sh by design.
_NON_RUN_SH_INPUTS = {"python-version", "install-deps", "upload-sarif"}


def _action_yml_inputs() -> set[str]:
    with ACTION_YML.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return set(data["inputs"].keys())


def _action_yml_env_mapping() -> dict[str, str]:
    """{ENV_VAR_NAME: dashed-input-name} from the "Run abicheck" step's env block."""
    text = ACTION_YML.read_text(encoding="utf-8")
    return {var: inp for var, inp in _ENV_TO_INPUT_RE.findall(text)}


def test_every_action_input_is_wired_to_run_sh() -> None:
    """A declared action.yml input must reach run.sh (or be a documented
    other-step exception) — otherwise setting it from a workflow is a silent
    no-op."""
    declared = _action_yml_inputs()
    mapped = set(_action_yml_env_mapping().values())
    unwired = declared - mapped - _NON_RUN_SH_INPUTS
    assert not unwired, (
        f'action.yml declares {sorted(unwired)} but the "Run abicheck" step\'s '
        f"env block never forwards them (INPUT_X: ${{{{ inputs.x }}}}) — setting "
        f"them from a workflow would silently do nothing. Add the env line, or "
        f"add to _NON_RUN_SH_INPUTS if another step legitimately consumes it."
    )


def test_no_stale_action_yml_env_entries() -> None:
    """Every env var the "Run abicheck" step sets must map to a real declared
    input — catches a stale/renamed entry left behind after an input rename."""
    declared = _action_yml_inputs()
    mapping = _action_yml_env_mapping()
    stale = {var: inp for var, inp in mapping.items() if inp not in declared}
    assert not stale, (
        f"action.yml's env block references undeclared input(s): {stale} — "
        f"likely a leftover from a renamed/removed `inputs:` entry."
    )


def test_every_run_sh_input_var_is_set_by_action_yml() -> None:
    """Every INPUT_* run.sh actually reads must be set by action.yml's env
    block — catches a typo'd env var name (silently reads unset/empty)."""
    env_vars = set(_action_yml_env_mapping().keys()) - {"GH_TOKEN"}
    run_sh_text = RUN_SH.read_text(encoding="utf-8")
    used = set(re.findall(r"INPUT_[A-Z0-9_]+", run_sh_text))
    unset = used - env_vars
    assert not unset, (
        f"action/run.sh reads {sorted(unset)}, which action.yml's \"Run "
        f'abicheck" step never sets — these always read as unset/empty '
        f"(likely a typo against the declared env var name)."
    )
