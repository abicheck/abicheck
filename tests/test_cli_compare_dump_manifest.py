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


def test_compare_dump_manifest_directory_rejection_wins_over_malformed_yaml(
    tmp_path, runner
):
    """A malformed manifest on a directory/package compare must fail with
    the clear "not supported for directory/package" message, not a
    confusing "invalid YAML" one for a flag combination that was never
    going to work anyway -- the manifest is parsed only after the
    directory/package rejection runs, not before it."""
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    bad_manifest = tmp_path / "bad.yaml"
    bad_manifest.write_text("roots: [unterminated\n")
    result = runner.invoke(
        main,
        [
            "compare", str(old_dir), str(new_dir),
            "--dump-manifest", "old=" + str(bad_manifest),
        ],
    )
    assert result.exit_code != 0
    assert "not supported for directory/package" in result.output
    assert "invalid YAML" not in result.output


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

    def test_both_sides_dump_manifest_dumps_each_from_its_own_manifest(
        self, tmp_path, runner
    ):
        """old=/new= each get their OWN manifest, not both dumped from one --
        the specific bug side-scoping (SIDED_PATH_PARAM, not a single shared
        --dump-manifest) exists to prevent."""
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        old_a = old_dir / "a.h"
        old_a.write_text("int add_a(int a, int b);\n")
        old_src = old_dir / "old.c"
        old_src.write_text("int add_a(int a, int b) { return a + b; }\n")
        old_so = old_dir / "libold.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(old_so), str(old_src)],
            check=True,
            capture_output=True,
        )
        old_manifest = _write_manifest(
            old_dir / "old_manifest.yaml",
            "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
        )
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_so, new_manifest = self._build_two_tu_lib(new_dir)

        out = tmp_path / "result.json"
        result = runner.invoke(
            main,
            [
                "compare", str(old_so), str(new_so),
                "--dump-manifest", "old=" + str(old_manifest),
                "--dump-manifest", "new=" + str(new_manifest),
                "--ast-frontend", "clang", "--lang", "c",
                "--diagnostic-comparison",
                "--format", "json", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        kinds = [c["kind"] for c in data.get("changes", [])]
        # old's manifest declares only add_a; new's own (different) manifest
        # merges add_a + add_b -- if the old side were accidentally dumped
        # from new's manifest (or vice versa) this would report no addition
        # (both sides identical) instead of a genuine add_b addition.
        assert "func_added" in kinds
        assert "symbol_removed" not in kinds


def test_compare_dump_manifest_and_release_manifest_coexist(
    tmp_path, runner, monkeypatch
):
    """--dump-manifest (this ADR's extraction manifest) and the pre-existing
    --instantiation-manifest (ADR-023 release instantiation manifest,
    renamed from --manifest by CLI cleanup phase two, PR J) are distinct,
    independently-settable options on the same compare invocation -- the
    specific Click-level collision this ADR's flag naming exists to avoid."""
    from abicheck.checker import DiffResult, Verdict
    from abicheck.model import AbiSnapshot

    old_so = _elf_stub(tmp_path / "old.so")
    new_so = _elf_stub(tmp_path / "new.so")
    dump_manifest = _write_manifest(
        tmp_path / "dm.yaml",
        "roots: [foo.h]\ntranslation_units:\n  - name: main\n    forced_includes: [foo.h]\n",
    )
    release_manifest = tmp_path / "release_manifest.yaml"
    release_manifest.write_text("symbols: []\n")

    captured: dict[str, object] = {}
    snap = AbiSnapshot(library="old.so", version="1.0")

    def _fake_resolve(*_a, **kw):
        captured["old_dump_manifest"] = kw.get("old_dump_manifest")
        return snap, snap

    monkeypatch.setattr(
        "abicheck.cli_compare_helpers._resolve_compare_snapshots", _fake_resolve
    )
    monkeypatch.setattr(
        "abicheck.service.compare_snapshots",
        lambda *_a, **_kw: DiffResult(
            old_version="1", new_version="1", library="old.so",
            verdict=Verdict.NO_CHANGE, assurance="none",
        ),
    )
    monkeypatch.setattr(
        "abicheck.service_render.to_markdown", lambda _r, **_kw: "REPORT"
    )

    result = runner.invoke(
        main,
        [
            "compare", str(old_so), str(new_so),
            "--instantiation-manifest", str(release_manifest),
            "--dump-manifest", "old=" + str(dump_manifest),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["old_dump_manifest"] is not None
    assert captured["old_dump_manifest"].roots  # a real parsed DumpManifest
