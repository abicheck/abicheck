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

"""ADR-063 Phase 0: CastXML now constructs ``Fact[...]`` siblings directly
at parse time, rather than relying on the ``bridge_legacy_and_fact``
omission sentinel to infer them.

Two things are pinned here: (1) ``bases``/``virtual_bases``/``vtable``/
``vptr_offset_bits`` are stated as ``Fact.present(...)`` explicitly,
matching the legacy value exactly (castxml resolves these itself via real
semantic analysis, opaque records included); (2) ``Param.is_va_list_fact``
is ``Fact.unsupported()`` — a real, deliberate divergence from what the
omission bridge alone would produce (``NOT_COLLECTED``), since castxml can
never determine va_list-ness for any parameter, on any run.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser
from abicheck.model.fact import FactStatus


def _base_root() -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    f1 = SubElement(root, "File", attrib={"id": "f1", "name": "lib.h"})
    del f1
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_v", "name": "void"})
    SubElement(root, "FundamentalType", attrib={"id": "_i", "name": "int"})
    return root


def _polymorphic_struct_root() -> Element:
    root = _base_root()
    SubElement(
        root,
        "Struct",
        attrib={
            "id": "_5",
            "name": "Base",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
            "size": "64",
            "align": "8",
        },
    )
    SubElement(
        root,
        "Struct",
        attrib={
            "id": "_7",
            "name": "Widget",
            "context": "_1",
            "file": "f1",
            "location": "f1:2",
            "size": "128",
            "align": "8",
        },
    )
    SubElement(root.find("Struct[@id='_7']"), "Base", attrib={"type": "_5"})
    SubElement(
        root,
        "Method",
        attrib={
            "id": "_13",
            "name": "run",
            "returns": "_v",
            "context": "_7",
            "access": "public",
            "file": "f1",
            "location": "f1:3",
            "virtual": "1",
            "mangled": "_ZN6Widget3runEv",
        },
    )
    return root


def _opaque_struct_root() -> Element:
    root = _base_root()
    SubElement(
        root,
        "Struct",
        attrib={
            "id": "_9",
            "name": "Opaque",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
            "incomplete": "1",
        },
    )
    return root


def _record(root: Element, name: str):
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    types = [t for t in parser.parse_types() if t.name == name]
    assert len(types) == 1
    return types[0]


def test_polymorphic_record_facts_present_and_match_legacy_fields() -> None:
    rec = _record(_polymorphic_struct_root(), "Widget")
    assert rec.bases == ["Base"]
    assert rec.bases_fact.status is FactStatus.PRESENT
    assert rec.bases_fact.value == rec.bases
    assert rec.virtual_bases_fact.status is FactStatus.PRESENT
    assert rec.virtual_bases_fact.value == rec.virtual_bases == []
    assert rec.vtable_fact.status is FactStatus.PRESENT
    assert rec.vtable_fact.value == rec.vtable
    assert rec.vtable != []
    assert rec.vptr_offset_bits_fact.status is FactStatus.PARTIAL
    assert rec.vptr_offset_bits_fact.value == rec.vptr_offset_bits == 0
    # ADR-063 Phase 5: is_final_fact is constructed directly too, the same
    # convention as the four fields above — Widget has no `final` attribute.
    assert rec.is_final is False
    assert rec.is_final_fact.status is FactStatus.PRESENT
    assert rec.is_final_fact.value is False
    # ADR-063 Phase 5 (Codex review, second pass): qualified_name_fact is
    # also constructed directly, as Fact.present(qualified_name) — Widget
    # sits directly under the global namespace, so qualified_name is None,
    # but that None is a confirmed "no enclosing scope" determination, not
    # missing evidence.
    assert rec.qualified_name is None
    assert rec.qualified_name_fact.status is FactStatus.PRESENT
    assert rec.qualified_name_fact.value is None


def test_opaque_record_facts_present_and_match_legacy_empty_values() -> None:
    # An opaque/incomplete record's empty lists are castxml's own answer —
    # "no member data available" — not a gap this fresh extraction leaves
    # unaddressed, so it is Fact.present([]), not Fact.not_collected().
    rec = _record(_opaque_struct_root(), "Opaque")
    assert rec.is_opaque
    assert rec.bases == rec.virtual_bases == rec.vtable == []
    assert rec.vptr_offset_bits is None
    assert rec.bases_fact.status is FactStatus.PRESENT
    assert rec.bases_fact.value == []
    assert rec.virtual_bases_fact.status is FactStatus.PRESENT
    assert rec.vtable_fact.status is FactStatus.PRESENT
    assert rec.vptr_offset_bits_fact.status is FactStatus.PARTIAL
    assert rec.vptr_offset_bits_fact.value is None
    assert rec.is_final_fact.status is FactStatus.PRESENT
    assert rec.is_final_fact.value is False


def test_final_record_is_final_fact_present_true() -> None:
    root = _base_root()
    SubElement(
        root,
        "Struct",
        attrib={
            "id": "_30",
            "name": "Sealed",
            "context": "_1",
            "file": "f1",
            "location": "f1:9",
            "size": "8",
            "align": "8",
            "attributes": "final",
        },
    )
    rec = _record(root, "Sealed")
    assert rec.is_final is True
    assert rec.is_final_fact.status is FactStatus.PRESENT
    assert rec.is_final_fact.value is True


def test_namespaced_record_qualified_name_fact_present_with_real_value() -> None:
    root = _base_root()
    SubElement(root, "Namespace", attrib={"id": "_2", "name": "ns", "context": "_1"})
    SubElement(
        root,
        "Struct",
        attrib={
            "id": "_40",
            "name": "Nested",
            "context": "_2",
            "file": "f1",
            "location": "f1:11",
            "size": "8",
            "align": "8",
        },
    )
    rec = _record(root, "Nested")
    assert rec.qualified_name == "ns::Nested"
    assert rec.qualified_name_fact.status is FactStatus.PRESENT
    assert rec.qualified_name_fact.value == "ns::Nested"


def test_enum_qualified_name_fact_present_at_global_scope() -> None:
    # ADR-063 Phase 5 (third batch): EnumType.qualified_name_fact is
    # constructed directly too, mirroring RecordType's own pattern.
    root = _base_root()
    SubElement(
        root,
        "Enumeration",
        attrib={
            "id": "_50",
            "name": "Color",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
        },
    )
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    (en,) = [e for e in parser.parse_enums() if e.name == "Color"]
    assert en.qualified_name is None
    assert en.qualified_name_fact.status is FactStatus.PRESENT
    assert en.qualified_name_fact.value is None


def test_enum_qualified_name_fact_present_with_real_value() -> None:
    root = _base_root()
    SubElement(root, "Namespace", attrib={"id": "_2", "name": "ns", "context": "_1"})
    SubElement(
        root,
        "Enumeration",
        attrib={
            "id": "_51",
            "name": "Color",
            "context": "_2",
            "file": "f1",
            "location": "f1:1",
        },
    )
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    (en,) = [e for e in parser.parse_enums() if e.name == "Color"]
    assert en.qualified_name == "ns::Color"
    assert en.qualified_name_fact.status is FactStatus.PRESENT
    assert en.qualified_name_fact.value == "ns::Color"


def test_param_is_va_list_fact_is_unsupported_not_not_collected() -> None:
    root = _base_root()
    SubElement(
        root,
        "Function",
        attrib={
            "id": "_20",
            "name": "f",
            "returns": "_v",
            "context": "_1",
            "file": "f1",
            "location": "f1:5",
            "mangled": "_Z1fi",
        },
    )
    SubElement(
        root.find("Function"),
        "Argument",
        attrib={"name": "a", "type": "_i"},
    )
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    funcs = [f for f in parser.parse_functions() if f.name == "f"]
    assert len(funcs) == 1
    param = funcs[0].params[0]
    assert param.is_va_list is False
    assert param.is_va_list_fact.status is FactStatus.UNSUPPORTED
