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

"""``Function.is_compiler_generated`` — closes the castxml L4 extractor bug
documented in ``AGENTS.md``'s "PR C" known-gaps entry: castxml's
compiler-synthesized implicit special members (constructors, destructor,
copy/move assignment ``operator=``) were leaking into the L4 source-ABI
extractor's "reachable declaration surface" as if they were genuine public
API, dragging the declaration-to-binary-symbol match ratio low enough to
trip a false-positive ``source_binary_provenance_mismatch``.

castxml stamps ``artificial="1"`` on EVERY function-like element it
synthesizes rather than parses from real source text — not just
``Constructor``/``Destructor`` (already read there for
``_ctor_or_dtor_visibility``, see ``test_castxml_constructor_visibility.py``)
but also a compiler-generated ``operator=``, emitted as an ``OperatorMethod``
element carrying a real-looking Itanium mangled name and no other
distinguishing marker. Confirmed against real castxml 0.7.0 output for
``struct Widget { int x; int y; int sum() const; };``: castxml emits three
``Constructor``, two ``OperatorMethod``, and one ``Destructor`` element, all
six carrying ``artificial="1"`` — none of them written by the user.

These fixtures mirror that exact real output (hand-built ``Element`` trees,
not a live castxml subprocess — same convention as
``test_castxml_constructor_visibility.py``), so this stays in the default
(fast) test lane.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser


def _make_root(*, method_artificial: str = "") -> Element:
    """A ``Widget`` class with one real user-written method (``sum``) plus
    the compiler-synthesized special members castxml always emits alongside
    it, matching real castxml 0.7.0 output for
    ``struct Widget { int x; int y; int sum() const; };`` element-for-element
    (three ``Constructor``, two ``OperatorMethod``, one ``Destructor``, all
    ``artificial="1"``).
    """
    root = Element("CastXML", attrib={"format": "1.4.0"})

    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "widget.h")

    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

    cls = SubElement(root, "Class")
    cls.set("id", "_2")
    cls.set("name", "Widget")
    cls.set("context", "_1")
    cls.set("file", "f1")
    cls.set("location", "f1:1")

    method = SubElement(root, "Method")
    method.set("id", "_3")
    method.set("name", "sum")
    method.set("context", "_2")
    method.set("access", "public")
    method.set("file", "f1")
    method.set("location", "f1:1")
    method.set("const", "1")
    method.set("mangled", "_ZNK6Widget3sumEv")
    if method_artificial:
        method.set("artificial", method_artificial)

    for i, kind in enumerate(
        ("default-ctor", "copy-ctor", "move-ctor", "dtor"), start=4
    ):
        el = SubElement(root, "Destructor" if kind == "dtor" else "Constructor")
        el.set("id", f"_{i}")
        el.set("name", "Widget")
        el.set("context", "_2")
        el.set("access", "public")
        el.set("file", "f1")
        el.set("location", "f1:1")
        el.set("artificial", "1")

    copy_assign = SubElement(root, "OperatorMethod")
    copy_assign.set("id", "_8")
    copy_assign.set("name", "=")
    copy_assign.set("context", "_2")
    copy_assign.set("access", "public")
    copy_assign.set("file", "f1")
    copy_assign.set("location", "f1:1")
    copy_assign.set("artificial", "1")
    copy_assign.set("mangled", "_ZN6WidgetaSERKS_")

    move_assign = SubElement(root, "OperatorMethod")
    move_assign.set("id", "_9")
    move_assign.set("name", "=")
    move_assign.set("context", "_2")
    move_assign.set("access", "public")
    move_assign.set("file", "f1")
    move_assign.set("location", "f1:1")
    move_assign.set("artificial", "1")
    move_assign.set("mangled", "_ZN6WidgetaSEOS_")

    return root


def _parse(**kwargs: str) -> dict[str, bool | None]:
    root = _make_root(**kwargs)
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    funcs = parser.parse_functions()
    # Real castxml mangled name, keyed by name -- for the ctors/dtor,
    # `_function_mangled_name`'s own synthesized `__abicheck_ctor__.../
    # ~Widget` key (not the bare "Widget", and not shared with `sum`'s or
    # `operator=`'s real symbols).
    by_mangled = {f.mangled: f.is_compiler_generated for f in funcs}
    ctor_dtor_flags = [
        f.is_compiler_generated for f in funcs if f.name in ("Widget", "~Widget")
    ]
    return {"by_mangled": by_mangled, "ctor_dtor_flags": ctor_dtor_flags}


class TestCompilerGeneratedFlag:
    def test_user_written_method_is_not_compiler_generated(self) -> None:
        result = _parse()
        assert result["by_mangled"]["_ZNK6Widget3sumEv"] is False

    def test_synthesized_operator_assign_is_compiler_generated(self) -> None:
        # This is the exact gap the two pre-existing synthetic-mangled-name
        # markers (is_synthetic_ctor_key/is_synthetic_dtor_key) could not
        # close: castxml gives a synthesized operator= a real-looking
        # Itanium mangled name, so neither marker catches it.
        result = _parse()
        assert result["by_mangled"]["_ZN6WidgetaSERKS_"] is True
        assert result["by_mangled"]["_ZN6WidgetaSEOS_"] is True

    def test_synthesized_ctors_and_dtor_are_compiler_generated(self) -> None:
        result = _parse()
        assert result["ctor_dtor_flags"] == [True, True, True, True]

    def test_a_real_operator_assign_the_user_wrote_is_not_flagged(self) -> None:
        # Negative control: an OperatorMethod without artificial="1" (a
        # genuinely user-written operator=) must not be excluded.
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "widget.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        cls = SubElement(root, "Class")
        cls.set("id", "_2")
        cls.set("name", "Widget")
        cls.set("context", "_1")
        cls.set("file", "f1")
        cls.set("location", "f1:1")
        assign = SubElement(root, "OperatorMethod")
        assign.set("id", "_3")
        assign.set("name", "=")
        assign.set("context", "_2")
        assign.set("access", "public")
        assign.set("file", "f1")
        assign.set("location", "f1:1")
        assign.set("mangled", "_ZN6WidgetaSERKS_")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].is_compiler_generated is False
