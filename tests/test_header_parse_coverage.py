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

"""Evidence-entity-model gap A3: a header the parse dropped is not "no header".

clang's ``#error`` retry drops top-level headers that refuse direct inclusion
and parses the rest. A public type declared in such a header is then named by
no header entity, so the L1 debug-type scope leaves it out -- and nothing said
so. The snapshot now records the dropped headers, the header-AST coverage
record reads ``partial`` covering nothing, and every debug type no header
names answers ``unknown`` in the report's coverage section instead of
``proven_absent``.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, RecordType
from abicheck.model.dwarf_facts import DwarfMetadata, StructLayout
from abicheck.serialization import save_snapshot

EXCLUDED_KEY = "header_parse_excluded"


def _snap(version: str, *, excluded: bool, dependency_scope: str | None) -> AbiSnapshot:
    """Header AST declares ``Pub`` only; DWARF also has ``Hidden`` -- a type
    whose header (``internal.h``) the parse dropped when *excluded*."""
    toolchain = {"producer": "clang"}
    if excluded:
        toolchain[EXCLUDED_KEY] = json.dumps(["/inc/internal.h"])
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=[Function(name="f", mangled="f", return_type="void")],
        types=[RecordType(name="Pub", kind="struct", size_bits=32)],
        dwarf=DwarfMetadata(
            structs={
                "Pub": StructLayout(name="Pub", byte_size=4),
                "Hidden": StructLayout(name="Hidden", byte_size=8),
            },
            has_dwarf=True,
        ),
        from_headers=True,
        dependency_scope=dependency_scope,
        ast_toolchain=toolchain,
        platform="elf",
    )


# Oracle: the unmatched debug type's answer, written out by hand.
def _expected_absence(excluded: bool) -> str:
    return "unknown" if excluded else "proven_absent"


@pytest.mark.parametrize(
    ("excluded", "dependency_scope"),
    [
        pytest.param(
            e,
            d,
            marks=pytest.mark.xfail(strict=True, reason="gap A3") if e else (),
        )
        for e, d in itertools.product((False, True), ("full", "filtered", None))
    ],
)
def test_debug_type_answer_follows_header_coverage(
    excluded: bool, dependency_scope: str | None
) -> None:
    from abicheck.compare.edge_query import EdgeEvidence

    snap = _snap("1", excluded=excluded, dependency_scope=dependency_scope)
    ev = EdgeEvidence(snap)
    subjects = [s for s, rec in ev.debug_types.join.right.items() if not rec.candidates]
    assert subjects, "the Hidden debug type must be an unmatched debug subject"
    for subject in subjects:
        got = ev.query("debug_type_of", subject).answer.value
        assert got == _expected_absence(excluded), (excluded, dependency_scope, got)


@pytest.mark.xfail(strict=True, reason="gap A3: dropped header reads proven_absent")
def test_cli_report_surfaces_unknown_for_a_dropped_header(tmp_path: Path) -> None:
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    save_snapshot(_snap("1", excluded=False, dependency_scope="filtered"), old_p)
    save_snapshot(_snap("2", excluded=True, dependency_scope="filtered"), new_p)
    res = CliRunner().invoke(main, ["compare", str(old_p), str(new_p), "-o", "json=-"])
    assert res.exit_code in (0, 1, 2, 4), res.output
    doc = json.loads(res.output)
    new_debug = doc["edge_coverage"]["new"]["debug_type_of"]
    old_debug = doc["edge_coverage"]["old"]["debug_type_of"]
    assert new_debug["absence"] == "unknown"
    assert any(r.get("reason") == "header_parse_excluded" for r in new_debug["records"])
    assert sum(c["unknown"] for c in new_debug["answers"].values()) >= 1
    # Negative control: the side whose parse dropped nothing still proves it.
    assert old_debug["absence"] == "proven_absent"
    md = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "-o", "markdown=-"]
    )
    assert "header_parse_excluded" in md.output
