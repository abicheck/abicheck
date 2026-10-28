"""C-linkage identity of a declaration, per target, across both clang backends.

A C-linkage declaration (plain C, or ``extern "C"`` in C++) has a linker
identity that differs by platform: Darwin's linker prepends one ``_`` to every
global symbol, so clang reports ``_cfun`` for a C function ``cfun``. The
identity both graphs join on is the decoration-free name, so every target
must yield the same ``linker`` key for it. The oracle below is written from
the platforms' conventions, not from the implementation.

Three layers are covered:

* fixture-AST unit tests (default lane, no clang): the raw ``mangledName``
  spellings are the ones a real clang 18 reported for the same sources;
* the real clang AST (``integration`` lane, skipped without clang): the same
  matrix through ``clang -ast-dump=json``, plus the L4 extractor's
  ``source_abi_from_clang_ast`` end to end;
* cross-backend agreement: the header-AST backend (``dumper_clang``) and the
  L4 source extractor (``clang_nodes``) must agree on every node of the same
  real AST.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors.clang import source_abi_from_clang_ast
from abicheck.buildsource.source_extractors.clang_command import dump_linkage_context
from abicheck.buildsource.source_extractors.clang_nodes import (
    _entity_names,
    _mangled,
)
from abicheck.dumper_clang import _ClangAstParser

#: Target triple per platform under test.
TARGETS: dict[str, str] = {
    "linux": "x86_64-unknown-linux-gnu",
    "darwin": "x86_64-apple-darwin",
    "windows-msvc": "x86_64-pc-windows-msvc",
}

#: The declaration kinds under test: name, AST kind, source language, and
#: whether the declaration is compiled as C++.
KINDS: dict[str, dict[str, Any]] = {
    "c_function": {"name": "cfun", "ast": "FunctionDecl", "lang": "c", "cxx": False},
    "cxx_function": {
        "name": "cxxfun",
        "ast": "FunctionDecl",
        "lang": "c++",
        "cxx": True,
    },
    "extern_c_function": {
        "name": "cfun2",
        "ast": "FunctionDecl",
        "lang": "c++",
        "cxx": True,
    },
    "c_variable": {"name": "cvar", "ast": "VarDecl", "lang": "c", "cxx": False},
    "cxx_variable": {"name": "cxxvar", "ast": "VarDecl", "lang": "c++", "cxx": True},
}

#: The ``mangledName`` clang 18.1.3 reports for each declaration, per target
#: (captured from ``clang -target <t> -x <lang> -fsyntax-only -Xclang
#: -ast-dump=json`` on the sources in ``_SOURCES`` below).
RAW_MANGLED: dict[tuple[str, str], str] = {
    ("c_function", "linux"): "cfun",
    ("c_function", "darwin"): "_cfun",
    ("c_function", "windows-msvc"): "cfun",
    ("cxx_function", "linux"): "_Z6cxxfuni",
    ("cxx_function", "darwin"): "__Z6cxxfuni",
    ("cxx_function", "windows-msvc"): "?cxxfun@@YAHH@Z",
    ("extern_c_function", "linux"): "cfun2",
    ("extern_c_function", "darwin"): "_cfun2",
    ("extern_c_function", "windows-msvc"): "cfun2",
    ("c_variable", "linux"): "cvar",
    ("c_variable", "darwin"): "_cvar",
    ("c_variable", "windows-msvc"): "cvar",
    ("cxx_variable", "linux"): "cxxvar",
    ("cxx_variable", "darwin"): "_cxxvar",
    ("cxx_variable", "windows-msvc"): "?cxxvar@@3HA",
}

#: The oracle: ``(linker, mangled)`` each declaration must report. ``linker``
#: is the decoration-free C name the L2 header graph keys on (on Darwin the
#: Mach-O symbol is ``_cfun``, but the identity drops the leading underscore on
#: every platform). ``mangled`` is the C++ mangling, ``""`` for C linkage.
ORACLE: dict[tuple[str, str], tuple[str, str]] = {
    ("c_function", "linux"): ("cfun", ""),
    ("c_function", "darwin"): ("cfun", ""),
    ("c_function", "windows-msvc"): ("cfun", ""),
    ("cxx_function", "linux"): ("", "_Z6cxxfuni"),
    ("cxx_function", "darwin"): ("", "_Z6cxxfuni"),
    ("cxx_function", "windows-msvc"): ("", "?cxxfun@@YAHH@Z"),
    ("extern_c_function", "linux"): ("cfun2", ""),
    ("extern_c_function", "darwin"): ("cfun2", ""),
    ("extern_c_function", "windows-msvc"): ("cfun2", ""),
    ("c_variable", "linux"): ("cvar", ""),
    ("c_variable", "darwin"): ("cvar", ""),
    ("c_variable", "windows-msvc"): ("cvar", ""),
    # A C++ global in the global namespace is unmangled on Itanium, so it is C
    # linkage by the same spelling rule on Linux.
    ("cxx_variable", "linux"): ("cxxvar", ""),
    ("cxx_variable", "darwin"): ("cxxvar", ""),
    ("cxx_variable", "windows-msvc"): ("", "?cxxvar@@3HA"),
}

_SOURCES: dict[str, str] = {
    "c": "void cfun(int x) { (void)x; }\nint cvar = 1;\n",
    "c++": (
        "int cxxfun(int x) { return x; }\n"
        'extern "C" void cfun2(void) {}\n'
        "int cxxvar = 2;\n"
    ),
}


def _node(kind: str, name: str, raw: str, *, asm_label: bool = False) -> dict:
    """A fixture AST node shaped like clang's ``-ast-dump=json`` output."""
    inner: list[dict] = [{"kind": "AsmLabelAttr"}] if asm_label else []
    return {
        "kind": kind,
        "name": name,
        "mangledName": raw,
        "type": {"qualType": "int"},
        "inner": inner,
    }


def _identity(node: dict, triple: str | None, *, is_cxx: bool) -> dict[str, str]:
    """The L4 identity of *node*: ``mangled`` and ``linker`` as the extractor
    records them."""
    name = str(node.get("name", ""))
    mangled = _mangled(node, triple, is_cxx=is_cxx)
    return _entity_names(name, mangled, node, triple, is_cxx=is_cxx)


# -- fixture-AST matrix (default lane) ---------------------------------------


@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("kind", list(KINDS))
def test_fixture_identity_matches_oracle_per_platform(kind: str, target: str) -> None:
    spec = KINDS[kind]
    node = _node(spec["ast"], spec["name"], RAW_MANGLED[(kind, target)])
    names = _identity(node, TARGETS[target], is_cxx=spec["cxx"])
    linker, mangled = ORACLE[(kind, target)]
    assert names.get("linker", "") == linker
    assert names.get("mangled", "") == mangled


# Kinds that are C linkage on every platform: the C++ global is unmangled only
# on Itanium, MSVC mangles it, so it is excluded from the cross-platform check.
_C_LINKAGE_KINDS = [
    kind for kind in KINDS if all(ORACLE[(kind, t)][0] for t in TARGETS)
]


@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("kind", _C_LINKAGE_KINDS)
def test_fixture_c_linkage_linker_key_is_platform_independent(
    kind: str, target: str
) -> None:
    """A C-linkage declaration's ``linker`` key is the same bare name on every
    platform; only the raw ``mangledName`` decoration differs."""
    spec = KINDS[kind]
    node = _node(spec["ast"], spec["name"], RAW_MANGLED[(kind, target)])
    assert (
        _identity(node, TARGETS[target], is_cxx=spec["cxx"]).get("linker")
        == (ORACLE[(kind, "linux")][0])
    )


@pytest.mark.parametrize(
    ("raw", "name", "triple", "is_cxx", "asm_label", "linker", "mangled"),
    [
        # A C++ asm label on Darwin is a distinct identity: not C linkage, and
        # its single-underscore spelling is never stripped.
        ("_foo", "foo", "x86_64-apple-darwin", True, True, "", "_foo"),
        # A plain-C asm label is routine (symbol versioning), not an identity
        # override: it stays C linkage.
        ("_foo", "foo", "x86_64-apple-darwin", False, True, "foo", ""),
        # An explicit `asm("__Zfake")` is reported verbatim, never decoded.
        ("__Zfake", "fake", "x86_64-apple-darwin", True, True, "", "__Zfake"),
        # Off Darwin a `__Z` spelling is not decoration: reported verbatim.
        (
            "__ZN4demo9configureEPvE",
            "configure",
            "x86_64-unknown-linux-gnu",
            True,
            False,
            "",
            "__ZN4demo9configureEPvE",
        ),
        # Off Darwin `_foo` is a real, distinct spelling, not decoration.
        ("_foo", "foo", "x86_64-unknown-linux-gnu", True, False, "", "_foo"),
        # No triple means no evidence: never guess Darwin.
        ("_foo", "foo", None, False, False, "", "_foo"),
    ],
)
def test_fixture_darwin_rule_edge_cases(
    raw: str,
    name: str,
    triple: str | None,
    is_cxx: bool,
    asm_label: bool,
    linker: str,
    mangled: str,
) -> None:
    node = _node("FunctionDecl", name, raw, asm_label=asm_label)
    names = _identity(node, triple, is_cxx=is_cxx)
    assert names.get("linker", "") == linker
    assert names.get("mangled", "") == mangled


def _unit(language: str, triple: str) -> CompileUnit:
    return CompileUnit(
        id="cu://linkage.c#matrix",
        source="linkage.c",
        language=language,
        standard="c11",
        target_triple=triple,
    )


@pytest.mark.parametrize(
    ("language", "triple", "host", "expected"),
    [
        # An explicit triple on the unit always wins.
        (
            "C",
            "x86_64-unknown-linux-gnu",
            "darwin",
            ("x86_64-unknown-linux-gnu", False),
        ),
        # No triple: the dump runs under the host default, Darwin on a Darwin host.
        ("CXX", "", "darwin", ("unknown-apple-darwin", True)),
        # No triple off Darwin: left unknown, never guessed.
        ("C", "", "linux", (None, False)),
    ],
)
def test_dump_linkage_context_resolution(
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    triple: str,
    host: str,
    expected: tuple[str | None, bool],
) -> None:
    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.clang_command.sys.platform", host
    )
    assert dump_linkage_context(_unit(language, triple)) == expected


# -- real clang AST (integration lane) ---------------------------------------

_CLANG = shutil.which("clang")
needs_clang = pytest.mark.skipif(_CLANG is None, reason="clang not on PATH")


def _clang_dump(src: Path, lang_key: str, triple: str) -> dict:
    """The real ``clang -ast-dump=json`` root of *src* under *triple*."""
    assert _CLANG is not None
    proc = subprocess.run(
        [
            _CLANG,
            "-target",
            triple,
            "-x",
            lang_key,
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(proc.stdout)


def _write_source(directory: Path, lang_key: str) -> Path:
    suffix = ".c" if lang_key == "c" else ".cc"
    src = directory / f"src_{lang_key.replace('+', 'x')}{suffix}"
    src.write_text(_SOURCES[lang_key], encoding="utf-8")
    return src


@pytest.fixture(scope="module")
def clang_asts(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict]:
    """The real ``-ast-dump=json`` root for each (target, language) pair."""
    root_dir = tmp_path_factory.mktemp("linkage_matrix")
    out: dict[tuple[str, str], dict] = {}
    for lang_key in _SOURCES:
        src = _write_source(root_dir, lang_key)
        for target, triple in TARGETS.items():
            out[(target, lang_key)] = _clang_dump(src, lang_key, triple)
    return out


def _find_decl(root: dict, name: str) -> dict:
    """The declaration named *name* anywhere in the AST."""
    stack = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("name") == name and node.get("kind") in ("FunctionDecl", "VarDecl"):
            return node
        stack.extend(child for child in node.get("inner", []) or [])
    raise AssertionError(f"no declaration named {name!r} in the AST")


@needs_clang
@pytest.mark.integration
@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("kind", list(KINDS))
def test_clang_ast_identity_matches_oracle_per_platform(
    clang_asts: dict[tuple[str, str], dict], kind: str, target: str
) -> None:
    spec = KINDS[kind]
    root = clang_asts[(target, spec["lang"])]
    node = _find_decl(root, spec["name"])
    # The real AST must carry the spelling the fixture matrix assumes.
    assert node.get("mangledName") == RAW_MANGLED[(kind, target)]
    names = _identity(node, TARGETS[target], is_cxx=spec["cxx"])
    linker, mangled = ORACLE[(kind, target)]
    assert names.get("linker", "") == linker
    assert names.get("mangled", "") == mangled


@needs_clang
@pytest.mark.integration
@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("lang_key", list(_SOURCES))
def test_source_abi_extractor_records_linker_identity_end_to_end(
    tmp_path: Path,
    target: str,
    lang_key: str,
) -> None:
    """The L4 extractor, run on the real AST of its own source, records the same
    linker identity the oracle names for every public declaration of the TU."""
    triple = TARGETS[target]
    src = _write_source(tmp_path, lang_key)
    unit = CompileUnit(
        id=f"cu://{src.name}#matrix",
        source=str(src),
        directory=str(tmp_path),
        argv=[
            "clang++" if lang_key == "c++" else "clang",
            "-c",
            str(src),
        ],
        language="CXX" if lang_key == "c++" else "C",
        standard="c++17" if lang_key == "c++" else "c11",
        target_triple=triple,
    )
    tu = source_abi_from_clang_ast(
        _clang_dump(src, lang_key, triple),
        unit,
        [str(src)],
        "matrix-target",
    )
    entities = {e.qualified_name: e for e in [*tu.functions, *tu.variables]}
    for kind, spec in KINDS.items():
        if spec["lang"] != lang_key:
            continue
        ent = entities[spec["name"]]
        linker, mangled = ORACLE[(kind, target)]
        assert ent.names.get("linker", "") == linker
        assert ent.names.get("mangled", "") == mangled


@needs_clang
@pytest.mark.integration
@pytest.mark.parametrize("target", list(TARGETS))
@pytest.mark.parametrize("lang_key", list(_SOURCES))
def test_header_and_source_backends_agree_on_every_node(
    clang_asts: dict[tuple[str, str], dict], target: str, lang_key: str
) -> None:
    """Both backends read the same AST nodes and must agree on each one.

    For a C-linkage declaration the header backend's ``Function.mangled``
    (decoration-stripped) and its ``is_extern_c`` must equal the L4
    extractor's ``linker`` identity. For a C++ declaration they must equal the
    L4 ``mangled``. Checked for functions and variables.
    """
    triple = TARGETS[target]
    is_cxx = lang_key == "c++"
    root = clang_asts[(target, lang_key)]
    parser = _ClangAstParser(root, set(), set(), target_triple=triple, is_cxx=is_cxx)
    header_fns = {fn.name: fn for fn in parser.parse_functions()}
    header_vars = {var.name: var for var in parser.parse_variables()}
    checked = 0
    for kind, spec in KINDS.items():
        if spec["lang"] != lang_key:
            continue
        node = _find_decl(root, spec["name"])
        names = _identity(node, triple, is_cxx=is_cxx)
        expected = names.get("linker") or names.get("mangled", "")
        if spec["ast"] == "FunctionDecl":
            header = header_fns.get(spec["name"])
            assert header is not None, f"header backend did not parse {kind}"
            # The two backends agree on C linkage, not only on the spelling.
            assert header.is_extern_c == ("linker" in names), kind
        else:
            header = header_vars.get(spec["name"])
            assert header is not None, f"header backend did not parse {kind}"
        assert header.mangled == expected, kind
        checked += 1
    assert checked == sum(1 for s in KINDS.values() if s["lang"] == lang_key)
