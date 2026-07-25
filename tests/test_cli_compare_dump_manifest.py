# Copyright 2026 Nikolay Petrov
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
"""ADR-050 D3 (G32 Phase B) — compare's side-scoped `--dump-manifest`.

Scope: the CLI-level wiring (cli.py's compare_cmd -> cli_compare_helpers.run_compare
-> cli_resolve._resolve_compare_snapshots -> service.resolve_input/run_dump ->
dumper.dump()), not the manifest parser or the per-TU loop themselves -- see
test_dump_manifest.py / test_dumper_manifest.py for those, and
test_cli_dump_manifest.py for the (unsided) `dump --dump-manifest` flag.
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


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_manifest(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def _elf_stub(path: Path) -> Path:
    path.write_bytes(b"\x7fELF")
    return path


def test_compare_dump_manifest_and_header_same_side_rejected(tmp_path, runner):
    old_so = _elf_stub(tmp_path / "old.so")
    new_so = _elf_stub(tmp_path / "new.so")
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n")
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(
        main,
        [
            "compare", str(old_so), str(new_so),
            "-H", "old=" + str(header),
            "--dump-manifest", "old=" + str(manifest),
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_compare_dump_manifest_rejects_non_elf_input(tmp_path, runner):
    snap = tmp_path / "snap.json"
    snap.write_text("{}")
    new_so = _elf_stub(tmp_path / "new.so")
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(
        main,
        [
            "compare", str(snap), str(new_so),
            "--dump-manifest", "old=" + str(manifest),
        ],
    )
    assert result.exit_code != 0
    assert "ELF binary" in result.output


def test_compare_dump_manifest_rejected_for_directory_inputs(tmp_path, runner):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(
        main,
        [
            "compare", str(old_dir), str(new_dir),
            "--dump-manifest", "old=" + str(manifest),
        ],
    )
    assert result.exit_code != 0
    assert "--dump-manifest" in result.output
    assert "not supported for directory/package" in result.output


def test_compare_dump_manifest_malformed_yaml_rejected(tmp_path, runner):
    old_so = _elf_stub(tmp_path / "old.so")
    new_so = _elf_stub(tmp_path / "new.so")
    manifest = _write_manifest(tmp_path / "m.yaml", "roots: [unterminated\n")
    result = runner.invoke(
        main,
        [
            "compare", str(old_so), str(new_so),
            "--dump-manifest", "new=" + str(manifest),
        ],
    )
    assert result.exit_code != 0
    assert "invalid YAML" in result.output


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="gcc -shared -o *.so only produces a real ELF binary on Linux "
    "(see test_dumper_manifest.py's TestDumpWithManifest for the same gate)",
)
class TestCompareDumpManifestEndToEnd:
    """Real end-to-end proof: a manifest-driven side merges through the full
    `compare` chain (run_compare -> _resolve_compare_snapshots -> _resolve_input
    -> service.resolve_input -> run_dump -> service._dump_elf -> dumper.dump()),
    not just accepted and silently dropped at some intermediate layer.
    """

    def _build_two_tu_lib(self, tmp_path: Path) -> tuple[Path, Path]:
        header_a = tmp_path / "new_a.h"
        header_a.write_text("int add_a(int a, int b);\n")
        header_b = tmp_path / "new_b.h"
        header_b.write_text("int add_b(int a, int b);\n")
        src = tmp_path / "new.c"
        src.write_text(
            "int add_a(int a, int b) { return a + b; }\n"
            "int add_b(int a, int b) { return a - b; }\n"
        )
        so = tmp_path / "libnew.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        manifest = _write_manifest(
            tmp_path / "new_manifest.yaml",
            "roots: [new_a.h, new_b.h]\n"
            "translation_units:\n"
            "  - name: tu_a\n    forced_includes: [new_a.h]\n"
            "  - name: tu_b\n    forced_includes: [new_b.h]\n",
        )
        return so, manifest

    def test_new_side_dump_manifest_merges_both_tus(self, tmp_path, runner):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        old_h = tmp_path / "old.h"
        old_h.write_text("int add_a(int a, int b);\nint add_b(int a, int b);\n")
        old_src = tmp_path / "old.c"
        old_src.write_text(
            "int add_a(int a, int b) { return a + b; }\n"
            "int add_b(int a, int b) { return a - b; }\n"
        )
        old_so = tmp_path / "libold.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(old_so), str(old_src)],
            check=True,
            capture_output=True,
        )
        new_so, manifest = self._build_two_tu_lib(tmp_path)

        out = tmp_path / "result.json"
        result = runner.invoke(
            main,
            [
                "compare", str(old_so), str(new_so),
                "-H", "old=" + str(old_h),
                "--dump-manifest", "new=" + str(manifest),
                "--ast-frontend", "clang", "--lang", "c",
                "--diagnostic-comparison",
                "--format", "json", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        # Both TU fragments' functions merged into the new side's snapshot --
        # a genuine two-function old side against the merged two-function new
        # side reports no symbol-level break (only the expected L5-coverage
        # asymmetry from the differing dump paths, not a function add/remove).
        kinds = [c["kind"] for c in data.get("changes", [])]
        assert "symbol_removed" not in kinds
        assert "func_added" not in kinds

    def test_non_manifest_driven_compare_still_resolves_normally(
        self, tmp_path, runner
    ):
        """--dump-manifest on only one side leaves the other side's plain
        -H/--header resolution completely unaffected (regression guard)."""
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        old_h = tmp_path / "old.h"
        old_h.write_text("int add_a(int a, int b);\n")
        old_src = tmp_path / "old.c"
        old_src.write_text("int add_a(int a, int b) { return a + b; }\n")
        old_so = tmp_path / "libold.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(old_so), str(old_src)],
            check=True,
            capture_output=True,
        )
        new_so, manifest = self._build_two_tu_lib(tmp_path)

        out = tmp_path / "result.json"
        result = runner.invoke(
            main,
            [
                "compare", str(old_so), str(new_so),
                "-H", "old=" + str(old_h),
                "--dump-manifest", "new=" + str(manifest),
                "--ast-frontend", "clang", "--lang", "c",
                "--diagnostic-comparison",
                "--format", "json", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        kinds = [c["kind"] for c in data.get("changes", [])]
        # old side only declares add_a -- add_b is a genuine addition on new.
        assert "func_added" in kinds
