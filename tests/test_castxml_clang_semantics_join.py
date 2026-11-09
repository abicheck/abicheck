# SPDX-License-Identifier: Apache-2.0
"""Unit tests for castxml's clang-semantics join and the generation-45 noexcept migration.

The integration oracle is ``tests/test_noexcept_header_backends.py``; these
pin the pieces it rests on without a compiler: the clang ``noexcept`` map's
keys, the per-function rule (clang's reading, else unknown in a file that can
spell one, else castxml's value), and the load migration of an older castxml
baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.headers.castxml.clang_semantics import (
    SEMANTICS_ATTR,
    CastxmlClangSemantics,
    load_clang_semantics,
)
from abicheck.extract.headers.castxml.clang_semantics_join import (
    clang_noexcept_map,
    join_clang_semantics,
    special_member_key,
)
from abicheck.storage.castxml_noexcept_migration import migrate_castxml_noexcept


def _fn(
    kind: str, name: str, qual: str, mangled: str | None = None, **kw: object
) -> dict:
    node: dict = {"kind": kind, "name": name, "type": {"qualType": qual}, **kw}
    if mangled:
        node["mangledName"] = mangled
    return node


def _record(name: str, *members: dict) -> dict:
    return {"kind": "CXXRecordDecl", "name": name, "inner": list(members)}


def test_clang_map_reads_each_specification_by_mangled_and_special_member_key() -> None:
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _fn("FunctionDecl", "f", "void () noexcept", "_Z1fv"),
            _fn("FunctionDecl", "g", "void () noexcept(false)", "_Z1gv"),
            _fn("FunctionDecl", "h", "void (int)", "_Z1hi"),
            {
                "kind": "NamespaceDecl",
                "name": "ns",
                "inner": [
                    _record(
                        "S",
                        _record("S"),  # the injected class name
                        _fn(
                            "CXXDestructorDecl",
                            "~S",
                            "void () noexcept",
                            "_ZN2ns1SD1Ev",
                        ),
                        _fn(
                            "CXXConstructorDecl",
                            "S",
                            "void (int) noexcept",
                            inner=[{"kind": "ParmVarDecl", "name": "n"}],
                        ),
                        _fn("CXXConstructorDecl", "S", "void ()", isImplicit=True),
                    )
                ],
            },
        ],
    }
    assert clang_noexcept_map(root) == {
        "_Z1fv": True,
        "_Z1gv": False,
        "_Z1hi": False,
        "fn:f#0": True,
        "fn:g#0": False,
        "fn:h#0": False,  # a FunctionDecl's arity is its ParmVarDecl children
        "_ZN2ns1SD1Ev": True,
        "dtor:ns::S": True,
        "ctor:ns::S#1": True,
    }


def test_same_arity_constructors_that_disagree_are_unknown() -> None:
    parm = [{"kind": "ParmVarDecl", "name": "x"}]
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _record(
                "R",
                _fn("CXXConstructorDecl", "R", "void (int) noexcept", inner=parm),
                _fn("CXXConstructorDecl", "R", "void (double)", inner=parm),
            )
        ],
    }
    assert "ctor:R#1" not in clang_noexcept_map(root)


@pytest.mark.parametrize(
    ("tag", "cls", "params", "key"),
    [
        ("Destructor", "ns::S", [], "dtor:ns::S"),
        ("Constructor", "ns::S", ["a", "b"], "ctor:ns::S#2"),
        ("Method", "ns::S", [], ""),
        ("Constructor", "", [], ""),
    ],
)
def test_special_member_key(tag: str, cls: str, params: list[str], key: str) -> None:
    assert special_member_key(tag, cls, params) == key


@pytest.mark.parametrize(
    ("joined", "file", "artificial", "expected"),
    [
        ({"_Z1fv": True}, "/i/api.h", False, True),  # clang's reading wins
        ({"_Z1fv": False}, "/i/api.h", False, False),
        ({}, "/i/api.h", False, None),  # file spells one, join silent: unknown
        (None, "/i/api.h", False, None),  # no join at all: unknown
        (None, "/i/other.h", False, False),  # file spells none: castxml's value
        (None, "/i/api.h", True, False),  # compiler-generated: castxml's value
    ],
)
def test_function_noexcept_rule(
    joined: dict[str, bool] | None, file: str, artificial: bool, expected: bool | None
) -> None:
    sem = CastxmlClangSemantics(noexcept=joined, mention_files=frozenset({"/i/api.h"}))
    assert (
        sem.function_noexcept(("", "_Z1fv"), file, False, artificial=artificial)
        is expected
    )


def test_semantics_round_trip_through_the_root_attribute() -> None:
    sem = CastxmlClangSemantics(
        noexcept={"_Z1fv": True},
        mention_files=frozenset({"/i/api.h"}),
        concepts=[("template f(int)", "conj:", "/i/api.h")],
    )
    root = Element("CastXML")
    root.set(
        SEMANTICS_ATTR,
        json.dumps(
            {
                "noexcept": sem.noexcept,
                "mention_files": sorted(sem.mention_files),
                "concepts": [list(e) for e in sem.concepts or []],
            }
        ),
    )
    loaded = load_clang_semantics(root)
    assert (loaded.noexcept, loaded.mention_files, loaded.concepts) == (
        sem.noexcept,
        sem.mention_files,
        sem.concepts,
    )
    assert load_clang_semantics(Element("CastXML")).noexcept is None


def test_a_c_parse_is_never_joined(tmp_path: Path) -> None:
    header = tmp_path / "api.h"
    header.write_text("int f(void) __THROW;\n")
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name=str(header))
    SubElement(root, "Function", id="_1", name="f", mangled="f", file="f1")
    out = join_clang_semantics(
        root, "k", headers=[header], extra_includes=[], compiler="cc",
        gcc_path=None, gcc_prefix=None, gcc_options=None, gcc_option_tokens=(),
        sysroot=None, nostdinc=False, lang="c", extra_hash_dirs=(),
        exported_symbols=frozenset(),
    )  # fmt: skip
    assert out is root and out.get(SEMANTICS_ATTR) is None


def _doc(producer: str, functions: list[dict]) -> dict:
    return {"ast_producer": producer, "functions": functions}


def test_pre_45_castxml_noexcept_false_loads_as_unknown() -> None:
    doc = _doc(
        "castxml",
        [
            {"mangled": "_ZNK5Shape4areaEv", "is_noexcept": False},
            {"mangled": "__abicheck_ctor__Shape()", "is_noexcept": False},
            {"mangled": "~Shape", "is_noexcept": False},
            {"mangled": "c_api", "is_noexcept": False},  # C: no specification
            {"mangled": "_Z1fv"},  # key absent: the stored default False
        ],
    )
    migrated, notices = migrate_castxml_noexcept(doc, 44)
    assert [f.get("is_noexcept", False) for f in migrated["functions"]] == [
        None,
        None,
        None,
        False,
        None,
    ]
    assert notices and "re-dump" in notices[0]
    assert doc["functions"][0]["is_noexcept"] is False  # not mutated in place


@pytest.mark.parametrize(
    ("producer", "generation"), [("clang", 44), ("hybrid", 44), ("castxml", 45)]
)
def test_migration_leaves_other_producers_and_new_generations(
    producer: str, generation: int
) -> None:
    doc = _doc(producer, [{"mangled": "_Z1fv", "is_noexcept": False}])
    assert migrate_castxml_noexcept(doc, generation) == (doc, [])


def test_an_unobserved_specification_survives_a_save_and_load(tmp_path: Path) -> None:
    """Live and stored operands agree: the not-observed state is carried by
    the ``abicheck.noexcept_unobserved`` extension fact, and an observed
    ``True``/``False`` by the stored value."""
    from abicheck.model import AbiSnapshot, FactStatus, Function
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[
            Function("f", "_Z1fv", "void", is_noexcept=None),
            Function("g", "_Z1gv", "void", is_noexcept=True),
            Function("h", "_Z1hv", "void"),
        ],
    )
    path = tmp_path / "s.json"
    save_snapshot(snap, path)
    loaded = {f.mangled: f for f in load_snapshot(path).declarations.functions}
    assert [
        (m, loaded[m].is_noexcept, loaded[m].is_noexcept_fact.status)  # type: ignore[union-attr]
        for m in ("_Z1fv", "_Z1gv", "_Z1hv")
    ] == [
        ("_Z1fv", False, FactStatus.NOT_COLLECTED),
        ("_Z1gv", True, FactStatus.PRESENT),
        ("_Z1hv", False, FactStatus.PRESENT),
    ]
    assert "is_noexcept_fact" not in path.read_text()


def test_mention_gate_reads_only_local_headers(tmp_path: Path) -> None:
    """The clang pass runs only for a header beside a given one that can spell
    an exception specification: a system header that spells ``noexcept``
    does not trigger it, a local one spelling it through a macro does."""
    from abicheck.extract.headers.castxml.clang_semantics_join import _mention_files

    inc = tmp_path / "include"
    inc.mkdir()
    api = inc / "api.h"
    api.write_text('#include "detail/cfg.h"\nint f() API_NOTHROW;\n')
    (inc / "detail").mkdir()
    cfg = inc / "detail" / "cfg.h"
    cfg.write_text("#define API_NOTHROW\n")
    plain = inc / "plain.h"
    plain.write_text("int g();\n")
    system = tmp_path / "sys" / "string.h"
    system.parent.mkdir()
    system.write_text("void s() noexcept;\n")
    root = Element("CastXML")
    for i, path in enumerate((api, cfg, plain, system)):
        SubElement(root, "File", id=f"f{i}", name=str(path))
    assert _mention_files(root, [api]) == frozenset({str(api), str(cfg)})
    assert _mention_files(root, [plain]) == frozenset({str(api), str(cfg)})
    assert _mention_files(root, [system]) == frozenset({str(system)})


def test_concept_entries_keep_only_public_unambiguous_declarations() -> None:
    """The castxml path filters the clang join's entries by its own public
    set: a private-header template is never recorded (so never reported), and
    a key two public declarations constrain differently is left out."""
    from abicheck.extract.declaration_provenance import build_public_set
    from abicheck.extract.headers.clang.concept_constraints import (
        public_concept_constraints,
    )

    segs, dirs, have = build_public_set(["/pkg/include/api.h"], [])
    assert have
    entries = [
        ("template f(int)", "conj:a", "/pkg/include/api.h"),
        ("template g(int)", "conj:b", "/pkg/src/private.h"),
        ("template h(int)", "conj:c", "/pkg/include/api.h"),
        ("template h(int)", "conj:d", "/pkg/include/api.h"),
        ("template k(int)", "conj:e", "/pkg/include/api.h"),
        ("template k(int)", "conj:e", "/pkg/include/api.h"),
    ]
    assert public_concept_constraints(entries, segs, dirs) == {
        "template f(int)": "conj:a",
        "template k(int)": "conj:e",
    }


def test_function_key_does_not_need_a_mangled_name() -> None:
    """Without (or with a foreign) mangled name, a function is still keyed by
    its qualified name, arity and const-ness; overloads that disagree drop."""
    from abicheck.extract.headers.castxml.clang_semantics_join import function_decl_key

    parm = {"kind": "ParmVarDecl", "name": "x"}
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "ns",
                "inner": [
                    _record(
                        "S",
                        _fn(
                            "CXXMethodDecl",
                            "area",
                            "int () const noexcept",
                            "?area@S@ns@@QEBAHXZ",
                        ),
                        _fn("CXXMethodDecl", "area", "int ()"),
                        _fn("CXXMethodDecl", "g", "void (int) noexcept", inner=[parm]),
                        _fn("CXXMethodDecl", "g", "void (double)", inner=[parm]),
                    ),
                    _fn("FunctionDecl", "count", "int (int)", inner=[parm]),
                ],
            }
        ],
    }
    got = clang_noexcept_map(root)
    assert got[function_decl_key("ns::S", "area", 0, True)] is True
    assert got[function_decl_key("ns::S", "area", 0, False)] is False
    assert got[function_decl_key("ns", "count", 1, False)] is False
    assert function_decl_key("ns::S", "g", 1, False) not in got  # overloads disagree
    assert function_decl_key("ns::S", "~S", 0, False) == ""
