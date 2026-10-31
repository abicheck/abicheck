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

"""Every RETIRED Action input reaches its tombstone check under its own name.

A tombstone (ADR-091 D7, no transition period) only fails closed when the
validator's ``INPUT_*`` variable carries the *retired* input's value. If the
step's ``env:`` maps that variable to another input -- the live replacement,
say -- the tombstone never fires for a caller who still sets the old name, and
a caller who sets the *new* name is refused as if they had used the old one.
ADR-091 Phase 5 shipped exactly that once (``INPUT_CONSUMER_GCC_PATH`` read
``consumer-compiler``).

The oracle is the validator's own tombstone table, parsed independently of the
Action's ``env:`` wiring: every ``name:VAR`` / ``name|VAR`` row must be mapped
as ``VAR: ${{ inputs.name }}`` in every step that sets ``VAR``, at least one
step must set it, and ``name`` must still be a declared input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from _yaml_fast import safe_load

ROOT = Path(__file__).resolve().parents[1]

#: (action.yml, its validator, the tombstone arrays in that validator)
ACTIONS = [
    (
        ROOT / "action.yml",
        ROOT / "action" / "validate-inputs.sh",
        ("_retired_to_set", "_renamed_inputs"),
    ),
    (
        ROOT / "actions" / "check-target" / "action.yml",
        ROOT / "actions" / "check-target" / "validate-inputs.sh",
        ("_renamed_inputs",),
    ),
]

#: Root-Action tombstones checked with their own `if` rather than a table row.
ROOT_SINGLE_TOMBSTONES = [
    ("jobs", "INPUT_JOBS"),
    ("allow-build-query", "INPUT_ALLOW_BUILD_QUERY"),
    ("public-header-dirs", "INPUT_PUBLIC_HEADER_DIRS"),
]

_ROW = re.compile(r'^\s*"([a-z0-9-]+)[:|](INPUT_[A-Z0-9_]+)[:|]', re.MULTILINE)


def _table_rows(validator: Path, array: str) -> list[tuple[str, str]]:
    text = validator.read_text(encoding="utf-8")
    start = text.index(f"{array}=(")
    body = text[start : text.index("\n)", start)]
    rows = _ROW.findall(body)
    assert rows, f"{validator.name}: {array} has no rows"
    return rows


def _env_blocks(node: Any) -> list[dict[str, Any]]:
    """Every ``env:`` mapping under *node* (steps, jobs, the runs block)."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        env = node.get("env")
        if isinstance(env, dict):
            found.append(env)
        for value in node.values():
            found.extend(_env_blocks(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_env_blocks(item))
    return found


def _cases() -> list[Any]:
    cases = []
    for action, validator, arrays in ACTIONS:
        rows = [row for array in arrays for row in _table_rows(validator, array)]
        if action.parent == ROOT:
            rows += ROOT_SINGLE_TOMBSTONES
        for name, var in rows:
            label = f"{action.relative_to(ROOT)}:{name}"
            cases.append(pytest.param(action, name, var, id=label))
    return cases


@pytest.mark.parametrize(("action", "name", "var"), _cases())
def test_tombstone_variable_reads_the_retired_input(
    action: Path, name: str, var: str
) -> None:
    document = safe_load(action.read_text(encoding="utf-8"))
    assert name in document["inputs"], f"{name} is no longer a declared input"
    values = [env[var] for env in _env_blocks(document["runs"]) if var in env]
    assert values, f"no step maps {var}, so the {name} tombstone never fires"
    assert set(values) == {"${{ inputs." + name + " }}"}, values


def test_the_table_parser_reads_every_row() -> None:
    """The parser is the oracle, so pin it against the rows a reader sees."""
    rows = _table_rows(
        ROOT / "actions" / "check-target" / "validate-inputs.sh", "_renamed_inputs"
    )
    assert ("consumer-gcc-path", "INPUT_CONSUMER_GCC_PATH") in rows
    assert ("requested-depth", "INPUT_REQUESTED_DEPTH") in rows
    root = _table_rows(ROOT / "action" / "validate-inputs.sh", "_retired_to_set")
    assert [name for name, _ in root] == [
        "dso-only",
        "include-private-dso",
        "fail-on-removed-library",
        "frontend",
        "compile-options",
        "nostdinc",
    ]
