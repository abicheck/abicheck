"""The castxml path reads a const variable template's value through clang's AST.

Unit level, no toolchain: a clang AST dict shaped like ``-ast-dump=json`` for
``namespace ns { template <class T> inline constexpr int vt = 1; }`` and a
castxml root without the template, checked against the expected constant. The
compiler-level oracle (a probe that prints the value) lives in
``test_value_bearing_declaration_forms.py``.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.headers.castxml.variable_template_join import (
    clang_const_variable_templates,
    has_variable_template_text,
    valid_sidecar,
    with_variable_templates,
)


def _header(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def _ast(file_name: str) -> dict:
    """A clang AST for ``namespace ns { template <class T> inline constexpr int vt = 1; }``."""
    var_decl = {
        "kind": "VarDecl",
        "name": "vt",
        "type": {"qualType": "const int"},
        "constexpr": True,
        "inline": True,
        "inner": [
            {"kind": "IntegerLiteral", "value": "1", "type": {"qualType": "int"}}
        ],
    }
    template = {
        "kind": "VarTemplateDecl",
        "name": "vt",
        "loc": {"file": file_name, "line": 2, "col": 41},
        "inner": [var_decl],
    }
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "ns",
                "loc": {"file": file_name, "line": 1, "col": 11},
                "inner": [template],
            }
        ],
    }


def test_clang_walker_records_the_const_template_with_scope_and_line(
    tmp_path: Path,
) -> None:
    header = _header(tmp_path, "api.h", "x\n")
    (entry,) = clang_const_variable_templates(_ast(str(header)), [header])
    assert entry == {
        "scope": ["ns"],
        "name": "vt",
        "type": "int",
        "const": True,
        "init": "1",
        "file": str(header),
        "line": 2,
    }


def test_template_outside_the_given_public_headers_is_not_joined(
    tmp_path: Path,
) -> None:
    """A header only included by a public header contributes nothing."""
    header = _header(tmp_path, "api.h", "x\n")
    other = _header(tmp_path, "private.h", "x\n")
    assert clang_const_variable_templates(_ast(str(other)), [header]) == []


def test_non_const_or_non_fundamental_template_is_not_joined(tmp_path: Path) -> None:
    header = _header(tmp_path, "api.h", "x\n")
    ast = _ast(str(header))
    decl = ast["inner"][0]["inner"][0]["inner"][0]
    decl["type"] = {"qualType": "int"}
    decl["constexpr"] = False
    assert clang_const_variable_templates(ast, [header]) == []
    decl["type"] = {"qualType": "const uint32_t"}
    decl["constexpr"] = True
    assert clang_const_variable_templates(ast, [header]) == []


def test_text_trigger_matches_a_const_variable_template_only(tmp_path: Path) -> None:
    yes = _header(
        tmp_path, "yes.h", "template <class T> inline constexpr int vt = 1;\n"
    )
    no_function = _header(
        tmp_path, "fn.h", "template <class T> const T& pick(const T& a);\n"
    )
    no_class = _header(tmp_path, "cls.h", "template <class T> struct S { int x; };\n")
    assert has_variable_template_text([yes]) is True
    assert has_variable_template_text([no_function]) is False
    assert has_variable_template_text([no_class]) is False


def _root() -> Element:
    root = Element("GCC_XML")
    SubElement(root, "Namespace", {"id": "_1", "name": "::"})
    return root


def test_castxml_root_gains_a_constant_the_parser_reads(tmp_path: Path) -> None:
    header = _header(tmp_path, "api.h", "x\n")
    root = with_variable_templates(
        _root(),
        [
            {
                "scope": ["ns"],
                "name": "vt",
                "type": "int",
                "const": True,
                "init": "1",
                "file": str(header),
                "line": 2,
            }
        ],
    )
    variables = [el for el in root if el.tag == "Variable"]
    assert len(variables) == 1
    (var,) = variables
    assert var.get("name") == "vt" and var.get("init") == "1"
    context = next(el for el in root if el.get("id") == var.get("context"))
    assert context.get("name") == "ns"
    assert context.get("context") == "_1"
    const_type = next(el for el in root if el.get("id") == var.get("type"))
    assert const_type.tag == "CvQualifiedType" and const_type.get("const") == "1"


def test_an_existing_variable_is_not_added_twice(tmp_path: Path) -> None:
    header = _header(tmp_path, "api.h", "x\n")
    entry = {
        "scope": [],
        "name": "vt",
        "type": "int",
        "const": True,
        "init": "1",
        "file": str(header),
        "line": 2,
    }
    root = _root()
    SubElement(
        root, "Variable", {"id": "_9", "name": "vt", "context": "_1", "init": "1"}
    )
    with_variable_templates(root, [entry])
    assert sum(1 for el in root if el.tag == "Variable") == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"templates": "not a list"},
        {"templates": [{"scope": "ns", "name": "vt"}]},
    ],
)
def test_malformed_sidecar_is_a_miss_not_an_empty_result(payload: object) -> None:
    assert valid_sidecar(payload) is None


def test_well_formed_sidecar_round_trips(tmp_path: Path) -> None:
    header = _header(tmp_path, "api.h", "x\n")
    entries = [
        {
            "scope": ["ns"],
            "name": "vt",
            "type": "int",
            "const": True,
            "init": "1",
            "file": str(header),
            "line": 2,
        }
    ]
    assert valid_sidecar({"templates": entries}) == entries
