# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D15's producer side: a PE dump whose located PDB fails to parse
records its ``debug`` section as ``failed`` with the diagnostic -- never an
empty present section -- while an absent PDB stays merely not collected.

Driven through the PE adapter with the platform parsers substituted (their
owners are ``pe_metadata``/``pdb_metadata``, not the module under test), then
through save -> load -> ``compare``: the stored and the live snapshot block
the same readers, and the removal of a function is still reported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import abicheck.pdb_metadata as pdb_metadata
import abicheck.pdb_utils as pdb_utils
import abicheck.pe_metadata as pe_metadata
from abicheck.checker import compare
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.workflows.dump.pe import extract_pe

#: PDB outcome -> (whether `debug` must be failed, the diagnostic needle).
_OUTCOMES = {
    "absent": (False, None),
    "lookup_error": (False, None),
    "parse_error": (True, "bad stream 7"),
    "parse_crash": (True, "unexpected end"),
    "parsed": (False, None),
}


def _dump(monkeypatch, tmp_path: Path, outcome: str, exports: list[str]):
    dll = tmp_path / "x.dll"
    dll.write_bytes(b"MZ")
    pdb = tmp_path / "x.pdb"
    monkeypatch.setattr(
        pe_metadata,
        "parse_pe_metadata",
        lambda path: PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(exports)],
        ),
    )

    def locate(path, *, pdb_path_override, allow_network):
        if outcome == "lookup_error":
            raise RuntimeError("no pdb support on this host")
        return None if outcome == "absent" else pdb

    def parse(path):
        if outcome == "parse_error":
            raise ValueError("bad stream 7")
        if outcome == "parse_crash":
            raise EOFError("unexpected end")
        return None, None

    monkeypatch.setattr(pdb_utils, "locate_pdb", locate)
    monkeypatch.setattr(pdb_metadata, "parse_pdb_debug_info", parse)
    return extract_pe(dll, "1.0")


@pytest.mark.parametrize("outcome", sorted(_OUTCOMES))
def test_only_a_located_pdb_that_fails_marks_debug_failed(
    monkeypatch, tmp_path: Path, outcome: str
) -> None:
    failed, needle = _OUTCOMES[outcome]
    live = _dump(monkeypatch, tmp_path, outcome, ["f"])
    written = json.loads(snapshot_to_json(live))
    status = written["section_availability"]["debug"]["status"]
    assert status == ("failed" if failed else "present")
    assert ("debug" in written["sections"]) is not failed
    reloaded = snapshot_from_dict(written)
    for snap in (live, reloaded):
        assert [k for k, _ in snap.unreadable_sections] == (["debug"] if failed else [])
        if failed:
            assert needle in snap.unreadable_sections[0][1]
    # Re-save is stable.
    assert json.loads(snapshot_to_json(reloaded)) == written


@pytest.mark.parametrize("stored", [False, True], ids=["live", "stored"])
def test_a_failed_pdb_blocks_debug_readers_and_still_reports_removal(
    monkeypatch, tmp_path: Path, stored: bool
) -> None:
    old = _dump(monkeypatch, tmp_path, "parsed", ["f", "g"])
    new = _dump(monkeypatch, tmp_path, "parse_error", ["f"])
    if stored:
        old = snapshot_from_dict(json.loads(snapshot_to_json(old)))
        new = snapshot_from_dict(json.loads(snapshot_to_json(new)))
    result = compare(old, new)
    blocked = {
        r.name
        for r in result.detector_results
        if (r.coverage_gap or "").startswith("not comparable")
    }
    assert blocked == {n for n, reads in DETECTOR_SECTIONS.items() if "debug" in reads}
    removed = [c.symbol for c in result.changes if c.kind.value == "func_removed"]
    assert removed == ["g"]
