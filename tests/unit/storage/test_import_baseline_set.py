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
from abicheck.storage.package import ArtifactRef, InMemoryObjectStore, PackageManifest


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
        "freshness": {"refresh_required": False, "reasons": []},
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

    @pytest.mark.parametrize("bad_version", [None, 0, 2, "1", True, 1.0])
    def test_rejects_an_unsupported_manifest_version(self, bad_version: Any) -> None:
        """`1.0` is included alongside the other malformed values: it is
        numerically `== 1` (and hashes the same), so a bare `not in`
        membership check would silently accept it even though
        `load_baseline_manifest` itself parses a non-strict-int
        `manifest_version` as unstated, not as version 1 (Codex review,
        fresh evidence)."""
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

    def test_case_colliding_library_names_get_opaque_artifact_ids(self) -> None:
        doc = _manifest_document(
            artifacts=[
                {"library": "libFoo.so", "artifact": "a", "snapshot": "a.json"},
                {"library": "libfoo.so", "artifact": "b", "snapshot": "b.json"},
            ]
        )
        snapshots = {
            "libFoo.so": snapshot_to_dict(
                AbiSnapshot(library="libFoo.so", version="1.0")
            ),
            "libfoo.so": snapshot_to_dict(
                AbiSnapshot(library="libfoo.so", version="1.0")
            ),
        }
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, snapshots, store=store)
        assert len(manifest.artifact_refs) == 2
        artifact_ids = {a.artifact_id for a in manifest.artifact_refs}
        # `resolve_ref_ids`'s own membership-independent design: the
        # already-canonical spelling (`libfoo.so`) keeps its literal id,
        # only the non-canonical one (`libFoo.so`) goes opaque.
        assert "libFoo.so" not in artifact_ids
        assert "libfoo.so" in artifact_ids
        _metadata, exported_snapshots = export_baseline_set(manifest, store=store)
        assert set(exported_snapshots) == {"libFoo.so", "libfoo.so"}

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
        assert metadata["freshness"] == {"refresh_required": False, "reasons": []}
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

    @pytest.mark.parametrize("key", ["snapshot_schema", "baseline_generation"])
    def test_a_float_strict_int_metadata_value_is_dropped_not_normalized(
        self, key: str
    ) -> None:
        """`SectionDTO` canonicalization would silently rewrite a float like
        `3.0` into the int `3`, laundering "unstated" into a stated,
        decision-bearing value. `load_baseline_manifest` itself treats a
        non-strict-int `snapshot_schema`/`baseline_generation` as unstated,
        so the import adapter must drop the field entirely rather than let
        canonicalization coerce it (Codex review, fresh evidence)."""
        doc = _manifest_document(**{key: 3.0})
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert key not in metadata

    @pytest.mark.parametrize("key", ["snapshot_schema", "baseline_generation"])
    def test_a_bool_strict_int_metadata_value_is_dropped(self, key: str) -> None:
        doc = _manifest_document(**{key: True})
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert key not in metadata

    @pytest.mark.parametrize("key", ["project_ref", "profile"])
    def test_a_non_string_string_metadata_value_is_coerced_like_the_canonical_reader(
        self, key: str
    ) -> None:
        """`load_baseline_manifest` itself coerces `project_ref`/`profile`
        via `str(value or "")` -- a raw non-string value (e.g. the float
        `1.0`) must be stored as that exact coerced string, not passed
        through unvalidated: `SectionDTO` canonicalization would otherwise
        silently rewrite `1.0` to the int `1`, so a later `str(1)` reads
        `"1"` where the canonical reader itself would have read `"1.0"`
        (Codex review, fresh evidence)."""
        doc = _manifest_document(**{key: 1.0})
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert metadata[key] == "1.0"

    @pytest.mark.parametrize("key", ["project_ref", "profile"])
    def test_a_falsey_non_string_string_metadata_value_becomes_empty_string(
        self, key: str
    ) -> None:
        doc = _manifest_document(**{key: 0})
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert metadata[key] == ""

    def test_a_float_fact_set_producer_is_coerced_like_the_canonical_reader(
        self,
    ) -> None:
        """`_evidence_incompatibility` (`buildsource.baseline_set`) reads
        `fact_set['producer']` and coerces it via `str(value or "")` --
        the identical nested-identity canonicalization risk
        `project_ref`/`profile` already guard against, one level deeper
        (Codex review, fresh evidence)."""
        doc = _manifest_document(fact_set={"producer": 1.0, "depth": "s4"})
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert metadata["fact_set"]["producer"] == "1.0"
        assert metadata["fact_set"]["depth"] == "s4"

    def test_a_non_mapping_fact_set_round_trips_unchanged(self) -> None:
        doc = _manifest_document(fact_set="not-a-mapping")
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        metadata, _snapshots = export_baseline_set(manifest, store=store)
        assert metadata["fact_set"] == "not-a-mapping"

    def test_export_rejects_an_unknown_variant_id(self) -> None:
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        with pytest.raises(ValueError, match="no variant"):
            export_baseline_set(manifest, store=store, variant_id="does-not-exist")

    def test_export_rejects_duplicate_recovered_library_names(self) -> None:
        """The same defensive check as `import_bundle_facts.export_
        bundle_facts`'s own duplicate-library-name finding (Codex review,
        fresh evidence): `PackageManifest` enforces unique `artifact_id`s
        but not unique recovered `native_identity['library_name']`
        values."""
        doc = _manifest_document()
        store = InMemoryObjectStore()
        manifest = import_baseline_set(doc, _snapshot_documents(), store=store)
        duplicated_artifacts = tuple(
            ArtifactRef(
                artifact_id=artifact.artifact_id,
                variant_id=artifact.variant_id,
                kind=artifact.kind,
                native_identity={"library_name": "liba.so"},
                sections=artifact.sections,
            )
            for artifact in manifest.artifact_refs
        )
        doctored = PackageManifest(
            versions=manifest.versions,
            variant_refs=manifest.variant_refs,
            artifact_refs=duplicated_artifacts,
        )
        with pytest.raises(ValueError, match="more than one artifact"):
            export_baseline_set(doctored, store=store)

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
