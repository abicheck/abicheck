# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.import_bundle_facts` — the `BundleFacts` import/export
adapter (ADR-063 Track C 8B, A1.4).
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND
from abicheck.storage.import_bundle_facts import (
    BUNDLE_FACTS_ARTIFACT_TYPE,
    export_bundle_facts,
    import_bundle_facts as _import_bundle_facts,
)
from abicheck.storage.package import InMemoryObjectStore


def import_bundle_facts(*args: Any, **kwargs: Any) -> Any:
    """`import_bundle_facts`, defaulting `max_known_schema_version` to this
    build's real `serialization.SCHEMA_VERSION` — mirrors
    `test_import_v1.py`'s own helper of the same shape."""
    kwargs.setdefault("max_known_schema_version", SCHEMA_VERSION)
    return _import_bundle_facts(*args, **kwargs)


def _bundle_document(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
        "schema_version": 2,
        "variant_fingerprint": "default",
        "per_library_snapshots": {
            "liba.so": snapshot_to_dict(AbiSnapshot(library="liba.so", version="1.0")),
            "libb.so": snapshot_to_dict(AbiSnapshot(library="libb.so", version="1.0")),
        },
        "filesystem_aliases": {"liba.so": ["liba.so.1"]},
        "library_filenames": {"liba.so": "liba.so.1.2.3"},
        "manifest": {"kind": "instantiation-manifest", "libraries": ["liba.so"]},
    }
    doc.update(overrides)
    return doc


class TestImportBundleFacts:
    def test_rejects_a_non_mapping_document(self) -> None:
        with pytest.raises(TypeError):
            import_bundle_facts(None, store=InMemoryObjectStore())  # type: ignore[arg-type]

    def test_rejects_a_mismatched_artifact_type(self) -> None:
        doc = _bundle_document(artifact_type="something-else")
        with pytest.raises(ValueError, match="artifact_type"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_requires_per_library_snapshots(self) -> None:
        doc = _bundle_document()
        del doc["per_library_snapshots"]
        with pytest.raises(ValueError, match="per_library_snapshots"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_an_empty_per_library_snapshots(self) -> None:
        doc = _bundle_document(per_library_snapshots={})
        with pytest.raises(ValueError, match="per_library_snapshots"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_produces_one_variant_and_one_artifact_per_library(self) -> None:
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert [v.variant_id for v in manifest.variant_refs] == ["default"]
        assert sorted(a.artifact_id for a in manifest.artifact_refs) == [
            "liba.so",
            "libb.so",
        ]
        assert manifest.variant_refs[0].artifact_ids == ("liba.so", "libb.so")

    def test_attaches_a_bundle_composition_section_to_the_variant(self) -> None:
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        sections = manifest.variant_refs[0].sections
        assert BUNDLE_COMPOSITION_SECTION_KIND in sections

    def test_rejects_per_library_snapshots_with_disagreeing_schema_versions(
        self,
    ) -> None:
        doc = _bundle_document()
        doc["per_library_snapshots"]["libb.so"]["schema_version"] = 1
        with pytest.raises(ValueError, match="schema_version"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_export_is_the_exact_inverse(self) -> None:
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["artifact_type"] == BUNDLE_FACTS_ARTIFACT_TYPE
        assert roundtrip["variant_fingerprint"] == "default"
        assert roundtrip["filesystem_aliases"] == {"liba.so": ["liba.so.1"]}
        assert roundtrip["library_filenames"] == {"liba.so": "liba.so.1.2.3"}
        assert roundtrip["manifest"] == doc["manifest"]
        assert set(roundtrip["per_library_snapshots"]) == {"liba.so", "libb.so"}
        assert roundtrip["per_library_snapshots"]["liba.so"]["library"] == "liba.so"

    def test_export_rejects_an_unknown_variant_id(self) -> None:
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        with pytest.raises(ValueError, match="no variant"):
            export_bundle_facts(manifest, store=store, variant_id="does-not-exist")

    def test_export_rejects_a_variant_with_no_composition_section(self) -> None:
        from abicheck.storage.import_v1 import import_legacy_snapshot
        from abicheck.storage.package import PackageManifest, VariantRef

        store = InMemoryObjectStore()
        single = import_legacy_snapshot(
            snapshot_to_dict(AbiSnapshot(library="liba.so", version="1.0")),
            store=store,
            artifact_id="liba.so",
            max_known_schema_version=SCHEMA_VERSION,
        )
        # `single` was produced by `import_legacy_snapshot`, not
        # `import_bundle_facts` -- its own `VariantRef` carries no
        # `BUNDLE_COMPOSITION_SECTION_KIND` section.
        assert isinstance(single, PackageManifest)
        assert isinstance(single.variant_refs[0], VariantRef)
        with pytest.raises(ValueError, match=BUNDLE_COMPOSITION_SECTION_KIND):
            export_bundle_facts(single, store=store)
