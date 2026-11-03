"""Export-only data removals keep their evidence-tier spelling, like functions."""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Variable, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind


@pytest.mark.parametrize(
    "symbol",
    ["counter", "_ZZ8registryvE5value", "_ZZNSt19_Sp_make_shared_tag5_S_tiEvE5__tag"],
)
@pytest.mark.parametrize("binary_only", [False, True])
@pytest.mark.parametrize("declared", [False, True])
def test_variable_removal_kind_tracks_the_available_evidence(
    symbol, binary_only, declared
):
    old = AbiSnapshot(
        library="libtest.so",
        version="1",
        elf_only_mode=binary_only,
        variables=[
            Variable(
                name=symbol,
                mangled=symbol,
                type="int" if declared else "?",
                visibility=Visibility.PUBLIC if declared else Visibility.ELF_ONLY,
            )
        ],
    )
    new = AbiSnapshot(library="libtest.so", version="2", elf_only_mode=binary_only)
    removals = [c for c in compare(old, new).changes if "removed" in c.kind.value]
    expected = (
        ChangeKind.VAR_REMOVED_ELF_ONLY
        if binary_only and not declared
        else ChangeKind.VAR_REMOVED
    )
    assert [(c.kind, c.symbol) for c in removals] == [(expected, symbol)]
