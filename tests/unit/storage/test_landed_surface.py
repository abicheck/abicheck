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

"""The plan's "Landed in Phase 0" table is the storage package's real surface.

Codex review found the table still advertising `VOLATILE_KEYS` after the
recursive volatile-key list was replaced by the root-only
`CAPTURE_METADATA_KEY` — so a contributor following the Phase 0 contract would
have targeted an API that no longer exists.

Hand-maintaining a doc table against code is the mechanism that failed, not
the one entry that went stale, so the table is checked instead. This mirrors
`tests/test_cli_root_surface.py`, which pins the root command set the same way
and for the same reason.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_PLAN = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contribute"
    / "plans"
    / "storage-format-v2.md"
)

_MODULES = (
    "abicheck.storage.availability",
    "abicheck.storage.identity",
    "abicheck.storage.canonical",
    "abicheck.storage.versioning",
)


def _table_rows() -> dict[str, set[str]]:
    """Parse the module -> advertised-names mapping out of the plan's table."""
    rows: dict[str, set[str]] = {}
    for line in _PLAN.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`(abicheck/storage/\w+\.py)`\s*\|(.*)\|\s*$", line)
        if not match:
            continue
        module = match.group(1).removesuffix(".py").replace("/", ".")
        rows[module] = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", match.group(2)))
    return rows


@pytest.mark.parametrize("module_name", _MODULES)
def test_the_table_matches_the_modules_public_surface(module_name: str) -> None:
    rows = _table_rows()
    assert module_name in rows, f"{module_name} has no row in the plan's table"

    exported = set(importlib.import_module(module_name).__all__)
    advertised = rows[module_name]

    assert advertised - exported == set(), (
        f"{module_name}'s row advertises names that do not exist: "
        f"{sorted(advertised - exported)}"
    )
    assert exported - advertised == set(), (
        f"{module_name}'s row omits exported names: {sorted(exported - advertised)}"
    )


def test_every_storage_module_has_a_row() -> None:
    """A new Phase 0 module must be advertised, not silently absent."""
    package = Path(importlib.import_module("abicheck.storage").__file__ or "").parent
    present = {
        f"abicheck.storage.{path.stem}"
        for path in package.glob("*.py")
        if path.stem != "__init__"
    }

    assert present == set(_MODULES) == set(_table_rows())
