"""Unit tests for :func:`unreachable_dependency_type_names` on constructed snapshots.

The classifier decides which DWARF-only records are dependency internals that no
public declaration reaches. These tests build the snapshot directly, so each
branch (own roots, reachability, alias size bounds, pointer aliases, name
collisions, header-derived snapshots, typedef targets) is exercised on exactly
the evidence it reads, without a compiler.
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.entities import RecordType, TypeField
from abicheck.workflows.dump.dwarf_dependency_types import (
    _alias_resolved,
    _alias_targets,
    _base_name,
    _exported_parameter_lists,
    _split_top_level,
    _strip_qualifiers,
    unreachable_dependency_type_names,
)

API = "/src/lib/api.h"
DEP = "/src/dep/fakevec/simd.h"

SCALE = "_ZN3lib5scaleEN7fakevec4simd9basic_vecIfNS1_10avx512_tagEEEf"
SUM = "_ZN3lib3sumERKNS_5StateE"
VECTOR = "fakevec::simd::basic_vec<float, fakevec::simd::avx512_tag>"


def _record(
    name: str, location: str, fields=(), size_bits: int | None = None
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size_bits,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        source_location=location,
    )


def _layout(name: str, fields: dict[str, int], size: int) -> StructLayout:
    return StructLayout(
        name=name,
        byte_size=size,
        fields=[
            FieldInfo(name=f, type_name=f, byte_offset=0, byte_size=s)
            for f, s in fields.items()
        ],
    )


def _snapshot(
    *,
    types: list[RecordType],
    symbols: list[str],
    typedefs: dict[str, str] | None = None,
    layouts: list[StructLayout] | None = None,
    from_headers: bool = False,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib",
        version="1",
        types=types,
        typedefs=typedefs or {},
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name=s, is_default=True) for s in symbols],
            imports=[],
        ),
        dwarf=DwarfMetadata(
            has_dwarf=True,
            structs={layout.name: layout for layout in layouts or []},
        ),
        from_headers=from_headers,
    )


def _library_with_vector_state(
    *, with_scale: bool, typedefs: dict[str, str] | None = None
) -> AbiSnapshot:
    """A library whose State holds the dependency's vector, the shape the
    fake header-only dependency produces. Internal tag and scratch types are
    instantiated by the dependency and never named by the library's API."""
    types = [
        _record("lib::State", f"{API}:5", fields=[("acc", "V"), ("n", "int")]),
        _record("fakevec::simd::avx512_tag", f"{DEP}:6", size_bits=8),
        _record(VECTOR, f"{DEP}:7", size_bits=256),
        _record("fakevec::simd::scratch_pad<float>", f"{DEP}:22", size_bits=128),
    ]
    symbols = [SUM] + ([SCALE] if with_scale else [])
    return _snapshot(
        types=types,
        symbols=symbols,
        typedefs=typedefs,
        layouts=[_layout("lib::State", {"acc": 32, "n": 4}, 40)],
    )


def test_fallback_shape_keeps_the_vector_by_size_and_excludes_tag_and_scratch() -> None:
    # The DWARF-only fallback keeps no typedef map. State's field is spelled
    # with the alias (``V``), so the vector is reached through the field's
    # 32-byte size, and the 8-bit tag and 128-bit scratch are not.
    snap = _library_with_vector_state(with_scale=True)
    assert unreachable_dependency_type_names(snap) == {
        "fakevec::simd::avx512_tag",
        "fakevec::simd::scratch_pad<float>",
    }


def test_resolved_alias_names_its_template_arguments_as_reached() -> None:
    # With the typedef resolved, State's field spells the vector out, and the
    # single-hop walk counts a dependency type named inside a kept field as
    # reached, template arguments included. The tag is then kept; the scratch,
    # named nowhere in the public surface, is still excluded.
    snap = _library_with_vector_state(with_scale=False, typedefs={"lib::V": VECTOR})
    assert unreachable_dependency_type_names(snap) == {
        "fakevec::simd::scratch_pad<float>"
    }


def test_pointer_alias_is_unbounded_and_excludes_nothing() -> None:
    # A pointer to an unresolved name could point at any dependency type, so
    # no dependency record can be declared unreachable.
    types = [
        _record("lib::State", f"{API}:5", fields=[("acc", "V*"), ("n", "int")]),
        _record("fakevec::simd::avx512_tag", f"{DEP}:6", size_bits=8),
    ]
    snap = _snapshot(
        types=types,
        symbols=[SUM],
        layouts=[_layout("lib::State", {"acc": 8, "n": 4}, 16)],
    )
    assert unreachable_dependency_type_names(snap) == frozenset()


def test_header_derived_snapshot_is_already_scoped_and_classified_nothing() -> None:
    snap = _library_with_vector_state(with_scale=True)
    snap.from_headers = True
    assert unreachable_dependency_type_names(snap) == frozenset()


def test_no_located_records_classifies_nothing() -> None:
    snap = _snapshot(types=[_record("lib::State", "", fields=[])], symbols=[SUM])
    assert unreachable_dependency_type_names(snap) == frozenset()


def test_without_an_exported_own_record_there_is_no_own_root() -> None:
    # The exported symbol names nothing the snapshot locates, so no own root
    # exists and nothing is classified: weaker evidence keeps the finding.
    types = [_record("fakevec::simd::avx512_tag", f"{DEP}:6", size_bits=8)]
    snap = _snapshot(types=types, symbols=[SCALE])
    assert unreachable_dependency_type_names(snap) == frozenset()


def test_a_name_shared_with_an_own_record_is_withheld() -> None:
    # The same bare name on both sides could be either declaration, so the
    # rule does not claim it.
    types = [
        _record("lib::State", f"{API}:5", fields=[]),
        _record("Tag", f"{API}:9", size_bits=8),
        _record("Tag", f"{DEP}:6", size_bits=8),
    ]
    snap = _snapshot(types=types, symbols=[SUM])
    assert "Tag" not in unreachable_dependency_type_names(snap)


def test_exported_full_spelling_reaches_a_dependency_record_directly() -> None:
    # A non-template dependency type named exactly by an exported parameter is
    # reached by that name, with no field needed.
    types = [
        _record("lib::State", f"{API}:5", fields=[]),
        _record("fakevec::simd::avx512_tag", f"{DEP}:6", size_bits=8),
    ]
    snap = _snapshot(types=types, symbols=["_ZN3lib5tagerEN7fakevec4simd10avx512_tagE"])
    assert "fakevec::simd::avx512_tag" not in unreachable_dependency_type_names(snap)


def test_split_top_level_respects_template_and_paren_depth() -> None:
    assert _split_top_level("int, basic_vec<float, T>, (a, b)", ",") == [
        "int",
        "basic_vec<float, T>",
        "(a, b)",
    ]


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("const basic_vec<float, T>&", "basic_vec<float, T>"),
        ("lib::State const&", "lib::State"),
        ("int*", "int"),
        ("const volatile int&&", "int"),
    ],
)
def test_strip_qualifiers_keeps_template_arguments(
    spelling: str, expected: str
) -> None:
    assert _strip_qualifiers(spelling) == expected


def test_base_name_drops_template_arguments() -> None:
    assert (
        _base_name("fakevec::simd::basic_vec<float, T>") == "fakevec::simd::basic_vec"
    )


def test_exported_parameter_lists_skip_non_cxx_and_undemangled_symbols() -> None:
    lists = _exported_parameter_lists(
        ["main", "_ZN3lib3sumERKNS_5StateE", "_Znot-a-real-symbol"]
    )
    assert lists == ["lib::State const&"]


def test_alias_targets_drop_unknown_and_refuse_ambiguous_leaves() -> None:
    targets = _alias_targets(
        {
            "lib::V": "basic_vec<float, T>",
            "lib::Unknown": "?",
            "a::Dup": "int",
            "b::Dup": "long",
        }
    )
    assert targets == {
        "lib::V": "basic_vec<float, T>",
        "V": "basic_vec<float, T>",
        "a::Dup": "int",
        "b::Dup": "long",
    }


def test_alias_resolved_copies_and_leaves_the_original_unchanged() -> None:
    record = _record("lib::State", f"{API}:5", fields=[("acc", "V"), ("n", "int")])
    view = _alias_resolved(record, {"V": "basic_vec<float, T>"})
    assert [f.type for f in view.fields] == ["basic_vec<float, T>", "int"]
    assert [f.type for f in record.fields] == ["V", "int"]
    untouched = _record("lib::Plain", f"{API}:9", fields=[("n", "int")])
    assert _alias_resolved(untouched, {"V": "x"}) is untouched
