"""A DEFAULT<->PROTECTED change is reported once, by the most specific kind.

Bug class: ``symbol_elf_visibility_changed`` fired for every exported-
visibility transition, beside ``func_visibility_protected_changed`` (a
function) or ``protected_visibility_changed`` (a data object): one event
reported twice. Oracle, per ELF symbol type: exactly one visibility finding,
the specific kind where a detector owns that type, else the generic one.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType


def _snap(elf: ElfMetadata) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", elf=elf)


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


@pytest.mark.parametrize(
    ("sym_type", "specific"),
    [
        (SymbolType.FUNC, ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED),
        (SymbolType.OBJECT, ChangeKind.PROTECTED_VISIBILITY_CHANGED),
        (SymbolType.COMMON, ChangeKind.PROTECTED_VISIBILITY_CHANGED),
        (SymbolType.TLS, None),
        (SymbolType.IFUNC, None),
    ],
)
def test_default_to_protected_is_reported_once(sym_type, specific) -> None:
    """Each DEFAULT->PROTECTED change yields exactly one visibility
    finding: the specific kind where one exists, else the generic one."""

    def elf(vis: str) -> ElfMetadata:
        return ElfMetadata(
            symbols=[
                ElfSymbol(
                    name="sym",
                    binding=SymbolBinding.GLOBAL,
                    sym_type=sym_type,
                    size=4,
                    visibility=vis,
                )
            ]
        )

    kinds = _kinds(compare(_snap(elf("default")), _snap(elf("protected"))))
    visibility_kinds = kinds & {
        ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED,
        ChangeKind.PROTECTED_VISIBILITY_CHANGED,
        ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
    }
    assert visibility_kinds == {specific or ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED}
