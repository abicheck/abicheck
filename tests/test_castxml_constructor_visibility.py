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

"""Regression tests for constructor-overload visibility (case78/case111).

CastXML may omit the ``mangled`` attribute for a user-declared, overloaded
constructor. ``_function_mangled_name`` already synthesizes a stable
per-overload snapshot key (``__abicheck_ctor__Class(params)``) so the
overloads don't collapse into one ``function_map`` entry — but the plain
ELF-symbol-table visibility lookup (``_visibility``) can never match that
synthetic key against a real exported symbol, so every such overload used to
classify ``Visibility.HIDDEN`` regardless of whether it was genuinely public.
That silently hid:

- a *removed* constructor overload (case78: ``task_arena(attach_mode_t)``),
  since ``_public_functions()`` filters to PUBLIC/ELF_ONLY visibility only;
- an *added* one (case111: the new ``int_factory_t`` overload).

``_constructor_visibility`` restores this signal from source access when
castxml gives no mangled name to check — but only for a genuinely
user-declared constructor (not compiler-generated default/copy/move ctors,
which carry no source declaration of their own to compare and would
otherwise become permanent add/remove noise on every trivial aggregate).
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser
from abicheck.model import Visibility


def _make_root_with_constructor(
    *,
    mangled: str = "",
    access: str = "public",
    deleted: str = "",
    artificial: str = "",
) -> Element:
    """Mirror castxml output for a single-constructor class."""
    root = Element("CastXML", attrib={"format": "1.4.0"})

    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "lib.h")

    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})

    cls = SubElement(root, "Class")
    cls.set("id", "_2")
    cls.set("name", "Widget")
    cls.set("context", "_1")
    cls.set("file", "f1")
    cls.set("location", "f1:1")

    ctor = SubElement(root, "Constructor")
    ctor.set("id", "_3")
    ctor.set("name", "Widget")
    ctor.set("context", "_2")
    ctor.set("file", "f1")
    ctor.set("location", "f1:2")
    ctor.set("access", access)
    if mangled:
        ctor.set("mangled", mangled)
    if deleted:
        ctor.set("deleted", deleted)
    if artificial:
        ctor.set("artificial", artificial)

    return root


def _ctor_visibility(**kwargs: str) -> Visibility:
    root = _make_root_with_constructor(**kwargs)
    parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
    funcs = parser.parse_functions()
    assert len(funcs) == 1
    return funcs[0].visibility


class TestConstructorVisibilityFallback:
    def test_public_user_declared_ctor_with_no_mangled_name_is_public(self) -> None:
        assert _ctor_visibility(access="public") == Visibility.PUBLIC

    def test_private_ctor_with_no_mangled_name_stays_hidden(self) -> None:
        assert _ctor_visibility(access="private") == Visibility.HIDDEN

    def test_deleted_ctor_with_no_mangled_name_stays_hidden(self) -> None:
        assert _ctor_visibility(access="public", deleted="1") == Visibility.HIDDEN

    def test_artificial_implicit_ctor_stays_hidden(self) -> None:
        # Compiler-generated default/copy/move ctors (artificial="1") must not
        # be promoted — they have no source declaration of their own, so
        # treating every trivial aggregate's synthesized ctors as public API
        # churn would be worse noise than the original HIDDEN default.
        assert _ctor_visibility(access="public", artificial="1") == Visibility.HIDDEN

    def test_real_mangled_name_not_in_export_tables_stays_hidden(self) -> None:
        # A real mangled name was checked and found absent from both ELF
        # tables — that is a trustworthy negative, not a gap to paper over.
        assert (
            _ctor_visibility(access="public", mangled="_ZN6WidgetC1Ev")
            == Visibility.HIDDEN
        )

    def test_real_mangled_name_in_dynamic_exports_is_public(self) -> None:
        root = _make_root_with_constructor(access="public", mangled="_ZN6WidgetC1Ev")
        parser = _CastxmlParser(
            root, exported_dynamic={"_ZN6WidgetC1Ev"}, exported_static=set()
        )
        funcs = parser.parse_functions()
        assert funcs[0].visibility == Visibility.PUBLIC


class TestConstructorOverloadKeyExemptFromElfNarrowing:
    def test_public_functions_keeps_synthetic_ctor_key_without_elf_match(self) -> None:
        from abicheck.diff_symbols import _public_functions
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol
        from abicheck.model import AbiSnapshot

        root = _make_root_with_constructor(access="public")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1

        snap = AbiSnapshot(
            library="libwidget.so",
            version="1",
            functions=funcs,
            elf=ElfMetadata(symbols=[ElfSymbol(name="some_other_export")]),
        )
        kept = _public_functions(snap)
        assert list(kept) == [funcs[0].mangled]
