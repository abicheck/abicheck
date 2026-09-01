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

import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from abicheck import (
    dumper,
    dumper_cache,
    dumper_clang,
    dumper_clang_errors,
    dumper_toolchain,
)
from abicheck.dumper import (
    _auto_system_includes_enabled,
    _build_clang_header_command,
    _clang_header_dump,
    _configured_target_triple,
    _header_ast_parser,
    _needs_sycl_host_only,
    _parse_gnu_include_search_dirs,
    _resolve_clang_system_includes,
    _resolve_header_backend,
    _resolve_probe_compiler,
)
from abicheck.dumper_clang import (
    _ClangAstParser,
    _Decl,
    _dpcpp_defaults_sycl_on,
    _function_qualifiers,
    _is_clang_family_binary,
    _is_intel_sycl_driver,
    _pointer_depth,
    _return_type,
    _user_explicitly_disabled_sycl,
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


@pytest.mark.parametrize("attr_kind, expected", [
    ("MSABIAttr", "ms_abi"),
    ("SysVABIAttr", "sysv_abi"),
])
def test_parse_functions_preserves_x86_64_abi_attributes(
    attr_kind: str, expected: str,
) -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {"qualType": "void ()"},
            "inner": [{"kind": attr_kind}],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == [expected]


def test_parse_functions_recovers_outer_ms_abi_from_qual_type_when_attr_node_is_omitted() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {"qualType": "void (int) __attribute__((ms_abi))"},
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == ["ms_abi"]


def test_parse_functions_skips_typeof_return_type_before_outer_convention() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {
                "qualType": "typeof (1) () __attribute__((ms_abi))",
            },
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == ["ms_abi"]


def test_parse_functions_does_not_claim_nested_callback_abi_attribute() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {
                "qualType": "void (void (*)(int) __attribute__((ms_abi)))"
            },
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == []


def test_parse_functions_does_not_claim_nested_return_function_abi_attribute() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "factory",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "factory",
            "type": {
                "qualType": "void (__attribute__((sysv_abi)) *())()"
            },
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"factory"}, set()).parse_functions()

    assert fn.contract_attributes == []


def test_parse_functions_does_not_claim_real_clang_returned_callback_abi_spelling() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "factory",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "factory",
            # This is the real Clang JSON AST spelling for:
            # void (__attribute__((ms_abi)) *factory(void))(int);
            "type": {
                "qualType": "void (*(void))(int) __attribute__((ms_abi))"
            },
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"factory"}, set()).parse_functions()

    assert fn.contract_attributes == []


def test_configured_target_triple_uses_frontend_option_order(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = cmd
        return type("Result", (), {"returncode": 0, "stdout": "x86_64-pc-linux-gnu\n"})()

    monkeypatch.setattr(dumper.subprocess, "run", _run)

    assert _configured_target_triple(
        "--target=first -DFROM_OPTIONS", ("--target=last", "@flags.rsp"), "clang"
    ) == "x86_64-pc-linux-gnu"
    assert captured["cmd"] == [
        "clang", "--target=first", "-DFROM_OPTIONS", "--target=last", "@flags.rsp",
        "-print-target-triple",
    ]


def test_configured_target_triple_honors_clang_response_file(tmp_path: Path) -> None:
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("requires clang")
    response = tmp_path / "target.rsp"
    response.write_text("--target=x86_64-pc-windows-msvc\n", encoding="utf-8")

    assert _configured_target_triple(None, (f"@{response}",), clang) == (
        "x86_64-pc-windows-msvc"
    )


def test_parse_functions_uses_desugared_outer_type_for_typedef_abi_attribute() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {
                "qualType": "ApiFn",
                "desugaredQualType": "void (int) __attribute__((sysv_abi))",
            },
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == ["sysv_abi"]


def test_parse_functions_does_not_make_ignored_attribute_spelling_a_fact() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            # Clang drops unknown/ignored spellings from its AST type.  The
            # fallback must only recognize its two supported ABI spellings.
            "type": {"qualType": "void () __attribute__((unknown_thing))"},
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == []


def test_parse_functions_discards_explicit_sysv_default_for_known_target() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl", "name": "api",
            "loc": {"file": "include/api.h", "line": 3}, "mangledName": "api",
            "type": {"qualType": "void () __attribute__((sysv_abi))"}, "inner": [],
        }
    )

    (fn,) = _ClangAstParser(
        root, {"api"}, set(), target_triple="x86_64-pc-linux-gnu"
    ).parse_functions()

    assert fn.contract_attributes == []


def test_parse_functions_discards_explicit_ms_default_for_known_target() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl", "name": "api",
            "loc": {"file": "include/api.h", "line": 3}, "mangledName": "api",
            "type": {"qualType": "void () __attribute__((ms_abi))"}, "inner": [],
        }
    )

    (fn,) = _ClangAstParser(
        root, {"api"}, set(), target_triple="x86_64-pc-windows-msvc"
    ).parse_functions()

    assert fn.contract_attributes == []


def test_parse_functions_keeps_abi_spelling_without_known_x86_default() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl", "name": "api",
            "loc": {"file": "include/api.h", "line": 3}, "mangledName": "api",
            "type": {"qualType": "void () __attribute__((ms_abi))"}, "inner": [],
        }
    )

    (fn,) = _ClangAstParser(
        root, {"api"}, set(), target_triple="aarch64-unknown-linux-gnu"
    ).parse_functions()

    assert fn.contract_attributes == ["ms_abi"]


def test_parse_functions_ignores_non_string_qual_type_for_abi_fallback() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "loc": {"file": "include/api.h", "line": 3},
            "mangledName": "api",
            "type": {"qualType": None},
            "inner": [],
        }
    )

    (fn,) = _ClangAstParser(root, {"api"}, set()).parse_functions()

    assert fn.contract_attributes == []


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
        # A forward declaration with no definition anywhere in this TU emits
        # an opaque stub (G31 Phase C fact-completeness, PR #719 follow-up)
        # -- mirrors castxml's own `incomplete="1"` handling instead of the
        # previous emit-nothing behavior.
        {
            "kind": "CXXRecordDecl",
            "name": "Opaque",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 10},
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    opaque = types["Opaque"]
    assert opaque.is_opaque is True
    assert opaque.fields == []
    assert opaque.bases == []
    assert opaque.size_bits is None
    derived = types["Derived"]
    assert derived.is_opaque is False
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


def test_parse_types_forward_decl_and_definition_collapse_to_one_complete_record() -> (
    None
):
    """A forward declaration followed by its own complete definition in the
    SAME TU (`struct Foo; struct Foo { int x; };`) -- confirmed with a real
    clang build that both land in self._records as separate CXXRecordDecl
    nodes sharing one identity -- must collapse to ONE record (the
    definition), not two, and must not be flagged opaque."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
        },
    )
    types = [
        t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "Foo"
    ]
    assert len(types) == 1
    assert types[0].is_opaque is False
    assert [f.name for f in types[0].fields] == ["x"]


def test_parse_types_definition_before_forward_decl_still_wins() -> None:
    """Order independence: the definition appearing FIRST in source order
    must still win over a later forward-decl stub sharing the same
    identity."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 5},
        },
    )
    types = [
        t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "Foo"
    ]
    assert len(types) == 1
    assert types[0].is_opaque is False
    assert [f.name for f in types[0].fields] == ["x"]


@pytest.mark.parametrize(
    "first_kind, second_kind", [("class", "struct"), ("struct", "class")]
)
def test_parse_types_opaque_redecl_kind_canonicalizes_regardless_of_order(
    first_kind: str, second_kind: str
) -> None:
    """`class Handle; struct Handle;` (either order) is legal, compiler-
    accepted C++ redeclaring one opaque type with different-but-compatible
    class keys. RecordType.kind must canonicalize to the same value
    ("struct" -- class/struct are collapsed to one fixed spelling before
    any min(kind), PR #719 follow-up) regardless of which spelling happened
    to come first, or reordering the two forward decls alone would flip the
    emitted kind and produce a false SOURCE_LEVEL_KIND_CHANGED (Codex
    review, PR #719). Collapsing to one fixed spelling (rather than
    min()-over-observed-spellings) also matters when the observed SET of
    redecls itself differs between two otherwise-unchanged snapshots -- see
    test_parse_types_opaque_redecl_kind_stable_when_a_compatible_redecl_is_added
    below."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": first_kind,
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": second_kind,
            "loc": {"file": "include/foo.h", "line": 2},
        },
    )
    types = [
        t
        for t in _ClangAstParser(root, set(), set()).parse_types()
        if t.name == "Handle"
    ]
    assert len(types) == 1
    assert types[0].is_opaque is True
    assert types[0].kind == "struct"


def test_parse_types_opaque_redecl_kind_stable_when_a_compatible_redecl_is_added() -> (
    None
):
    """Codex review, PR #719, follow-up: a snapshot with ONLY `struct H;`
    must canonicalize to the SAME kind as a later snapshot that adds the
    legal, semantics-preserving redeclaration `class H;` alongside it --
    both describe the identical opaque type. Before class/struct were
    canonicalized to one fixed spelling, min() over the OBSERVED set of
    redecl spellings changed value the moment `class H;` was added (min
    over {"struct"} is "struct", but min over {"class", "struct"} is
    "class"), producing a false SOURCE_LEVEL_KIND_CHANGED for a header
    change that didn't alter the type's real kind at all."""
    struct_only = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        }
    )
    struct_and_class = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 2},
        },
    )
    before = [
        t
        for t in _ClangAstParser(struct_only, set(), set()).parse_types()
        if t.name == "H"
    ]
    after = [
        t
        for t in _ClangAstParser(struct_and_class, set(), set()).parse_types()
        if t.name == "H"
    ]
    assert len(before) == len(after) == 1
    assert before[0].kind == after[0].kind == "struct"


def test_parse_types_opaque_kind_matches_later_complete_definition_of_same_key() -> (
    None
):
    """Codex review, PR #719, follow-up round two: a snapshot with ONLY
    `class H;` (no ambiguity -- a single, unopposed opaque redeclaration)
    must keep reporting its REAL kind ("class"), not a forced canonical
    spelling. Otherwise, comparing it against a later snapshot where `H`
    gains a same-key COMPLETE definition (`class H {};`) -- whose kind is
    always its own real, unmodified `_record_kind`, never overridden --
    would read as a class/struct mismatch purely because the OPAQUE side
    was unconditionally relabeled, producing a false
    SOURCE_LEVEL_KIND_CHANGED even though the class key never changed."""
    opaque_only = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 1},
        }
    )
    complete_same_key = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
        }
    )
    before = [
        t
        for t in _ClangAstParser(opaque_only, set(), set()).parse_types()
        if t.name == "H"
    ]
    after = [
        t
        for t in _ClangAstParser(complete_same_key, set(), set()).parse_types()
        if t.name == "H"
    ]
    assert len(before) == len(after) == 1
    assert before[0].is_opaque is True
    assert after[0].is_opaque is False
    assert before[0].kind == after[0].kind == "class"


def test_parse_types_opaque_redecl_merges_deprecated_from_a_later_decl() -> None:
    """`struct Handle; struct [[deprecated("old")]] Handle;` -- both nodes
    are non-definitions, so the kind tie-break keeps the FIRST one, which
    carries no DeprecatedAttr of its own (real clang attaches the attribute
    per-node). The message must still surface on the merged RecordType
    instead of vanishing with the discarded node (Codex review, PR #719)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "inner": [{"kind": "DeprecatedAttr", "message": "old"}],
        },
    )
    types = [
        t
        for t in _ClangAstParser(root, set(), set()).parse_types()
        if t.name == "Handle"
    ]
    assert len(types) == 1
    assert types[0].is_opaque is True
    assert types[0].deprecated == "old"


def test_parse_types_opaque_redecl_merges_bare_deprecated_marker() -> None:
    """`struct Handle; struct [[deprecated]] Handle;` (no message) -- the
    merge must preserve the intentionally meaningful empty-string marker
    from the discarded later node, not just a truthy message string, or
    ``deprecated`` wrongly reads as ``None`` (Codex review, PR #719)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "inner": [{"kind": "DeprecatedAttr"}],
        },
    )
    types = [
        t
        for t in _ClangAstParser(root, set(), set()).parse_types()
        if t.name == "Handle"
    ]
    assert len(types) == 1
    assert types[0].deprecated == ""


def test_parse_types_opaque_redecl_uses_most_recent_deprecated_reason() -> None:
    """`struct [[deprecated]] H; struct [[deprecated("old")]] H;` -- real
    clang's own diagnostic uses the MOST RECENT redeclaration's marker as
    the effective reason (verified empirically: `-Wdeprecated-declarations`
    reports "old", pointing at the second decl, not the bare first one; the
    reverse order reports the bare form). The merge must overwrite with each
    later marker, not keep whichever (bare or messaged) came first (Codex
    review, PR #719)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [{"kind": "DeprecatedAttr"}],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "inner": [{"kind": "DeprecatedAttr", "message": "old"}],
        },
    )
    types = [
        t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "H"
    ]
    assert len(types) == 1
    assert types[0].deprecated == "old"

    # Reversed order: the later (now bare) marker wins instead.
    root2 = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [{"kind": "DeprecatedAttr", "message": "old"}],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "H",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "inner": [{"kind": "DeprecatedAttr"}],
        },
    )
    types2 = [
        t for t in _ClangAstParser(root2, set(), set()).parse_types() if t.name == "H"
    ]
    assert len(types2) == 1
    assert types2[0].deprecated == ""


def test_parse_types_opaque_redecl_keeps_public_location_over_private() -> None:
    """A public `struct Handle;` compatibly redeclared with a different class
    key in a private header (`class Handle;`) must keep its PUBLIC
    source_location -- kind canonicalization (class/struct collapsed to one
    fixed spelling, PR #719 follow-up) must not also drag the private
    redecl's location along with it, or a genuinely public handle would
    silently read as PRIVATE_HEADER downstream (Codex review, PR #719)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "class",
            "loc": {"file": "src/internal.h", "line": 2},
        },
    )
    parser = _ClangAstParser(root, set(), set(), public_header_paths=["include/foo.h"])
    types = [t for t in parser.parse_types() if t.name == "Handle"]
    assert len(types) == 1
    assert types[0].kind == "struct"  # canonicalized to a fixed spelling
    assert types[0].source_location == "include/foo.h:1"

    # Reversed declaration order: the public one still wins the location.
    root2 = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "class",
            "loc": {"file": "src/internal.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Handle",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
        },
    )
    parser2 = _ClangAstParser(
        root2, set(), set(), public_header_paths=["include/foo.h"]
    )
    types2 = [t for t in parser2.parse_types() if t.name == "Handle"]
    assert len(types2) == 1
    assert types2[0].kind == "struct"
    assert types2[0].source_location == "include/foo.h:2"


def test_parse_types_definition_kind_not_overridden_by_opaque_redecl() -> None:
    """`class Foo;` (unchanged) followed by a definition changing from
    `class Foo { ... };` to `struct Foo { ... };` -- the opaque forward
    decl's own canonicalized `opaque_kinds` entry must NOT also apply to
    the surviving COMPLETE record, or a real kind change on the definition
    itself would be silently hidden (Codex review, PR #719)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 1},
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "completeDefinition": True,
            "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
        },
    )
    types = [
        t for t in _ClangAstParser(root, set(), set()).parse_types() if t.name == "Foo"
    ]
    assert len(types) == 1
    assert types[0].is_opaque is False
    assert types[0].kind == "struct"  # the definition's own kind, not "class"


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


def test_qualtype_strips_anonymous_type_location_from_field_and_param_types() -> None:
    """A field/param whose declared type is itself a lambda closure (e.g. a
    class-template specialization instantiated with a lambda argument,
    ``Guard<decltype([]{})>``) has clang print its `type.qualType` as
    ``"(lambda at <path>:<line>:<col>)"`` -- verified against real Clang 18
    output (see `_qualtype`'s own docstring). Left unstripped, that embeds an
    absolute, checkout-dependent path into `TypeField.type`/`Param.type`,
    which would manufacture a spurious finding across two checkouts of the
    identical, unchanged declaration.
    """
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Guard",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "fn",
                    "type": {"qualType": "(lambda at /a/foo.h:4:29)"},
                },
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "take_lambda",
            "loc": {"file": "include/foo.h", "line": 5},
            "mangledName": "take_lambda",
            "type": {"qualType": "void ((lambda at /a/foo.h:4:29))"},
            "inner": [
                {
                    "kind": "ParmVarDecl",
                    "name": "f",
                    "type": {"qualType": "(lambda at /a/foo.h:4:29)"},
                },
            ],
        },
    )
    (guard,) = _ClangAstParser(root, {"take_lambda"}, set()).parse_types()
    (fn,) = guard.fields
    # See strip_anonymous_type_location's docstring: line/column and the
    # declaring header's own basename are kept as discriminators -- only the
    # absolute, checkout-dependent directory prefix is stripped.
    assert fn.type == "(lambda:foo.h:4:29)"
    assert "/a/foo.h" not in fn.type

    (take_lambda,) = _ClangAstParser(root, {"take_lambda"}, set()).parse_functions()
    assert "/a/foo.h" not in take_lambda.params[0].type
    assert take_lambda.params[0].type == "(lambda:foo.h:4:29)"


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


# ── G31 Phase C: CastXML schema-completeness audit -- direct-clang backend ──


def test_clang_deprecated_message_three_states() -> None:
    from abicheck.dumper_clang import _clang_deprecated_message

    not_deprecated = {"kind": "FunctionDecl", "name": "f", "inner": []}
    bare = {
        "kind": "FunctionDecl",
        "name": "f",
        "inner": [{"kind": "DeprecatedAttr"}],
    }
    messaged = {
        "kind": "FunctionDecl",
        "name": "f",
        "inner": [{"kind": "DeprecatedAttr", "message": "use g instead"}],
    }
    assert _clang_deprecated_message(not_deprecated) is None
    assert _clang_deprecated_message(bare) == ""
    assert _clang_deprecated_message(messaged) == "use g instead"


def test_clang_record_type_traits_three_states() -> None:
    from abicheck.dumper_clang import _clang_record_type_traits

    no_definition_data = {"kind": "RecordDecl", "name": "PlainC"}
    both_true = {
        "kind": "CXXRecordDecl",
        "name": "Pod",
        "definitionData": {"isStandardLayout": True, "isTriviallyCopyable": True},
    }
    both_false = {
        "kind": "CXXRecordDecl",
        "name": "NotStdLayout",
        # Confirmed against real clang -ast-dump=json: a trait that does NOT
        # hold has its key entirely absent, never present as a literal false.
        "definitionData": {},
    }
    assert _clang_record_type_traits(no_definition_data) == (None, None)
    assert _clang_record_type_traits(both_true) == (True, True)
    assert _clang_record_type_traits(both_false) == (False, False)


def test_parse_types_populates_standard_layout_and_trivially_copyable() -> None:
    # Wires _clang_record_type_traits into parse_types -- previously these two
    # RecordType fields were never populated by the direct-clang backend at
    # all, leaving the already-built STANDARD_LAYOUT_LOST/
    # TRIVIALLY_COPYABLE_LOST detectors permanently dead on that backend
    # (verified end-to-end against a real compiled example before wiring
    # this up; see docs/contribute/plans/g31-header-graph-default-on-followup.md).
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Pod",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "definitionData": {"isStandardLayout": True, "isTriviallyCopyable": True},
            "inner": [
                {"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}},
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "NotStdLayout",
            "tagUsed": "class",
            "loc": {"file": "include/foo.h", "line": 10},
            "completeDefinition": True,
            "definitionData": {"isTriviallyCopyable": True},
            "inner": [
                {"kind": "FieldDecl", "name": "b", "type": {"qualType": "int"}},
            ],
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    assert types["Pod"].is_standard_layout is True
    assert types["Pod"].is_trivially_copyable is True
    assert types["NotStdLayout"].is_standard_layout is False
    assert types["NotStdLayout"].is_trivially_copyable is True


def test_clang_method_is_override() -> None:
    from abicheck.dumper_clang import _clang_method_is_override

    not_override = {"kind": "CXXMethodDecl", "name": "f", "virtual": True}
    is_override = {
        "kind": "CXXMethodDecl",
        "name": "f",
        "inner": [{"kind": "OverrideAttr"}],
    }
    assert _clang_method_is_override(not_override) is False
    assert _clang_method_is_override(is_override) is True


def test_clang_record_is_abstract() -> None:
    from abicheck.dumper_clang import _clang_record_is_abstract

    no_definition_data = {"kind": "RecordDecl", "name": "PlainC"}
    abstract = {
        "kind": "CXXRecordDecl",
        "name": "Abstract1",
        "definitionData": {"isAbstract": True},
    }
    # Confirmed against real clang -ast-dump=json: a trait that does NOT
    # hold has its key entirely absent, never present as a literal false.
    concrete = {
        "kind": "CXXRecordDecl",
        "name": "Concrete1",
        "definitionData": {},
    }
    assert _clang_record_is_abstract(no_definition_data) is None
    assert _clang_record_is_abstract(abstract) is True
    assert _clang_record_is_abstract(concrete) is False


def test_parse_functions_populates_is_override() -> None:
    # G31 Phase C backend audit: previously Function.is_override was never
    # populated by the direct-clang backend at all, leaving the already-built
    # FUNC_OVERRIDE_SPECIFIER_ADDED/REMOVED detector permanently dead on that
    # backend for any cross-producer or clang-vs-clang comparison (verified
    # end-to-end against a real compiled example before wiring this up).
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Base",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "CXXMethodDecl",
                    "name": "f",
                    "mangledName": "_ZN4Base1fEv",
                    "type": {"qualType": "void ()"},
                    "virtual": True,
                },
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Derived",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 5},
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "CXXMethodDecl",
                    "name": "f",
                    "mangledName": "_ZN7Derived1fEv",
                    "type": {"qualType": "void ()"},
                    "inner": [{"kind": "OverrideAttr"}],
                },
                {
                    "kind": "CXXConstructorDecl",
                    "name": "Derived",
                    "mangledName": "_ZN7DerivedC1Ev",
                    "type": {"qualType": "void ()"},
                },
            ],
        },
    )
    funcs = {
        f.mangled: f for f in _ClangAstParser(root, set(), set()).parse_functions()
    }
    assert funcs["_ZN4Base1fEv"].is_override is False
    assert funcs["_ZN7Derived1fEv"].is_override is True
    # A constructor can never carry `override` -- None (not applicable),
    # not a computed False, matching castxml's own tri-state convention.
    assert funcs["_ZN7DerivedC1Ev"].is_override is None


def test_parse_types_populates_is_abstract() -> None:
    # G31 Phase C backend audit: previously RecordType.is_abstract was never
    # populated by the direct-clang backend at all, leaving the already-built
    # TYPE_BECAME_ABSTRACT detector permanently dead on that backend for any
    # cross-producer or clang-vs-clang comparison (verified end-to-end
    # against a real compiled example before wiring this up).
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Abstract1",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "definitionData": {"isAbstract": True},
            "inner": [],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Concrete1",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 5},
            "completeDefinition": True,
            "definitionData": {},
            "inner": [],
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}
    assert types["Abstract1"].is_abstract is True
    assert types["Concrete1"].is_abstract is False


def test_parse_types_and_enums_populate_deprecated() -> None:
    # Mirrors dumper_castxml's own deprecated wiring -- the direct-clang
    # backend previously left Function/Variable/TypeField/RecordType/
    # EnumType.deprecated at None unconditionally (undocumented gap; the
    # model.py field comment called it "castxml-only as of this field's
    # introduction").
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {"kind": "DeprecatedAttr", "message": "old widget"},
                {
                    "kind": "FieldDecl",
                    "name": "legacy",
                    "type": {"qualType": "int"},
                    "inner": [{"kind": "DeprecatedAttr"}],
                },
            ],
        },
        {
            "kind": "EnumDecl",
            "name": "Mode",
            "loc": {"file": "include/foo.h", "line": 20},
            "scopedEnumTag": "class",
            "inner": [
                {"kind": "DeprecatedAttr", "message": "use NewMode"},
                {"kind": "EnumConstantDecl", "name": "A"},
            ],
        },
    )
    parser = _ClangAstParser(root, set(), set())
    types = {t.name: t for t in parser.parse_types()}
    enums = {e.name: e for e in parser.parse_enums()}
    assert types["Widget"].deprecated == "old widget"
    assert types["Widget"].fields[0].deprecated == ""
    assert enums["Mode"].deprecated == "use NewMode"
    assert enums["Mode"].is_scoped is True


def test_parse_types_populates_field_default_initializer() -> None:
    """G31 Phase C's last direct-clang fact-completeness gap:
    TypeField.default (the default member initializer) previously stayed
    None unconditionally on this backend -- verified against real
    Clang 18 ``-ast-dump=json`` output before wiring this up (see
    dumper_clang_expr._field_initializer_value's docstring for the exact shapes
    below)."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                # int bf : 3; -- a bitfield width with NO initializer. The
                # width is nested as a ConstantExpr child, structurally
                # identical to how an initializer would be nested, but
                # hasInClassInitializer is absent -- must NOT be read as a
                # default value of "3".
                {
                    "kind": "FieldDecl",
                    "name": "bf",
                    "type": {"qualType": "int"},
                    "isBitfield": True,
                    "inner": [{"kind": "ConstantExpr", "value": "3"}],
                },
                # int bfi : 3 = 2; -- a bitfield WITH an initializer. The
                # width comes first, the initializer second; _init_expr's
                # "last non-Decl/Attr/Comment child" rule must pick the
                # initializer, not the width.
                {
                    "kind": "FieldDecl",
                    "name": "bfi",
                    "type": {"qualType": "int"},
                    "isBitfield": True,
                    "hasInClassInitializer": True,
                    "inner": [
                        {"kind": "ConstantExpr", "value": "3"},
                        {"kind": "IntegerLiteral", "value": "2"},
                    ],
                },
                # [[deprecated("old")]] int d = 7; -- initializer followed
                # by a trailing DeprecatedAttr (real clang ordering); the
                # attr must not be mistaken for the initializer either.
                {
                    "kind": "FieldDecl",
                    "name": "d",
                    "type": {"qualType": "int"},
                    "hasInClassInitializer": True,
                    "inner": [
                        {"kind": "IntegerLiteral", "value": "7"},
                        {"kind": "DeprecatedAttr", "message": "old"},
                    ],
                },
                # int plain; -- no initializer, no bitfield.
                {
                    "kind": "FieldDecl",
                    "name": "plain",
                    "type": {"qualType": "int"},
                },
            ],
        },
    )
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    fields = {f.name: f for f in t.fields}
    assert fields["bf"].default is None
    assert fields["bfi"].default == "2"
    assert fields["d"].default == "7"
    assert fields["plain"].default is None


def _decl_ref_initializer(field_name: str, referenced_name: str) -> dict:
    """A synthetic ``FieldDecl`` shaped like ``int <field_name> =
    <referenced_name>;`` -- the real Clang 18 ``DeclRefExpr``/
    ``referencedDecl`` nesting confirmed in
    ``dumper_clang._canonical_expr``'s own docstring."""
    return {
        "kind": "FieldDecl",
        "name": field_name,
        "type": {"qualType": "int"},
        "hasInClassInitializer": True,
        "inner": [
            {
                "kind": "ImplicitCastExpr",
                "type": {"qualType": "int"},
                "castKind": "LValueToRValue",
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "type": {"qualType": "const int"},
                        "referencedDecl": {
                            "kind": "VarDecl",
                            "name": referenced_name,
                            "type": {"qualType": "const int"},
                        },
                    }
                ],
            }
        ],
    }


def test_field_default_fingerprint_distinguishes_referenced_declarations() -> None:
    """Codex review, PR #687, fresh evidence: ``_canonical_expr`` dropped
    ``DeclRefExpr``'s ``referencedDecl`` sibling entirely, so ``int x =
    DEFAULT_A;`` and ``int x = DEFAULT_B;`` (or two different function
    calls) fingerprinted identically -- a real
    ``FIELD_DEFAULT_INITIALIZER_CHANGED`` (and, since ``Param.default``
    shares the same ``_canonical_expr``/``_expr_fingerprint`` helper, a real
    ``PARAM_DEFAULT_VALUE_CHANGED``) would go completely undetected on this
    backend whenever both initializers were declaration references."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                _decl_ref_initializer("a", "DEFAULT_A"),
                _decl_ref_initializer("b", "DEFAULT_B"),
                _decl_ref_initializer("a_again", "DEFAULT_A"),
            ],
        },
    )
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    fields = {f.name: f for f in t.fields}

    assert fields["a"].default is not None
    assert fields["a"].default != fields["b"].default
    # The SAME referenced declaration still fingerprints identically --
    # this fix must not turn every decl-ref initializer into a fresh,
    # nondeterministic value.
    assert fields["a"].default == fields["a_again"].default


def _namespaced_var_decl(namespace: str, var_name: str, decl_id: str) -> dict:
    return {
        "kind": "NamespaceDecl",
        "name": namespace,
        "inner": [
            {
                "kind": "VarDecl",
                "id": decl_id,
                "name": var_name,
                "type": {"qualType": "const int"},
            }
        ],
    }


def _decl_ref_initializer_by_id(field_name: str, referenced_id: str) -> dict:
    """Same shape as ``_decl_ref_initializer``, but the ``referencedDecl``
    stub's ``id`` is caller-supplied (so it can be made to resolve, via
    ``_index_decl_id_qualified_names``, to a real namespaced declaration
    planted elsewhere in the same synthetic AST) rather than sharing
    ``referenced_name`` as both the bare stub name AND the lookup key --
    two DIFFERENT namespaced declarations sharing the same bare leaf name
    is exactly the case this test targets."""
    return {
        "kind": "FieldDecl",
        "name": field_name,
        "type": {"qualType": "int"},
        "hasInClassInitializer": True,
        "inner": [
            {
                "kind": "ImplicitCastExpr",
                "type": {"qualType": "int"},
                "castKind": "LValueToRValue",
                "inner": [
                    {
                        "kind": "DeclRefExpr",
                        "type": {"qualType": "const int"},
                        "referencedDecl": {
                            "kind": "VarDecl",
                            "name": "VALUE",
                            "type": {"qualType": "const int"},
                            "id": referenced_id,
                        },
                    }
                ],
            }
        ],
    }


def test_field_default_fingerprint_distinguishes_namespaced_same_name_referents() -> (
    None
):
    """Codex review, PR #687, second round, fresh evidence: a
    ``referencedDecl`` stub carries only a BARE, unqualified name -- real
    Clang 17/18 output confirmed ``a::VALUE`` and ``b::VALUE`` share the
    byte-identical stub ``{"kind": "VarDecl", "name": "VALUE", ...}``, so
    ``int x = a::VALUE;`` vs ``int x = b::VALUE;`` still fingerprinted
    identically after the first round's fix (which only distinguished
    referenced declarations by bare name/kind/type, not by scope). The
    fix resolves each reference's ``id`` through
    ``_index_decl_id_qualified_names``'s namespace-qualified index instead."""
    root = _tu(
        _namespaced_var_decl("a", "VALUE", "0x1"),
        _namespaced_var_decl("b", "VALUE", "0x2"),
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                _decl_ref_initializer_by_id("x", "0x1"),
                _decl_ref_initializer_by_id("y", "0x2"),
                _decl_ref_initializer_by_id("x_again", "0x1"),
            ],
        },
    )
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    fields = {f.name: f for f in t.fields}

    assert fields["x"].default is not None
    assert fields["x"].default != fields["y"].default
    # The SAME referenced declaration (by id) still fingerprints
    # identically, even though this fix resolves scope through it.
    assert fields["x"].default == fields["x_again"].default


def test_id_index_falls_back_to_bare_name_for_unresolved_reference() -> None:
    """A referencedDecl id absent from the index (e.g. a builtin, or a
    declaration genuinely outside this AST root) must not crash or drop
    the fingerprint entirely -- it falls back to the bare-name/kind/type
    identity the first round's fix already established."""
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [_decl_ref_initializer_by_id("x", "0xDEADBEEF")],
        },
    )
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    assert t.fields[0].default is not None


def test_id_index_not_built_for_field_without_initializer() -> None:
    """Codex review, PR #687, fresh evidence: ``self._id_index()`` used to be
    a plain function-call ARGUMENT to ``_field_initializer_value(child,
    self._id_index())`` in ``_make_field``, so Python evaluated it eagerly
    regardless of whether ``child`` even has an initializer -- the first
    field processed in nearly every direct-clang dump paid the one-time
    whole-AST index-build walk for nothing. Gated on
    ``hasInClassInitializer`` first, matching the sibling ``Param.default``
    call site's own short-circuiting ternary."""
    import abicheck.dumper_clang as dumper_clang_module

    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                {"kind": "FieldDecl", "name": "plain", "type": {"qualType": "int"}},
            ],
        },
    )
    parser = _ClangAstParser(root, set(), set())
    calls: list[int] = []
    orig = dumper_clang_module._index_decl_id_qualified_names

    def _spy(*args: object, **kwargs: object) -> dict[str, str]:
        calls.append(1)
        return orig(*args, **kwargs)  # type: ignore[arg-type]

    dumper_clang_module._index_decl_id_qualified_names = _spy  # type: ignore[assignment]
    try:
        (t,) = parser.parse_types()
    finally:
        dumper_clang_module._index_decl_id_qualified_names = orig

    assert t.fields[0].default is None
    assert calls == []


def test_field_default_fingerprint_distinguishes_template_specializations() -> None:
    """Codex review, PR #687, second round, fresh evidence: distinct
    specializations of the same template (``A<int>`` vs. ``A<long>``) are
    separate ``ClassTemplateSpecializationDecl`` nodes sharing the bare
    primary-template name ``"A"`` -- verified against real Clang 17 output
    that the node itself carries no template-argument spelling at all, and
    that kind is absent from ``_SCOPE_NODE_KINDS``. Without the fix, both
    specializations' ``VALUE`` members indexed to the identical bare
    ``"VALUE"`` (not even qualified by ``"A"``), so ``int x = A<int>::VALUE;``
    vs. ``int x = A<long>::VALUE;`` fingerprinted identically."""
    root = _tu(
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x1",
                    "name": "VALUE",
                    "mangledName": "_ZN1AIiE5VALUEE",
                    "type": {"qualType": "const int"},
                },
            ],
        },
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x2",
                    "name": "VALUE",
                    "mangledName": "_ZN1AIlE5VALUEE",
                    "type": {"qualType": "const int"},
                },
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "S1",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [_decl_ref_initializer_by_id("x", "0x1")],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "S2",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 2},
            "completeDefinition": True,
            "inner": [_decl_ref_initializer_by_id("x", "0x2")],
        },
    )
    types = {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}

    assert types["S1"].fields[0].default is not None
    assert types["S1"].fields[0].default != types["S2"].fields[0].default


def test_field_default_fingerprint_stable_across_unrelated_member_insertion() -> None:
    """Codex review, PR #687, third round, fresh evidence: end-to-end
    version of the order-independence guarantee. `S1.x = A<int>::VALUE`
    must fingerprint identically whether or not an unrelated `AAA` member
    was inserted earlier in the SAME specialization -- the earlier
    "first mangled child" disambiguator broke this (adding `AAA` ahead of
    `VALUE` changed the computed disambiguator from `VALUE`'s own mangled
    name to `AAA`'s), producing a false `FIELD_DEFAULT_INITIALIZER_CHANGED`
    purely from an unrelated addition."""

    def _snap(with_aaa: bool):
        members = []
        if with_aaa:
            members.append(
                {
                    "kind": "VarDecl",
                    "id": "0xAAA",
                    "name": "AAA",
                    "mangledName": "_ZN1AIiE3AAAE",
                    "type": {"qualType": "const int"},
                }
            )
        members.append(
            {
                "kind": "VarDecl",
                "id": "0x1",
                "name": "VALUE",
                "mangledName": "_ZN1AIiE5VALUEE",
                "type": {"qualType": "const int"},
            }
        )
        root = _tu(
            {
                "kind": "ClassTemplateSpecializationDecl",
                "name": "A",
                "completeDefinition": True,
                "inner": members,
            },
            {
                "kind": "CXXRecordDecl",
                "name": "S1",
                "tagUsed": "struct",
                "loc": {"file": "include/foo.h", "line": 1},
                "completeDefinition": True,
                "inner": [_decl_ref_initializer_by_id("x", "0x1")],
            },
        )
        (t,) = _ClangAstParser(root, set(), set()).parse_types()
        return t.fields[0].default

    before = _snap(with_aaa=False)
    after = _snap(with_aaa=True)
    assert before is not None
    assert before == after


def test_field_default_fingerprint_distinguishes_sizeof_operand_type() -> None:
    """Codex review, PR #687, third round, fresh evidence: a
    ``UnaryExprOrTypeTraitExpr`` (``sizeof``/``alignof``/...) stores its
    TYPE operand exclusively in ``argType`` -- its own ``type`` key is just
    the trait's result type (always ``unsigned long`` for ``sizeof``,
    identical regardless of operand) and ``name`` only says which trait, not
    what it was applied to. Verified against real Clang 18 output that
    ``sizeof(int)`` and ``sizeof(long long)`` produced byte-identical nodes
    apart from ``argType`` before this fix."""

    def _sizeof_field(field_name: str, arg_qual_type: str) -> dict:
        return {
            "kind": "FieldDecl",
            "name": field_name,
            "type": {"qualType": "unsigned long"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "UnaryExprOrTypeTraitExpr",
                    "type": {"qualType": "unsigned long"},
                    "name": "sizeof",
                    "argType": {"qualType": arg_qual_type},
                }
            ],
        }

    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "S",
            "tagUsed": "struct",
            "loc": {"file": "include/foo.h", "line": 1},
            "completeDefinition": True,
            "inner": [
                _sizeof_field("a", "int"),
                _sizeof_field("b", "long long"),
                _sizeof_field("a_again", "int"),
            ],
        },
    )
    (t,) = _ClangAstParser(root, set(), set()).parse_types()
    fields = {f.name: f for f in t.fields}

    assert fields["a"].default is not None
    assert fields["a"].default != fields["b"].default
    assert fields["a"].default == fields["a_again"].default


def test_normalize_qual_type_strips_anonymous_type_location() -> None:
    from abicheck.dumper_clang import _normalize_qual_type

    assert _normalize_qual_type("(unnamed enum at t.hpp:1:1)") == "(unnamed enum)"
    assert (
        _normalize_qual_type("union (unnamed union at t.hpp:2:5)")
        == "union (unnamed union)"
    )
    assert (
        _normalize_qual_type("struct (unnamed struct at /abs/other/path/t.hpp:9:1)")
        == "struct (unnamed struct)"
    )
    # A named type (the overwhelming common case) passes through unchanged.
    assert _normalize_qual_type("const int") == "const int"
    assert _normalize_qual_type("std::vector<int>") == "std::vector<int>"


def test_normalize_qual_type_strips_lambda_location() -> None:
    """Codex review, PR #687, fourth round, fresh evidence: a lambda's
    closure type is spelled ``"(lambda at <file>:<line>:<col>)"`` -- also
    location-bearing, but a DIFFERENT shape than the "(unnamed <kind> at
    ...)" pattern the anonymous-tag fix above handles (no "unnamed" word).
    Verified against real Clang 17 output that this spelling appears
    repeatedly throughout a ``std::function<...>``-wrapped lambda's whole
    template instantiation chain, so every occurrence must be normalized,
    not just the first/outermost."""
    from abicheck.dumper_clang import _normalize_qual_type

    assert _normalize_qual_type("(lambda at t.hpp:2:38)") == "(lambda)"
    assert _normalize_qual_type("S::(lambda at t.hpp:2:38)") == "S::(lambda)"
    assert _normalize_qual_type("S::(lambda at t.hpp:2:38) &&") == "S::(lambda) &&"
    # Repeated occurrences within one complex template spelling.
    assert (
        _normalize_qual_type(
            "std::is_nothrow_constructible<(lambda at t.hpp:2:38), "
            "(lambda at t.hpp:2:38)>"
        )
        == "std::is_nothrow_constructible<(lambda), (lambda)>"
    )


def test_field_default_fingerprint_stable_across_lambda_location() -> None:
    """End-to-end version of the lambda-location test above: a field
    initialized with ``std::function<void()> f = []{};`` must fingerprint
    identically whether parsed from ``t.hpp:2:38`` or a completely
    different checkout path/line."""

    def _lambda_field(loc: str) -> dict:
        return {
            "kind": "FieldDecl",
            "name": "f",
            "type": {"qualType": "std::function<void ()>"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "CXXConstructExpr",
                    "type": {"qualType": "std::function<void ()>"},
                    "inner": [
                        {
                            "kind": "LambdaExpr",
                            "type": {"qualType": f"(lambda at {loc})"},
                        }
                    ],
                }
            ],
        }

    def _snap(loc: str):
        root = _tu(
            {
                "kind": "CXXRecordDecl",
                "name": "S",
                "tagUsed": "struct",
                "loc": {"file": "include/foo.h", "line": 1},
                "completeDefinition": True,
                "inner": [_lambda_field(loc)],
            },
        )
        (t,) = _ClangAstParser(root, set(), set()).parse_types()
        return t.fields[0].default

    same_checkout = _snap("t.hpp:2:38")
    different_checkout = _snap("/different/tree/t.hpp:9:99")
    assert same_checkout is not None
    assert same_checkout == different_checkout


def test_field_default_fingerprint_stable_across_anonymous_type_location() -> None:
    """Codex review, PR #687, fourth round, fresh evidence: clang spells an
    anonymous enum/struct/union's type as ``"(unnamed <kind> at <file>:
    <line>:<col>)"`` -- an absolute path and line embedded directly in the
    ``qualType`` string. Verified against real Clang 17 output that parsing
    identical source from two different checkout paths, or merely inserting
    a blank line before an anonymous ``enum { VALUE = 3 };``, produced two
    DIFFERENT ``TypeField.default`` fingerprints for an unrelated, unchanged
    initializer referencing it -- a false
    ``FIELD_DEFAULT_INITIALIZER_CHANGED`` purely from where/how the source
    was checked out."""

    def _anon_enum_ref_field(loc: str) -> dict:
        return {
            "kind": "FieldDecl",
            "name": "x",
            "type": {"qualType": "int"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "DeclRefExpr",
                    "type": {"qualType": f"(unnamed enum at {loc})"},
                    "referencedDecl": {
                        "kind": "EnumConstantDecl",
                        "name": "VALUE",
                        "type": {"qualType": f"(unnamed enum at {loc})"},
                    },
                }
            ],
        }

    def _snap(loc: str):
        root = _tu(
            {
                "kind": "CXXRecordDecl",
                "name": "S",
                "tagUsed": "struct",
                "loc": {"file": "include/foo.h", "line": 1},
                "completeDefinition": True,
                "inner": [_anon_enum_ref_field(loc)],
            },
        )
        (t,) = _ClangAstParser(root, set(), set()).parse_types()
        return t.fields[0].default

    same_checkout = _snap("t.hpp:1:1")
    different_checkout = _snap("/different/checkout/path/t.hpp:2:1")
    assert same_checkout is not None
    assert same_checkout == different_checkout


def test_index_disambiguates_specializations_via_scope_key() -> None:
    """Codex review, PR #687, third round, fresh evidence: an earlier
    version derived the specialization disambiguator from whichever direct
    child happened to be first with a mangled name -- unstable to an
    unrelated member being inserted earlier in the same specialization
    (see the dedicated order-independence test below). Fixed via
    ``_specialization_scope_key``, which extracts only the SCOPE portion of
    a representative member's mangled name (``itanium_scope_components``,
    dropping the trailing leaf/member component) -- identical regardless of
    which member of the SAME specialization contributed it."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x1",
                    "name": "VALUE",
                    "mangledName": "_ZN1AIiE5VALUEE",
                }
            ],
        },
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x2",
                    "name": "VALUE",
                    "mangledName": "_ZN1AIlE5VALUEE",
                }
            ],
        },
        # No mangled child at all -- falls back to the bare (collision-
        # prone) name rather than crashing or fabricating a value.
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "B",
            "inner": [{"kind": "VarDecl", "id": "0x3", "name": "VALUE"}],
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0x1"] == "A#AIiE::VALUE"
    assert index["0x2"] == "A#AIlE::VALUE"
    assert index["0x1"] != index["0x2"]
    assert index["0x3"] == "B::VALUE"


def test_index_specialization_key_stable_across_member_insertion() -> None:
    """The order-independence guarantee directly, at the index level: two
    members of the SAME specialization (`VALUE` and a newly-inserted `AAA`
    ahead of it) must resolve to the identical scope-key prefix, so adding
    one never perturbs the other's already-computed identity."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0xAAA",
                    "name": "AAA",
                    "mangledName": "_ZN1AIiE3AAAE",
                },
                {
                    "kind": "VarDecl",
                    "id": "0x1",
                    "name": "VALUE",
                    "mangledName": "_ZN1AIiE5VALUEE",
                },
            ],
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0xAAA"] == "A#AIiE::AAA"
    assert index["0x1"] == "A#AIiE::VALUE"


def test_specialization_scope_key_documents_msvc_gap() -> None:
    """Codex review, PR #687, third round, fresh evidence: a real
    ``clang-cl``/``--target=*-windows-msvc`` snapshot mangles a
    specialization member as e.g. ``?VALUE@?$A@H@@2HB``, which neither
    ``itanium_scope_components`` NOR ``diff_cxx_rules.msvc_scope_components``
    can parse (both return ``None`` -- the latter by its own documented
    design, since a specialization's class-name component always starts
    with the ``?$`` template marker it deliberately rejects). This is a
    KNOWN, deliberately deferred gap (see ``_specialization_scope_key``'s
    own docstring) -- this test pins the current, honest behavior (falls
    back to the bare, collision-prone name; never crashes or fabricates a
    value) so a future fix has a concrete regression to flip, and so this
    doesn't silently regress further (e.g. start raising)."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x1",
                    "name": "VALUE",
                    "mangledName": "?VALUE@?$A@H@@2HB",
                },
            ],
        },
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "VarDecl",
                    "id": "0x2",
                    "name": "VALUE",
                    "mangledName": "?VALUE@?$A@J@@2JB",
                },
            ],
        },
    )
    index = _index_decl_id_qualified_names(root)

    # Both fall back to the same bare, undisambiguated key -- the documented
    # collision, not a crash.
    assert index["0x1"] == "A::VALUE"
    assert index["0x2"] == "A::VALUE"


def test_index_disambiguates_function_template_specializations() -> None:
    """Codex review, PR #687, fourth round, fresh evidence: ``f<int>()``/
    ``f<long>()`` both report a ``FunctionDecl`` named ``"f"`` -- no
    template-argument spelling on the node itself, verified against real
    Clang 17 output -- so both used to resolve to the identical index entry
    ``"f"``. Each specialization DOES carry its own build-stable
    ``mangledName`` directly (unlike a class specialization), so it's used
    outright instead of the bare name."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "FunctionDecl",
            "id": "0x1",
            "name": "f",
            "mangledName": "_Z1fIiEiv",
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
        },
        {
            "kind": "FunctionDecl",
            "id": "0x2",
            "name": "f",
            "mangledName": "_Z1fIlEiv",
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "TemplateArgument", "type": {"qualType": "long"}}],
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0x1"] == "_Z1fIiEiv"
    assert index["0x2"] == "_Z1fIlEiv"
    assert index["0x1"] != index["0x2"]


def test_index_does_not_use_mangled_name_for_a_plain_function() -> None:
    """The disambiguation above must stay scoped to an actual template
    specialization (a ``FunctionDecl`` with a ``TemplateArgument`` child) --
    an ordinary, non-template ``FunctionDecl`` has no such child and keeps
    the existing scope+name index value."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "FunctionDecl",
            "id": "0x1",
            "name": "plain",
            "mangledName": "_Z5plainv",
            "type": {"qualType": "void ()"},
            "inner": [],
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0x1"] == "plain"


def test_index_disambiguates_variable_template_specializations() -> None:
    """Same collision, one level down, for a VARIABLE template
    specialization: ``V<int>``/``V<long>`` both report a
    ``VarTemplateSpecializationDecl`` named ``"V"``, verified against real
    Clang 17 output."""
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        {
            "kind": "VarTemplateSpecializationDecl",
            "id": "0x1",
            "name": "V",
            "mangledName": "_Z1VIiE",
            "type": {"qualType": "const int"},
        },
        {
            "kind": "VarTemplateSpecializationDecl",
            "id": "0x2",
            "name": "V",
            "mangledName": "_Z1VIlE",
            "type": {"qualType": "const int"},
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0x1"] == "_Z1VIiE"
    assert index["0x2"] == "_Z1VIlE"
    assert index["0x1"] != index["0x2"]


def test_field_default_changes_across_function_template_specializations() -> None:
    """End-to-end: a field default referencing distinct specializations of
    the same function template must fingerprint distinctly (Codex review,
    PR #687, fourth round, fresh evidence)."""
    from abicheck.dumper_clang_expr import (
        _field_initializer_value,
        _index_decl_id_qualified_names,
    )

    def _cfg(mangled: str) -> dict:
        return {
            "kind": "CXXRecordDecl",
            "name": "Cfg",
            "tagUsed": "struct",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "x",
                    "type": {"qualType": "int"},
                    "hasInClassInitializer": True,
                    "inner": [
                        {
                            "kind": "CallExpr",
                            "inner": [
                                {
                                    "kind": "ImplicitCastExpr",
                                    "inner": [
                                        {
                                            "kind": "DeclRefExpr",
                                            "referencedDecl": {
                                                "kind": "FunctionDecl",
                                                "name": "f",
                                                "id": "0x1",
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    root_int = _tu(
        {
            "kind": "FunctionDecl",
            "id": "0x1",
            "name": "f",
            "mangledName": "_Z1fIiEiv",
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
        },
        _cfg("_Z1fIiEiv"),
    )
    root_long = _tu(
        {
            "kind": "FunctionDecl",
            "id": "0x1",
            "name": "f",
            "mangledName": "_Z1fIlEiv",
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "TemplateArgument", "type": {"qualType": "long"}}],
        },
        _cfg("_Z1fIlEiv"),
    )
    field_int = root_int["inner"][1]["inner"][0]
    field_long = root_long["inner"][1]["inner"][0]
    idx_int = _index_decl_id_qualified_names(root_int)
    idx_long = _index_decl_id_qualified_names(root_long)

    value_int = _field_initializer_value(field_int, lambda: idx_int)
    value_long = _field_initializer_value(field_long, lambda: idx_long)
    assert value_int is not None
    assert value_long is not None
    assert value_int != value_long


def test_dependent_scope_initializer_documents_known_gap() -> None:
    """Codex review, PR #687, fourth round, fresh evidence: a template-
    DEPENDENT operand inside an uninstantiated class template pattern (e.g.
    ``T::template value<1>()`` vs. ``T::template value<2>()``) is a
    ``DependentScopeDeclRefExpr`` that clang's ``-ast-dump=json`` prints
    with no ``name``, no ``value``, and no children at all -- verified
    against real Clang 17 output that BOTH instances reduce to the
    byte-identical ``{"kind": "DependentScopeDeclRefExpr", "type":
    "<dependent type>"}``. This is a KNOWN, deliberately deferred gap (see
    ``_canonical_expr``'s own docstring) -- this test pins the current,
    honest collision behavior (never crashes or fabricates a value) so a
    future fix has a concrete regression to flip, and so this doesn't
    silently regress further."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _dependent_field(kind: str) -> dict:
        return {
            "kind": "FieldDecl",
            "name": "x",
            "type": {"qualType": "int"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "CallExpr",
                    "type": {"qualType": "<dependent type>"},
                    "inner": [
                        {
                            "kind": kind,
                            "type": {"qualType": "<dependent type>"},
                        }
                    ],
                }
            ],
        }

    field_1 = _dependent_field("DependentScopeDeclRefExpr")
    field_2 = _dependent_field("DependentScopeDeclRefExpr")

    value_1 = _field_initializer_value(field_1)
    value_2 = _field_initializer_value(field_2)

    # The documented collision, not a crash -- pinned so a real fix (which
    # needs source-text or a different clang dump mode, per the docstring)
    # has a concrete regression to flip.
    assert value_1 is not None
    assert value_1 == value_2


def test_offsetof_initializer_documents_known_gap() -> None:
    """Codex review, PR #687, fresh evidence: ``offsetof(A, x)`` vs.
    ``offsetof(A, y)`` -- clang's ``OffsetOfExpr`` node, verified against
    real ``clang++ --std=c++17 -Xclang -ast-dump=json`` output (Clang 18),
    carries no ``inner`` children and no key identifying which member the
    offset walk selects; both reduce to the byte-identical
    ``{"kind": "OffsetOfExpr", "type": "unsigned long"}`` real-world AST
    node shape reproduced verbatim here. This is a KNOWN, deliberately
    deferred gap (see ``_canonical_expr``'s own docstring) -- pinned so a
    future fix has a concrete regression to flip, and so this doesn't
    silently regress further."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _offsetof_field() -> dict:
        # Real clang output has no reference to the selected member (`x` vs.
        # `y`) anywhere on this node -- there is nothing here to vary
        # between the two call sites below, which is exactly the gap.
        return {
            "kind": "FieldDecl",
            "name": "v",
            "type": {"qualType": "const unsigned long"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "OffsetOfExpr",
                    "type": {"qualType": "unsigned long"},
                    "valueCategory": "prvalue",
                }
            ],
        }

    field_x = _offsetof_field()  # stands in for `offsetof(A, x)`
    field_y = _offsetof_field()  # stands in for `offsetof(A, y)`

    value_x = _field_initializer_value(field_x)
    value_y = _field_initializer_value(field_y)

    assert value_x is not None
    assert value_x == value_y


def test_new_expr_allocation_semantics_distinguish_fingerprints() -> None:
    """Codex review, PR #687, fresh evidence: ``S* p = new S();`` vs.
    ``S* p = ::new S();`` when ``S`` declares its own ``operator new`` --
    ``CXXNewExpr``'s ``isGlobal``/``operatorNewDecl``/``operatorDeleteDecl``
    keys are what distinguish a class-member allocation from a global one,
    and were previously dropped by ``_canonical_expr``'s whitelist entirely.
    Node shapes below are trimmed verbatim from real
    ``clang++ --std=c++17 -Xclang -ast-dump=json`` output (Clang 18) for
    exactly this pair -- the member form has no ``isGlobal`` key and an
    ``operatorNewDecl.kind`` of ``CXXMethodDecl``; the global form has
    ``isGlobal: true`` and ``FunctionDecl``."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _new_field(*, is_global: bool) -> dict:
        new_expr: dict = {
            "kind": "CXXNewExpr",
            "type": {"qualType": "S *"},
            "valueCategory": "prvalue",
            "initStyle": "call",
            "operatorNewDecl": {
                "kind": "FunctionDecl" if is_global else "CXXMethodDecl",
                "name": "operator new",
                "type": {"qualType": "void *(unsigned long)"},
            },
            "operatorDeleteDecl": {
                "kind": "FunctionDecl",
                "name": "operator delete",
                "type": {"qualType": "void (void *) noexcept"},
            },
            "inner": [
                {
                    "kind": "CXXConstructExpr",
                    "type": {"qualType": "S"},
                    "valueCategory": "prvalue",
                    "ctorType": {"qualType": "void () noexcept"},
                }
            ],
        }
        if is_global:
            new_expr["isGlobal"] = True
        return {
            "kind": "FieldDecl",
            "name": "p",
            "type": {"qualType": "S *"},
            "hasInClassInitializer": True,
            "inner": [new_expr],
        }

    field_member = _new_field(is_global=False)
    field_global = _new_field(is_global=True)

    value_member = _field_initializer_value(field_member)
    value_global = _field_initializer_value(field_global)

    assert value_member is not None
    assert value_global is not None
    assert value_member != value_global


def test_new_expr_init_style_distinguishes_fingerprints() -> None:
    """Codex review, PR #687, fresh evidence: ``S* p = new S;`` (default-
    initialization) vs. ``S* p = new S();`` (value-initialization, which
    zero-initializes ``S``'s scalar members) previously fingerprinted
    identically -- ``CXXNewExpr.initStyle`` (absent vs. ``"call"``) and the
    nested ``CXXConstructExpr``'s ``zeroing`` (absent vs. ``true``) were both
    dropped by ``_canonical_expr``'s whitelist. Node shapes below are
    trimmed verbatim from real ``clang++ --std=c++17 -Xclang
    -ast-dump=json`` output (Clang 18) for exactly this pair."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _new_field(*, value_init: bool) -> dict:
        construct_expr: dict = {
            "kind": "CXXConstructExpr",
            "type": {"qualType": "S"},
            "valueCategory": "prvalue",
            "ctorType": {"qualType": "void () noexcept"},
        }
        new_expr: dict = {
            "kind": "CXXNewExpr",
            "type": {"qualType": "S *"},
            "valueCategory": "prvalue",
            "operatorNewDecl": {
                "kind": "FunctionDecl",
                "name": "operator new",
                "type": {"qualType": "void *(unsigned long)"},
            },
            "operatorDeleteDecl": {
                "kind": "FunctionDecl",
                "name": "operator delete",
                "type": {"qualType": "void (void *) noexcept"},
            },
        }
        if value_init:
            new_expr["initStyle"] = "call"
            construct_expr["zeroing"] = True
        new_expr["inner"] = [construct_expr]
        return {
            "kind": "FieldDecl",
            "name": "p",
            "type": {"qualType": "S *"},
            "hasInClassInitializer": True,
            "inner": [new_expr],
        }

    field_default_init = _new_field(value_init=False)
    field_value_init = _new_field(value_init=True)

    value_default = _field_initializer_value(field_default_init)
    value_value = _field_initializer_value(field_value_init)

    assert value_default is not None
    assert value_value is not None
    assert value_default != value_value


def test_typeid_operand_distinguishes_fingerprints() -> None:
    """Codex review, PR #687, fresh evidence: ``typeid(int)`` vs.
    ``typeid(long)`` -- clang's ``CXXTypeidExpr`` node applied to a TYPE
    operand -- previously fingerprinted identically. Confirmed against real
    Clang 18 output: this node is childless and its own ``type`` is always
    the fixed result type ``"const std::type_info"`` regardless of operand;
    the real operand lives exclusively in a separate ``typeArg`` key
    (a different key than sizeof/alignof's ``argType``), which
    ``_canonical_expr`` didn't read. Node shape below is trimmed verbatim
    from real ``clang++ --std=c++17 -Xclang -ast-dump=json`` output for
    exactly this pair."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _typeid_field(type_arg: str) -> dict:
        return {
            "kind": "FieldDecl",
            "name": "t",
            "type": {"qualType": "const std::type_info *"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "UnaryOperator",
                    "type": {"qualType": "const std::type_info *"},
                    "valueCategory": "prvalue",
                    "isPostfix": False,
                    "opcode": "&",
                    "canOverflow": False,
                    "inner": [
                        {
                            "kind": "CXXTypeidExpr",
                            "type": {"qualType": "const std::type_info"},
                            "valueCategory": "lvalue",
                            "typeArg": {"qualType": type_arg},
                        }
                    ],
                }
            ],
        }

    field_int = _typeid_field("int")
    field_long = _typeid_field("long")

    value_int = _field_initializer_value(field_int)
    value_long = _field_initializer_value(field_long)

    assert value_int is not None
    assert value_long is not None
    assert value_int != value_long


def test_postfix_increment_distinguishes_fingerprints() -> None:
    """Codex review, PR #687, fresh evidence: ``(g++, 1)`` vs. ``(++g, 1)``
    -- a discarded increment inside a larger expression whose overall VALUE
    is the same literal ``1`` in both forms, so only the SIDE EFFECT
    differs -- previously fingerprinted identically. Confirmed against real
    Clang 17 output: a pre/post increment ``UnaryOperator`` shares the same
    ``opcode`` (``"++"``) for both forms; the only structural distinction is
    a separate ``isPostfix`` boolean key, which ``_canonical_expr`` didn't
    read. Node shape below is trimmed verbatim from real
    ``clang++ --std=c++17 -Xclang -ast-dump=json`` output for exactly this
    pair."""
    from abicheck.dumper_clang_expr import _field_initializer_value

    def _increment_field(is_postfix: bool) -> dict:
        return {
            "kind": "FieldDecl",
            "name": "x",
            "type": {"qualType": "int"},
            "hasInClassInitializer": True,
            "inner": [
                {
                    "kind": "ParenExpr",
                    "type": {"qualType": "int"},
                    "valueCategory": "prvalue",
                    "inner": [
                        {
                            "kind": "BinaryOperator",
                            "type": {"qualType": "int"},
                            "valueCategory": "prvalue",
                            "opcode": ",",
                            "inner": [
                                {
                                    "kind": "UnaryOperator",
                                    "type": {"qualType": "int"},
                                    "valueCategory": "prvalue"
                                    if is_postfix
                                    else "lvalue",
                                    "isPostfix": is_postfix,
                                    "opcode": "++",
                                    "inner": [
                                        {
                                            "kind": "DeclRefExpr",
                                            "type": {"qualType": "int"},
                                            "valueCategory": "lvalue",
                                            "referencedDecl": {
                                                "kind": "VarDecl",
                                                "name": "g",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "kind": "IntegerLiteral",
                                    "type": {"qualType": "int"},
                                    "valueCategory": "prvalue",
                                    "value": "1",
                                },
                            ],
                        }
                    ],
                }
            ],
        }

    field_postfix = _increment_field(True)
    field_prefix = _increment_field(False)

    value_postfix = _field_initializer_value(field_postfix)
    value_prefix = _field_initializer_value(field_prefix)

    assert value_postfix is not None
    assert value_prefix is not None
    assert value_postfix != value_prefix


def test_parse_enums_is_scoped_false_for_plain_enum() -> None:
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "PlainMode",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [{"kind": "EnumConstantDecl", "name": "A"}],
        },
    )
    enums = {e.name: e for e in _ClangAstParser(root, set(), set()).parse_enums()}
    assert enums["PlainMode"].is_scoped is False
    assert enums["PlainMode"].deprecated is None


def test_parse_functions_and_variables_populate_deprecated() -> None:
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "old_fn",
            "mangledName": "_Z6old_fnv",
            "type": {"qualType": "void ()"},
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [{"kind": "DeprecatedAttr"}],
        },
        {
            "kind": "FunctionDecl",
            "name": "new_fn",
            "mangledName": "_Z6new_fnv",
            "type": {"qualType": "void ()"},
            "loc": {"file": "include/foo.h", "line": 2},
            "inner": [],
        },
        {
            "kind": "VarDecl",
            "name": "old_var",
            "mangledName": "old_var",
            "type": {"qualType": "int"},
            "loc": {"file": "include/foo.h", "line": 3},
            "inner": [{"kind": "DeprecatedAttr", "message": "unused"}],
        },
    )
    parser = _ClangAstParser(root, set(), set())
    funcs = {f.name: f for f in parser.parse_functions()}
    variables = {v.name: v for v in parser.parse_variables()}
    assert funcs["old_fn"].deprecated == ""
    assert funcs["new_fn"].deprecated is None
    assert variables["old_var"].deprecated == "unused"


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


def test_parse_typedefs_qualified_disambiguates_bare_name_collision() -> None:
    # G31 Phase C: two unrelated member typedefs sharing the bare spelling
    # "value_type" in different classes previously collided in the plain
    # `typedefs` dict (whichever was parsed last silently won) -- the
    # qualified twin must carry both, distinctly.
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Foo",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "name": "value_type",
                    "loc": {"line": 2},
                    "type": {"qualType": "int"},
                },
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Bar",
            "loc": {"file": "include/foo.h", "line": 3},
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "name": "value_type",
                    "loc": {"line": 4},
                    "type": {"qualType": "double"},
                },
            ],
        },
    )
    parser = _ClangAstParser(root, set(), set())
    # The plain (bare-keyed) dict still collides -- unchanged, pre-existing
    # behavior, whichever declaration was visited last wins.
    assert parser.parse_typedefs() == {"value_type": "double"}
    # The qualified twin disambiguates both.
    assert parser.parse_typedefs_qualified() == {
        "Foo::value_type": "int",
        "Bar::value_type": "double",
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


@pytest.mark.parametrize(
    "cc_bin,tokens,expected",
    [
        ("icpx", ["-fsycl"], True),
        ("icx", ["-fsycl", "-DFOO=1"], True),
        ("/opt/oneapi/bin/dpcpp", ["-fsycl"], True),
        ("dpcpp-cl", ["-fsycl"], True),
        # No -fsycl at all: nothing to pin.
        ("icpx", ["-DFOO=1"], False),
        ("icpx", [], False),
        # Caller already pinned a single pass explicitly: don't second-guess it.
        ("icpx", ["-fsycl", "-fsycl-host-only"], False),
        ("icpx", ["-fsycl", "-fsycl-device-only"], False),
        # A flag that merely starts with "-fsycl" is not the bare enabling
        # flag itself (exact-token match, not a prefix/substring check).
        ("icpx", ["-fsycl-device-only"], False),
        # Stock clang accepts a bare -fsycl as a single pass and does not
        # recognize -fsycl-host-only at all (Codex review, PR #643) -- must
        # never be gated on for a non-Intel clang-family binary.
        ("clang", ["-fsycl"], False),
        ("clang++", ["-fsycl"], False),
        ("/usr/bin/clang-18", ["-fsycl"], False),
        # The clang driver applies -fsycl/-fno-sycl last-flag-wins like any
        # other toggle (confirmed via `clang++ -fsycl -fno-sycl -###`, which
        # emits a single ordinary host -cc1, no device pass). A later
        # -fno-sycl means SYCL is not actually enabled, so nothing needs
        # pinning to a single pass (Codex review, PR #643, round 5).
        ("icpx", ["-fsycl", "-fno-sycl"], False),
        ("icpx", ["-fno-sycl", "-fsycl"], True),
        ("icpx", ["-fsycl", "-fno-sycl", "-fsycl"], True),
        ("icpx", ["-fno-sycl"], False),
        # Intel's legacy "dpcpp"/"dpcpp-cl" driver names imply SYCL is
        # already on even with no -fsycl token at all (Codex review, PR
        # #643, round 8) -- an explicit -fno-sycl still overrides that
        # default. icx/icpx are NOT in this default-on set: they need an
        # explicit -fsycl, same as stock clang.
        ("dpcpp", [], True),
        ("dpcpp-cl", [], True),
        ("/opt/oneapi/bin/dpcpp", [], True),
        ("DPCPP", [], True),  # case-insensitive
        ("dpcpp.exe", [], True),  # extension stripped, still matches
        ("dpcpp", ["-DFOO=1"], True),
        ("dpcpp", ["-fno-sycl"], False),
        ("dpcpp", ["-fno-sycl", "-fsycl"], True),
        ("icx", [], False),
    ],
)
def test_needs_sycl_host_only(cc_bin: str, tokens: list[str], expected: bool) -> None:
    assert _needs_sycl_host_only(cc_bin, tokens) is expected


@pytest.mark.parametrize(
    "cc_bin,expected",
    [
        ("dpcpp", True),
        ("dpcpp-cl", True),
        ("/opt/oneapi/bin/dpcpp", True),
        ("DPCPP", True),
        ("dpcpp.exe", True),
        ("icx", False),
        ("icpx", False),
        ("clang", False),
        ("clang++", False),
        ("gcc", False),
    ],
)
def test_dpcpp_defaults_sycl_on(cc_bin: str, expected: bool) -> None:
    assert _dpcpp_defaults_sycl_on(cc_bin) is expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        ([], False),
        (["-fsycl"], False),
        (["-fno-sycl"], True),
        (["-fsycl", "-fno-sycl"], True),
        # Last-flag-wins: a later -fsycl re-enables it (Codex review, P2).
        (["-fno-sycl", "-fsycl"], False),
        (["-fsycl", "-fno-sycl", "-fsycl"], False),
        (["-DFOO=1"], False),
    ],
)
def test_user_explicitly_disabled_sycl(tokens: list[str], expected: bool) -> None:
    assert _user_explicitly_disabled_sycl(tokens) is expected


@pytest.mark.parametrize(
    "clang_bin,frontend_context,tokens,expected",
    [
        ("icpx", "host", [], True),
        ("icpx", "host", ["-fno-sycl"], False),
        ("clang++", "host", [], False),
        ("icpx", "device", [], True),
    ],
)
def test_resolve_dpcpp_multi_context(
    clang_bin: str, frontend_context: str, tokens: list[str], expected: bool
) -> None:
    assert (
        dumper_clang._resolve_dpcpp_multi_context(
            clang_bin, frontend_context, None, tuple(tokens)
        )
        is expected
    )


def test_resolve_dpcpp_multi_context_device_on_plain_clang_raises() -> None:
    from abicheck.errors import AstContextMissingError

    with pytest.raises(AstContextMissingError, match="DPC\\+\\+-capable"):
        dumper_clang._resolve_dpcpp_multi_context("clang++", "device", None, ())


def test_resolve_dpcpp_multi_context_device_with_fno_sycl_raises() -> None:
    from abicheck.errors import AstContextMissingError

    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        dumper_clang._resolve_dpcpp_multi_context(
            "icpx", "device", None, ("-fno-sycl",)
        )


def test_build_clang_header_command_dpcpp_defaults_sycl_on(tmp_path: Path) -> None:
    # Codex review (PR #643, round 8): "dpcpp" implies SYCL is already on
    # even with no -fsycl token at all, so it must still get
    # -fsycl-host-only pinned -- otherwise the double-JSON-document bug
    # this whole fix targets reappears for this driver name.
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command("dpcpp", "gnu", [], agg, force_cpp=True)
    assert "-fsycl-host-only" in cmd


def test_build_clang_header_command_dpcpp_multi_context_skips_host_only(
    tmp_path: Path,
) -> None:
    """PR #643 / ADR-050 D5 interaction: a legacy "dpcpp" driver name defaults
    to SYCL-on (test_build_clang_header_command_dpcpp_defaults_sycl_on above),
    so _needs_sycl_host_only would normally pin -fsycl-host-only -- but an
    explicit dpcpp_multi_context request (Phase D host/device selection)
    wants BOTH passes and must never have that collapsed back to one by
    this default-case behavior."""
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "dpcpp", "gnu", [], agg, force_cpp=True, dpcpp_multi_context=True
    )
    assert "-fsycl-host-only" not in cmd
    assert "-fsycl" in cmd
    assert "-v" in cmd


def test_build_clang_header_command_dpcpp_explicit_fno_sycl_overrides_default(
    tmp_path: Path,
) -> None:
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "dpcpp", "gnu", [], agg, force_cpp=True, gcc_options="-fno-sycl"
    )
    assert "-fsycl-host-only" not in cmd


def test_build_clang_header_command_appends_sycl_host_only(tmp_path: Path) -> None:
    # A bare -fsycl makes a SYCL-capable driver (icpx/dpcpp) run a device pass
    # *and* a host pass, each emitting a full -ast-dump=json document to the
    # same stdout stream -- the second document breaks json.load() with
    # "Extra data". -fsycl-host-only pins the compile to the single pass that
    # actually matches what links into the scanned binary.
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "icpx", "gnu", [], agg, force_cpp=True, gcc_options="-fsycl -DFOO=1"
    )
    assert "-fsycl-host-only" in cmd
    assert cmd.count("-fsycl-host-only") == 1


def test_build_clang_header_command_respects_explicit_sycl_device_only(
    tmp_path: Path,
) -> None:
    # An explicit --gcc-options choice must never be second-guessed.
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "icpx",
        "gnu",
        [],
        agg,
        force_cpp=True,
        gcc_options="-fsycl -fsycl-device-only",
    )
    assert "-fsycl-host-only" not in cmd


def test_build_clang_header_command_respects_later_fno_sycl(tmp_path: Path) -> None:
    # A later -fno-sycl disables SYCL under the driver's last-flag-wins
    # semantics -- appending -fsycl-host-only anyway would tack a SYCL-only
    # selector onto a non-SYCL compile (Codex review, PR #643, round 5).
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "icpx", "gnu", [], agg, force_cpp=True, gcc_options="-fsycl -fno-sycl"
    )
    assert "-fsycl-host-only" not in cmd


def test_build_clang_header_command_without_sycl_unaffected(tmp_path: Path) -> None:
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command("clang++", "gnu", [], agg, force_cpp=True)
    assert "-fsycl-host-only" not in cmd


def test_build_clang_header_command_stock_clang_sycl_not_gated(tmp_path: Path) -> None:
    # Codex review (PR #643): stock upstream clang accepts a bare -fsycl and
    # parses it as a single pass fine, but does not recognize
    # -fsycl-host-only at all -- it hard-rejects it with "unknown argument".
    # Appending it here would turn a working --compiler clang + -fsycl parse
    # into a guaranteed failure, so this must stay gated on the resolved
    # binary actually being an Intel oneAPI driver (icx/icpx/dpcpp[-cl]).
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "clang++", "gnu", [], agg, force_cpp=True, gcc_options="-fsycl"
    )
    assert "-fsycl-host-only" not in cmd


def test_build_clang_header_command_sycl_via_gcc_option_tokens(tmp_path: Path) -> None:
    # The repeatable --compiler-option path (gcc_option_tokens) must be checked the
    # same way as the shlex-split --gcc-options string.
    agg = tmp_path / "agg.hpp"
    cmd = _build_clang_header_command(
        "icpx", "gnu", [], agg, force_cpp=True, gcc_option_tokens=("-fsycl",)
    )
    assert "-fsycl-host-only" in cmd


def test_parse_clang_ast_result_rejects_concatenated_json_documents(
    tmp_path: Path,
) -> None:
    # A SYCL device pass + host pass (or any other flag that makes clang run
    # more than one -cc1 pass for one compile) each write a complete
    # -ast-dump=json document to the same stdout stream, back-to-back with no
    # separator -- json.load() parses only the first and raises on the rest.
    # The error must name the likely cause instead of a bare byte offset.
    ast_path = tmp_path / "ast.json"
    doc = '{"kind": "TranslationUnitDecl", "inner": []}'
    ast_path.write_text(doc + doc, encoding="utf-8")
    result = _fake_proc(stdout="", stderr="", returncode=0)
    with pytest.raises(SnapshotError, match="more than one JSON document"):
        _parse_clang_ast_result(result, tmp_path / "cache.json", ast_path)


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
        root, _resolved_kind, _ = _clang_header_dump([header], [], lang="c")
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
        root, _resolved_kind, _ = _clang_header_dump(
            [header], []
        )  # lang=None → auto-detect
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
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False))
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


def test_header_ast_parser_clang_branch_records_resolved_lang_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: the third ``_clang_header_dump`` return
    value (the actual, possibly post-self-heal language mode) must be
    stamped onto ``ast_toolchain["resolved_lang_mode"]`` so the provenance
    probe uses it instead of re-deriving a stale guess."""
    ast = _tu({"kind": "TranslationUnitDecl", "inner": []})
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: (ast, None, True))
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
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert parser._abicheck_ast_toolchain["resolved_lang_mode"] == "c++"


def test_header_ast_parser_passes_explicit_target_to_clang_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast = _tu(
        {
            "kind": "FunctionDecl", "name": "api",
            "loc": {"file": "api.h", "line": 1}, "mangledName": "api",
            "type": {"qualType": "void () __attribute__((sysv_abi))"},
        }
    )
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False))
    parser = _header_ast_parser(
        [], [], backend="clang", compiler="c++", gcc_path=None, gcc_prefix=None,
        gcc_options="--target=x86_64-pc-linux-gnu", sysroot=None, nostdinc=False,
        lang=None, exported_dynamic={"api"}, exported_static=set(),
        public_header_paths=[], public_dir_paths=[],
    )

    assert [f.contract_attributes for f in parser.parse_functions()] == [[]]


def test_header_ast_parser_clang_branch_records_abi_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-050 D1 (Codex review, PR #624 follow-up): abi_dialect must reach
    # ast_toolchain, not be silently discarded, for the clang backend too.
    ast = _tu(
        {
            "kind": "FunctionDecl",
            "name": "foo",
            "loc": {"file": "foo.h", "line": 1},
            "mangledName": "_Z3foov",
            "type": {"qualType": "void ()"},
        }
    )
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False))
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
    assert parser._abicheck_ast_toolchain["abi_dialect"] == "gnu"


def test_header_ast_parser_clang_branch_records_msvc_abi_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast = _tu(
        {
            "kind": "FunctionDecl",
            "name": "foo",
            "loc": {"file": "foo.h", "line": 1},
            "mangledName": "_Z3foov",
            "type": {"qualType": "void ()"},
        }
    )
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: (ast, None, False))
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/llvm/bin/cl.exe"
    )
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
    assert parser._abicheck_ast_toolchain["abi_dialect"] == "msvc"


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


def test_header_ast_parser_castxml_branch_records_abi_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-050 D1 (Codex review, PR #624 follow-up): the castxml branch's
    # _resolve_compiler_binary(...) already resolves (host_cc, cc_id) -- this
    # pins that cc_id reaches ast_toolchain["abi_dialect"] instead of being
    # discarded as `_`.
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
    assert parser._abicheck_ast_toolchain["abi_dialect"] == "gnu"


def test_header_ast_parser_castxml_branch_records_the_force_cpp_aware_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: for a C-mode dump under the default
    ``compiler="c++"``, ``_castxml_dump`` internally remaps to ``"cc"``
    before resolving ``cc_bin`` -- ``ast_toolchain["compiler_selected"]``
    must record *that* resolved identity, not a re-derivation from the
    caller's original, unresolved ``"c++"`` (which would record g++'s
    identity even though castxml actually ran gcc)."""
    sentinel = object()
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)

    def fake_castxml_dump(*a, **k):
        # Mirrors _castxml_dump's own force_cpp=False remap: "c++" -> "cc".
        out = k.get("_selected_meta_out")
        if out is not None:
            out.append(("cc", False))
        return sentinel

    monkeypatch.setattr(dumper, "_castxml_dump", fake_castxml_dump)
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
        lang="c",
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    cc_selected = parser._abicheck_ast_toolchain["compiler_selected"]
    gxx_selected = dumper._tool_identity_metadata("g++").get("selected")
    # "cc" must have actually been resolved, and it must differ from what a
    # re-derivation from the unresolved "c++" would have recorded.
    assert cc_selected != gxx_selected


def test_header_ast_parser_castxml_branch_records_msvc_abi_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: sentinel)
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: parser_sentinel)
    parser = _header_ast_parser(
        [],
        [],
        backend="castxml",
        compiler="cl",
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
    assert parser._abicheck_ast_toolchain["abi_dialect"] == "msvc"


def test_header_ast_parser_stamps_castxml_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    parser_cls = dumper._CastxmlParser
    parser_sentinel = parser_cls.__new__(parser_cls)
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: sentinel)
    monkeypatch.setattr(dumper, "_CastxmlParser", lambda *a, **k: parser_sentinel)
    monkeypatch.setattr(
        dumper_toolchain,
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
        dumper_toolchain,
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

    root, resolved_kind, _ = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert resolved_kind is None
    assert cache.exists()  # result was cached
    # Second call hits the cache — subprocess is not invoked again.
    root2, resolved_kind2, _ = _clang_header_dump([header], [])
    assert root2 == root
    assert resolved_kind2 is None
    assert calls["n"] == 1


def test_header_graph_attach_reuses_primary_snapshot_ast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """G31 Phase C: the always-on header-only graph's own AST pass
    (``service._attach_header_graph``) must reuse the identical clang AST the
    main ``--ast-frontend clang`` snapshot pass already parsed for the same
    resolved headers/includes -- not a second, independent
    ``clang -ast-dump=json`` invocation. This is the fix for the perf
    regression G31 Phase A introduced by making the graph unconditional
    (docs/contribute/plans/g31-header-graph-default-on-followup.md): before
    this fix, every ``--ast-frontend clang`` dump paid a full second
    disk-read/JSON-reparse (or a second subprocess, on a cold cache) purely
    to build the graph.
    """
    from abicheck.model import AbiSnapshot
    from abicheck.service import _attach_header_graph

    header = tmp_path / "api.h"
    header.write_text("int f(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        calls["n"] += 1
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    # Step 1: the main snapshot pass's own parser construction (mirrors
    # dumper._header_ast_parser's `_run_clang()` inner call for
    # `--ast-frontend clang`) -- the exact path a real ELF/PE/Mach-O
    # clang-frontend dump takes. Wrapped in ast_memoize_scope() -- the same
    # scope service.run_dump's own format branches open around their
    # primary dump call -- since without it _clang_header_dump's memoize
    # resolves to False and step 2 below would only prove disk-cache reuse
    # (pre-existing behaviour), not the new in-process AST memo this test
    # is meant to guard (CodeRabbit review).
    with dumper_cache.ast_memoize_scope():
        parser = dumper._header_ast_parser(
            [header],
            [],
            backend="clang",
            compiler="c++",
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            gcc_option_tokens=(),
            sysroot=None,
            nostdinc=False,
            lang="c",
            exported_dynamic=set(),
            exported_static=set(),
            public_header_paths=[],
            public_dir_paths=[],
        )
        # The memo slot now holds the parsed AST, primed for step 2's pop --
        # proving this test actually exercises the memo handoff, not just
        # the pre-existing disk-cache dedup.
        assert dumper_cache._ast_memo_slot.get() is not None
    assert calls["n"] == 1

    # Step 2: the post-dump header-graph attach step, over the identical
    # resolved header/includes -- must not invoke the clang pass again.
    snap = AbiSnapshot(
        library="lib", version="1.0", functions=list(parser.parse_functions())
    )
    _attach_header_graph(
        snap,
        header_graph=True,
        header_graph_includes=False,
        headers=[header],
        includes=[],
        lang="c",
        compile=None,
        public_headers=None,
        public_header_dirs=None,
    )
    assert calls["n"] == 1  # unchanged: reused the in-process AST memo


def test_clang_header_dump_skips_memo_write_on_toolchain_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CodeRabbit review: a compiler-identity change mid-parse already skips
    the on-disk cache write (``cache_write=identities_stable``) -- the
    in-process memo write must honor the same safeguard, or a result
    produced by the replacement toolchain could be served back under the
    original tool's cache key on a later same-process call."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    # First call (frontend_identity) returns "v1"; every later call (the
    # post-parse identities_stable check, plus this same probe on the second
    # _clang_header_dump call below) returns "v2" -- simulating the
    # toolchain changing under us mid-execution.
    identities = iter(["v1"])
    monkeypatch.setattr(
        dumper, "_tool_identity", lambda *a, **k: next(identities, "v2")
    )
    calls = {"n": 0}

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        calls["n"] += 1
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    _clang_header_dump([header], [])
    assert not cache.exists()  # disk write skipped, as before this PR

    # A second call must NOT hit the memo either -- it should re-invoke the
    # subprocess rather than serve the unstable-toolchain result back.
    _clang_header_dump([header], [])
    assert calls["n"] == 2


def test_clang_header_dump_memoize_false_never_writes_the_memo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: ``_attach_header_graph`` is the *final* consumer of its
    own ``_clang_header_dump`` call when the primary snapshot pass used
    castxml (never wrote a memo entry of its own) -- passing
    ``memoize=False`` there must mean neither a disk-cache hit nor a fresh
    parse populates the in-process memo, since no further same-process
    reader will ever pop it. Otherwise a long-lived process (the MCP
    server, ``scan`` over many libraries) accumulates dead, potentially
    multi-GB entries with no consumer."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    # Fresh-parse path: memoize=False must leave this thread's slot unset.
    _clang_header_dump([header], [], memoize=False)
    assert cache.exists()  # the disk cache is still populated, as always
    assert dumper_cache._ast_memo_slot.get() is None

    # Disk-cache-hit path: a second memoize=False call reads the file this
    # test just warmed on disk, and must still leave the slot unset.
    _clang_header_dump([header], [], memoize=False)
    assert dumper_cache._ast_memo_slot.get() is None


def test_ast_memo_slot_overwrites_previous_pending_value() -> None:
    """CodeRabbit/Codex review: the memo is a single-consumption, per-thread
    handoff, not a general keyed cache -- there is at most one legitimate
    pending AST at a time, so a second write before the first is consumed
    simply replaces it (nothing to "evict" across distinct keys, unlike the
    old shared-dict design this superseded)."""
    dumper_cache.store_cached_ast("k1", "clang", {"n": 1})
    dumper_cache.store_cached_ast("k2", "clang", {"n": 2})
    assert dumper_cache._ast_memo_slot.get() == ("clang", "k2", {"n": 2})


def test_clang_header_dump_direct_caller_never_memoizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: a direct ``_clang_header_dump`` caller with no
    ``service.run_dump``-style downstream ``_attach_header_graph`` consumer
    (``appcompat.check_app_compatibility``, a direct Python-API/MCP caller
    selecting the clang backend) must not populate the in-process memo at
    all -- outside ``dumper_cache.ast_memoize_scope()``, ``memoize=None``
    (the default every such caller uses) resolves to ``False``, so the
    entry is dead weight nothing will ever pop."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    assert not dumper_cache.ast_memoize_active()
    _clang_header_dump([header], [])  # no ast_memoize_scope() active
    assert cache.exists()  # the disk cache is still populated, as always
    assert dumper_cache._ast_memo_slot.get() is None


def test_clang_header_dump_memoizes_inside_ast_memoize_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: a caller inside ``service.run_dump``'s
    ``ast_memoize_scope()`` (its primary-dump call) does get the memo
    write, exactly the handoff ``_attach_header_graph`` then consumes."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    with dumper_cache.ast_memoize_scope():
        _clang_header_dump([header], [])
        assert dumper_cache._ast_memo_slot.get() is not None  # written
    # The scope exiting doesn't itself clear the slot -- only a subsequent
    # pop (the header-graph attach step) does; still present right after.
    assert dumper_cache._ast_memo_slot.get() is not None


def test_ast_memoize_scope_cleans_up_its_own_writes_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: a primary dump that successfully parses/memoizes an AST
    but then fails *later* in the same scoped call (e.g. snapshot
    construction raises after the AST parse succeeded) never reaches
    _attach_header_graph to pop that entry -- ast_memoize_scope must clear
    this thread's slot when the scoped operation raises, or it sits set
    for however long this thread lives afterward in a long-lived process."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)

    with pytest.raises(RuntimeError, match="snapshot construction failed"):
        with dumper_cache.ast_memoize_scope():
            _clang_header_dump([header], [])
            assert dumper_cache._ast_memo_slot.get() is not None  # written
            raise RuntimeError("snapshot construction failed")
    assert dumper_cache._ast_memo_slot.get() is None  # cleaned up on the way out


def test_ast_memo_slot_is_per_thread_not_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: ``service.compare``'s default worker
    pool dumps the old/new sides *concurrently* in separate threads, and
    the AST cache key has no side/binary component -- comparing against
    the same ``-H`` header set (the common case) makes both sides compute
    the identical key. A shared dict would let one side's single-
    consumption pop steal (or clobber) the other side's pending AST. Prove
    the per-thread slot design structurally can't: a background thread's
    own write must be invisible to (and unaffected by) the main thread's
    slot, even when both use the exact same (backend, key)."""
    import threading

    dumper_cache.store_cached_ast("shared-key", "clang", {"side": "main"})

    other_saw: dict[str, object] = {}

    def _other_thread() -> None:
        # A fresh thread starts with an empty slot regardless of what the
        # main thread just stored under the identical key.
        other_saw["before"] = dumper_cache._ast_memo_slot.get()
        dumper_cache.store_cached_ast("shared-key", "clang", {"side": "other"})
        other_saw["after"] = dumper_cache._ast_memo_slot.get()

    t = threading.Thread(target=_other_thread)
    t.start()
    t.join()

    assert other_saw["before"] is None
    assert other_saw["after"] == ("clang", "shared-key", {"side": "other"})
    # The main thread's own slot is untouched by the other thread's write
    # under the identical key.
    assert dumper_cache._ast_memo_slot.get() == (
        "clang",
        "shared-key",
        {"side": "main"},
    )


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
    root, resolved_kind, _ = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert resolved_kind is None


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
    # Force the second call past the in-process AST memo (G31 Phase C reuse)
    # onto the on-disk read this test is actually about -- a memo hit would
    # skip json.loads (and the slow-patched cost below) entirely, which is
    # exactly the intended optimization but not what this test exercises.
    dumper_cache._ast_memo_slot.set(None)

    # The disk-cache read (json.loads) now lives in dumper_cache.py, not
    # dumper.py itself (G31 Phase C AST reuse split the read into
    # dumper_cache.load_cached_ast) -- patch the module that actually calls it.
    real_json_loads = dumper_cache.json.loads

    def _slow_loads(text: str) -> Any:
        time.sleep(0.05)
        return real_json_loads(text)

    monkeypatch.setattr(dumper_cache.json, "loads", _slow_loads)
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
    root, _resolved_kind, resolved_force_cpp = _clang_header_dump([header], [])
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert len(cmds) == 2  # one C attempt + one C++ retry
    assert "c" in cmds[0] and cmds[0][cmds[0].index("-x") + 1] == "c"
    assert cmds[1][cmds[1].index("-x") + 1] == "c++"
    # Codex review, fresh evidence: the self-heal's real, post-retry language
    # mode must be reported back, not the pre-retry "c" guess -- this is what
    # the provenance probe (dumper_toolchain._ast_compile_provenance) relies
    # on instead of silently re-deriving a stale answer.
    assert resolved_force_cpp is True


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


# ── ADR-050 D5 (G32 Phase D): SYCL/DPC++ host/device context wiring ─────────


_G32_DPCPP_DIR = Path(__file__).parent / "fixtures" / "g32" / "dpcpp"


def _dpcpp_fixture_texts() -> tuple[str, str]:
    """Real captured (stdout, stderr) pair from ``icpx -fsycl`` (Phase 0's
    fixture, see ``tests/fixtures/g32/README.md``) -- not synthesized."""
    stdout = (_G32_DPCPP_DIR / "ast_dump.json").read_text(encoding="utf-8")
    stderr = (_G32_DPCPP_DIR / "compiler_invocation.log").read_text(encoding="utf-8")
    return stdout, stderr


def test_clang_header_dump_device_context_selects_real_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end wiring: a DPC++-capable clang_bin + frontend_context="device"
    decodes the real two-document capture and selects the spir64 device AST,
    proving sycl_context.py is actually reached from _clang_header_dump, not
    just exercised in isolation (Codex P1 review)."""
    header = tmp_path / "foo.h"
    header.write_text("struct Point { int x, y; };\nint add(int, int);\n")
    stdout_text, stderr_text = _dpcpp_fixture_texts()
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx")
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, stdout_text)
        return _fake_proc(stderr=stderr_text, returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    root, resolved_kind, _ = _clang_header_dump([header], [], frontend_context="device")
    assert resolved_kind == "device"
    assert root["kind"] == "TranslationUnitDecl"


def test_clang_header_dump_host_context_selects_real_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Companion to the device-selection test above: the same DPC++-capable
    invocation with the default frontend_context="host" selects the OTHER
    (x86_64) document from the identical real capture."""
    header = tmp_path / "foo.h"
    header.write_text("struct Point { int x, y; };\nint add(int, int);\n")
    stdout_text, stderr_text = _dpcpp_fixture_texts()
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx")
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, stdout_text)
        return _fake_proc(stderr=stderr_text, returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    root, resolved_kind, _ = _clang_header_dump([header], [])
    assert resolved_kind == "host"
    assert root["kind"] == "TranslationUnitDecl"


def test_clang_header_dump_dpcpp_empty_stream_raises_ast_context_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fallback-gating (ADR-050 D5 acceptance criterion): a DPC++-capable
    invocation whose decoded stream comes back empty (broken toolchain
    invocation, truncated output) must raise AstContextMissingError -- never
    silently degrade to a single-context path, which is reserved for an
    invocation that was never positively identified as DPC++-capable at all."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx")
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        # No document-boundary markers at all -- an empty/malformed stream,
        # not "this wasn't a multi-document invocation."
        _write_stdout_file(kwargs, "")
        return _fake_proc(stderr="", returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with pytest.raises((AstContextMissingError, SnapshotError)):
        _clang_header_dump([header], [], frontend_context="device")


def test_clang_header_dump_fno_sycl_skips_multi_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (P2): an explicit ``-fno-sycl`` in gcc_options on a
    DPC++-capable compiler must not be silently overridden by
    dpcpp_multi_context's unconditional ``-fsycl`` append -- the actual
    clang invocation must not re-enable SYCL, and the decode falls back to
    the ordinary single-document path (resolved_kind is None)."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: tmp_path / "c.json")
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx")
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    captured_cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        captured_cmds.append(cmd)
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    root, resolved_kind, _ = _clang_header_dump([header], [], gcc_options="-fno-sycl")
    assert resolved_kind is None
    assert root["kind"] == "TranslationUnitDecl"
    assert captured_cmds and all("-fsycl" not in cmd for cmd in captured_cmds)


def test_clang_header_dump_device_context_with_fno_sycl_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Companion: requesting --frontend-context device while gcc_options
    explicitly disables SYCL is a contradictory combination that must fail
    fast with AstContextMissingError -- never silently re-enable SYCL to
    honor frontend_context, nor silently ignore -fno-sycl (Codex review,
    P2)."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        _clang_header_dump(
            [header], [], gcc_options="-fno-sycl", frontend_context="device"
        )
    assert calls["n"] == 0  # fails before any subprocess is invoked


def test_clang_header_dump_non_host_on_plain_frontend_raises_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-"host" frontend_context against a plain, non-DPC++-capable
    clang_bin fails immediately with AstContextMissingError, before any
    subprocess is invoked -- a user who explicitly requests "device" on a
    frontend that cannot produce one must get a clear failure, never the
    ordinary single-context host AST silently standing in for it."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        dumper, "_resolve_clang_bin", lambda *a, **k: "/usr/bin/clang++"
    )
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    with pytest.raises(AstContextMissingError):
        _clang_header_dump([header], [], frontend_context="device")
    assert calls["n"] == 0  # never spent a subprocess invocation on it


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


def test_enumerator_aliasing_a_sibling_enumerator_keeps_its_real_value() -> None:
    # `enum { A, B, X = B }`: clang's JSON wraps the DeclRefExpr-to-B
    # initializer in a ConstantExpr that itself carries the folded value
    # ("1"), but the ConstantExpr is also a registered wrapper kind that
    # _unwrap_expr descends straight through into the DeclRefExpr (which has
    # no "value" of its own). Reading only the original node and the fully
    # unwrapped leaf (the pre-fix behavior) misses the intermediate
    # ConstantExpr's value entirely and silently falls back to the wrong,
    # positional auto-increment number -- and everything after it shifts too.
    root = _tu(
        {
            "kind": "EnumDecl",
            "name": "E",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {"kind": "EnumConstantDecl", "name": "A"},
                {"kind": "EnumConstantDecl", "name": "B"},
                {
                    "kind": "EnumConstantDecl",
                    "name": "X",
                    "inner": [
                        {
                            "kind": "ImplicitCastExpr",
                            "inner": [
                                {
                                    "kind": "ConstantExpr",
                                    "value": "1",
                                    "inner": [
                                        {
                                            "kind": "DeclRefExpr",
                                            "referencedDecl": {
                                                "kind": "EnumConstantDecl",
                                                "name": "B",
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
                {"kind": "EnumConstantDecl", "name": "Y"},
            ],
        }
    )
    (enum,) = _ClangAstParser(root, set(), set()).parse_enums()
    # X aliases B's real value (1), not X's own positional index (2); Y then
    # auto-increments from X's real value, not from the wrong one.
    assert [(m.name, m.value) for m in enum.members] == [
        ("A", 0),
        ("B", 1),
        ("X", 1),
        ("Y", 2),
    ]


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
    # ADR-063 Phase 5 (Codex review, PR #982): a resolved friend owner is
    # real evidence -- Fact.PRESENT, not NOT_COLLECTED.
    from abicheck.model import FactStatus

    assert fn.hidden_friend_owner_fact.status == FactStatus.PRESENT
    assert fn.hidden_friend_owner_fact.value == fn.hidden_friend_owner == "Pt"


def test_hidden_friend_owner_fact_not_applicable_for_ordinary_function() -> None:
    """An owner is conceptually inapplicable for a non-friend function -- a
    confirmed non-gap, not missing evidence (Codex review, PR #982)."""
    from abicheck.model import FactStatus

    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "do_thing",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_Z8do_thingv",
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_hidden_friend is False
    assert fn.hidden_friend_owner is None
    assert fn.hidden_friend_owner_fact.status == FactStatus.NOT_APPLICABLE


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
    """Pure-function unit test for the --compiler clang-family classifier,
    directly exercising both the "clang" substring branch and the vendor
    alias-set branch (icx/icpx/dpcpp/dpcpp-cl) in isolation from the larger
    _clang_header_dump/_resolve_clang_bin call chain the tests below cover."""
    assert _is_clang_family_binary(path) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("icx", True),
        ("icpx", True),
        ("dpcpp", True),
        ("dpcpp-cl", True),
        ("/opt/intel/oneapi/compiler/2026.1/bin/icpx", True),
        ("ICPX", True),  # case-insensitive
        ("icpx.exe", True),  # extension stripped, still matches
        # Stock clang is clang-family but NOT the Intel SYCL driver: it does
        # not implement -fsycl-host-only/-fsycl-device-only at all.
        ("clang", False),
        ("clang++", False),
        ("/usr/bin/clang-18", False),
        ("clang-cl.exe", False),
        ("gcc", False),
        ("icc", False),
    ],
)
def test_is_intel_sycl_driver(path: str, expected: bool) -> None:
    assert _is_intel_sycl_driver(path) is expected


def test_clang_header_dump_gcc_path_not_used_as_clang(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A --compiler pointing at g++ must NOT become the clang executable.
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
    # but is not spelled "clang" -- --compiler must still be honored for it
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


def test_index_decl_id_qualified_names() -> None:
    from abicheck.dumper_clang import _index_decl_id_qualified_names

    root = _tu(
        _namespaced_var_decl("a", "VALUE", "0x1"),
        _namespaced_var_decl("b", "VALUE", "0x2"),
        {
            "kind": "CXXRecordDecl",
            "name": "Outer",
            "inner": [
                {"kind": "VarDecl", "id": "0x3", "name": "member"},
                {
                    "kind": "CXXRecordDecl",
                    "name": "Inner",
                    "inner": [{"kind": "VarDecl", "id": "0x4", "name": "member"}],
                },
            ],
        },
        # An anonymous scope (no name) contributes no scope segment, but its
        # own children are still indexed.
        {
            "kind": "LinkageSpecDecl",
            "inner": [{"kind": "VarDecl", "id": "0x5", "name": "global"}],
        },
    )
    index = _index_decl_id_qualified_names(root)

    assert index["0x1"] == "a::VALUE"
    assert index["0x2"] == "b::VALUE"
    assert index["0x3"] == "Outer::member"
    assert index["0x4"] == "Outer::Inner::member"
    assert index["0x5"] == "global"
    # A non-dict node anywhere in the tree is skipped, not a crash.
    assert _index_decl_id_qualified_names({"inner": ["not-a-dict", None]}) == {}


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
    root, _resolved_kind, _ = _clang_header_dump([header], [])
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
    # An explicit GNU --compiler is used verbatim…
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
    """A --compiler icx/icpx/dpcpp/dpcpp-cl is clang-family, not GNU — probing it
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
    # Hermetic/cross flags supplied via --compiler-option must suppress
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


# ---------------------------------------------------------------------------
# Tests: resolve_source_frontend_clang_bin — the shared S2/L4 clang_bin
# resolver used by scan_engine._preprocessor_scan_clang_bin and
# cli_buildsource.embed_build_source's clang_bin (dump/scan L4 replay).
# ---------------------------------------------------------------------------


def test_resolve_source_frontend_clang_bin_no_overrides() -> None:
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert resolve_source_frontend_clang_bin(None, None) == "clang"
    assert (
        resolve_source_frontend_clang_bin(None, None, fallback="clang++") == "clang++"
    )


def test_resolve_source_frontend_clang_bin_honors_clang_family_gcc_path() -> None:
    """A ``--compiler`` pointing at a clang-family binary (e.g. an Intel oneAPI
    ``icpx`` symlink) is used directly for L4/S2 replay, not a plain fallback —
    otherwise a non-default toolchain's real compiler is silently ignored."""
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert resolve_source_frontend_clang_bin("/opt/bin/icpx", None) == "/opt/bin/icpx"


def test_resolve_source_frontend_clang_bin_ignores_non_clang_gcc_path() -> None:
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert (
        resolve_source_frontend_clang_bin("g++", None, fallback="clang++") == "clang++"
    )


def test_resolve_source_frontend_clang_bin_excludes_cl_style_driver() -> None:
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert (
        resolve_source_frontend_clang_bin("clang-cl", None, fallback="clang++")
        == "clang++"
    )


def test_resolve_source_frontend_clang_bin_keeps_cl_style_when_not_excluded() -> None:
    """L4 source-ABI replay (``ClangSourceExtractor``) already detects a CL
    compile unit and re-drives it with ``--driver-mode=cl`` -- unlike the S2
    preprocessor pre-scan, it must not fall back to a plain, non-CL
    ``clang``/``clang++`` for a ``--compiler clang-cl``/``dpcpp-cl`` build,
    or an Intel DPC++/SYCL context parsed at L2 gets replayed through stock
    Clang at L4 and silently loses source-ABI coverage."""
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert (
        resolve_source_frontend_clang_bin(
            "clang-cl", None, fallback="clang++", exclude_cl_style=False
        )
        == "clang-cl"
    )
    assert (
        resolve_source_frontend_clang_bin(
            "/opt/oneapi/bin/dpcpp-cl", None, exclude_cl_style=False
        )
        == "/opt/oneapi/bin/dpcpp-cl"
    )


def test_resolve_source_frontend_clang_bin_excludes_versioned_cl_driver() -> None:
    """LLVM/Debian packaging commonly ships a versioned CL-mode driver
    (``clang-cl-20``) alongside the unversioned name -- the version suffix
    must not hide it from CL-style exclusion, or the S2 pre-scan drives it
    with GNU-only flags (``-dM``/``-M``) it silently ignores instead of
    falling back to plain ``clang++`` (Codex review)."""
    from abicheck.dumper_clang import resolve_source_frontend_clang_bin

    assert (
        resolve_source_frontend_clang_bin("clang-cl-20", None, fallback="clang++")
        == "clang++"
    )
    assert (
        resolve_source_frontend_clang_bin(
            "/usr/bin/clang-cl-20.exe", None, fallback="clang++"
        )
        == "clang++"
    )


def test_resolve_source_frontend_clang_bin_prefix_when_available(monkeypatch) -> None:
    from abicheck import dumper_clang

    monkeypatch.setattr(
        dumper_clang.shutil,
        "which",
        lambda name: name if name.endswith("clang") else None,
    )
    assert (
        dumper_clang.resolve_source_frontend_clang_bin(None, "/opt/x86_64-linux-gnu-")
        == "/opt/x86_64-linux-gnu-clang"
    )


def test_resolve_source_frontend_clang_bin_prefix_missing_falls_back(
    monkeypatch,
) -> None:
    from abicheck import dumper_clang

    monkeypatch.setattr(dumper_clang.shutil, "which", lambda name: None)
    assert (
        dumper_clang.resolve_source_frontend_clang_bin(None, "aarch64-linux-gnu-")
        == "clang"
    )
