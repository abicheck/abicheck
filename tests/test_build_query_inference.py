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

"""Zero-config build-system inference (ADR-032 amendment): ``--sources`` alone
must detect the build system and run abicheck's own query — no
``--allow-build-query`` flag, no manual compile step. Pure detection / command
construction tested here; the live subprocess is exercised behind a stub."""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.build_query import (
    ABICHECK_BUILD_DIR,
    detect_build_system,
    inferred_query_command,
    run_inferred_build_query,
)


def test_detect_cmake(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_bazel(tmp_path: Path):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    assert detect_build_system(tmp_path) == "bazel"


def test_detect_make(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    assert detect_build_system(tmp_path) == "make"


def test_cmake_wins_over_make_when_both_present(tmp_path: Path):
    # A CMake project often ships a convenience Makefile; CMake is authoritative.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    (tmp_path / "Makefile").write_text("all:\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_none_for_plain_dir(tmp_path: Path):
    assert detect_build_system(tmp_path) == ""
    assert detect_build_system(None) == ""


def test_cmake_command_is_fixed_and_uses_export_flag(tmp_path: Path):
    cmd = inferred_query_command("cmake", tmp_path)
    assert cmd is not None
    assert cmd[0] == "cmake"
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in cmd
    assert str(tmp_path / ABICHECK_BUILD_DIR) in cmd


def test_make_has_no_auto_command(tmp_path: Path):
    # Make is detected but never auto-run: `make -n` is not reliably
    # side-effect-free (GNU make runs `+`/`$(MAKE)` recipes even in dry run).
    assert inferred_query_command("make", tmp_path) is None


def test_unknown_system_has_no_command(tmp_path: Path):
    assert inferred_query_command("scons", tmp_path) is None


def test_run_skips_with_diagnostic_when_tool_missing(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    merged = BuildEvidence()
    extractors: list = []
    # Simulate cmake not installed.
    out = run_inferred_build_query(
        tmp_path, merged, extractors, which=lambda _tool: None
    )
    assert out is None
    assert len(extractors) == 1
    rec = extractors[0]
    assert rec.status == "skipped"
    assert "not installed" in rec.detail


def test_run_returns_none_for_non_build_tree(tmp_path: Path):
    merged = BuildEvidence()
    extractors: list = []
    assert run_inferred_build_query(tmp_path, merged, extractors) is None
    assert extractors == []  # nothing detected -> no noise


# ── runner paths (subprocess stubbed) ────────────────────────────────────────

from abicheck.buildsource import build_query as _bq  # noqa: E402


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_cmake_success_returns_compile_db(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")

    def fake_run(cmd, **kw):
        # emulate cmake writing the compile DB into the -B dir
        bdir = Path(cmd[cmd.index("-B") + 1])
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "compile_commands.json").write_text("[]")
        return _FakeProc(0)

    monkeypatch.setattr(_bq.subprocess, "run", fake_run)
    merged, ext = BuildEvidence(), []
    db = run_inferred_build_query(tmp_path, merged, ext)
    assert db is not None and db.is_file() and db.name == "compile_commands.json"
    assert ext[-1].status == "ok"


def test_run_cmake_no_db_is_partial(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(_bq.subprocess, "run", lambda cmd, **kw: _FakeProc(0))
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "partial"


def test_run_nonzero_exit_is_failed(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(
        _bq.subprocess, "run", lambda cmd, **kw: _FakeProc(1, stderr="boom")
    )
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "failed"
    assert merged.diagnostics


def test_run_subprocess_error_is_failed(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")

    def boom(cmd, **kw):
        raise OSError("no cmake")

    monkeypatch.setattr(_bq.subprocess, "run", boom)
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "failed"


def test_run_make_is_skipped_with_diagnostic(tmp_path: Path, monkeypatch):
    # Make is detected but never auto-run for safety; it must not invoke any
    # subprocess and must record a skip diagnostic pointing to the opt-in path.
    (tmp_path / "Makefile").write_text("all:\n\t+touch pwned\n")

    def boom(cmd, **kw):  # pragma: no cover - must never be called
        raise AssertionError("make must not be auto-run")

    monkeypatch.setattr(_bq.subprocess, "run", boom)
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].name == "build_query_auto"
    assert ext[-1].status == "skipped"
    assert "build-query" in ext[-1].detail and "Make" in ext[-1].detail
    assert not merged.compile_units


def test_bazel_command_includes_param_files(tmp_path: Path):
    cmd = inferred_query_command("bazel", tmp_path)
    assert cmd is not None
    assert "--include_param_files" in cmd  # expands @...params (Codex review)


def test_inferred_query_diag_yields_partial_l3_coverage():
    # A build_query_auto skipped/failed diagnostic must produce a partial L3 row,
    # not a silent not_collected, so the user learns why source scanning got no L3.
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import CoverageStatus, ExtractorRecord

    rec = ExtractorRecord(
        name="build_query_auto", status="skipped", detail="cmake not installed"
    )
    rows = build_inline_coverage(BuildEvidence(), False, None, None, [rec])
    l3 = next(r for r in rows if r.layer == "L3_build")
    assert l3.status == CoverageStatus.PARTIAL
    assert "cmake not installed" in (l3.detail or "")


def test_run_bazel_empty_action_graph_is_partial(tmp_path: Path, monkeypatch):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    monkeypatch.setattr(
        _bq.subprocess, "run", lambda cmd, **kw: _FakeProc(0, stdout='{"actions":[]}')
    )
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].name == "build_query_auto"
    assert ext[-1].status == "partial"  # no CppCompile actions
