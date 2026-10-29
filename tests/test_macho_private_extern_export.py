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
