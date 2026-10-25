"""Benign duplicate function declarations collapse at the castxml source.

Bug class ``identity.benign_duplicate_declaration_key``: glibc's C++ cv
overloads (``wcschr``/``strchr``/... sharing one asm name) and libstdc++'s
copy/move constructor next to a same-signature converting-constructor template
(one ``__abicheck_ctor__std::...`` placeholder key) used to reach the snapshot
as two records under one key, so every dump of a header including
``<memory>``/``<cstring>`` warned "Duplicate mangled symbols skipped
(first-wins)". A group whose members all come from system headers and agree
modulo cv-qualification is one entity: it keeps exactly its first declaration.
A duplicate reaching the library's own headers, or with conflicting
signatures, is left to the snapshot index, which still warns.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import pprint
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.declaration_provenance import is_system_header
from abicheck.extract.headers.castxml.duplicate_declarations import (
    collapse_benign_duplicates,
)
from abicheck.model import AbiSnapshot, Function, Param

REPO = Path(__file__).resolve().parents[1]


def _fn(name: str, mangled: str, ret: str, *params: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
    )


def _parsed(funcs: list[Function]) -> list[tuple[Element, Function]]:
    return [(Element("Function", id=f"_{i}"), f) for i, f in enumerate(funcs)]


_GLIBC = [
    _fn("wcschr", "wcschr", "wchar_t*", "wchar_t*", "wchar_t"),
    _fn("wcschr", "wcschr", "const wchar_t*", "const wchar_t*", "wchar_t"),
]
_STD_CTOR_KEY = "__abicheck_ctor__std::shared_ptr<X>(const shared_ptr<X>&)"
_STD_CTOR = [
    _fn("shared_ptr", _STD_CTOR_KEY, "void", "const shared_ptr<X>&"),
    _fn("shared_ptr", _STD_CTOR_KEY, "void", "const shared_ptr<X>&"),
]
#: mingw-w64's ``<wchar.h>`` and ``swprintf.inl`` both declare ``_vswprintf``,
#: once through ``va_list`` and once with the builtin it names.
_MINGW_VA_LIST = [
    _fn("_vswprintf", "_vswprintf", "int", "wchar_t*", "const wchar_t*", "va_list"),
    _fn(
        "_vswprintf",
        "_vswprintf",
        "int",
        "wchar_t*",
        "const wchar_t*",
        "__builtin_va_list",
    ),
]


@pytest.mark.parametrize(
    "group",
    [_GLIBC, _STD_CTOR, _MINGW_VA_LIST],
    ids=["glibc_cv", "std_ctor", "mingw_va_list"],
)
def test_benign_group_keeps_first_declaration_in_any_order(
    group: list[Function], caplog: pytest.LogCaptureFixture
) -> None:
    other = _fn("f", "_Z1fv", "void")
    for perm in itertools.permutations([*group, other]):
        out = collapse_benign_duplicates(_parsed(list(perm)), lambda _el: True)
        dup_first = next(f for f in perm if f is not other)
        assert out == [f for f in perm if f is other or f is dup_first]
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="abicheck.model"):
            AbiSnapshot(library="l", version="1", functions=out).index()
        assert not [r for r in caplog.records if "first-wins" in r.getMessage()]


@pytest.mark.parametrize(
    ("group", "dependency"),
    [
        (_GLIBC, [False, True]),  # one member from the library's own header
        (_STD_CTOR, [False, False]),
        (
            [  # conflicting signatures under one key, both system headers
                _fn("g", "_Z1gi", "int", "int"),
                _fn("g", "_Z1gi", "int", "long"),
            ],
            [True, True],
        ),
        ([_fn("h", "_Z1hv", "int"), _fn("h", "_Z1hv", "double")], [True, True]),
        (  # exception specifications are not mangled: same key, two entities
            [
                _fn("k", "_Z1kv", "void"),
                dataclasses.replace(_fn("k", "_Z1kv", "void"), is_noexcept=True),
            ],
            [True, True],
        ),
    ],
)
def test_conflicting_or_public_duplicate_is_kept_and_still_warns(
    group: list[Function], dependency: list[bool], caplog: pytest.LogCaptureFixture
) -> None:
    parsed = _parsed(group)
    by_el = {id(el): p for (el, _), p in zip(parsed, dependency, strict=True)}
    out = collapse_benign_duplicates(parsed, lambda el: by_el[id(el)])
    assert out == group
    with caplog.at_level(logging.WARNING, logger="abicheck.model"):
        AbiSnapshot(library="l", version="1", functions=out).index()
    assert [r for r in caplog.records if "first-wins" in r.getMessage()]


def _two_ctor_root(header: str) -> Element:
    """castxml shape of two same-key artificial ctors (one
    ``__abicheck_ctor__W()`` key) declared in *header*."""
    root = Element("CastXML", attrib={"format": "1.4.0"})
    SubElement(root, "File", attrib={"id": "f1", "name": header})
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(
        root,
        "Class",
        attrib={
            "id": "_2",
            "name": "W",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
        },
    )
    for i in (3, 4):
        SubElement(
            root,
            "Constructor",
            attrib={
                "id": f"_{i}", "name": "W", "context": "_2", "access": "public",
                "file": "f1", "line": "1", "artificial": "1",
            },
        )  # fmt: skip
    return root


@pytest.mark.parametrize(
    ("header", "public", "kept"),
    [
        ("/usr/include/c++/13/bits/w.h", None, 2),  # no public set: unknown
        ("/usr/include/mylib/w.h", None, 2),  # installed library, no public set
        ("/usr/include/c++/13/bits/w.h", ["/opt/lib/api.h"], 1),  # system
        ("/usr/include/mylib/w.h", ["/usr/include/mylib/w.h"], 2),  # declared public
        ("/src/mylib/include/w.h", None, 2),  # project header, no public set
        ("/src/mylib/include/w.h", ["/src/mylib/include/api.h"], 2),  # private
        # an installed library's private header beside its public one
        ("/usr/include/mylib/detail.h", ["/usr/include/mylib/api.h"], 2),
        # ... while the toolchain's headers stay dependencies of that library
        ("/usr/include/c++/13/bits/w.h", ["/usr/include/mylib/api.h"], 1),
        # a public header straight in a bare system root owns nothing there
        ("/usr/include/c++/13/bits/w.h", ["/usr/include/zlib.h"], 1),
        # standalone mingw-w64 CRT, as castxml reports it on Windows
        (
            "C:/mingw64/bin/../lib/gcc/x86_64-w64-mingw32/15.2.0/../../../../"
            "x86_64-w64-mingw32/include\\wchar.h",
            ["C:/src/mylib/include/api.h"],
            1,
        ),
        ("C:\\work\\my-w64-tool\\include\\w.h", ["C:/src/api.h"], 2),  # project dir
    ],
)
def test_parser_collapses_only_provable_system_header_duplicates(
    header: str, public: list[str] | None, kept: int
) -> None:
    """Through the real castxml parser: the provenance oracle is the header
    path itself -- only a toolchain/system header outside a given public set
    is a dependency the library cannot have meant twice. With no public set
    a system-prefixed path may be the library itself, so both are kept."""
    from abicheck.extract.headers.castxml.dumper import _CastxmlParser

    parser = _CastxmlParser(
        _two_ctor_root(header), set(), set(), public_header_paths=public
    )
    names = [f.mangled for f in parser.parse_functions() if f.name == "W"]
    assert len(names) == kept
    assert len(set(names)) == 1


_CASES = ("case80_pimpl_shared_to_unique", "case126_sycl_device_impl_ptr")


class _Records(logging.Handler):
    """Every ``abicheck.model`` record, attached to the logger itself: the
    in-process dump reconfigures logging, so ``caplog`` misses them."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _dump_and_index_first_wins(
    lib: Path, header: Path
) -> tuple[AbiSnapshot, list[str]]:
    """Dump *lib* against *header* the way the depth-metamorphic run does
    (in-process ``dump``, then ``index``) and return its first-wins warnings."""
    from abicheck.dumper import dump

    logger = logging.getLogger("abicheck.model")
    handler, old_level = _Records(), logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        snap = dump(lib, headers=[header], version="v1")
        snap.index()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return snap, [m for m in handler.messages if "first-wins" in m]


def _build(src: Path, out: Path, *include: Path) -> Path:
    if not shutil.which("castxml") or not shutil.which("g++"):
        pytest.skip("needs castxml and g++")
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-std=c++17",
         *(f"-I{d}" for d in include), "-o", str(out), str(src)],
        check=True,
    )  # fmt: skip
    return out


@pytest.mark.integration
@pytest.mark.model_warning_hygiene
@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("v", ["v1", "v2"])
def test_catalog_case_dump_logs_no_first_wins_duplicate(
    case: str, v: str, tmp_path: Path
) -> None:
    """case80/case126 include ``<memory>``/``<cstring>``: before the fix each
    dump warned about ``wcschr (x2)`` ... and ``__abicheck_ctor__std::...
    (x2)``."""
    src = REPO / "catalog" / "cases" / case
    lib = _build(src / f"{v}.cpp", tmp_path / f"lib{v}.so", src)
    snap, first_wins = _dump_and_index_first_wins(lib, src / f"{v}.h")
    groups = _same_key_groups(snap)
    assert first_wins == [], pprint.pformat(
        {"ast_producer": snap.ast_producer, "groups": groups}, width=120
    )
    assert groups == {}


def _same_key_groups(snap: AbiSnapshot) -> dict[str, list[tuple[object, ...]]]:
    """Every function key the dump holds more than once, with the facts the
    collapse compares and the provenance it reads -- the failure message that
    says *why* a group was kept on a toolchain this suite cannot run locally."""
    by_key: dict[str, list[Function]] = {}
    for f in snap.declarations.functions:
        by_key.setdefault(f.mangled, []).append(f)
    return {
        key: [
            (
                f.return_type,
                tuple(p.type for p in f.params),
                f.is_variadic,
                f.is_noexcept,
                f.exception_spec,
                f.source_header,
                str(f.origin),
                is_system_header(f.source_header),
            )
            for f in funcs
        ]  # fmt: skip
        for key, funcs in by_key.items()
        if len(funcs) > 1
    }


#: Same-key pairs in the library's *own* header (asm-labelled to one symbol,
#: the glibc mechanism): not collapsed, so the index still warns.
_PUBLIC_DUPLICATES = {
    "conflicting": (
        'extern "C++" int lookup(int key) __asm__("api_lookup");\n'
        'extern "C++" double lookup(double key) __asm__("api_lookup");\n'
    ),
    "cv_only": (
        'extern "C++" char* lookup(char* s) __asm__("api_lookup");\n'
        'extern "C++" const char* lookup(const char* s) __asm__("api_lookup");\n'
    ),
}


@pytest.mark.integration
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF asm labels")
@pytest.mark.parametrize("shape", sorted(_PUBLIC_DUPLICATES))
def test_library_header_duplicate_still_warns(shape: str, tmp_path: Path) -> None:
    header = tmp_path / "api.h"
    header.write_text(
        "#pragma once\n" + _PUBLIC_DUPLICATES[shape] + "int other(int);\n",
        encoding="utf-8",
    )
    impl = tmp_path / "api.cpp"
    impl.write_text(
        '#include "api.h"\n'
        'extern "C" void* api_lookup(void* k) { return k; }\n'
        "int other(int x) { return x; }\n",
        encoding="utf-8",
    )
    lib = _build(impl, tmp_path / "libapi.so", tmp_path)
    snap, first_wins = _dump_and_index_first_wins(lib, header)
    assert len(first_wins) == 1 and "api_lookup (\u00d72)" in first_wins[0]
    assert [f.mangled for f in snap.declarations.functions].count("api_lookup") == 2
