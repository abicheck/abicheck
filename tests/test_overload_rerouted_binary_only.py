"""OVERLOAD_SET_REROUTED at binary-only evidence: export-table stubs carry no
parameters, so overloads are told apart by their mangled signature. Real
Itanium manglings throughout."""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.diff_templates import detect_overload_set_rerouted
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.extract.export_symbol_identity import itanium_export_function
from abicheck.model import AbiSnapshot


def _exports(*names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="l.so.1",
        version="1",
        functions=[itanium_export_function(n) for n in names],
        elf=ElfMetadata(
            symbols=[
                ElfSymbol(
                    name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                )
                for n in names
            ]
        ),
        elf_only_mode=True,
    )


@pytest.mark.parametrize(
    ("old", "new", "stem"),
    [
        # kput(int) -> kput(long), beside kput(double)
        (["_Z4kputi", "_Z4kputd"], ["_Z4kputl", "_Z4kputd"], "kput"),
        # ns::put(char const*) -> ns::put(std::string_view), beside put(int)
        (
            ["_ZN2ns3putEPKc", "_ZN2ns3putEi"],
            ["_ZN2ns3putESt17basic_string_viewIcSt11char_traitsIcEE", "_ZN2ns3putEi"],
            "ns::put",
        ),
        # member S::get(int) const -> S::get(long) const, beside S::get()
        (["_ZNK1S3getEi", "_ZNK1S3getEv"], ["_ZNK1S3getEl", "_ZNK1S3getEv"], "S::get"),
    ],
)
def test_retyped_overload_detected_from_symbols(old, new, stem):
    (finding,) = detect_overload_set_rerouted(_exports(*old), _exports(*new))
    assert finding.kind == ChangeKind.OVERLOAD_SET_REROUTED
    assert finding.symbol == stem


def test_retyped_overload_reaches_compare():
    r = compare(_exports("_Z4kputi", "_Z4kputd"), _exports("_Z4kputl", "_Z4kputd"))
    assert ChangeKind.OVERLOAD_SET_REROUTED in {c.kind for c in r.changes}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (["_Z4kputi", "_Z4kputd"], ["_Z4kputi", "_Z4kputd"]),  # unchanged
        (["_Z4kputi", "_Z4kputd"], ["_Z4kputd"]),  # pure removal
        (["_Z4kputi"], ["_Z4kputl"]),  # 1 -> 1 is a signature change
        # const-only sibling: f(int) const added beside f(int), nothing removed
        (["_ZN1S1fEi"], ["_ZN1S1fEi", "_ZNK1S1fEi"]),
    ],
)
def test_no_reroute_without_both_removal_and_addition(old, new):
    assert detect_overload_set_rerouted(_exports(*old), _exports(*new)) == []
