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

"""Tests for the ADR-030 phase-5 clang source ABI extractor.

The argv builder and the JSON-AST → SourceAbiTu mapping are pure and tested in
the fast lane; the end-to-end clang run is marked ``integration``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.evidence.build_evidence import CompileUnit
from abicheck.evidence.source_abi import SourceAbiTu
from abicheck.evidence.source_diff import diff_source_abi
from abicheck.evidence.source_extractors import (
    ClangSourceExtractor,
    SourceExtractionError,
    build_clang_command,
    build_clang_macro_command,
    source_abi_from_clang_ast,
)
from abicheck.evidence.source_link import link_source_abi


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++17",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


# -- build_clang_command (pure, D2) ------------------------------------------


def test_build_command_reflects_compile_context() -> None:
    cu = _cu(
        defines={"FOO": "1", "BARE": ""},
        undefines=["NDEBUG"],
        include_paths=["include"],
        system_include_paths=["/opt/sdk/include"],
        sysroot="/sysroot",
        target_triple="aarch64-linux-gnu",
    )
    cmd = build_clang_command(cu, Path("src/foo.cpp"), clang_bin="clang")
    assert cmd[0] == "clang"
    assert "-x" in cmd and cmd[cmd.index("-x") + 1] == "c++"
    assert "-std=c++17" in cmd
    assert "-DFOO=1" in cmd and "-DBARE" in cmd and "-UNDEBUG" in cmd
    assert cmd[cmd.index("-I") + 1] == "include"
    assert cmd[cmd.index("-isystem") + 1] == "/opt/sdk/include"
    assert "--sysroot=/sysroot" in cmd
    assert "--target=aarch64-linux-gnu" in cmd
    # The AST dump invocation and the source operand come last.
    assert "-fsyntax-only" in cmd
    assert cmd[cmd.index("-ast-dump=json")] == "-ast-dump=json"
    assert cmd[-1] == str(Path("src/foo.cpp"))


def test_build_command_c_language() -> None:
    cmd = build_clang_command(_cu(language="C", standard="c11"), Path("a.c"))
    assert cmd[cmd.index("-x") + 1] == "c"
    assert "-std=c11" in cmd


def test_build_command_msvc_driver_mode() -> None:
    cmd = build_clang_command(
        _cu(standard="c++20", defines={"WIN": "1"}, include_paths=["inc"]),
        Path("a.cpp"),
        compiler_binary="clang-cl",
    )
    assert "--driver-mode=cl" in cmd
    assert "/std:c++20" in cmd
    assert "/DWIN=1" in cmd
    assert cmd[cmd.index("/I") + 1] == "inc"
    # No --target in cl mode (clang-cl rejects the GNU spelling here).
    assert not any(a.startswith("--target=") for a in cmd)


def test_build_command_carries_abi_flags_and_unwraps_launcher() -> None:
    cu = _cu(
        argv=["ccache", "clang++", "-c", "foo.cpp"],
        abi_relevant_flags=["-fvisibility=hidden"],
    )
    cmd = build_clang_command(cu, Path("foo.cpp"))
    assert "-fvisibility=hidden" in cmd
    assert "ccache" not in cmd


# -- source_abi_from_clang_ast (pure, D4) ------------------------------------


def _ast() -> dict:
    """A small clang JSON AST root covering each entity kind we extract."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "ns",
                "loc": {"file": "include/foo.h", "line": 1},
                "inner": [
                    {
                        "kind": "FunctionDecl",
                        "name": "add",
                        "loc": {"line": 3},
                        "mangledName": "_ZN2ns3addEii",
                        "type": {"qualType": "int (int, int)"},
                        "inline": True,
                        "inner": [
                            {"kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"}},
                            {
                                "kind": "ParmVarDecl", "name": "y", "type": {"qualType": "int"},
                                "init": "c",
                                "inner": [{"kind": "IntegerLiteral", "value": "1"}],
                            },
                            {
                                "kind": "CompoundStmt",
                                "inner": [{"kind": "ReturnStmt", "inner": [
                                    {"kind": "DeclRefExpr", "name": "x"}]}],
                            },
                        ],
                    },
                    {
                        "kind": "VarDecl", "name": "kMax", "loc": {"line": 9},
                        "constexpr": True, "type": {"qualType": "const int"},
                        "inner": [{"kind": "IntegerLiteral", "value": "42"}],
                    },
                    {
                        "kind": "CXXRecordDecl", "name": "Widget", "loc": {"line": 12},
                        "inner": [{"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}}],
                    },
                    {
                        "kind": "FunctionTemplateDecl", "name": "maxv", "loc": {"line": 20},
                        "inner": [
                            {"kind": "TemplateTypeParmDecl", "name": "T"},
                            {"kind": "FunctionDecl", "name": "maxv",
                             "inner": [{"kind": "CompoundStmt", "inner": []}]},
                        ],
                    },
                ],
            },
            {
                "kind": "FunctionDecl", "name": "priv",
                "loc": {"file": "src/internal.h", "line": 2},
                "type": {"qualType": "void ()"}, "inline": True,
                "inner": [{"kind": "CompoundStmt", "inner": []}],
            },
        ],
    }


def test_ast_mapping_extracts_each_entity_kind() -> None:
    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "target://libfoo")
    assert tu.extractor["name"] == "clang-source"
    funcs = {e.qualified_name: e for e in tu.functions}
    # Default argument captured, keyed by parameter *position* (p1 = 2nd param),
    # not name — a rename keeping the same default is not a change.
    assert funcs["ns::add"].value == "p1=1"
    assert funcs["ns::add"].mangled_name == "_ZN2ns3addEii"
    assert {e.qualified_name for e in tu.inline_bodies} == {"ns::add"}
    assert tu.inline_bodies[0].body_hash.startswith("sha256:")
    assert {e.qualified_name: e.value for e in tu.constexpr_values} == {"ns::kMax": "42"}
    assert {e.qualified_name for e in tu.types} == {"ns::Widget"}
    assert {e.qualified_name for e in tu.templates} == {"ns::maxv"}
    # Round-trips through the normalized schema.
    assert SourceAbiTu.from_dict(tu.to_dict()).tu_id == tu.tu_id


def test_ast_mapping_extracts_typedef_underlying_type() -> None:
    # ADR-030 follow-up #3: a public typedef/alias records its underlying type so
    # a later target change is detectable; bare typedefs have no exported symbol.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "TypedefDecl", "name": "handle_t",
                "loc": {"file": "include/foo.h", "line": 4},
                "type": {"qualType": "int32_t"},
            },
            {
                "kind": "TypeAliasDecl", "name": "size_alias",
                "loc": {"file": "include/foo.h", "line": 5},
                "type": {"qualType": "unsigned long"},
            },
        ],
    }
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    typedefs = {e.qualified_name: e for e in tu.types if e.kind == "typedef"}
    assert typedefs["handle_t"].value == "int32_t"
    assert typedefs["size_alias"].value == "unsigned long"
    assert typedefs["handle_t"].type_hash.startswith("sha256:")


def test_typedef_underlying_fallback_and_missing() -> None:
    from abicheck.evidence.source_extractors.clang import _typedef_underlying

    # qualType wins; desugaredQualType is the fallback; a non-dict/absent type
    # yields "" so _emit_typedef skips the entity.
    assert _typedef_underlying({"type": {"qualType": "int32_t"}}) == "int32_t"
    assert _typedef_underlying({"type": {"desugaredQualType": "int"}}) == "int"
    assert _typedef_underlying({"type": None}) == ""
    assert _typedef_underlying({}) == ""


def test_typedef_without_underlying_is_skipped() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "TypedefDecl", "name": "opaque",
            "loc": {"file": "include/foo.h", "line": 2}, "type": {},
        }],
    }
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    assert not [e for e in tu.types if e.kind == "typedef"]


# -- alpha-equivalence body fingerprint (ADR-030 follow-up #6) ---------------


def _fn_ast(param, local, ret_ref):
    """A FunctionDecl `int f(int <param>) {{ int <local> = <param>; return <ret>; }}`.

    Each of param/local/ret_ref is an ``(id, name)`` pair; ret_ref's id picks
    which binding the `return` references (the local, or some other id).
    """
    pid, pname = param
    lid, lname = local
    rid, rname = ret_ref
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "FunctionDecl", "name": "f", "mangledName": "_Z1fi",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int (int)"},
            "inner": [
                {"kind": "ParmVarDecl", "id": pid, "name": pname,
                 "type": {"qualType": "int"}},
                {"kind": "CompoundStmt", "inner": [
                    {"kind": "DeclStmt", "inner": [
                        {"kind": "VarDecl", "id": lid, "name": lname,
                         "type": {"qualType": "int"}, "inner": [
                            {"kind": "ImplicitCastExpr", "inner": [
                                {"kind": "DeclRefExpr",
                                 "referencedDecl": {"id": pid, "name": pname}}]}]}]},
                    {"kind": "ReturnStmt", "inner": [
                        {"kind": "ImplicitCastExpr", "inner": [
                            {"kind": "DeclRefExpr",
                             "referencedDecl": {"id": rid, "name": rname}}]}]},
                ]},
            ],
        }],
    }


def _body_hash(ast):
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    return tu.inline_bodies[0].body_hash


def test_body_fingerprint_invariant_under_local_and_param_rename() -> None:
    # Same body, locals/params spelled differently and with different clang ids
    # (as a real reparse would assign) → identical alpha-equivalence fingerprint.
    base = _fn_ast(("P1", "x"), ("L1", "y"), ("L1", "y"))
    renamed = _fn_ast(("P9", "arg"), ("L9", "result"), ("L9", "result"))
    assert _body_hash(base) == _body_hash(renamed)


def test_body_fingerprint_changes_when_reference_target_differs() -> None:
    # Returning a *different* binding (a global `g`, id not among the locals) is a
    # real semantic change — the global's name is kept, so the hash differs.
    returns_local = _fn_ast(("P1", "x"), ("L1", "y"), ("L1", "y"))
    returns_param = _fn_ast(("P1", "x"), ("L1", "y"), ("P1", "x"))
    returns_global = _fn_ast(("P1", "x"), ("L1", "y"), ("G1", "g"))
    # local vs param: both are alpha-renamed locals but to *different* slots → differ.
    assert _body_hash(returns_local) != _body_hash(returns_param)
    # referencing a non-local global keeps its real name → differs from either.
    assert _body_hash(returns_global) != _body_hash(returns_local)


def _fn_with_static_local(static_name):
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "FunctionDecl", "name": "f", "mangledName": "_Z1fv",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "CompoundStmt", "inner": [
                {"kind": "DeclStmt", "inner": [
                    {"kind": "VarDecl", "id": "S1", "name": static_name,
                     "storageClass": "static", "type": {"qualType": "int"}}]},
                {"kind": "ReturnStmt", "inner": [
                    {"kind": "ImplicitCastExpr", "inner": [
                        {"kind": "DeclRefExpr",
                         "referencedDecl": {"id": "S1", "name": static_name}}]}]},
            ]}],
        }],
    }


def test_body_fingerprint_changes_on_static_local_rename() -> None:
    # A function-local `static` emits a distinct linkage symbol (f()::counter),
    # so renaming it is observable and must change the fingerprint — it is NOT
    # alpha-renamed like an automatic local (Codex review).
    assert _body_hash(_fn_with_static_local("counter")) != _body_hash(
        _fn_with_static_local("state")
    )


def test_body_fingerprint_changes_on_global_rename() -> None:
    # Two bodies that reference *different* globals must stay distinct (the
    # alpha-renaming must not collapse non-local references).
    g1 = _fn_ast(("P1", "x"), ("L1", "y"), ("G1", "alpha"))
    g2 = _fn_ast(("P1", "x"), ("L1", "y"), ("G2", "beta"))
    assert _body_hash(g1) != _body_hash(g2)


def _fn_binop(op, lname, rname):
    """`int f() {{ return <lname> <op> <rname>; }}` with two global operands."""
    def _ref(gid, name):
        return {"kind": "DeclRefExpr", "referencedDecl": {"id": gid, "name": name}}
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "FunctionDecl", "name": "f", "mangledName": "_Z1fv",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int ()"},
            "inner": [{"kind": "CompoundStmt", "inner": [
                {"kind": "ReturnStmt", "inner": [
                    {"kind": "BinaryOperator", "opcode": op,
                     "inner": [_ref("GL", lname), _ref("GR", rname)]}]}]}],
        }],
    }


def test_commutative_operands_sorted_in_fingerprint() -> None:
    # `a + b` and `b + a` hash identically; `a == b` / `b == a` too.
    assert _body_hash(_fn_binop("+", "a", "b")) == _body_hash(_fn_binop("+", "b", "a"))
    assert _body_hash(_fn_binop("==", "a", "b")) == _body_hash(_fn_binop("==", "b", "a"))


def test_noncommutative_operands_kept_ordered() -> None:
    # `a - b` != `b - a`; short-circuit `&&` is NOT reordered (side-effect order).
    assert _body_hash(_fn_binop("-", "a", "b")) != _body_hash(_fn_binop("-", "b", "a"))
    assert _body_hash(_fn_binop("&&", "a", "b")) != _body_hash(_fn_binop("&&", "b", "a"))


def test_directory_header_root_classifies_decls_as_public(tmp_path: Path) -> None:
    # Codex #339 P2: `--headers include/` (a directory root) must classify a decl
    # reported under it (include/foo.h) as public, not drop the whole tree.
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "foo.h").write_text("int x;\n")
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "FunctionDecl", "name": "pub",
            "loc": {"file": str(inc / "foo.h")},
            "mangledName": "_Z3pubv", "type": {"qualType": "void ()"},
        }],
    }
    # The public root is the directory itself.
    tu = source_abi_from_clang_ast(ast, _cu(), [str(inc)], "t")
    assert any(e.qualified_name == "pub" for e in tu.functions)


def test_trailing_slash_root_treated_as_directory() -> None:
    # A root with a trailing separator is a directory even if it does not exist
    # on disk, so a decl under it is public.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "FunctionDecl", "name": "pub", "loc": {"file": "include/api.h"},
            "mangledName": "_Z3pubv", "type": {"qualType": "void ()"},
        }],
    }
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/"], "t")
    assert any(e.qualified_name == "pub" for e in tu.functions)


def test_ast_mapping_excludes_private_header_decls() -> None:
    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "target://libfoo")
    # `priv` lives in src/internal.h, not the public set → never emitted.
    all_names = {e.qualified_name for e in tu.all_entities()}
    assert "priv" not in all_names


def _record_ast(tag: str, members: list[dict]) -> dict:
    """A clang AST with one record (``tag`` = class/struct) in a public header."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{
            "kind": "CXXRecordDecl", "name": "C", "tagUsed": tag,
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": members,
        }],
    }


def _method(name: str, *, default: str | None = None, body: bool = False) -> dict:
    inner: list[dict] = []
    if default is not None:
        inner.append({
            "kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"},
            "init": "c", "inner": [{"kind": "IntegerLiteral", "value": default}],
        })
    if body:
        inner.append({"kind": "CompoundStmt", "inner": []})
    return {
        "kind": "CXXMethodDecl", "name": name, "loc": {"line": 3},
        "type": {"qualType": "int (int)"}, "inner": inner,
    }


def test_class_default_private_members_excluded() -> None:
    # A `class` defaults to private; a member before any `public:` is hidden and
    # must not reach the L4 surface (Codex #339 P2).
    ast = _record_ast("class", [_method("secret", default="1", body=True)])
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    assert all(e.qualified_name != "C::secret" for e in tu.all_entities())


def test_struct_default_public_members_emitted() -> None:
    # A `struct` defaults to public, so its members are part of the surface.
    ast = _record_ast("struct", [_method("visible", default="1", body=True)])
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    assert any(e.qualified_name == "C::visible" for e in tu.functions)
    assert any(e.qualified_name == "C::visible" for e in tu.inline_bodies)


def test_access_spec_section_switches_visibility() -> None:
    # `public:` exposes following members; `private:` hides them again.
    ast = _record_ast("class", [
        {"kind": "AccessSpecDecl", "access": "public"},
        _method("api", default="1"),
        {"kind": "AccessSpecDecl", "access": "private"},
        _method("impl", default="2"),
    ])
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    names = {e.qualified_name for e in tu.functions}
    assert "C::api" in names
    assert "C::impl" not in names


def test_explicit_per_decl_access_honored() -> None:
    # Newer clang stamps `access` on each member decl; a private one is dropped
    # even in a struct (default public).
    ast = _record_ast("struct", [
        dict(_method("api", default="1"), access="public"),
        dict(_method("impl", default="2"), access="private"),
    ])
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    names = {e.qualified_name for e in tu.functions}
    assert "C::api" in names
    assert "C::impl" not in names


def test_members_of_private_nested_class_excluded() -> None:
    # A private nested class is not public, so its (struct-default-public)
    # members stay off the surface too.
    nested = {
        "kind": "CXXRecordDecl", "name": "Impl", "tagUsed": "struct",
        "loc": {"line": 4}, "inner": [_method("run", default="1")],
    }
    ast = _record_ast("class", [
        {"kind": "AccessSpecDecl", "access": "private"},
        nested,
    ])
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    names = {e.qualified_name for e in tu.all_entities()}
    assert "C::Impl" not in names
    assert "C::Impl::run" not in names


def test_ast_mapping_template_not_double_counted_as_function() -> None:
    # The templated pattern FunctionDecl inside FunctionTemplateDecl must not also
    # surface as a plain function entity.
    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "target://libfoo")
    assert "ns::maxv" not in {e.qualified_name for e in tu.functions}


def test_ast_body_hash_is_stable_and_change_detected() -> None:
    base = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "t")
    # Same AST again → identical body hash (build-root independent / deterministic).
    again = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "t")
    assert base.inline_bodies[0].body_hash == again.inline_bodies[0].body_hash

    # Edit the inline body → inline_body_changed fires via the linker+diff.
    mutated = _ast()
    fn = mutated["inner"][0]["inner"][0]
    fn["inner"][-1]["inner"][0]["inner"][0]["name"] = "y"  # return x -> return y
    new = source_abi_from_clang_ast(mutated, _cu(), ["include/foo.h"], "t")
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([base]), link_source_abi([new]))
    }
    assert "inline_body_changed" in kinds


def test_ast_constexpr_change_detected_end_to_end() -> None:
    old = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "t")
    mutated = _ast()
    mutated["inner"][0]["inner"][1]["inner"][0]["value"] = "43"  # kMax 42 -> 43
    new = source_abi_from_clang_ast(mutated, _cu(), ["include/foo.h"], "t")
    changes = diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    by_kind = {c.kind.value: c for c in changes}
    assert "constexpr_value_changed" in by_kind
    assert by_kind["constexpr_value_changed"].old_value == "42"


def test_ast_records_read_files_for_cache_deps() -> None:
    # Codex #339 P1: the TU records every file it read so the per-TU cache can
    # invalidate on a transitive-include edit. Both the public and private
    # headers that contributed nodes appear. Paths are OS-normalized (backslashes
    # on Windows), so compare against os.path.normpath.
    import os

    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "t")
    assert os.path.normpath("include/foo.h") in tu.read_files
    assert os.path.normpath("src/internal.h") in tu.read_files


def _constexpr_ast(rhs_literal: str) -> dict:
    """A constexpr `N = 1 + <rhs>` (a compound expression, not a lone literal)."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "VarDecl", "name": "N", "loc": {"file": "include/foo.h"},
                "constexpr": True, "type": {"qualType": "const int"},
                "inner": [
                    {
                        "kind": "ConstantExpr", "value": "x",
                        "inner": [
                            {
                                "kind": "BinaryOperator", "opcode": "+",
                                "inner": [
                                    {"kind": "IntegerLiteral", "value": "1"},
                                    {"kind": "IntegerLiteral", "value": rhs_literal},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_walk_threads_sticky_file_across_siblings() -> None:
    # CodeRabbit: clang's loc.file is sticky. After a child subtree switches file,
    # a following sibling that omits loc.file must inherit the *last seen* file
    # (not the parent's own file), or it is misclassified public/private.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl", "name": "Outer",
                "loc": {"file": "include/pub.h"},
                "inner": [
                    {"kind": "FieldDecl", "name": "a", "type": {"qualType": "int"}},
                    # Nested decl switches the current file to a private header.
                    {"kind": "CXXRecordDecl", "name": "Nested",
                     "loc": {"file": "src/detail/priv.h"},
                     "inner": [{"kind": "FieldDecl", "name": "b",
                                "type": {"qualType": "int"}}]},
                ],
            },
            # Sibling omits loc.file → inherits the last seen file (priv.h),
            # so it is private and must NOT land on the public surface.
            {"kind": "FunctionDecl", "name": "leaked",
             "mangledName": "_Z6leakedv", "type": {"qualType": "void ()"}},
        ],
    }
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/pub.h"], "t")
    assert "leaked" not in {e.qualified_name for e in tu.functions}
    # Outer (public) is still captured.
    assert any("Outer" in e.qualified_name for e in tu.types)


def test_read_files_resolved_against_build_directory() -> None:
    # CodeRabbit/Codex P2: relative read_files (clang emits these for headers
    # found via a relative -I) are resolved to absolute against the TU directory
    # so the cache can read them from a different CWD.
    import os

    tu = source_abi_from_clang_ast(
        _ast(), _cu(directory="/work/proj"), ["include/foo.h"], "t"
    )
    assert os.path.normpath("/work/proj/include/foo.h") in tu.read_files


def test_constexpr_compound_expression_change_is_detected() -> None:
    # Codex #339 P2: `1 + 2` and `1 + 3` must not collapse to the same "1".
    old = source_abi_from_clang_ast(_constexpr_ast("2"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(_constexpr_ast("3"), _cu(), ["include/foo.h"], "t")
    assert old.constexpr_values[0].value != new.constexpr_values[0].value
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "constexpr_value_changed" in kinds


def test_default_argument_compound_expression_change_is_detected() -> None:
    def ast(rhs: str) -> dict:
        return {
            "kind": "TranslationUnitDecl",
            "inner": [{
                "kind": "FunctionDecl", "name": "f", "loc": {"file": "include/foo.h"},
                "mangledName": "_Z1fi", "type": {"qualType": "void (int)"},
                "inner": [{
                    "kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"},
                    "init": "c",
                    "inner": [{
                        "kind": "BinaryOperator", "opcode": "+",
                        "inner": [
                            {"kind": "IntegerLiteral", "value": "1"},
                            {"kind": "IntegerLiteral", "value": rhs},
                        ],
                    }],
                }],
            }],
        }

    old = source_abi_from_clang_ast(ast("2"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(ast("3"), _cu(), ["include/foo.h"], "t")
    assert old.functions[0].value != new.functions[0].value
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "default_argument_changed" in kinds


def _ctor_ast(default: str, *, with_definition: bool = False) -> dict:
    """A public ``Widget(int n = <default>)`` constructor decl, optionally also its
    out-of-line inline definition (which carries no default)."""
    inner = [
        {
            "kind": "CXXConstructorDecl", "name": "Widget", "loc": {"file": "include/foo.h"},
            "mangledName": "_ZN6WidgetC1Ei", "type": {"qualType": "void (int)"},
            "inner": [{
                "kind": "ParmVarDecl", "name": "n", "type": {"qualType": "int"},
                "init": "c", "inner": [{"kind": "IntegerLiteral", "value": default}],
            }],
        }
    ]
    if with_definition:
        inner.append({
            "kind": "CXXConstructorDecl", "name": "Widget", "loc": {"file": "include/foo.h"},
            "mangledName": "_ZN6WidgetC1Ei", "type": {"qualType": "void (int)"},
            "inner": [
                {"kind": "ParmVarDecl", "name": "n", "type": {"qualType": "int"}},
                {"kind": "CompoundStmt", "inner": []},
            ],
        })
    return {"kind": "TranslationUnitDecl", "inner": [
        {"kind": "CXXRecordDecl", "name": "Widget", "loc": {"file": "include/foo.h"},
         "inner": inner},
    ]}


def test_implicit_inline_method_body_is_fingerprinted() -> None:
    # Codex #339 P2: an in-class method definition (CompoundStmt, no `inline`
    # key) is implicitly inline — a body change must fire inline_body_changed.
    def ast(retval: str) -> dict:
        return {"kind": "TranslationUnitDecl", "inner": [{
            "kind": "CXXRecordDecl", "name": "W", "loc": {"file": "include/foo.h"},
            "inner": [{
                "kind": "CXXMethodDecl", "name": "f", "mangledName": "_ZN1W1fEv",
                "type": {"qualType": "int ()"},
                # No "inline" key — clang omits it for in-class definitions.
                "inner": [{"kind": "CompoundStmt", "inner": [{
                    "kind": "ReturnStmt", "inner": [
                        {"kind": "IntegerLiteral", "value": retval}]}]}],
            }],
        }]}

    old = source_abi_from_clang_ast(ast("1"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(ast("2"), _cu(), ["include/foo.h"], "t")
    assert {e.qualified_name for e in old.inline_bodies} == {"W::f"}
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "inline_body_changed" in kinds


def test_constructor_default_argument_change_detected() -> None:
    # Codex #339 P2: a CXXConstructorDecl must route through _emit_function so a
    # constructor default-argument change is detected (was previously skipped).
    old = source_abi_from_clang_ast(_ctor_ast("1"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(_ctor_ast("2"), _cu(), ["include/foo.h"], "t")
    assert any("Widget::Widget" in e.qualified_name for e in old.functions)
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "default_argument_changed" in kinds


def test_inline_defined_constructor_default_change_not_masked() -> None:
    # When the header carries both the declaration (with the default) and the
    # out-of-line inline definition (no default), the value-less definition must
    # not overwrite the default-bearing declaration in the diff (Codex #339 P2).
    old = source_abi_from_clang_ast(
        _ctor_ast("1", with_definition=True), _cu(), ["include/foo.h"], "t"
    )
    new = source_abi_from_clang_ast(
        _ctor_ast("2", with_definition=True), _cu(), ["include/foo.h"], "t"
    )
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "default_argument_changed" in kinds


# -- macro extraction (-E -dD, pure parser) ----------------------------------


def test_macros_from_preprocessor_scopes_to_public_headers() -> None:
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = (
        '# 1 "src/foo.cpp"\n'
        '# 1 "<built-in>" 1\n'
        "#define __STDC__ 1\n"
        '# 1 "include/foo.h" 1\n'
        "#define FOO_SIZE 16\n"
        "#define ADD(a,b) ((a)+(b))\n"
        '# 1 "/usr/include/sys.h" 1\n'
        "#define SYS_ONLY 9\n"
    )
    macros, files = macros_from_preprocessor(text, ["include/foo.h"])
    by_name = {e.qualified_name: e.value for e in macros}
    # Public-header macros captured (object- and function-like)...
    assert by_name["FOO_SIZE"] == "16"
    assert by_name["ADD"] == "(a,b) ((a)+(b))"
    # ...while builtin and system macros are filtered out of the *entities*.
    assert "__STDC__" not in by_name and "SYS_ONLY" not in by_name
    # The cache-dependency file list, by contrast, is the *complete* set of real
    # files the preprocessor read — including the system header that declared
    # only a non-public macro — so a macro-only private/system header edit
    # invalidates the dump (Codex #339 P2). The <built-in> pseudo-file is skipped.
    assert files == ["/usr/include/sys.h", "include/foo.h", "src/foo.cpp"]


def test_macros_suppress_include_guards() -> None:
    # ADR-030 follow-up #2: an include guard (empty-valued, filename-derived) is
    # filtered out, while a real empty feature flag and a valued macro survive.
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = (
        '# 1 "include/foo.h" 1\n'
        "#define FOO_H\n"            # include guard for foo.h — suppressed
        "#define __FOO_H__\n"        # guard with underscores — suppressed
        "#define FOO_ENABLED\n"      # real empty feature flag — kept
        "#define FOO_H_FEATURE\n"    # starts with stem but is NOT the guard — kept
        "#define FOO_SIZE 16\n"      # valued macro — kept
    )
    macros, _ = macros_from_preprocessor(text, ["include/foo.h"])
    names = {e.qualified_name for e in macros}
    assert "FOO_H" not in names
    assert "__FOO_H__" not in names
    # Exact-spelling match only: a feature macro that merely starts with the
    # stem must survive (Codex review — substring match was too aggressive).
    assert names == {"FOO_ENABLED", "FOO_H_FEATURE", "FOO_SIZE"}


def test_macros_suppress_hpp_include_guard() -> None:
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = '# 1 "include/bar.hpp" 1\n#define BAR_HPP\n#define BAR_VALUE 3\n'
    macros, _ = macros_from_preprocessor(text, ["include/bar.hpp"])
    names = {e.qualified_name for e in macros}
    assert names == {"BAR_VALUE"}


def test_macros_track_private_macro_only_header_as_cache_dep() -> None:
    # A private header that defines a macro gating an #if in a public header is
    # seen only by the preprocessor (no public macro entity, no AST node), but it
    # must still be a cache dependency so editing it invalidates the dump.
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = (
        '# 1 "include/api.h" 1\n'
        '# 1 "include/detail/config.h" 1\n'
        "#define ENABLE_X 1\n"  # private gating macro — not on the public surface
        '# 2 "include/api.h" 2\n'
        "#define API_VERSION 3\n"
    )
    macros, files = macros_from_preprocessor(text, ["include/api.h"])
    names = {e.qualified_name for e in macros}
    # Private gating macro is filtered out of the entities...
    assert "ENABLE_X" not in names and "API_VERSION" in names
    # ...but its header is still tracked as a cache dependency.
    assert "include/detail/config.h" in files


def test_macros_unfold_line_continuations() -> None:
    # CodeRabbit: a backslash-continued macro must be parsed whole, so an edit
    # below the first physical line is still visible to the value comparison.
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = (
        '# 1 "include/foo.h" 1\n'
        "#define BIG(a, b) \\\n"
        "    ((a) + \\\n"
        "     (b))\n"
    )
    macros, _ = macros_from_preprocessor(text, ["include/foo.h"])
    by_name = {e.qualified_name: e.value for e in macros}
    assert "BIG" in by_name
    # The whole body is captured (both continuation lines), not just "(a, b)".
    assert "(a) +" in by_name["BIG"] and "(b)" in by_name["BIG"]


def test_macros_honor_undef() -> None:
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    text = (
        '# 1 "include/foo.h" 1\n'
        "#define TMP 1\n"
        "#undef TMP\n"
        "#define KEEP 2\n"
    )
    macros, _ = macros_from_preprocessor(text, ["include/foo.h"])
    names = {e.qualified_name for e in macros}
    assert "TMP" not in names and "KEEP" in names


def test_public_macro_value_change_detected_end_to_end() -> None:
    from abicheck.evidence.source_abi import SourceAbiTu
    from abicheck.evidence.source_extractors import macros_from_preprocessor

    def surface(value: str):  # type: ignore[no-untyped-def]
        macros, _ = macros_from_preprocessor(
            f'# 1 "include/foo.h" 1\n#define FOO_SIZE {value}\n', ["include/foo.h"]
        )
        return link_source_abi([SourceAbiTu(tu_id="cu://a", macros=macros)])

    changes = diff_source_abi(surface("16"), surface("32"))
    by_kind = {c.kind.value: c for c in changes}
    assert "public_macro_value_changed" in by_kind
    assert by_kind["public_macro_value_changed"].old_value == "16"


def test_param_rename_with_same_default_is_not_a_change() -> None:
    # Codex #339 P2: a pure parameter rename keeping the same default value must
    # NOT fire default_argument_changed (callers omitting the arg get the same
    # value). The comparable value is keyed by position, not name.
    def fn(pname: str) -> dict:
        return {"kind": "TranslationUnitDecl", "inner": [{
            "kind": "FunctionDecl", "name": "f", "loc": {"file": "include/foo.h"},
            "mangledName": "_Z1fi", "type": {"qualType": "void (int)"},
            "inner": [{
                "kind": "ParmVarDecl", "name": pname, "type": {"qualType": "int"},
                "init": "c", "inner": [{"kind": "IntegerLiteral", "value": "1"}],
            }],
        }]}

    old = source_abi_from_clang_ast(fn("x"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(fn("y"), _cu(), ["include/foo.h"], "t")
    assert old.functions[0].value == new.functions[0].value == "p0=1"
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "default_argument_changed" not in kinds


def test_constexpr_referencing_changed_constant_is_detected() -> None:
    # Codex #339 P2: a constexpr initialized from another named constant
    # (DeclRefExpr) that changes kOld -> kNew (same type) must be detected;
    # _canonical now preserves the referenced declaration name.
    def ast(ref: str) -> dict:
        return {"kind": "TranslationUnitDecl", "inner": [{
            "kind": "VarDecl", "name": "N", "loc": {"file": "include/foo.h"},
            "constexpr": True, "type": {"qualType": "const int"},
            "inner": [{
                "kind": "ConstantExpr", "type": {"qualType": "const int"},
                "inner": [{
                    "kind": "DeclRefExpr", "type": {"qualType": "const int"},
                    "referencedDecl": {"kind": "VarDecl", "name": ref},
                }],
            }],
        }]}

    old = source_abi_from_clang_ast(ast("kOld"), _cu(), ["include/foo.h"], "t")
    new = source_abi_from_clang_ast(ast("kNew"), _cu(), ["include/foo.h"], "t")
    assert old.constexpr_values[0].value != new.constexpr_values[0].value
    kinds = {
        c.kind.value
        for c in diff_source_abi(link_source_abi([old]), link_source_abi([new]))
    }
    assert "constexpr_value_changed" in kinds


# -- extractor subprocess orchestration (mocked clang) -----------------------


class _Result:
    def __init__(self, rc: int, out: str, err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _patch_run(monkeypatch, handler) -> ClangSourceExtractor:  # type: ignore[no-untyped-def]
    from abicheck.evidence.source_extractors import clang as clang_mod

    extractor = ClangSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    monkeypatch.setattr(clang_mod.subprocess, "run", handler)
    return extractor


def test_extract_runs_macro_pass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    calls: list[list[str]] = []

    def handler(cmd, **kw):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        if "-ast-dump=json" in cmd:
            return _Result(0, json.dumps(_ast()))
        return _Result(0, '# 1 "include/foo.h" 1\n#define FOO_SIZE 16\n')

    extractor = _patch_run(monkeypatch, handler)
    tu = extractor.extract(
        _cu(source="foo.cpp"), public_header_roots=["include/foo.h"], target_id="t"
    )
    assert len(calls) == 2  # AST pass + macro pass
    assert any(e.qualified_name == "FOO_SIZE" for e in tu.macros)


def test_extract_macro_pass_failure_is_diagnostic(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    def handler(cmd, **kw):  # type: ignore[no-untyped-def]
        if "-ast-dump=json" in cmd:
            return _Result(0, json.dumps(_ast()))
        return _Result(1, "", "macro boom")

    extractor = _patch_run(monkeypatch, handler)
    tu = extractor.extract(_cu(), public_header_roots=["include/foo.h"])
    assert tu.macros == []
    assert any("macro pass" in d for d in tu.diagnostics)


def test_extract_records_recovered_ast_exit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    def handler(cmd, **kw):  # type: ignore[no-untyped-def]
        if "-ast-dump=json" in cmd:
            return _Result(1, json.dumps(_ast()), "warning: recovered")
        return _Result(0, "")

    extractor = _patch_run(monkeypatch, handler)
    tu = extractor.extract(_cu(), public_header_roots=["include/foo.h"])
    assert any("recovered" in d for d in tu.diagnostics)


def test_extract_timeout_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import subprocess as sp

    def handler(cmd, **kw):  # type: ignore[no-untyped-def]
        raise sp.TimeoutExpired(cmd, 1)

    extractor = _patch_run(monkeypatch, handler)
    extractor.timeout = 1
    with pytest.raises(SourceExtractionError, match="timed out"):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


def test_extract_invalid_json_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    extractor = _patch_run(monkeypatch, lambda cmd, **kw: _Result(0, "{not json"))
    with pytest.raises(SourceExtractionError, match="not valid JSON"):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


def test_build_clang_macro_command_gnu_and_msvc() -> None:
    gnu = build_clang_macro_command(_cu(standard="c++17"), Path("a.cpp"))
    assert "-E" in gnu and "-dD" in gnu and gnu[-1] == str(Path("a.cpp"))
    msvc = build_clang_macro_command(
        _cu(standard="c++20"), Path("a.cpp"), compiler_binary="clang-cl"
    )
    # cl-driver mode ignores -dD; clang-cl's /d1PP retains macro defs in /E mode.
    assert "--driver-mode=cl" in msvc and "/E" in msvc and "/d1PP" in msvc
    assert "-dD" not in msvc


def test_forward_declaration_emits_no_type() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "CXXRecordDecl", "name": "Fwd", "loc": {"file": "include/foo.h"}},
        ],
    }
    tu = source_abi_from_clang_ast(ast, _cu(), ["include/foo.h"], "t")
    assert tu.types == []  # forward decl (no inner members) → skipped


# -- extractor orchestration -------------------------------------------------


def test_extract_raises_when_clang_unavailable() -> None:
    extractor = ClangSourceExtractor(clang_bin="clang-does-not-exist-xyz")
    assert extractor.available() is False
    with pytest.raises(SourceExtractionError, match="requires"):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


def test_extract_parses_fake_clang_json(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Mock clang so the extract() success path runs without the tool installed.
    import json

    from abicheck.evidence.source_extractors import clang as clang_mod

    extractor = ClangSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps(_ast())

    def _fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        captured["cwd"] = kw.get("cwd")
        return _Result()

    monkeypatch.setattr(clang_mod.subprocess, "run", _fake_run)
    cu = _cu(source="src/foo.cpp", directory=str(tmp_path))
    tu = extractor.extract(cu, public_header_roots=["include/foo.h"], target_id="target://x")
    assert captured["cwd"] == str(tmp_path)
    assert any(e.qualified_name == "ns::add" for e in tu.functions)


def test_extract_raises_on_empty_clang_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from abicheck.evidence.source_extractors import clang as clang_mod

    extractor = ClangSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)

    class _Result:
        returncode = 1
        stderr = "fatal error: 'foo.h' file not found"
        stdout = ""

    monkeypatch.setattr(clang_mod.subprocess, "run", lambda cmd, **kw: _Result())
    with pytest.raises(SourceExtractionError, match="no AST"):
        extractor.extract(_cu(), public_header_roots=["include/foo.h"])


# -- end-to-end via real clang (integration) ---------------------------------


@pytest.mark.integration
def test_clang_extractor_end_to_end(tmp_path: Path) -> None:
    extractor = ClangSourceExtractor()
    if not extractor.available():
        pytest.skip("clang not installed")
    header = tmp_path / "foo.h"
    header.write_text(
        textwrap.dedent(
            """
            #ifndef FOO_H
            #define FOO_H
            namespace ns {
            inline int add(int x, int y = 1) { return x + y; }
            constexpr int kAnswer = 42;
            template <typename T> T maxv(T a, T b) { return a < b ? b : a; }
            }
            #endif
            """
        )
    )
    src = tmp_path / "foo.cpp"
    src.write_text('#include "foo.h"\n')
    cu = CompileUnit(id="cu://foo.cpp", source=str(src), language="CXX", standard="c++17")
    tu = extractor.extract(cu, public_header_roots=[str(header)], target_id="target://libfoo")
    names = {e.qualified_name for e in tu.all_entities()}
    assert any("add" in n for n in names)
    assert any("kAnswer" in e.qualified_name for e in tu.constexpr_values)
    assert any("maxv" in e.qualified_name for e in tu.templates)
    # The inline body fingerprint is populated (clang's job, not castxml's).
    assert any("add" in e.qualified_name and e.body_hash for e in tu.inline_bodies)
