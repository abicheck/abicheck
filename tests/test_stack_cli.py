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

"""Tests for CLI commands: abicheck deps / abicheck stack-check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _require_linux_elf(path: Path) -> Path:
    """Verify we're on Linux and the candidate is an ELF binary."""
    if sys.platform != "linux":
        pytest.skip("Full-stack dependency tests require Linux")
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"\x7fELF":
                pytest.skip(f"{path} is not an ELF binary")
    except OSError:
        pytest.skip(f"Cannot read {path}")
    return path


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def real_binary():
    if sys.platform != "linux":
        pytest.skip("Full-stack dependency tests require Linux")
    candidates = [Path("/usr/bin/python3"), Path("/usr/bin/ls"), Path("/bin/ls")]
    for p in candidates:
        if p.exists():
            return _require_linux_elf(p)
    pytest.skip("No suitable ELF binary found")


class TestDepsCommand:
    # The `deps --format json` invocation follows the binary's whole dependency
    # chain (~0.6s). Three tests below asserted on the *same* JSON output, so
    # run it once per class and share the parsed payload instead of paying the
    # resolution cost 3× (~1.8s → ~0.6s).
    @staticmethod
    @pytest.fixture(scope="class")
    def deps_json():
        # Class-scoped, so it can't take the function-scoped `real_binary`
        # fixture — resolve the binary inline (same logic as `real_binary`).
        if sys.platform != "linux":
            pytest.skip("Full-stack dependency tests require Linux")
        binary = None
        for p in (Path("/usr/bin/python3"), Path("/usr/bin/ls"), Path("/bin/ls")):
            if p.exists():
                binary = _require_linux_elf(p)
                break
        if binary is None:
            pytest.skip("No suitable ELF binary found")
        result = CliRunner().invoke(main, ["deps", str(binary), "--format", "json"])
        assert result.exit_code == 0
        return json.loads(result.output)

    def test_deps_markdown(self, runner, real_binary):
        result = runner.invoke(main, ["deps", str(real_binary)])
        assert result.exit_code == 0
        assert "Stack Report" in result.output
        assert "Dependency Tree" in result.output
        assert "Symbol Binding Summary" in result.output

    def test_deps_json(self, deps_json):
        assert "root_binary" in deps_json
        assert "verdict" in deps_json
        assert deps_json["verdict"]["loadability"] == "pass"

    def test_deps_output_file(self, runner, real_binary, tmp_path):
        outfile = tmp_path / "deps.json"
        result = runner.invoke(main, [
            "deps", str(real_binary), "--format", "json", "-o", str(outfile),
        ])
        assert result.exit_code == 0
        assert outfile.exists()
        data = json.loads(outfile.read_text())
        assert "root_binary" in data

    def test_deps_nonexistent_binary(self, runner, tmp_path):
        result = runner.invoke(main, ["deps", str(tmp_path / "nonexistent")])
        assert result.exit_code != 0  # Click raises error for non-existent path

    def test_deps_contains_libc(self, deps_json):
        sonames = [n["soname"] for n in deps_json["baseline_graph"]["nodes"]]
        assert "libc.so.6" in sonames

    def test_deps_binding_summary(self, deps_json):
        assert "bindings_summary" in deps_json
        assert "resolved_ok" in deps_json["bindings_summary"]
        assert deps_json["bindings_summary"]["resolved_ok"] > 0


class TestStackCheckCommand:
    def test_stack_check_help(self, runner):
        result = runner.invoke(main, ["stack-check", "--help"])
        assert result.exit_code == 0
        assert "baseline" in result.output.lower()
        assert "candidate" in result.output.lower()


# ---------------------------------------------------------------------------
# --follow-deps flag on dump and compare
# ---------------------------------------------------------------------------

@pytest.fixture
def real_lib():
    """Find a real shared library for --follow-deps tests."""
    if sys.platform != "linux":
        pytest.skip("Full-stack dependency tests require Linux")
    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/libz.so.1"),
        Path("/usr/lib/x86_64-linux-gnu/libexpat.so.1"),
        Path("/lib/x86_64-linux-gnu/libz.so.1"),
    ]
    for p in candidates:
        if p.exists():
            return _require_linux_elf(p)
    pytest.skip("No suitable shared library found")


class TestDumpFollowDeps:
    def test_dump_follow_deps_json(self, runner, real_lib):
        result = runner.invoke(main, ["dump", str(real_lib), "--follow-deps"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dependency_info" in data
        di = data["dependency_info"]
        assert len(di["nodes"]) >= 1
        assert "bindings_summary" in di
        assert di["bindings_summary"].get("resolved_ok", 0) > 0

    def test_dump_follow_deps_has_libc(self, runner, real_lib):
        result = runner.invoke(main, ["dump", str(real_lib), "--follow-deps"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        sonames = [n["soname"] for n in data["dependency_info"]["nodes"]]
        assert "libc.so.6" in sonames

    def test_dump_without_follow_deps_no_dep_info(self, runner, real_lib):
        result = runner.invoke(main, ["dump", str(real_lib)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("dependency_info") is None

    def test_dump_follow_deps_to_file(self, runner, real_lib, tmp_path):
        outfile = tmp_path / "snap.json"
        result = runner.invoke(main, [
            "dump", str(real_lib), "--follow-deps", "-o", str(outfile),
        ])
        assert result.exit_code == 0
        data = json.loads(outfile.read_text())
        assert "dependency_info" in data

    def test_dump_follow_deps_roundtrip(self, runner, real_lib, tmp_path):
        """Dump with --follow-deps, load snapshot, verify dep info survives."""
        outfile = tmp_path / "snap.json"
        result = runner.invoke(main, [
            "dump", str(real_lib), "--follow-deps", "-o", str(outfile),
        ])
        assert result.exit_code == 0

        from abicheck.serialization import load_snapshot
        snap = load_snapshot(outfile)
        assert snap.dependency_info is not None
        assert len(snap.dependency_info.nodes) >= 1
        assert snap.dependency_info.bindings_summary.get("resolved_ok", 0) > 0


def _extract_json(output: str) -> dict:
    """Extract JSON from CLI output that may contain leading warning lines."""
    # Find the first '{' (start of JSON) in the output.
    idx = output.find("{")
    if idx < 0:
        raise ValueError(f"No JSON found in output: {output[:200]}")
    return json.loads(output[idx:])


class TestCompareFollowDeps:
    def test_compare_follow_deps_json(self, runner, real_lib):
        result = runner.invoke(main, [
            "compare", str(real_lib), str(real_lib), "--follow-deps", "--format", "json",
        ])
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "old_dependency_info" in data
        assert "new_dependency_info" in data
        assert data["old_dependency_info"]["bindings_summary"]["resolved_ok"] > 0

    def test_compare_follow_deps_markdown(self, runner, real_lib):
        result = runner.invoke(main, [
            "compare", str(real_lib), str(real_lib), "--follow-deps",
        ])
        assert result.exit_code == 0
        assert "Dependency Analysis" in result.output
        assert "resolved_ok" in result.output

    def test_compare_without_follow_deps_no_dep_section(self, runner, real_lib):
        result = runner.invoke(main, [
            "compare", str(real_lib), str(real_lib),
        ])
        assert result.exit_code == 0
        assert "Dependency Analysis" not in result.output
