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

import hashlib
import json
from pathlib import Path

import pytest

from abicheck import serialization
from abicheck.buildsource.baseline_set import (
    BASELINE_MANIFEST_FILENAME,
    ResolveOutcome,
    ResolveResult,
    compute_snapshot_content_hash,
    load_baseline_manifest,
    resolve_bundle,
    resolve_target,
    strip_volatile_snapshot_fields,
)
from abicheck.elf_metadata import (
    ElfMetadata,
    parse_elf_metadata as _real_parse_elf_metadata,
)

PROFILE = "linux-x86_64-gcc13-release"


@pytest.fixture(autouse=True)
def _stub_bundle_elf_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bundle-resolution tests stage placeholder bytes (not a real ELF
    structure) under ``binaries/`` -- stub the deep ELF parse
    ``_not_elf_issue()`` runs (added to catch truncated/corrupted staged
    binaries that still pass the magic-byte sniff, Codex review) so those
    tests exercise ``resolve_bundle()``'s path/digest/output plumbing
    rather than requiring a hand-built, fully valid ELF fixture.
    ``test_resolve_bundle_rejects_truncated_elf_binary`` below restores
    the real parser to test the deep-parse guard itself.
    """
    monkeypatch.setattr(
        "abicheck.buildsource.baseline_set.parse_elf_metadata",
        lambda path: ElfMetadata(soname=path.name),
    )


def _write_manifest(
    baseline_dir: Path,
    *,
    manifest_version: int | None = 1,
    profile: str = PROFILE,
    snapshot_schema: int | None = 9,
    fact_set: dict | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": manifest_version,
        "project_ref": "v1.0.0",
        "profile": profile,
        "snapshot_schema": snapshot_schema,
        "fact_set": fact_set,
        "artifacts": artifacts if artifacts is not None else [],
    }
    (baseline_dir / BASELINE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _target_artifact(
    name: str, *, snapshot: bool = True, extra: dict | None = None
) -> dict:
    # No "sha256" by default -- an empty/absent recorded digest means
    # resolve_target()'s digest-verification check has nothing to compare
    # against and no-ops (see _snapshot_digest_issue), so tests that aren't
    # specifically about digest verification aren't coupled to the exact
    # bytes a fixture snapshot happens to contain. Tests that DO want digest
    # verification pass extra={"sha256": compute_snapshot_content_hash(...)}
    # explicitly (see the TestSnapshotDigestVerification-equivalent cases
    # below).
    entry = {
        "library": name,
        "artifact": f"build/{name}.so",
        "snapshot": f"{name}.abicheck.json" if snapshot else "",
        "sha256": "",
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


def test_load_baseline_manifest_invalid_utf8_raises_value_error(tmp_path: Path) -> None:
    # UnicodeDecodeError (raised by the text-mode read itself) is a
    # ValueError subclass, not a json.JSONDecodeError -- must be caught
    # alongside it so this function's documented "raises ValueError"
    # contract holds for a manifest replaced by binary garbage too, not
    # just malformed-but-valid-UTF-8 JSON (Codex review).
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_bytes(b"\xff\xfe\x00not valid utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_baseline_manifest(tmp_path)


def test_load_baseline_manifest_os_error_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OSError (e.g. a permission error, or the file disappearing between the
    # is_file() check and open() -- a restored archive/cache race) is raised
    # by open() itself, before JSON decoding even starts -- must be caught
    # too, or a manifest that exists but can't be read escapes this
    # function's documented ValueError contract (Codex review). Simulated
    # via monkeypatch since a real permission error isn't reliably
    # reproducible when tests run as root.
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    real_open = Path.open

    def _boom(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == BASELINE_MANIFEST_FILENAME:
            raise PermissionError("synthetic permission error")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _boom)
    with pytest.raises(ValueError, match="could not be read"):
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
    # The baseline-path itself must not exist for the not_found/bootstrap
    # case -- an *existing* directory with no manifest.json is a distinct,
    # more concerning failure (see
    # test_resolve_target_existing_dir_without_manifest_is_ambiguous below).
    missing = tmp_path / "does-not-exist"
    result = resolve_target(missing, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is False
    assert not result.ok


def test_resolve_target_not_found_not_required_is_bootstrap(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = resolve_target(missing, target="libpvxs", profile=PROFILE, required=False)
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is True


def test_resolve_target_existing_dir_without_manifest_is_ambiguous(
    tmp_path: Path,
) -> None:
    # An existing baseline-path directory (e.g. an empty/partial
    # actions/cache restore) with no manifest.json inside it is malformed,
    # not simply "unpublished" -- it must not silently bootstrap a
    # required=False caller to a green run with zero comparison performed
    # (Codex review).
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=False)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert result.bootstrap is False
    assert "does not contain a" in result.message
    assert not result.ok


def test_resolve_target_stale_schema(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path, manifest_version=999, artifacts=[_target_artifact("libpvxs")]
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA


def test_resolve_target_snapshot_schema_newer_than_reader_is_stale_schema(
    tmp_path: Path,
) -> None:
    # manifest_version (this resolver's own manifest.json shape) is a
    # separate axis from snapshot_schema (the referenced .abicheck.json
    # snapshots' serialization.SCHEMA_VERSION) -- a baseline built by a
    # newer abicheck than this checkout's installed reader must be caught
    # here as stale_schema, not silently reported resolved only to fail
    # opaquely in the later compare step (Codex review).
    _write_manifest(
        tmp_path,
        snapshot_schema=serialization.SCHEMA_VERSION + 1,
        artifacts=[_target_artifact("libpvxs")],
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA
    assert "snapshot_schema" in result.message


def test_resolve_target_snapshot_schema_at_reader_version_resolves(
    tmp_path: Path,
) -> None:
    _write_manifest(
        tmp_path,
        snapshot_schema=serialization.SCHEMA_VERSION,
        artifacts=[_target_artifact("libpvxs")],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_missing_snapshot_schema_is_a_noop(tmp_path: Path) -> None:
    # An older/hand-authored manifest with no snapshot_schema field has
    # nothing to compare against -- must not be treated as "newer than
    # supported" by accident (same no-op-on-absence contract as the digest
    # checks).
    _write_manifest(
        tmp_path, snapshot_schema=None, artifacts=[_target_artifact("libpvxs")]
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_snapshot_own_schema_newer_than_reader_is_stale_schema(
    tmp_path: Path,
) -> None:
    # _schema_and_profile_check only looks at the manifest's aggregate
    # snapshot_schema field, which an older/hand-authored manifest may
    # omit entirely -- but the snapshot file itself always carries its own
    # schema_version. Without checking that too, this would previously
    # resolve as RESOLVED (only JSON-shape validated) and fail opaquely in
    # the later compare step instead of returning the typed stale_schema
    # outcome resolve-baseline exists to give callers (Codex review).
    _write_manifest(
        tmp_path, snapshot_schema=None, artifacts=[_target_artifact("libpvxs")]
    )
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps({"schema_version": serialization.SCHEMA_VERSION + 1}),
        encoding="utf-8",
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA
    assert "schema_version" in result.message


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


def test_resolve_target_ambiguous_empty_snapshot_filename(tmp_path: Path) -> None:
    # A manifest entry with a "library"/"artifact" but an empty "snapshot"
    # filename (snapshot=False) is a distinct problem from a missing file on
    # disk -- the manifest itself never named one.
    _write_manifest(tmp_path, artifacts=[_target_artifact("libpvxs", snapshot=False)])
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "no snapshot filename" in result.message


def test_resolve_target_ambiguous_duplicate_manifest_entries(tmp_path: Path) -> None:
    # A real actions/baseline-produced manifest can never have two entries
    # for the same library (run.sh's own input validation already rejects
    # a duplicate library name before anything is dumped) -- two rows here
    # means a hand-edited or corrupted manifest, and artifact_for() would
    # otherwise silently pick whichever one happens to appear first
    # (Codex review).
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"snapshot": "a.abicheck.json"}),
            _target_artifact("libpvxs", extra={"snapshot": "b.abicheck.json"}),
        ],
    )
    (tmp_path / "a.abicheck.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "multiple artifacts" in result.message


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


def test_resolve_target_version_mismatch_alone_is_not_incompatible(
    tmp_path: Path,
) -> None:
    # evidence_producer.version (candidate, package-release-styled per
    # ADR-047 section 2's own example) and fact_set.producer_version
    # (baseline, an independent internal extractor-recipe version e.g.
    # "0.7") are two incommensurable numbering schemes -- comparing them
    # directly would reject nearly every real resolution on a coincidental
    # mismatch, so this check intentionally does NOT compare version when
    # the producer kind itself matches (Codex review).
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
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_incompatible_evidence_noop_when_candidate_kind_empty(
    tmp_path: Path,
) -> None:
    # An empty/missing candidate "kind" has nothing to compare against --
    # must no-op (resolve normally), not treat an unset field as a mismatch.
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
        candidate_evidence_producer={"kind": "", "tool": "abicheck"},
    )
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_incompatible_evidence_noop_when_baseline_producer_empty(
    tmp_path: Path,
) -> None:
    # A baseline fact_set with no (or empty) "producer" recorded has nothing
    # to compare against either -- an older/hand-authored manifest, not an
    # incompatibility.
    _write_manifest(
        tmp_path,
        fact_set={"name": "pvxs", "version": 3, "producer": ""},
        artifacts=[_target_artifact("libpvxs")],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
    result = resolve_target(
        tmp_path,
        target="libpvxs",
        profile=PROFILE,
        required=True,
        candidate_evidence_producer={"kind": "wrapper", "tool": "abicheck"},
    )
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_clang_plugin_alias_matches_real_producer_id(
    tmp_path: Path,
) -> None:
    # The C++ clang-plugin extractor self-reports fact_set.producer as
    # "abicheck-clang-plugin" (contrib/abicheck-clang-plugin/
    # AbicheckFactsPlugin.cpp), not the "clang-plugin" name
    # actions/collect-facts/run.sh's own `producer` input/build-output.json
    # evidence_producer.kind uses -- must not spuriously report
    # incompatible_evidence for a candidate/baseline pair that are actually
    # the same producer (Codex review).
    _write_manifest(
        tmp_path,
        fact_set={
            "name": "pvxs",
            "version": 3,
            "producer": "abicheck-clang-plugin",
            "producer_version": "0.3",
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
            "kind": "clang-plugin",
            "tool": "abicheck-clang-plugin",
            "version": "0.x.y",
        },
    )
    assert result.outcome == ResolveOutcome.RESOLVED


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


def test_resolve_target_rejects_absolute_snapshot_path(tmp_path: Path) -> None:
    # A corrupt/hand-edited/tampered manifest.json pointing "snapshot" at an
    # absolute path must never resolve -- Path's own "/" operator silently
    # discards the left operand for an absolute right-hand side, so without
    # an explicit guard this would hand a downstream compare an arbitrary
    # file outside the baseline-set (Codex review).
    outside = tmp_path.parent / "outside.abicheck.json"
    outside.write_text("{}", encoding="utf-8")
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"snapshot": str(outside)})],
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "escapes" in result.message


def test_resolve_target_rejects_traversing_snapshot_path(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.abicheck.json"
    outside.write_text("{}", encoding="utf-8")
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"snapshot": f"../{outside.name}"})
        ],
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "escapes" in result.message


def test_resolve_target_digest_match_resolves(tmp_path: Path) -> None:
    snapshot_content = {"library": "libpvxs", "schema_version": 9}
    real_digest = compute_snapshot_content_hash(snapshot_content)
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"sha256": real_digest})],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps(snapshot_content), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_digest_mismatch_is_ambiguous(tmp_path: Path) -> None:
    # A truncated/replaced snapshot file (partial download, stale cache
    # restore) must never resolve just because a file with the right name
    # exists -- the manifest's recorded digest is what actually vouches for
    # its content (Codex review).
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"sha256": "0" * 64})],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps({"library": "libpvxs", "schema_version": 9}), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "digest does not match" in result.message


def test_resolve_target_digest_check_handles_invalid_utf8_snapshot(
    tmp_path: Path,
) -> None:
    # A snapshot replaced by non-UTF-8/binary garbage must produce the same
    # typed ambiguous outcome as any other corrupt snapshot, not an
    # unhandled UnicodeDecodeError (Codex review) -- UnicodeDecodeError is a
    # ValueError subclass, not a json.JSONDecodeError, so it needs its own
    # except clause.
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"sha256": "0" * 64})],
    )
    (tmp_path / "libpvxs.abicheck.json").write_bytes(b"\xff\xfe\x00not valid utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert not result.ok


def test_resolve_target_digest_ignores_volatile_fields(tmp_path: Path) -> None:
    # created_at is stripped before hashing (same convention as
    # actions/baseline/build_manifest.py) -- a snapshot re-dumped with a
    # fresh timestamp but otherwise identical content must still verify.
    original = {
        "library": "libpvxs",
        "schema_version": 9,
        "created_at": "2026-01-01T00:00:00Z",
    }
    real_digest = compute_snapshot_content_hash(original)
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"sha256": real_digest})],
    )
    redumped = {
        "library": "libpvxs",
        "schema_version": 9,
        "created_at": "2026-07-22T12:00:00Z",
    }
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps(redumped), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_digest_check_rejects_snapshot_that_is_not_a_json_object(
    tmp_path: Path,
) -> None:
    # A snapshot file that parses as valid JSON but isn't an object (e.g. an
    # array) must still be reported as corrupt, not crash or silently
    # compare against something with no library/schema_version fields.
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"sha256": "0" * 64})],
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("[1, 2, 3]", encoding="utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "JSON object" in result.message


def test_resolve_target_corrupt_snapshot_is_ambiguous_even_without_recorded_digest(
    tmp_path: Path,
) -> None:
    # An older/hand-authored manifest with no recorded sha256 has nothing
    # to compare a digest against, but that must not skip JSON-shape
    # validation entirely -- a corrupt/non-JSON snapshot with no recorded
    # digest was previously resolving as RESOLVED purely because a file
    # with the right name existed on disk (Codex review).
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs")],  # no sha256
    )
    (tmp_path / "libpvxs.abicheck.json").write_text("not valid json", encoding="utf-8")
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert not result.ok


def test_strip_volatile_snapshot_fields_strips_nested_build_source_coverage(
    tmp_path: Path,
) -> None:
    # compute_snapshot_content_hash's stable view must strip volatile fields
    # nested under build_source_pack/build_source.manifest(.coverage/
    # .extractors)/build_source.source_abi.coverage too, not just the
    # top-level keys -- a re-dump with fresh build-evidence timing/cache
    # counters but otherwise identical content must still verify.
    raw = {
        "library": "libpvxs",
        "created_at": "2026-01-01T00:00:00Z",
        "build_source_pack": {"path_hint": "/tmp/run1", "kind": "embedded"},
        "build_source": {
            "manifest": {
                "created_at": "2026-01-01T00:00:00Z",
                "targets": 1,
                "coverage": [{"detail": "x", "elapsed_s": 1.0, "state": "ok"}],
                "extractors": [
                    {
                        "detail": "y",
                        "started_at": "t0",
                        "finished_at": "t1",
                        "name": "clang",
                    }
                ],
            },
            "source_abi": {
                "coverage": {
                    "cache_lookup_s": 0.1,
                    "extract_s": 0.2,
                    "link_s": 0.3,
                    "elapsed_s": 0.6,
                    "cache_misses": 2,
                    "cache_hits": 5,
                    "extractor_jobs": 4,
                    "units": 10,
                },
            },
        },
    }
    redumped = {
        "library": "libpvxs",
        "created_at": "2026-07-22T12:00:00Z",
        "build_source_pack": {"path_hint": "/tmp/run2", "kind": "embedded"},
        "build_source": {
            "manifest": {
                "created_at": "2026-07-22T12:00:00Z",
                "targets": 1,
                "coverage": [{"detail": "z", "elapsed_s": 9.9, "state": "ok"}],
                "extractors": [
                    {
                        "detail": "w",
                        "started_at": "t9",
                        "finished_at": "t10",
                        "name": "clang",
                    }
                ],
            },
            "source_abi": {
                "coverage": {
                    "cache_lookup_s": 9.0,
                    "extract_s": 9.0,
                    "link_s": 9.0,
                    "elapsed_s": 9.0,
                    "cache_misses": 99,
                    "cache_hits": 99,
                    "extractor_jobs": 99,
                    "units": 10,
                },
            },
        },
    }
    stable_1 = strip_volatile_snapshot_fields(raw)
    stable_2 = strip_volatile_snapshot_fields(redumped)
    assert stable_1 == stable_2
    assert "path_hint" not in stable_1["build_source_pack"]
    assert stable_1["build_source_pack"]["kind"] == "embedded"
    assert "created_at" not in stable_1["build_source"]["manifest"]
    assert stable_1["build_source"]["manifest"]["coverage"] == [{"state": "ok"}]
    assert stable_1["build_source"]["manifest"]["extractors"] == [{"name": "clang"}]
    assert stable_1["build_source"]["source_abi"]["coverage"] == {"units": 10}
    assert compute_snapshot_content_hash(raw) == compute_snapshot_content_hash(redumped)


def test_strip_volatile_snapshot_fields_tolerates_malformed_shapes() -> None:
    # Defensive fallbacks for a hand-edited/corrupt manifest whose nested
    # fields don't match the producer's real shape -- must pass the
    # malformed value through unchanged rather than raising, mirroring this
    # whole module's "never crash on corrupt content" convention.
    raw = {
        "library": "libpvxs",
        "build_source": {
            # coverage/extractors as non-list values -- _strip_row_keys must
            # return them unchanged instead of assuming list-of-dict shape.
            "manifest": {"coverage": "not-a-list", "extractors": 42},
            # source_abi missing entirely is the common case (already
            # covered elsewhere); here it's present but not a dict.
            "source_abi": "not-a-dict",
        },
    }
    stable = strip_volatile_snapshot_fields(raw)
    assert stable["build_source"]["manifest"]["coverage"] == "not-a-list"
    assert stable["build_source"]["manifest"]["extractors"] == 42
    assert stable["build_source"]["source_abi"] == "not-a-dict"

    raw_coverage_not_dict = {
        "library": "libpvxs",
        "build_source": {"source_abi": {"coverage": "not-a-dict"}},
    }
    stable_2 = strip_volatile_snapshot_fields(raw_coverage_not_dict)
    assert stable_2["build_source"]["source_abi"]["coverage"] == "not-a-dict"


def test_resolve_result_to_dict(tmp_path: Path) -> None:
    result = ResolveResult(
        outcome=ResolveOutcome.RESOLVED,
        message="resolved target 'libpvxs'.",
        bootstrap=False,
        manifest_path=str(tmp_path / "manifest.json"),
        snapshot_path=str(tmp_path / "libpvxs.abicheck.json"),
    )
    assert result.to_dict() == {
        "outcome": ResolveOutcome.RESOLVED,
        "message": "resolved target 'libpvxs'.",
        "bootstrap": False,
        "manifest_path": str(tmp_path / "manifest.json"),
        "snapshot_path": str(tmp_path / "libpvxs.abicheck.json"),
        "binaries_dir": "",
        "binary_paths": {},
    }


def test_resolve_target_corrupt_manifest_is_stale_schema_not_a_traceback(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_text(
        "{not valid json", encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA
    assert not result.ok


def test_resolve_bundle_corrupt_manifest_is_stale_schema(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / BASELINE_MANIFEST_FILENAME).write_text("[]", encoding="utf-8")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.STALE_SCHEMA


# ── resolve_bundle ────────────────────────────────────────────────────────


def _write_binary(baseline_dir: Path, rel_path: str) -> None:
    full = baseline_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x7fELF-fake-binary-contents")


def test_resolve_bundle_not_found_required(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = resolve_bundle(
        missing,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is False


def test_resolve_bundle_not_found_bootstrap(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = resolve_bundle(
        missing,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=False,
    )
    assert result.outcome == ResolveOutcome.NOT_FOUND
    assert result.bootstrap is True


def test_resolve_bundle_existing_dir_without_manifest_is_ambiguous(
    tmp_path: Path,
) -> None:
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=False,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert result.bootstrap is False
    assert "does not contain a" in result.message


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
    assert "'libpvxsIoc':" in result.message
    assert "'libpvxs':" not in result.message


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


def test_resolve_bundle_rejects_escaping_binary_path(tmp_path: Path) -> None:
    # Same path-traversal guard as resolve_target, applied to a bundle
    # member's "binary" field (Codex review).
    outside = tmp_path.parent / "outside.so"
    outside.write_bytes(b"\x7fELF-fake")
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": f"../{outside.name}"}),
            _target_artifact(
                "libpvxsIoc", extra={"binary": "binaries/libpvxsIoc.so.1.5"}
            ),
        ],
    )
    _write_binary(tmp_path, "binaries/libpvxsIoc.so.1.5")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "'libpvxs':" in result.message
    assert "'libpvxsIoc':" not in result.message


def test_resolve_bundle_rejects_binary_outside_binaries_dir(tmp_path: Path) -> None:
    # A "binary" entry that is a valid, non-escaping relative path but sits
    # outside binaries/ must still be rejected -- the documented bundle
    # contract is that every member's binary lives under binaries/ (the
    # same directory the binaries-dir output advertises), not merely
    # somewhere in the baseline-set (Codex review).
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"binary": "libpvxs.so"})],
    )
    (tmp_path / "libpvxs.so").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS


def test_resolve_bundle_digest_match_resolves(tmp_path: Path) -> None:
    content = b"\x7fELF-fake-binary-contents"
    real_digest = hashlib.sha256(content).hexdigest()
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={
                    "binary": "binaries/libpvxs.so.1.5",
                    "binary_sha256": real_digest,
                },
            )
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so.1.5").write_bytes(content)
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_bundle_snapshot_sha256_is_not_used_as_binary_digest(
    tmp_path: Path,
) -> None:
    # A bundle-scoped manifest row can carry a "sha256" left over from the
    # snapshot it was originally written for (build_manifest.py always sets
    # it), plus a "binary" path added on top. If binary-digest verification
    # ever reused that same "sha256" field, it would compare the JSON
    # snapshot's content hash against the ELF binary's raw-byte hash --
    # these can never coincidentally match, so the bundle would report
    # ambiguous for every real baseline. binary_sha256 is a separate field
    # and, when absent (as here), the binary-digest check must no-op just
    # like an ordinary missing digest (Codex review).
    binary_content = b"\x7fELF-fake-binary-contents"
    snapshot_content = {"library": "libpvxs", "schema_version": 9}
    snapshot_digest = compute_snapshot_content_hash(snapshot_content)
    assert snapshot_digest != hashlib.sha256(binary_content).hexdigest()
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={
                    "binary": "binaries/libpvxs.so.1.5",
                    "sha256": snapshot_digest,  # leftover snapshot digest
                    # no binary_sha256 recorded
                },
            )
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so.1.5").write_bytes(binary_content)
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_bundle_digest_mismatch_is_ambiguous(tmp_path: Path) -> None:
    # A truncated/replaced staged binary (partial download, stale cache
    # restore) must never resolve just because a file with the right name
    # exists under binaries/ -- the manifest's recorded digest is what
    # actually vouches for its content (Codex review, mirroring the
    # target-snapshot digest-verification finding).
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={
                    "binary": "binaries/libpvxs.so.1.5",
                    "binary_sha256": "0" * 64,
                },
            )
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so.1.5").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS


def test_resolve_bundle_message_reports_distinct_per_member_reasons(
    tmp_path: Path,
) -> None:
    # Three different rejection causes (no manifest entry, digest mismatch,
    # missing/mis-located binary) must surface as three distinct per-member
    # reasons in one message, not one generic "have no staged binary"
    # sentence that points every operator at the same wrong fix
    # (CodeRabbit review).
    content = b"\x7fELF-real"
    real_digest = hashlib.sha256(content).hexdigest()
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={
                    "binary": "binaries/libpvxs.so.1.5",
                    "binary_sha256": real_digest,
                },
            ),
            _target_artifact(
                "libpvxsIoc",
                extra={
                    "binary": "binaries/libpvxsIoc.so.1.5",
                    "binary_sha256": "0" * 64,
                },
            ),
            # libpvxsExtra has no manifest entry at all.
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so.1.5").write_bytes(content)
    (tmp_path / "binaries" / "libpvxsIoc.so.1.5").write_bytes(b"\x7fELF-wrong")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs", "libpvxsIoc", "libpvxsExtra"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "digest does not match" in result.message  # libpvxsIoc
    assert "not in this baseline-set's manifest" in result.message  # libpvxsExtra
    assert "'libpvxs':" not in result.message  # libpvxs itself resolved fine


def test_resolve_bundle_ambiguous_duplicate_manifest_entries(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/a.so"}),
            _target_artifact("libpvxs", extra={"binary": "binaries/b.so"}),
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "a.so").write_bytes(b"\x7fELF-fake")
    (tmp_path / "binaries" / "b.so").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "multiple artifacts" in result.message


def test_resolve_bundle_duplicate_entries_reported_even_when_first_lacks_binary(
    tmp_path: Path,
) -> None:
    # artifact_for() returns only the first matching row -- if that row
    # happens to lack "binary" while a later duplicate has one, the
    # duplicate-entry check must still win over the generic "no staged
    # binary declared" message, not silently depend on manifest row order
    # (CodeRabbit review).
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": ""}),
            _target_artifact("libpvxs", extra={"binary": "binaries/b.so"}),
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "b.so").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "multiple artifacts" in result.message
    assert "no staged binary declared" not in result.message


def test_resolve_bundle_rejects_non_elf_binary_no_digest(tmp_path: Path) -> None:
    # build_bundle_snapshot() (abicheck/bundle.py) silently skips any staged
    # input that isn't a real ELF file -- a non-ELF file staged under
    # binaries/ must not resolve just because a file with the right name
    # exists and the manifest recorded no digest to catch it (Codex review).
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so"})
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so").write_bytes(b"not an elf file at all")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "not an ELF file" in result.message


def test_resolve_bundle_rejects_non_elf_binary_matching_digest(tmp_path: Path) -> None:
    # Even when the recorded digest matches the non-ELF bytes exactly (e.g.
    # a JSON snapshot was staged at the binary path and the manifest was
    # built from that same file), the ELF-magic check must still catch it --
    # a matching digest only proves the staged bytes are what the manifest
    # expects, not that they're a binary build_bundle_snapshot() can read.
    content = b"{}"
    digest = hashlib.sha256(content).hexdigest()
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={"binary": "binaries/libpvxs.so", "binary_sha256": digest},
            )
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so").write_bytes(content)
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "not an ELF file" in result.message


def test_resolve_bundle_rejects_truncated_elf_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A bare magic-byte sniff isn't enough: a truncated/corrupted staged
    # binary can still start with \x7fELF while being unparseable (or
    # parsing to essentially-empty metadata) -- build_bundle_snapshot()
    # would silently skip it just the same as a non-ELF file, so this must
    # be caught too, not just the missing-magic case (Codex review, second
    # round). Restores the real parser (undoing the autouse stub) since
    # this test is specifically about the deep-parse guard.
    monkeypatch.setattr(
        "abicheck.buildsource.baseline_set.parse_elf_metadata",
        _real_parse_elf_metadata,
    )
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so"})
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so").write_bytes(
        b"\x7fELF-truncated-not-a-real-elf-structure"
    )
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "essentially empty ELF file" in result.message


def test_resolve_bundle_binary_digest_os_error_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A permission error (or the file disappearing mid-read) while hashing a
    # staged binary must produce the same typed ambiguous outcome as any
    # other corrupt binary, not an unhandled OSError. Simulated via
    # monkeypatch since a real permission error isn't reliably reproducible
    # when tests run as root (same rationale as
    # test_load_baseline_manifest_os_error_raises_value_error).
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact(
                "libpvxs",
                extra={"binary": "binaries/libpvxs.so", "binary_sha256": "0" * 64},
            )
        ],
    )
    (tmp_path / "binaries").mkdir()
    binary_path = tmp_path / "binaries" / "libpvxs.so"
    binary_path.write_bytes(b"\x7fELF-fake")
    real_open = Path.open
    # _not_elf_issue opens+reads the same binary_path first (a 4-byte magic
    # sniff) before _binary_digest_issue's own whole-file read -- let that
    # first open succeed for real (so the ELF check passes normally, given
    # the autouse fixture's non-empty ElfMetadata stub) and only fail
    # starting from the second open of this path, which is the digest read
    # this test is actually about.
    open_count = 0

    def _boom(self: Path, *args: object, **kwargs: object) -> object:
        nonlocal open_count
        if self == binary_path:
            open_count += 1
            if open_count > 1:
                raise PermissionError("synthetic permission error")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _boom)
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "could not be read to verify its digest" in result.message


def test_resolve_bundle_not_elf_os_error_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A permission error while reading the ELF-magic header must be caught
    # too, not just the digest read (same rationale as the digest-OSError
    # test above). No recorded digest, so _not_elf_issue's own read is the
    # first one reached.
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so"})
        ],
    )
    (tmp_path / "binaries").mkdir()
    binary_path = tmp_path / "binaries" / "libpvxs.so"
    binary_path.write_bytes(b"\x7fELF-fake")
    real_open = Path.open

    def _boom(self: Path, *args: object, **kwargs: object) -> object:
        if self == binary_path:
            raise PermissionError("synthetic permission error")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _boom)
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "could not be read to verify it is an ELF file" in result.message


def test_resolve_bundle_not_elf_unexpected_parse_exception_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # parse_elf_metadata's own documented contract is "returns an empty
    # ElfMetadata on any parse error" (it catches ELFError/OSError/
    # ValueError internally) -- but a sufficiently pathological/adversarial
    # input could still trip an exception type outside that tuple deep in
    # pyelftools. _not_elf_issue's own except Exception clause is defensive
    # coverage for that case; simulated directly since crafting real bytes
    # that reproduce it is impractical.
    def _boom(path: Path) -> object:
        raise RuntimeError("synthetic unexpected parser failure")

    monkeypatch.setattr("abicheck.buildsource.baseline_set.parse_elf_metadata", _boom)
    _write_manifest(
        tmp_path,
        artifacts=[
            _target_artifact("libpvxs", extra={"binary": "binaries/libpvxs.so"})
        ],
    )
    (tmp_path / "binaries").mkdir()
    (tmp_path / "binaries" / "libpvxs.so").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "could not be parsed as a valid ELF file" in result.message


def test_resolve_bundle_rejects_binary_field_equal_to_binaries_dir_itself(
    tmp_path: Path,
) -> None:
    # Path.is_relative_to() is true for a path relative to *itself*, so a
    # manifest entry whose "binary" field is exactly "binaries" (equal to
    # BASELINE_BINARIES_DIRNAME) would otherwise satisfy the containment
    # check without actually being a child of it. In a corrupted
    # baseline-set where binaries/ was itself staged as a plain file (not
    # a directory), this member would then "resolve" to that file --
    # meaning the advertised binaries-dir output points at a file, not a
    # directory containing member binaries (Codex review, fourth round).
    _write_manifest(
        tmp_path,
        artifacts=[_target_artifact("libpvxs", extra={"binary": "binaries"})],
    )
    (tmp_path / "binaries").write_bytes(b"\x7fELF-fake")
    result = resolve_bundle(
        tmp_path,
        bundle="pvxs-release",
        members=["libpvxs"],
        profile=PROFILE,
        required=True,
    )
    assert result.outcome == ResolveOutcome.AMBIGUOUS
