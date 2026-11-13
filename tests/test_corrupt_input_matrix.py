# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Corrupt input never reads as a clean comparison, through the public CLI.

Bug class: a parser that meets a damaged input returns whatever it managed
to read, and a dump built on that is a smaller surface rather than an error.
A PE whose export names were cut read as one export instead of three, a
Mach-O cut inside its string table as none, a flipped ``.debug_abbrev`` byte
as "no DWARF" -- and a compare of two identically damaged copies reported
``NO_CHANGE`` (exit 0), while a damaged OLD side read the lost types as
added (``COMPATIBLE``, exit 0).

The matrix is format {ELF, PE, Mach-O, stored snapshot} x corruption (bad
magic, zero length, cuts in the header, section/load-command table, string
table, symbol table, export directory/trie and DWARF, a corrupt DWARF
abbreviation table, random bytes after a valid header, and malformed
storage-v2 structure) x side {old, new, both} x mode {``compare`` of two
files, ``compare`` of two release directories, ``dump``}. The invariant, per
cell:

* never exit 0, never a ``COMPATIBLE``/``NO_CHANGE`` verdict;
* an explicit failure: an ``Error:`` line (scalar compare, dump) or the
  member reported ``ERROR`` (directory compare), never a silent smaller
  surface;
* ``dump`` writes no snapshot;
* no traceback and no exception other than the command's own exit.

The intact seeds are run first, through the same modes, and must come out
clean -- so a broken fixture cannot make a cell pass for the wrong reason.

Every cell runs in-process (Click's runner) in about 25 ms, so the whole
matrix is a default-lane test. The ELF cells need gcc linking ELF and skip
otherwise (the Linux unit lanes have it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from tests._corrupt_input_matrix import (
    CLEAN_VERDICTS,
    FILE_NAMES,
    FORMATS,
    MODES,
    NOT_APPLICABLE,
    Cell,
    CellResult,
    Seeds,
    all_cells,
    build_seeds,
    failed_members,
    run_cell,
)

CELLS = list(all_cells())


@pytest.fixture(scope="module")
def seeds(tmp_path_factory: pytest.TempPathFactory) -> Seeds:
    return build_seeds(tmp_path_factory.mktemp("corrupt-matrix-seeds"))


def _invoke(argv: list[str]) -> Any:
    return CliRunner().invoke(main, argv)


def _require(seeds: Seeds, fmt: str) -> None:
    if seeds.get(fmt) is None:
        pytest.skip(seeds.elf_skip)


def _run(cell: Cell, seeds: Seeds, tmp_path: Path) -> CellResult:
    _require(seeds, cell.fmt)
    return run_cell(cell, seeds, tmp_path, _invoke)


def test_matrix_covers_every_axis() -> None:
    """Every format, side and mode appears; only the documented pairs are
    left out (a stored snapshot is not a directory member nor a dump input)."""
    assert {c.fmt for c in CELLS} == set(FORMATS)
    assert {c.mode for c in CELLS} == set(MODES)
    assert {c.side for c in CELLS if c.mode != "dump"} == {"old", "new", "both"}
    assert {(c.fmt, c.mode) for c in CELLS}.isdisjoint(NOT_APPLICABLE)
    for fmt in ("elf", "pe", "macho"):
        cuts = {c.corruption for c in CELLS if c.fmt == fmt}
        assert {"bad_magic", "zero_length", "cut_header", "random_after_header"} <= cuts


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_intact_seed_is_clean(
    fmt: str, mode: str, seeds: Seeds, tmp_path: Path
) -> None:
    """The control: the uncorrupted seed through the same mode is clean."""
    if (fmt, mode) in NOT_APPLICABLE:
        pytest.skip(f"{fmt} has no {mode} mode")
    _require(seeds, fmt)
    seed = seeds.get(fmt)
    assert seed is not None
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / FILE_NAMES[fmt]).write_bytes(seed)
    (new / FILE_NAMES[fmt]).write_bytes(seed)
    if mode == "dump":
        out = tmp_path / "dumped.json"
        result = _invoke(["dump", str(old / FILE_NAMES[fmt]), "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.stat().st_size > 0
        return
    if mode == "compare":
        argv = ["compare", str(old / FILE_NAMES[fmt]), str(new / FILE_NAMES[fmt])]
    else:
        argv = ["compare", str(old), str(new)]
    result = _invoke(argv)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert failed_members(result.output) == []
    # Binary-only PE/Mach-O pairs may read UNCONFIRMED (exit 1: no type-level
    # evidence); what matters is that no member failed and nothing errored.
    assert "Error:" not in result.output, result.output
    assert result.exit_code in (0, 1), result.output


def _assert_refused(cell: Cell, res: CellResult) -> None:
    where = f"{cell.id}: exit {res.exit_code}, {res.summary}"
    assert res.exception is None, f"{where}\n{res.output}"
    assert "Traceback (most recent call last)" not in res.output, where
    assert res.exit_code != 0, where
    assert not any(word in res.verdict for word in CLEAN_VERDICTS), where
    if cell.mode == "directory":
        # A failed member floors the release at exit 4 (ERROR), and the
        # report names it rather than reading it as unmatched or clean.
        assert failed_members(res.output) == [FILE_NAMES[cell.fmt]], where
        assert res.exit_code == 4, where
    else:
        # A refused input: exit 1 (unreadable) or 64 (format not detected),
        # with the error stated.
        assert res.error, where
        assert res.exit_code in (1, 64), where
        assert res.verdict == "", where
    if cell.mode == "dump":
        assert res.dumped is None, f"{where}: dump wrote a snapshot of a corrupt input"


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.id)
def test_corrupt_input_is_refused(cell: Cell, seeds: Seeds, tmp_path: Path) -> None:
    _assert_refused(cell, _run(cell, seeds, tmp_path))
