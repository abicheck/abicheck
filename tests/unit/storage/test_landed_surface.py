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

import ast
import importlib
import re
from pathlib import Path

import pytest
from adr062_scope import adr062_module_paths

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
    "abicheck.storage.package",
    "abicheck.storage.dto",
    "abicheck.storage.import_v1",
)

#: Modules the package deliberately does not re-export. They are still
#: advertised in the plan's table and still have their surface pinned below —
#: "internal" is a statement about who imports it, not a licence to be absent
#: from the documented surface.
_INTERNAL_MODULES = (
    "abicheck.storage.guards",
    "abicheck.storage.availability_status",
    "abicheck.storage.entity_ids",
    "abicheck.storage.fact_availability",
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


@pytest.mark.parametrize("module_name", _MODULES + _INTERNAL_MODULES)
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
    present = {f"abicheck.storage.{path.stem}" for path in adr062_module_paths()}

    assert present == set(_MODULES) | set(_INTERNAL_MODULES) == set(_table_rows())


def test_the_package_reexports_exactly_the_modules_public_surface() -> None:
    """The package's `__all__` is the union of its modules', in sorted order.

    Same mechanism as the plan table above, one layer in: a hand-maintained
    list of names beside a set of modules that moved four times during review.
    It had already drifted — `UNSTATED_VERSION` was exported by
    `versioning` and absent here, so a consumer reading
    `StorageVersions.package_format_version == 0` had no name for what that
    zero means — and the ordering had drifted too (CodeRabbit review).

    Sorted is asserted rather than merely preferred because an unsorted list
    is how the missing entry stayed invisible: with no order to violate,
    nothing about the list looked wrong.
    """
    package = importlib.import_module("abicheck.storage")
    # `_INTERNAL_MODULES` is excluded on purpose: `guards` holds the doors'
    # own instruments, which no consumer of this package calls.
    union: set[str] = set()
    for module_name in _MODULES:
        union |= set(importlib.import_module(module_name).__all__)

    assert set(package.__all__) == union, (
        f"package re-export drift: missing {sorted(union - set(package.__all__))}, "
        f"extra {sorted(set(package.__all__) - union)}"
    )
    assert list(package.__all__) == sorted(package.__all__)
    for name in package.__all__:
        assert hasattr(package, name), f"{name} is in __all__ but not importable"


def test_no_docstring_carries_a_lone_surrogate() -> None:
    """A docstring is source text, and source text has to survive being encoded.

    `semantic_digest`'s docstring quoted the surrogate-escaped path this
    module handles — in a non-raw string, so the escape became a real lone
    surrogate in `__doc__`. Nothing on Linux minded. On macOS the storage
    package failed to import at all and took every test in this directory
    with it, and coverage could not parse the file either.

    The failure is a property of the character, not of the module that
    happens to hold it, so this checks the whole package rather than the one
    docstring that had it. Documenting an escape means writing it escaped.
    """
    package = Path(importlib.import_module("abicheck.storage").__file__ or "").parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            for index, char in enumerate(doc):
                if 0xD800 <= ord(char) <= 0xDFFF:
                    name = getattr(node, "name", "<module>")
                    offenders.append(f"{path.name}:{name} at {index} ({char!r})")

    assert offenders == [], (
        "a lone surrogate in a docstring cannot be encoded to UTF-8, and the "
        f"module carrying one may fail to import: {offenders}"
    )


def test_every_excluded_module_actually_exists() -> None:
    """The exclusion list cannot rot into a silent blanket.

    `NON_ADR062_MODULES` is what lets a module escape every ADR-062 sweep in
    this directory, so a stale name in it is an exemption nobody is watching
    — and if a G40 module is ever renamed or removed, the entry that named it
    would go on quietly excusing nothing while looking deliberate.
    """
    from adr062_scope import NON_ADR062_MODULES, STORAGE_PACKAGE

    present = {path.stem for path in STORAGE_PACKAGE.glob("*.py")}
    stale = NON_ADR062_MODULES - present
    assert stale == set(), (
        f"these modules are excluded from the ADR-062 sweeps but do not exist: "
        f"{sorted(stale)}"
    )
