"""PDB procedure types are spelled with their signature and convention.

A synthetic TPI stream (no toolchain needed): an LF_PROCEDURE, its LF_ARGLIST,
and optionally an LF_POINTER to it. Before this change every procedure was the
opaque ``fn(...)``, so a callback whose signature or stdcall/fastcall
convention changed compared equal. The same spelling the DWARF and header
readers give is expected here.
"""

from __future__ import annotations

import struct

import pytest

from abicheck.pdb_parser import (
    LF_ARGLIST,
    LF_POINTER,
    LF_PROCEDURE,
    TypeDatabase,
    parse_tpi_stream,
)

_T_VOID = 0x0003
_T_INT4 = 0x0074
_T_NOTYPE = 0x0000
_CV_CALL_NEAR_C = 0x00
_CV_CALL_NEAR_STD = 0x07
_CV_CALL_NEAR_FAST = 0x04


def _tpi(records: list[tuple[int, bytes]]) -> bytes:
    """A TPI stream of (leaf, payload) records, indices starting at 0x1000."""
    rec_data = b""
    for leaf, payload in records:
        rec_bytes = struct.pack("<HH", 2 + len(payload), leaf) + payload
        rec_bytes += b"\x00" * ((4 - len(rec_bytes) % 4) % 4)
        rec_data += rec_bytes
    header = struct.pack(
        "<IIIII", 20040203, 56, 0x1000, 0x1000 + len(records), len(rec_data)
    )
    return header + b"\x00" * (56 - len(header)) + rec_data


def _procedure(return_ti: int, calltype: int, arglist_ti: int, parmcount: int) -> bytes:
    return struct.pack("<IBBHI", return_ti, calltype, 0, parmcount, arglist_ti)


def _arglist(args: list[int]) -> bytes:
    return struct.pack(f"<I{len(args)}I", len(args), *args)


def _pointer(referent_ti: int) -> bytes:
    # attrs: size 8 in bits 13-18, pointer mode 0 in bits 5-7, near64 kind 0x0C.
    return struct.pack("<II", referent_ti, (8 << 13) | 0x0C)


def _db(records: list[tuple[int, bytes]]) -> TypeDatabase:
    db = TypeDatabase(parse_tpi_stream(_tpi(records)))
    db.parse_all()
    return db


def test_procedure_spells_return_and_parameters() -> None:
    # 0x1000: int f(int, int) with the argument list at 0x1001.
    db = _db(
        [
            (LF_PROCEDURE, _procedure(_T_INT4, _CV_CALL_NEAR_C, 0x1001, 2)),
            (LF_ARGLIST, _arglist([_T_INT4, _T_INT4])),
        ]
    )
    assert db.type_name(0x1000) == "int (int, int)"


def test_procedure_without_parameters_spells_void() -> None:
    db = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, _CV_CALL_NEAR_C, 0x1001, 0)),
            (LF_ARGLIST, _arglist([])),
        ]
    )
    assert db.type_name(0x1000) == "void (void)"


def test_variadic_tail_is_spelled_as_ellipsis() -> None:
    db = _db(
        [
            (LF_PROCEDURE, _procedure(_T_INT4, _CV_CALL_NEAR_C, 0x1001, 2)),
            (LF_ARGLIST, _arglist([_T_INT4, _T_NOTYPE])),
        ]
    )
    assert db.type_name(0x1000) == "int (int, ...)"


@pytest.mark.parametrize(
    ("calltype", "attribute"),
    [
        (_CV_CALL_NEAR_STD, "stdcall"),
        (_CV_CALL_NEAR_FAST, "fastcall"),
        (_CV_CALL_NEAR_C, None),
    ],
)
def test_pointer_to_procedure_spells_convention(
    calltype: int, attribute: str | None
) -> None:
    # 0x1000 procedure, 0x1001 its argument list, 0x1002 a pointer to it.
    db = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, calltype, 0x1001, 1)),
            (LF_ARGLIST, _arglist([_T_INT4])),
            (LF_POINTER, _pointer(0x1000)),
        ]
    )
    suffix = f" __attribute__(({attribute}))" if attribute else ""
    assert db.type_name(0x1002) == f"void (*)(int){suffix}"


def test_convention_difference_is_visible_in_the_spelling() -> None:
    """stdcall and cdecl callbacks differ in spelling: a convention change is a change."""
    std = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, _CV_CALL_NEAR_STD, 0x1001, 1)),
            (LF_ARGLIST, _arglist([_T_INT4])),
            (LF_POINTER, _pointer(0x1000)),
        ]
    )
    cdecl = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, _CV_CALL_NEAR_C, 0x1001, 1)),
            (LF_ARGLIST, _arglist([_T_INT4])),
            (LF_POINTER, _pointer(0x1000)),
        ]
    )
    assert std.type_name(0x1002) != cdecl.type_name(0x1002)


def test_parameter_change_is_visible_in_the_spelling() -> None:
    long_arg = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, _CV_CALL_NEAR_C, 0x1001, 1)),
            (LF_ARGLIST, _arglist([_T_INT4])),
            (LF_POINTER, _pointer(0x1000)),
        ]
    )
    short_arg = _db(
        [
            (LF_PROCEDURE, _procedure(_T_VOID, _CV_CALL_NEAR_C, 0x1001, 1)),
            (LF_ARGLIST, _arglist([_T_INT4, _T_INT4])),
            (LF_POINTER, _pointer(0x1000)),
        ]
    )
    assert long_arg.type_name(0x1002) == "void (*)(int)"
    assert short_arg.type_name(0x1002) == "void (*)(int, int)"
