# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-062 D14/D15 through the public workflow: ``abicheck compare`` on two
stored snapshots where one carries a section this build cannot use.

The analyses reading that section resolve to not comparable (a detector
``coverage_gap`` naming the extension, rendered in the Markdown report);
the others still produce findings; ``analysis_assurance`` reads
``partial``; the exit code follows ``docs/reference/exit-codes.md`` -- the
verdict's code, raised to 1 only under ``assurance.require_complete``.
Bug class ``storage.wire_extension_dropped_or_refused``.
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
from abicheck.serialization import snapshot_to_json


def _snap(
    version: str, symbols: list[str], size_bits: int, needed: tuple[str, ...] = ()
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version=version,
        functions=[Function(name=n, mangled=n, return_type="int") for n in symbols],
        types=[
            RecordType(
                name="T",
                kind="struct",
                size_bits=size_bits,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            )
        ],
        elf=ElfMetadata(
            soname="libx.so.1",
            needed=list(needed),
            symbols=[ElfSymbol(name=n) for n in symbols],
        ),
    )


def _pair(tmp_path: Path, mutate) -> tuple[Path, Path]:
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(snapshot_to_json(_snap("1", ["f", "g"], 32)), encoding="utf-8")
    document = json.loads(snapshot_to_json(_snap("2", ["f"], 64, ("libdep.so.1",))))
    mutate(document)
    new.write_text(json.dumps(document), encoding="utf-8")
    return old, new


def _must_understand(section: str):
    def mutate(document: dict) -> None:
        document["sections"][section]["must_understand"] = ["x_layout_v2"]

    return mutate


def _failed(section: str):
    def mutate(document: dict) -> None:
        document["sections"].pop(section)
        document["section_availability"][section] = {
            "diagnostics": ["castxml crashed"],
            "status": "failed",
        }

    return mutate


def _compare(tmp_path: Path, mutate, *extra: str):
    old, new = _pair(tmp_path, mutate)
    report = tmp_path / "r.json"
    md = tmp_path / "r.md"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "-o",
            f"json={report}",
            "-o",
            f"markdown={md}",
            *extra,
        ],
    )
    return result, json.loads(report.read_text()), md.read_text()


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [(_must_understand("types"), "x_layout_v2"), (_failed("types"), "castxml crashed")],
    ids=["must-understand", "failed"],
)
def test_blocked_types_section_blocks_only_its_readers(
    tmp_path: Path, mutate, needle: str
) -> None:
    result, report, md = _compare(tmp_path, mutate)
    blocked = {
        d["name"]
        for d in report["detectors"]
        if "not comparable" in (d.get("coverage_gap") or "")
    }
    assert blocked == {n for n, reads in DETECTOR_SECTIONS.items() if "types" in reads}
    assert all(
        needle in (d.get("coverage_gap") or "")
        for d in report["detectors"]
        if d["name"] in blocked
    )
    kinds = {c["kind"] for c in report["changes"]}
    # The type grew 32 -> 64 bits, but its section was not comparable: no
    # type finding, and no false shrink of the public surface from defaults.
    assert not any(k.startswith("type_") for k in kinds)
    assert "public_surface_shrank" not in kinds
    # `func_removed` comes from the `functions` detector, which reads
    # `types` too, so it is not comparable either; a detector that reads only
    # the binary section still runs and reports the new DT_NEEDED entry.
    assert "func_removed" not in kinds
    assert "needed_added" in kinds, kinds
    assert report["analysis_assurance"]["status"] == "partial"
    assert any("'types'" in n for n in report["analysis_assurance"]["notes"])
    assert needle in md
    # `needed_added` is compatible and a blocked section never fabricates a
    # break: exit 0 under the default policy (exit-codes.md P0.4).
    assert result.exit_code == 0, result.output


def test_a_clean_pair_with_a_blocked_section_exits_by_policy(tmp_path: Path) -> None:
    """Nothing else changed: the verdict's exit is 0, and
    `assurance.require_complete` raises it to 1 (exit-codes.md P0.4)."""

    def same(document: dict) -> None:
        document["sections"]["types"]["must_understand"] = ["x_layout_v2"]

    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(snapshot_to_json(_snap("1", ["f"], 32)), encoding="utf-8")
    document = json.loads(snapshot_to_json(_snap("2", ["f"], 32)))
    same(document)
    new.write_text(json.dumps(document), encoding="utf-8")
    plain = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert plain.exit_code == 0, plain.output
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("assurance:\n  require_complete: true\n", encoding="utf-8")
    strict = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg)]
    )
    assert strict.exit_code == 1, strict.output


def test_an_unknown_document_level_name_is_a_usage_failure(tmp_path: Path) -> None:
    def whole(document: dict) -> None:
        document["must_understand"] = ["x_whole"]

    old, new = _pair(tmp_path, whole)
    result = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert result.exit_code != 0
    assert "x_whole" in result.output


@pytest.mark.parametrize("section", ["types", "declarations", "debug"])
def test_console_summary_names_the_not_comparable_section(
    tmp_path: Path, section: str
) -> None:
    """The default console summary of a run whose analyses were blocked
    never reads as a clean comparison: it states the partial assurance and
    names the blocked section, as JSON and Markdown already do."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(snapshot_to_json(_snap("1", ["f"], 32)), encoding="utf-8")
    document = json.loads(snapshot_to_json(_snap("2", ["f"], 32)))
    document["sections"][section]["must_understand"] = ["x_layout_v2"]
    new.write_text(json.dumps(document), encoding="utf-8")
    result = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert result.exit_code == 0, result.output
    warning = next(
        line
        for line in result.output.splitlines()
        if line.startswith("WARNING: Analysis assurance PARTIAL: not comparable")
    )
    assert f"'{section}'" in warning
    assert "x_layout_v2" in warning
    # A clean pair prints no such line.
    clean = CliRunner().invoke(main, ["compare", str(old), str(old)])
    assert "not comparable" not in clean.output
