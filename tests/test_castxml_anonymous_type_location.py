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

"""Regression test for castxml lambda-closure/anonymous-type name location
leakage (defect: identical headers in two different checkout directories
compared as BREAKING, since a lambda closure type's ``name`` embeds an
absolute source path -- ``"raii_guard<(lambda at /tmp/old/lib.hpp:4:37)>"``
vs. ``"raii_guard<(lambda at /tmp/new/lib.hpp:4:37)>"`` -- and old/new type
matching (``diff_helpers.type_map_key``) keys on that raw, unstripped
spelling. The clang JSON-AST frontend already strips this
(``dumper_clang_expr._normalize_qual_type``); the castxml frontend had no
equivalent.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.diff_helpers import type_map_key
from abicheck.dumper import _CastxmlParser


def _file(root: Element, file_id: str, name: str) -> None:
    f = SubElement(root, "File")
    f.set("id", file_id)
    f.set("name", name)


def _lambda_struct_root(source_path: str) -> Element:
    """Mirror castxml output for a class template instantiated with a
    lambda closure type, e.g. ``raii_guard<decltype([]{})>``."""
    root = Element("CastXML", attrib={"format": "1.4.0"})
    _file(root, "f1", source_path)
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

    struct_el = SubElement(root, "Struct")
    struct_el.set("id", "_2")
    struct_el.set(
        "name",
        f"raii_guard<(lambda at {source_path}:4:37)>",
    )
    struct_el.set("context", "_1")
    struct_el.set("file", "f1")
    struct_el.set("line", "4")
    struct_el.set("members", "")

    return root


class TestLambdaClosureTypeNameLocationStripped:
    def test_type_name_strips_embedded_source_location(self) -> None:
        root = _lambda_struct_root("/tmp/old/lib.hpp")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        # The path is stripped; the :line:col discriminator is kept (Codex
        # review) so two distinct lambdas in the same header don't collapse
        # to one identity — see TestDistinctLambdasInOneSnapshotStayDistinct.
        assert parser._type_name("_2") == "raii_guard<(lambda:4:37)>"

    def test_record_type_own_name_has_no_embedded_path(self) -> None:
        root = _lambda_struct_root("/tmp/old/lib.hpp")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        (rec,) = [t for t in types if t.name.startswith("raii_guard")]
        assert rec.name == "raii_guard<(lambda:4:37)>"
        assert "/tmp/old" not in rec.name

    def test_two_checkout_directories_produce_identical_type_identity(self) -> None:
        # The actual bug: the same declaration, compiled from two different
        # checkout directories, must resolve to the SAME type_map_key so
        # old/new matching doesn't manufacture a spurious
        # type_removed/type_added pair for an unchanged type.
        old_root = _lambda_struct_root("/tmp/old/lib.hpp")
        new_root = _lambda_struct_root("/tmp/new/lib.hpp")
        old_parser = _CastxmlParser(
            old_root, exported_dynamic=set(), exported_static=set()
        )
        new_parser = _CastxmlParser(
            new_root, exported_dynamic=set(), exported_static=set()
        )
        (old_rec,) = [
            t for t in old_parser.parse_types() if t.name.startswith("raii_guard")
        ]
        (new_rec,) = [
            t for t in new_parser.parse_types() if t.name.startswith("raii_guard")
        ]
        assert type_map_key(old_rec) == type_map_key(new_rec)


class TestDistinctLambdasInOneSnapshotStayDistinct:
    """Codex review, real finding: stripping the *entire* location (path
    AND line:col) collapsed two distinct lambda closure types declared in
    the same header into one identical key, so diff_helpers.TypeMap
    silently overwrote one entry with the other -- changes to the
    discarded instantiation could be missed or compared against the wrong
    record. Keeping :line:col as a discriminator fixes this while still
    matching across checkout roots (the test above)."""

    def test_two_lambdas_in_one_header_produce_distinct_type_map_keys(self) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/tmp/old/lib.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        first = SubElement(root, "Struct")
        first.set("id", "_2")
        first.set("name", "guard<(lambda at /tmp/old/lib.hpp:4:3)>")
        first.set("context", "_1")
        first.set("file", "f1")
        first.set("line", "4")
        first.set("members", "")

        second = SubElement(root, "Struct")
        second.set("id", "_3")
        second.set("name", "guard<(lambda at /tmp/old/lib.hpp:40:3)>")
        second.set("context", "_1")
        second.set("file", "f1")
        second.set("line", "40")
        second.set("members", "")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = [t for t in parser.parse_types() if t.name.startswith("guard")]
        assert len(types) == 2
        names = {t.name for t in types}
        assert names == {"guard<(lambda:4:3)>", "guard<(lambda:40:3)>"}
        keys = {type_map_key(t) for t in types}
        assert len(keys) == 2, "distinct lambdas must not collide on one key"


class TestAnonymousEnumLocationStripped:
    def test_enum_name_has_no_embedded_path(self) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        _file(root, "f1", "/tmp/old/lib.hpp")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

        enum_el = SubElement(root, "Enumeration")
        enum_el.set("id", "_2")
        enum_el.set("name", "(unnamed enum at /tmp/old/lib.hpp:56:5)")
        enum_el.set("context", "_1")
        enum_el.set("file", "f1")
        enum_el.set("line", "56")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (enum_type,) = parser.parse_enums()
        assert enum_type.name == "(unnamed enum:56:5)"
        assert "/tmp/old" not in enum_type.name
