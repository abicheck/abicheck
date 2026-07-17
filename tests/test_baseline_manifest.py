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
"""Unit tests for ``actions/baseline/build_manifest.py``.

Pure-Python tests over synthetic snapshot JSON files (no compiler, no real
`abicheck dump` needed) -- ``actions/baseline/run.sh`` is what actually
produces the per-library snapshots this script reads; see
``tests/test_action_baseline.py`` for the bash-level orchestration
(including one ``integration``-marked end-to-end test with real compiled
libraries).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "actions" / "baseline" / "build_manifest.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "baseline_build_manifest", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(
    not _MODULE_PATH.is_file(), reason="actions/baseline/build_manifest.py not found"
)
build_manifest_module = _load_module() if _MODULE_PATH.is_file() else None


def _write_snapshot(
    path: Path,
    *,
    library: str,
    schema_version: int = 9,
    git_commit: str | None = None,
    fact_set: dict | None = None,
) -> None:
    data = {
        "library": library,
        "version": "1.0.0",
        "schema_version": schema_version,
        "git_commit": git_commit,
        "git_tag": None,
        "created_at": "2026-07-17T00:00:00+00:00",
        "build_id": None,
    }
    if fact_set is not None:
        data["build_source"] = {"source_abi": {"fact_set": fact_set}}
    path.write_text(json.dumps(data), encoding="utf-8")


class TestBuildManifestBasics:
    def test_manifest_lists_every_library(self, tmp_path: Path) -> None:
        out = tmp_path
        _write_snapshot(out / "libfoo.abicheck.json", library="libfoo")
        _write_snapshot(out / "libbar.abicheck.json", library="libbar")
        entries = [
            {"name": "libfoo", "artifact": "build/libfoo.so"},
            {"name": "libbar", "artifact": "build/libbar.so"},
        ]
        manifest = build_manifest_module.build_manifest(
            out, "v1.0.0", "linux-x86_64", entries, None
        )
        assert manifest["manifest_version"] == 1
        assert manifest["project_ref"] == "v1.0.0"
        assert manifest["profile"] == "linux-x86_64"
        assert [a["library"] for a in manifest["artifacts"]] == ["libfoo", "libbar"]
        assert all(a["sha256"] for a in manifest["artifacts"])

    def test_missing_snapshot_raises(self, tmp_path: Path) -> None:
        entries = [{"name": "libfoo", "artifact": "build/libfoo.so"}]
        with pytest.raises(SystemExit, match="libfoo"):
            build_manifest_module.build_manifest(tmp_path, "v1.0.0", "", entries, None)

    def test_snapshot_schema_is_recorded(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        entries = [{"name": "libfoo", "artifact": "build/libfoo.so"}]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["snapshot_schema"] == 9

    def test_fact_set_recorded_when_consistent(self, tmp_path: Path) -> None:
        fact_set = {"name": "abicheck-clang-canonical", "version": 1}
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", fact_set=fact_set
        )
        _write_snapshot(
            tmp_path / "libbar.abicheck.json", library="libbar", fact_set=fact_set
        )
        entries = [
            {"name": "libfoo", "artifact": "a.so"},
            {"name": "libbar", "artifact": "b.so"},
        ]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["fact_set"] == fact_set

    def test_fact_set_none_when_absent(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path / "libfoo.abicheck.json", library="libfoo")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["fact_set"] is None

    def test_fact_set_none_when_inconsistent_across_libraries(
        self, tmp_path: Path
    ) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json",
            library="libfoo",
            fact_set={"name": "abicheck-clang-canonical", "version": 1},
        )
        _write_snapshot(
            tmp_path / "libbar.abicheck.json",
            library="libbar",
            fact_set={"name": "abicheck-clang-canonical", "version": 2},
        )
        entries = [
            {"name": "libfoo", "artifact": "a.so"},
            {"name": "libbar", "artifact": "b.so"},
        ]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        # Disagreement is reported (::warning:: to stderr) rather than
        # silently picking one side's identity as if it applied to both.
        assert manifest["fact_set"] is None


class TestFreshness:
    def test_no_previous_manifest_means_not_required(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path / "libfoo.abicheck.json", library="libfoo")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["freshness"] == {"refresh_required": False, "reasons": []}

    def test_identical_manifest_is_not_stale(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        first = build_manifest_module.build_manifest(tmp_path, "v1", "p", entries, None)
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(json.dumps(first))

        second = build_manifest_module.build_manifest(
            tmp_path, "v1", "p", entries, previous_path
        )
        assert second["freshness"]["refresh_required"] is False
        assert second["freshness"]["reasons"] == []

    def test_schema_version_bump_requires_refresh(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": "",
                    "profile": "",
                    "snapshot_schema": 8,
                    "fact_set": None,
                    "artifacts": [{"library": "libfoo"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, previous_path
        )
        assert manifest["freshness"]["refresh_required"] is True
        assert any("snapshot_schema" in r for r in manifest["freshness"]["reasons"])

    def test_added_library_requires_refresh(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        _write_snapshot(
            tmp_path / "libbar.abicheck.json", library="libbar", schema_version=9
        )
        entries = [
            {"name": "libfoo", "artifact": "a.so"},
            {"name": "libbar", "artifact": "b.so"},
        ]
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": "",
                    "profile": "",
                    "snapshot_schema": 9,
                    "fact_set": None,
                    "artifacts": [{"library": "libfoo"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, previous_path
        )
        assert manifest["freshness"]["refresh_required"] is True
        assert any(
            "added" in r and "libbar" in r for r in manifest["freshness"]["reasons"]
        )

    def test_removed_library_requires_refresh(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": "",
                    "profile": "",
                    "snapshot_schema": 9,
                    "fact_set": None,
                    "artifacts": [{"library": "libfoo"}, {"library": "libbar"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, previous_path
        )
        assert manifest["freshness"]["refresh_required"] is True
        assert any(
            "removed" in r and "libbar" in r for r in manifest["freshness"]["reasons"]
        )

    def test_fact_set_change_requires_refresh(self, tmp_path: Path) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json",
            library="libfoo",
            schema_version=9,
            fact_set={"name": "abicheck-clang-canonical", "version": 2},
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": "",
                    "profile": "",
                    "snapshot_schema": 9,
                    "fact_set": {"name": "abicheck-clang-canonical", "version": 1},
                    "artifacts": [{"library": "libfoo"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, previous_path
        )
        assert manifest["freshness"]["refresh_required"] is True
        assert any("fact_set" in r for r in manifest["freshness"]["reasons"])

    def test_fact_set_producer_change_requires_refresh(self, tmp_path: Path) -> None:
        # Same fact_set name/version, but a different producer_version (e.g.
        # a rebuilt Clang plugin) -- the recipe identity changed even though
        # the two-field identity alone would look unchanged.
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json",
            library="libfoo",
            schema_version=9,
            fact_set={
                "name": "abicheck-clang-canonical",
                "version": 1,
                "compiler_family": "clang",
                "producer": "clang-plugin",
                "producer_version": "2.0",
                "compiler_version": "18.1.0",
            },
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        previous_path = tmp_path / "previous.json"
        previous_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": "",
                    "profile": "",
                    "snapshot_schema": 9,
                    "fact_set": {
                        "name": "abicheck-clang-canonical",
                        "version": 1,
                        "compiler_family": "clang",
                        "producer": "clang-plugin",
                        "producer_version": "1.0",
                        "compiler_version": "18.1.0",
                    },
                    "artifacts": [{"library": "libfoo"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, previous_path
        )
        assert manifest["fact_set"]["producer_version"] == "2.0"
        assert manifest["freshness"]["refresh_required"] is True
        assert any("fact_set" in r for r in manifest["freshness"]["reasons"])


class TestMainCli:
    def test_main_writes_manifest_and_prints_outputs(
        self, tmp_path: Path, capsys
    ) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        libraries = json.dumps([{"name": "libfoo", "artifact": "a.so"}])
        manifest_out = tmp_path / "manifest.json"
        rc = build_manifest_module.main(
            [
                "--output-dir",
                str(tmp_path),
                "--project-ref",
                "v1.0.0",
                "--profile",
                "linux",
                "--libraries",
                libraries,
                "--manifest-out",
                str(manifest_out),
            ]
        )
        assert rc == 0
        assert manifest_out.is_file()
        out = capsys.readouterr().out
        assert "library-count=1" in out
        assert "refresh-required=false" in out
        assert "content-digest=" in out

    def test_content_digest_changes_when_snapshot_content_changes(
        self, tmp_path: Path
    ) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", git_commit="aaa"
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", git_commit="bbb"
        )
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        digest1 = manifest1["artifacts"][0]["sha256"]
        digest2 = manifest2["artifacts"][0]["sha256"]
        assert digest1 != digest2
