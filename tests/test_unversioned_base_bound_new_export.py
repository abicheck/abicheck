"""A new export GNU ld bound to the base version reads unversioned.

Bug class: ``unversioned_exported_symbol`` flagged only
``ElfVersionBinding.UNVERSIONED``, but GNU ld binds every export a version
script does not match to the base version, so a symbol newly added outside
every node read only as ``func_added``. Oracle, on the inputs, over each
binding x whether OLD exported the name: in a versioned NEW library, the
finding is INTRODUCED iff the export is UNVERSIONED (any history) or BASE and
absent from OLD; a NAMED binding, or a BASE export OLD already had (pre-script
API), is never reported as introduced.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, ElfVersionBinding
from abicheck.model.evidence_status import CrossSourceEvolution
from abicheck.workflows.cross_source_hygiene import compute_cross_source_evolution

_KEEP = ElfSymbol(
    name="kept_api",
    version="LIB_1.0",
    version_binding=ElfVersionBinding.NAMED,
)


def _snap(version: str, symbols: list[ElfSymbol]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version=version,
        elf=ElfMetadata(versions_defined=["LIB_1.0"], symbols=[_KEEP, *symbols]),
    )


def _sym(binding: ElfVersionBinding) -> ElfSymbol:
    named = binding is ElfVersionBinding.NAMED
    return ElfSymbol(
        name="new_fn", version="LIB_1.0" if named else "", version_binding=binding
    )


_BINDINGS = (
    ElfVersionBinding.UNVERSIONED,
    ElfVersionBinding.BASE,
    ElfVersionBinding.NAMED,
)


@pytest.mark.parametrize(
    ("binding", "in_old"), list(itertools.product(_BINDINGS, (False, True)))
)
def test_new_base_bound_export_is_introduced(
    binding: ElfVersionBinding, in_old: bool
) -> None:
    old = _snap("1", [_sym(binding)] if in_old else [])
    new = _snap("2", [_sym(binding)])
    introduced = [
        c.symbol
        for c in compute_cross_source_evolution(old, new)
        if c.kind == ChangeKind.UNVERSIONED_EXPORTED_SYMBOL
        and c.cross_source_evolution is CrossSourceEvolution.INTRODUCED
    ]
    expected = binding is ElfVersionBinding.UNVERSIONED and not in_old
    expected |= binding is ElfVersionBinding.BASE and not in_old
    assert introduced == (["new_fn"] if expected else [])
