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

"""``Function.is_explicit_fact``/``is_override_fact`` must be
``Fact.not_applicable()`` -- not ``NOT_COLLECTED`` -- for a declaration kind
where the specifier is conceptually inapplicable (a plain free function for
``explicit``, any non-virtual-eligible kind for ``override``): the generic
``bridge_legacy_and_fact`` omission bridge alone cannot tell a confirmed
non-gap apart from a real evidence gap, so both header backends construct
these two facts explicitly at parse time (Codex review, PR #982).

Also covers the sibling ``tu_merge`` finding: when both TUs merging a
function leave ``contract_attributes`` at ``None`` ("neither side captured
this"), the merged ``contract_attributes_fact`` must stay ``NOT_COLLECTED``,
not a fabricated ``Fact.present(None)``.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser
from abicheck.dumper_clang import _ClangAstParser
from abicheck.model import Function
from abicheck.model.fact import FactStatus
from abicheck.tu_fragment import TuFragment
from abicheck.tu_merge import merge_fragments


def _castxml_root_with_free_function_and_constructor() -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    SubElement(root, "File", attrib={"id": "f1", "name": "lib.h"})
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_v", "name": "void"})
    SubElement(
        root,
        "Function",
        attrib={
            "id": "_2",
            "name": "do_thing",
            "returns": "_v",
            "context": "_1",
            "file": "f1",
            "location": "f1:1",
            "mangled": "_Z8do_thingv",
        },
    )
    SubElement(
        root,
        "Class",
        attrib={
            "id": "_3",
            "name": "Widget",
            "context": "_1",
            "file": "f1",
            "location": "f1:2",
        },
    )
    SubElement(
        root,
        "Constructor",
        attrib={
            "id": "_4",
            "name": "Widget",
            "context": "_3",
            "access": "public",
            "file": "f1",
            "location": "f1:3",
            "explicit": "1",
            "mangled": "_ZN6WidgetC1Ev",
        },
    )
    return root


def test_castxml_free_function_is_explicit_fact_not_applicable() -> None:
    parser = _CastxmlParser(
        _castxml_root_with_free_function_and_constructor(),
        exported_dynamic=set(),
        exported_static=set(),
    )
    funcs = {f.name: f for f in parser.parse_functions()}
    free_fn = funcs["do_thing"]
    assert free_fn.is_explicit is None
    assert free_fn.is_explicit_fact.status is FactStatus.NOT_APPLICABLE
    # is_override is also inapplicable to a free function.
    assert free_fn.is_override is None
    assert free_fn.is_override_fact.status is FactStatus.NOT_APPLICABLE


def test_castxml_constructor_is_explicit_fact_present() -> None:
    parser = _CastxmlParser(
        _castxml_root_with_free_function_and_constructor(),
        exported_dynamic=set(),
        exported_static=set(),
    )
    funcs = {f.name: f for f in parser.parse_functions()}
    ctor = funcs["Widget"]
    assert ctor.is_explicit is True
    assert ctor.is_explicit_fact.status is FactStatus.PRESENT
    assert ctor.is_explicit_fact.value is True


def test_castxml_ordinary_method_is_explicit_fact_not_applicable() -> None:
    """Codex review, PR #982: `explicit` only applies to constructors and
    conversion functions in real C++ -- an ordinary Method element must be
    NOT_APPLICABLE, matching clang/DWARF, not a confirmed-False PRESENT
    (castxml 0.7.0 never emits the `explicit` attribute on a plain Method
    at all, confirmed empirically)."""
    root = Element("CastXML", attrib={"format": "1.4.0"})
    SubElement(root, "File", attrib={"id": "f1", "name": "lib.h"})
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_v", "name": "void"})
    SubElement(
        root,
        "Class",
        attrib={
            "id": "_3",
            "name": "Widget",
            "context": "_1",
            "file": "f1",
            "location": "f1:2",
        },
    )
    SubElement(
        root,
        "Method",
        attrib={
            "id": "_4",
            "name": "doThing",
            "returns": "_v",
            "context": "_3",
            "access": "public",
            "file": "f1",
            "location": "f1:3",
            "mangled": "_ZN6Widget7doThingEv",
        },
    )
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    funcs = {f.name: f for f in parser.parse_functions()}
    method = funcs["doThing"]
    assert method.is_explicit is None
    assert method.is_explicit_fact.status is FactStatus.NOT_APPLICABLE


def _clang_tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def test_clang_free_function_is_explicit_fact_not_applicable() -> None:
    root = _clang_tu(
        {
            "kind": "FunctionDecl",
            "name": "do_thing",
            "mangledName": "_Z8do_thingv",
            "type": {"qualType": "void ()"},
            "loc": {"file": "lib.h", "line": 1},
        }
    )
    (fn,) = [
        f
        for f in _ClangAstParser(root, set(), set()).parse_functions()
        if f.name == "do_thing"
    ]
    assert fn.is_explicit is None
    assert fn.is_explicit_fact.status is FactStatus.NOT_APPLICABLE
    assert fn.is_override is None
    assert fn.is_override_fact.status is FactStatus.NOT_APPLICABLE


def test_clang_explicit_constructor_is_explicit_fact_present() -> None:
    root = _clang_tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "tagUsed": "class",
            "loc": {"file": "lib.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "CXXConstructorDecl",
                    "name": "Widget",
                    "mangledName": "_ZN6WidgetC1Ev",
                    "type": {"qualType": "void ()"},
                    "explicit": True,
                }
            ],
        }
    )
    (ctor,) = [
        f
        for f in _ClangAstParser(root, set(), set()).parse_functions()
        if f.name == "Widget"
    ]
    assert ctor.is_explicit is True
    assert ctor.is_explicit_fact.status is FactStatus.PRESENT
    assert ctor.is_explicit_fact.value is True


class TestTuMergeContractAttributesFactStaysNotCollected:
    def test_both_sides_none_merges_to_not_collected_not_present_none(self) -> None:
        f_a = Function(
            name="f",
            mangled="_Z1fi",
            return_type="void",
            contract_attributes=None,
        )
        f_b = Function(
            name="f",
            mangled="_Z1fi",
            return_type="void",
            contract_attributes=None,
        )
        a = TuFragment(tu_name="a", functions=(f_a,))
        b = TuFragment(tu_name="b", functions=(f_b,))
        merged = merge_fragments([a, b])
        (merged_fn,) = merged.functions
        assert merged_fn.contract_attributes is None
        assert merged_fn.contract_attributes_fact.status is FactStatus.NOT_COLLECTED

    def test_one_side_real_value_merges_to_present(self) -> None:
        f_a = Function(
            name="f",
            mangled="_Z1fi",
            return_type="void",
            contract_attributes=["nodiscard"],
        )
        f_b = Function(
            name="f",
            mangled="_Z1fi",
            return_type="void",
            contract_attributes=None,
        )
        a = TuFragment(tu_name="a", functions=(f_a,))
        b = TuFragment(tu_name="b", functions=(f_b,))
        merged = merge_fragments([a, b])
        (merged_fn,) = merged.functions
        assert merged_fn.contract_attributes == ["nodiscard"]
        assert merged_fn.contract_attributes_fact.status is FactStatus.PRESENT
        assert merged_fn.contract_attributes_fact.value == ["nodiscard"]
