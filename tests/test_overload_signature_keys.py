"""``compare.overload_signature``: the overload key of each function, read
from declared parameters or, for an export-table stub, from its demangling.

Every expected key is written out by hand from the Itanium manglings."""

from __future__ import annotations

import pytest

from abicheck.compare.overload_signature import format_overload_key, overload_keys
from abicheck.extract.export_symbol_identity import itanium_export_function
from abicheck.model import Function, Param


def _stub(mangled: str) -> Function:
    return itanium_export_function(mangled)


@pytest.mark.parametrize(
    ("mangled", "key"),
    [
        ("_Z3putN1a5TokenE", (("a::Token",), False, False, "")),
        ("_Z3putv", ((), False, False, "")),
        ("_ZNK1S3getEi", (("int",), True, False, "")),
        ("_ZNVKR1S3getEv", ((), True, True, "&")),
        ("_ZNO1S3getEv", ((), False, False, "&&")),
        ("_Z1fSt4pairIiiE", (("std::pair<int, int>",), False, False, "")),
        # Not demanglable: the linker spelling is the identity.
        ("_Zbogus", (("<symbol _Zbogus>",), False, False, "")),
        # A special name with no parameter list.
        ("_ZTV1S", (("<symbol _ZTV1S>",), False, False, "")),
    ],
)
def test_export_only_key_reads_the_demangling(mangled: str, key: tuple) -> None:
    old, _ = overload_keys([_stub(mangled)], [])
    assert old == {key}


def test_declared_key_reads_the_parameters_once_per_object() -> None:
    shared = Function(
        name="put",
        mangled="_Z3puti",
        return_type="void",
        params=[Param(name="x", type="int")],
    )
    added = Function(
        name="put",
        mangled="_Z3putl",
        return_type="void",
        params=[Param(name="x", type="long")],
        is_const=True,
        ref_qualifier="&",
    )
    old, new = overload_keys([shared], [shared, added])
    assert old == {(("int",), False, False, "")}
    assert new == {(("int",), False, False, ""), (("long",), True, False, "&")}


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ((("int",), False, False, ""), "(int)"),
        (((), True, True, "&&"), "() const volatile &&"),
        ((("a::T", "int"), False, True, "&"), "(a::T, int) volatile &"),
    ],
)
def test_format_overload_key(key: tuple, text: str) -> None:
    assert format_overload_key(key) == text
