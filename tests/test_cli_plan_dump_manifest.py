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
"""ADR-050 D3 (G32 Phase B) — the `plan --dump-manifest` diagnostic command.

Scope: this command parses and normalizes a manifest and prints its
scope_fingerprint WITHOUT running any compiler -- see test_dump_manifest.py
for the manifest parser itself and test_dumper_manifest.py/
test_cli_dump_manifest.py for the real extraction path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from abicheck.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_manifest(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_plan_missing_dump_manifest_rejected(runner):
    result = runner.invoke(main, ["plan"])
    assert result.exit_code != 0
    assert "--dump-manifest" in result.output


def test_plan_malformed_yaml_rejected(tmp_path, runner):
    manifest = _write_manifest(tmp_path / "m.yaml", "roots: [unterminated\n")
    result = runner.invoke(main, ["plan", "--dump-manifest", str(manifest)])
    assert result.exit_code != 0
    assert "invalid YAML" in result.output


def test_plan_missing_translation_units_rejected(tmp_path, runner):
    manifest = _write_manifest(tmp_path / "m.yaml", "roots: [foo.h]\n")
    result = runner.invoke(main, ["plan", "--dump-manifest", str(manifest)])
    assert result.exit_code != 0
    assert "translation_units" in result.output


def test_plan_text_output_prints_scope_fingerprint_not_profile(tmp_path, runner):
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [a.h, b.h]\n"
        "translation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "  - name: tu_b\n    forced_includes: [b.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    (tmp_path / "b.h").write_text("int g(void);\n")
    result = runner.invoke(main, ["plan", "--dump-manifest", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "scope_fingerprint: sha256:" in result.output
    assert "profile_fingerprint: (not computed" in result.output
    assert "tu_a" in result.output
    assert "tu_b" in result.output


def test_plan_json_output_shape(tmp_path, runner):
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [a.h]\ntranslation_units:\n  - name: main\n    forced_includes: [a.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    result = runner.invoke(
        main, ["plan", "--dump-manifest", str(manifest), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["scope_fingerprint"].startswith("sha256:")
    assert data["manifest"]["roots"] == [str((tmp_path / "a.h").resolve())]
    assert data["manifest"]["translation_units"][0]["name"] == "main"
    assert data["manifest"]["frontend_context"] == "host"


def test_plan_never_invokes_a_compiler(tmp_path, runner):
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [a.h]\ntranslation_units:\n  - name: main\n    forced_includes: [a.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    with mock.patch("subprocess.run") as mock_run, mock.patch(
        "subprocess.Popen"
    ) as mock_popen:
        result = runner.invoke(main, ["plan", "--dump-manifest", str(manifest)])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    mock_popen.assert_not_called()


def test_plan_two_manifests_with_same_declared_surface_share_scope_fingerprint(
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
        _write_manifest(
            d / "m.yaml",
            "roots: [a.h, b.h]\n"
            "translation_units:\n"
            "  - name: tu_a\n    forced_includes: [a.h]\n"
            "  - name: tu_b\n    forced_includes: [b.h]\n",
        )
    old_out = runner.invoke(
        main, ["plan", "--dump-manifest", str(old_dir / "m.yaml"), "--format", "json"]
    )
    new_out = runner.invoke(
        main, ["plan", "--dump-manifest", str(new_dir / "m.yaml"), "--format", "json"]
    )
    assert old_out.exit_code == 0 and new_out.exit_code == 0
    old_data = json.loads(old_out.output)
    new_data = json.loads(new_out.output)
    assert old_data["scope_fingerprint"] == new_data["scope_fingerprint"]


def test_plan_output_written_to_file(tmp_path, runner):
    manifest = _write_manifest(
        tmp_path / "m.yaml",
        "roots: [a.h]\ntranslation_units:\n  - name: main\n    forced_includes: [a.h]\n",
    )
    (tmp_path / "a.h").write_text("int f(void);\n")
    out = tmp_path / "plan.json"
    result = runner.invoke(
        main,
        [
            "plan", "--dump-manifest", str(manifest),
            "--format", "json", "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["scope_fingerprint"].startswith("sha256:")
