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

import time
from pathlib import Path

import pytest

from abicheck import dumper, dumper_clang, dumper_clang_errors
from abicheck.dumper import (
    _auto_system_includes_enabled,
    _build_clang_header_command,
    _clang_header_dump,
    _header_ast_parser,
    _parse_gnu_include_search_dirs,
    _resolve_clang_system_includes,
    _resolve_header_backend,
    _resolve_probe_compiler,
)
from abicheck.dumper_clang import (
    _ClangAstParser,
    _Decl,
    _function_qualifiers,
    _is_clang_family_binary,
    _pointer_depth,
    _return_type,
)
from abicheck.dumper_clang_errors import _parse_clang_ast_result
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
    # The second parameter carries a default-argument expression; its evaluated
    # value is preserved so a changed default fires PARAM_DEFAULT_VALUE_CHANGED.
    assert fn.params[0].default is None
    assert fn.params[1].default == "1"


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
                        {
                            "kind": "ParmVarDecl",
                            "name": "n",
                            "type": {"qualType": "int"},
                        }
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
    parser = _ClangAstParser(root, set(), set(), public_header_paths=["include/foo.h"])
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
    assert derived.has_anonymous_aggregate_fields is False


def test_parse_types_sets_qualified_name_for_namespaced_record() -> None:
    # Codex review (G28 Phase 4): RecordType.qualified_name must be populated
    # for a namespaced/nested type -- the layout tool's own JSON output
    # indexes records by RD->getQualifiedNameAsString() (e.g. "ns::Foo"), and
    # a lookup that only ever sees the bare name never matches, silently
    # losing layout enrichment for any non-global-namespace type.
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "CXXRecordDecl",
                    "name": "Foo",
                    "tagUsed": "struct",
                    "loc": {"line": 2},
                    "completeDefinition": True,
                    "inner": [
                        {"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}},
                    ],
                },
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Global",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 20},
            "completeDefinition": True,
            "inner": [
                {"kind": "FieldDecl", "name": "b", "type": {"qualType": "int"}},
            ],
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    assert types["Foo"].qualified_name == "ns::Foo"
    # A global-scope record's qualified spelling is identical to its bare
    # name, so qualified_name stays None (matches castxml's own convention).
    assert types["Global"].qualified_name is None


def test_parse_enums_sets_qualified_name_for_namespaced_enum() -> None:
    # Mirrors test_parse_types_sets_qualified_name_for_namespaced_record --
    # EnumType.qualified_name (PR #608 follow-up) uses the same scope-derived
    # population so old/new enum matching can key on namespace-qualified
    # identity instead of the bare, collidable EnumType.name.
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "EnumDecl",
                    "name": "Status",
                    "loc": {"line": 2},
                    "inner": [
                        {"kind": "EnumConstantDecl", "name": "OK"},
                    ],
                },
            ],
        },
        {
            "kind": "EnumDecl",
            "name": "Global",
            "loc": {"file": "include/foo.h", "line": 20},
            "inner": [
                {"kind": "EnumConstantDecl", "name": "A"},
            ],
        },
    )
    enums = {e.name: e for e in _ClangAstParser(root, set(), set()).parse_enums()}
    assert enums["Status"].qualified_name == "ns::Status"
    # A global-scope enum's qualified spelling is identical to its bare name,
    # so qualified_name stays None (matches castxml's own convention).
    assert enums["Global"].qualified_name is None


def test_parse_types_anonymous_aggregate_flattening_sets_flag() -> None:
    """A record whose members come from an anonymous struct/union gets
    those members flattened into `fields` (clang emits an IndirectFieldDecl
    per injected member), and RecordType.has_anonymous_aggregate_fields must
    reflect that — the DWARF layout backfill relies on this structural
    signal to trust a namespaced match for exactly this pattern without also
    trusting an ordinary struct's coincidental match (Codex review)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "RecordDecl",
                    "tagUsed": "union",
                    "loc": {"file": "include/foo.h", "line": 2},
                    "completeDefinition": True,
                    "inner": [
                        {"kind": "FieldDecl", "name": "i", "type": {"qualType": "int"}},
                        {
                            "kind": "FieldDecl",
                            "name": "f",
                            "type": {"qualType": "float"},
                        },
                    ],
                },
                {"kind": "IndirectFieldDecl", "name": "i"},
                {"kind": "IndirectFieldDecl", "name": "f"},
            ],
        }
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    foo = types["Foo"]
    assert [f.name for f in foo.fields] == ["i", "f"]
    assert foo.has_anonymous_aggregate_fields is True


def test_parse_types_mixed_anonymous_and_ordinary_fields_flag_is_false() -> None:
    """Regression (Codex review, fresh evidence): a record mixing an
    anonymous-aggregate member with an *ordinary* named field (`struct Foo {
    union { int i; }; int tag; };`) must NOT set
    has_anonymous_aggregate_fields — the flag means every field came from
    the flatten, not merely "at least one did". Computing it from
    bool(injected) alone would let `tag` (no field/base corroboration of
    its own) ride along with a match trust it never earned, reopening the
    unrelated-empty-DWARF-candidate risk the flag exists to close."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "RecordDecl",
                    "tagUsed": "union",
                    "loc": {"file": "include/foo.h", "line": 2},
                    "completeDefinition": True,
                    "inner": [
                        {"kind": "FieldDecl", "name": "i", "type": {"qualType": "int"}},
                    ],
                },
                {"kind": "IndirectFieldDecl", "name": "i"},
                {"kind": "FieldDecl", "name": "tag", "type": {"qualType": "int"}},
            ],
        }
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    foo = types["Foo"]
    assert [f.name for f in foo.fields] == ["i", "tag"]
    assert foo.has_anonymous_aggregate_fields is False


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


def test_parse_anonymous_typedef_enum_uses_typedef_name() -> None:
    root = _tu(
        {
            "kind": "EnumDecl",
            "id": "0xenum",
            "name": "",
            "loc": {"file": "include/log.h", "line": 3},
            "inner": [
                {"kind": "EnumConstantDecl", "name": "LOG_NONE"},
                {"kind": "EnumConstantDecl", "name": "LOG_ERR"},
                {"kind": "EnumConstantDecl", "name": "LOG_WARN"},
            ],
        },
        {
            "kind": "TypedefDecl",
            "name": "log_level_t",
            "loc": {"file": "include/log.h", "line": 7},
            "type": {"qualType": "enum log_level_t"},
            "inner": [
                {
                    "kind": "ElaboratedType",
                    "ownedTagDecl": {
                        "kind": "EnumDecl",
                        "id": "0xenum",
                        "name": "",
                    },
                }
            ],
        },
    )
    (enum,) = _ClangAstParser(root, set(), set()).parse_enums()
    assert enum.name == "log_level_t"
    assert [(m.name, m.value) for m in enum.members] == [
        ("LOG_NONE", 0),
        ("LOG_ERR", 1),
        ("LOG_WARN", 2),
    ]


def test_parse_anonymous_typedef_enum_ignores_non_owned_typedefs() -> None:
    root = _tu(
        {
            "kind": "TypedefDecl",
            "name": "builtin_enum_t",
            "loc": {"file": "<built-in>", "line": 1},
            "inner": [
                {"ownedTagDecl": {"kind": "EnumDecl", "id": "0xbuiltin"}},
            ],
        },
        {
            "kind": "TypedefDecl",
            "name": "",
            "loc": {"file": "include/log.h", "line": 1},
            "inner": [
                {"ownedTagDecl": {"kind": "EnumDecl", "id": "0xempty"}},
            ],
        },
        {
            "kind": "TypedefDecl",
            "name": "not_enum_t",
            "loc": {"file": "include/log.h", "line": 2},
            "inner": [
                "not-a-dict-child",
                {"ownedTagDecl": {"kind": "RecordDecl", "id": "0xrecord"}},
                {"ownedTagDecl": {"kind": "EnumDecl"}},
            ],
        },
        {
            "kind": "EnumDecl",
            "id": "0xbuiltin",
            "name": "builtin_enum_t",
            "loc": {"file": "<built-in>", "line": 1},
        },
        {
            "kind": "EnumDecl",
            "id": "0xanon",
            "name": "",
            "loc": {"file": "include/log.h", "line": 3},
        },
    )
    parser = _ClangAstParser(root, set(), set())
    parser._typedefs.append(
        _Decl(
            {
                "kind": "TypedefDecl",
                "name": "",
                "inner": [{"ownedTagDecl": {"kind": "EnumDecl", "id": "0xmanual"}}],
            },
            (),
            "include/log.h",
            "public",
        )
    )
    assert parser.parse_enums() == []


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
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    assert _resolve_header_backend("auto") == "clang"
    assert _resolve_header_backend(None) == "clang"
    # An explicit request always wins over the env default.
    assert _resolve_header_backend("castxml") == "castxml"


def test_resolve_header_backend_ast_frontend_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ABICHECK_AST_FRONTEND is the canonical env knob."""
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    assert _resolve_header_backend("auto") == "clang"
    assert _resolve_header_backend(None) == "clang"
    # An out-of-enum ABICHECK_AST_FRONTEND value is ignored; auto then
    # fails closed to castxml.
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "bogus")
    monkeypatch.setattr("abicheck.dumper._castxml_available", lambda: True)
    assert _resolve_header_backend("auto") == "castxml"


def test_resolve_header_backend_auto_stays_castxml_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.setattr("abicheck.dumper._castxml_available", lambda: True)
    monkeypatch.setattr("abicheck.dumper_clang._clang_available", lambda *a, **k: True)
    assert _resolve_header_backend("auto") == "castxml"
    # Do not silently fall back to clang: clang AST lacks computed layout
    # evidence, so auto must fail closed through the castxml path.
    monkeypatch.setattr("abicheck.dumper._castxml_available", lambda: False)
    assert _resolve_header_backend("auto") == "castxml"


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

    monkeypatch.setattr("abicheck.dumper_clang._clang_available", lambda *a, **k: False)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    with pytest.raises(SnapshotError, match="not found in PATH"):
        _clang_header_dump([header], [])


def _stub_clang_self_heal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make _clang_header_dump fail once on a missing <cstddef> then succeed.

    Mocks the clang availability/system-include probe and subprocess so the
    C→C++ self-heal branch runs without a real compiler: the first parse exits
    nonzero with a missing C++ stdlib header, the C++ retry returns a minimal AST.
    """
    import subprocess as _sp

    monkeypatch.setattr("abicheck.dumper_clang._clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        "abicheck.dumper._resolve_clang_system_includes", lambda *a, **k: ()
    )
    fail = _sp.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="fatal error: 'cstddef' file not found"
    )
    ok = _sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    calls = {"n": 0}

    def _run(*a: object, **k: object) -> _sp.CompletedProcess[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return fail
        _write_stdout_file(k, '{"kind": "TranslationUnitDecl", "inner": []}')
        return ok

    monkeypatch.setattr("abicheck.dumper.deadline.run_bounded", _run)


def test_clang_self_heal_explicit_c_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
) -> None:
    # Explicit --lang c that self-heals to C++ overrides the user's request, so
    # it stays a visible warning (Codex review).
    import logging

    _stub_clang_self_heal(monkeypatch)
    header = tmp_path / "umbrella.h"
    header.write_text("int foo(void);\n")
    with caplog.at_level(logging.DEBUG, logger="abicheck.dumper"):
        root = _clang_header_dump([header], [], lang="c")
    assert root["kind"] == "TranslationUnitDecl"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("asked for C" in r.message for r in warns), [r.message for r in warns]


def test_clang_self_heal_auto_detected_is_debug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
) -> None:
    # Auto-detected C (lang=None, no inline C++ syntax) self-healing to C++ is
    # just noise → demoted to debug, no warning (the P6 fix this guards).
    import logging

    _stub_clang_self_heal(monkeypatch)
    header = tmp_path / "umbrella.h"
    header.write_text("int foo(void);\n")
    with caplog.at_level(logging.DEBUG, logger="abicheck.dumper"):
        root = _clang_header_dump([header], [])  # lang=None → auto-detect
    assert root["kind"] == "TranslationUnitDecl"
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert any(
        r.levelno == logging.DEBUG and "self-healed to C++" in r.message
        for r in caplog.records
    )


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
    assert (
        _ClangAstParser(
            root, set(), set(), public_header_paths=["include/foo.h"]
        ).parse_constants()
        == {}
    )


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


def _write_stdout_file(kwargs: dict, text: str) -> None:
    """Write *text* to a mocked ``deadline.run_bounded(..., stdout=<file>)``
    call's file object, mirroring what a real clang subprocess (its stdout
    redirected to a temp file by dumper.py's L2 streaming, see
    _clang_header_dump._run_clang) would have written. A no-op if the mock
    wasn't invoked with a real file (e.g. a test that intentionally leaves the
    AST empty to exercise the "no AST" error path)."""
    fobj = kwargs.get("stdout")
    if fobj is not None:
        fobj.write(text.encode("utf-8"))


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
        [],
        [],
        backend="clang",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic={"_Z3foov"},
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert isinstance(parser, _ClangAstParser)
    assert [f.name for f in parser.parse_functions()] == ["foo"]


def test_header_ast_parser_castxml_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: sentinel)
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: parser_sentinel)
    parser = _header_ast_parser(
        [],
        [],
        backend="castxml",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert parser is parser_sentinel
    assert parser._abicheck_ast_toolchain["producer"] == "castxml"


def test_header_ast_parser_stamps_castxml_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: sentinel)
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: parser_sentinel)
    monkeypatch.setattr(
        dumper,
        "_tool_identity_metadata",
        lambda _executable: {
            "selected": "/mock/castxml",
            "version": "castxml version 0.7.0\nclang version 18.1.8",
        },
    )
    parser = _header_ast_parser(
        [],
        [],
        backend="castxml",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert parser._abicheck_ast_supported is True
    assert parser._abicheck_ast_unsupported_reasons == []


def test_header_ast_parser_stamps_castxml_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: object())
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: parser_sentinel)
    monkeypatch.setattr(
        dumper,
        "_tool_identity_metadata",
        lambda _executable: {
            "selected": "/mock/castxml",
            "version": "castxml version 0.4.5\nclang version 8.0.0",
        },
    )
    parser = _header_ast_parser(
        [],
        [],
        backend="castxml",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert parser._abicheck_ast_supported is False
    assert "castxml_version_below_minimum" in parser._abicheck_ast_unsupported_reasons


def test_clang_header_dump_success_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'

    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    # Isolate the single clang AST-dump call: disable the castxml↔clang
    # system-include probe (itself a separate, best-effort subprocess).
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    root = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert cache.exists()  # result was cached
    # Second call hits the cache — subprocess is not invoked again.
    root2 = _clang_header_dump([header], [])
    assert root2 == root
    assert calls["n"] == 1


def test_clang_only_dump_does_not_require_gxx_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    monkeypatch.setattr(
        dumper,
        "_resolve_compiler_binary",
        lambda *_a, **_k: pytest.fail("clang-only dump resolved g++"),
    )

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    assert _clang_header_dump([header], []) == {
        "kind": "TranslationUnitDecl",
        "inner": [],
    }


def test_clang_header_dump_rechecks_deadline_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591): a warm AST cache hit still costs real time
    reading/parsing a potentially huge cached AST — deadline.check() must
    fire on that path too, not just on the cache-miss subprocess path."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    _clang_header_dump([header], [])  # warms the cache
    assert cache.exists() and calls["n"] == 1

    with dumper.deadline.deadline_scope(-1):  # already expired
        with pytest.raises(dumper.deadline.DeadlineExceeded):
            _clang_header_dump([header], [])
    assert calls["n"] == 1  # never reached the subprocess path — cache hit


def test_clang_header_dump_rechecks_deadline_after_cache_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591, round 3): json.loads() on a warm cache hit can
    itself consume the rest of the budget for a huge cached AST -- the
    existing pre-load deadline.check() doesn't catch that; must re-check
    again after the load before handing the root to the AST walker."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    _clang_header_dump([header], [])  # warms the cache
    assert cache.exists()

    real_json_loads = dumper.json.loads

    def _slow_loads(text):
        time.sleep(0.05)
        return real_json_loads(text)

    monkeypatch.setattr(dumper.json, "loads", _slow_loads)
    with dumper.deadline.deadline_scope(0.03):
        with pytest.raises(dumper.deadline.DeadlineExceeded):
            _clang_header_dump([header], [])


def test_parse_clang_ast_result_missing_ast_file_reports_no_ast(tmp_path: Path) -> None:
    # ast_path.stat() itself can fail (the temp file vanished/was never
    # created) — that must degrade to the same "no AST" error as an empty
    # file, not an unhandled OSError.
    result = _fake_proc(stdout="", stderr="boom", returncode=0)
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(SnapshotError, match="no AST"):
        _parse_clang_ast_result(result, tmp_path / "cache.json", missing)


def test_parse_clang_ast_result_swallows_cache_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The cache write (_atomic_copy) is best-effort: a failure there (e.g. an
    # unwritable cache dir) must not turn a successful parse into an error —
    # the caller already has the parsed root; only the cache population is lost.
    ast_path = tmp_path / "ast.json"
    ast_path.write_text(
        '{"kind": "TranslationUnitDecl", "inner": []}', encoding="utf-8"
    )
    result = _fake_proc(stdout="", stderr="", returncode=0)

    def _boom(_src, _dst):
        raise OSError("cache dir unwritable")

    monkeypatch.setattr(dumper_clang_errors, "_atomic_copy", _boom)
    root = _parse_clang_ast_result(result, tmp_path / "cache.json", ast_path)
    assert root == {"kind": "TranslationUnitDecl", "inner": []}


def test_parse_clang_ast_result_rechecks_deadline_after_json_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # CodeRabbit review (PR #591): json.load() on a multi-GB AST can itself
    # consume the remaining budget; the existing pre-load deadline.check()
    # doesn't catch that -- must re-check again before the (also non-trivial)
    # streamed cache copy, or an expired budget still completes it and hands
    # back a result for downstream AST walking.
    ast_path = tmp_path / "ast.json"
    ast_path.write_text(
        '{"kind": "TranslationUnitDecl", "inner": []}', encoding="utf-8"
    )
    result = _fake_proc(stdout="", stderr="", returncode=0)
    real_json_load = dumper_clang_errors.json.load

    def _slow_load(fh):
        time.sleep(0.05)
        return real_json_load(fh)

    monkeypatch.setattr(dumper_clang_errors.json, "load", _slow_load)
    with dumper_clang_errors.deadline.deadline_scope(0.03):
        with pytest.raises(dumper_clang_errors.deadline.DeadlineExceeded):
            _parse_clang_ast_result(result, tmp_path / "cache.json", ast_path)


def test_parse_clang_ast_result_rechecks_deadline_after_cache_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # CodeRabbit review (PR #591): the streamed cache copy (_atomic_copy) can
    # also consume the remaining budget on a huge AST; re-check once more
    # before returning so a budget that expired during the copy doesn't
    # silently hand the result to the (also potentially expensive) caller.
    ast_path = tmp_path / "ast.json"
    ast_path.write_text(
        '{"kind": "TranslationUnitDecl", "inner": []}', encoding="utf-8"
    )
    result = _fake_proc(stdout="", stderr="", returncode=0)

    def _slow_copy(_src, _dst):
        time.sleep(0.05)

    monkeypatch.setattr(dumper_clang_errors, "_atomic_copy", _slow_copy)
    with dumper_clang_errors.deadline.deadline_scope(0.03):
        with pytest.raises(dumper_clang_errors.deadline.DeadlineExceeded):
            _parse_clang_ast_result(result, tmp_path / "cache.json", ast_path)


def test_clang_header_dump_streams_stdout_to_file_not_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # P0 follow-up: a pathological header's clang -ast-dump=json can be
    # hundreds of MB to multiple GB (real-world field report). The L2 call
    # must redirect stdout to a real file (dumper.py's L2 streaming, mirroring
    # source_extractors/clang.py's L4 _run_ast_to_file) instead of
    # capture_output=True, which would buffer the whole payload into a Python
    # str on top of the dict this function builds.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    # Isolate the single clang AST-dump call from the (also run_bounded-based,
    # Codex review follow-up) system-include probe — otherwise its capture_
    # output=True call leaks into `seen` alongside the AST-dump call's kwargs.
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    seen: dict[str, object] = {}

    def _run(cmd, **kwargs):
        seen.update(kwargs)
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    _clang_header_dump([header], [])
    assert seen.get("capture_output") is not True
    assert hasattr(seen.get("stdout"), "write"), (
        "stdout must be a real writable file object, not None/PIPE — a "
        "pathological header's AST dump must never be buffered in memory"
    )


def test_clang_header_dump_no_output_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    # Exit 0 but empty stdout → the "no AST" path (a nonzero exit is the
    # earlier branch, covered by test_clang_header_dump_nonzero_exit_raises).
    monkeypatch.setattr(
        dumper.deadline,
        "run_bounded",
        lambda *a, **k: _fake_proc(stdout="", stderr="boom", returncode=0),
    )
    with pytest.raises(SnapshotError, match="no AST"):
        _clang_header_dump([header], [])


def test_clang_header_dump_rechecks_deadline_before_loading_ast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591): a --budget that expires exactly as clang exits
    successfully must not silently let the (potentially huge) AST JSON load +
    walk run well past it. deadline.check() must fire again right after the
    subprocess returns, before json.load — not just once before it was
    spawned."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    # Isolate the single clang AST-dump call from the setup/prep work leading
    # up to it (same as test_clang_header_dump_success_and_cache): disable the
    # castxml↔clang system-include probe, a separate best-effort subprocess
    # whose own variable cost would otherwise eat into the tight budget below
    # before the call under test even starts.
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with dumper.deadline.deadline_scope(0.03):
        with pytest.raises(dumper.deadline.DeadlineExceeded):
            _clang_header_dump([header], [])


def test_clang_header_dump_bad_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")

    def _run(*a, **k):
        _write_stdout_file(k, "not json")
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with pytest.raises(SnapshotError, match="not valid JSON"):
        _clang_header_dump([header], [])


@pytest.mark.parametrize(
    "stderr,expected",
    [
        # The <cXXX> C-compatibility wrappers are the unambiguous trigger.
        ("fatal error: 'cstddef' file not found", True),
        ("fatal error: 'cstdint' file not found", True),
        ("error: 'cstdlib' file not found", True),
        ("fatal error: 'cstring' file not found", True),
        # A C header miss carries a .h suffix → not a C++ stdlib signal.
        ("fatal error: 'stdio.h' file not found", False),
        ("fatal error: 'oneapi/tbb.h' file not found", False),
        # Plain-name C++ headers are deliberately NOT matched — they collide with
        # plausible C project header names (oneTBB ships a version.h), so matching
        # them could mask a real missing C dependency by re-parsing as C++.
        ("fatal error: 'string' file not found", False),
        ("fatal error: 'version' file not found", False),
        ("fatal error: 'vector' file not found", False),
        # An extensionless *project* include that is not a stdlib header must NOT
        # trigger the retry (a C header's real missing dependency, not C++).
        ("fatal error: 'config' file not found", False),
        ("fatal error: 'myheader' file not found", False),
        # Unrelated parse errors must not trigger the retry.
        ("error: use of undeclared identifier 'foo'", False),
        ("", False),
    ],
)
def test_is_missing_cpp_stdlib_header_error(stderr: str, expected: bool) -> None:
    assert dumper._is_missing_cpp_stdlib_header_error(stderr) is expected


def test_clang_header_dump_retries_cpp_on_missing_cpp_stdlib_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A pure-#include umbrella header has no inline C++ syntax, so it is parsed in
    # C mode first; the missing-<cstddef> failure must trigger one C++-mode retry
    # (with -x c++ in the rebuilt command) rather than hard-failing.
    header = tmp_path / "umbrella.h"
    header.write_text('#include "detail/impl.h"\n')
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        if len(cmds) == 1:
            return _fake_proc(
                stderr="fatal error: 'cstddef' file not found", returncode=1
            )
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    root = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert len(cmds) == 2  # one C attempt + one C++ retry
    assert "c" in cmds[0] and cmds[0][cmds[0].index("-x") + 1] == "c"
    assert cmds[1][cmds[1].index("-x") + 1] == "c++"


def test_clang_header_dump_no_retry_on_other_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A non-"missing C++ stdlib header" failure must NOT retry — it surfaces as-is.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(stderr="error: undeclared identifier 'x'", returncode=1)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with pytest.raises(SnapshotError, match="failed to parse"):
        _clang_header_dump([header], [])
    assert calls["n"] == 1  # no retry


def test_resolve_header_backend_neither_tool_defaults_castxml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.setattr(dumper, "_castxml_available", lambda: False)
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: False)
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
    assert _ClangAstParser(root, set(), set()).parse_typedefs() == {
        "t": "unsigned long"
    }


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


def test_anonymous_union_members_flattened() -> None:
    # struct S { union { int i; float f; }; int tag; }; — clang nests the
    # anonymous union as an unnamed RecordDecl plus an implicit unnamed
    # FieldDecl, and marks the injected members with IndirectFieldDecl. The
    # union's members must surface directly on S (matching castxml).
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "RecordDecl",
                    "name": "",
                    "tagUsed": "union",
                    "inner": [
                        {"kind": "FieldDecl", "name": "i", "type": {"qualType": "int"}},
                        {
                            "kind": "FieldDecl",
                            "name": "f",
                            "type": {"qualType": "float"},
                        },
                    ],
                },
                {
                    "kind": "FieldDecl",
                    "name": "",
                    "type": {"qualType": "union S::(anonymous)"},
                },
                {"kind": "IndirectFieldDecl", "name": "i"},
                {"kind": "IndirectFieldDecl", "name": "f"},
                {"kind": "FieldDecl", "name": "tag", "type": {"qualType": "int"}},
            ],
        }
    )
    (s,) = _ClangAstParser(root, set(), set()).parse_types()
    assert [(f.name, f.type) for f in s.fields] == [
        ("i", "int"),
        ("f", "float"),
        ("tag", "int"),
    ]


def test_typedef_anonymous_record_inside_struct_not_flattened() -> None:
    # struct S { typedef struct { int z; } T; int a; }; — the unnamed RecordDecl
    # is owned by a nested typedef, NOT an anonymous aggregate member (no
    # IndirectFieldDecl), so its `z` must not leak into S's fields.
    root = _tu(
        {
            "kind": "RecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "RecordDecl",
                    "name": "",
                    "tagUsed": "struct",
                    "inner": [
                        {"kind": "FieldDecl", "name": "z", "type": {"qualType": "int"}}
                    ],
                },
                {"kind": "TypedefDecl", "name": "T", "type": {"qualType": "struct T"}},
                {"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}},
            ],
        }
    )
    (s,) = _ClangAstParser(root, set(), set()).parse_types()
    assert [f.name for f in s.fields] == ["a"]


def test_hidden_friend_function_marked() -> None:
    # struct Pt { friend bool operator==(Pt, Pt) { ... } }; — an inline friend
    # is ADL-only; the FriendDecl-wrapped FunctionDecl must be flagged
    # is_hidden_friend so add/remove of the operator is still diffed.
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Pt",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FriendDecl",
                    "inner": [
                        {
                            "kind": "FunctionDecl",
                            "name": "operator==",
                            "loc": {"file": "include/foo.h", "line": 2},
                            "mangledName": "_ZeqRK2PtS1_",
                            "type": {"qualType": "bool (Pt, Pt)"},
                            "inner": [
                                {
                                    "kind": "ParmVarDecl",
                                    "name": "a",
                                    "type": {"qualType": "Pt"},
                                },
                                {
                                    "kind": "ParmVarDecl",
                                    "name": "b",
                                    "type": {"qualType": "Pt"},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.name == "operator=="
    assert fn.is_hidden_friend is True


def test_default_argument_non_literal_fingerprint_and_marker_fallback() -> None:
    # A non-literal default keeps a stable structural fingerprint (so two
    # different defaults compare unequal); a default flagged present but with no
    # usable expression child still records its presence via the bare marker.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z1fii",
            "type": {"qualType": "void (int, int)"},
            "inner": [
                {
                    "kind": "ParmVarDecl",
                    "name": "x",
                    "type": {"qualType": "int"},
                    "init": "c",
                    "inner": [{"kind": "DeclRefExpr", "name": "kDefault"}],
                },
                {
                    "kind": "ParmVarDecl",
                    "name": "y",
                    "type": {"qualType": "int"},
                    "init": "c",
                    # presence flagged but no usable expression child
                    "inner": [{"kind": "FullComment"}],
                },
            ],
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.params[0].default is not None
    assert fn.params[0].default.startswith("expr:")
    assert fn.params[1].default == "default"


def test_clang_header_dump_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A hard parse error (nonzero exit) must fail, even if clang emitted some
    # JSON — the L2 header AST must be complete to be authoritative.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")

    class _P:
        stdout = '{"kind": "TranslationUnitDecl", "inner": []}'
        stderr = "error: use of undeclared identifier"
        returncode = 1

    monkeypatch.setattr(dumper.deadline, "run_bounded", lambda *a, **k: _P())
    with pytest.raises(SnapshotError, match="failed to parse"):
        _clang_header_dump([header], [])


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/usr/bin/clang", True),
        ("/usr/bin/clang++-18", True),
        ("clang-cl.exe", True),  # extension stripped via .stem, still matches
        ("/opt/intel/oneapi/compiler/2026.1/bin/icx", True),
        ("/opt/intel/oneapi/compiler/2026.1/bin/icpx", True),
        ("/opt/intel/oneapi/compiler/2026.1/bin/dpcpp", True),
        ("/opt/intel/oneapi/compiler/2026.1/bin/dpcpp-cl", True),
        ("ICPX", True),  # case-insensitive
        ("icpx.exe", True),  # extension stripped, still matches the alias set
        ("/usr/bin/g++-13", False),
        ("/usr/bin/x86_64-linux-gnu-gcc-13", False),
        ("cl.exe", False),  # MSVC's own cl.exe is not clang-family
        ("icc", False),  # Intel's older, non-clang-based Classic compiler
    ],
)
def test_is_clang_family_binary(path: str, expected: bool) -> None:
    """Pure-function unit test for the --gcc-path clang-family classifier,
    directly exercising both the "clang" substring branch and the vendor
    alias-set branch (icx/icpx/dpcpp/dpcpp-cl) in isolation from the larger
    _clang_header_dump/_resolve_clang_bin call chain the tests below cover."""
    assert _is_clang_family_binary(path) is expected


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

    monkeypatch.setattr(dumper_clang, "_clang_available", _avail)
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

    monkeypatch.setattr(dumper_clang, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump([header], [], gcc_path="/opt/llvm/bin/clang-18")
    assert seen["bin"] == "/opt/llvm/bin/clang-18"


@pytest.mark.parametrize(
    "gcc_path",
    [
        "/opt/intel/oneapi/compiler/2026.1/bin/icpx",
        "/opt/intel/oneapi/compiler/2026.1/bin/icx",
        "/opt/intel/oneapi/compiler/2026.1/bin/dpcpp",
        "/opt/intel/oneapi/compiler/2026.1/bin/dpcpp-cl",
        "ICPX",  # case-insensitive
    ],
)
def test_clang_header_dump_gcc_path_recognizes_icx_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gcc_path: str
) -> None:
    # Intel's oneAPI DPC++/C++ compiler is clang-based (accepts -Xclang directly)
    # but is not spelled "clang" -- --gcc-path must still be honored for it
    # instead of silently falling back to a plain "clang" on PATH (a different,
    # possibly differently-configured compiler than the one the real build used).
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False

    monkeypatch.setattr(dumper_clang, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump([header], [], gcc_path=gcc_path)
    assert seen["bin"] == gcc_path


def test_clang_header_dump_gcc_path_gcc_binary_not_mistaken_for_icx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A real GCC binary must not accidentally match the icx/icpx alias set.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False

    monkeypatch.setattr(dumper_clang, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump([header], [], gcc_path="/usr/bin/x86_64-linux-gnu-gcc-13")
    assert seen["bin"] != "/usr/bin/x86_64-linux-gnu-gcc-13"
    assert "clang" in seen["bin"]


def test_clang_header_dump_gcc_prefix_maps_to_prefixed_clang(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    seen = {}

    def _avail(b="clang"):
        seen["bin"] = b
        return False

    monkeypatch.setattr(dumper_clang, "_clang_available", _avail)
    with pytest.raises(SnapshotError):
        _clang_header_dump(
            [header], [], gcc_prefix="aarch64-linux-gnu-", compiler="c++"
        )
    assert seen["bin"] == "aarch64-linux-gnu-clang++"


def test_owned_tag_id_absent_returns_empty() -> None:
    from abicheck.dumper_clang import _owned_tag_id

    assert _owned_tag_id({"kind": "TypedefDecl", "name": "t"}) == ""


# ── pure-helper branch coverage ──────────────────────────────────────────────


def test_return_type_and_qualifiers_with_template_brackets() -> None:
    from abicheck.dumper_clang import _function_qualifiers, _return_type

    # Generic return type with <> brackets, then a templated param list + const.
    assert _return_type("std::map<int, char> (int)") == "std::map<int, char>"
    quals = _function_qualifiers("void (std::vector<int>) const &&")
    assert "const" in quals and "&&" in quals


def test_pointer_depth_closing_brackets_underflow() -> None:
    # Stray closing brackets must clamp at zero, not go negative.
    assert _pointer_depth("]>)*") == 1


def test_visibility_matches_by_plain_name() -> None:
    from abicheck.dumper_clang import _ClangAstParser
    from abicheck.model import Visibility

    p = _ClangAstParser({"kind": "TranslationUnitDecl", "inner": []}, {"foo"}, {"bar"})
    # No mangled name, matched by plain name in the dynamic/static tables.
    assert p._visibility("", "foo") == Visibility.PUBLIC
    assert p._visibility("", "bar") == Visibility.ELF_ONLY
    assert p._visibility("", "nope") == Visibility.HIDDEN


def test_symbol_candidates_and_source_location_edges() -> None:
    from abicheck.dumper_clang import _ClangAstParser, _Decl

    assert _ClangAstParser._symbol_candidates("") == ()
    assert _ClangAstParser._symbol_candidates("foo") == ("foo",)
    # No file at all → no source location.
    entry = _Decl(node={"kind": "FunctionDecl"}, scope=(), file="", access="public")
    assert _ClangAstParser._source_location(entry) is None


def test_node_helpers_on_non_dict_and_missing_loc() -> None:
    from abicheck.dumper_clang import _node_file, _node_line

    assert _node_file({}, "prev.h") == "prev.h"  # no loc → keep current
    assert _node_line({}) == 0
    assert _node_line({"loc": {}}) == 0


def test_canonical_expr_and_typedef_underlying_edges() -> None:
    from abicheck.dumper_clang import _canonical_expr, _typedef_underlying

    # A non-dict node is returned verbatim; a dict keeps type.qualType.
    assert _canonical_expr("leaf") == "leaf"
    out = _canonical_expr({"kind": "X", "type": {"qualType": "int"}, "inner": ["y"]})
    assert out == {"kind": "X", "type": "int", "inner": ["y"]}
    # Typedef with a non-dict type → empty underlying.
    assert _typedef_underlying({"type": None}) == ""


def test_owned_tag_id_nested_and_non_dict() -> None:
    from abicheck.dumper_clang import _owned_tag_id

    node = {"inner": ["x", {"inner": [{"ownedTagDecl": {"id": "0xABC"}}]}]}
    assert _owned_tag_id(node) == "0xABC"
    assert _owned_tag_id({"inner": [123]}) == ""


def test_clang_header_dump_timeout_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess as _sp

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")

    def _boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="clang", timeout=120)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _boom)
    with pytest.raises(SnapshotError, match="timed out"):
        _clang_header_dump([header], [])


def test_clang_header_dump_corrupt_cache_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "c.json"
    cache.write_text("{ this is not valid json")  # corrupt prior cache entry
    ast = '{"kind": "TranslationUnitDecl", "inner": []}'

    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)

    def _run(*a, **k):
        _write_stdout_file(k, ast)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    # The corrupt cache is unlinked and the fresh clang run repopulates it.
    root = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}


# ── castxml↔clang system-include auto-detection (parity fix) ─────────────────

_GCC_VERBOSE_STDERR = """\
ignoring nonexistent directory "/usr/local/include/x86_64-linux-gnu"
#include "..." search starts here:
#include <...> search starts here:
 /usr/include/c++/13
 /usr/include/x86_64-linux-gnu/c++/13
 /usr/lib/gcc/x86_64-linux-gnu/13/include
 /usr/include
End of search list.
"""


def test_parse_gnu_include_search_dirs() -> None:
    dirs = _parse_gnu_include_search_dirs(_GCC_VERBOSE_STDERR)
    # Only the lines inside the <...> block, in order; the leading "ignoring"
    # line and the quote-include marker are excluded.
    assert dirs == [
        "/usr/include/c++/13",
        "/usr/include/x86_64-linux-gnu/c++/13",
        "/usr/lib/gcc/x86_64-linux-gnu/13/include",
        "/usr/include",
    ]


def test_parse_gnu_include_search_dirs_strips_framework_note() -> None:
    stderr = (
        "#include <...> search starts here:\n"
        " /System/Library/Frameworks (framework directory)\n"
        "End of search list.\n"
    )
    assert _parse_gnu_include_search_dirs(stderr) == ["/System/Library/Frameworks"]


def test_parse_gnu_include_search_dirs_empty_when_no_block() -> None:
    assert _parse_gnu_include_search_dirs("clang: error: no input files\n") == []


@pytest.mark.parametrize("off", ["0", "false", "no", "off", "OFF"])
def test_auto_system_includes_enabled_off_values(
    monkeypatch: pytest.MonkeyPatch, off: str
) -> None:
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", off)
    assert _auto_system_includes_enabled() is False


def test_auto_system_includes_enabled_default_and_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_AUTO_SYSTEM_INCLUDES", raising=False)
    assert _auto_system_includes_enabled() is True
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "1")
    assert _auto_system_includes_enabled() is True


def test_resolve_probe_compiler_prefers_gnu_gcc_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck import dumper_sysinc

    monkeypatch.setattr(dumper_sysinc.shutil, "which", lambda c: c)
    # An explicit GNU --gcc-path is used verbatim…
    assert _resolve_probe_compiler("c++", "/opt/gcc-13/bin/g++", None) == (
        "/opt/gcc-13/bin/g++"
    )
    # …but a clang there is skipped (useless for libstdc++ discovery) → g++.
    assert _resolve_probe_compiler("c++", "/usr/bin/clang++", None) == "g++"
    # Cross prefix maps to the prefixed GNU driver.
    assert _resolve_probe_compiler("c++", None, "aarch64-linux-gnu-") == (
        "aarch64-linux-gnu-g++"
    )
    # C mode probes gcc.
    assert _resolve_probe_compiler("cc", None, None) == "gcc"


def test_resolve_probe_compiler_skips_clang_family_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A --gcc-path icx/icpx/dpcpp/dpcpp-cl is clang-family, not GNU — probing it
    (instead of a real g++/gcc on PATH) yields incomplete system include dirs
    (e.g. missing /usr/include with stdlib.h), since these are the same clang
    binary under a different name, not real gcc/g++."""
    from abicheck import dumper_sysinc

    monkeypatch.setattr(dumper_sysinc.shutil, "which", lambda c: c)
    for alias in ("icx", "icpx", "dpcpp", "dpcpp-cl"):
        assert _resolve_probe_compiler("c++", f"/opt/intel/bin/{alias}", None) == "g++"


def test_resolve_probe_compiler_none_when_no_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck import dumper_sysinc

    monkeypatch.setattr(dumper_sysinc.shutil, "which", lambda c: None)
    assert _resolve_probe_compiler("c++", None, None) is None


def test_resolve_clang_system_includes_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck import dumper_sysinc

    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "1")
    monkeypatch.setattr(
        dumper_sysinc,
        "_probe_gnu_system_includes",
        lambda *a, **k: ["/usr/include/c++/13"],
    )
    monkeypatch.setattr(dumper_sysinc, "_resolve_probe_compiler", lambda *a, **k: "g++")

    base = dict(gcc_path=None, gcc_prefix=None, force_cpp=True)
    # Default: probed dirs returned.
    assert _resolve_clang_system_includes(
        "c++", sysroot=None, nostdinc=False, **base
    ) == ("/usr/include/c++/13",)
    # nostdinc, explicit sysroot, or the env toggle each suppress the probe.
    assert (
        _resolve_clang_system_includes("c++", sysroot=None, nostdinc=True, **base) == ()
    )
    assert (
        _resolve_clang_system_includes(
            "c++", sysroot=Path("/sysroot"), nostdinc=False, **base
        )
        == ()
    )
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    assert (
        _resolve_clang_system_includes("c++", sysroot=None, nostdinc=False, **base)
        == ()
    )


def test_resolve_clang_system_includes_no_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck import dumper_sysinc

    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "1")
    monkeypatch.setattr(dumper_sysinc, "_resolve_probe_compiler", lambda *a, **k: None)
    assert (
        _resolve_clang_system_includes(
            "c++",
            gcc_path=None,
            gcc_prefix=None,
            sysroot=None,
            nostdinc=False,
            force_cpp=True,
        )
        == ()
    )


def test_build_clang_command_injects_isystem(tmp_path: Path) -> None:
    agg = tmp_path / "agg.hpp"
    agg.write_text("")
    cmd = _build_clang_header_command(
        "clang++",
        "gnu",
        [tmp_path / "inc"],
        agg,
        force_cpp=True,
        system_includes=("/usr/include/c++/13", "/usr/include"),
    )
    # User -I precedes the probed -isystem dirs, which appear as pairs.
    assert "-I" in cmd
    i = cmd.index("-isystem")
    assert cmd[i + 1] == "/usr/include/c++/13"
    assert cmd[cmd.index("/usr/include") - 1] == "-isystem"
    # The user's -I still comes before the first -isystem (explicit wins).
    assert cmd.index("-I") < i


def test_build_clang_command_probed_isystem_after_user_flags(tmp_path: Path) -> None:
    # Auto-probed -isystem must follow the user's pass-through flags so a
    # user-supplied SDK -isystem keeps higher search priority (Codex review).
    agg = tmp_path / "agg.hpp"
    agg.write_text("")
    cmd = _build_clang_header_command(
        "clang++",
        "gnu",
        [],
        agg,
        force_cpp=True,
        gcc_options="-isystem /sdk/include",
        gcc_option_tokens=("-isystem", "/sdk2"),
        system_includes=("/usr/include/c++/13",),
    )
    user_sdk = cmd.index("/sdk/include")
    user_sdk2 = cmd.index("/sdk2")
    probed = cmd.index("/usr/include/c++/13")
    # Both user-supplied system dirs are searched before the probed fallback.
    assert user_sdk < probed
    assert user_sdk2 < probed


@pytest.mark.parametrize(
    "gcc_options,gcc_option_tokens",
    [
        ("-nostdinc", ()),
        ("-nostdinc++", ()),
        ("--sysroot=/sdk", ()),
        ("-isysroot /sdk", ()),
        ("--gcc-toolchain=/opt/gcc", ()),
        ("--gcc-install-dir=/opt/gcc/lib", ()),
        ("--target=aarch64-linux-gnu", ()),
        (None, ("-nostdinc",)),
        (None, ("--sysroot=/sdk",)),
        (None, ("-nostdinc++",)),
        (None, ("--gcc-toolchain=/opt/gcc",)),
        (None, ("--target=aarch64-linux-gnu",)),
        (None, ("-target", "aarch64-linux-gnu")),
    ],
)
def test_resolve_clang_system_includes_respects_passthrough(
    monkeypatch: pytest.MonkeyPatch, gcc_options, gcc_option_tokens
) -> None:
    # Hermetic/cross flags supplied via --gcc-options/--gcc-option must suppress
    # the host probe too, not just the structured nostdinc/sysroot (Codex review).
    from abicheck import dumper_sysinc

    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "1")
    monkeypatch.setattr(dumper_sysinc, "_resolve_probe_compiler", lambda *a, **k: "g++")
    monkeypatch.setattr(
        dumper_sysinc, "_probe_gnu_system_includes", lambda *a, **k: ["/usr/x"]
    )
    assert (
        _resolve_clang_system_includes(
            "c++",
            gcc_path=None,
            gcc_prefix=None,
            sysroot=None,
            nostdinc=False,
            force_cpp=True,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
        )
        == ()
    )


def test_resolve_clang_system_includes_probes_without_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A benign --gcc-options that doesn't isolate the parse still probes.
    from abicheck import dumper_sysinc

    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "1")
    monkeypatch.setattr(dumper_sysinc, "_resolve_probe_compiler", lambda *a, **k: "g++")
    monkeypatch.setattr(
        dumper_sysinc, "_probe_gnu_system_includes", lambda *a, **k: ["/usr/x"]
    )
    assert _resolve_clang_system_includes(
        "c++",
        gcc_path=None,
        gcc_prefix=None,
        sysroot=None,
        nostdinc=False,
        force_cpp=True,
        gcc_options="-DFOO=1",
        gcc_option_tokens=("-O2",),
    ) == ("/usr/x",)


# ── field CV facts through a typedef indirection (Codex review, PR #582) ────
#
# clang's ``qualType`` renders a field's typedef'd type as the bare alias
# name (e.g. "T"), so a regex over that spelling alone can never see a
# qualifier the typedef's target carries. The real spelling is only visible
# via the separate ``desugaredQualType`` key. But scanning the WHOLE
# desugared spelling is itself wrong for a pointer typedef: a qualifier
# before the ``*`` belongs to the pointee, not the field's own pointer
# value — only a trailing, no-space qualifier after the ``*``
# (``int *const``, confirmed against real clang output) describes the
# field itself.


def _field_via_typedef(desugared: str) -> dict:
    return _tu(
        {
            "kind": "CXXRecordDecl",
            "tagUsed": "struct",
            "name": "S",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "x",
                    "type": {"qualType": "T", "desugaredQualType": desugared},
                }
            ],
        }
    )


def test_scalar_typedef_hides_const_in_qualtype_but_desugared_reveals_it() -> None:
    # typedef const int T; struct S { T x; };
    root = _field_via_typedef("const int")
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    (field,) = t.fields
    assert field.type == "T"
    assert field.is_const is True


def test_pointer_typedef_pointee_const_does_not_mark_field_const() -> None:
    # typedef const int *P; struct S { P x; }; — P is a plain, non-const
    # pointer; only its pointee is const. The earlier fix for the scalar
    # case above scanned the whole desugared spelling and wrongly marked
    # the field itself const here too.
    root = _field_via_typedef("const int *")
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    (field,) = t.fields
    assert field.is_const is False


def test_pointer_typedef_own_const_marks_field_const() -> None:
    # typedef int * const P; struct S { P x; }; — the pointer VALUE itself
    # is const this time (trailing, no-space "*const").
    root = _field_via_typedef("int *const")
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    (field,) = t.fields
    assert field.is_const is True


def test_pointer_typedef_both_pointer_and_pointee_const() -> None:
    # typedef const int * const P; — pointer itself const AND pointee
    # const; only the trailing "*const" should count toward the field's
    # own is_const.
    root = _field_via_typedef("const int *const")
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    (field,) = t.fields
    assert field.is_const is True


def test_pointer_typedef_own_volatile_marks_field_volatile() -> None:
    root = _field_via_typedef("int *volatile")
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    (field,) = t.fields
    assert field.is_volatile is True
