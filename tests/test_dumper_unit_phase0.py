from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import (
    _cache_key,
    _CastxmlParser,
    _parse_vtable_index,
    _vt_sort_key,
)
from abicheck.model import Visibility


def _mini_root() -> Element:
    root = Element("GCC_XML")

    SubElement(root, "File", id="f1", name="sample.hpp")
    SubElement(root, "FundamentalType", id="t_int", name="int")
    SubElement(root, "FundamentalType", id="t_void", name="void")
    SubElement(root, "PointerType", id="t_int_ptr", type="t_int")
    SubElement(root, "CvQualifiedType", id="t_const_int", type="t_int", const="1")
    SubElement(root, "Typedef", id="td_i", name="I32", type="t_int")

    cls = SubElement(root, "Class", id="c1", name="Widget", size="64", align="64")
    SubElement(cls, "Field", name="x", type="t_int", offset="0")
    SubElement(cls, "Field", name="flags", type="t_int", bits="3", offset="32")

    loc = SubElement(root, "Location", id="loc1", file="f1", line="12")
    assert loc is not None

    # virtual method with vtable index
    SubElement(
        root,
        "Method",
        id="m1",
        context="c1",
        name="draw",
        mangled="_ZN6Widget4drawEv",
        returns="t_int",
        virtual="1",
        vtable_index="0",
        location="loc1",
        attributes="noexcept",
    )

    # extern C function without mangled name
    fn = SubElement(
        root,
        "Function",
        id="fn1",
        name="compute",
        returns="t_int",
        extern="1",
        location="loc1",
    )
    SubElement(fn, "Argument", name="v", type="t_const_int")

    # variable + enum + enum values
    SubElement(root, "Variable", id="v1", name="g_counter", mangled="g_counter", type="t_int")
    en = SubElement(root, "Enumeration", id="e1", name="Mode")
    SubElement(en, "EnumValue", name="FAST", init="1")
    SubElement(en, "EnumValue", name="SLOW", init="2")

    return root


def test_parser_core_paths() -> None:
    root = _mini_root()
    parser = _CastxmlParser(
        root,
        exported_dynamic={"_ZN6Widget4drawEv", "compute"},
        exported_static={"g_counter"},
    )

    funcs = parser.parse_functions()
    draw = next(f for f in funcs if f.name == "draw")
    compute = next(f for f in funcs if f.name == "compute")

    # virtual method
    assert draw.is_virtual
    assert draw.vtable_index == 0
    assert draw.is_noexcept
    assert draw.visibility == Visibility.PUBLIC
    assert draw.return_type == "int"

    # extern C function
    assert compute.is_extern_c
    assert compute.return_type == "int"
    assert compute.visibility == Visibility.PUBLIC
    assert len(compute.params) == 1
    assert compute.params[0].type == "const int"

    vars_ = parser.parse_variables()
    assert len(vars_) == 1
    assert vars_[0].name == "g_counter"
    assert vars_[0].visibility == Visibility.ELF_ONLY

    types = parser.parse_types()
    assert len(types) == 1
    widget = types[0]
    assert widget.name == "Widget"
    assert len(widget.fields) == 2
    assert widget.fields[0].name == "x"
    assert not widget.fields[0].is_bitfield
    assert widget.fields[1].name == "flags"
    assert widget.fields[1].is_bitfield
    assert widget.fields[1].bitfield_bits == 3
    # vtable should contain the mangled name of the virtual draw method
    assert "_ZN6Widget4drawEv" in widget.vtable

    enums = parser.parse_enums()
    assert len(enums) == 1
    assert [m.name for m in enums[0].members] == ["FAST", "SLOW"]
    assert [m.value for m in enums[0].members] == [1, 2]

    typedefs = parser.parse_typedefs()
    assert typedefs["I32"] == "int"


def test_small_utilities() -> None:
    assert _parse_vtable_index(None) is None
    assert _parse_vtable_index("3") == 3
    assert _parse_vtable_index("-2") == -2
    assert _parse_vtable_index("bad") is None

    # vtable sort: numeric indices first, then None
    assert sorted([(None, "a"), (1, "b"), (0, "c")], key=_vt_sort_key) == [
        (0, "c"),
        (1, "b"),
        (None, "a"),
    ]


def test_cache_key_changes_with_inputs(tmp_path: Path) -> None:
    h1 = tmp_path / "a.h"
    h2 = tmp_path / "b.hpp"
    inc = tmp_path / "inc"
    inc.mkdir()
    ih = inc / "x.h"

    h1.write_text("int a();\n")
    h2.write_text("int b();\n")
    ih.write_text("#define X 1\n")

    k1 = _cache_key([h1], [inc], compiler="c++")
    k2 = _cache_key([h1, h2], [inc], compiler="c++")
    k3 = _cache_key([h1], [inc], compiler="cc")

    assert k1 != k2
    assert k1 != k3

    # Determinism: same inputs → same key
    assert k1 == _cache_key([h1], [inc], compiler="c++")


def test_castxml_type_alignment_preserves_typedef_align() -> None:
    root = Element("GCC_XML")
    SubElement(root, "FundamentalType", id="t_int", name="int", align="32")
    SubElement(root, "Typedef", id="td_over", name="over", type="t_int", align="128")
    SubElement(root, "Variable", id="v1", name="g", mangled="g", type="td_over")

    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static={"g"})

    assert parser._type_alignment_bits("td_over") == 128
    assert parser.parse_variables()[0].alignment_bits == 128


def test_castxml_type_alignment_falls_through_unaligned_typedef() -> None:
    root = Element("GCC_XML")
    SubElement(root, "FundamentalType", id="t_int", name="int", align="32")
    SubElement(root, "Typedef", id="td_plain", name="plain", type="t_int")

    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())

    assert parser._type_alignment_bits("td_plain") == 32
