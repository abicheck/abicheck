"""Removing a version node no symbol was bound to is not a binary break.

Bug class: ``symbol_version_defined_removed`` kept its BREAKING default for a
``.gnu.version_d`` entry no consumer can require -- a consumer records in
``.gnu.version_r`` only the versions of the symbols it binds. Oracle, on the
inputs: the removal is lowered to COMPATIBLE_WITH_RISK iff no OLD symbol
carries that version, whatever the node is called and whatever other nodes
hold; a node binding even one symbol (default or not) keeps BREAKING.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.diff_platform_elf_symbols import _diff_elf_symbol_versioning
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

_NODES = ("LIB_1.0", "LIB_1.1", "LIB_2.0")


def _elf(defined: tuple[str, ...], bound: dict[str, str]) -> ElfMetadata:
    return ElfMetadata(
        versions_defined=list(defined),
        symbols=[
            ElfSymbol(name=n, version=v, is_default=(i % 2 == 0))
            for i, (n, v) in enumerate(bound.items())
        ],
    )


@pytest.mark.parametrize("dropped", _NODES)
@pytest.mark.parametrize("binding", list(itertools.product((None, *_NODES), repeat=2)))
def test_dropped_node_grade_follows_whether_a_symbol_was_bound(
    dropped: str, binding: tuple[str | None, ...]
) -> None:
    bound = {f"f{i}": v for i, v in enumerate(binding) if v is not None}
    old = _elf(_NODES, bound)
    new = _elf(tuple(n for n in _NODES if n != dropped), {})
    removed = [
        c
        for c in _diff_elf_symbol_versioning(old, new)
        if c.kind == ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED
    ]
    assert [c.symbol for c in removed] == [dropped]
    proven_unbound = bool(bound) and dropped not in bound.values()
    expected = Verdict.COMPATIBLE_WITH_RISK if proven_unbound else None
    assert removed[0].effective_verdict == expected
