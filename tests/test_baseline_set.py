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

"""Unit tests for ``abicheck/buildsource/baseline_set.py`` (G30 P1.2,
ADR-047 §4/§6).

Pure-Python tests over hand-authored ``manifest.json``/snapshot/binary
fixtures -- no compiler, no real ``abicheck dump``/``actions/baseline`` run
needed. See ``tests/test_action_resolve_baseline.py`` for the bash/CLI-level
orchestration this module's logic backs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.baseline_set import (
    BASELINE_MANIFEST_FILENAME,
    ResolveOutcome,
    load_baseline_manifest,
    resolve_bundle,
    resolve_target,
)

PROFILE = "linux-x86_64-gcc13-release"


def _write_manifest(
    baseline_dir: Path,
    *,
    manifest_version: int | None = 1,
    profile: str = PROFILE,
    fact_set: dict | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": manifest_version,
        "project_ref": "v1.0.0",
        "profile": profile,
        "snapshot_schema": 9,
        "fact_set": fact_set,
        "artifacts": artifacts if artifacts is not None else [],
    }
    (baseline_dir / BASELINE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _target_artifact(
    name: str, *, snapshot: bool = True, extra: dict | None = None
) -> dict:
    entry = {
        "library": name,
        "artifact": f"build/{name}.so",
        "snapshot": f"{name}.abicheck.json" if snapshot else "",
        "sha256": "deadbeef",
    }
    if extra:
        entry.update(extra)
    return entry


# ── load_baseline_manifest ───────────────────────────────────────────────


def test_load_baseline_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert load_baseline_manifest(tmp_path) is None


def test_load_baseline_manifest_malformed_json_raises(tmp_path: Path) -> None:
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_baseline_manifest(tmp_path)


def test_load_baseline_manifest_not_a_dict_raises(tmp_path: Path) -> None:
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_baseline_manifest(tmp_path)


def test_load_baseline_manifest_round_trip(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "wrapper",
            "producer_version": "0.5.0",
        },
        artifacts=[_target_artifact("libpvxs")],
    )
    manifest = load_baseline_manifest(tmp_path)
    assert manifest is not None
    assert manifest.manifest_version == 1
    assert manifest.profile == PROFILE
    assert manifest.fact_set == {
        "name": "pvxs",
        "version": 3,
        "producer": "wrapper",
        "producer_version": "0.5.0",
    }
    artifact = manifest.artifact_for("libpvxs")
    assert artifact is not None
    assert artifact.snapshot == "libpvxs.abicheck.json"
    assert manifest.artifact_for("nope") is None


# ── resolve_target ───────────────────────────────────────────────────────


def test_resolve_target_not_found_required_is_hard_failure(tmp_path: Path) -> None:
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is False
    assert not result.ok


def test_resolve_target_not_found_not_required_is_bootstrap(tmp_path: Path) -> None:
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=False)
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is True


def test_resolve_target_stale_schema(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path, manifest_version=999, artifacts=[_target_artifact("libpvxs")]
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA


def test_resolve_target_wrong_profile(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        profile="windows-x86_64-msvc-release",
        artifacts=[_target_artifact("libpvxs")],
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.WRONG_PROFILE


def test_resolve_target_ambiguous_target_missing_from_set(tmp_path: Path) -> None:
    _write_manifest(tmp_path, artifacts=[_target_artifact("libpvxsIoc")])
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "libpvxsIoc" in result.message


def test_resolve_target_ambiguous_snapshot_file_missing_on_disk(tmp_path: Path) -> None:
    _write_manifest(tmp_path, artifacts=[_target_artifact("libpvxs")])
    # No libpvxs.abicheck.json actually written to disk.
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS


def test_resolve_target_incompatible_evidence_producer_mismatch(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "wrapper",
            "producer_version": "0.5.0",
        },
        artifacts=[_target_artifact("libpvxs")],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(
        tmp_path,
        target="libpvxs",
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={
            "kind": "replay",
            "tool": "abicheck",
            "version": "0.5.0",
        },
    )
    assert result.outcome == ResolveOutcome.INCOMPATIBLE_EVIDENCE
    assert "wrapper" in result.message and "replay" in result.message


def test_resolve_target_incompatible_evidence_producer_version_mismatch(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "wrapper",
            "producer_version": "0.5.0",
        },
        artifacts=[_target_artifact("libpvxs")],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(
        tmp_path,
        target="libpvxs",
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={
            "kind": "wrapper",
            "tool": "abicheck-cc",
            "version": "0.6.0",
        },
    )
    assert result.outcome == ResolveOutcome.INCOMPATIBLE_EVIDENCE


def test_resolve_target_evidence_check_skipped_when_baseline_has_no_fact_set(
    tmp_path: Path,
) -> None:
    # A binary/header-depth-only baseline (no --build-info/--sources) has no
    # fact_set at all -- a candidate that *does* declare an evidence
    # producer must not be penalized for a comparison that isn't meaningful.
    _write_manifest(tmp_path, fact_set=None, artifacts=[_target_artifact("libpvxs")])
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(
        tmp_path,
        target="libpvxs",
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={
            "kind": "wrapper",
            "tool": "abicheck-cc",
            "version": "0.6.0",
        },
    )
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_resolved(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "wrapper",
            "producer_version": "0.5.0",
        },
        artifacts=[_target_artifact("libpvxs")],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(
        tmp_path,
        target="libpvxs",
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={
            "kind": "wrapper",
            "tool": "abicheck-cc",
            "version": "0.5.0",
        },
    )
    assert result.outcome == ResolveOutcome.RESOLVED
    assert result.ok
    assert result.bootstrap is False
    assert result.snapshot_path == str(tmp_path / "libpvxs.abicheck.json")
    assert result.manifest_path == str(tmp_path / BASELINE_MANIFEST_FILENAME)


# ── resolve_bundle ────────────────────────────────────────────────────────


def _write_binary(baseline_dir: Path, rel_path: str) -> None:
    full = baseline_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x7fELF-fake-binary-contents")


def test_resolve_bundle_not_found_required(tmp_path: Path) -> None:
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is False


def test_resolve_bundle_not_found_bootstrap(tmp_path: Path) -> None:
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=False,
    )
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is True


def test_resolve_bundle_wrong_profile(tmp_path: Path) -> None:
    _write_manifest(tmp_path, profile="windows-x86_64-msvc-release")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.WRONG_PROFILE


def test_resolve_bundle_ambiguous_when_member_has_no_binary_field(
    tmp_path: Path,
) -> None:
    # Snapshots exist for both members, but the manifest was produced by a
    # non-bundle-aware actions/baseline (no "binary" field / binaries/ dir
    # staged yet, pre-G30-P1.6) -- bundle resolution must not silently fall
    # back to snapshots (build_bundle_snapshot() ignores them).
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs"), _target_artifact("libpvxsIoc")],
    )
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "libpvxs" in result.message and "libpvxsIoc" in result.message


def test_resolve_bundle_ambiguous_when_staged_binary_file_missing(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so.1.5"}),
            _target_artifact(
                "libpvxsIoc", extra={"binary": "binaries/libpvxsIoc.so.1.5"}
            ),
        ],
    )
    # Only one of the two declared binaries actually exists on disk.
    _write_binary(tmp_path, "binaries/libpvxs.so.1.5")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "['libpvxsIoc']" in result.message


def test_resolve_bundle_incompatible_evidence(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "wrapper",
            "producer_version": "0.5.0",
        },
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so.1.5"}),
            _target_artifact(
                "libpvxsIoc", extra={"binary": "binaries/libpvxsIoc.so.1.5"}
            ),
        ],
    )
    _write_binary(tmp_path, "binaries/libpvxs.so.1.5")
    _write_binary(tmp_path, "binaries/libpvxsIoc.so.1.5")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={
            "kind": "replay",
            "tool": "abicheck",
            "version": "0.5.0",
        },
    )
    assert result.outcome == ResolveOutcome.INCOMPATIBLE_EVIDENCE


def test_resolve_bundle_resolved_returns_binaries_not_snapshots(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so.1.5"}),
            _target_artifact(
                "libpvxsIoc", extra={"binary": "binaries/libpvxsIoc.so.1.5"}
            ),
        ],
    )
    _write_binary(tmp_path, "binaries/libpvxs.so.1.5")
    _write_binary(tmp_path, "binaries/libpvxsIoc.so.1.5")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.RESOLVED
    assert result.ok
    assert result.snapshot_path is None
    assert result.binaries_dir == str(tmp_path / "binaries")
    assert result.binary_paths == {
        "libpvxs": str(tmp_path / "binaries" / "libpvxs.so.1.5"),
        "libpvxsIoc": str(tmp_path / "binaries" / "libpvxsIoc.so.1.5"),
    }
    for path in result.binary_paths.values():
        assert Path(path).is_file()
