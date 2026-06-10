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


def test_ast_mapping_excludes_private_header_decls() -> None:
    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "target://libfoo")
    # `priv` lives in src/internal.h, not the public set → never emitted.
    all_names = {e.qualified_name for e in tu.all_entities()}
    assert "priv" not in all_names


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
    # headers that contributed nodes appear.
    tu = source_abi_from_clang_ast(_ast(), _cu(), ["include/foo.h"], "t")
    assert "include/foo.h" in tu.read_files
    assert "src/internal.h" in tu.read_files


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
    # ...while builtin and system macros are filtered out.
    assert "__STDC__" not in by_name and "SYS_ONLY" not in by_name
    assert files == ["include/foo.h"]


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
    assert "--driver-mode=cl" in msvc and "/E" in msvc and "-dD" in msvc


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
