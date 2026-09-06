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

"""Tests for abicheck.model.consumer_spec — Workstream D-S1's consumer
specification (vision-api-abi-evolution.md "D. Optional prebuilt-consumer
lifecycle")."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from abicheck.model.consumer_spec import (
    ConsumerDigestMismatchError,
    ConsumerImpactSummary,
    ConsumerRequirement,
    ConsumerSpec,
    ConsumerUnreadableError,
    as_consumer_spec,
    parse_consumer_manifest,
    verify_digest,
)


class TestConsumerSpecNormalization:
    def test_from_path(self, tmp_path):
        spec = ConsumerSpec.from_path(tmp_path / "app")
        assert spec.path == tmp_path / "app"
        assert spec.requirement is ConsumerRequirement.REQUIRED
        assert spec.is_advisory is False

    def test_as_consumer_spec_wraps_bare_path(self, tmp_path):
        path = tmp_path / "app"
        spec = as_consumer_spec(path)
        assert isinstance(spec, ConsumerSpec)
        assert spec.path == path

    def test_as_consumer_spec_passes_through_existing_spec(self, tmp_path):
        original = ConsumerSpec(path=tmp_path / "app", platform="linux-x86_64")
        assert as_consumer_spec(original) is original

    def test_is_advisory(self, tmp_path):
        spec = ConsumerSpec(path=tmp_path / "app", requirement=ConsumerRequirement.ADVISORY)
        assert spec.is_advisory is True

    def test_provenance_omits_unset_fields(self, tmp_path):
        spec = ConsumerSpec(path=tmp_path / "app")
        assert spec.provenance() == {"requirement": "required"}

    def test_provenance_includes_set_fields(self, tmp_path):
        spec = ConsumerSpec(
            path=tmp_path / "app",
            manifest=tmp_path / "manifest.json",
            digest="sha256:abcd",
            platform="linux-x86_64",
            profile="release",
            provider_baseline="nightly",
            requirement=ConsumerRequirement.ADVISORY,
        )
        prov = spec.provenance()
        assert prov["digest"] == "sha256:abcd"
        assert prov["platform"] == "linux-x86_64"
        assert prov["profile"] == "release"
        assert prov["provider_baseline"] == "nightly"
        assert prov["requirement"] == "advisory"
        assert prov["manifest"] == str(tmp_path / "manifest.json")


class TestVerifyDigest:
    def test_no_digest_is_a_no_op(self, tmp_path):
        spec = ConsumerSpec(path=tmp_path / "missing-file-never-read")
        verify_digest(spec)  # must not raise / must not touch the filesystem

    def test_matching_digest_passes(self, tmp_path):
        f = tmp_path / "app"
        f.write_bytes(b"hello world")
        digest = "sha256:" + hashlib.sha256(b"hello world").hexdigest()
        verify_digest(ConsumerSpec(path=f, digest=digest))

    def test_mismatched_digest_raises(self, tmp_path):
        f = tmp_path / "app"
        f.write_bytes(b"hello world")
        digest = "sha256:" + "0" * 64
        with pytest.raises(ConsumerDigestMismatchError, match="digest mismatch"):
            verify_digest(ConsumerSpec(path=f, digest=digest))

    def test_case_insensitive_hex_matches(self, tmp_path):
        f = tmp_path / "app"
        f.write_bytes(b"hello world")
        digest = "sha256:" + hashlib.sha256(b"hello world").hexdigest().upper()
        verify_digest(ConsumerSpec(path=f, digest=digest))

    def test_malformed_digest_raises(self, tmp_path):
        f = tmp_path / "app"
        f.write_bytes(b"hello world")
        with pytest.raises(ConsumerDigestMismatchError, match="Malformed"):
            verify_digest(ConsumerSpec(path=f, digest="not-a-digest"))

    def test_unsupported_algorithm_raises(self, tmp_path):
        f = tmp_path / "app"
        f.write_bytes(b"hello world")
        with pytest.raises(ConsumerDigestMismatchError, match="Unsupported digest"):
            verify_digest(ConsumerSpec(path=f, digest="not-an-algorithm:abcd"))

    def test_unreadable_file_raises_unreadable_not_mismatch(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(ConsumerUnreadableError):
            verify_digest(ConsumerSpec(path=missing, digest="sha256:" + "0" * 64))


class TestParseConsumerManifest:
    def _write(self, tmp_path: Path, document: dict) -> Path:
        manifest_path = tmp_path / "consumers.json"
        manifest_path.write_text(json.dumps(document), encoding="utf-8")
        return manifest_path

    def test_minimal_entry(self, tmp_path):
        manifest = self._write(tmp_path, {"consumers": [{"path": "bin/app"}]})
        specs = parse_consumer_manifest(manifest)
        assert len(specs) == 1
        assert specs[0].path == tmp_path / "bin" / "app"
        assert specs[0].requirement is ConsumerRequirement.REQUIRED
        assert specs[0].manifest == manifest

    def test_full_entry(self, tmp_path):
        manifest = self._write(
            tmp_path,
            {
                "consumers": [
                    {
                        "path": "bin/app",
                        "digest": "sha256:abcd",
                        "platform": "linux-x86_64",
                        "profile": "release",
                        "provider_baseline": "nightly-2026-09-05",
                        "requirement": "advisory",
                    }
                ]
            },
        )
        (spec,) = parse_consumer_manifest(manifest)
        assert spec.digest == "sha256:abcd"
        assert spec.platform == "linux-x86_64"
        assert spec.profile == "release"
        assert spec.provider_baseline == "nightly-2026-09-05"
        assert spec.requirement is ConsumerRequirement.ADVISORY

    def test_multiple_consumers(self, tmp_path):
        manifest = self._write(
            tmp_path,
            {"consumers": [{"path": "bin/a"}, {"path": "bin/b", "requirement": "advisory"}]},
        )
        specs = parse_consumer_manifest(manifest)
        assert [s.path.name for s in specs] == ["a", "b"]
        assert specs[0].requirement is ConsumerRequirement.REQUIRED
        assert specs[1].requirement is ConsumerRequirement.ADVISORY

    def test_absolute_path_is_not_rebased(self, tmp_path):
        absolute = tmp_path / "elsewhere" / "app"
        manifest = self._write(tmp_path, {"consumers": [{"path": str(absolute)}]})
        (spec,) = parse_consumer_manifest(manifest)
        assert spec.path == absolute

    def test_missing_path_field_raises(self, tmp_path):
        manifest = self._write(tmp_path, {"consumers": [{"digest": "sha256:abcd"}]})
        with pytest.raises(ValueError, match="missing required 'path'"):
            parse_consumer_manifest(manifest)

    def test_unknown_requirement_raises(self, tmp_path):
        manifest = self._write(
            tmp_path, {"consumers": [{"path": "bin/app", "requirement": "mandatory"}]}
        )
        with pytest.raises(ValueError, match="unknown requirement"):
            parse_consumer_manifest(manifest)

    def test_not_a_json_object_raises(self, tmp_path):
        manifest_path = tmp_path / "consumers.json"
        manifest_path.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="'consumers' array"):
            parse_consumer_manifest(manifest_path)

    def test_missing_consumers_key_raises(self, tmp_path):
        manifest = self._write(tmp_path, {"not_consumers": []})
        with pytest.raises(ValueError, match="'consumers' array"):
            parse_consumer_manifest(manifest)

    def test_malformed_json_raises(self, tmp_path):
        manifest_path = tmp_path / "consumers.json"
        manifest_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_consumer_manifest(manifest_path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Could not read consumer manifest"):
            parse_consumer_manifest(tmp_path / "does-not-exist.json")

    def test_empty_consumers_list_is_valid(self, tmp_path):
        manifest = self._write(tmp_path, {"consumers": []})
        assert parse_consumer_manifest(manifest) == []


class TestConsumerImpactSummary:
    def test_defaults(self):
        summary = ConsumerImpactSummary()
        assert summary.to_json() == {
            "total": 0,
            "evaluated": 0,
            "affected": 0,
            "unreadable_advisory": 0,
            "unreadable_paths": [],
        }

    def test_to_json_reflects_fields(self):
        summary = ConsumerImpactSummary(
            total=3, evaluated=2, affected=1, unreadable_advisory=1,
            unreadable_paths=("/tmp/app",),
        )
        assert summary.to_json() == {
            "total": 3,
            "evaluated": 2,
            "affected": 1,
            "unreadable_advisory": 1,
            "unreadable_paths": ["/tmp/app"],
        }
