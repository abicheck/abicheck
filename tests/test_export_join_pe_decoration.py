"""x86 PE/COFF C calling-convention decoration in the ``exports`` join
(evidence-entity-model Phase 2, I2).

An exhaustive small-domain enumeration: PE machine x calling convention x
mangled/unmangled declaration. The oracle is the hand-written truth table
``_EXPECTED`` below -- every cell is stated by hand from the MSVC/MinGW
linker conventions, never recomputed through the join or any decoration
helper, so the oracle cannot share a bug with the implementation.

Conventions (the declaration is ``foo``, or the Itanium-mangled ``_Z3fooi``):

* ``cdecl``      -> ``_foo``    (32-bit x86 only)
* ``stdcall``    -> ``_foo@8``  (32-bit x86 only)
* ``fastcall``   -> ``@foo@8``  (32-bit x86 only)
* ``vectorcall`` -> ``foo@@8``  (32-bit x86 only; MSVC also decorates it on
  x64, but ``@N`` is never stripped there)
* ``none``       -> ``foo``     (exact spelling; joins everywhere)

A C++-mangled name is never undecorated (MSVC ``?`` names carry their
convention inside the mangling; an Itanium ``_Z`` name is never treated as
a C decoration), and an unknown machine never undecorates (fail closed).
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.graph_join import JoinState
from abicheck.pe_metadata import PeExport, PeMetadata

_MACHINES = {
    "i386": "IMAGE_FILE_MACHINE_I386",
    "amd64": "IMAGE_FILE_MACHINE_AMD64",
    "arm64": "IMAGE_FILE_MACHINE_ARM64",
    "unknown": "",
}

# (declaration kind, convention) -> the export spelling the linker writes.
_EXPORT = {
    ("plain", "cdecl"): "_foo",
    ("plain", "stdcall"): "_foo@8",
    ("plain", "fastcall"): "@foo@8",
    ("plain", "vectorcall"): "foo@@8",
    ("plain", "none"): "foo",
    ("mangled", "cdecl"): "__Z3fooi",
    ("mangled", "stdcall"): "__Z3fooi@4",
    ("mangled", "fastcall"): "@_Z3fooi@4",
    ("mangled", "vectorcall"): "_Z3fooi@@4",
    ("mangled", "none"): "_Z3fooi",
}

_M = JoinState.MATCHED
_U = JoinState.UNMATCHED

# Hand-written truth table: (decl kind, convention) -> {machine: state}.
_EXPECTED = {
    ("plain", "cdecl"): {"i386": _M, "amd64": _U, "arm64": _U, "unknown": _U},
    ("plain", "stdcall"): {"i386": _M, "amd64": _U, "arm64": _U, "unknown": _U},
    ("plain", "fastcall"): {"i386": _M, "amd64": _U, "arm64": _U, "unknown": _U},
    ("plain", "vectorcall"): {"i386": _M, "amd64": _U, "arm64": _U, "unknown": _U},
    ("plain", "none"): {"i386": _M, "amd64": _M, "arm64": _M, "unknown": _M},
    ("mangled", "cdecl"): {"i386": _U, "amd64": _U, "arm64": _U, "unknown": _U},
    ("mangled", "stdcall"): {"i386": _U, "amd64": _U, "arm64": _U, "unknown": _U},
    ("mangled", "fastcall"): {"i386": _U, "amd64": _U, "arm64": _U, "unknown": _U},
    ("mangled", "vectorcall"): {"i386": _U, "amd64": _U, "arm64": _U, "unknown": _U},
    ("mangled", "none"): {"i386": _M, "amd64": _M, "arm64": _M, "unknown": _M},
}

_DECL = {"plain": ("foo", "foo"), "mangled": ("foo", "_Z3fooi")}

_CASES = [(kind, conv, machine) for (kind, conv) in _EXPORT for machine in _MACHINES]


def _fn(name, mangled, *params):
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions, exports, machine):
    return AbiSnapshot(
        library="x.dll",
        version="1",
        functions=list(functions),
        pe=PeMetadata(
            machine=machine,
            exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(exports)],
        ),
    )


def _join(snap):
    from abicheck.compare.export_join import join_exports

    return join_exports(snap)


def test_truth_table_is_complete_and_not_constant():
    # Vacuity guard on the oracle itself.
    assert set(_EXPECTED) == set(_EXPORT)
    states = {s for row in _EXPECTED.values() for s in row.values()}
    assert states == {_M, _U}
    assert all(set(row) == set(_MACHINES) for row in _EXPECTED.values())


@pytest.mark.parametrize(("kind", "conv", "machine"), _CASES)
def test_decorated_export_join_matches_truth_table(kind, conv, machine):
    name, mangled = _DECL[kind]
    export = _EXPORT[(kind, conv)]
    j = _join(_snap([_fn(name, mangled, "int")], [export], _MACHINES[machine]))
    want = _EXPECTED[(kind, conv)][machine]
    node = f"decl://{mangled}"
    assert j.declaration(node).state is want
    assert j.export("pe", export).state is want
    if want is _M:
        assert j.declaration(node).candidates == (f"binary_symbol://pe/{export}",)


@pytest.mark.parametrize(
    "export",
    ["_foo@7", "_foo@08", "_foo@", "@foo@", "foo@@", "_foo@8x", "@foo", "_@8", "@@8"],
)
def test_malformed_decoration_does_not_join_on_i386(export):
    j = _join(_snap([_fn("foo", "foo")], [export], _MACHINES["i386"]))
    assert j.declaration("decl://foo").state is _U
    assert j.export("pe", export).state is _U


def test_two_declarations_collapsing_onto_one_decorated_export_are_ambiguous():
    # Two unmangled `foo` declarations (distinct entities, both spelling `foo`)
    # both undecorate from `_foo@8`: the export is ambiguous, never guessed.
    j = _join(
        _snap(
            [_fn("foo", ""), _fn("foo", "", "int")],
            ["_foo@8"],
            _MACHINES["i386"],
        )
    )
    rec = j.export("pe", "_foo@8")
    assert rec.state is JoinState.AMBIGUOUS
    assert len(rec.candidates) == 2


def test_one_declaration_with_two_decorated_exports_is_ambiguous():
    j = _join(_snap([_fn("foo", "foo")], ["_foo@8", "@foo@8"], _MACHINES["i386"]))
    assert j.declaration("decl://foo").state is JoinState.AMBIGUOUS


def test_alias_refused_when_another_declaration_owns_the_export_exactly():
    j = _join(
        _snap(
            [_fn("foo", "foo"), _fn("_foo", "_foo")],
            ["_foo"],
            _MACHINES["i386"],
        )
    )
    assert j.export("pe", "_foo").candidates == ("decl://_foo",)
    assert j.declaration("decl://foo").state is _U


def test_msvc_cpp_export_still_joins_exactly_on_i386():
    j = _join(_snap([_fn("foo", "?foo@@YGHH@Z")], ["?foo@@YGHH@Z"], _MACHINES["i386"]))
    assert j.declaration("decl://?foo@@YGHH@Z").state is _M
