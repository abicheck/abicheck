# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The three PDB outcomes of ``extract_pdb_debug_outcome``, with the lookup and
parser stubbed: a lookup that raises, no PDB at all, and a PDB that fails to
parse. Each is checked against its contract (ADR-062 D15): a raised lookup and a
failed parse are reported as a failure string; no PDB is absent, not failed."""

from __future__ import annotations

from pathlib import Path

import abicheck.pdb_metadata as pdb_metadata
import abicheck.pdb_utils as pdb_utils
from abicheck.workflows.dump import pe as pe_dump

LIB = Path("/fixture/libk.dll")


def test_a_raising_pdb_lookup_is_a_failure_not_an_absence(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("symbol server unreachable")

    monkeypatch.setattr(pdb_utils, "locate_pdb", boom)
    meta, adv, failure = pe_dump.extract_pdb_debug_outcome(LIB, None)
    assert (meta, adv) == (None, None)
    assert failure == "pdb lookup: libk.dll: symbol server unreachable"


def test_no_pdb_is_absent_and_not_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(pdb_utils, "locate_pdb", lambda *_a, **_k: None)
    assert pe_dump.extract_pdb_debug_outcome(LIB, None) == (None, None, None)
    assert pe_dump.pe_pdb_debug_kwargs(LIB) == {}


def test_a_pdb_that_fails_to_parse_is_a_failure(monkeypatch, tmp_path: Path) -> None:
    pdb = tmp_path / "libk.pdb"
    pdb.write_bytes(b"not a pdb")
    monkeypatch.setattr(pdb_utils, "locate_pdb", lambda *_a, **_k: pdb)

    def bad(_path):
        raise ValueError("bad magic")

    monkeypatch.setattr(pdb_metadata, "parse_pdb_debug_info", bad)
    meta, adv, failure = pe_dump.extract_pdb_debug_outcome(LIB, None)
    assert (meta, adv) == (None, None)
    assert failure == "pdb: libk.pdb: bad magic"
    # The keyword helper drops the failure string but never invents debug inputs.
    assert pe_dump.pe_pdb_debug_kwargs(LIB) == {}
