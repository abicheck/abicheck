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

Scope: the CLI-level wiring (dump_cmd -> the shared typed executor
(`service_dump_pipeline.execute_dump_request`, via
`frontends.cli.dump_execute`) -> `dumper.dump()`; it was
`cli_dump_helpers.perform_elf_dump` in the middle until ADR-063 Phase 1
migrated the real run and Track 1 deleted the function), not the manifest
parser or the per-TU loop themselves -- see test_dump_manifest.py /
test_dumper_manifest.py for those.
"""

from __future__ import annotations

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
        [
            "dump",
            str(so),
            "-H",
            str(pub),
            "--dump-manifest",
            str(manifest),
        ],
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
            "dump",
            str(so),
            "--dump-manifest",
            str(manifest),
            "--ast-frontend",
            "clang",
            "--lang",
            "c",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    from abicheck.serialization import load_snapshot_document

    snap = load_snapshot_document(out)
    names = {f["name"] for f in snap["functions"]}
    assert {"add_a", "add_b"} <= names


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="gcc -shared -o *.so only produces a real ELF binary on Linux "
    "(see test_dumper_manifest.py's TestDumpWithManifest for the same gate)",
)
def test_dump_manifest_with_compiler_option_include_dir_still_works(tmp_path, runner):
    """Codex review, fresh evidence: `--compiler-option -I<dir>` is a
    *global* flag applied to every TU regardless of the manifest, but the
    fix folding it into `public_include_search_dirs` (for provenance
    widening) unconditionally would make that flat, non-empty value collide
    with `dump()`'s own manifest mutual-exclusivity check -- turning this
    previously-working combination into a usage error. It must keep
    working, with the compiler option still reaching the TU parse."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip("clang and gcc are required for this end-to-end test")
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    (include_dir / "dep.h").write_text("int dep(int a, int b);\n")
    header_a = tmp_path / "a.h"
    header_a.write_text('#include "dep.h"\nint add_a(int a, int b);\n')
    src = tmp_path / "lib.c"
    src.write_text(
        "int add_a(int a, int b) { return a + b; }\n"
        "int dep(int a, int b) { return a - b; }\n"
    )
    so = tmp_path / "liblib.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            f"-I{include_dir}",
            "-o",
            str(so),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    manifest = _write_manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
    )
    out = tmp_path / "snap.json"
    result = runner.invoke(
        main,
        [
            "dump",
            str(so),
            "--dump-manifest",
            str(manifest),
            "--ast-frontend",
            "clang",
            "--lang",
            "c",
            "--compiler-option",
            f"-I{include_dir}",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    from abicheck.serialization import load_snapshot_document

    snap = load_snapshot_document(out)
    names = {f["name"] for f in snap["functions"]}
    assert {"add_a", "dep"} <= names


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


# ---------------------------------------------------------------------------
# `dump --dump-manifest FILE --dry-run` — the manifest-only preflight (ADR-050
# D3/D1), folded from the former standalone `plan --dump-manifest` command
# (ADR-054, CLI-organization consolidation). No SO_PATH required: parses and
# normalizes the manifest and computes its scope_fingerprint WITHOUT running
# any compiler -- never a profile_fingerprint, which needs a real L2
# extraction. See test_dump_manifest.py for the manifest parser itself.
# ---------------------------------------------------------------------------


def test_dry_run_manifest_only_no_so_path_succeeds(tmp_path, runner):
    manifest = _write_manifest(
        tmp_path,
        "roots: [a.h, b.h]\n"
        "translation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "  - name: tu_b\n    forced_includes: [b.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    (tmp_path / "b.h").write_text("int g(void);\n")
    result = runner.invoke(
        main, ["dump", "--dump-manifest", str(manifest), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "scope_fingerprint: sha256:" in result.output
    assert "profile_fingerprint: (not computed" in result.output
    assert "tu_a" in result.output
    assert "tu_b" in result.output
    assert "no artifact (SO_PATH)" in result.output


def test_dry_run_manifest_reports_public_headers_and_tu_includes(tmp_path, runner):
    """The removed standalone `plan --dump-manifest` command printed every
    public_header_paths/public_header_dirs entry and each TU's
    forced_includes/includes (with project_owned) -- the dry-run replacement
    must not silently drop those, since they're exactly what a user checks
    path resolution against before a real extraction (Codex review)."""
    (tmp_path / "a.h").write_text("int f(void);\n")
    (tmp_path / "pub.h").write_text("int g(void);\n")
    manifest = _write_manifest(
        tmp_path,
        "roots: [a.h]\n"
        "public_header_paths: [pub.h]\n"
        "public_header_dirs: [.]\n"
        "translation_units:\n"
        "  - name: main\n"
        "    forced_includes: [a.h]\n"
        "    includes:\n"
        "      - path: a.h\n"
        "        project_owned: true\n",
    )
    result = runner.invoke(
        main, ["dump", "--dump-manifest", str(manifest), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "public_header_paths:" in result.output
    assert str(tmp_path / "pub.h") in result.output
    assert "public_header_dirs:" in result.output
    assert "forced_includes:" in result.output
    assert "includes:" in result.output
    assert "[project_owned]" in result.output


def test_dry_run_manifest_malformed_yaml_rejected(tmp_path, runner):
    manifest = _write_manifest(tmp_path, "roots: [unterminated\n")
    result = runner.invoke(
        main, ["dump", "--dump-manifest", str(manifest), "--dry-run"]
    )
    assert result.exit_code != 0
    assert "invalid YAML" in result.output


def test_dry_run_manifest_missing_translation_units_rejected(tmp_path, runner):
    manifest = _write_manifest(tmp_path, "roots: [foo.h]\n")
    result = runner.invoke(
        main, ["dump", "--dump-manifest", str(manifest), "--dry-run"]
    )
    assert result.exit_code != 0
    assert "translation_units" in result.output


def test_dry_run_manifest_two_manifests_same_surface_share_scope_fingerprint(
    tmp_path, runner
):
    """Two manifests declaring the same logical surface under different
    roots/checkouts fingerprint identically -- scope_fingerprint identifies
    the DECLARED surface, not the checkout path (ADR-050 D1)."""
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    for d in (old_dir, new_dir):
        (d / "a.h").write_text("int f(void);\n")
        (d / "b.h").write_text("int g(void);\n")
        (d / "m.yaml").write_text(
            "roots: [a.h, b.h]\n"
            "translation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "  - name: tu_b\n    forced_includes: [b.h]\n"
        )
    old_out = runner.invoke(
        main, ["dump", "--dump-manifest", str(old_dir / "m.yaml"), "--dry-run"]
    )
    new_out = runner.invoke(
        main, ["dump", "--dump-manifest", str(new_dir / "m.yaml"), "--dry-run"]
    )
    assert old_out.exit_code == 0 and new_out.exit_code == 0

    def _fingerprint(output: str) -> str:
        for line in output.splitlines():
            if "scope_fingerprint:" in line:
                return line.split("scope_fingerprint:", 1)[1].strip()
        raise AssertionError(f"no scope_fingerprint in output: {output!r}")

    assert _fingerprint(old_out.output) == _fingerprint(new_out.output)


def test_dry_run_manifest_reflects_tu_includes_in_scope_fingerprint(tmp_path, runner):
    """Codex/CodeRabbit review: compute_extraction_contract's manifest
    branch previously omitted both manifest_tu_scope and declared_includes,
    so the dry-run's printed scope_fingerprint fell back to the legacy field
    set -- identical for any two manifests sharing the same roots, even
    when their TU includes genuinely differed. A real (non-dry) manifest
    dump DOES fold both into scope_fingerprint, so the dry-run preview was
    silently unusable for previewing/troubleshooting the actual extraction
    contract."""
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    for d in (a_dir, b_dir):
        (d / "a.h").write_text("int f(void);\n")
        (d / "vendor").mkdir()
    (a_dir / "m.yaml").write_text(
        "roots: [a.h]\ntranslation_units:\n  - name: main\n    forced_includes: [a.h]\n"
    )
    (b_dir / "m.yaml").write_text(
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: main\n    forced_includes: [a.h]\n    includes: [vendor]\n"
    )
    out_a = runner.invoke(
        main, ["dump", "--dump-manifest", str(a_dir / "m.yaml"), "--dry-run"]
    )
    out_b = runner.invoke(
        main, ["dump", "--dump-manifest", str(b_dir / "m.yaml"), "--dry-run"]
    )
    assert out_a.exit_code == 0 and out_b.exit_code == 0

    def _fingerprint(output: str) -> str:
        for line in output.splitlines():
            if "scope_fingerprint:" in line:
                return line.split("scope_fingerprint:", 1)[1].strip()
        raise AssertionError(f"no scope_fingerprint in output: {output!r}")

    assert _fingerprint(out_a.output) != _fingerprint(out_b.output)


def test_dry_run_manifest_never_invokes_a_compiler(tmp_path, runner):
    from unittest import mock

    manifest = _write_manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n  - name: main\n    forced_includes: [a.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    with (
        mock.patch("subprocess.run") as mock_run,
        mock.patch("subprocess.Popen") as mock_popen,
    ):
        result = runner.invoke(
            main, ["dump", "--dump-manifest", str(manifest), "--dry-run"]
        )
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    mock_popen.assert_not_called()
