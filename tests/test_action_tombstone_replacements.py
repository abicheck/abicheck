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

"""Every RETIRED Action input's replacement hint actually works.

A tombstone fails the run and tells the workflow author what to write instead.
That advice is only useful if following it succeeds. ADR-091 Phase 5 shipped
three tombstones whose ``set:`` line ``compare --set`` refused with exit 64, one
whose ``set:`` key was not a header root at all, and three renames that pointed
at an input Phase 5 itself retired.

The oracle is the rejection the validator actually prints, executed per input,
not the hint text in this file. Each hint must be one of:

* a ``set: PATH=VALUE`` line that ``abicheck compare --dry-run --set`` accepts
  on both the single-pair and the directory/package route, or
* an input name that ``action.yml`` declares and does not itself retire.

An ADR-091 tombstone (the "RETIRED and no longer accepted" wording) must give
at least one such hint or state that there is no replacement.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_action_retired_inputs_contract import (
    RETIRED_INPUTS,
    VALIDATE_SH,
    _input_env_var,
    _meaningful_value,
    _run_script,
)

from abicheck.cli import main

_SET_HINT = re.compile(r"`set: ([a-z_.]+)=([^`]*)`")
_RENAMED_HINT = re.compile(r"renamed to ([a-z][a-z0-9-]*)")
_INPUT_HINT = re.compile(r"`([a-z][a-z0-9-]*): [^`]*`")

#: A concrete value for each placeholder a hint may show.
_PLACEHOLDERS = {
    "<value>": "clang",
    "[<dir>, ...]": "[include]",
    "[<flag>, ...]": "[-DX=1]",
}


def _rejection(name: str) -> str:
    result = _run_script(
        VALIDATE_SH,
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            _input_env_var(name): _meaningful_value(RETIRED_INPUTS[name]),
        },
    )
    lines = [line for line in result.stdout.splitlines() if "::error::" in line]
    assert result.returncode != 0 and lines, result.stdout + result.stderr
    return lines[0]


_MESSAGES = {name: _rejection(name) for name in sorted(RETIRED_INPUTS)}


def _set_hints() -> list[tuple[str, str, str]]:
    hints = []
    for name, message in _MESSAGES.items():
        for path, value in _SET_HINT.findall(message):
            hints.append((name, path, _PLACEHOLDERS.get(value, value)))
    return hints


def _input_hints() -> list[tuple[str, str]]:
    hints = []
    for name, message in _MESSAGES.items():
        found = set(_RENAMED_HINT.findall(message)) | {
            hint for hint in _INPUT_HINT.findall(message) if hint != "set"
        }
        hints.extend((name, hint) for hint in sorted(found))
    return hints


SET_HINTS = _set_hints()
INPUT_HINTS = _input_hints()


def test_the_hint_parsers_find_the_known_shapes() -> None:
    """Vacuity guard: the parametrized checks below see each hint shape."""
    assert ("dso-only", "release.dso_only", "true") in SET_HINTS
    assert ("frontend", "compile.frontend", "clang") in SET_HINTS
    assert ("public-header-dirs", "header") in INPUT_HINTS
    assert ("gcc-path", "compiler") in INPUT_HINTS


@pytest.mark.parametrize(
    ("name", "path", "value"),
    SET_HINTS,
    ids=[f"{name}->{path}" for name, path, _ in SET_HINTS],
)
@pytest.mark.parametrize("route", ["single-pair", "directory"])
def test_a_set_hint_is_accepted_by_compare(
    tmp_path: Path, name: str, path: str, value: str, route: str
) -> None:
    if route == "directory":
        old, new = tmp_path / "old", tmp_path / "new"
        old.mkdir()
        new.mkdir()
    else:
        old = new = Path(__file__)
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--dry-run", "--set", f"{path}={value}"],
    )
    assert result.exit_code == 0, (
        f"{name}'s tombstone tells the author to write `set: {path}=...`, "
        f"but compare refuses it on the {route} route:\n{result.output}"
    )


@pytest.mark.parametrize(
    ("name", "hint"), INPUT_HINTS, ids=[f"{n}->{h}" for n, h in INPUT_HINTS]
)
def test_an_input_hint_names_a_live_input(name: str, hint: str) -> None:
    from test_action_run_contract import _action_yml_inputs

    assert hint in _action_yml_inputs(), f"{name} points at undeclared input {hint!r}"
    assert hint not in RETIRED_INPUTS, (
        f"{name}'s tombstone points at {hint!r}, which is itself RETIRED"
    )


#: The ADR-091 tombstones; older ones (mode: scan era) give free-form guidance.
ADR091_TOMBSTONES = sorted(
    name for name, message in _MESSAGES.items() if "no longer accepted" in message
)


#: The inputs ADR-091 Phase 5 retired; each must be covered by name.
PHASE5_RETIRED = {
    "dso-only",
    "include-private-dso",
    "fail-on-removed-library",
    "public-header-dirs",
    "frontend",
    "compile-options",
    "nostdinc",
    "jobs",
    "allow-build-query",
}


def test_every_phase5_retirement_is_checked_here() -> None:
    assert PHASE5_RETIRED <= set(ADR091_TOMBSTONES)
    assert {"gcc-path", "ast-frontend"} <= set(ADR091_TOMBSTONES)


@pytest.mark.parametrize("name", ADR091_TOMBSTONES)
def test_an_adr091_tombstone_gives_a_replacement_or_says_there_is_none(
    name: str,
) -> None:
    message = _MESSAGES[name]
    hinted = any(n == name for n, *_ in SET_HINTS) or any(
        n == name for n, _ in INPUT_HINTS
    )
    assert hinted or "no replacement" in message, message


@pytest.mark.parametrize("name", ["nostdinc", "allow-build-query"])
@pytest.mark.parametrize("value", ["false", "true"])
def test_a_boolean_tombstone_fails_on_any_supplied_value(name: str, value: str) -> None:
    """These inputs carry no default, so an omitted input arrives empty and
    any supplied value -- an explicit ``false`` too -- is a workflow still
    naming a retired input, refused rather than passed silently."""
    assert "default" not in RETIRED_INPUTS[name]
    result = _run_script(
        VALIDATE_SH,
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            _input_env_var(name): value,
        },
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert name in result.stdout
