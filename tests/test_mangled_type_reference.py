"""``mangling_references_type``: a structural reading of whether a linker
spelling encodes a reference to a named type, over real Itanium (ELF,
Mach-O) and MSVC manglings -- never a substring test."""

from __future__ import annotations

import pytest

from abicheck.model.mangled_type_reference import mangling_references_type


@pytest.mark.parametrize(
    ("symbol", "leaf"),
    [
        ("_Z4kfamI6ktag_aEiv", "ktag_a"),  # kfam<ktag_a>()
        ("__Z4kfamI6ktag_aEiv", "ktag_a"),  # Mach-O, still decorated
        ("_Z3use6ktag_a", "ktag_a"),  # use(ktag_a)
        ("_ZN2ns4kfamINS_6ktag_aEEEiv", "ktag_a"),  # ns::kfam<ns::ktag_a>()
        ("_ZN6ktag_aC1Ev", "ktag_a"),  # ktag_a::ktag_a()
        ("_ZN6ktag_aD2Ev", "ktag_a"),  # ktag_a::~ktag_a()
        ("_ZNSt6vectorI6ktag_aSaIS0_EE9push_backERKS0_", "ktag_a"),
        ("_ZTV6ktag_a", "ktag_a"),  # vtable for ktag_a
        ("??$kfam@Uktag_a@@@@YAHXZ", "ktag_a"),  # MSVC kfam<struct ktag_a>()
        ("?use@@YAXVktag_a@ns@@@Z", "ktag_a"),  # MSVC use(class ns::ktag_a)
    ],
)
def test_reference_detected(symbol: str, leaf: str) -> None:
    assert mangling_references_type(symbol, leaf)


@pytest.mark.parametrize(
    ("symbol", "leaf"),
    [
        ("ktag_a_count", "ktag_a"),  # C export prefixed with the name
        ("_ktag_a_count", "ktag_a"),  # Mach-O C export
        ("_Z12ktag_a_countv", "ktag_a"),  # C++ function named with the prefix
        ("_Z6ktag_av", "ktag_a"),  # function named exactly like the type
        ("_Z4kfamI6ktag_bEiv", "ktag_a"),  # a different tag
        ("_Z4kfamI7ktag_abEiv", "ktag_a"),  # a longer name sharing the prefix
        ("?ktag_a_count@@3HA", "ktag_a"),  # MSVC variable
        ("?ktag_a@@YAXXZ", "ktag_a"),  # MSVC function named like the type
        ("_Z10bruteforcev", "brute_force"),
    ],
)
def test_non_reference_rejected(symbol: str, leaf: str) -> None:
    assert not mangling_references_type(symbol, leaf)
