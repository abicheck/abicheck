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
        ("_ZN6ktag_aB2v13getEv", "ktag_a"),  # ktag_a[abi:v1]::get()
        ("?use@@YAXVUktag_a@@@Z", "Uktag_a"),  # use(class Uktag_a)
        ("?use@@YAXPEAUktag_a@@@Z", "ktag_a"),  # use(ktag_a*)
        ("?use@@YAXAEBVktag_a@@@Z", "ktag_a"),  # use(ktag_a const&)
        ("?use@@YAX$$QEAUktag_a@@@Z", "ktag_a"),  # use(ktag_a&&)
        ("?mk@@YA?AVktag_a@@XZ", "ktag_a"),  # ktag_a mk()
        ("?use@@YAXW4ktag_a@@@Z", "ktag_a"),  # use(enum ktag_a)
        ("?use@@YAX_NUktag_a@@@Z", "ktag_a"),  # use(bool, ktag_a)
        ("?use@@YAXV?$vec@Uktag_a@@@std@@@Z", "ktag_a"),  # use(std::vec<ktag_a>)
        ("?m@Foo@@QEAAXUktag_a@@@Z", "ktag_a"),  # Foo::m(ktag_a)
        ("?m@ktag_a@@QEAAXXZ", "ktag_a"),  # ktag_a::m(): its scope
        ("?g@@3Uktag_a@@A", "ktag_a"),  # ktag_a g;
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
        # ns::ktag_a[abi:v1](): the ABI tag is the entity's, not a scope.
        ("_ZN2ns6ktag_aB2v1Ev", "ktag_a"),
        # A class code letter that only starts a longer name: class Uktag_a,
        # struct Vktag_a, struct Tktag_a, enum Wktag_a.
        ("?use@@YAXVUktag_a@@@Z", "ktag_a"),
        ("?use@@YAXUVktag_a@@@Z", "ktag_a"),
        ("?use@@YAXUTktag_a@@@Z", "ktag_a"),
        ("?use@@YAXW4Wktag_a@@@Z", "ktag_a"),
        ("?Uktag_a@@YAXXZ", "ktag_a"),  # a function named Uktag_a
        ("?use@@YAX", "ktag_a"),  # truncated: unreadable, no claim
        ("?use@", "ktag_a"),
    ],
)
def test_non_reference_rejected(symbol: str, leaf: str) -> None:
    assert not mangling_references_type(symbol, leaf)
