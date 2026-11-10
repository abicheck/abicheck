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
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from tests._abicheck_subprocess import abicheck_command, abicheck_env

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


# One symbol ``foo`` (always present, so no removal muddies the verdict) plus
# stable anchors ``bar@@LIBX_1.0`` and ``bar2@@LIBX_2.0`` that keep both nodes
# populated (an emptied node is a different, separately-reported change).
# Each side exports foo at a non-empty subset of the nodes, one of them the
# default; the others are non-default (compat) versions.
_SIDES = [
    (versions, default)
    for r in (1, 2)
    for versions in itertools.combinations(NODES, r)
    for default in versions
]
_LAYOUTS = [(old, new) for old in _SIDES for new in _SIDES]


def _side(foo_versions: tuple[str, ...], foo_default: str) -> list[ElfSymbol]:
    return [
        _sym("bar", "LIBX_1.0", True),
        _sym("bar2", "LIBX_2.0", True),
        *(_sym("foo", v, v == foo_default) for v in foo_versions),
    ]


@pytest.mark.parametrize("old_side,new_side", _LAYOUTS, ids=str)
def test_verdict_follows_whether_old_references_resolve(
    old_side: tuple[tuple[str, ...], str], new_side: tuple[tuple[str, ...], str]
) -> None:
    old, new = _side(*old_side), _side(*new_side)
    # Every version the old library defined: the default is what a new link
    # records, a non-default one what a consumer of an older release recorded.
    refs = [(s.name, s.version) for s in old]
    breaks = not all(_loader_resolves(r, new) for r in refs)

    result = compare(_snap(old), _snap(new))
    kinds = {c.kind for c in result.changes}

    assert (result.verdict == Verdict.BREAKING) is breaks, (
        f"{old_side} -> {new_side}: "
        f"verdict {result.verdict.name}, kinds {sorted(k.value for k in kinds)}"
    )
    assert (ChangeKind.SYMBOL_MOVED_VERSION_NODE in kinds) is breaks
    if new_side[1] != old_side[1] and not breaks:
        # Every old version kept: the default change is a compatible alias change.
        assert ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED in kinds


def test_layout_domain_has_both_outcomes() -> None:
    outcomes = {
        all(_loader_resolves((s.name, s.version), _side(*n)) for s in _side(*o))
        for o, n in _LAYOUTS
    }
    assert outcomes == {True, False}


@pytest.mark.parametrize(
    "name,old_node,demoted",
    [
        # A public symbol whose *name* looks private: the node decides, not the name.
        ("private_data", "LIBX_1.0", False),
        ("x_INTERNAL_y", "LIBX_1.0", False),
        # A symbol bound only to a private node: not public ABI, demoted to a risk.
        ("helper", "LIBX_PRIVATE", True),
    ],
)
def test_a_lost_version_is_demoted_only_for_a_symbol_on_a_private_node(
    name: str, old_node: str, demoted: bool
) -> None:
    nodes = ["LIBX_1.0", "LIBX_2.0", "LIBX_PRIVATE"]

    def snap(node: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="libx.so.1",
            version="v",
            functions=[],
            variables=[],
            types=[],
            enums=[],
            typedefs={},
            elf=ElfMetadata(
                soname="libx.so.1",
                versions_defined=nodes,
                symbols=[
                    _sym("bar", "LIBX_1.0", True),
                    _sym("bar2", "LIBX_2.0", True),
                    _sym("baz", "LIBX_PRIVATE", True),
                    _sym(name, node, True),
                ],
            ),
        )

    result = compare(snap(old_node), snap("LIBX_2.0"))
    moved = [
        c for c in result.changes if c.kind == ChangeKind.SYMBOL_MOVED_VERSION_NODE
    ]
    assert [c.symbol for c in moved] == [name]
    assert (moved[0].effective_verdict == Verdict.COMPATIBLE_WITH_RISK) is demoted
    assert (result.verdict == Verdict.BREAKING) is (not demoted)


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
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="links an ELF .so with a GNU version script and runs glibc's loader",
)
@pytest.mark.parametrize(
    "new_src,new_map,loads",
    [(_SRC, _NEW_MAP, False), (_RETAINED_SRC, _NEW_MAP_RETAINED, True)],
    ids=["moved-old-version-dropped", "moved-old-version-retained"],
)
def test_real_loader_and_compare_agree(
    tmp_path: Path, new_src: str, new_map: str, loads: bool
) -> None:
    # gcc and Linux are present (the skipifs above): a failing build is a failure.
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
    kinds = {c.kind for c in result.changes}
    assert (result.verdict == Verdict.BREAKING) is (not loads), (
        result.verdict,
        sorted(k.value for k in kinds),
    )
    assert (ChangeKind.SYMBOL_MOVED_VERSION_NODE in kinds) is (not loads)
    assert (ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED in kinds) is loads
    # The public workflow: `abicheck compare` exits 4 (ABI break) exactly when the
    # loader fails. A binary-only run without debug info is UNCONFIRMED (ADR-090),
    # which may raise a passing exit to 1, never to 4.
    cli = subprocess.run(
        abicheck_command("compare", old_so, new_so),
        env=abicheck_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert (cli.returncode == 4) is (not loads), cli.stdout[-2000:] + cli.stderr[-2000:]
