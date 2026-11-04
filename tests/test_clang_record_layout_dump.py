"""Built-in clang record-layout pass (known-gaps 2026-11-04, clang layout).

The parser is checked against the layout clang itself dumps, cross-checked
against ``sizeof``/``offsetof`` from a real compile when a compiler exists
(an oracle independent of the parser), and against fixed dumps otherwise.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.compare.internal_leak import _alias_scoped_path_keys
from abicheck.extract.headers.clang.record_layout_dump import (
    normalized_record_name,
    parse_record_layout_dump,
    probe_source,
    probeable_record_name,
)
from abicheck.extract.headers.clang.records import alias_template_target
from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command, abicheck_env

_DERIVED_DUMP = """
*** Dumping AST Record Layout
         0 | class Base
         0 |   (Base vtable pointer)
         8 |   int base_id
        12 |   int extra_field
           | [sizeof=16, dsize=16, align=8,
           |  nvsize=16, nvalign=8]

*** Dumping AST Record Layout
         0 | class Derived
         0 |   class Base (primary base)
         0 |     (Base vtable pointer)
         8 |     int base_id
        12 |     int extra_field
        16 |   int value
           | [sizeof=24, dsize=20, align=8,
           |  nvsize=20, nvalign=8]
"""

_BITFIELD_DUMP = """
*** Dumping AST Record Layout
         0 | struct ns::Flags<int>
     0:0-2 |   unsigned int a
     0:3-7 |   unsigned int b
         1 |   char c
         8 |   struct ns::Inner inner
         8 |     long q
           | [sizeof=16, dsize=16, align=8,
           |  nvsize=16, nvalign=8]
"""


def test_parse_top_level_members_only_and_bases() -> None:
    recs = {r["qualified_name"]: r for r in parse_record_layout_dump(_DERIVED_DUMP)}
    assert set(recs) == {"Base", "Derived"}
    base, derived = recs["Base"], recs["Derived"]
    assert (base["size_bits"], base["alignment_bits"], base["vptr_offset_bits"]) == (
        128,
        64,
        0,
    )
    assert base["fields"] == [
        {"name": "base_id", "offset_bits": 64},
        {"name": "extra_field", "offset_bits": 96},
    ]
    # The base's own members (deeper indent) never leak into Derived.
    assert derived["fields"] == [{"name": "value", "offset_bits": 128}]
    assert derived["bases"] == [{"name": "Base", "offset_bits": 0}]
    assert derived["data_size_bits"] == 160
    assert "vptr_offset_bits" not in derived


def test_parse_bitfields_and_nested_by_value() -> None:
    (rec,) = parse_record_layout_dump(_BITFIELD_DUMP)
    assert rec["qualified_name"] == "ns::Flags<int>"
    assert rec["fields"] == [
        {"name": "a", "offset_bits": 0},
        {"name": "b", "offset_bits": 3},
        {"name": "c", "offset_bits": 8},
        {"name": "inner", "offset_bits": 64},
    ]


@pytest.mark.parametrize(
    "text",
    ["", "garbage", "*** Dumping AST Record Layout\n  0 | class X\n", "***"],
)
def test_parse_rejects_incomplete_entries(text: str) -> None:
    assert parse_record_layout_dump(text) == []


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Foo", True),
        ("ns::Foo<int>", True),
        ("", False),
        ("::Foo", False),
        ("(anonymous struct at a.h:1:1)", False),
        ("ns::(unnamed union at a.h:3:1)", False),
        ("(lambda at a.h:2:3)", False),
    ],
)
def test_probeable_record_name(name: str, ok: bool) -> None:
    assert probeable_record_name(name) is ok


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ns::A<int, long>", "ns::A<int,long>"),
        ("struct ns::A", "ns::A"),
        ("class Buffer<int>", "Buffer< int >"),
    ],
)
def test_normalized_record_name_equates_spellings(a: str, b: str) -> None:
    assert normalized_record_name(a) == normalized_record_name(b)


@pytest.mark.parametrize(
    ("written", "desugared", "expected"),
    [
        # alias template -> its (different) target template
        (
            "detail::pimpl<detail::impl>",
            "std::shared_ptr<mylib::detail::impl>",
            "std::shared_ptr<mylib::detail::impl>",
        ),
        (
            "const pimpl<X> *",
            "const std::unique_ptr<X> *",
            "const std::unique_ptr<X> *",
        ),
        # plain typedef / ordinary specialization keep the written spelling
        ("std::size_t", "unsigned long", "std::size_t"),
        (
            "std::vector<int>",
            "std::vector<int, std::allocator<int>>",
            "std::vector<int>",
        ),
        ("Foo<int>::type", "int", "Foo<int>::type"),
        ("pimpl<X>", "", "pimpl<X>"),
        # same last segment, different template: a::Box aliases b::Box
        ("a::Box<int>", "b::Box<int>", "b::Box<int>"),
        # same template written without its leading scope
        ("vector<int>", "std::vector<int, std::allocator<int>>", "vector<int>"),
    ],
)
def test_alias_template_target(written: str, desugared: str, expected: str) -> None:
    assert alias_template_target(written, desugared) == expected


_BASE_PATH = ["type:P", "P", "base:detail::Impl", "detail::Impl"]


@pytest.mark.parametrize(
    ("records", "aliased"),
    [
        # found from P's enclosing namespace lib -> lib::detail::Impl
        ([("P", "lib::P"), ("Impl", "lib::detail::Impl")], True),
        # found in P's own scope (nested class lookup comes first)
        ([("P", "lib::P"), ("Impl", "lib::P::detail::Impl")], True),
        # name lookup finds nothing: other::detail::Impl is not visible from lib::P
        ([("P", "lib::P"), ("Impl", "other::detail::Impl")], False),
        # two records share the bare name Impl: never merged
        (
            [("P", "lib::P"), ("Impl", "lib::detail::Impl"), ("Impl", "x::Impl")],
            False,
        ),
        # the referencing record is ambiguous, so the lookup scope is unknown
        (
            [("P", "lib::P"), ("P", "y::P"), ("Impl", "lib::detail::Impl")],
            False,
        ),
    ],
)
def test_alias_scoped_path_keys_by_name_lookup(
    records: list[tuple[str, str]], aliased: bool
) -> None:
    paths = {"detail::Impl": [list(_BASE_PATH)]}
    out = _alias_scoped_path_keys(paths, records, ["Impl"])
    assert ("Impl" in out) is aliased
    assert out["detail::Impl"] == paths["detail::Impl"]


def test_alias_scoped_path_keys_signature_path_and_only_resolved_paths() -> None:
    # A public function returning detail::Impl by value: the lookup scope is
    # the function's own (lib::make), so the spelling resolves to lib::detail::Impl.
    fn_path = ["fn:lib::make", "detail::Impl"]
    # A path written from other::Q, where detail::Impl names nothing: it must
    # not ride along on the alias.
    stray = ["type:Q", "Q", "field:x", "detail::Impl"]
    records = [("Q", "other::Q"), ("Impl", "lib::detail::Impl")]
    paths = {"detail::Impl": [fn_path, stray]}
    out = _alias_scoped_path_keys(
        paths, records, ["Impl"], [("fn:lib::make", "lib::make")]
    )
    assert out["Impl"] == [fn_path]
    # Without the function's scope the signature path resolves nowhere.
    assert "Impl" not in _alias_scoped_path_keys(paths, records, ["Impl"])
    # A record no change names is never aliased.
    assert "Impl" not in _alias_scoped_path_keys(
        paths, records, ["Other"], [("fn:lib::make", "lib::make")]
    )


def test_exact_layout_key_is_never_shadowed_by_a_normalized_one() -> None:
    from abicheck.extract.headers.clang.layout_tool import apply_layout_facts
    from abicheck.model import AbiSnapshot, RecordType

    snap = AbiSnapshot(
        library="l",
        version="1",
        types=[
            RecordType(name='Box<"ab">', kind="struct", qualified_name='Box<"ab">'),
            RecordType(name="A<int,long>", kind="struct", qualified_name="A<int,long>"),
        ],
    )
    records = [
        {"qualified_name": 'Box<"a b">', "size_bits": 8, "fields": []},
        {"qualified_name": 'Box<"ab">', "size_bits": 16, "fields": []},
        {"qualified_name": "A<int, long>", "size_bits": 32, "fields": []},
    ]
    sizes = {
        t.name: t.size_bits
        for t in apply_layout_facts(snap, records).declarations.types
    }
    # exact key wins; a unique normalized key still matches
    assert sizes == {'Box<"ab">': 16, "A<int,long>": 32}
    # two dumped records sharing a normalized key: no guess
    ambiguous = [
        {"qualified_name": "A<int, long>", "size_bits": 32, "fields": []},
        {"qualified_name": "A< int,long >", "size_bits": 64, "fields": []},
    ]
    sizes = {
        t.name: t.size_bits
        for t in apply_layout_facts(snap, ambiguous).declarations.types
    }
    assert sizes["A<int,long>"] is None


_ORACLE_HEADER = """
#include <cstddef>
namespace ns {
struct Base { virtual ~Base(); int id; char tag; };
struct Derived : Base { short s; double d; };
template <class T> struct Box { T a; char b; T c; };
extern template struct Box<long>;
}
"""

_ORACLE_RECORDS = {
    "ns::Base": ("id", "tag"),
    "ns::Derived": ("s", "d"),
    "ns::Box<long>": ("a", "b", "c"),
}


@pytest.mark.skipif(shutil.which("clang++") is None, reason="needs clang++")
def test_probe_layout_matches_compiled_sizeof_offsetof(tmp_path: Path) -> None:
    """Every size/offset the parser reports equals what a compiled program
    measures with sizeof/offsetof (an oracle outside the parser)."""
    header = tmp_path / "h.hpp"
    header.write_text(_ORACLE_HEADER, encoding="utf-8")
    probe = tmp_path / "probe.cpp"
    probe.write_text(
        probe_source(f'#include "{header}"\n', list(_ORACLE_RECORDS)), encoding="utf-8"
    )
    dump = subprocess.run(
        [
            "clang++",
            "-std=c++17",
            "-fsyntax-only",
            "-Xclang",
            "-fdump-record-layouts",
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    recs = {r["qualified_name"]: r for r in parse_record_layout_dump(dump)}

    lines = [
        "#include <cstdio>",
        "#include <cstddef>",
        f'#include "{header}"',
        '#pragma clang diagnostic ignored "-Winvalid-offsetof"',
        "ns::Base::~Base() {}",
        "template struct ns::Box<long>;",
        "int main() {",
    ]
    for rec, fields in _ORACLE_RECORDS.items():
        lines.append(f'std::printf("{rec} size %zu\\n", sizeof({rec}));')
        for f in fields:
            lines.append(f'std::printf("{rec} {f} %zu\\n", offsetof({rec}, {f}));')
    lines.append("}")
    src = tmp_path / "oracle.cpp"
    src.write_text("\n".join(lines), encoding="utf-8")
    exe = tmp_path / "oracle"
    subprocess.run(["clang++", "-std=c++17", str(src), "-o", str(exe)], check=True)
    measured = subprocess.run(
        [str(exe)], capture_output=True, text=True, check=True
    ).stdout

    for line in measured.splitlines():
        rec, what, value = line.rsplit(" ", 2)
        facts = recs[rec]
        if what == "size":
            assert facts["size_bits"] == int(value) * 8, line
        else:
            offsets = {f["name"]: f["offset_bits"] for f in facts["fields"]}
            assert offsets[what] == int(value) * 8, line


#: Catalog cases whose expected kinds the clang backend used to miss
#: (known-gaps 2026-11-04): each must now report all of them.
_CATALOG_CASES = {
    "case17_template_abi": {"type_size_changed", "type_field_added"},
    "case43_base_class_member_added": {
        "type_size_changed",
        "type_field_offset_changed",
    },
    "case74_detail_base_class_changed": {"internal_type_leaks_via_public_api"},
    "case75_detail_embedded_by_value": {"internal_type_leaks_via_public_api"},
    "case76_detail_pimpl_vtable_changed": {"internal_type_leaks_via_public_api"},
    "case77_detail_templated_base_changed": {
        "type_field_added",
        "type_field_offset_changed",
        "type_size_changed",
    },
    "case80_pimpl_shared_to_unique": {"type_size_changed", "type_field_type_changed"},
}


@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(_CATALOG_CASES))
def test_clang_backend_reports_catalog_kinds(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    if shutil.which("clang++") is None or shutil.which("g++") is None:
        pytest.skip("needs clang++ and g++")
    root = Path(__file__).resolve().parents[1] / "catalog" / "cases" / case
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    monkeypatch.delenv("ABICHECK_CLANG_LAYOUT_TOOL", raising=False)
    snaps = []
    for v in ("v1", "v2"):
        header = next(p for p in sorted(root.glob(f"{v}.h*")))
        lib = tmp_path / f"lib{v}.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-g",
                "-shared",
                "-fPIC",
                f"-I{root}",
                str(root / f"{v}.cpp"),
                "-o",
                str(lib),
            ],
            check=True,
        )
        snap = tmp_path / f"{v}.json"
        subprocess.run(
            abicheck_command("dump", lib, "-H", header, "-o", snap),
            check=True,
            capture_output=True,
            env=abicheck_env(),
        )
        snaps.append(snap)
    report = tmp_path / "r.json"
    subprocess.run(
        abicheck_command("compare", *snaps, "-o", f"json={report}"),
        capture_output=True,
        env=abicheck_env(),
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    kinds = {c["kind"] for c in data["changes"]}
    if case == "case77_detail_templated_base_changed":
        # `extern template class detail::descriptor_base<...>;` is written in
        # namespace mylib but names mylib::detail's template: the record takes
        # its semantic scope and is sized.
        base = {
            t.qualified_name: t.size_bits
            for t in load_snapshot(snaps[1]).declarations.types
        }
        assert (
            base["mylib::detail::descriptor_base<mylib::task::classification>"] == 64
        )  # two ints
    assert data["verdict"] == "BREAKING"
    assert _CATALOG_CASES[case] <= kinds, sorted(kinds)


@pytest.mark.parametrize(
    "target",
    ["--target=x86_64-w64-windows-gnu", "--target=x86_64-pc-windows-msvc", None],
)
def test_layout_pass_uses_the_binarys_abi_target(
    target: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layout probe lays records out for the ABI the main header dump
    chose from the binary's exports (MinGW Itanium vs MSVC), never clang's
    host default: a different ABI moves the vptr and the base subobjects."""
    from abicheck.extract.headers.clang import layout_tool
    from abicheck.model import AbiSnapshot, RecordType

    header = tmp_path / "a.hpp"
    header.write_text("struct S { int a; };\n", encoding="utf-8")
    snap = AbiSnapshot(
        library="l",
        version="1",
        types=[RecordType(name="S", kind="struct", qualified_name="S")],
    )
    object.__setattr__(snap, "ast_producer", "clang")
    seen: dict[str, tuple[str, ...]] = {}

    def fake_run(binary, headers, includes, **kw):
        seen["tokens"] = tuple(kw["gcc_option_tokens"])
        return [{"qualified_name": "S", "size_bits": 32, "fields": []}]

    monkeypatch.delenv(layout_tool.LAYOUT_TOOL_ENV_VAR, raising=False)
    monkeypatch.setattr(layout_tool, "find_layout_tool_bin", lambda: None)
    monkeypatch.setattr(layout_tool, "_binary_abi_target", lambda *a: target)
    monkeypatch.setattr(layout_tool, "run_layout_tool", fake_run)
    out = layout_tool.attach_clang_layout(snap, [header], [], lang="c++", compile=None)
    assert (
        (target in seen["tokens"])
        if target
        else not any(t.startswith("--target") for t in seen["tokens"])
    )
    assert out.declarations.types[0].size_bits == 32
