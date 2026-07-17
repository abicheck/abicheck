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
    created_at: str = "2026-07-17T00:00:00+00:00",
) -> None:
    data = {
        "library": library,
        "version": "1.0.0",
        "schema_version": schema_version,
        "git_commit": git_commit,
        "git_tag": None,
        "created_at": created_at,
        "build_id": None,
    }
    if fact_set is not None:
        # Matches the real SourceAbiSurface.to_dict() shape
        # (abicheck/buildsource/source_abi.py): there is no top-level
        # "fact_set" key, only "coverage": {"fact_set": ...}, written by
        # source_link.link_source_abi() (Codex review).
        data["build_source"] = {"source_abi": {"coverage": {"fact_set": fact_set}}}
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

    def test_fails_when_schema_version_inconsistent_across_libraries(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): every dump in one baseline-set run
        # goes through the same installed abicheck, so disagreeing
        # schema_versions means something is structurally broken -- silently
        # taking max() published a manifest whose declared schema didn't
        # match what one of its own snapshots actually used.
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", schema_version=9
        )
        _write_snapshot(
            tmp_path / "libbar.abicheck.json", library="libbar", schema_version=10
        )
        entries = [
            {"name": "libfoo", "artifact": "a.so"},
            {"name": "libbar", "artifact": "b.so"},
        ]
        with pytest.raises(SystemExit, match="disagree on schema_version"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, None)

    def test_fact_set_read_from_real_snapshot_shape(self, tmp_path: Path) -> None:
        # Regression (Codex review): SourceAbiSurface.to_dict() has no
        # top-level "fact_set" key -- link_source_abi() writes the rolled-up
        # identity to surface.coverage["fact_set"]. Written directly (not via
        # the _write_snapshot helper) to pin the exact real production
        # schema, so this can't pass merely because the helper and the reader
        # drifted together.
        snap_path = tmp_path / "libfoo.abicheck.json"
        snap_path.write_text(
            json.dumps(
                {
                    "library": "libfoo",
                    "version": "1.0.0",
                    "schema_version": 9,
                    "git_commit": None,
                    "git_tag": None,
                    "created_at": None,
                    "build_id": None,
                    "build_source": {
                        "source_abi": {
                            "coverage": {
                                "fact_set": {
                                    "name": "abicheck-clang-canonical",
                                    "version": 1,
                                },
                                "exported_symbols": 3,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["fact_set"] == {
            "name": "abicheck-clang-canonical",
            "version": 1,
        }

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

    def test_fails_when_fact_set_inconsistent_across_libraries(
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
        # Disagreement is a hard failure (CodeRabbit review) -- silently
        # picking one side's identity, or discarding it to None, would let a
        # later manifest with the *same* silently-discarded state compare
        # equal and report no refresh, hiding a real recipe-identity drift.
        with pytest.raises(SystemExit, match="disagree on fact_set identity"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, None)

    def test_fails_when_fact_set_present_on_only_some_libraries(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): libfoo carries a fact_set, libbar
        # carries none -- fact_set_ids only ever gets libfoo's one entry, so
        # len(fact_set_ids) == 1 looked "consistent" even though libbar has
        # no source-fact evidence at all.
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json",
            library="libfoo",
            fact_set={"name": "abicheck-clang-canonical", "version": 1},
        )
        _write_snapshot(tmp_path / "libbar.abicheck.json", library="libbar")
        entries = [
            {"name": "libfoo", "artifact": "a.so"},
            {"name": "libbar", "artifact": "b.so"},
        ]
        with pytest.raises(SystemExit, match="whether source-fact evidence is present"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, None)

    def test_fails_when_schema_version_missing(self, tmp_path: Path) -> None:
        # Regression (CodeRabbit review): a snapshot with schema_version
        # omitted entirely was silently skipped (never added to
        # schema_versions), so a single library -- or every library -- with
        # a missing schema_version passed with no error, publishing a
        # manifest whose snapshot_schema silently lost that information.
        path = tmp_path / "libfoo.abicheck.json"
        data = {
            "library": "libfoo",
            "version": "1.0.0",
            "schema_version": None,
            "git_commit": None,
            "git_tag": None,
            "created_at": "2026-07-17T00:00:00+00:00",
            "build_id": None,
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        with pytest.raises(SystemExit, match="missing schema_version"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, None)

    def test_fails_when_fact_set_is_malformed(self, tmp_path: Path) -> None:
        # Regression (CodeRabbit review): a present-but-malformed fact_set
        # (e.g. missing "version") used to be silently reclassified as
        # fact_set_absent -- collapsing corrupted evidence into "no
        # evidence" instead of surfacing the corruption.
        path = tmp_path / "libfoo.abicheck.json"
        data = {
            "library": "libfoo",
            "version": "1.0.0",
            "schema_version": 9,
            "git_commit": None,
            "git_tag": None,
            "created_at": "2026-07-17T00:00:00+00:00",
            "build_id": None,
            "build_source": {
                "source_abi": {
                    "coverage": {"fact_set": {"name": "abicheck-clang-canonical"}}
                }
            },
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        with pytest.raises(SystemExit, match="malformed fact_set identity"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, None)


class TestFreshness:
    def test_no_previous_manifest_means_not_required(self, tmp_path: Path) -> None:
        _write_snapshot(tmp_path / "libfoo.abicheck.json", library="libfoo")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest = build_manifest_module.build_manifest(tmp_path, "", "", entries, None)
        assert manifest["freshness"] == {"refresh_required": False, "reasons": []}

    def test_fails_when_previous_manifest_path_given_but_missing(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): omitting --previous-manifest
        # entirely is the documented way to say "no previous baseline"
        # (action.yml). A caller that passes one pointing at a path that
        # doesn't exist has a broken workflow (typo, failed artifact
        # download) -- silently returning refresh_required=False as if the
        # comparison ran and found nothing stale would hide that.
        _write_snapshot(tmp_path / "libfoo.abicheck.json", library="libfoo")
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        missing = tmp_path / "does-not-exist.json"
        with pytest.raises(SystemExit, match="does not exist"):
            build_manifest_module.build_manifest(tmp_path, "", "", entries, missing)

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

    def test_profile_change_requires_refresh(self, tmp_path: Path) -> None:
        # previous-manifest from a different build profile (e.g.
        # linux-x86_64-gcc vs linux-x86_64-clang) is a baseline for a
        # different target entirely, not a stale copy of this one -- even
        # when schema/fact_set/library-set all otherwise match (Codex
        # review).
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
                    "profile": "linux-x86_64-gcc",
                    "snapshot_schema": 9,
                    "fact_set": None,
                    "artifacts": [{"library": "libfoo"}],
                }
            )
        )
        manifest = build_manifest_module.build_manifest(
            tmp_path, "", "linux-x86_64-clang", entries, previous_path
        )
        assert manifest["freshness"]["refresh_required"] is True
        assert any("profile" in r for r in manifest["freshness"]["reasons"])

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

    def test_sha256_stable_when_only_created_at_changes(self, tmp_path: Path) -> None:
        # Regression (Codex review): the per-artifact sha256 used to be a raw
        # file hash, so it changed on every dump call even when the actual
        # ABI content was identical -- dumper.py auto-stamps created_at fresh
        # each time (absent SOURCE_DATE_EPOCH, the normal CI case). This
        # rippled into content-digest too, since that's built from these
        # per-artifact digests.
        snap_path = tmp_path / "libfoo.abicheck.json"
        _write_snapshot(
            snap_path,
            library="libfoo",
            git_commit="aaa",
            created_at="2026-07-17T00:00:00+00:00",
        )
        entries = [{"name": "libfoo", "artifact": "a.so"}]
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        _write_snapshot(
            snap_path,
            library="libfoo",
            git_commit="aaa",
            created_at="2026-07-17T01:23:45+00:00",
        )
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

    def test_sha256_stable_when_only_embedded_build_source_created_at_changes(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): a snapshot dumped with --build-info/
        # --sources embeds a *second*, independently-stamped timestamp at
        # build_source.manifest.created_at (BuildSourceManifest.created_at,
        # written fresh by every collect-facts run). Stripping only the
        # top-level created_at (the fix above) left this nested one in
        # place, so the digest was still unstable for any baseline with
        # embedded source facts.
        snap_path = tmp_path / "libfoo.abicheck.json"

        def _write(build_source_created_at: str) -> None:
            data = {
                "library": "libfoo",
                "version": "1.0.0",
                "schema_version": 9,
                "git_commit": "aaa",
                "git_tag": None,
                "created_at": "2026-07-17T00:00:00+00:00",
                "build_id": None,
                "build_source": {
                    "manifest": {
                        "build_source_pack_version": 1,
                        "created_at": build_source_created_at,
                    },
                    "source_abi": {"coverage": {}},
                },
            }
            snap_path.write_text(json.dumps(data), encoding="utf-8")

        entries = [{"name": "libfoo", "artifact": "a.so"}]
        _write("2026-07-17T00:00:00+00:00")
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        _write("2026-07-17T01:23:45+00:00")
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )

        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

    def test_sha256_stable_when_only_source_mtime_changes(self, tmp_path: Path) -> None:
        # Regression (Codex review): AbiSnapshot.source_mtime/source_mtime_epoch
        # (abicheck/model.py) reflect the dumped binary's filesystem mtime at
        # dump time -- a fresh CI checkout of byte-identical source gets a new
        # mtime every run, so hashing these made the digest unstable even
        # though the actual ABI content never changed.
        snap_path = tmp_path / "libfoo.abicheck.json"

        def _write(source_mtime: float) -> None:
            data = {
                "library": "libfoo",
                "version": "1.0.0",
                "schema_version": 9,
                "git_commit": "aaa",
                "git_tag": None,
                "created_at": "2026-07-17T00:00:00+00:00",
                "build_id": None,
                "source_mtime": source_mtime,
                "source_mtime_epoch": False,
            }
            snap_path.write_text(json.dumps(data), encoding="utf-8")

        entries = [{"name": "libfoo", "artifact": "a.so"}]
        _write(1000.0)
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        _write(2000.0)
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

    def test_sha256_stable_when_only_replay_timing_counters_change(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): source_replay.py's replay producer
        # stamps wall-clock durations and cache hit/miss counts into
        # build_source.source_abi.coverage (cache_lookup_s, extract_s,
        # link_s, elapsed_s, cache_misses, cache_hits) -- these depend on the
        # runner's cache warmth/load, not on the source-fact content, so
        # hashing them made the digest unstable across reruns of identical
        # source facts.
        snap_path = tmp_path / "libfoo.abicheck.json"

        def _write(elapsed_s: float, cache_misses: int) -> None:
            data = {
                "library": "libfoo",
                "version": "1.0.0",
                "schema_version": 9,
                "git_commit": "aaa",
                "git_tag": None,
                "created_at": "2026-07-17T00:00:00+00:00",
                "build_id": None,
                "build_source": {
                    "source_abi": {
                        "coverage": {
                            "cache_lookup_s": 0.1,
                            "extract_s": 1.5,
                            "link_s": 0.2,
                            "elapsed_s": elapsed_s,
                            "cache_misses": cache_misses,
                            "cache_hits": 3,
                            "compile_units_parsed": 12,
                        }
                    }
                },
            }
            snap_path.write_text(json.dumps(data), encoding="utf-8")

        entries = [{"name": "libfoo", "artifact": "a.so"}]
        _write(1.8, 0)
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        _write(3.4, 5)
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

    def test_sha256_stable_when_only_manifest_coverage_row_changes(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): build_inline_coverage()
        # (abicheck/buildsource/inline.py) copies the same cache/timing
        # state that source_abi.coverage carries into each
        # build_source.manifest.coverage row's "detail" (a free-form string
        # like "cache 2/3 hit (67%), 1.80s") and "elapsed_s" -- a third
        # place the same volatile info leaks into, so stripping only
        # source_abi.coverage's volatile keys still left the digest
        # unstable.
        snap_path = tmp_path / "libfoo.abicheck.json"

        def _write(detail: str, elapsed_s: float) -> None:
            data = {
                "library": "libfoo",
                "version": "1.0.0",
                "schema_version": 9,
                "git_commit": "aaa",
                "git_tag": None,
                "created_at": "2026-07-17T00:00:00+00:00",
                "build_id": None,
                "build_source": {
                    "manifest": {
                        "build_source_pack_version": 1,
                        "coverage": [
                            {
                                "layer": "L4_source_abi",
                                "status": "present",
                                "confidence": "high",
                                "detail": detail,
                                "elapsed_s": elapsed_s,
                            }
                        ],
                    },
                    "source_abi": {"coverage": {}},
                },
            }
            snap_path.write_text(json.dumps(data), encoding="utf-8")

        entries = [{"name": "libfoo", "artifact": "a.so"}]
        _write("cache 2/3 hit (67%), 1.80s", 1.8)
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        _write("cache 0/3 hit (0%), 3.40s", 3.4)
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

    def test_sha256_stable_when_only_manifest_extractor_row_changes(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): inline_graph_fold.py appends
        # "N.NNs, jobs=M" (last_elapsed_s/last_jobs) to a
        # build_source.manifest.extractors row's "detail", and
        # started_at/finished_at are ISO 8601 wall-clock bounds -- runner
        # load/CPU count and collection time, not source-fact content, so a
        # fourth place the same volatile-info-in-a-row-field shape leaked
        # into the digest.
        snap_path = tmp_path / "libfoo.abicheck.json"

        def _write(detail: str, started_at: str, finished_at: str) -> None:
            data = {
                "library": "libfoo",
                "version": "1.0.0",
                "schema_version": 9,
                "git_commit": "aaa",
                "git_tag": None,
                "created_at": "2026-07-17T00:00:00+00:00",
                "build_id": None,
                "build_source": {
                    "manifest": {
                        "build_source_pack_version": 1,
                        "extractors": [
                            {
                                "name": "compile_commands",
                                "version": "1.0",
                                "status": "ok",
                                "inputs": [],
                                "artifacts": [],
                                "detail": detail,
                                "started_at": started_at,
                                "finished_at": finished_at,
                            }
                        ],
                    },
                    "source_abi": {"coverage": {}},
                },
            }
            snap_path.write_text(json.dumps(data), encoding="utf-8")

        entries = [{"name": "libfoo", "artifact": "a.so"}]
        _write(
            "1.80s, jobs=4", "2026-07-17T00:00:00+00:00", "2026-07-17T00:00:02+00:00"
        )
        manifest1 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        _write(
            "3.40s, jobs=8", "2026-07-17T01:00:00+00:00", "2026-07-17T01:00:04+00:00"
        )
        manifest2 = build_manifest_module.build_manifest(
            tmp_path, "", "", entries, None
        )
        assert (
            manifest1["artifacts"][0]["sha256"] == manifest2["artifacts"][0]["sha256"]
        )

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

    def _run_main_and_get_content_digest(
        self, tmp_path: Path, libraries: list[dict[str, str]], capsys
    ) -> str:
        # Overwritten on every call -- only the printed content-digest= line
        # matters to these tests, not the manifest file's own path/identity.
        manifest_out = tmp_path / "manifest-out.json"
        rc = build_manifest_module.main(
            [
                "--output-dir",
                str(tmp_path),
                "--libraries",
                json.dumps(libraries),
                "--manifest-out",
                str(manifest_out),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        (line,) = [ln for ln in out.splitlines() if ln.startswith("content-digest=")]
        return line.removeprefix("content-digest=")

    def test_content_digest_via_cli_stable_across_artifact_path_and_order(
        self, tmp_path: Path, capsys
    ) -> None:
        # CodeRabbit review: the aggregate content-digest output is
        # documented as "library names + per-file digests" -- it must not
        # move just because a matrix reordered entries or an artifact was
        # built at a different path this run.
        _write_snapshot(tmp_path / "libfoo.abicheck.json", library="libfoo")
        _write_snapshot(tmp_path / "libbar.abicheck.json", library="libbar")

        digest1 = self._run_main_and_get_content_digest(
            tmp_path,
            [
                {"name": "libfoo", "artifact": "build/libfoo.so"},
                {"name": "libbar", "artifact": "build/libbar.so"},
            ],
            capsys,
        )
        digest2 = self._run_main_and_get_content_digest(
            tmp_path,
            [
                {"name": "libbar", "artifact": "elsewhere/libbar.so"},
                {"name": "libfoo", "artifact": "elsewhere/libfoo.so"},
            ],
            capsys,
        )
        assert digest1 == digest2

    def test_content_digest_via_cli_changes_when_snapshot_content_changes(
        self, tmp_path: Path, capsys
    ) -> None:
        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", git_commit="aaa"
        )
        libraries = [{"name": "libfoo", "artifact": "a.so"}]
        digest1 = self._run_main_and_get_content_digest(tmp_path, libraries, capsys)

        _write_snapshot(
            tmp_path / "libfoo.abicheck.json", library="libfoo", git_commit="bbb"
        )
        digest2 = self._run_main_and_get_content_digest(tmp_path, libraries, capsys)
        assert digest1 != digest2
