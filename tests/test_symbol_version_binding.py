"""A symbol-version transition is a break exactly when an old reference stops resolving.

A consumer linked against the old library records ``(symbol, version)`` for
every versioned symbol it uses: the symbol's default (``@@``) version at link
time. The dynamic loader binds that reference only if the new library still
defines the symbol at that version, as its default or as a retained
non-default alias (``.symver``). The oracle below is that loader rule, written
independently of abicheck's detectors; ``compare``'s verdict must agree with
it over every small version layout, and the node-move finding must appear
exactly when a reference is left unresolvable.

The integration test builds the two real shapes with gcc and runs the
consumer, so the oracle itself is checked against the loader.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot

NODES = ("LIBX_1.0", "LIBX_2.0")


def _sym(name: str, version: str, default: bool) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        binding=SymbolBinding.GLOBAL,
        sym_type=SymbolType.FUNC,
        size=8,
        version=version,
        is_default=default,
    )


def _snap(symbols: list[ElfSymbol]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="v",
        functions=[],
        variables=[],
        types=[],
        enums=[],
        typedefs={},
        elf=ElfMetadata(
            soname="libx.so.1", versions_defined=list(NODES), symbols=symbols
        ),
    )


def _loader_resolves(ref: tuple[str, str], new: list[ElfSymbol]) -> bool:
    """The glibc rule: a versioned reference binds a definition at that version."""
    name, version = ref
    return any(s.name == name and s.version == version for s in new)


# One symbol ``foo`` (always present, so no removal muddies the verdict) plus a
# stable anchor ``bar@@LIBX_1.0`` that keeps both nodes populated (an emptied
# node is a different, separately-reported change). The new side exports foo
# at a non-empty subset of the nodes, one of them the default.
_LAYOUTS = [
    (old_default, new_versions, new_default)
    for old_default in NODES
    for r in (1, 2)
    for new_versions in itertools.combinations(NODES, r)
    for new_default in new_versions
]


def _side(foo_versions: tuple[str, ...], foo_default: str) -> list[ElfSymbol]:
    return [
        _sym("bar", "LIBX_1.0", True),
        _sym("bar2", "LIBX_2.0", True),
        *(_sym("foo", v, v == foo_default) for v in foo_versions),
    ]


@pytest.mark.parametrize("old_default,new_versions,new_default", _LAYOUTS)
def test_verdict_follows_whether_old_references_resolve(
    old_default: str, new_versions: tuple[str, ...], new_default: str
) -> None:
    old = _side((old_default,), old_default)
    new = _side(new_versions, new_default)
    refs = [(s.name, s.version) for s in old if s.is_default]
    breaks = not all(_loader_resolves(r, new) for r in refs)

    result = compare(_snap(old), _snap(new))
    kinds = {c.kind for c in result.changes}

    assert (result.verdict == Verdict.BREAKING) is breaks, (
        f"{old_default} -> {new_versions} (default {new_default}): "
        f"verdict {result.verdict.name}, kinds {sorted(k.value for k in kinds)}"
    )
    assert (ChangeKind.SYMBOL_MOVED_VERSION_NODE in kinds) is breaks
    if new_default != old_default and not breaks:
        # The kept old version makes the default change a compatible alias change.
        assert ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED in kinds


def test_layout_domain_has_both_outcomes() -> None:
    outcomes = {
        all(
            _loader_resolves((s.name, s.version), _side(nv, nd))
            for s in _side((od,), od)
            if s.is_default
        )
        for od, nv, nd in _LAYOUTS
    }
    assert outcomes == {True, False}


# --- real binaries --------------------------------------------------------------

_SRC = "int f(int a) { return a; }\nint g(int a) { return a + 1; }\n"
_MAIN = '#include <stdio.h>\nint g(int);\nint main(void) { printf("%d\\n", g(1)); return 0; }\n'
_RETAINED_SRC = (
    "int f(int a) { return a; }\n"
    "int g_v1(int a) { return a + 1; }\n"
    "int g_v2(int a) { return a + 1; }\n"
    '__asm__(".symver g_v1, g@LIBEDIT_1.0");\n'
    '__asm__(".symver g_v2, g@@LIBEDIT_1.1");\n'
)
_OLD_MAP = "LIBEDIT_1.0 { global: f; g; local: *; };\n"
_NEW_MAP = (
    "LIBEDIT_1.0 { global: f; local: *; };\nLIBEDIT_1.1 { global: g; } LIBEDIT_1.0;\n"
)
# The versioned names come from the .symver directives; the script only says
# which version nodes exist and that the implementation names stay local.
_NEW_MAP_RETAINED = (
    "LIBEDIT_1.0 { global: f; g; local: *; };\n"
    "LIBEDIT_1.1 { global: g; } LIBEDIT_1.0;\n"
)


def _lib(d: Path, src: str, vmap: str) -> Path:
    d.mkdir(parents=True)
    (d / "s.c").write_text(src, encoding="utf-8")
    (d / "v.map").write_text(vmap, encoding="utf-8")
    so = d / "libedit.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(d / "s.c"),
            "-Wl,-soname,libedit.so",
            f"-Wl,--version-script={d / 'v.map'}",
        ],
        check=True,
    )
    return so


@pytest.mark.integration
@pytest.mark.parametrize(
    "new_src,new_map,loads",
    [(_SRC, _NEW_MAP, False), (_RETAINED_SRC, _NEW_MAP_RETAINED, True)],
    ids=["moved-old-version-dropped", "moved-old-version-retained"],
)
def test_real_loader_and_compare_agree(
    tmp_path: Path, new_src: str, new_map: str, loads: bool
) -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc is not installed")
    from abicheck.dumper import dump

    old_so = _lib(tmp_path / "old", _SRC, _OLD_MAP)
    new_so = _lib(tmp_path / "new", new_src, new_map)
    (tmp_path / "m.c").write_text(_MAIN, encoding="utf-8")
    exe = tmp_path / "m"
    subprocess.run(
        [
            "gcc",
            "-o",
            str(exe),
            str(tmp_path / "m.c"),
            "-L",
            str(old_so.parent),
            "-ledit",
        ],
        check=True,
    )
    run = subprocess.run(
        [str(exe)],
        env={"LD_LIBRARY_PATH": str(new_so.parent), "LD_BIND_NOW": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (run.returncode == 0) is loads, run.stderr  # the oracle, against the loader
    result = compare(dump(old_so, [], version="old"), dump(new_so, [], version="new"))
    assert (result.verdict == Verdict.BREAKING) is (not loads), (
        result.verdict,
        sorted(c.kind.value for c in result.changes),
    )
