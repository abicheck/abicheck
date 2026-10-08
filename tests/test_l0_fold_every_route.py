# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A header-scoped pair's ELF-only removal is reported alike on every route.

``examples/case97``: a function still exported by the binary can drop out of
the header AST (a macro-gated declaration). ``fold_l0_hard_removals`` restores
such a removal from the binary's own export table; it now runs in the shared
evidence fold (``workflows.pair_evidence``) instead of only in the native
``compare`` CLI. For a stored snapshot that carries its ELF table the
comparison already reports the removal on every route, so this pins that the
move changed nothing observable: CLI, typed API and release member agree.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from _family_f2_routes import (
    AXES_BY_NAME,
    Operands,
    run_api,
    run_cli,
    run_release_member,
)

from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

KEEP, HIDDEN = "keep_fn", "macro_gated_fn"


def _side(tmp: Path, side: str, exports: tuple[str, ...], *, headers: bool) -> Path:
    binary = tmp / f"{side}.so"
    binary.write_bytes(b"\0" * (16 if side == "old" else 8))
    st = binary.stat()
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0" if side == "old" else "2.0",
        from_headers=headers,
        functions=[
            Function(
                name=KEEP, mangled=KEEP, return_type="int", visibility=Visibility.PUBLIC
            )
        ],
    )
    snap.elf = ElfMetadata(
        machine="EM_X86_64",
        symbols=[
            ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
            for n in exports
        ],
    )
    snap = dataclasses.replace(
        snap, source_path=str(binary), source_mtime=st.st_mtime, source_size=st.st_size
    )
    d = tmp / side
    d.mkdir(exist_ok=True)
    out = d / "libfoo.json"
    out.write_text(snapshot_to_json(snap), encoding="utf-8")
    return out


def _ops(tmp: Path, *, headers: bool) -> Operands:
    old = _side(tmp, "old", (KEEP, HIDDEN), headers=headers)
    new = _side(tmp, "new", (KEEP,), headers=headers)
    return Operands(old, new, old.parent, new.parent)


def _elf_only_removals(report: dict[str, Any]) -> list[str]:
    return sorted(
        c["symbol"]
        for c in report.get("changes", [])
        if c.get("kind") == "func_removed_elf_only"
    )


@pytest.mark.parametrize(
    "route", [run_cli, run_api, run_release_member], ids=["cli", "api", "release"]
)
def test_hidden_elf_only_removal_is_reported(route: Any, tmp_path: Path) -> None:
    ops = _ops(tmp_path, headers=True)
    out = route(ops, AXES_BY_NAME["default"], tmp_path)
    assert _elf_only_removals(out.report) == [HIDDEN], json.dumps(out.report)[:500]
    assert out.verdict == "BREAKING"
