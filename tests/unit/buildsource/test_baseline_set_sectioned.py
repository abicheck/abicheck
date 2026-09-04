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

"""ADR-062/063 Phase 8 (redesign): ``abicheck/buildsource/baseline_set.py``'s
handling of the single-file sectioned envelope specifically -- split out of
``tests/test_baseline_set.py`` (that file's own `no_growth` architecture-debt
baseline) rather than growing it further. See that file for the general
``resolve_target``/``resolve_bundle`` test suite these tests share fixtures
with."""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.buildsource.baseline_set import (
    BASELINE_MANIFEST_FILENAME,
    ResolveOutcome,
    compute_snapshot_content_hash,
    resolve_target,
)

PROFILE = "linux-x86_64-gcc13-release"


def _write_manifest(baseline_dir: Path, *, artifacts: list[dict]) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 1,
        "project_ref": "v1.0.0",
        "profile": PROFILE,
        "snapshot_schema": 9,
        "fact_set": None,
        "baseline_generation": None,
        "artifacts": artifacts,
    }
    (baseline_dir / BASELINE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _target_artifact(name: str, *, sha256: str) -> dict:
    return {
        "library": name,
        "artifact": f"build/{name}.so",
        "snapshot": f"{name}.abicheck.json",
        "sha256": sha256,
    }


def test_resolve_target_digest_match_resolves_sectioned_snapshot(
    tmp_path: Path,
) -> None:
    """ADR-062/063 Phase 8 (Codex review): the manifest records a digest of
    the *unwrapped* content (matching build_manifest.py's own hash), so the
    resolver must unwrap the sectioned envelope the same way before
    hashing, or every new baseline would permanently mismatch."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.storage.sectioned_document import to_sectioned_document

    flat = snapshot_to_dict(AbiSnapshot(library="libpvxs", version="1.0.0"))
    real_digest = compute_snapshot_content_hash(flat)
    sectioned = to_sectioned_document(flat, max_known_schema_version=SCHEMA_VERSION)
    _write_manifest(
        tmp_path, artifacts=[_target_artifact("libpvxs", sha256=real_digest)]
    )
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps(sectioned), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.RESOLVED


def test_resolve_target_malformed_sectioned_snapshot_is_ambiguous(
    tmp_path: Path,
) -> None:
    """Codex review: `from_sectioned_document()` can itself raise -- e.g. a
    raw `TypeError` for a section whose value is `[]` where a dict is
    expected. Left uncaught, this previously aborted `resolve_target()`
    instead of returning the documented `AMBIGUOUS` outcome."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.storage.sectioned_document import to_sectioned_document

    flat = snapshot_to_dict(AbiSnapshot(library="libpvxs", version="1.0.0"))
    sectioned = to_sectioned_document(flat, max_known_schema_version=SCHEMA_VERSION)
    some_section = next(iter(sectioned["sections"]))
    sectioned["sections"][some_section] = []  # payload must be a mapping, not a list
    _write_manifest(tmp_path, artifacts=[_target_artifact("libpvxs", sha256="0" * 64)])
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps(sectioned), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.AMBIGUOUS
    assert "could not be unwrapped" in result.message


def test_resolve_target_future_schema_sectioned_snapshot_is_stale_not_ambiguous(
    tmp_path: Path,
) -> None:
    """Codex review: a sectioned envelope from a genuinely newer abicheck
    build must resolve as `STALE_SCHEMA` ("upgrade"), never `AMBIGUOUS`
    ("corrupt"), even when its unrecognized sections would also fail to
    unwrap -- the schema check runs BEFORE the unwrap is attempted."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.storage.sectioned_document import to_sectioned_document

    flat = snapshot_to_dict(AbiSnapshot(library="libpvxs", version="1.0.0"))
    sectioned = to_sectioned_document(flat, max_known_schema_version=SCHEMA_VERSION)
    sectioned["schema_version"] = SCHEMA_VERSION + 1
    # A section this build has never heard of -- what a newer envelope
    # would carry, and what would otherwise raise before the schema check.
    sectioned["sections"]["a_future_section_kind"] = {
        "section_kind": "a_future_section_kind",
        "section_schema_version": 1,
        "payload": {},
    }
    sectioned["section_schema_versions"]["a_future_section_kind"] = 1
    _write_manifest(tmp_path, artifacts=[_target_artifact("libpvxs", sha256="0" * 64)])
    (tmp_path / "libpvxs.abicheck.json").write_text(
        json.dumps(sectioned), encoding="utf-8"
    )
    result = resolve_target(tmp_path, target="libpvxs", profile=PROFILE, required=True)
    assert result.outcome == ResolveOutcome.STALE_SCHEMA
    assert "newer than this resolver understands" in result.message
