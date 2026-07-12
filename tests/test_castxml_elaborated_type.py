# Copyright 2026 Nikolay Petrov
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

"""Regression test for castxml ``ElaboratedType`` unwrapping.

castxml wraps a type written with an elaborated-type-specifier (plain C
``enum Status`` / ``struct Foo`` / ``union Foo`` used directly, rather than
through a typedef) in an ``ElaboratedType`` node that carries no ``name`` of
its own — only a ``type`` attribute pointing at the real underlying type.
``_CastxmlParser._type_name`` previously had no case for this tag, so it fell
through to the generic fallback and returned the literal string
``"ElaboratedType"`` as the resolved type name. That broke public-surface
reachability: a function like ``enum Status get_status(void);`` reported a
return type of ``"ElaboratedType"`` instead of ``"Status"``, so the enum
looked unreachable from any public API root and its member changes were
silently scoped out as ``non-public-type`` / ``no-provenance`` instead of
surfacing as real ABI breaks.
"""

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser


def _make_root_with_elaborated_enum_return() -> Element:
    """Mirror castxml output for ``enum Status get_status(void);``."""
    root = Element("CastXML", attrib={"format": "1.4.0"})

    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "lib.h")

    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

    enum_el = SubElement(root, "Enumeration")
    enum_el.set("id", "_3")
    enum_el.set("name", "Status")
    enum_el.set("context", "_1")
    enum_el.set("file", "f1")
    enum_el.set("location", "f1:1")

    # The elaborated-type-specifier wrapper castxml inserts for `enum Status`
    # used directly (no typedef) as a function's return type.
    elab = SubElement(root, "ElaboratedType")
    elab.set("id", "_4")
    elab.set("type", "_3")

    func = SubElement(root, "Function")
    func.set("id", "_5")
    func.set("name", "get_status")
    func.set("returns", "_4")
    func.set("context", "_1")
    func.set("file", "f1")
    func.set("location", "f1:2")
    func.set("mangled", "get_status")

    return root


class TestElaboratedTypeUnwrapping:
    def test_function_return_type_resolves_through_elaborated_type(self) -> None:
        root = _make_root_with_elaborated_enum_return()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].return_type == "Status"

    def test_type_name_unwraps_elaborated_type_directly(self) -> None:
        root = _make_root_with_elaborated_enum_return()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        assert parser._type_name("_4") == "Status"
