# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A Mach-O private extern is linked into the image but is not an export.

The nlist of a dylib can keep ``N_PEXT`` alongside ``N_EXT`` (a private
extern: an external the linker was told to keep local to the image). The dyld
export trie never lists such a symbol, so the export surface must not either,
just as an ELF ``STV_HIDDEN`` symbol is not in the dynamic export surface.
Bug class ``symbol_rule.single_object_format``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.macho_metadata import parse_macho_metadata
from abicheck.serialization import load_snapshot
from tests._macho_synthetic import (
    EXPORT_WEAK_DEFINITION,
    N_EXT,
    N_PEXT,
    N_SECT,
    N_UNDF,
    N_WEAK_DEF,
    MachoSymbol,
    write_dylib,
)

# (n_type, n_desc) -> whether a consumer can bind to the symbol. The oracle is
# the Mach-O convention, not the parser: N_EXT defined and not N_PEXT is an
# export; N_PEXT is linked-in but private; no N_EXT is a local.
_FLAGS = [
    pytest.param(N_SECT | N_EXT, 0, True, False, id="global-export"),
    pytest.param(N_SECT | N_EXT, N_WEAK_DEF, True, True, id="weak-export"),
    pytest.param(N_SECT | N_EXT | N_PEXT, 0, False, False, id="private-extern"),
    pytest.param(
        N_SECT | N_EXT | N_PEXT, N_WEAK_DEF, False, False, id="private-extern-weak"
    ),
    pytest.param(N_SECT, 0, False, False, id="local"),
]


@pytest.mark.parametrize(("n_type", "n_desc", "exported", "weak"), _FLAGS)
def test_only_non_private_defined_externals_are_exports(
    tmp_path: Path, n_type: int, n_desc: int, exported: bool, weak: bool
) -> None:
    lib = write_dylib(
        tmp_path / "libflag.dylib",
        [
            MachoSymbol("_anchor", N_SECT | N_EXT),
            MachoSymbol("_probe", n_type, n_desc=n_desc),
        ],
    )
    meta = parse_macho_metadata(lib)
    by_name = {e.name: e for e in meta.exports}
    assert ("probe" in by_name) is exported
    if exported:
        assert by_name["probe"].is_weak is weak


def test_private_extern_is_not_an_export_but_its_import_neighbour_still_is(
    tmp_path: Path,
) -> None:
    lib = write_dylib(
        tmp_path / "libmix.dylib",
        [
            MachoSymbol("_public", N_SECT | N_EXT),
            MachoSymbol("_hidden_helper", N_SECT | N_EXT | N_PEXT),
            MachoSymbol("_needed", N_UNDF | N_EXT),
        ],
    )
    meta = parse_macho_metadata(lib)
    assert [e.name for e in meta.exports] == ["public"]
    # The undefined external is still what this dylib requires.
    assert meta.imported_symbols == ["needed"]


def test_dump_cli_omits_the_private_extern_from_the_snapshot(tmp_path: Path) -> None:
    lib = write_dylib(
        tmp_path / "libcli.dylib",
        [
            MachoSymbol("_public_api", N_SECT | N_EXT),
            MachoSymbol("_private_api", N_SECT | N_EXT | N_PEXT),
        ],
    )
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(main, ["dump", str(lib), "-o", str(out)])
    assert result.exit_code == 0, result.output
    snap = load_snapshot(out)
    assert [f.name for f in snap.declarations.functions] == ["public_api"]


# -- The dyld export trie is the export table when the image has one ---------
#
# dyld binds only what the export trie lists. An nlist entry the trie omits is
# not an export (a stale or kept-local external), and a trie entry the nlist
# lacks (a stripped symbol table) is one. Without a trie the nlist stands alone.

_NLIST = [
    MachoSymbol("_public", N_SECT | N_EXT),
    MachoSymbol("_weak", N_SECT | N_EXT, n_desc=N_WEAK_DEF),
    MachoSymbol("_nlist_only", N_SECT | N_EXT),  # defined external, not in trie
    MachoSymbol("_private", N_SECT | N_EXT | N_PEXT),
]
_TRIE = [
    ("_public", 0),
    ("_weak", EXPORT_WEAK_DEFINITION),
    ("_trie_only", 0),
]


def test_trie_lists_the_exports_and_a_nlist_only_external_is_dropped(
    tmp_path: Path,
) -> None:
    lib = write_dylib(tmp_path / "libtrie.dylib", _NLIST, export_trie=_TRIE)
    by_name = {e.name: e for e in parse_macho_metadata(lib).exports}
    assert set(by_name) == {"public", "weak", "trie_only"}
    assert by_name["weak"].is_weak is True
    assert by_name["public"].is_weak is False


def test_without_a_trie_the_nlist_is_the_export_table(tmp_path: Path) -> None:
    lib = write_dylib(tmp_path / "libnotrie.dylib", _NLIST)
    names = {e.name for e in parse_macho_metadata(lib).exports}
    assert names == {"public", "weak", "nlist_only"}


@pytest.mark.parametrize(
    ("nlist_weak", "trie_flags", "expected_weak"),
    [
        pytest.param(True, 0, False, id="nlist-weak-trie-strong"),
        pytest.param(False, EXPORT_WEAK_DEFINITION, True, id="nlist-strong-trie-weak"),
    ],
)
def test_trie_flags_decide_linkage_over_the_nlist(
    tmp_path: Path, nlist_weak: bool, trie_flags: int, expected_weak: bool
) -> None:
    lib = write_dylib(
        tmp_path / "liblink.dylib",
        [MachoSymbol("_probe", N_SECT | N_EXT, n_desc=N_WEAK_DEF if nlist_weak else 0)],
        export_trie=[("_probe", trie_flags)],
    )
    (probe,) = parse_macho_metadata(lib).exports
    assert probe.name == "probe"
    assert probe.is_weak is expected_weak


def test_dump_cli_exports_follow_the_trie(tmp_path: Path) -> None:
    lib = write_dylib(tmp_path / "libclitrie.dylib", _NLIST, export_trie=_TRIE)
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(main, ["dump", str(lib), "-o", str(out)])
    assert result.exit_code == 0, result.output
    snap = load_snapshot(out)
    assert {f.name for f in snap.declarations.functions} == {
        "public",
        "weak",
        "trie_only",
    }
