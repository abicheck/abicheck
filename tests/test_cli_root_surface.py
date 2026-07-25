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

"""Root command-surface behavior tests (ADR-043).

The pre-1.0 CLI reset requires the public root surface to show *exactly*
``dump``, ``compare``, ``scan``, ``deps``, ``compat`` — plus ``aggregate``
(the multi-target CI fan-in gate), ``build-output`` (the G30 P1.1
``build-output.json`` validator group), ``project-targets`` (the G30
P1.5 ``targets:``/``bundles:``/``profiles:``/``baseline:`` validator group),
and ``run-plan`` (the G30 P1.4 run-plan generator group), all added
afterward — with no hidden aliases, and no deprecated shims for
the deleted commands
(``appcompat``, ``plugin-check``, ``baseline``, ``collect``, ``merge``,
``recommend-collect-mode``, ``debian-symbols``, ``doctor``, ``config``,
``init``, ``surface-report``, ``pr-comment``, ``suggest-suppressions``,
``probe``). This module pins that contract as an executable behavior test,
distinct from ``test_cli_surface_diff.py`` (which exercises the
CLI-surface-dump scripts used by the CI gate) and ``test_cli_contract.py``
(the Tier-2 chokepoint gate).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from click.testing import CliRunner

from abicheck.cli import main

_PUBLIC_COMMANDS = frozenset(
    {
        "dump",
        "compare",
        "scan",
        "deps",
        "compat",
        "aggregate",
        "build-output",
        "project-targets",
        "run-plan",
    }
)

_REMOVED_COMMANDS = (
    "appcompat",
    "plugin-check",
    "baseline",
    "collect",
    "merge",
    "recommend-collect-mode",
    "debian-symbols",
    "doctor",
    "config",
    "init",
    "surface-report",
    "pr-comment",
    "suggest-suppressions",
    "probe",
)


def test_root_surface_is_exactly_the_public_commands() -> None:
    assert set(main.commands) == _PUBLIC_COMMANDS


def test_no_hidden_commands_remain() -> None:
    """No registered command is marked hidden — there are no old-name aliases
    quietly kept around for back-compat."""
    for name, cmd in main.commands.items():
        assert not cmd.hidden, f"{name!r} is hidden; ADR-043 forbids CLI aliases"


@pytest.mark.parametrize("removed", _REMOVED_COMMANDS)
def test_removed_command_is_a_usage_error(removed: str) -> None:
    """Every deleted command produces a plain Click 'No such command' usage
    error (exit 64), not a deprecation shim or a different failure mode."""
    result = CliRunner().invoke(main, [removed, "--help"])
    assert result.exit_code == 64, (
        f"`abicheck {removed}` exited {result.exit_code}, expected 64 (usage "
        f"error / No such command). Output: {result.output!r}"
    )
    assert "no such command" in result.output.lower()


def test_help_shows_exactly_the_public_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in _PUBLIC_COMMANDS:
        assert cmd in result.output, f"{cmd!r} missing from `abicheck --help`"
    for removed in _REMOVED_COMMANDS:
        # Word-boundary-free substring check is deliberately strict: even a
        # removed command appearing as a fragment of unrelated help text would
        # be worth a maintainer's attention.
        assert removed not in result.output, (
            f"deleted command {removed!r} still mentioned in `abicheck --help`"
        )


def test_help_groups_commands_by_role() -> None:
    """Root help groups the verbs into role panels (rich-click COMMAND_GROUPS):
    core-analysis verbs, `aggregate` under workflow composition, `compat` under
    legacy — not one flat list. Falls back cleanly when rich-click is absent."""
    pytest.importorskip("rich_click")
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    # The panels render (grouping active) and aggregate is presented as workflow
    # composition, not a sixth core-analysis peer.
    assert "Core analysis" in result.output
    assert "Workflow composition" in result.output
    # Every public command is still reachable in the help regardless of panel.
    for cmd in _PUBLIC_COMMANDS:
        assert cmd in result.output


def test_python_dash_m_abicheck_shows_public_commands() -> None:
    """``python -m abicheck --help`` (the documented entry point) surfaces the
    same public commands as in-process ``main.commands`` introspection."""
    result = subprocess.run(
        [sys.executable, "-m", "abicheck", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for cmd in _PUBLIC_COMMANDS:
        assert cmd in result.stdout, f"{cmd!r} missing from `python -m abicheck --help`"


def test_deps_compare_rejects_old_flag_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``deps compare``'s pre-reset ``--baseline``/``--candidate`` spellings
    (renamed to ``--old-root``/``--new-root``) are gone, not kept as aliases."""
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    old_root.mkdir()
    new_root.mkdir()
    for flag in ("--baseline", "--candidate"):
        result = CliRunner().invoke(
            main,
            [
                "deps",
                "compare",
                "usr/bin/myapp",
                flag,
                str(old_root),
                "--old-root",
                str(old_root),
                "--new-root",
                str(new_root),
            ],
        )
        assert result.exit_code == 64, f"{flag} was unexpectedly accepted"
        assert "no such option" in result.output.lower()


def test_python_dash_m_abicheck_cli_matches_python_dash_m_abicheck() -> None:
    """``python -m abicheck.cli --help`` (a common typo/alternative spelling)
    must show the identical command set as ``python -m abicheck --help`` —
    a past bug made the sibling-module commands silently vanish under this
    invocation path (see test_main_entrypoint.py)."""
    via_package = subprocess.run(
        [sys.executable, "-m", "abicheck", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    via_module = subprocess.run(
        [sys.executable, "-m", "abicheck.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    for cmd in _PUBLIC_COMMANDS:
        assert cmd in via_package
        assert cmd in via_module
