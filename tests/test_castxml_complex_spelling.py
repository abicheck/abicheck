# SPDX-License-Identifier: Apache-2.0
"""castxml leaves complex and bit-precise integer types ``Unimplemented``.

castxml renders ``float _Complex`` as ``<Unimplemented type_class="Complex"/>``
and ``_BitInt(8)`` as ``type_class="BitInt"``, with no element data. Every such
parameter, field and return therefore shared the literal spelling
``Unimplemented``, and ``void f(float _Complex)`` -> ``void f(double _Complex)``
compared equal on the default header lane. The clang AST of the same header
keeps the spelling, so the join must hand it back for these nodes.

The clang JSON and castxml XML below are small hand-written fixtures in the
shapes both tools emit (checked against castxml 0.7 and clang's ``-ast-dump=json``),
not output of the code under test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from abicheck.extract.headers.castxml.vector_spellings import (
    UNEXPRESSIBLE_TYPE_CLASSES,
    castxml_declaration_keys,
    clang_vector_spellings,
    has_vector_nodes,
    parse_unexpressible_qualtype,
    resolve_vector_spellings,
)


@pytest.mark.parametrize(
    ("qual_type", "spelling"),
    [
        ("_Complex float", "_Complex float"),
        ("double _Complex", "double _Complex"),
        ("long double _Complex", "long double _Complex"),
        ("unsigned _BitInt(8)", "unsigned _BitInt(8)"),
        ("_BitInt(16)", "_BitInt(16)"),
        (
            "__attribute__((__vector_size__(16))) float",
            "float __attribute__((vector_size(16)))",
        ),
    ],
)
def test_unexpressible_types_keep_the_clang_spelling(
    qual_type: str, spelling: str
) -> None:
    assert parse_unexpressible_qualtype(qual_type) == spelling


@pytest.mark.parametrize(
    "qual_type",
    [
        "_Complex float *",  # a pointer: its inner node is not keyed
        "_Complex float[4]",
        "int",  # castxml spells plain fundamentals itself
        "__int128",  # castxml spells this one too
        "struct S",
        "",
    ],
)
def test_other_types_are_not_joined(qual_type: str) -> None:
    assert parse_unexpressible_qualtype(qual_type) is None


def _first_keys(root):
    return {node: found[0] for node, found in castxml_declaration_keys(root).items()}


def test_the_three_unimplemented_classes_are_the_ones_castxml_cannot_name() -> None:
    assert frozenset({"Vector", "Complex", "BitInt"}) == UNEXPRESSIBLE_TYPE_CLASSES


_CASTXML_COMPLEX = """\
<CastXML>
  <Unimplemented id="_1" type_class="Complex"/>
  <Unimplemented id="_2" type_class="Complex"/>
  <Function id="_3" name="f" returns="_4" mangled="f" file="f0" line="1">
    <Argument name="p" type="_1" location="f0:1" file="f0" line="1"/>
  </Function>
  <Function id="_5" name="g" returns="_2" mangled="g" file="f0" line="2"/>
  <FundamentalType id="_4" name="void" size="0" align="8"/>
</CastXML>
"""

_CLANG_COMPLEX = {
    "kind": "TranslationUnitDecl",
    "inner": [
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "f",
            "type": {"qualType": "void (_Complex float)"},
            "inner": [
                {
                    "kind": "ParmVarDecl",
                    "name": "p",
                    "type": {"qualType": "_Complex float"},
                }
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "g",
            "mangledName": "g",
            "type": {"qualType": "_Complex double ()"},
        },
    ],
}


def test_castxml_complex_parameter_and_return_join_to_clang_spelling() -> None:
    root = ET.fromstring(_CASTXML_COMPLEX)
    assert has_vector_nodes(root)
    keys = _first_keys(root)
    assert keys == {"_1": "param:f#0", "_2": "return:g"}
    resolved = resolve_vector_spellings(root, _CLANG_COMPLEX)
    # The parameter gets clang's spelling; the return of ``g`` is keyed by the
    # same node id, so the spelling it resolves to is clang's return spelling.
    assert resolved == {"_1": "_Complex float", "_2": "_Complex double"}


def test_clang_keys_match_the_castxml_key_grammar() -> None:
    by_key = clang_vector_spellings(_CLANG_COMPLEX)
    assert by_key == {"param:f#0": "_Complex float", "return:g": "_Complex double"}


_CASTXML_TYPEDEF_FIELD = """\
<CastXML>
  <Namespace id="_0" name="::"/>
  <Typedef id="_1" name="cf" type="_6" context="_0"/>
  <Unimplemented id="_6" type_class="Complex"/>
  <Struct id="_2" name="S" context="_0"/>
  <Unimplemented id="_7" type_class="Complex"/>
  <Field id="_3" name="c" type="_7" context="_2"/>
</CastXML>
"""

_CLANG_TYPEDEF_FIELD = {
    "kind": "TranslationUnitDecl",
    "inner": [
        {"kind": "TypedefDecl", "name": "cf", "type": {"qualType": "_Complex float"}},
        {
            "kind": "RecordDecl",
            "name": "S",
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "c",
                    "type": {"qualType": "_Complex double"},
                }
            ],
        },
    ],
}


def test_complex_typedef_and_field_join_to_clang_spelling() -> None:
    root = ET.fromstring(_CASTXML_TYPEDEF_FIELD)
    assert _first_keys(root) == {"_6": "typedef:cf", "_7": "field:S::c"}
    resolved = resolve_vector_spellings(root, _CLANG_TYPEDEF_FIELD)
    assert resolved == {"_6": "_Complex float", "_7": "_Complex double"}


# A complex element type reached through a pointer, an array and a typedef
# chain. castxml wraps the one Unimplemented node in PointerType / ArrayType /
# Typedef nodes; the declarations keyed here are the parameters and the return.
_CASTXML_DECLARATORS = """\
<CastXML>
  <Namespace id="_0" name="::"/>
  <Unimplemented id="_1" type_class="Complex"/>
  <Typedef id="_2" name="cf" type="_1" context="_0"/>
  <PointerType id="_3" type="_1"/>
  <Typedef id="_4" name="cfp" type="_3" context="_0"/>
  <ArrayType id="_5" type="_1" max="3"/>
  <Function id="_6" name="h" returns="_1" mangled="h" context="_0">
    <Argument name="p" type="_3"/>
    <Argument name="q" type="_4"/>
    <Argument name="a" type="_5"/>
  </Function>
</CastXML>
"""

_CLANG_DECLARATORS = {
    "kind": "TranslationUnitDecl",
    "inner": [
        {"kind": "TypedefDecl", "name": "cf", "type": {"qualType": "_Complex float"}},
        {
            "kind": "TypedefDecl",
            "name": "cfp",
            "type": {"qualType": "_Complex float *"},
        },
        {
            "kind": "FunctionDecl",
            "name": "h",
            "mangledName": "h",
            "type": {
                "qualType": "_Complex float (_Complex float *, cfp, _Complex float [4])"
            },
            "inner": [
                {
                    "kind": "ParmVarDecl",
                    "name": "p",
                    "type": {"qualType": "_Complex float *"},
                },
                {"kind": "ParmVarDecl", "name": "q", "type": {"qualType": "cfp"}},
                {
                    "kind": "ParmVarDecl",
                    "name": "a",
                    "type": {"qualType": "_Complex float [4]"},
                },
            ],
        },
    ],
}


def test_complex_pointer_array_and_typedef_chain_reach_the_element_node() -> None:
    root = ET.fromstring(_CASTXML_DECLARATORS)
    keys = castxml_declaration_keys(root)
    # Every declaration that wraps the element node names it, in document order.
    assert keys == {
        "_1": [
            "typedef:cf",
            "typedef:cfp",
            "param:h#0",
            "param:h#1",
            "param:h#2",
            "return:h",
        ]
    }


def test_pointer_array_and_typedef_spellings_resolve_to_the_element_spelling() -> None:
    root = ET.fromstring(_CASTXML_DECLARATORS)
    resolved = resolve_vector_spellings(root, _CLANG_DECLARATORS)
    # One Unimplemented node, one spelling: the element type's, whichever
    # declarator (pointer, typedef, array) the first known key came through.
    assert resolved == {"_1": "_Complex float"}
