# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-063 Phase 8's opt-in `dump --project-snapshot-dir`/`compare`/
`scan --against` storage-v2 wiring: the directory round trip end to end,
and the CLI-side disambiguation between a real `ProjectSnapshot` package
directory, a plain directory, and a `BuildSourcePack`-shaped one.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from abicheck.errors import SnapshotError
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.canonical import canonical_form


def _write_package(tmp_path: Path, snap: AbiSnapshot, name: str = "pkg") -> Path:
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package

    doc = snapshot_to_dict(snap)
    root = tmp_path / name
    write_legacy_snapshot_package(
        doc, root, artifact_id=snap.library, max_known_schema_version=SCHEMA_VERSION
    )
    return root


class TestProjectSnapshotLegacyRoundTrip:
    def test_write_then_resolve_input_round_trips(self, tmp_path: Path) -> None:
        from abicheck.workflows.input_resolution import resolve_input

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        resolved = resolve_input(root)
        assert resolved.library == "libfoo.so.1"
        assert resolved.version == "1.0.0"

    def test_write_then_read_legacy_snapshot_document_round_trips(
        self, tmp_path: Path
    ) -> None:
        from abicheck.project_snapshot_legacy import read_legacy_snapshot_document

        snap = AbiSnapshot(library="libbar.so.2", version="2.0.0")
        doc = snapshot_to_dict(snap)
        root = _write_package(tmp_path, snap, name="bar")
        rebuilt = read_legacy_snapshot_document(root)
        assert canonical_form(rebuilt) == canonical_form(doc)

    def test_resolve_input_rejects_a_directory_with_no_manifest(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.input_resolution import resolve_input

        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SnapshotError):
            resolve_input(empty)

    def test_resolve_input_rejects_a_corrupted_manifest(self, tmp_path: Path) -> None:
        from abicheck.workflows.input_resolution import resolve_input

        root = tmp_path / "corrupt"
        root.mkdir()
        (root / "manifest.json").write_text("not json at all", encoding="utf-8")
        with pytest.raises(SnapshotError):
            resolve_input(root)

    def test_resolve_input_translates_a_malformed_artifact_ref_type_error(
        self, tmp_path: Path
    ) -> None:
        """A wrong-container-type field (`"sections": []` instead of a
        mapping) makes `ArtifactRef.from_dict` raise `TypeError` --
        `resolve_input`'s directory branch must translate that into its
        documented `SnapshotError`, the same way every other malformed-
        package case already is, rather than leaking a raw `TypeError` to a
        typed-API caller (Codex review)."""
        import json

        from abicheck.storage.package import artifact_ref_relpath
        from abicheck.workflows.input_resolution import resolve_input

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        art_path = root / artifact_ref_relpath("libfoo.so.1")
        art = json.loads(art_path.read_text(encoding="utf-8"))
        art["sections"] = []
        art_path.write_text(json.dumps(art), encoding="utf-8")
        with pytest.raises(SnapshotError):
            resolve_input(root)

    def test_read_legacy_snapshot_document_rejects_a_stale_variant_ref(
        self, tmp_path: Path
    ) -> None:
        """A package whose `refs/variants/*.json` omits the artifact it
        itself declares `variant_id` for -- a stale/corrupted membership
        graph -- must be refused, the same way `read_project_manifest`/
        `read_variant_artifact_pair` already refuse it, rather than
        `read_legacy_snapshot_document` silently exporting the artifact
        anyway (Codex review)."""
        from abicheck.project_snapshot_legacy import read_legacy_snapshot_document
        from abicheck.storage.package import variant_ref_relpath

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        variant_path = root / variant_ref_relpath("default")
        corrupted = json.loads(variant_path.read_text(encoding="utf-8"))
        assert corrupted["artifact_ids"] == ["libfoo.so.1"]
        corrupted["artifact_ids"] = []
        variant_path.write_text(json.dumps(corrupted), encoding="utf-8")
        with pytest.raises(ValueError):
            read_legacy_snapshot_document(root)

    def test_read_legacy_snapshot_document_rejects_a_missing_section(
        self, tmp_path: Path
    ) -> None:
        """A stale/corrupted artifact ref that has dropped a section
        `manifest.json`'s own `section_schema_versions` still advertises
        must be refused -- `export_legacy_snapshot` only iterates the
        sections actually present in `artifact.sections`, so silently
        proceeding would let `join_legacy_document` backfill the missing
        section's declarations/types with empty defaults, turning lost
        evidence into confirmed absence (Codex review)."""
        from abicheck.project_snapshot_legacy import read_legacy_snapshot_document
        from abicheck.storage.package import artifact_ref_relpath

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        art_path = root / artifact_ref_relpath("libfoo.so.1")
        art = json.loads(art_path.read_text(encoding="utf-8"))
        assert art["sections"], "fixture snapshot must publish at least one section"
        dropped_kind = next(iter(art["sections"]))
        del art["sections"][dropped_kind]
        art_path.write_text(json.dumps(art), encoding="utf-8")
        with pytest.raises(SnapshotError, match=dropped_kind):
            read_legacy_snapshot_document(root)


class TestIsProjectSnapshotPackageDir:
    def test_true_for_a_real_package(self, tmp_path: Path) -> None:
        from abicheck.project_snapshot_legacy import is_project_snapshot_package_dir

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        assert is_project_snapshot_package_dir(root) is True

    def test_false_for_a_missing_manifest(self, tmp_path: Path) -> None:
        from abicheck.project_snapshot_legacy import is_project_snapshot_package_dir

        empty = tmp_path / "empty"
        empty.mkdir()
        assert is_project_snapshot_package_dir(empty) is False

    def test_false_for_a_build_source_pack_shaped_manifest(
        self, tmp_path: Path
    ) -> None:
        """A `BuildSourcePack`'s own `manifest.json` uses the identical
        filename at its own directory root -- this must not be
        misidentified as a `ProjectSnapshot` package (it lacks the
        `versions`/`variant_ids`/`artifact_ids` shape `read_manifest_summary`
        requires, and carries `build_source_pack_version` instead)."""
        from abicheck.project_snapshot_legacy import is_project_snapshot_package_dir

        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text(
            json.dumps({"build_source_pack_version": 1, "created_at": "now"}),
            encoding="utf-8",
        )
        assert is_project_snapshot_package_dir(pack_dir) is False

    def test_false_for_a_plain_directory_of_libraries(self, tmp_path: Path) -> None:
        from abicheck.project_snapshot_legacy import is_project_snapshot_package_dir

        libdir = tmp_path / "libs"
        libdir.mkdir()
        (libdir / "libfoo.so").write_bytes(b"not a real binary")
        assert is_project_snapshot_package_dir(libdir) is False


class TestClassifyCompareOperand:
    def test_a_project_snapshot_package_classifies_as_file(
        self, tmp_path: Path
    ) -> None:
        from abicheck.cli_resolve import classify_compare_operand

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        assert classify_compare_operand(root) == "file"

    def test_a_plain_directory_still_classifies_as_directory(
        self, tmp_path: Path
    ) -> None:
        from abicheck.cli_resolve import classify_compare_operand

        libdir = tmp_path / "libs"
        libdir.mkdir()
        assert classify_compare_operand(libdir) == "directory"


class TestRejectUnsupportedAgainstOperand:
    def test_none_is_a_no_op(self) -> None:
        from abicheck.frontends.cli.scan_against import (
            reject_unsupported_against_operand,
        )

        reject_unsupported_against_operand(None)  # must not raise

    def test_a_project_snapshot_package_is_accepted(self, tmp_path: Path) -> None:
        from abicheck.frontends.cli.scan_against import (
            reject_unsupported_against_operand,
        )

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        reject_unsupported_against_operand(root)  # must not raise

    def test_a_plain_directory_is_rejected(self, tmp_path: Path) -> None:
        from abicheck.frontends.cli.scan_against import (
            reject_unsupported_against_operand,
        )

        libdir = tmp_path / "libs"
        libdir.mkdir()
        with pytest.raises(click.UsageError, match="plain directory"):
            reject_unsupported_against_operand(libdir)

    def test_a_single_file_is_accepted(self, tmp_path: Path) -> None:
        from abicheck.frontends.cli.scan_against import (
            reject_unsupported_against_operand,
        )

        f = tmp_path / "snap.json"
        f.write_text("{}", encoding="utf-8")
        reject_unsupported_against_operand(f)  # must not raise


class TestWriteLegacySnapshotPackageRefusesNonemptyRoot:
    """`write_project_manifest`'s own docstring names its ref-then-manifest
    publish order as safe for a *first* publication only, not for
    republishing changed content under an already-live path --
    `write_legacy_snapshot_package` must refuse that unsafe case outright
    now that `dump --project-snapshot-dir` is a real caller that could
    otherwise be run twice against the same directory (Codex review)."""

    def test_a_fresh_nonexistent_root_is_accepted(self, tmp_path: Path) -> None:
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        _write_package(tmp_path, snap)  # must not raise

    def test_an_existing_empty_root_is_accepted(self, tmp_path: Path) -> None:
        from abicheck.project_snapshot_legacy import write_legacy_snapshot_package

        root = tmp_path / "pkg"
        root.mkdir()
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        write_legacy_snapshot_package(
            snapshot_to_dict(snap),
            root,
            artifact_id=snap.library,
            max_known_schema_version=SCHEMA_VERSION,
        )  # must not raise

    def test_republishing_into_an_existing_package_is_refused(
        self, tmp_path: Path
    ) -> None:
        from abicheck.project_snapshot_legacy import write_legacy_snapshot_package

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        other = AbiSnapshot(library="libbar.so.1", version="2.0.0")
        with pytest.raises(ValueError, match="already exists and is not empty"):
            write_legacy_snapshot_package(
                snapshot_to_dict(other),
                root,
                artifact_id=other.library,
                max_known_schema_version=SCHEMA_VERSION,
            )

    def test_a_nonempty_non_package_directory_is_also_refused(
        self, tmp_path: Path
    ) -> None:
        from abicheck.project_snapshot_legacy import write_legacy_snapshot_package

        root = tmp_path / "pkg"
        root.mkdir()
        (root / "unrelated.txt").write_text("x", encoding="utf-8")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        with pytest.raises(ValueError, match="already exists and is not empty"):
            write_legacy_snapshot_package(
                snapshot_to_dict(snap),
                root,
                artifact_id=snap.library,
                max_known_schema_version=SCHEMA_VERSION,
            )


class TestCollectMetadataSkipsDirectories:
    """`workflows.input_resolution.collect_metadata` is the typed-API
    counterpart of `frontends/cli/runtime._collect_metadata` -- both must
    treat a `ProjectSnapshot` package directory as non-hashable rather than
    calling `Path.read_bytes()` on it (Codex review: the CLI path already
    had this guard, `service.run_compare_request`'s typed-API path did
    not)."""

    def test_returns_none_for_a_project_snapshot_directory(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.input_resolution import collect_metadata

        snap = AbiSnapshot(library="libfoo.so.1", version="1.0.0")
        root = _write_package(tmp_path, snap)
        assert collect_metadata(root) is None

    def test_returns_none_for_any_directory(self, tmp_path: Path) -> None:
        from abicheck.workflows.input_resolution import collect_metadata

        empty = tmp_path / "empty"
        empty.mkdir()
        assert collect_metadata(empty) is None
