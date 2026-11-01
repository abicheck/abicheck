# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Every blocked section x every cause x side x operand form, through
``abicheck compare`` (ADR-062 D14/D15).

Exhaustive over a small domain: for each section a stored snapshot can block
(all but the identity section), each cause (an unknown must-understand
name, a newer section version, a failed extraction), the side carrying it,
and the operand form (single-file document or ProjectSnapshot package):

* exactly the detectors whose declared reads include that section are not
  comparable -- the oracle is ``DETECTOR_SECTIONS`` checked against an AST
  scan of each detector (``test_section_readers_contract.py``), not the
  registry's own decision;
* the removed function ``g`` is reported exactly once, as ``func_removed``,
  unless the section blocked is one presence itself reads (``binary`` or
  ``declarations``), where nothing can establish it -- never twice (the
  ``functions`` and ``function_presence`` detectors never both report it)
  and never from defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import Function, RecordType, TypeField
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_json

_SECTIONS = ("binary", "build", "debug", "declarations", "layout", "types")
_CAUSES = ("must_understand", "newer_version", "failed")
#: The sections `function_presence` reads -- written out, not imported.
_PRESENCE_READS = {"binary", "declarations"}


def _snap(version: str, symbols: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libm.so.1",
        version=version,
        functions=[Function(name=n, mangled=n, return_type="int") for n in symbols],
        types=[
            RecordType(
                name="T",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            )
        ],
        elf=ElfMetadata(
            soname="libm.so.1", symbols=[ElfSymbol(name=n) for n in symbols]
        ),
    )


def _block(document: dict, section: str, cause: str) -> None:
    if cause == "must_understand":
        document["sections"][section]["must_understand"] = ["vendor.never"]
    elif cause == "newer_version":
        document["sections"][section]["section_schema_version"] += 1
        document["section_schema_versions"][section] += 1
    else:
        document["sections"].pop(section)
        document["section_availability"][section] = {
            "diagnostics": ["extractor crashed"],
            "status": "failed",
        }


def _operand(tmp_path: Path, name: str, document: dict, form: str) -> Path:
    if form == "file":
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path
    path = tmp_path / name
    write_legacy_snapshot_package(
        snapshot_from_dict(document),
        path,
        artifact_id="libm",
        max_known_schema_version=SCHEMA_VERSION,
    )
    return path


@pytest.mark.parametrize("form", ["file", "package"])
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_a_blocked_section_blocks_exactly_its_readers(
    tmp_path: Path, section: str, cause: str, side: str, form: str
) -> None:
    documents = {
        "old": json.loads(snapshot_to_json(_snap("1", ["f", "g"]))),
        "new": json.loads(snapshot_to_json(_snap("2", ["f"]))),
    }
    _block(documents[side], section, cause)
    old = _operand(tmp_path, "old", documents["old"], form)
    new = _operand(tmp_path, "new", documents["new"], form)
    report_path = tmp_path / "r.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={report_path}"]
    )
    report = json.loads(report_path.read_text())
    blocked = {
        d["name"]
        for d in report["detectors"]
        if (d.get("coverage_gap") or "").startswith("not comparable")
    }
    assert blocked == {n for n, reads in DETECTOR_SECTIONS.items() if section in reads}
    removals = [c for c in report["changes"] if c["kind"] == "func_removed"]
    if section in _PRESENCE_READS:
        assert removals == []
        assert result.exit_code in (0, 1), result.output
    else:
        assert [c["symbol"] for c in removals] == ["g"]
        assert result.exit_code == 4, result.output
    assert report["analysis_assurance"]["status"] == "partial"
