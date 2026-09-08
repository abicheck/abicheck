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

"""Regression test for the fourth recurrence of
``extraction.macho_mangled_identity_normalization``
(``tests/regressions/manifest.py``, fixed by PR #1156): a real castxml
``mangled`` attribute never carries Darwin's Mach-O linker decoration to
begin with (unlike clang's ``-ast-dump=json`` ``mangledName``), so the ONE
real thing a ``"__Z..."``-shaped castxml ``mangled`` attribute can mean is
a literal, explicit ``asm("__Zfake")`` assembler-label declaration --
Darwin or not. An earlier revision of the Darwin decoration-stripping fix
applied ``strip_macho_itanium_decoration`` to castxml's own mangled-name
production unconditionally, with no platform gate at all, which would have
silently corrupted that literal spelling into ``"_Zfake"`` on any target.

Constructs synthetic castxml XML rather than shelling out to the real
``castxml`` binary (mirroring ``test_castxml_hidden_friends.py``'s own
convention), so this runs in the fast default suite with no external
tooling and no Mach-O toolchain -- unlike the rest of this bug class's
seed tests, which all depend on either a real compiler or a monkeypatch of
the clang backend's own probe.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser


def _root_with_literal_asm_label(mangled: str) -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    SubElement(root, "File", attrib={"id": "f1", "name": "lib.h"})
    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_v", "name": "void"})

    fn = SubElement(root, "Function")
    fn.set("id", "_10")
    fn.set("name", "foo")
    fn.set("returns", "_v")
    fn.set("context", "_1")
    fn.set("file", "f1")
    fn.set("location", "f1:1")
    # e.g. `void foo() asm("__Zfake");` -- a real, literal linker-name
    # override, not Mach-O decoration of a real Itanium mangling.
    fn.set("mangled", mangled)

    return root


def test_literal_double_underscore_asm_label_survives_unchanged() -> None:
    root = _root_with_literal_asm_label("__Zfake")
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    (fn,) = parser.parse_functions()
    assert fn.mangled == "__Zfake"


def test_genuine_itanium_mangled_name_also_survives_unchanged() -> None:
    """Companion case: an ordinary, real Itanium mangled name (single
    leading underscore) must obviously be untouched too -- pinning both
    ends of the shape space this bug class is about, not just the one
    corrupted by the reverted fix."""
    root = _root_with_literal_asm_label("_Z3fooi")
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    (fn,) = parser.parse_functions()
    assert fn.mangled == "_Z3fooi"
