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
    list(itertools.product((False, True), ("full", "filtered", None))),
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


def test_retry_records_what_it_dropped(tmp_path: Path) -> None:
    """The retry stamps the dropped headers on the result it returns."""
    import subprocess

    from abicheck.extract.headers.clang.error_header_retry import (
        retry_excluding_error_headers,
    )

    agg = tmp_path / "agg.hpp"
    headers = [tmp_path / "a.h", tmp_path / "internal.h"]
    stderr = (
        f"In file included from {agg}:2:\n"
        f"{headers[1]}:1:2: error: do not include this header directly\n"
        '    1 | #error "do not include this header directly"\n'
    )
    first = subprocess.CompletedProcess(["clang"], 1, "", stderr)
    ok = subprocess.CompletedProcess(["clang"], 0, "", "")
    written: list[list[Path]] = []
    result = retry_excluding_error_headers(
        result=first,
        run_clang=lambda: ok,
        write_agg=written.append,
        agg_path=agg,
        active_headers=list(headers),
    )
    assert written == [[headers[0]]]
    assert getattr(result, "abicheck_excluded_headers", None) == [str(headers[1])]


@pytest.mark.parametrize("dropped", [[], ["/inc/internal.h", "/inc/detail.h"]])
def test_exclusions_survive_the_ast_cache(tmp_path: Path, dropped: list[str]) -> None:
    """Cold run records on the tree and beside the entry; a warm read of the
    entry restores exactly that list; a later clean run clears it."""
    from abicheck.storage.ast_parse_exclusions import (
        HEADER_PARSE_EXCLUDED_KEY,
        attach_parse_exclusions,
        record_parse_exclusions,
    )

    entry = tmp_path / "ast.json"
    entry.write_text("{}")
    cold: dict[str, object] = {}
    record_parse_exclusions(dropped, cold, entry, cache_write=True)
    warm: dict[str, object] = {}
    attach_parse_exclusions(warm, entry)
    assert cold.get(HEADER_PARSE_EXCLUDED_KEY, []) == dropped
    assert sorted(warm.get(HEADER_PARSE_EXCLUDED_KEY, [])) == sorted(dropped)
    record_parse_exclusions([], {}, entry, cache_write=True)
    cleared: dict[str, object] = {}
    attach_parse_exclusions(cleared, entry)
    assert HEADER_PARSE_EXCLUDED_KEY not in cleared


def test_parser_stamp_carries_the_record_into_the_snapshot_field() -> None:
    from abicheck.dumper_toolchain import _stamp_ast_parser
    from abicheck.model.header_parse_coverage import header_parse_excluded
    from abicheck.storage.ast_parse_exclusions import HEADER_PARSE_EXCLUDED_KEY

    class _P:
        _root = {HEADER_PARSE_EXCLUDED_KEY: ["/inc/b.h", "/inc/a.h"]}

    parser = _stamp_ast_parser(
        _P(), producer="clang", executable="clang", compiler="clang",
        gcc_path=None, gcc_prefix=None,
    )  # fmt: skip
    snap = AbiSnapshot(
        library="l", version="1", ast_toolchain=parser._abicheck_ast_toolchain
    )
    assert header_parse_excluded(snap) == ("/inc/a.h", "/inc/b.h")
    assert header_parse_excluded(AbiSnapshot(library="l", version="1")) == ()


@pytest.mark.integration
def test_real_clang_dump_records_a_dropped_header(tmp_path: Path) -> None:
    """End to end with clang: a public directory whose ``internal.h``
    refuses direct inclusion. The dump records it, and the report says the
    DWARF type it declares is ``unknown`` rather than silently out of scope."""
    import shutil
    import subprocess

    if shutil.which("clang") is None or shutil.which("gcc") is None:
        pytest.skip("needs clang and gcc")
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "pub.h").write_text("struct Pub { int a; };\nint f(struct Pub *);\n")
    (inc / "internal.h").write_text(
        "#ifndef LIBX_BUILDING\n"
        '#error "do not include this header directly"\n'
        "#endif\n"
        "struct Hidden { long b; };\n"
        "int g(struct Hidden *);\n"
    )
    src = tmp_path / "x.c"
    src.write_text(
        '#define LIBX_BUILDING\n#include "pub.h"\n#include "internal.h"\n'
        "int f(struct Pub *p) { return p->a; }\n"
        "int g(struct Hidden *h) { return (int)h->b; }\n"
    )
    lib = tmp_path / "libx.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-I", str(inc), str(src), "-o", str(lib)],
        check=True,
    )
    out = tmp_path / "x.json"
    res = CliRunner().invoke(
        main,
        [
            "dump", str(lib), "-H", str(inc), "-o", str(out),
        ],
        env={"ABICHECK_AST_FRONTEND": "clang"},
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    from abicheck.model.header_parse_coverage import header_parse_excluded
    from abicheck.serialization import load_snapshot

    snap = load_snapshot(out)
    assert any(p.endswith("internal.h") for p in header_parse_excluded(snap))
    report = tmp_path / "report.json"
    rep = CliRunner().invoke(
        main, ["compare", str(out), str(out), "-o", f"json={report}"]
    )
    assert rep.exit_code in (0, 1, 2, 4), rep.output
    doc = json.loads(report.read_text())
    assert doc["edge_coverage"]["new"]["debug_type_of"]["absence"] == "unknown"


@pytest.mark.parametrize("excluded", [False, True])
def test_dependency_scope_never_drops_an_unnamed_dwarf_type_under_partial_parse(
    excluded: bool,
) -> None:
    """With the parse partial, the DWARF filter drops only confirmed
    dependency types; a type no parsed header names is kept (unknown)."""
    from abicheck.dumper_scoping import scope_snapshot_excluding_dependencies

    snap = _snap("1", excluded=excluded, dependency_scope=None)
    snap.types = [
        RecordType(name="Pub", kind="struct", size_bits=32, source_header="/inc/pub.h"),
        RecordType(
            name="DepT", kind="struct", size_bits=8,
            source_header="/usr/include/dep.h",
        ),
    ]  # fmt: skip
    snap.dwarf.structs["DepT"] = StructLayout(name="DepT", byte_size=1)
    scoped = scope_snapshot_excluding_dependencies(snap, header_roots=["/inc"])
    kept = set(scoped.dwarf.structs)
    # Oracle, by hand: the dependency type always goes, the public one stays,
    # the unnamed one stays exactly when the parse was partial.
    assert "DepT" not in kept
    assert "Pub" in kept
    assert ("Hidden" in kept) is excluded
