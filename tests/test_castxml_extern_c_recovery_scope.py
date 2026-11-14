# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""castxml's extern-C recovery applies only to a header that could be C.

Bug class: a C++ declaration (``int kdl_gone(int);`` in a C++ header) whose
mangled export disappeared while the library gained a bare C namesake was
recovered as C linkage, so the loss read as ``FUNC_LANGUAGE_LINKAGE_CHANGED``
instead of ``FUNC_DELETED_ELF_FALLBACK`` (abiforge
``declared_function_export_became_c_namesake``). The recovery's legitimate
purpose -- a C header without ``extern "C"`` guards, parsed in C++ mode --
must keep working. The oracle is the language rule: an unguarded declaration
in a header that declares C++-only constructs has C++ linkage.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.headers.castxml.dumper import _CastxmlParser


def _xml(cxx_evidence: str | None) -> Element:
    """castxml 0.7's shape for ``int gone(int); int keep(int); int gv;`` in
    ``f1``, plus one C++-only construct in the same file."""
    root = Element("CastXML", format="1.4.0")
    SubElement(root, "Namespace", id="_1", name="::", members="_2 _3 _4 _5 _6")
    SubElement(root, "File", id="f1", name="/inc/lib.h")
    SubElement(root, "FundamentalType", id="_20", name="int", size="32", align="32")
    for eid, name in (("_2", "gone"), ("_3", "keep")):
        fn = SubElement(
            root,
            "Function",
            id=eid,
            name=name,
            returns="_20",
            context="_1",
            location="f1:1",
            file="f1",
            line="1",
            mangled=f"_Z{len(name)}{name}i",
        )
        SubElement(fn, "Argument", name="a", type="_20", location="f1:1", file="f1")
    SubElement(
        root,
        "Variable",
        id="_6",
        name="gv",
        type="_20",
        context="_1",
        location="f1:2",
        file="f1",
        line="2",
        mangled="gv",
        extern="1",
    )
    if cxx_evidence == "class":
        SubElement(root, "Class", id="_4", name="K", context="_1", file="f1")
    elif cxx_evidence == "method":
        SubElement(root, "Struct", id="_4", name="S", context="_1", file="f1")
        SubElement(
            root, "Method", id="_7", name="m", returns="_20", context="_4", file="f1"
        )
    elif cxx_evidence == "namespace":
        SubElement(root, "Namespace", id="_5", name="n", context="_1", members="_8")
        SubElement(
            root,
            "Function",
            id="_8",
            name="g",
            returns="_20",
            context="_5",
            file="f1",
            mangled="_ZN1n1gEv",
        )
    return root


def _gone(root: Element, exports: set[str]):
    parser = _CastxmlParser(root, exported_dynamic=exports, exported_static=set())
    return next(f for f in parser.parse_functions() if f.name == "gone")


@pytest.mark.parametrize("static", [False, True])
def test_a_c_header_parsed_as_cxx_is_recovered_as_c_linkage(static: bool) -> None:
    exports = {"gone", "keep"}
    parser = _CastxmlParser(
        _xml(None),
        exported_dynamic=set() if static else exports,
        exported_static=exports if static else set(),
    )
    gone = next(f for f in parser.parse_functions() if f.name == "gone")
    assert (gone.mangled, gone.is_extern_c) == ("gone", True)


@pytest.mark.parametrize(
    ("evidence", "exports"),
    [
        ("class", {"gone"}),
        ("method", {"gone"}),
        ("namespace", {"gone"}),
        # a sibling's guessed C++ mangling is exported: the library compiled
        # this header's declarations with C++ linkage
        (None, {"gone", "_Z4keepi"}),
    ],
)
def test_a_cxx_header_keeps_the_declarations_cxx_linkage(evidence, exports) -> None:
    gone = _gone(_xml(evidence), exports)
    assert (gone.mangled, gone.is_extern_c) == ("_Z4gonei", False)


def _cxx_header_with_variable(name: str, mangled: str) -> Element:
    root = _xml("namespace")
    SubElement(
        root,
        "Variable",
        id="_9",
        name=name,
        type="_20",
        context="_1",
        location="f1:3",
        file="f1",
        line="3",
        mangled=mangled,
        extern="1",
    )
    return root


@pytest.mark.parametrize("name", ["c_var", "v", "a_much_longer_name_10"])
def test_an_itanium_global_variable_guess_is_bare_even_in_a_cxx_header(
    name: str,
) -> None:
    # Itanium never mangles a global-namespace variable: its symbol is the
    # bare name under C and C++ linkage alike, so castxml's _Z<len><name>
    # guess (written even for ``extern "C" int c_var;``) is never real.
    parser = _CastxmlParser(
        _cxx_header_with_variable(name, f"_Z{len(name)}{name}"),
        exported_dynamic={name},
        exported_static=set(),
    )
    var = next(v for v in parser.parse_variables() if v.name == name)
    assert var.mangled == name


def test_a_decorated_global_variable_in_a_cxx_header_keeps_its_decoration() -> None:
    # MSVC decorates a C++-linkage global; in a provably C++ header the
    # decoration is real, so the C-header gate still holds it.
    parser = _CastxmlParser(
        _cxx_header_with_variable("c_var", "?c_var@@3HA"),
        exported_dynamic={"c_var"},
        exported_static=set(),
    )
    var = next(v for v in parser.parse_variables() if v.name == "c_var")
    assert var.mangled == "?c_var@@3HA"
