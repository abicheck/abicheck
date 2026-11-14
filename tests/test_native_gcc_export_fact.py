"""The native GCC backend's export fact tiers a lookup like the other backends.

Before, a declaration counted as ``PRESENT(True)`` exported whenever its
mangled name was in either table, and an ``extern "C"`` namesake read as
``PRESENT(False)``: the same binary gave a different export fact (and so a
different finding) depending on which header backend parsed it. The oracle
here is the ``ExportMatch`` contract, written out per case, and the clang
backend's own answer for the same tables.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.extract.headers.clang.context import visibility_and_surface_facts
from abicheck.extract.headers.gcc.backend import export_fact
from abicheck.model import FactStatus

DYN = frozenset({"_Z4keepi", "cname"})
STATIC = frozenset({"_Z4keepi", "cname", "_Z6hiddeni"})

# (mangled, name, expected (status, value, diagnostic or None))
CASES = [
    # Its own linker spelling is a dynamic export.
    ("_Z4keepi", "keep", (FactStatus.PRESENT, True, None)),
    # Only in .symtab: a symbol, but not one a consumer can bind to.
    ("_Z6hiddeni", "hidden", (FactStatus.PARTIAL, True, "export-match:static_only")),
    # Mangled spelling gone; a C function of the same bare name is exported.
    ("_Z5cnamei", "cname", (FactStatus.PARTIAL, True, "export-match:name_alias")),
    # A namespaced declaration never borrows a global C namesake.
    ("_ZN2ns5cnameEi", "ns::cname", (FactStatus.PRESENT, False, None)),
    # Nowhere at all.
    ("_Z4gonei", "gone", (FactStatus.PRESENT, False, None)),
]


def _request(*, no_binary: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        no_binary_evidence=no_binary, exported_dynamic=DYN, exported_static=STATIC
    )


@pytest.mark.parametrize(("mangled", "name", "expected"), CASES)
def test_export_fact_tiers(mangled: str, name: str, expected: tuple) -> None:
    fact = export_fact(SimpleNamespace(mangled=mangled, name=name), _request())
    status, value, diag = expected
    assert (fact.status, fact.value) == (status, value)
    assert fact.diagnostics == ((diag,) if diag else ())


@pytest.mark.parametrize(("mangled", "name", "expected"), CASES)
def test_gcc_and_clang_backends_agree(mangled: str, name: str, expected: tuple) -> None:
    gcc = export_fact(SimpleNamespace(mangled=mangled, name=name), _request())
    bare = "" if "::" in name else name
    _vis, facts = visibility_and_surface_facts(set(DYN), set(STATIC), mangled, bare)
    clang = facts["binary_exported_fact"]
    assert (gcc.status, gcc.value, gcc.diagnostics) == (
        clang.status,
        clang.value,
        clang.diagnostics,
    )


def test_header_only_dump_collects_no_export_fact() -> None:
    fact = export_fact(
        SimpleNamespace(mangled="_Z4keepi", name="keep"), _request(no_binary=True)
    )
    assert fact.status is FactStatus.NOT_COLLECTED
