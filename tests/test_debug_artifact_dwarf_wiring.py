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

"""End-to-end proof that a resolved detached debug artifact actually feeds
the DWARF parse for ``dump``/``compare`` (P1.1, ADR-021a).

Before this fix, ``--debug-root``/``--debuginfod`` resolved and *logged* a
matching ``.debug`` file, but the DWARF parse always read the (stripped)
binary itself — so a normal split-debug production ``.so`` stayed L0-only
(symbols-only) even after abicheck reported it found the matching debug file.
`compare` against two stripped builds could report a false-green NO_CHANGE
even when the real DWARF-visible ABI (e.g. a struct layout) had changed.

Requires gcc + objcopy + strip (binutils) to build a real split-debug
fixture — no synthetic bytes can stand in for real DWARF sections.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.integration

_NEEDED_TOOLS = ("gcc", "objcopy", "strip", "readelf")


def _missing_tools() -> list[str]:
    return [t for t in _NEEDED_TOOLS if shutil.which(t) is None]


def _build_id(binary: Path) -> str:
    out = subprocess.run(
        ["readelf", "-n", str(binary)], capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        if "Build ID:" in line:
            return line.rsplit(":", 1)[-1].strip()
    raise AssertionError(f"no Build ID note found in {binary}")


def _build_split_debug_lib(tmp_path: Path, name: str, source: str) -> tuple[Path, Path]:
    """Compile *source* to a shared lib, split its debug info out, strip it.

    Returns (stripped_so_path, debug_root_dir) where debug_root_dir is a
    ``.build-id/<xx>/<rest>.debug`` tree (the same layout ``/usr/lib/debug``
    uses on a real distro).
    """
    src_dir = tmp_path / name
    src_dir.mkdir()
    c_file = src_dir / "foo.c"
    c_file.write_text(source, encoding="utf-8")
    so_path = src_dir / "libfoo.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_file)],
        check=True, capture_output=True,
    )
    debug_file = src_dir / "libfoo.so.debug"
    subprocess.run(
        ["objcopy", "--only-keep-debug", str(so_path), str(debug_file)],
        check=True, capture_output=True,
    )
    build_id = _build_id(so_path)
    subprocess.run(
        ["strip", "--strip-debug", "--strip-unneeded", str(so_path)],
        check=True, capture_output=True,
    )
    debug_root = tmp_path / f"debugroot_{name}"
    bid_dir = debug_root / ".build-id" / build_id[:2]
    bid_dir.mkdir(parents=True)
    shutil.copy(debug_file, bid_dir / f"{build_id[2:]}.debug")
    return so_path, debug_root


_POINT_V1 = "struct Point { int x; int y; };\nint add_point(struct Point p) { return p.x + p.y; }\n"
_POINT_V2 = (
    "struct Point { int x; int y; int z; };\n"
    "int add_point(struct Point p) { return p.x + p.y + p.z; }\n"
)


@pytest.mark.skipif(bool(_missing_tools()), reason=f"requires {_NEEDED_TOOLS}")
class TestDumpUsesResolvedDebugArtifact:
    def test_stripped_binary_without_debug_root_is_symbols_only(
        self, tmp_path: Path
    ) -> None:
        so_path, _debug_root = _build_split_debug_lib(tmp_path, "v1", _POINT_V1)
        out = tmp_path / "no_debug_root.json"
        result = CliRunner().invoke(
            main, ["dump", str(so_path), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        snap = json.loads(out.read_text())
        assert snap["dwarf"]["has_dwarf"] is False

    def test_stripped_binary_with_debug_root_gets_dwarf(self, tmp_path: Path) -> None:
        """The fix: --debug-root resolves the split .debug file AND the DWARF
        parse now actually reads it, instead of only logging that it exists."""
        so_path, debug_root = _build_split_debug_lib(tmp_path, "v1", _POINT_V1)
        out = tmp_path / "with_debug_root.json"
        result = CliRunner().invoke(
            main,
            ["dump", str(so_path), "--debug-root", str(debug_root), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert "Debug info:" in result.output
        snap = json.loads(out.read_text())
        assert snap["dwarf"]["has_dwarf"] is True
        assert len(snap["functions"]) >= 1


@pytest.mark.skipif(bool(_missing_tools()), reason=f"requires {_NEEDED_TOOLS}")
class TestCompareUsesResolvedDebugArtifacts:
    def test_compare_stripped_binaries_without_debug_root_is_false_green(
        self, tmp_path: Path
    ) -> None:
        """Baseline for the regression: two stripped builds with an identical
        export table but a changed struct layout report NO_CHANGE when no
        DWARF evidence is available — the false-green the report describes."""
        old_so, _ = _build_split_debug_lib(tmp_path, "old", _POINT_V1)
        new_so, _ = _build_split_debug_lib(tmp_path, "new", _POINT_V2)
        out = tmp_path / "result.json"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_so), str(new_so), "--format", "json", "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        assert data["verdict"] == "NO_CHANGE"

    def test_compare_stripped_binaries_with_debug_root_detects_real_break(
        self, tmp_path: Path
    ) -> None:
        """The fix: with --debug-root old=/new=, DWARF is resolved and parsed
        for both stripped sides, so the real struct-layout break surfaces
        instead of the false-green NO_CHANGE above."""
        old_so, old_debug_root = _build_split_debug_lib(tmp_path, "old", _POINT_V1)
        new_so, new_debug_root = _build_split_debug_lib(tmp_path, "new", _POINT_V2)
        out = tmp_path / "result.json"
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_so), str(new_so),
                "--debug-root", f"old={old_debug_root}",
                "--debug-root", f"new={new_debug_root}",
                "--format", "json", "-o", str(out),
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Debug info (old):" in result.output
        assert "Debug info (new):" in result.output
        data = json.loads(out.read_text())
        assert data["verdict"] == "BREAKING"
        kinds = {c["kind"] for c in data["changes"]}
        assert "type_size_changed" in kinds
