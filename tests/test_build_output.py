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

"""Tests for ``buildsource.build_output`` (ADR-047 §2/§11.1, G30 P1.1).

``build-output.json`` is a standardized, producer-agnostic artifact directory
a project's existing build populates once. These tests cover the schema
round-trip and the validator's full ADR-047 §11.1 failure taxonomy: empty
declared header roots, binary digest mismatches, ``evidence.projection``
other than ``"declared"``, and the corrected shared-pack /
manifest-mismatch scope for per-target evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_output import (
    BUILD_OUTPUT_SCHEMA,
    BuildOutput,
    BuildOutputBundle,
    BuildOutputEvidence,
    BuildOutputEvidenceProducer,
    BuildOutputProfile,
    BuildOutputTarget,
    is_build_output_dir,
    load_build_output,
    validate_build_output,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pack(
    root: Path,
    rel: str,
    *,
    library: str = "",
    tu_target_ids: list[str | None] | None = None,
) -> None:
    """Write a minimal Flow-2 ``abicheck_inputs/`` pack at *root/rel*."""
    pack_dir = root / rel
    (pack_dir / "source_facts").mkdir(parents=True)
    manifest = {"kind": "abicheck_inputs", "abicheck_inputs_version": 1}
    if library:
        manifest["library"] = library
    (pack_dir / "manifest.json").write_text(json.dumps(manifest))
    for i, target_id in enumerate(tu_target_ids or []):
        record: dict[str, object] = {"tu_id": f"tu{i}", "declarations": []}
        if target_id is not None:
            record["target_id"] = target_id
        (pack_dir / "source_facts" / f"tu{i}.jsonl").write_text(
            json.dumps(record) + "\n"
        )


def _build_output_dir(
    tmp_path: Path,
    *,
    targets: list[dict],
    digests: dict[str, str] | None = None,
) -> Path:
    root = tmp_path / "abicheck-build"
    root.mkdir()
    manifest = {
        "schema": BUILD_OUTPUT_SCHEMA,
        "project": "example/project",
        "targets": targets,
        "digests": digests or {},
    }
    (root / "build-output.json").write_text(json.dumps(manifest))
    return root


def _binary(root: Path, rel: str, content: bytes = b"binary-content") -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256(path)


def _header_root(root: Path, rel: str, *, populated: bool = True) -> None:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if populated:
        (d / "header.h").write_text("#pragma once\n")


class TestSchemaRoundTrip:
    def test_full_round_trip(self) -> None:
        bo = BuildOutput(
            schema=BUILD_OUTPUT_SCHEMA,
            project="epics-base/pvxs",
            head_sha="b7e2c1a",
            source_tree_digest="sha256:abc",
            profile=BuildOutputProfile(
                id="linux-x86_64-gcc13-release",
                os="linux",
                arch="x86_64",
                compiler={"family": "gcc", "version": "13.2.0"},
                cxx_abi="itanium",
                stdlib="libstdc++",
                config="release",
            ),
            targets=[
                BuildOutputTarget(
                    id="libpvxs",
                    binary="artifacts/lib/libpvxs.so.1.5",
                    public_header_roots=["headers/pvxs"],
                    generated_header_roots=["generated-headers/pvxs"],
                    compile_context={"include_dirs": ["headers"]},
                    bundle="pvxs-release",
                    evidence=BuildOutputEvidence(
                        kind="source-facts",
                        path="evidence/abicheck_inputs",
                        projection="declared",
                    ),
                )
            ],
            bundles=[BuildOutputBundle(id="pvxs-release", targets=["libpvxs"])],
            evidence_producer=BuildOutputEvidenceProducer(
                kind="wrapper", tool="abicheck-cc", version="0.x.y"
            ),
            digests={"artifacts/lib/libpvxs.so.1.5": "sha256:deadbeef"},
        )
        round_tripped = BuildOutput.from_dict(json.loads(json.dumps(bo.to_dict())))
        assert round_tripped == bo

    def test_from_dict_is_forward_compatible(self) -> None:
        # A hand-written/future manifest missing every optional key must not
        # raise -- matches the buildsource-wide "every field optional" rule.
        bo = BuildOutput.from_dict({})
        assert bo.schema == BUILD_OUTPUT_SCHEMA
        assert bo.targets == []
        assert bo.profile == BuildOutputProfile()

    def test_from_dict_ignores_malformed_nested_shapes(self) -> None:
        bo = BuildOutput.from_dict(
            {
                "schema": BUILD_OUTPUT_SCHEMA,
                "targets": "not-a-list",
                "bundles": [{"id": "x"}, "not-a-dict"],
                "digests": "not-a-dict",
                "profile": "not-a-dict",
            }
        )
        assert bo.targets == []
        assert len(bo.bundles) == 1
        assert bo.digests == {}
        assert bo.profile == BuildOutputProfile()

    def test_target_to_dict_omits_evidence_key_when_unset(self) -> None:
        target = BuildOutputTarget(id="libfoo", binary="artifacts/libfoo.so")
        assert "evidence" not in target.to_dict()

    def test_diagnostics_round_trip(self) -> None:
        bo = BuildOutput.from_dict(
            {
                "schema": BUILD_OUTPUT_SCHEMA,
                "diagnostics": {
                    "warnings": ["w1"],
                    "skipped_targets": ["libbar"],
                    "not_a_list": "ignored",
                },
            }
        )
        assert bo.diagnostics == {
            "warnings": ["w1"],
            "skipped_targets": ["libbar"],
        }
        assert bo.to_dict()["diagnostics"] == bo.diagnostics


class TestIsBuildOutputDir:
    def test_true_for_real_manifest(self, tmp_path: Path) -> None:
        root = _build_output_dir(tmp_path, targets=[])
        assert is_build_output_dir(root) is True

    def test_false_for_plain_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "plain"
        d.mkdir()
        assert is_build_output_dir(d) is False

    def test_false_for_malformed_json(self, tmp_path: Path) -> None:
        d = tmp_path / "abicheck-build"
        d.mkdir()
        (d / "build-output.json").write_text("{not valid json")
        assert is_build_output_dir(d) is False

    def test_false_for_inputs_pack_dir(self, tmp_path: Path) -> None:
        # A Flow-2 abicheck_inputs/ pack must not be mistaken for a
        # build-output dir -- different schema discriminator.
        d = tmp_path / "abicheck_inputs"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"kind": "abicheck_inputs"}))
        # is_build_output_dir looks for build-output.json specifically, so a
        # differently-named manifest already returns False; this pins that.
        assert is_build_output_dir(d) is False


class TestLoadBuildOutput:
    def test_missing_manifest_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_build_output(tmp_path)

    def test_wrong_schema_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "build-output.json").write_text(
            json.dumps({"schema": "something-else/v1"})
        )
        with pytest.raises(ValueError, match="schema"):
            load_build_output(tmp_path)

    def test_non_object_manifest_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "build-output.json").write_text(json.dumps(["not", "an", "object"]))
        with pytest.raises(ValueError):
            load_build_output(tmp_path)


class TestValidateBuildOutputHappyPath:
    def test_single_target_valid(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        _header_root(root, "headers/foo")
        _write_pack(root, "evidence/abicheck_inputs", library="libfoo")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "public_header_roots": ["headers/foo"],
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/abicheck_inputs",
                                "projection": "declared",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert report.ok, report.errors

    def test_no_targets_is_a_warning_not_an_error(self, tmp_path: Path) -> None:
        root = _build_output_dir(tmp_path, targets=[])
        report = validate_build_output(root)
        assert report.ok
        assert report.warnings

    def test_target_with_no_evidence_never_triggers_projection_checks(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [{"id": "libfoo", "binary": "artifacts/lib/libfoo.so"}],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert report.ok, report.errors


class TestHeaderRootValidation:
    def test_missing_public_header_root_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "public_header_roots": ["headers/does-not-exist"],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("does not exist" in e for e in report.errors)

    def test_empty_public_header_root_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        _header_root(root, "headers/foo", populated=False)
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "public_header_roots": ["headers/foo"],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("empty" in e for e in report.errors)

    def test_empty_generated_header_root_is_hard_failure_not_warning(
        self, tmp_path: Path
    ) -> None:
        # ADR-047 §2's S10 guard: an empty generated-headers/ root declared
        # non-empty is an error, never a warning.
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        _header_root(root, "generated-headers/foo", populated=False)
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "generated_header_roots": ["generated-headers/foo"],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert not report.warnings
        assert any("generated header" in e for e in report.errors)

    def test_target_with_empty_generated_header_roots_makes_no_claim(
        self, tmp_path: Path
    ) -> None:
        # An empty generated_header_roots: [] list is simply not a claim --
        # nothing to check, no failure.
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "generated_header_roots": [],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert report.ok, report.errors

    def test_absolute_header_root_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "public_header_roots": ["/etc"],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("absolute or escapes" in e for e in report.errors)

    def test_header_root_escaping_directory_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "public_header_roots": ["../escape"],
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("absolute or escapes" in e for e in report.errors)


class TestBinaryValidation:
    def test_missing_binary_fails(self, tmp_path: Path) -> None:
        root = _build_output_dir(
            tmp_path,
            targets=[{"id": "libfoo", "binary": "artifacts/lib/libfoo.so"}],
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("does not exist" in e for e in report.errors)

    def test_absolute_binary_path_is_rejected(self, tmp_path: Path) -> None:
        root = _build_output_dir(
            tmp_path, targets=[{"id": "libfoo", "binary": "/etc/passwd"}]
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("absolute or escapes" in e for e in report.errors)

    def test_digest_mismatch_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [{"id": "libfoo", "binary": "artifacts/lib/libfoo.so"}],
                    "digests": {"artifacts/lib/libfoo.so": "sha256:0000"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("digest mismatch" in e for e in report.errors)

    def test_missing_digest_entry_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [{"id": "libfoo", "binary": "artifacts/lib/libfoo.so"}],
                    "digests": {},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("no digests" in e for e in report.errors)

    def test_no_binary_declared_fails(self, tmp_path: Path) -> None:
        root = _build_output_dir(tmp_path, targets=[{"id": "libfoo"}])
        report = validate_build_output(root)
        assert not report.ok
        assert any("no binary declared" in e for e in report.errors)

    def test_target_with_no_id_is_an_error(self, tmp_path: Path) -> None:
        root = _build_output_dir(
            tmp_path, targets=[{"binary": "artifacts/lib/libfoo.so"}]
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("no id" in e for e in report.errors)


class TestEvidenceProjection:
    def test_inferred_projection_is_hard_failure(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        _write_pack(root, "evidence/abicheck_inputs", library="libfoo")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/abicheck_inputs",
                                "projection": "inferred",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("P2" in e for e in report.errors)

    def test_unknown_projection_value_is_hard_failure(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        _write_pack(root, "evidence/abicheck_inputs", library="libfoo")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "libfoo",
                            "binary": "artifacts/lib/libfoo.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/abicheck_inputs",
                                "projection": "typo",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("must be 'declared'" in e for e in report.errors)


class TestDeclaredEvidenceSharingScope:
    """ADR-047 §11.1's corrected scope, mirrored from the plan's required
    test matrix: (1) two targets sharing one pack must fail regardless of
    per-TU tags, (2) a manifest/target mismatch must fail, (3) a
    single-target, matched, untagged-TU pack must still pass (regression
    guard against over-rejecting the legitimate legacy case)."""

    def test_absolute_evidence_path_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/liba.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "liba",
                            "binary": "artifacts/lib/liba.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "/etc/passwd",
                                "projection": "declared",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/liba.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any(
            "evidence.path" in e and "absolute or escapes" in e for e in report.errors
        )

    def _two_target_setup(
        self,
        tmp_path: Path,
        *,
        shared: bool,
        tu_target_ids: list[str | None] | None = None,
    ) -> Path:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest_a = _binary(root, "artifacts/lib/liba.so", b"a")
        digest_b = _binary(root, "artifacts/lib/libb.so", b"b")
        _write_pack(
            root, "evidence/pack_a", library="liba", tu_target_ids=tu_target_ids
        )
        pack_b_rel = "evidence/pack_a" if shared else "evidence/pack_b"
        if not shared:
            _write_pack(root, "evidence/pack_b", library="libb")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "liba",
                            "binary": "artifacts/lib/liba.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/pack_a",
                                "projection": "declared",
                            },
                        },
                        {
                            "id": "libb",
                            "binary": "artifacts/lib/libb.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": pack_b_rel,
                                "projection": "declared",
                            },
                        },
                    ],
                    "digests": {
                        "artifacts/lib/liba.so": f"sha256:{digest_a}",
                        "artifacts/lib/libb.so": f"sha256:{digest_b}",
                    },
                }
            )
        )
        return root

    def test_case1_shared_pack_untagged_tus_fails(self, tmp_path: Path) -> None:
        root = self._two_target_setup(tmp_path, shared=True)
        report = validate_build_output(root)
        assert not report.ok
        assert any("referenced by more than one target" in e for e in report.errors)

    def test_case1_shared_pack_tagged_tus_still_fails(self, tmp_path: Path) -> None:
        # Even if the shared pack's TUs carry target_id tags, sharing itself
        # is the failure -- tags don't rescue it.
        root = self._two_target_setup(
            tmp_path, shared=True, tu_target_ids=["target://liba"]
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("referenced by more than one target" in e for e in report.errors)

    def test_case2_manifest_library_target_mismatch_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/liba.so")
        # manifest names a DIFFERENT library than the target referencing it.
        _write_pack(root, "evidence/pack_a", library="some-other-lib")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "liba",
                            "binary": "artifacts/lib/liba.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/pack_a",
                                "projection": "declared",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/liba.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("does not match the target" in e for e in report.errors)

    def test_case2_tu_target_id_mismatch_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/liba.so")
        # No manifest.library set, but a TU tags a DIFFERENT target.
        _write_pack(
            root,
            "evidence/pack_a",
            tu_target_ids=["target://some-other-lib"],
        )
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "liba",
                            "binary": "artifacts/lib/liba.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/pack_a",
                                "projection": "declared",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/liba.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("different target_id" in e for e in report.errors)

    def test_case3_single_target_untagged_matched_pack_passes(
        self, tmp_path: Path
    ) -> None:
        # Regression guard: must NOT reject the legitimate legacy-producer
        # case (untagged TUs, manifest.library correctly names the one
        # target referencing it, and no other target shares the pack).
        root = self._two_target_setup(
            tmp_path, shared=False, tu_target_ids=[None, None]
        )
        report = validate_build_output(root)
        assert report.ok, report.errors

    def test_pack_referenced_but_not_a_readable_inputs_pack_fails(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/liba.so")
        (root / "evidence" / "not_a_pack").mkdir(parents=True)
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [
                        {
                            "id": "liba",
                            "binary": "artifacts/lib/liba.so",
                            "evidence": {
                                "kind": "source-facts",
                                "path": "evidence/not_a_pack",
                                "projection": "declared",
                            },
                        }
                    ],
                    "digests": {"artifacts/lib/liba.so": f"sha256:{digest}"},
                }
            )
        )
        report = validate_build_output(root)
        assert not report.ok
        assert any("not a readable abicheck_inputs pack" in e for e in report.errors)


class TestBuildOutputCLI:
    """``abicheck build-output validate DIRECTORY`` (G30 P1.1)."""

    def _run(self, args):
        from abicheck.cli import main

        return CliRunner().invoke(main, ["build-output", "validate", *args])

    def _valid_dir(self, tmp_path: Path) -> Path:
        root = tmp_path / "abicheck-build"
        root.mkdir()
        digest = _binary(root, "artifacts/lib/libfoo.so")
        (root / "build-output.json").write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [{"id": "libfoo", "binary": "artifacts/lib/libfoo.so"}],
                    "digests": {"artifacts/lib/libfoo.so": f"sha256:{digest}"},
                }
            )
        )
        return root

    def test_valid_dir_exits_0(self, tmp_path: Path) -> None:
        root = self._valid_dir(tmp_path)
        res = self._run([str(root)])
        assert res.exit_code == 0, res.output
        assert "OK" in res.output

    def test_text_format_shows_warnings(self, tmp_path: Path) -> None:
        # No targets[] is valid (ok) but produces a warning -- exercises the
        # text-format warnings branch, distinct from the errors branch.
        root = _build_output_dir(tmp_path, targets=[])
        res = self._run([str(root)])
        assert res.exit_code == 0, res.output
        assert "warning(s)" in res.output
        assert "no targets" in res.output

    def test_invalid_dir_exits_1(self, tmp_path: Path) -> None:
        root = _build_output_dir(
            tmp_path, targets=[{"id": "libfoo", "binary": "does-not-exist"}]
        )
        res = self._run([str(root)])
        assert res.exit_code == 1
        assert "FAILED" in res.output

    def test_json_format(self, tmp_path: Path) -> None:
        root = self._valid_dir(tmp_path)
        res = self._run([str(root), "--format", "json"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["ok"] is True
        assert payload["errors"] == []

    def test_not_a_build_output_dir_is_usage_error(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        res = self._run([str(plain)])
        assert res.exit_code == 64

    def test_nonexistent_dir_is_usage_error(self, tmp_path: Path) -> None:
        res = self._run([str(tmp_path / "does-not-exist")])
        assert res.exit_code != 0

    def test_output_flag_writes_file(self, tmp_path: Path) -> None:
        root = self._valid_dir(tmp_path)
        out_file = tmp_path / "report.json"
        res = self._run([str(root), "--format", "json", "-o", str(out_file)])
        assert res.exit_code == 0, res.output
        payload = json.loads(out_file.read_text())
        assert payload["ok"] is True
