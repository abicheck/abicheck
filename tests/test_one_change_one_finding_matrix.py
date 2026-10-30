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

"""One change, one finding: an entity-kind x change matrix through the CLI.

Each row builds a v1/v2 pair of a real shared object with its public header,
its source and its ``compile_commands.json``, then runs the full source-depth
``abicheck compare`` (``--header --sources --build-info --depth source
--contract all``) under both header frontends (castxml, the default, and
``ABICHECK_AST_FRONTEND=clang``).

The oracle is written per row, not derived from the implementation:

* ``primary``: at least one of these kinds must survive, on the changed entity;
* ``companions``: the only other kinds that may survive alongside the primary.
  Each one is a distinct consequence (a size that follows a field addition, for
  example), named here so that loosening the check is an explicit edit;
* no kind may survive twice on the same entity. Two findings of one kind for one
  entity are one observation reported twice.

Inline-variable misses and the castxml ``noexcept`` gap are tracked by their own
sessions and are not rows here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.integration

_KEEP = "int keep(int x);\n"
_KEEP_IMPL = "int keep(int x) { return x + 1; }\n"


@dataclass(frozen=True)
class Row:
    """One entity kind x one change, with its expected finding kinds."""

    name: str
    entity: str  # the leaf token a finding on this entity names
    v1_header: str
    v1_impl: str
    v2_header: str
    v2_impl: str
    std: str = "c++17"


#: Retypes widen to ``long long``, not ``long``: ``long`` is 4 bytes on an LLP64
#: host (Windows), where ``int -> long`` is ABI-identical and rightly reports no
#: break, so a ``long`` row would assert an LP64 fact on every host.
ROWS: tuple[Row, ...] = (
    Row(
        "free_function_removed",
        "f_remove",
        _KEEP + "int f_remove(int x);\n",
        _KEEP_IMPL + "int f_remove(int x) { return x; }\n",
        _KEEP,
        _KEEP_IMPL,
    ),
    Row(
        "free_function_param_retyped",
        "f_prm",
        _KEEP + "int f_prm(int x);\n",
        _KEEP_IMPL + "int f_prm(int x) { return x; }\n",
        _KEEP + "int f_prm(long long x);\n",
        _KEEP_IMPL + "int f_prm(long long x) { return (int)x; }\n",
    ),
    Row(
        "free_function_return_retyped",
        "f_ret",
        _KEEP + "int f_ret();\n",
        _KEEP_IMPL + "int f_ret() { return 1; }\n",
        _KEEP + "long long f_ret();\n",
        _KEEP_IMPL + "long long f_ret() { return 1; }\n",
    ),
    Row(
        "global_variable_retyped",
        "g_var",
        _KEEP + "extern int g_var;\n",
        _KEEP_IMPL + "int g_var = 1;\n",
        _KEEP + "extern long long g_var;\n",
        _KEEP_IMPL + "long long g_var = 1;\n",
    ),
    Row(
        "enum_member_value_changed",
        "E_B",
        _KEEP + "enum E_T { E_A = 1, E_B = 2 };\n",
        _KEEP_IMPL,
        _KEEP + "enum E_T { E_A = 1, E_B = 3 };\n",
        _KEEP_IMPL,
    ),
    Row(
        "enumerator_removed",
        "E_C",
        _KEEP + "enum E_R { E_RA = 1, E_C = 2 };\n",
        _KEEP_IMPL,
        _KEEP + "enum E_R { E_RA = 1 };\n",
        _KEEP_IMPL,
    ),
    Row(
        "struct_field_added",
        "fld_add",
        _KEEP + "struct S_add { int a; };\n",
        _KEEP_IMPL,
        _KEEP + "struct S_add { int a; int fld_add; };\n",
        _KEEP_IMPL,
    ),
    Row(
        "struct_field_removed",
        "fld_rm",
        _KEEP + "struct S_rm { int a; int fld_rm; };\n",
        _KEEP_IMPL,
        _KEEP + "struct S_rm { int a; };\n",
        _KEEP_IMPL,
    ),
    Row(
        "struct_field_retyped",
        "fld_rt",
        _KEEP + "struct S_rt { int a; int fld_rt; };\n",
        _KEEP_IMPL,
        _KEEP + "struct S_rt { int a; long long fld_rt; };\n",
        _KEEP_IMPL,
    ),
    Row(
        "macro_value_changed",
        "LIM_MAC",
        _KEEP + "#define LIM_MAC 10\n",
        _KEEP_IMPL,
        _KEEP + "#define LIM_MAC 20\n",
        _KEEP_IMPL,
    ),
    Row(
        "class_virtual_method_added",
        "vg_add",
        _KEEP + "class Vc { public: virtual void vf() {} };\n",
        _KEEP_IMPL,
        _KEEP + "class Vc { public: virtual void vf() {} virtual void vg_add() {} };\n",
        _KEEP_IMPL,
    ),
    Row(
        "template_default_argument_changed",
        "TD_def",
        _KEEP + "template<class T, class U = int> struct TD_def { T t; U u; };\n",
        _KEEP_IMPL,
        _KEEP + "template<class T, class U = long> struct TD_def { T t; U u; };\n",
        _KEEP_IMPL,
    ),
    Row(
        "using_alias_retargeted",
        "AliasT",
        _KEEP + "using AliasT = int;\n",
        _KEEP_IMPL,
        _KEEP + "using AliasT = long;\n",
        _KEEP_IMPL,
    ),
    Row(
        "static_constexpr_member_value_changed",
        "K_V",
        _KEEP + "struct K_S { static constexpr int K_V = 1; };\n",
        _KEEP_IMPL,
        _KEEP + "struct K_S { static constexpr int K_V = 2; };\n",
        _KEEP_IMPL,
    ),
    Row(
        "default_argument_changed",
        "da_fn",
        _KEEP + "int da_fn(int x = 1);\n",
        _KEEP_IMPL + "int da_fn(int x) { return x; }\n",
        _KEEP + "int da_fn(int x = 2);\n",
        _KEEP_IMPL + "int da_fn(int x) { return x; }\n",
    ),
    Row(
        "function_became_deleted",
        "df_fn",
        _KEEP + "void df_fn();\n",
        _KEEP_IMPL + "void df_fn() {}\n",
        _KEEP + "void df_fn() = delete;\n",
        _KEEP_IMPL,
    ),
    Row(
        "inline_function_removed",
        "inl_fn",
        _KEEP + "inline int inl_fn() { return 1; }\n",
        _KEEP_IMPL,
        _KEEP,
        _KEEP_IMPL,
    ),
    Row(
        "uninstantiated_template_removed",
        "UT_tpl",
        _KEEP + "template<class T> void UT_tpl(T) {}\n",
        _KEEP_IMPL,
        _KEEP,
        _KEEP_IMPL,
    ),
    Row(
        "concept_tightened",
        "CT_c",
        _KEEP + "#include <type_traits>\n"
        "template<class T> concept CT_c = true;\n"
        "template<class T> requires CT_c<T> int ct_fn(T) { return 1; }\n",
        _KEEP_IMPL,
        _KEEP + "#include <type_traits>\n"
        "template<class T> concept CT_c = std::is_integral_v<T>;\n"
        "template<class T> requires CT_c<T> int ct_fn(T) { return 1; }\n",
        _KEEP_IMPL,
        std="c++20",
    ),
)


def _k(*kinds: str) -> frozenset[str]:
    return frozenset(kinds)


#: (row, frontend) -> (primary kinds, allowed companion kinds). Reviewed row by
#: row against the observed output; a row missing here fails the matrix rather
#: than passing silently. Companions are the distinct consequences one edit
#: legitimately implies, each with its reason:
#:
#: * ``free_function_param_retyped``: the old overload is removed (BREAKING) and
#:   the new one added (compatible): two symbols, two facts.
#: * ``global_variable_retyped``: ``int`` -> ``long`` changes the symbol's size
#:   and alignment as well as its type; each is a fact about the same variable.
#: * ``struct_field_retyped`` (castxml): the field's offset moves in the castxml
#:   layout; the clang frontend reports only the type.
#: * ``class_virtual_method_added``: adding a virtual changes the vtable layout
#:   as well as adding the function.
#: * ``template_default_argument_changed``: the template's declaration text
#:   moved, so the replay's body fingerprint moves too. The clang frontend has
#:   the default facet, which is the primary there; castxml has only the body.
EXPECTED: dict[tuple[str, str], tuple[frozenset[str], frozenset[str]]] = {
    ("free_function_removed", "castxml"): (_k("func_removed"), _k()),
    ("free_function_removed", "clang"): (_k("func_removed"), _k()),
    ("free_function_param_retyped", "castxml"): (_k("func_removed"), _k("func_added")),
    ("free_function_param_retyped", "clang"): (_k("func_removed"), _k("func_added")),
    ("free_function_return_retyped", "castxml"): (_k("func_return_changed"), _k()),
    ("free_function_return_retyped", "clang"): (_k("func_return_changed"), _k()),
    ("global_variable_retyped", "castxml"): (
        _k("var_type_changed"),
        _k("var_alignment_changed", "symbol_size_changed"),
    ),
    ("global_variable_retyped", "clang"): (
        _k("var_type_changed"),
        _k("symbol_size_changed"),
    ),
    ("enum_member_value_changed", "castxml"): (_k("enum_member_value_changed"), _k()),
    ("enum_member_value_changed", "clang"): (_k("enum_member_value_changed"), _k()),
    ("enumerator_removed", "castxml"): (_k("enum_member_removed"), _k()),
    ("enumerator_removed", "clang"): (_k("enum_member_removed"), _k()),
    ("struct_field_added", "castxml"): (_k("type_field_added_compatible"), _k()),
    ("struct_field_added", "clang"): (_k("type_field_added_compatible"), _k()),
    ("struct_field_removed", "castxml"): (_k("type_field_removed"), _k()),
    ("struct_field_removed", "clang"): (_k("type_field_removed"), _k()),
    ("struct_field_retyped", "castxml"): (
        _k("type_field_type_changed"),
        _k("type_field_offset_changed"),
    ),
    ("struct_field_retyped", "clang"): (_k("type_field_type_changed"), _k()),
    ("macro_value_changed", "castxml"): (_k("public_macro_value_changed"), _k()),
    ("macro_value_changed", "clang"): (_k("public_macro_value_changed"), _k()),
    ("class_virtual_method_added", "castxml"): (
        _k("func_added"),
        _k("type_vtable_changed"),
    ),
    ("class_virtual_method_added", "clang"): (
        _k("func_added"),
        _k("type_vtable_changed"),
    ),
    ("template_default_argument_changed", "castxml"): (
        _k("template_body_changed"),
        _k(),
    ),
    ("template_default_argument_changed", "clang"): (
        _k("template_param_default_changed"),
        _k("template_body_changed"),
    ),
    ("using_alias_retargeted", "castxml"): (_k("typedef_base_changed"), _k()),
    ("using_alias_retargeted", "clang"): (_k("typedef_base_changed"), _k()),
    ("static_constexpr_member_value_changed", "castxml"): (
        _k("constant_changed"),
        _k(),
    ),
    ("static_constexpr_member_value_changed", "clang"): (_k("constant_changed"), _k()),
    ("default_argument_changed", "castxml"): (_k("default_argument_changed"), _k()),
    ("default_argument_changed", "clang"): (_k("default_argument_changed"), _k()),
    ("function_became_deleted", "castxml"): (_k("func_removed"), _k()),
    ("function_became_deleted", "clang"): (_k("func_deleted"), _k()),
    ("inline_function_removed", "castxml"): (_k("inline_function_removed"), _k()),
    ("inline_function_removed", "clang"): (_k("inline_function_removed"), _k()),
    ("uninstantiated_template_removed", "castxml"): (
        _k("uninstantiated_template_removed"),
        _k(),
    ),
    ("uninstantiated_template_removed", "clang"): (
        _k("uninstantiated_template_removed"),
        _k(),
    ),
    ("concept_tightened", "castxml"): (_k("concept_tightened"), _k()),
    ("concept_tightened", "clang"): (_k("concept_tightened"), _k()),
}

_FRONTENDS = ("castxml", "clang")


def _build(side: Path, header: str, impl: str, std: str) -> None:
    side.mkdir(parents=True, exist_ok=True)
    (side / "widget.h").write_text("#pragma once\n" + header, encoding="utf-8")
    src = side / "widget.cpp"
    src.write_text('#include "widget.h"\n' + impl, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g",
            f"-std={std}",
            f"-I{side}",
            str(src),
            "-o",
            str(side / "libwidget.so"),
        ],
        check=True,
        capture_output=True,
    )
    (side / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(side),
                    "arguments": [
                        "g++",
                        f"-std={std}",
                        f"-I{side}",
                        "-c",
                        "widget.cpp",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )


def observe(
    tmp_path: Path, row: Row, frontend: str, monkeypatch: pytest.MonkeyPatch
) -> list[dict]:
    """Run the full source-depth compare for *row* and return the surviving
    entity-bearing findings (``kind``, ``symbol``, ``description``)."""
    if frontend == "clang":
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    else:
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    old = tmp_path / "v1"
    new = tmp_path / "v2"
    _build(old, row.v1_header, row.v1_impl, row.std)
    _build(new, row.v2_header, row.v2_impl, row.std)
    report = tmp_path / "report.json"
    args = [
        "compare",
        str(old / "libwidget.so"),
        str(new / "libwidget.so"),
        "--header",
        f"old={old / 'widget.h'}",
        "--header",
        f"new={new / 'widget.h'}",
        "--sources",
        f"old={old}",
        "--sources",
        f"new={new}",
        "--build-info",
        f"old={old / 'compile_commands.json'}",
        "--build-info",
        f"new={new / 'compile_commands.json'}",
        "--depth",
        "source",
        "--contract",
        "all",
        "-o",
        f"json={report}",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code in (0, 2, 4, 8), result.output
    data = json.loads(report.read_text(encoding="utf-8"))
    return [
        {
            "kind": c["kind"],
            "symbol": c.get("symbol"),
            "description": c.get("description", ""),
        }
        for c in data["changes"]
        if row.entity in json.dumps(c)
    ]


@pytest.fixture(autouse=True)
def _need_toolchain() -> None:
    if shutil.which("g++") is None:
        pytest.skip("g++ not installed")


@pytest.mark.parametrize("frontend", _FRONTENDS)
@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
def test_one_change_one_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: Row, frontend: str
) -> None:
    findings = observe(tmp_path, row, frontend, monkeypatch)
    kinds = [f["kind"] for f in findings]
    present = frozenset(kinds)

    # No kind survives twice on the entity: one observation, one finding.
    duplicated = sorted({k for k in kinds if kinds.count(k) > 1})
    assert not duplicated, f"kinds reported more than once: {duplicated}: {findings}"

    assert (row.name, frontend) in EXPECTED, (
        f"row {row.name!r} has no reviewed expectation for {frontend}; "
        f"observed kinds: {sorted(present)}"
    )
    primary, companions = EXPECTED[(row.name, frontend)]
    assert present & primary, f"no primary finding; got {sorted(present)}"
    unexpected = present - primary - companions
    assert not unexpected, f"unexpected companions {sorted(unexpected)}: {findings}"
