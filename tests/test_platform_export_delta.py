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

"""The raw PE/Mach-O export delta never reads an unread table as empty.

A default (never parsed) ``PeMetadata()``/``MachoMetadata()`` on either side
reported every export of the other side as removed or added -- an identical
pair came back BREAKING (F1, design-hardening Phase 1). Stated over every
platform x unread side x export shape, through ``compare()``, against an
oracle that counts the export findings directly.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest
from _family_f1_harness import CORPUS

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.extract.export_table_read import finish_binary_snapshot
from abicheck.model import AbiSnapshot
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata

_EXPORT_KINDS = {
    ChangeKind.FUNC_REMOVED,
    ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ChangeKind.FUNC_ADDED,
}


def _names(s: AbiSnapshot) -> list[str]:
    assert s.elf is not None
    return [x.name for x in s.elf.symbols]


def _on(platform: str, s: AbiSnapshot, names: list[str], *, read: bool) -> AbiSnapshot:
    if platform == "pe":
        block = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(names)],
        )
        changes = {"pe": block if read else PeMetadata(), "library": "x.dll"}
    else:
        block = MachoMetadata(
            cpu_type="ARM64",
            filetype="MH_DYLIB",
            install_name="@rpath/libx.1.dylib",
            exports=[MachoExport(name=n) for n in names],
        )
        changes = {
            "macho": block
            if read
            else MachoMetadata(install_name="@rpath/libx.1.dylib"),
            "library": "libx.1.dylib",
        }
    return finish_binary_snapshot(
        dataclasses.replace(s, elf=None, platform=platform, **changes)
    )


def _export_findings(old: AbiSnapshot, new: AbiSnapshot) -> set[tuple[str, str]]:
    return {
        (c.kind.value, c.symbol)
        for c in compare(old, new).changes
        if c.kind in _EXPORT_KINDS and "export" in (c.description or "")
    }


@pytest.mark.parametrize(
    ("platform", "unread"),
    list(itertools.product(["pe", "macho"], ["old", "new", "both"])),
)
def test_unread_table_yields_no_export_delta(platform: str, unread: str) -> None:
    for case, (o, n) in CORPUS.items():
        old = _on(platform, o, _names(o), read=unread not in ("old", "both"))
        new = _on(platform, n, _names(n), read=unread not in ("new", "both"))
        assert _export_findings(old, new) == set(), (case, platform, unread)
    o, n = CORPUS["identical"]
    old = _on(platform, o, _names(o), read=unread not in ("old", "both"))
    new = _on(platform, n, _names(n), read=unread not in ("new", "both"))
    # Not NO_CHANGE: an unread side is reduced assurance, stated as a risk --
    # but never a break.
    assert compare(old, new).verdict.value not in ("BREAKING", "API_BREAK")


@pytest.mark.parametrize("platform", ["pe", "macho"])
def test_read_tables_still_report_an_undeclared_export_removal(platform: str) -> None:
    """Negative control: with both tables read, an export no declaration
    carries is still reported removed (and one only NEW carries, added)."""
    o, n = CORPUS["identical"]
    old = _on(platform, o, [*_names(o), "orphan_old"], read=True)
    new = _on(platform, n, [*_names(n), "orphan_new"], read=True)
    assert _export_findings(old, new) == {
        ("func_removed", "orphan_old"),
        ("func_added", "orphan_new"),
    }


@pytest.mark.parametrize("platform", ["pe", "macho"])
def test_removed_variable_export_is_not_re_reported_as_a_function(
    platform: str,
) -> None:
    o, n = CORPUS["var_removed"]
    old = _on(platform, o, _names(o), read=True)
    new = _on(platform, n, _names(n), read=True)
    kinds = {(c.kind.value, c.symbol) for c in compare(old, new).changes}
    assert ("var_removed", "_ZL7g_count") in kinds
    assert ("func_removed", "_ZL7g_count") not in kinds
