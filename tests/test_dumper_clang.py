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

from abicheck import dumper
from abicheck.dumper import (
    _build_clang_header_command,
    _clang_header_dump,
    _header_ast_parser,
    _resolve_header_backend,
)
from abicheck.dumper_clang import (
    _ClangAstParser,
    _function_qualifiers,
    _pointer_depth,
    _return_type,
)
from abicheck.errors import SnapshotError
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


def test_macho_underscore_prefixed_mangled_name_matches_export() -> None:
    # Mach-O: clang's mangledName carries the platform global prefix
    # (``__ZN3lib3addEii``); the macho dumper strips one leading underscore off
    # the export set, so visibility resolution must match the de-prefixed form.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "add",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "__ZN3lib3addEii",  # macOS leading-underscore prefix
            "type": {"qualType": "int (int, int)"},
        }
    )
    # The dumper passes the prefix-free export set ("_ZN3lib3addEii").
    (fn,) = _ClangAstParser(root, {"_ZN3lib3addEii"}, set()).parse_functions()
    assert fn.visibility == Visibility.PUBLIC


def test_elf_itanium_name_matches_as_is_and_unexported_stays_hidden() -> None:
    # ELF: the export set carries the real underscore-prefixed Itanium name, so
    # it matches as-is (the first candidate). A decl absent from the export set —
    # whose name and de-prefixed form are both unexported — stays HIDDEN.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "add",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_ZN3lib3addEii",
            "type": {"qualType": "int (int, int)"},
        },
        {
            "kind": "FunctionDecl",
            "name": "internal",
            "loc": {"line": 2},
            "mangledName": "_ZN3lib8internalEv",
            "type": {"qualType": "void ()"},
        },
    )
    fns = {
        f.name: f
        for f in _ClangAstParser(root, {"_ZN3lib3addEii"}, set()).parse_functions()
    }
    assert fns["add"].visibility == Visibility.PUBLIC
    assert fns["internal"].visibility == Visibility.HIDDEN


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


# ── parse_variables / constants edge branches ────────────────────────────────


def test_parse_variables_skips_block_locals_and_empty_names() -> None:
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "loc",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "storageClass": "auto",  # a block-scope local → skipped
        },
        {
            "kind": "VarDecl",
            "name": "g",
            "loc": {"line": 2},
            "type": {"qualType": "const int"},
            "mangledName": "g",
        },
    )
    vs = _ClangAstParser(root, set(), set()).parse_variables()
    assert [v.name for v in vs] == ["g"]
    assert vs[0].is_const is True  # const detected from the type spelling


def test_parse_constants_compound_value_and_skips() -> None:
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                # compound initializer → a stable fingerprint string, not a literal
                {
                    "kind": "VarDecl",
                    "name": "kSum",
                    "loc": {"line": 2},
                    "constexpr": True,
                    "type": {"qualType": "const int"},
                    "inner": [
                        {
                            "kind": "BinaryOperator",
                            "opcode": "+",
                            "inner": [
                                {"kind": "IntegerLiteral", "value": "1"},
                                {"kind": "IntegerLiteral", "value": "2"},
                            ],
                        }
                    ],
                },
                # const but no initializer → skipped (value is None)
                {
                    "kind": "VarDecl",
                    "name": "kNoInit",
                    "loc": {"line": 3},
                    "type": {"qualType": "const int"},
                },
                # non-const → skipped
                {
                    "kind": "VarDecl",
                    "name": "kMutable",
                    "loc": {"line": 4},
                    "type": {"qualType": "int"},
                    "inner": [{"kind": "IntegerLiteral", "value": "9"}],
                },
            ],
        }
    )
    consts = _ClangAstParser(
        root, set(), set(), public_header_paths=["include/foo.h"]
    ).parse_constants()
    assert set(consts) == {"ns::kSum"}
    assert consts["ns::kSum"].startswith("expr:")


def test_constexpr_member_without_line_keeps_provenance() -> None:
    # Codex P2: `struct C { static constexpr int N = 1; };` — the member VarDecl
    # carries the header file but no loc.line; the constant must still be kept.
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "C",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "N",
                    # no loc.line — clang inherits the parent's line
                    "constexpr": True,
                    "storageClass": "static",
                    "type": {"qualType": "const int"},
                    "inner": [{"kind": "IntegerLiteral", "value": "1"}],
                }
            ],
        }
    )
    consts = _ClangAstParser(
        root, set(), set(), public_header_paths=["include/foo.h"]
    ).parse_constants()
    assert consts == {"C::N": "1"}


def test_constants_private_member_skipped() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "C",
            "tagUsed": "class",  # members default to private
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "kHidden",
                    "loc": {"line": 2},
                    "constexpr": True,
                    "type": {"qualType": "const int"},
                    "inner": [{"kind": "IntegerLiteral", "value": "1"}],
                }
            ],
        }
    )
    # The default class access is private → not part of the public constant set.
    assert _ClangAstParser(
        root, set(), set(), public_header_paths=["include/foo.h"]
    ).parse_constants() == {}


# ── records: union / C struct / field qualifiers ─────────────────────────────


def test_union_and_field_qualifiers() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "U",
            "tagUsed": "union",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {"kind": "FieldDecl", "name": "i", "type": {"qualType": "int"}},
                {
                    "kind": "FieldDecl",
                    "name": "cv",
                    "type": {"qualType": "const volatile char"},
                    "mutable": True,
                },
            ],
        }
    )
    (u,) = _ClangAstParser(root, set(), set()).parse_types()
    assert u.is_union is True
    assert u.kind == "union"
    cv = u.fields[1]
    assert cv.is_const is True
    assert cv.is_volatile is True
    assert cv.is_mutable is True


def test_c_record_decl_and_bitfield_without_literal() -> None:
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "bits",
                    "type": {"qualType": "unsigned int"},
                    "isBitfield": True,
                    # no inner literal → width unknown but still a bitfield
                    "inner": [{"kind": "DeclRefExpr", "name": "WIDTH"}],
                }
            ],
        }
    )
    (s,) = _ClangAstParser(root, set(), set()).parse_types()
    assert s.kind == "struct"
    assert s.fields[0].is_bitfield is True
    assert s.fields[0].bitfield_bits is None


def test_record_with_dunder_name_and_builtin_skipped() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "__hidden",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}}],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Builtin",
            "tagUsed": "struct",
            "loc": {"file": "<built-in>", "line": 1},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}}],
        },
    )
    assert _ClangAstParser(root, set(), set()).parse_types() == []


def test_bases_skip_malformed_entries() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "D",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "bases": [
                "not-a-dict",  # skipped
                {"access": "public"},  # missing type.qualType → skipped
                {"type": {"qualType": "Base"}, "access": "public", "isVirtual": False},
            ],
            "inner": [{"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}}],
        }
    )
    (d,) = _ClangAstParser(root, set(), set()).parse_types()
    assert d.bases == ["Base"]


# ── enums / typedefs edge branches ───────────────────────────────────────────


def test_enum_fixed_underlying_and_dunder_skip() -> None:
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "E",
            "loc": {"file": "include/foo.h", "line": 1},
            "fixedUnderlyingType": {"qualType": "unsigned char"},
            "inner": [{"kind": "EnumConstantDecl", "name": "A"}],
        },
        {
            "kind": "EnumDecl",
            "name": "__reserved",
            "loc": {"line": 5},
            "inner": [{"kind": "EnumConstantDecl", "name": "X"}],
        },
    )
    enums = _ClangAstParser(root, set(), set()).parse_enums()
    assert [e.name for e in enums] == ["E"]
    assert enums[0].underlying_type == "unsigned char"


def test_typedef_builtin_skip_and_missing_underlying() -> None:
    root = _tu(
        {
            "kind": "TypedefDecl",
            "name": "builtin_t",
            "loc": {"file": "<built-in>", "line": 1},
            "type": {"qualType": "int"},
        },
        {
            "kind": "TypedefDecl",
            "name": "opaque",
            "loc": {"file": "include/foo.h", "line": 2},
            "type": {},  # no qualType → "?"
        },
    )
    assert _ClangAstParser(root, set(), set()).parse_typedefs() == {"opaque": "?"}


# ── extern "C" linkage + ref-qualifiers ──────────────────────────────────────


def test_linkage_spec_marks_extern_c_even_with_itanium_name() -> None:
    # Inside extern "C", clang may still attach a name; the LinkageSpecDecl
    # establishes C linkage so the function reads as extern "C".
    root = _tu(
        {
            "kind": "LinkageSpecDecl",
            "language": "C",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "c_fn",
                    "loc": {"line": 2},
                    "mangledName": "c_fn",
                    "type": {"qualType": "void ()"},
                }
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_extern_c is True


@pytest.mark.parametrize(
    "qualtype,expected",
    [
        ("void () &", "&"),
        ("void () &&", "&&"),
        ("void ()", ""),
    ],
)
def test_method_ref_qualifier(qualtype: str, expected: str) -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "C",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "CXXMethodDecl",
                    "name": "m",
                    "type": {"qualType": qualtype},
                    "mangledName": "_ZN1C1mEv",
                }
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.ref_qualifier == expected


def test_node_file_falls_back_to_expansion_loc() -> None:
    # A macro-expanded decl carries its file under expansionLoc, not loc.file.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "m",
            "loc": {"expansionLoc": {"file": "include/foo.h", "line": 7}},
            "mangledName": "_Z1mv",
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.source_location == "include/foo.h:7"


# ── backend factory + clang dump driver ──────────────────────────────────────


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    class _P:
        pass

    p = _P()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def test_header_ast_parser_clang_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    ast = _tu(
        {
            "kind": "FunctionDecl",
            "name": "foo",
            "loc": {"file": "foo.h", "line": 1},
            "mangledName": "_Z3foov",
            "type": {"qualType": "void ()"},
        }
    )
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: ast)
    parser = _header_ast_parser(
        [], [], backend="clang", compiler="c++",
        gcc_path=None, gcc_prefix=None, gcc_options=None,
        sysroot=None, nostdinc=False, lang=None,
        exported_dynamic={"_Z3foov"}, exported_static=set(),
        public_header_paths=[], public_dir_paths=[],
    )
    assert isinstance(parser, _ClangAstParser)
    assert [f.name for f in parser.parse_functions()] == ["foo"]


def test_header_ast_parser_castxml_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: sentinel)
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: "castxml-parser")
    parser = _header_ast_parser(
        [], [], backend="castxml", compiler="c++",
        gcc_path=None, gcc_prefix=None, gcc_options=None,
        sysroot=None, nostdinc=False, lang=None,
        exported_dynamic=set(), exported_static=set(),
        public_header_paths=[], public_dir_paths=[],
    )
    assert parser == "castxml-parser"


def test_clang_header_dump_success_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'

    monkeypatch.setattr(dumper, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(stdout=ast_json)

    monkeypatch.setattr(dumper.subprocess, "run", _run)

    root = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert cache.exists()  # result was cached
    # Second call hits the cache — subprocess is not invoked again.
    root2 = _clang_header_dump([header], [])
    assert root2 == root
    assert calls["n"] == 1


def test_clang_header_dump_no_output_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    # Exit 0 but empty stdout → the "no AST" path (a nonzero exit is the
    # earlier branch, covered by test_clang_header_dump_nonzero_exit_raises).
    monkeypatch.setattr(
        dumper.subprocess, "run",
        lambda *a, **k: _fake_proc(stdout="", stderr="boom", returncode=0),
    )
    with pytest.raises(SnapshotError, match="no AST"):
        _clang_header_dump([header], [])


def test_clang_header_dump_bad_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(
        dumper.subprocess, "run",
        lambda *a, **k: _fake_proc(stdout="not json", returncode=0),
    )
    with pytest.raises(SnapshotError, match="not valid JSON"):
        _clang_header_dump([header], [])


def test_resolve_header_backend_neither_tool_defaults_castxml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_HEADER_BACKEND", raising=False)
    monkeypatch.setattr(dumper, "_castxml_available", lambda: False)
    monkeypatch.setattr(dumper, "_clang_available", lambda *a, **k: False)
    # Falls back to castxml so the existing "install castxml" error surfaces.
    assert _resolve_header_backend("auto") == "castxml"


# ── remaining branch coverage ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "type_str,expected",
    [
        # A function-pointer's `*` sits inside parens, so the top-level heuristic
        # reads it as depth 0 (a documented spelling-heuristic limit).
        ("int (*)(int)", 0),
        ("char[10]", 0),
        ("int **)(", 2),
    ],
)
def test_pointer_depth_brackets(type_str: str, expected: int) -> None:
    assert _pointer_depth(type_str) == expected


def test_function_qualifiers_nested_parens() -> None:
    # A parameter that is itself a function pointer: the qualifier scan must skip
    # past the *outer* parameter list, landing on the trailing ` const`.
    assert "const" in _function_qualifiers("void (int (*)(int)) const")


def test_visibility_elf_only_from_static_table() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1fv",
            "type": {"qualType": "void ()"},
        }
    )
    # Present in the static (.symtab) set only → ELF_ONLY.
    (fn,) = _ClangAstParser(root, set(), {"_Z1fv"}).parse_functions()
    assert fn.visibility == Visibility.ELF_ONLY


def test_protected_member_access() -> None:
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "C",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {"kind": "AccessSpecDecl", "access": "protected"},
                {
                    "kind": "CXXMethodDecl",
                    "name": "p",
                    "type": {"qualType": "void ()"},
                    "mangledName": "_ZN1C1pEv",
                },
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.access == AccessLevel.PROTECTED


def test_enum_explicit_non_integer_value_falls_back_to_autoincrement() -> None:
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "E",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {"kind": "EnumConstantDecl", "name": "A"},
                {
                    "kind": "EnumConstantDecl",
                    "name": "B",
                    # a non-numeric ConstantExpr value → treated as implicit
                    "inner": [{"kind": "ConstantExpr", "value": "not-a-number"}],
                },
            ],
        }
    )
    (enum,) = _ClangAstParser(root, set(), set()).parse_enums()
    assert [(m.name, m.value) for m in enum.members] == [("A", 0), ("B", 1)]


def test_typedef_desugared_fallback() -> None:
    root = _tu(
        {
            "kind": "TypedefDecl",
            "name": "t",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"desugaredQualType": "unsigned long"},
        }
    )
    assert _ClangAstParser(root, set(), set()).parse_typedefs() == {"t": "unsigned long"}


def test_function_with_no_type_defaults_to_void_return() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1fv",
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.return_type == "void"


def test_node_file_spelling_loc_fallback() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"spellingLoc": {"file": "include/foo.h", "line": 4}},
            "mangledName": "_Z1fv",
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.source_location == "include/foo.h:4"


# ── review-driven correctness fixes ──────────────────────────────────────────


@pytest.mark.parametrize(
    "qualtype,expected",
    [
        ("void () noexcept", True),
        ("void () noexcept(true)", True),
        ("void () noexcept(1)", True),
        ("void () noexcept(false)", False),
        ("void () noexcept(0)", False),
        ("void ()", False),
    ],
)
def test_noexcept_false_is_throwing(qualtype: str, expected: bool) -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1fv",
            "type": {"qualType": qualtype},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_noexcept is expected


def test_extern_c_from_linkage_spec_flag_not_just_heuristic() -> None:
    # A C++-mangled name inside extern "C" is unusual, but the linkage-spec flag
    # is authoritative and must win regardless of the mangled==name heuristic.
    root = _tu(
        {
            "kind": "LinkageSpecDecl",
            "language": "C",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "f",
                    "loc": {"line": 2},
                    "mangledName": "_Z1fv",  # heuristic alone would say "not C"
                    "type": {"qualType": "void ()"},
                }
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_extern_c is True


def test_function_body_locals_do_not_leak_into_variables() -> None:
    # An inline function defined in a public header: its block-scope local with
    # no storageClass must NOT be categorized as an ABI variable/constant.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1fv",
            "type": {"qualType": "void ()"},
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [
                        {
                            "kind": "DeclStmt",
                            "inner": [
                                {
                                    "kind": "VarDecl",
                                    "name": "local",
                                    "constexpr": True,
                                    "type": {"qualType": "const int"},
                                    "inner": [{"kind": "IntegerLiteral", "value": "7"}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    parser = _ClangAstParser(root, set(), set(), public_header_paths=["include/foo.h"])
    assert parser.parse_variables() == []
    assert parser.parse_constants() == {}


def test_folded_enum_value_on_constantexpr_wrapper() -> None:
    # `enum { A = 1 << 3 }`: clang folds the value onto the ConstantExpr wrapper,
    # whose BinaryOperator child has no `value`. The wrapper value must be read.
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "E",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "EnumConstantDecl",
                    "name": "A",
                    "inner": [
                        {
                            "kind": "ConstantExpr",
                            "value": "8",
                            "inner": [{"kind": "BinaryOperator", "opcode": "<<"}],
                        }
                    ],
                },
                {"kind": "EnumConstantDecl", "name": "B"},
            ],
        }
    )
    (enum,) = _ClangAstParser(root, set(), set()).parse_enums()
    assert [(m.name, m.value) for m in enum.members] == [("A", 8), ("B", 9)]


def test_folded_bitfield_width_on_constantexpr_wrapper() -> None:
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "bits",
                    "type": {"qualType": "unsigned int"},
                    "isBitfield": True,
                    "inner": [
                        {
                            "kind": "ConstantExpr",
                            "value": "4",
                            "inner": [{"kind": "BinaryOperator", "opcode": "<<"}],
                        }
                    ],
                }
            ],
        }
    )
    (s,) = _ClangAstParser(root, set(), set()).parse_types()
    assert s.fields[0].bitfield_bits == 4


# ── anonymous typedef records + remaining backend branches ───────────────────


def test_anonymous_typedef_struct_emitted_with_fields() -> None:
    # typedef struct { int x; } Foo; — clang emits an unnamed RecordDecl that
    # the typedef's ownedTagDecl links to; the record must surface as "Foo".
    rid = "0xRECORD"
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "",
            "id": rid,
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
        },
        {
            "kind": "TypedefDecl",
            "name": "Foo",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "struct Foo"},
            "inner": [
                {
                    "kind": "ElaboratedType",
                    "ownedTagDecl": {"id": rid, "kind": "RecordDecl", "name": ""},
                }
            ],
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    assert "Foo" in types
    assert [(f.name, f.type) for f in types["Foo"].fields] == [("x", "int")]


def test_truly_anonymous_record_without_typedef_dropped() -> None:
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "",
            "id": "0xANON",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
        }
    )
    assert _ClangAstParser(root, set(), set()).parse_types() == []


def test_clang_header_dump_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A hard parse error (nonzero exit) must fail, even if clang emitted some
    # JSON — the L2 header AST must be complete to be authoritative.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")

    class _P:
        stdout = '{"kind": "TranslationUnitDecl", "inner": []}'
        stderr = "error: use of undeclared identifier"
        returncode = 1

    monkeypatch.setattr(dumper.subprocess, "run", lambda *a, **k: _P())
    with pytest.raises(SnapshotError, match="failed to parse"):
        _clang_header_dump([header], [])


def test_clang_header_dump_gcc_path_not_used_as_clang(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A --gcc-path pointing at g++ must NOT become the clang executable.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False  # force the missing-tool error so we can inspect the bin

    monkeypatch.setattr(dumper, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump([header], [], gcc_path="/usr/bin/g++")
    # Fell back to a clang driver, NOT the supplied g++ binary.
    assert seen["bin"] != "/usr/bin/g++"
    assert "clang" in seen["bin"]


def test_clang_header_dump_explicit_clang_path_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False

    monkeypatch.setattr(dumper, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump([header], [], gcc_path="/opt/llvm/bin/clang-18")
    assert seen["bin"] == "/opt/llvm/bin/clang-18"


def test_clang_header_dump_gcc_prefix_maps_to_prefixed_clang(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False

    monkeypatch.setattr(dumper, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump(
            [header], [], gcc_prefix="aarch64-linux-gnu-", compiler="c++"
        )
    assert seen["bin"] == "aarch64-linux-gnu-clang++"


def test_owned_tag_id_absent_returns_empty() -> None:
    from abicheck.dumper_clang import _owned_tag_id

    assert _owned_tag_id({"kind": "TypedefDecl", "name": "t"}) == ""
