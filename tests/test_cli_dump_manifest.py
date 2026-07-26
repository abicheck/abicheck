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
"""ADR-050 D3 (G32 Phase B) — the `dump --dump-manifest PATH` CLI flag.

Scope: the CLI-level wiring (cli.py's dump_cmd -> cli_dump_helpers.perform_elf_dump
-> dumper.dump()), not the manifest parser or the per-TU loop themselves --
see test_dump_manifest.py / test_dumper_manifest.py for those.
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


def _write_manifest(tmp_path: Path, text: str) -> Path:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(text)
    return manifest


def test_dump_manifest_and_header_flag_rejected(tmp_path, runner):
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF")
    header = tmp_path / "foo.h"
    header.write_text("int f(void);\n")
    manifest = _write_manifest(
        tmp_path,
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(
        main, ["dump", str(so), "-H", str(header), "--dump-manifest", str(manifest)]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_dump_manifest_and_public_header_rejected(tmp_path, runner):
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF")
    pub = tmp_path / "pub.h"
    pub.write_text("int f(void);\n")
    manifest = _write_manifest(
        tmp_path,
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(
        main,
        ["dump", str(so), "--public-header", str(pub), "--dump-manifest", str(manifest)],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_dump_manifest_malformed_yaml_rejected(tmp_path, runner):
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF")
    manifest = _write_manifest(tmp_path, "roots: [unterminated\n")
    result = runner.invoke(main, ["dump", str(so), "--dump-manifest", str(manifest)])
    assert result.exit_code != 0
    assert "invalid YAML" in result.output


def test_dump_manifest_missing_translation_units_rejected(tmp_path, runner):
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF")
    manifest = _write_manifest(tmp_path, "roots: [foo.h]\n")
    result = runner.invoke(main, ["dump", str(so), "--dump-manifest", str(manifest)])
    assert result.exit_code != 0
    assert "translation_units" in result.output


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="gcc -shared -o *.so only produces a real ELF binary on Linux "
    "(see test_dumper_manifest.py's TestDumpWithManifest for the same gate)",
)
def test_dump_manifest_end_to_end_merges_two_tus(tmp_path, runner):
    if not (_have("clang") and _have("gcc")):
        pytest.skip("clang and gcc are required for this end-to-end test")
    header_a = tmp_path / "a.h"
    header_a.write_text("int add_a(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("int add_b(int a, int b);\n")
    src = tmp_path / "lib.c"
    src.write_text(
        "int add_a(int a, int b) { return a + b; }\n"
        "int add_b(int a, int b) { return a - b; }\n"
    )
    so = tmp_path / "liblib.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    manifest = _write_manifest(
        tmp_path,
        "roots: [a.h, b.h]\n"
        "translation_units:\n"
        "  - name: tu_a\n"
        "    forced_includes: [a.h]\n"
        "  - name: tu_b\n"
        "    forced_includes: [b.h]\n",
    )
    out = tmp_path / "snap.json"
    result = runner.invoke(
        main,
        [
            "dump", str(so),
            "--dump-manifest", str(manifest),
            "--ast-frontend", "clang",
            "--lang", "c",
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    snap = json.loads(out.read_text())
    names = {f["name"] for f in snap["functions"]}
    assert {"add_a", "add_b"} <= names


def test_dump_manifest_rejected_for_pe_binary(tmp_path, runner):
    dll = tmp_path / "foo.dll"
    dll.write_bytes(b"MZ" + b"\x00" * 62)
    manifest = _write_manifest(
        tmp_path,
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    result = runner.invoke(main, ["dump", str(dll), "--dump-manifest", str(manifest)])
    assert result.exit_code != 0
    assert "PE" in result.output
