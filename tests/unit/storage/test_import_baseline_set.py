# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.import_baseline_set` — the `actions/baseline`-produced
baseline-set import/export adapter (ADR-063 Track C 8B, A1.4).
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.dto import BASELINE_SET_SECTION_KIND
from abicheck.storage.import_baseline_set import (
    export_baseline_set,
    import_baseline_set as _import_baseline_set,
)
from abicheck.storage.package import InMemoryObjectStore


def import_baseline_set(*args: Any, **kwargs: Any) -> Any:
    """`import_baseline_set`, defaulting `max_known_schema_version` to this
    build's real `serialization.SCHEMA_VERSION`."""
    kwargs.setdefault("max_known_schema_version", SCHEMA_VERSION)
    return _import_baseline_set(*args, **kwargs)


def _manifest_document(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "manifest_version": 1,
        "project_ref": "refs/heads/main",
        "profile": "default",
        "snapshot_schema": SCHEMA_VERSION,
        "fact_set": {"depth": "s4"},
        "baseline_generation": 3,
        "generator": {"tool": "actions/baseline", "version": "1.2.3"},
        "artifacts": [
            {
                "library": "liba.so",
                "artifact": "a",
                "snapshot": "liba.so/liba.so.abi.json",
                "sha256": "aaaa",
                "binary_sha256": "bbbb",
            },
            {
                "library": "libb.so",
                "artifact": "b",
                "snapshot": "libb.so/libb.so.abi.json",
            },
        ],
    }
    doc.update(overrides)
    return doc


def _snapshot_documents() -> dict[str, dict[str, Any]]:
    return {
        "liba.so": snapshot_to_dict(AbiSnapshot(library="liba.so", version="1.0")),
        "libb.so": snapshot_to_dict(AbiSnapshot(library="libb.so", version="1.0")),
    }


class TestImportBaselineSet:
    def test_rejects_a_non_mapping_manifest_document(self) -> None:
        with pytest.raises(TypeError):
            import_baseline_set(
                None,  # type: ignore[arg-type]
                _snapshot_documents(),
                store=InMemoryObjectStore(),
            )

    @pytest.mark.parametrize("bad_version", [None, 0, 2, "1", True])
    def test_rejects_an_unsupported_manifest_version(self, bad_version: Any) -> None:
        doc = _manifest_document(manifest_version=bad_version)
        with pytest.raises(ValueError, match="manifest_version"):
            import_baseline_set(doc, _snapshot_documents(), store=InMemoryObjectStore())

    def test_requires_a_non_empty_artifacts_list(self) -> None:
        doc = _manifest_document(artifacts=[])
        with pytest.raises(ValueError, match="artifacts"):
            import_baseline_set(doc, _snapshot_documents(), store=InMemoryObjectStore())

    def test_rejects_an_entry_with_no_library_name(self) -> None:
        doc = _manifest_document(artifacts=[{"artifact": "a", "snapshot": "x.json"}])
        with pytest.raises(ValueError, match="library"):
            import_baseline_set(doc, _snapshot_documents(), store=InMemoryObjectStore())

    def test_rejects_a_duplicate_library(self) -> None:
        doc = _manifest_document()
        doc["artifacts"].append(dict(doc["artifacts"][0]))
        with pytest.raises(ValueError, match="more than once"):
            import_baseline_set(doc, _snapshot_documents(), store=InMemoryObjectStore())

    def test_rejects_a_missing_snapshot_document(self) -> None:
        doc = _manifest_document()
        snapshots = _snapshot_documents()
        del snapshots["libb.so"]
        with pytest.raises(ValueError, match="libb.so"):
            import_baseline_set(doc, snapshots, store=InMemoryObjectStore())

    def test_produces_one_variant_and_one_artifact_per_library(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        assert [v.variant_id for v in manifest.variant_refs] == ["default"]
        assert sorted(a.artifact_id for a in manifest.artifact_refs) == [
            "liba.so",
            "libb.so",
        ]

    def test_carries_binary_sha256_onto_native_identity(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        liba = next(a for a in manifest.artifact_refs if a.artifact_id == "liba.so")
        libb = next(a for a in manifest.artifact_refs if a.artifact_id == "libb.so")
        assert liba.native_identity["binary_sha256"] == "bbbb"
        assert "binary_sha256" not in libb.native_identity

    @pytest.mark.parametrize("bad_value", [0, 1234, 0.0, [], {}])
    def test_rejects_a_non_string_binary_sha256(self, bad_value: Any) -> None:
        doc = _manifest_document()
        doc["artifacts"][0]["binary_sha256"] = bad_value
        with pytest.raises(ValueError, match="binary_sha256"):
            import_baseline_set(doc, _snapshot_documents(), store=InMemoryObjectStore())

    def test_an_empty_string_binary_sha256_means_no_staged_binary(self) -> None:
        """`""` is `BaselineArtifact.binary_sha256`'s own documented default
        for "no staged binary" -- not an error, and not carried onto
        `native_identity` (CodeRabbit review)."""
        doc = _manifest_document()
        doc["artifacts"][0]["binary_sha256"] = ""
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        liba = next(a for a in manifest.artifact_refs if a.artifact_id == "liba.so")
        assert "binary_sha256" not in liba.native_identity

    def test_attaches_a_baseline_set_metadata_section_to_the_variant(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        assert BASELINE_SET_SECTION_KIND in manifest.variant_refs[0].sections

    def test_rejects_per_library_snapshots_with_disagreeing_schema_versions(
        self,
    ) -> None:
        doc = _manifest_document()
        snapshots = _snapshot_documents()
        snapshots["libb.so"]["schema_version"] = 1
        with pytest.raises(ValueError, match="schema_version"):
            import_baseline_set(doc, snapshots, store=InMemoryObjectStore())

    def test_export_returns_metadata_and_snapshot_documents(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, snapshots = export_baseline_set(manifest, store=store)
        assert metadata["project_ref"] == "refs/heads/main"
        assert metadata["profile"] == "default"
        assert metadata["baseline_generation"] == 3
        assert metadata["generator"] == {"tool": "actions/baseline", "version": "1.2.3"}
        assert set(snapshots) == {"liba.so", "libb.so"}
        assert snapshots["liba.so"]["library"] == "liba.so"

    def test_absent_optional_metadata_keys_stay_absent_on_export(self) -> None:
        """`fact_set`/`baseline_generation`/`generator` are legitimately
        absent on a real manifest -- re-exporting must not turn that
        absence into an explicit `null` (CodeRabbit review)."""
        doc = _manifest_document()
        del doc["fact_set"]
        del doc["baseline_generation"]
        del doc["generator"]
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert "fact_set" not in metadata
        assert "baseline_generation" not in metadata
        assert "generator" not in metadata
        assert metadata["project_ref"] == "refs/heads/main"

    def test_export_rejects_an_unknown_variant_id(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        with pytest.raises(ValueError, match="no variant"):
            export_baseline_set(manifest, store=store, variant_id="does-not-exist")

    def test_export_rejects_a_variant_with_no_metadata_section(self) -> None:
        from abicheck.storage.import_v1 import import_legacy_snapshot

        store = InMemoryObjectStore()
        single = import_legacy_snapshot(
            snapshot_to_dict(AbiSnapshot(library="liba.so", version="1.0")),
            store=store,
            artifact_id="liba.so",
            max_known_schema_version=SCHEMA_VERSION,
        )
        with pytest.raises(ValueError, match=BASELINE_SET_SECTION_KIND):
            export_baseline_set(single, store=store)
