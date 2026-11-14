"""``std_reexport_removed`` from the public headers' re-export table.

Three layers, each against an oracle written out by hand from the C++ rule
(a namespace-scope ``using std::X;`` in a library namespace makes
``lib::X`` spellable; removing it breaks that spelling), never from the
implementation's own helpers:

* the collectors: castxml XML and clang JSON shaped like the real backends'
  output (castxml lists the std entity among the namespace's ``members``;
  clang writes a ``UsingDecl``), and the GCC projection of plugin rows;
* the detector over both sides' tables, including "not modelled";
* storage: the table survives a write/load round trip, ``{}`` included,
  and an older document without it loads as not modelled.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.namespace_shape_detectors import detect_std_reexport_removed
from abicheck.extract.declaration_provenance import build_public_set
from abicheck.extract.headers.gcc.acquisition import HeaderScope
from abicheck.extract.headers.gcc.projection import std_reexports
from abicheck.extract.headers.std_reexports import (
    castxml_std_reexports,
    clang_std_reexports,
)
from abicheck.model import AbiSnapshot, Function, RecordType

PUB = "/inc/lib.h"
PRIV = "/usr/include/c++/13/cstdlib"


def _segs() -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    header_segs, dir_segs, _have = build_public_set([PUB], [])
    return header_segs, dir_segs


# ---------------------------------------------------------------------------
# castxml
# ---------------------------------------------------------------------------


def _castxml(lib_members_public: bool = True, reexport_in: str = "lib") -> Element:
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name=PUB if lib_members_public else PRIV)
    SubElement(root, "File", id="f2", name=PRIV)
    SubElement(root, "Namespace", id="_1", name="::", members="_2 _3 _9")
    SubElement(root, "Namespace", id="_2", name="std", context="_1", members="_5 _6")
    # ::abs (extern "C", global) and std::abs(long) are both bound by
    # `using std::abs;` -- castxml lists every bound declaration.
    SubElement(root, "Function", id="_9", name="abs", context="_1", file="f2")
    SubElement(root, "Function", id="_5", name="abs", context="_2", file="f2")
    SubElement(root, "Typedef", id="_6", name="string", context="_2", file="f2")
    SubElement(
        root,
        "Namespace",
        id="_3",
        name=reexport_in,
        context="_1",
        members="_9 _5 _6 _7",
    )
    SubElement(root, "Function", id="_7", name="use", context="_3", file="f1")
    return root


def test_castxml_reads_the_namespace_members_bound_from_std() -> None:
    assert castxml_std_reexports(_castxml(), *_segs(), True) == {
        "lib::abs": "std::abs",
        "lib::string": "std::string",
    }


@pytest.mark.parametrize(
    ("root", "have_public_set"),
    [
        (_castxml(lib_members_public=False), True),  # namespace not public
        (_castxml(reexport_in="_impl"), True),  # reserved namespace
    ],
)
def test_castxml_records_no_reexport_outside_a_public_library_namespace(
    root: Element, have_public_set: bool
) -> None:
    assert castxml_std_reexports(root, *_segs(), have_public_set) == {}


def test_castxml_without_a_public_set_is_not_modelled() -> None:
    assert castxml_std_reexports(_castxml(), *_segs(), False) is None


# ---------------------------------------------------------------------------
# clang -ast-dump=json
# ---------------------------------------------------------------------------


def _clang(using_file: str = PUB, namespace: str = "lib") -> dict:
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "NamespaceDecl", "name": "std", "loc": {"file": PRIV}},
            {
                "kind": "NamespaceDecl",
                "name": namespace,
                "loc": {"file": using_file},
                "inner": [
                    {
                        "kind": "UsingDecl",
                        "name": "std::abs",
                        "loc": {"line": 2},
                        "inner": [{"kind": "UsingShadowDecl"}],
                    },
                    {"kind": "UsingDecl", "name": "::std::string", "loc": {"line": 3}},
                    # not a std entity: not this kind
                    {"kind": "UsingDecl", "name": "other::f", "loc": {"line": 4}},
                    {
                        "kind": "CXXRecordDecl",
                        "name": "S",
                        "inner": [{"kind": "UsingDecl", "name": "std::abs"}],
                    },
                ],
            },
        ],
    }


def test_clang_reads_namespace_scope_using_declarations() -> None:
    assert clang_std_reexports(_clang(), *_segs(), True) == {
        "lib::abs": "std::abs",
        "lib::string": "std::string",
    }


@pytest.mark.parametrize(
    "root",
    [_clang(using_file=PRIV), _clang(namespace="std"), _clang(namespace="__detail")],
)
def test_clang_records_no_reexport_outside_a_public_library_namespace(
    root: dict,
) -> None:
    assert clang_std_reexports(root, *_segs(), True) == {}


# ---------------------------------------------------------------------------
# native GCC plugin rows
# ---------------------------------------------------------------------------


def _ns(*names: str) -> list[dict]:
    return [{"kind": "namespace", "name": n} for n in names]


GCC_ROWS = [
    {"kind": "compiler"},
    {"kind": "function_decl", "name": "use", "source": PUB, "scope": _ns("lib")},
    {"kind": "using", "name": "abs", "target_scope": "std", "scope": _ns("lib")},
    {"kind": "using", "name": "abs", "target_scope": "", "scope": _ns("lib")},
    {"kind": "using", "name": "string", "target_scope": "std", "scope": _ns("lib")},
    # a namespace with no public declaration of its own
    {"kind": "using", "name": "abs", "target_scope": "std", "scope": _ns("hidden")},
    {"kind": "using", "name": "f", "target_scope": "other", "scope": _ns("lib")},
]


def test_gcc_projection_reads_the_plugin_using_rows(tmp_path) -> None:
    header = tmp_path / "lib.h"
    header.write_text("")
    rows = [dict(r, source=str(header)) if "source" in r else r for r in GCC_ROWS]
    policy = HeaderScope(header.resolve(), (), ())
    assert std_reexports(rows, policy) == {
        "lib::abs": "std::abs",
        "lib::string": "std::string",
    }


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------


def _snap(table: dict[str, str] | None, **kw) -> AbiSnapshot:
    snap = AbiSnapshot(library="lib.so", version="1", **kw)
    snap.std_reexports = table
    return snap


TABLE = {"lib::abs": "std::abs", "lib::string": "std::string"}


def test_a_dropped_reexport_is_reported_once_with_its_std_target() -> None:
    changes = detect_std_reexport_removed(
        _snap(TABLE), _snap({"lib::string": "std::string"})
    )
    assert [(c.kind, c.symbol, c.old_value) for c in changes] == [
        (ChangeKind.STD_REEXPORT_REMOVED, "lib::abs", "lib::abs → std::abs")
    ]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (TABLE, TABLE),  # unchanged
        ({}, TABLE),  # re-exports added
        (TABLE, None),  # new side not modelled: proves nothing
        (None, {}),  # old side not modelled
    ],
)
def test_no_removal_without_both_tables_showing_it(old, new) -> None:
    assert detect_std_reexport_removed(_snap(old), _snap(new)) == []


def test_a_library_definition_replacing_the_reexport_keeps_the_name() -> None:
    new = _snap(
        {},
        functions=[
            Function(name="lib::abs", mangled="_ZN3lib3absEi", return_type="int")
        ],
        types=[RecordType(name="string", kind="class", qualified_name="lib::string")],
    )
    assert detect_std_reexport_removed(_snap(TABLE), new) == []


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", [TABLE, {}, None])
def test_the_table_round_trips(table, tmp_path) -> None:
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    path = tmp_path / "s.json"
    save_snapshot(_snap(table), path)
    assert load_snapshot(path).std_reexports == table
