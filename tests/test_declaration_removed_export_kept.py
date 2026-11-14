"""A declaration removed from the headers while its export stays is an API break.

Bug class: removing a function's declaration from the public header while the
library keeps exporting it read as ``func_removed`` BREAKING, though every
built consumer still binds the export. Oracle, on the inputs: the removal is
always reported, and graded API_BREAK iff NEW's export table was read and
lists the symbol; an unread table, or an export that really went away, keeps
BREAKING. Other removal kinds are never touched.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare import declaration_removed_export_kept as mod
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol


def _new(exported: bool) -> AbiSnapshot:
    syms = [ElfSymbol(name="kept_fn")] + ([ElfSymbol(name="wget")] if exported else [])
    return AbiSnapshot(library="l.so", version="2", elf=ElfMetadata(symbols=syms))


@pytest.mark.parametrize(
    ("kind", "exported", "table_read"),
    list(
        itertools.product(
            (
                ChangeKind.FUNC_REMOVED,
                ChangeKind.FUNC_REMOVED_ELF_ONLY,
                ChangeKind.VAR_REMOVED,
            ),
            (False, True),
            (False, True),
        )
    ),
)
def test_grade_follows_the_new_export_table(
    monkeypatch: pytest.MonkeyPatch, kind: ChangeKind, exported: bool, table_read: bool
) -> None:
    monkeypatch.setattr(mod, "export_table_covered", lambda _snap, _p: table_read)
    change = Change(kind=kind, symbol="wget", description="removed")
    out = mod.grade_declaration_removed_export_kept([change], _new(exported))
    assert out == [change]  # still reported, never dropped
    lowered = kind is ChangeKind.FUNC_REMOVED and exported and table_read
    assert change.effective_verdict == (Verdict.API_BREAK if lowered else None)
