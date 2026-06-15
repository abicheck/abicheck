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

"""Unit tests for the clang ``-ast-dump=json`` → ABI model parser (L2 backend).

The parser is pure (consumes an already-parsed JSON dict), so the whole emit
surface is exercised here without clang installed. The clang↔castxml parity and
the live ``clang -ast-dump=json`` run live in the integration lane
(``test_clang_header_backend_integration.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.dumper import (
    _build_clang_header_command,
    _resolve_header_backend,
)
from abicheck.dumper_clang import (
    _ClangAstParser,
    _function_qualifiers,
    _pointer_depth,
    _return_type,
)
from abicheck.model import AccessLevel, Visibility


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "type_str,expected",
    [
        ("int", 0),
        ("char *", 1),
        ("int **", 2),
        ("const char *", 1),
        ("std::vector<int *>", 0),  # the * is inside template brackets
    ],
)
def test_pointer_depth(type_str: str, expected: int) -> None:
    assert _pointer_depth(type_str) == expected


@pytest.mark.parametrize(
    "qualtype,expected",
    [
        ("int (int, int)", "int"),
        ("void ()", "void"),
        ("const char *(int)", "const char *"),
        ("int (int) const noexcept", "int"),
    ],
)
def test_return_type(qualtype: str, expected: str) -> None:
    assert _return_type(qualtype) == expected


def test_function_qualifiers() -> None:
    assert "const" in _function_qualifiers("int (int) const")
    assert "noexcept" in _function_qualifiers("void () noexcept")
    assert _function_qualifiers("int (int)").strip() == ""


# ── parse_functions ──────────────────────────────────────────────────────────


def test_parse_functions_signature_and_qualifiers() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "add",
            "loc": {"file": "include/foo.h", "line": 3},
            "mangledName": "_Z3addii",
            "type": {"qualType": "int (int, int) noexcept"},
            "inner": [
                {"kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"}},
                {
                    "kind": "ParmVarDecl",
                    "name": "y",
                    "type": {"qualType": "int"},
                    "inner": [{"kind": "IntegerLiteral", "value": "1"}],
                },
            ],
        }
    )
    parser = _ClangAstParser(root, {"_Z3addii"}, set())
    (fn,) = parser.parse_functions()
    assert fn.name == "add"
    assert fn.mangled == "_Z3addii"
    assert fn.return_type == "int"
    assert fn.is_noexcept is True
    assert fn.visibility == Visibility.PUBLIC
    assert [p.name for p in fn.params] == ["x", "y"]
    # The second parameter carries a default-argument expression.
    assert fn.params[0].default is None
    assert fn.params[1].default == "default"


def test_parse_functions_method_const_and_access() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                # default (private) section: a hidden method
                {
                    "kind": "CXXMethodDecl",
                    "name": "secret",
                    "type": {"qualType": "void ()"},
                    "mangledName": "_ZN6Widget6secretEv",
                },
                {"kind": "AccessSpecDecl", "access": "public"},
                {
                    "kind": "CXXMethodDecl",
                    "name": "get",
                    "type": {"qualType": "int () const"},
                    "mangledName": "_ZNK6Widget3getEv",
                },
            ],
        }
    )
    parser = _ClangAstParser(root, set(), set())
    by_name = {f.name: f for f in parser.parse_functions()}
    assert by_name["secret"].access == AccessLevel.PRIVATE
    assert by_name["get"].access == AccessLevel.PUBLIC
    assert by_name["get"].is_const is True


def test_parse_functions_extern_c_via_mangled_equals_name() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "c_api",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "c_api",  # C linkage: mangled == name
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_extern_c is True


def test_explicit_is_tristate() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "C",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "CXXConstructorDecl",
                    "name": "C",
                    "type": {"qualType": "void (int)"},
                    "mangledName": "_ZN1CC1Ei",
                    "explicit": True,
                    "inner": [
                        {"kind": "ParmVarDecl", "name": "n", "type": {"qualType": "int"}}
                    ],
                },
                {
                    "kind": "CXXMethodDecl",
                    "name": "run",
                    "type": {"qualType": "void ()"},
                    "mangledName": "_ZN1C3runEv",
                },
            ],
        }
    )
    by_name = {f.name: f for f in _ClangAstParser(root, set(), set()).parse_functions()}
    assert by_name["C"].is_explicit is True
    # A plain method is not a constructor/conversion: explicit is unknown (None).
    assert by_name["run"].is_explicit is None


# ── variables + constants ────────────────────────────────────────────────────


def test_parse_constants_scoped_to_public_headers() -> None:
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "kMax",
                    "loc": {"line": 9},
                    "constexpr": True,
                    "type": {"qualType": "const int"},
                    "mangledName": "_ZN2ns4kMaxE",
                    "inner": [{"kind": "IntegerLiteral", "value": "42"}],
                },
            ],
        },
        {
            "kind": "VarDecl",
            "name": "kPrivate",
            "loc": {"file": "src/internal.h", "line": 2},
            "constexpr": True,
            "type": {"qualType": "const int"},
            "inner": [{"kind": "IntegerLiteral", "value": "7"}],
        },
    )
    parser = _ClangAstParser(
        root, set(), set(), public_header_paths=["include/foo.h"]
    )
    # Public constant kept and namespace-qualified; private-header one dropped.
    assert parser.parse_constants() == {"ns::kMax": "42"}


def test_parse_constants_empty_without_public_set() -> None:
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "kMax",
            "loc": {"file": "include/foo.h", "line": 1},
            "constexpr": True,
            "type": {"qualType": "const int"},
            "inner": [{"kind": "IntegerLiteral", "value": "42"}],
        }
    )
    # Provenance is opt-in: no public set → no constants.
    assert _ClangAstParser(root, set(), set()).parse_constants() == {}


def test_parse_variables_visibility() -> None:
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "g_count",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "mangledName": "g_count",
        }
    )
    (var,) = _ClangAstParser(root, {"g_count"}, set()).parse_variables()
    assert var.visibility == Visibility.PUBLIC
    assert var.is_const is False


# ── types / enums / typedefs ─────────────────────────────────────────────────


def test_parse_types_fields_bases_and_forward_decl_skipped() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Derived",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "bases": [
                {"type": {"qualType": "Base"}, "access": "public", "isVirtual": False},
                {"type": {"qualType": "Mixin"}, "access": "public", "isVirtual": True},
            ],
            "inner": [
                {"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}},
                {
                    "kind": "FieldDecl",
                    "name": "flags",
                    "type": {"qualType": "unsigned int"},
                    "isBitfield": True,
                    "inner": [{"kind": "IntegerLiteral", "value": "3"}],
                },
            ],
        },
        # A forward declaration of the same kind must NOT emit a (false) record.
        {
            "kind": "CXXRecordDecl",
            "name": "Opaque",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 10},
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    assert "Opaque" not in types
    derived = types["Derived"]
    assert derived.kind == "struct"
    assert [f.name for f in derived.fields] == ["a", "flags"]
    assert derived.fields[1].is_bitfield is True
    assert derived.fields[1].bitfield_bits == 3
    assert derived.bases == ["Base"]
    assert derived.virtual_bases == ["Mixin"]
    # clang's JSON AST carries no computed layout.
    assert derived.size_bits is None
    assert derived.fields[0].offset_bits is None


def test_parse_enums_auto_increment_and_explicit() -> None:
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "Color",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {"kind": "EnumConstantDecl", "name": "Red"},
                {
                    "kind": "EnumConstantDecl",
                    "name": "Green",
                    "inner": [
                        {
                            "kind": "ConstantExpr",
                            "value": "5",
                            "inner": [{"kind": "IntegerLiteral", "value": "5"}],
                        }
                    ],
                },
                {"kind": "EnumConstantDecl", "name": "Blue"},
            ],
        }
    )
    (enum,) = _ClangAstParser(root, set(), set()).parse_enums()
    assert [(m.name, m.value) for m in enum.members] == [
        ("Red", 0),
        ("Green", 5),
        ("Blue", 6),  # auto-increments from the explicit 5
    ]


def test_parse_typedefs() -> None:
    root = _tu(
        {
            "kind": "TypedefDecl",
            "name": "handle_t",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
        },
        {
            "kind": "TypeAliasDecl",
            "name": "size_alias",
            "loc": {"file": "include/foo.h", "line": 2},
            "type": {"qualType": "unsigned long"},
        },
    )
    assert _ClangAstParser(root, set(), set()).parse_typedefs() == {
        "handle_t": "int",
        "size_alias": "unsigned long",
    }


def test_builtin_file_declarations_skipped() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "__builtin_thing",
            "loc": {"file": "<built-in>", "line": 1},
            "mangledName": "__builtin_thing",
            "type": {"qualType": "void ()"},
        }
    )
    assert _ClangAstParser(root, set(), set()).parse_functions() == []


def test_sticky_file_threaded_to_siblings() -> None:
    # clang omits loc.file when unchanged; the second function inherits the file
    # of the first, so both classify against include/foo.h.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "a",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1av",
            "type": {"qualType": "void ()"},
        },
        {
            "kind": "FunctionDecl",
            "name": "b",
            "loc": {"line": 2},  # no file → sticky from previous
            "mangledName": "_Z1bv",
            "type": {"qualType": "void ()"},
        },
    )
    fns = {f.name: f for f in _ClangAstParser(root, set(), set()).parse_functions()}
    assert fns["b"].source_location == "include/foo.h:2"


# ── backend resolver / command builder ───────────────────────────────────────


def test_resolve_header_backend_explicit() -> None:
    assert _resolve_header_backend("castxml") == "castxml"
    assert _resolve_header_backend("clang") == "clang"


def test_resolve_header_backend_rejects_unknown() -> None:
    from abicheck.errors import ValidationError

    with pytest.raises(ValidationError):
        _resolve_header_backend("gcc")


def test_resolve_header_backend_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABICHECK_HEADER_BACKEND", "clang")
    assert _resolve_header_backend("auto") == "clang"
    assert _resolve_header_backend(None) == "clang"
    # An explicit request always wins over the env default.
    monkeypatch.setenv("ABICHECK_HEADER_BACKEND", "clang")
    assert _resolve_header_backend("castxml") == "castxml"


def test_resolve_header_backend_auto_prefers_castxml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_HEADER_BACKEND", raising=False)
    monkeypatch.setattr("abicheck.dumper._castxml_available", lambda: True)
    monkeypatch.setattr("abicheck.dumper._clang_available", lambda *a, **k: True)
    assert _resolve_header_backend("auto") == "castxml"
    # castxml absent, clang present → clang.
    monkeypatch.setattr("abicheck.dumper._castxml_available", lambda: False)
    assert _resolve_header_backend("auto") == "clang"


def test_build_clang_header_command_cpp_and_c(tmp_path: Path) -> None:
    agg = tmp_path / "agg.hpp"
    inc = tmp_path / "inc"
    cpp = _build_clang_header_command(
        "clang++", "gnu", [inc], agg, force_cpp=True, force_cpp20=True
    )
    assert cpp[0] == "clang++"
    assert "-ast-dump=json" in cpp
    assert "-fsyntax-only" in cpp
    assert "-std=gnu++20" in cpp
    assert ["-I", str(inc)] == cpp[1:3]
    # C mode forces a C standard.
    c_cmd = _build_clang_header_command("clang", "gnu", [], agg, force_cpp=False)
    assert "-std=gnu11" in c_cmd
    assert "-x" in c_cmd


def test_clang_header_dump_missing_clang_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from abicheck.dumper import _clang_header_dump
    from abicheck.errors import SnapshotError

    monkeypatch.setattr("abicheck.dumper._clang_available", lambda *a, **k: False)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    with pytest.raises(SnapshotError, match="not found in PATH"):
        _clang_header_dump([header], [])
