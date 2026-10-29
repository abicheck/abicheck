# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A Mach-O export's linkage fills the platform-neutral ``elf_binding`` fact.

ELF stamps ``Function/Variable.elf_binding`` from ``.dynsym``; before this, a
Mach-O export never carried it, so a policy ``binding: weak`` rule (#1630's
selector) could not match a removed weak Mach-O export and the finding stayed
BREAKING. Bug class ``symbol_rule.single_object_format``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.model.elf_facts import SymbolBinding
from abicheck.model.macho_facts import MachoExport, MachoMetadata, macho_export_linkage
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_load_normalization import (
    backfill_missing_elf_binding,
    stamp_macho_elf_binding,
)
from tests._macho_synthetic import N_EXT, N_SECT, N_WEAK_DEF, MachoSymbol, write_dylib

_REMOVAL_KINDS = frozenset({"func_removed", "func_removed_elf_only"})


@pytest.mark.parametrize(
    ("exports", "expected"),
    [
        pytest.param(
            [MachoExport("strong_fn")], {"strong_fn": SymbolBinding.GLOBAL}, id="strong"
        ),
        pytest.param(
            [MachoExport("weak_fn", is_weak=True)],
            {"weak_fn": SymbolBinding.WEAK},
            id="weak",
        ),
        pytest.param(
            [MachoExport("twice"), MachoExport("twice")],
            {"twice": SymbolBinding.GLOBAL},
            id="repeated-agreeing",
        ),
        pytest.param(
            [MachoExport("mixed"), MachoExport("mixed", is_weak=True)],
            {"mixed": None},
            id="repeated-disagreeing-is-unknown",
        ),
        pytest.param([MachoExport("")], {}, id="empty-name-dropped"),
    ],
)
def test_export_linkage_is_weak_or_global_and_unknown_when_it_disagrees(
    exports: list[MachoExport], expected: dict[str, SymbolBinding | None]
) -> None:
    assert macho_export_linkage(exports) == expected


def _macho_snapshot(*, elf_binding: SymbolBinding | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libm.dylib",
        version="1",
        functions=[
            Function(name="strong_fn", mangled="strong_fn", return_type="int"),
            Function(name="weak_fn", mangled="weak_fn", return_type="int"),
            Function(name="unexported", mangled="unexported", return_type="int"),
        ],
        macho=MachoMetadata(
            exports=[MachoExport("strong_fn"), MachoExport("weak_fn", is_weak=True)]
        ),
        platform="macho",
    )


def test_stamp_sets_binding_and_its_fact_sibling_and_leaves_unexported_none() -> None:
    snap = _macho_snapshot()
    stamp_macho_elf_binding(snap)
    by_name = {f.name: f for f in snap.declarations.functions}
    assert by_name["strong_fn"].elf_binding is SymbolBinding.GLOBAL
    assert by_name["weak_fn"].elf_binding is SymbolBinding.WEAK
    assert by_name["unexported"].elf_binding is None
    # The fact sibling is kept in sync, not left at its NOT_COLLECTED default.
    assert by_name["weak_fn"].elf_binding_fact.value is SymbolBinding.WEAK


def test_load_backfill_recovers_binding_for_an_archived_macho_snapshot() -> None:
    snap = _macho_snapshot()
    backfill_missing_elf_binding(snap)
    by_name = {f.name: f for f in snap.declarations.functions}
    assert by_name["weak_fn"].elf_binding is SymbolBinding.WEAK
    assert by_name["strong_fn"].elf_binding is SymbolBinding.GLOBAL


def test_dump_cli_stamps_linkage_and_archived_baseline_recovers_it(
    tmp_path: Path,
) -> None:
    lib = write_dylib(
        tmp_path / "libweak.dylib",
        [
            MachoSymbol("_strong_fn", N_SECT | N_EXT),
            MachoSymbol("_weak_fn", N_SECT | N_EXT, n_desc=N_WEAK_DEF),
        ],
    )
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(main, ["dump", str(lib), "-o", str(out)])
    assert result.exit_code == 0, result.output
    live = {f.name: f.elf_binding for f in load_snapshot(out).declarations.functions}
    assert live == {"strong_fn": SymbolBinding.GLOBAL, "weak_fn": SymbolBinding.WEAK}

    # An archived baseline written before the field existed carries no
    # elf_binding key; the load recovers it from the stored export table.
    doc = json.loads(out.read_text())
    for decl in doc["sections"]["declarations"]["payload"]["functions"]:
        decl.pop("elf_binding", None)
        decl.pop("elf_binding_fact", None)
    out.write_text(json.dumps(doc))
    archived = {
        f.name: f.elf_binding for f in load_snapshot(out).declarations.functions
    }
    assert archived == live


def _compare_removal(tmp_path: Path, rule_binding: str) -> list[dict]:
    (tmp_path / "old").mkdir(parents=True)
    (tmp_path / "new").mkdir()
    old = write_dylib(
        tmp_path / "old" / "libw.dylib",
        [
            MachoSymbol("_keep", N_SECT | N_EXT),
            MachoSymbol("_weak_gone", N_SECT | N_EXT, n_desc=N_WEAK_DEF),
        ],
    )
    new = write_dylib(
        tmp_path / "new" / "libw.dylib", [MachoSymbol("_keep", N_SECT | N_EXT)]
    )
    suppress = tmp_path / f"supp-{rule_binding}.yml"
    suppress.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: weak_gone\n"
        f"    binding: {rule_binding}\n"
    )
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--suppress",
            str(suppress),
            "-o",
            "json=-",
        ],
    )
    assert result.exit_code in (0, 2, 4), result.output
    start = result.output.index("{")
    return json.loads(result.output[start:])["changes"]


def test_binding_weak_rule_applies_to_a_removed_weak_macho_export(
    tmp_path: Path,
) -> None:
    changes = _compare_removal(tmp_path / "weak", "weak")
    # The weak rule suppresses the removal: it is not in the report.
    assert not [c for c in changes if c["kind"] in _REMOVAL_KINDS]


def test_binding_global_rule_does_not_match_a_weak_macho_export(
    tmp_path: Path,
) -> None:
    changes = _compare_removal(tmp_path / "global", "global")
    removed = [c for c in changes if c["kind"] in _REMOVAL_KINDS]
    assert len(removed) == 1
    assert removed[0]["symbol_binding"] == "weak"
    assert removed[0]["symbol"] == "weak_gone"
