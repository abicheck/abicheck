"""A variable's declared alignment on the castxml header path (WS-2 A2.1).

castxml 0.7 writes no alignment specifier on a ``<Variable>``, so the header
path used to record a variable's natural alignment as a lower bound, and a
raised ``aligned(16) -> aligned(64)`` read NO_CHANGE at headers depth. The
clang join now carries each declaration's own ``AlignedAttr`` and the castxml
reader takes it as the declared value.

Oracle for the integration cases: gcc's ``__alignof__`` of the variable, probed
in a separately compiled library and read back with ``ctypes``. That is the
compiler's layout, not an abicheck helper.
"""

from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.headers.castxml.clang_semantics import CastxmlClangSemantics
from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.preprocessed_text import (
    PreprocessedText,
    attach_preprocessed_text,
)
from abicheck.extract.headers.castxml.variable_alignment import (
    variable_alignment_fact,
)
from abicheck.extract.headers.clang.var_alignment import (
    clang_variable_alignment_map,
)
from abicheck.model.fact import FactStatus
from tests._abicheck_subprocess import abicheck_command

# ---------------------------------------------------------------------------
# Clang AST join: fast, on hand-built AST nodes.
# ---------------------------------------------------------------------------


def _var(
    name: str,
    *aligned: int | None,
    mangled: str | None = None,
    implicit: bool = False,
) -> dict[str, Any]:
    """A VarDecl with one AlignedAttr per entry; ``None`` is a bare ``aligned``."""
    attrs = [
        {"kind": "AlignedAttr", "inner": [{"kind": "IntegerLiteral", "value": str(v)}]}
        if v is not None
        else {"kind": "AlignedAttr", "inner": []}
        for v in aligned
    ]
    node: dict[str, Any] = {"kind": "VarDecl", "name": name, "inner": attrs}
    if mangled is not None:
        node["mangledName"] = mangled
    if implicit:
        node["isImplicit"] = True
    return node


@pytest.mark.parametrize(
    ("decls", "expected"),
    [
        # Raised, reduced and an unspecified variable each read their own value.
        ([_var("g_up", 64, mangled="g_up")], {"g_up": 512}),
        ([_var("g_down", 16, mangled="g_down")], {"g_down": 128}),
        ([_var("g_plain", mangled="g_plain")], {}),
        # Redeclarations that state the same value are one declared value.
        (
            [
                _var("g_twice", 32, mangled="g_twice"),
                _var("g_twice", 32, mangled="g_twice"),
            ],
            {"g_twice": 256},
        ),
        # Declarations that disagree leave the variable out (a lower bound).
        (
            [
                _var("g_clash", 16, mangled="g_clash"),
                _var("g_clash", 64, mangled="g_clash"),
            ],
            {},
        ),
        # A zero-only specifier (valid _Alignas(0)) has no alignment effect and
        # states nothing; a zero beside a stated value does not change it.
        ([_var("g_zero", 0, mangled="g_zero")], {}),
        ([_var("g_mixed", 0, 16, mangled="g_mixed")], {"g_mixed": 128}),
        # A bare aligned states no value: the variable stays out.
        ([_var("g_bare", None, mangled="g_bare")], {}),
        # An implicit declaration is not part of the spelled surface.
        ([_var("g_impl", 64, mangled="g_impl", implicit=True)], {}),
    ],
)
def test_clang_join_reads_each_declarations_own_specifier(
    decls: list[dict[str, Any]], expected: dict[str, int]
) -> None:
    root = {"kind": "TranslationUnitDecl", "inner": list(decls)}
    assert clang_variable_alignment_map(root) == expected


def test_clang_join_skips_a_variable_declared_inside_a_function_body() -> None:
    local = _var("g_local", 64, mangled="g_local")
    fn = {
        "kind": "FunctionDecl",
        "name": "f",
        "inner": [{"kind": "CompoundStmt", "inner": [local]}],
    }
    assert (
        clang_variable_alignment_map({"kind": "TranslationUnitDecl", "inner": [fn]})
        == {}
    )


# ---------------------------------------------------------------------------
# castxml reader: the joined declaration wins over the natural lower bound.
# ---------------------------------------------------------------------------


def _castxml_ctx(joined: dict[str, int] | None) -> tuple[CastxmlParserContext, Element]:
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name="/x/api.h")
    SubElement(root, "Namespace", id="_0", name="::")
    SubElement(root, "FundamentalType", id="_1", name="int", size="32", align="32")
    # castxml writes no mangled name for a file-scope variable, only its context.
    var = SubElement(root, "Variable", id="_2", name="g_al", context="_0", type="_1")
    var.set("file", "f1")
    # The variable's file spells an alignment specifier, so castxml's natural
    # value alone is only a lower bound.
    attach_preprocessed_text(root, PreprocessedText({}, "", frozenset({"/x/api.h"})))
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    ctx.clang_semantics = CastxmlClangSemantics(variable_alignment=joined)
    return ctx, var


def test_mach_o_mangling_does_not_hide_a_file_scope_declaration() -> None:
    """clang on a Mach-O target records a C global's mangledName as "_g_v"
    while castxml writes no mangled name for it: the join must key it by "g_v"."""
    decl = {
        "kind": "VarDecl",
        "name": "g_v",
        "mangledName": "_g_v",
        "inner": [
            {
                "kind": "AlignedAttr",
                "inner": [{"kind": "IntegerLiteral", "value": "16"}],
            }
        ],
    }
    keyed = clang_variable_alignment_map(
        {"kind": "TranslationUnitDecl", "inner": [decl]}
    )
    assert keyed == {"g_v": 128}
    root = Element("CastXML")
    SubElement(root, "Namespace", id="_0", name="::")
    SubElement(root, "FundamentalType", id="_1", name="int", size="32", align="32")
    var = SubElement(root, "Variable", id="_2", name="g_v", context="_0", type="_1")
    attach_preprocessed_text(root, PreprocessedText({}, "", frozenset({"/x/api.h"})))
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    ctx.clang_semantics = CastxmlClangSemantics(variable_alignment=keyed)
    fact = variable_alignment_fact(ctx, var, 32)
    assert (fact.status, fact.value) == (FactStatus.PRESENT, 128)


def test_a_namespace_member_is_keyed_by_its_mangled_name_only() -> None:
    """n::g (mangled _ZN1n1gE) and the global g must not share a key."""
    nested = {
        "kind": "VarDecl",
        "name": "g",
        "mangledName": "_ZN1n1gE",
        "inner": [
            {
                "kind": "AlignedAttr",
                "inner": [{"kind": "IntegerLiteral", "value": "64"}],
            }
        ],
    }
    tree = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "NamespaceDecl", "name": "n", "inner": [nested]},
        ],
    }
    keyed = clang_variable_alignment_map(tree)
    assert keyed == {"_ZN1n1gE": 512}
    assert "g" not in keyed


def test_castxml_never_reads_another_variables_value_by_plain_name() -> None:
    """A namespace-scope n::g (mangled _ZN1n1gE) must not take the global g's
    joined value through its plain name."""
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name="/x/api.h")
    SubElement(root, "FundamentalType", id="_1", name="int", size="32", align="32")
    var = SubElement(root, "Variable", id="_2", name="g", mangled="_ZN1n1gE", type="_1")
    var.set("file", "f1")
    attach_preprocessed_text(root, PreprocessedText({}, "", frozenset({"/x/api.h"})))
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    ctx.clang_semantics = CastxmlClangSemantics(variable_alignment={"g": 512})
    fact = variable_alignment_fact(ctx, var, 32)
    assert (fact.status, fact.value) == (FactStatus.PARTIAL, 32)


@pytest.mark.parametrize(
    ("joined", "expected_bits", "status"),
    [
        ({"g_al": 512}, 512, FactStatus.PRESENT),  # the header states aligned(64)
        ({"g_al": 128}, 128, FactStatus.PRESENT),  # a reduction is declared too
        (None, 32, FactStatus.PARTIAL),  # no join: natural value, a lower bound
        ({"other": 512}, 32, FactStatus.PARTIAL),  # another name's value never leaks
    ],
)
def test_castxml_takes_the_joined_declaration_as_declared(
    joined: dict[str, int] | None, expected_bits: int, status: FactStatus
) -> None:
    ctx, var = _castxml_ctx(joined)
    fact = variable_alignment_fact(ctx, var, 32)
    assert (fact.status, fact.value) == (status, expected_bits)


# ---------------------------------------------------------------------------
# End to end on stripped libraries: the compiler oracle versus the dumped fact.
# ---------------------------------------------------------------------------

_CC = shutil.which("gcc")

#: Each pair is the old and new declaration of one variable ``g_v``. The
#: header declares it; the library defines it with the same specifier.
_SIBLINGS = [
    ("raised", "aligned(16)", "aligned(64)"),
    ("reduced", "aligned(64)", "aligned(16)"),
    ("wide", "aligned(16)", "aligned(256)"),
]


def _build_stripped(work: Path, tag: str, spec: str) -> tuple[Path, Path]:
    """A stripped shared library and header declaring ``g_v`` with *spec*."""
    header = work / f"{tag}.h"
    header.write_text(f"extern int g_v __attribute__(({spec}));\n")
    src = work / f"{tag}.c"
    src.write_text(f'#include "{tag}.h"\nint g_v __attribute__(({spec})) = 5;\n')
    so = work / f"lib{tag}.so"
    subprocess.run(
        [_CC, "-s", "-shared", "-fPIC", "-O2", str(src), "-o", str(so)], check=True
    )
    return so, header


def _compiler_alignment_bytes(work: Path, tag: str, header: Path) -> int:
    """gcc's ``__alignof__(g_v)`` under *header*, read from a probe library."""
    src = work / f"probe_{tag}.c"
    src.write_text(
        f'#include "{header.name}"\nconst unsigned long probe_al = __alignof__(g_v);\n'
    )
    so = work / f"libprobe_{tag}.so"
    subprocess.run(
        [_CC, "-shared", "-fPIC", "-O2", "-I", str(work), str(src), "-o", str(so)],
        check=True,
    )
    return ctypes.c_ulong.in_dll(ctypes.CDLL(str(so)), "probe_al").value


def _dump(work: Path, so: Path, header: Path, label: str) -> dict[str, Any]:
    out = work / f"{label}.json"
    subprocess.run(
        abicheck_command(
            "dump",
            str(so),
            "-H",
            str(header),
            "--label",
            label,
            "-o",
            str(out),
        ),
        check=True,
        capture_output=True,
    )
    return json.loads(out.read_text())


def _variable_fact(snapshot: dict[str, Any], name: str) -> tuple[int, str]:
    for var in snapshot["sections"]["declarations"]["payload"]["variables"]:
        if var["name"] == name:
            return var["alignment_bits"], var["alignment_bits_fact"]["status"]
    raise AssertionError(f"{name} is not in the dump")


@pytest.mark.integration
@pytest.mark.skipif(_CC is None, reason="gcc not on PATH")
@pytest.mark.parametrize(
    ("case", "old_spec", "new_spec"), _SIBLINGS, ids=[c[0] for c in _SIBLINGS]
)
def test_stripped_header_fact_matches_the_compiler_on_both_sides(
    tmp_path: Path, case: str, old_spec: str, new_spec: str
) -> None:
    for side, spec in (("old", old_spec), ("new", new_spec)):
        tag = f"{case}_{side}"
        so, header = _build_stripped(tmp_path, tag, spec)
        oracle_bits = _compiler_alignment_bytes(tmp_path, tag, header) * 8
        bits, status = _variable_fact(_dump(tmp_path, so, header, tag), "g_v")
        assert status == "present", side
        assert bits == oracle_bits, side


@pytest.mark.integration
@pytest.mark.skipif(_CC is None, reason="gcc not on PATH")
@pytest.mark.parametrize(
    ("case", "old_spec", "new_spec"), _SIBLINGS, ids=[c[0] for c in _SIBLINGS]
)
def test_stripped_header_alignment_change_is_not_a_clean_pass(
    tmp_path: Path, case: str, old_spec: str, new_spec: str
) -> None:
    """The reported defect: a stripped {B,H} change must not read NO_CHANGE."""
    old_so, old_h = _build_stripped(tmp_path, f"{case}_old", old_spec)
    new_so, new_h = _build_stripped(tmp_path, f"{case}_new", new_spec)
    old_json = tmp_path / f"{case}_old.json"
    new_json = tmp_path / f"{case}_new.json"
    _dump(tmp_path, old_so, old_h, f"{case}_old")
    _dump(tmp_path, new_so, new_h, f"{case}_new")
    result = subprocess.run(
        abicheck_command("compare", str(old_json), str(new_json)),
        capture_output=True,
        text=True,
    )
    # Exit 4: an ABI break, the verdict the stripped header cell must reach.
    assert result.returncode == 4, result.stdout


# ---------------------------------------------------------------------------
# The join runs only for the library's own surface (perf regression, WS-2 A2.1).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spelled_in", "expected"),
    [
        ("system", False),  # a dependency header spells alignas: not the library's
        ("api", True),  # the given header spells aligned(N)
        ("detail", True),  # a header under the given header's directory does
        ("none", False),  # no file spells a specifier at all
    ],
)
def test_only_the_library_surface_triggers_the_clang_join(
    tmp_path: Path, spelled_in: str, expected: bool
) -> None:
    from abicheck.extract.headers.castxml.clang_semantics_join import (
        _spells_alignment_locally,
    )

    api_dir = tmp_path / "api"
    header = api_dir / "api.h"
    detail = api_dir / "detail" / "impl.h"
    system = "/usr/include/c++/v1/vector"
    by_key = {"system": system, "api": str(header), "detail": str(detail)}
    root = Element("CastXML")
    for name in (str(header), str(detail), system):
        SubElement(root, "File", id=f"f{len(root)}", name=name)
    spelled = frozenset({by_key[spelled_in]}) if spelled_in in by_key else frozenset()
    table = PreprocessedText({}, "", spelled)
    assert _spells_alignment_locally(root, [header], table) is expected


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating a directory symlink needs a privilege the Windows runners lack",
)
@pytest.mark.parametrize("recorded", ["given", "resolved"])
def test_a_symlinked_header_directory_is_matched_by_either_spelling(
    tmp_path: Path, recorded: str
) -> None:
    """macOS temp dirs are /var -> /private/var: the preprocessing run may record a
    header by the path it was given while castxml reports the resolved one."""
    from abicheck.extract.headers.castxml.clang_semantics_join import (
        _spells_alignment_locally,
    )

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    header = real_dir / "api.h"
    header.write_text("extern int g_v __attribute__((aligned(16)));\n")
    link = tmp_path / "link"
    link.symlink_to(real_dir, target_is_directory=True)
    given = link / "api.h"
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name=str(header.resolve()))
    name = str(given) if recorded == "given" else str(header.resolve())
    table = PreprocessedText({}, "", frozenset({name}))
    assert _spells_alignment_locally(root, [given], table) is True
