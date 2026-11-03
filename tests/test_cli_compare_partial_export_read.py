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

"""``abicheck compare`` against a stored snapshot whose export table was cut
short (ADR-079): the report, the assurance and the exit all say so.

Bug class ``evidence.silent_degradation_to_clean_verdict``. The NEW snapshot
is the externally produced case the ADR names -- a document recording
``export_read: partial`` (the ``binary`` section's ``abicheck.export_read``
extension fact) -- written to disk and compared through the CLI.
Before the fix the document had no way to say it, so the missing exports
read as removed: ``BREAKING``, exit 4, ``assurance: complete``.

The oracle is the fixture itself: which names the NEW table omits, and the
state it records. The same pair with a complete NEW table is the control.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model.export_read import ExportReadState
from abicheck.serialization import load_snapshot, snapshot_to_json

ALL = ("lib_init", "lib_read", "lib_write")
KEPT = ("lib_init",)


def _write(
    path: Path, names: tuple[str, ...], version: str, state: ExportReadState
) -> Path:
    snap = AbiSnapshot(
        library="libio.so.1",
        version=version,
        platform="elf",
        functions=[
            Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
            for n in names
        ],
        elf=ElfMetadata(
            soname="libio.so.1",
            machine="EM_X86_64",
            symbols=[ElfSymbol(name=n, sym_type=SymbolType.FUNC) for n in names],
            export_read=state,
        ),
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _compare(tmp: Path, state: ExportReadState, *extra: str) -> tuple[int, dict, str]:
    old = _write(tmp / "old.abi.json", ALL, "1.0", ExportReadState.COMPLETE)
    new = _write(tmp / f"new-{state.value}.abi.json", KEPT, "1.1", state)
    report = tmp / f"report-{state.value}-{len(extra)}.json"
    markdown = tmp / f"report-{state.value}-{len(extra)}.md"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            *extra,
            "-o",
            f"json={report}",
            "-o",
            f"markdown={markdown}",
        ],  # fmt: skip
        catch_exceptions=False,
    )
    return (
        result.exit_code,
        json.loads(report.read_text(encoding="utf-8")),
        markdown.read_text(encoding="utf-8"),
    )


def _removals(report: dict) -> dict[str, dict]:
    return {
        c["symbol"]: c
        for c in report["changes"]
        if c["kind"] == "func_removed" and c["symbol"] in set(ALL) - set(KEPT)
    }


@pytest.mark.parametrize("state", [ExportReadState.PARTIAL, ExportReadState.FAILED])
def test_a_partially_read_new_table_is_reported_not_scored(
    tmp_path: Path, state: ExportReadState
) -> None:
    code, report, markdown = _compare(tmp_path, state)
    assert code == 0, report["exit"]
    assurance = report["analysis_assurance"]
    assert assurance["status"] == "partial"
    assert assurance["export_read"] == {
        "old": {"elf": "complete"},
        "new": {"elf": state.value},
    }
    assert report["run_outcome"]["scope"] == "incomplete"
    removals = _removals(report)
    assert set(removals) == {"lib_read", "lib_write"}  # kept, not dropped
    assert report["verdict"] != "BREAKING"
    assert (
        f"| Coverage gap | export table not completely read (new snapshot, elf: {state.value})"
        in markdown
    )
    for symbol in removals:
        assert removals[symbol]["modulation_reason"].startswith(
            "the new side's export table was not completely read"
        )


def test_the_gap_fails_a_run_that_requires_complete_analysis(tmp_path: Path) -> None:
    config = tmp_path / ".abicheck.yml"
    config.write_text("assurance:\n  require_complete: true\n", encoding="utf-8")
    code, report, _ = _compare(
        tmp_path, ExportReadState.PARTIAL, "--config", str(config)
    )
    assert code == 1
    assert report["exit"]["analysis_assurance_contribution"] == 1


def test_control_a_complete_new_table_still_scores_the_removals(tmp_path: Path) -> None:
    code, report, _ = _compare(tmp_path, ExportReadState.COMPLETE)
    assert code == 4
    assert report["verdict"] == "BREAKING"
    assert report["analysis_assurance"]["export_read"]["new"] == {"elf": "complete"}
    assert report["run_outcome"]["scope"] == "complete"
    assert set(_removals(report)) == {"lib_read", "lib_write"}


@pytest.mark.integration
@pytest.mark.skipif(
    not (shutil.which("gcc") and sys.platform.startswith("linux")),
    reason="needs gcc and an ELF target",
)
@pytest.mark.parametrize("to_stdout", [False, True])
def test_a_cli_dump_records_its_complete_read(tmp_path: Path, to_stdout: bool) -> None:
    """The public ``dump`` writer carries the fact: it sections a flat
    payload itself, which used to drop every section-header carrier, so a
    stored dump read ``unrecorded`` while the live binary read ``complete``
    (the stored-vs-live split ``tests/test_family_f2_route_parity.py``
    caught). Oracle: the live binary's own block state."""
    source = tmp_path / "foo.c"
    source.write_text("int foo(void) { return 1; }\n", encoding="utf-8")
    lib = tmp_path / "libfoo.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(lib), str(source)], check=True)
    out = tmp_path / "foo.abi.json"
    args = ["dump", str(lib)] + ([] if to_stdout else ["-o", str(out)])
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    if to_stdout:
        out.write_text(result.stdout, encoding="utf-8")
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["sections"]["binary"]["extensions"]["abicheck.export_read"] == {}
    stored = load_snapshot(out)
    assert stored.elf is not None
    assert stored.elf.export_read is ExportReadState.COMPLETE
