# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for two castxml-parser fallbacks.

Released castxml versions (≤0.6.x) omit two pieces of information the
model needs:

* no ``refqual`` attribute on ``<Method>`` — the &/&& ref-qualifier is
  only recoverable from the Itanium mangling (``_ZN[rVK]*[RO]…``);
* no ``mangled`` attribute on ``<Destructor>`` — without a fallback every
  virtual destructor is dropped from the reconstructed vtable, making
  each polymorphic type look like it lacks a destructor slot (a false
  POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR).

These tests build synthetic castxml XML fragments, so they run in the
fast default suite without the external tool.
"""
from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.dumper import _CastxmlParser
from abicheck.dumper_castxml import _ref_qualifier_from_mangled
from abicheck.idioms import _has_virtual_destructor
from abicheck.model import RecordType

# ---------------------------------------------------------------------------
# _ref_qualifier_from_mangled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mangled,expected",
    [
        ("_ZNR14MessageBuilder3strEv", "&"),  # str() &
        ("_ZNO14MessageBuilder4takeEv", "&&"),  # take() &&
        ("_ZNKR3Buf4viewEv", "&"),  # view() const &
        ("_ZNKO3Buf4moveEv", "&&"),  # move() const &&
        ("_ZN14MessageBuilder3strEv", ""),  # unqualified method
        ("_ZNK3Buf3getEv", ""),  # const, no ref-qualifier
        ("_ZNSt6vectorIiSaIiEE4sizeEv", ""),  # St substitution ≠ qualifier
        ("_Z4freev", ""),  # non-member function
        ("stat", ""),  # C symbol
    ],
)
def test_ref_qualifier_from_mangled(mangled: str, expected: str) -> None:
    assert _ref_qualifier_from_mangled(mangled) == expected


# ---------------------------------------------------------------------------
# parse_functions fallback (no refqual attribute, like castxml 0.6.x)
# ---------------------------------------------------------------------------


def _root_with_method(mangled: str, refqual: str | None = None) -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "mylib.h")
    ns = SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    del ns
    SubElement(
        root,
        "Class",
        attrib={
            "id": "_7",
            "name": "MessageBuilder",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
            "size": "64",
            "align": "8",
        },
    )
    SubElement(root, "FundamentalType", attrib={"id": "_r", "name": "char"})
    m = SubElement(
        root,
        "Method",
        attrib={
            "id": "_13",
            "name": "str",
            "returns": "_r",
            "context": "_7",
            "access": "public",
            "file": "f1",
            "location": "f1:3",
            "mangled": mangled,
        },
    )
    if refqual is not None:
        m.set("refqual", refqual)
    return root


def _parse_str_method(root: Element):
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    funcs = [f for f in parser.parse_functions() if f.name == "str"]
    assert len(funcs) == 1
    return funcs[0]


def test_method_ref_qualifier_falls_back_to_mangling() -> None:
    fn = _parse_str_method(_root_with_method("_ZNR14MessageBuilder3strEv"))
    assert fn.ref_qualifier == "&"


def test_method_rvalue_ref_qualifier_falls_back_to_mangling() -> None:
    fn = _parse_str_method(_root_with_method("_ZNO14MessageBuilder3strEv"))
    assert fn.ref_qualifier == "&&"


def test_method_refqual_attribute_still_wins_when_present() -> None:
    # Deliberately conflicting inputs: the mangling says && (O) while the
    # attribute says lvalue — the attribute must win, proving precedence.
    fn = _parse_str_method(
        _root_with_method("_ZNO14MessageBuilder3strEv", refqual="lvalue")
    )
    assert fn.ref_qualifier == "&"


def test_method_without_ref_qualifier_stays_empty() -> None:
    fn = _parse_str_method(_root_with_method("_ZN14MessageBuilder3strEv"))
    assert fn.ref_qualifier == ""


# ---------------------------------------------------------------------------
# Virtual destructor vtable fallback (Destructor has no mangled attribute)
# ---------------------------------------------------------------------------


def _root_with_polymorphic_class(*, virtual_dtor: bool) -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "render.h")
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(
        root,
        "Class",
        attrib={
            "id": "_7",
            "name": "Renderer",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
            "size": "128",
            "align": "64",
        },
    )
    SubElement(root, "FundamentalType", attrib={"id": "_v", "name": "void"})
    SubElement(
        root,
        "Method",
        attrib={
            "id": "_13",
            "name": "draw",
            "returns": "_v",
            "context": "_7",
            "access": "public",
            "file": "f1",
            "location": "f1:4",
            "virtual": "1",
            "mangled": "_ZN8Renderer4drawEi",
        },
    )
    dtor = SubElement(
        root,
        "Destructor",
        attrib={
            "id": "_14",
            "name": "Renderer",
            "context": "_7",
            "access": "public",
            "file": "f1",
            "location": "f1:3",
        },
    )
    # Mirror real castxml output: <Destructor> has no mangled attribute.
    if virtual_dtor:
        dtor.set("virtual", "1")
    return root


def _parse_renderer(root: Element) -> RecordType:
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    types = [t for t in parser.parse_types() if t.name == "Renderer"]
    assert len(types) == 1
    return types[0]


def test_virtual_destructor_appears_in_castxml_vtable() -> None:
    rec = _parse_renderer(_root_with_polymorphic_class(virtual_dtor=True))
    assert "~Renderer" in rec.vtable
    assert _has_virtual_destructor(rec)


def test_non_virtual_destructor_not_in_castxml_vtable() -> None:
    rec = _parse_renderer(_root_with_polymorphic_class(virtual_dtor=False))
    assert "~Renderer" not in rec.vtable
    assert not _has_virtual_destructor(rec)


def test_has_virtual_destructor_matches_gcc_unified_dwarf_clone() -> None:
    # GCC's DWARF records the declaration-only destructor as the unified
    # D4 clone (e.g. _ZN8RendererD4Ev), not D0/D1/D2.
    rec = RecordType(
        name="Renderer",
        kind="class",
        vtable=["_ZN8RendererD4Ev", "_ZN8Renderer4drawEi"],
    )
    assert _has_virtual_destructor(rec)


def test_method_literally_named_d4_is_not_a_destructor_clone() -> None:
    # A virtual method *named* D4 mangles as _ZN1C2D4Ev — the D4 sits
    # behind a "2" length prefix, so it is a source name, not a clone.
    # Treating it as a destructor would suppress the
    # POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR risk for exactly the class of
    # types the anti-pattern exists to catch (Codex review, PR #509).
    rec = RecordType(name="C", kind="class", vtable=["_ZN1C2D4Ev"])
    assert not _has_virtual_destructor(rec)


@pytest.mark.parametrize(
    "entry,is_dtor",
    [
        ("_ZN8RendererD1Ev", True),  # plain class
        ("_ZN8RendererD4Ev", True),  # GCC unified clone
        ("_ZN3ns18RendererD1Ev", True),  # nested namespace
        ("_ZN3FooIiED1Ev", True),  # template class
        ("_ZN3FooILi5EED2Ev", True),  # integer template arg (L…E literal)
        ("_ZNSt6vectorIiSaIiEED1Ev", True),  # std:: substitutions
        ("_ZN1C2D4Ev", False),  # method literally named D4
        ("_ZN2D45resetEv", False),  # class literally named D4
        ("_ZN8Renderer4drawEi", False),  # ordinary virtual method
        ("_Z4freev", False),  # non-member function
        ("stat", False),  # C symbol
    ],
)
def test_is_itanium_dtor_symbol_structural(entry: str, is_dtor: bool) -> None:
    from abicheck.idioms import _is_itanium_dtor_symbol

    assert _is_itanium_dtor_symbol(entry) is is_dtor
