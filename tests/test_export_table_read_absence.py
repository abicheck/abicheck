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

"""Evidence-entity-model gap A1: an export fact is decided from the read.

``binary_exported_fact`` used to be ``PRESENT(False)`` for every declaration
an export lookup missed whenever a binary was supplied -- including when the
table came back empty because the container had none or its parse failed
(``parse_pe_metadata``/``parse_macho_metadata`` return empty metadata on
error). ``is_export_confirmed_absent`` readers then acted on a table nobody
read.

The oracle below is a hand-written truth table over the five read states,
checked for every producer (castxml, clang, DWARF), independently of the
``ExportTableState`` helpers the implementation uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.export_index import ExportMatch
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.model.surface_facts import is_export_confirmed_absent
from abicheck.serialization import save_snapshot, snapshot_from_dict, snapshot_to_dict


def _platform_blocks() -> dict[str, tuple[dict[str, object], dict[str, object]]]:
    """(empty table, read table) per platform, as the dump records them."""
    return {
        "elf": (
            {"elf": ElfMetadata()},
            {"elf": ElfMetadata(symbols=[ElfSymbol(name="other")])},
        ),
        "pe": (
            {"pe": PeMetadata()},
            {"pe": PeMetadata(exports=[PeExport(name="other", ordinal=1)])},
        ),
        "macho": (
            {"macho": MachoMetadata()},
            {"macho": MachoMetadata(exports=[MachoExport(name="other")])},
        ),
    }


def _absent_fn(*, legacy: bool) -> Function:
    if legacy:
        return Function(
            name="f", mangled="_Z1fv", return_type="void", visibility=Visibility.HIDDEN
        )
    facts = header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml")
    return Function(name="f", mangled="_Z1fv", return_type="void", **facts)


@pytest.mark.parametrize("platform", ["elf", "pe", "macho"])
@pytest.mark.parametrize("legacy", [False, True], ids=["v46_fact", "pre_v46_hidden"])
@pytest.mark.parametrize("stored", [True], ids=["stored"])
@pytest.mark.xfail(
    strict=True, reason="gap A1: stored empty table still reads as confirmed absence"
)
def test_snapshot_rule_live_and_stored(
    platform: str, legacy: bool, stored: bool
) -> None:
    empty, read = _platform_blocks()[platform]
    for block, expect_absent in ((empty, False), (read, True)):
        snap = AbiSnapshot(
            library="libx", version="1", functions=[_absent_fn(legacy=legacy)], **block
        )
        if stored:
            snap = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
        assert is_export_confirmed_absent(snap.functions[0]) is expect_absent, (
            platform,
            legacy,
            stored,
            block,
        )


@pytest.mark.xfail(
    strict=True, reason="gap A1: an unread export table still yields a removal"
)
def test_cli_failed_read_never_reports_a_removal(tmp_path: Path) -> None:
    """Through the CLI: a NEW binary whose export table came back empty (a
    PE that failed to parse) must not turn a still-declared function into a
    removal/visibility finding, while a real read still does."""
    old_fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        **header_ast_surface_facts(exported=ExportMatch.DYNAMIC, producer="castxml"),
    )
    old = AbiSnapshot(
        library="x.dll",
        version="1",
        functions=[old_fn],
        pe=PeMetadata(exports=[PeExport(name="f", ordinal=1)]),
    )
    new_fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )
    old_p, unread_p, read_p = (
        tmp_path / n for n in ("old.json", "unread.json", "read.json")
    )
    save_snapshot(old, old_p)
    save_snapshot(
        AbiSnapshot(library="x.dll", version="2", functions=[new_fn], pe=PeMetadata()),
        unread_p,
    )
    save_snapshot(
        AbiSnapshot(
            library="x.dll",
            version="2",
            functions=[new_fn],
            pe=PeMetadata(exports=[PeExport(name="g", ordinal=1)]),
        ),
        read_p,
    )

    def kinds(new: Path) -> set[str]:
        res = CliRunner().invoke(
            main, ["compare", str(old_p), str(new), "-o", "json=-"]
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        doc = json.loads(res.output)
        return {
            c["kind"] for c in doc["changes"] if "f" in (c.get("symbol"), c.get("name"))
        }

    removal = {"func_removed", "func_visibility_changed", "func_removed_elf_only"}
    assert not kinds(unread_p) & removal
    assert kinds(read_p) & removal
