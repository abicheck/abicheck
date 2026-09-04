# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.import_bundle_facts` — the `BundleFacts` import/export
adapter (ADR-063 Track C 8B, A1.4).
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.errors import IncompatibleSnapshotSchemaError
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
        "manifest": {"provides": [{"symbol": "liba_init"}]},
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

    def test_accepts_an_explicitly_present_empty_per_library_snapshots(self) -> None:
        """A vacuous bundle (present but empty) is a real, valid
        `BundleFacts` document -- `bundle_facts_from_dict` accepts it too;
        only an *absent* key is malformed (Codex review)."""
        doc = _bundle_document(per_library_snapshots={})
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert manifest.artifact_refs == ()
        assert manifest.variant_refs[0].artifact_ids == ()
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["per_library_snapshots"] == {}

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

    def test_rejects_a_container_schema_version_newer_than_this_build_knows(
        self,
    ) -> None:
        doc = _bundle_document(schema_version=999)
        with pytest.raises(IncompatibleSnapshotSchemaError):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_schema_version_2_without_artifact_type(self) -> None:
        doc = _bundle_document()
        del doc["artifact_type"]
        with pytest.raises(ValueError, match="artifact_type"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_artifact_type_at_a_schema_version_that_predates_it(
        self,
    ) -> None:
        doc = _bundle_document(schema_version=1)
        with pytest.raises(ValueError, match="predates artifact_type"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_accepts_a_true_legacy_document_with_neither_field(self) -> None:
        doc = _bundle_document()
        del doc["artifact_type"]
        del doc["schema_version"]
        manifest = import_bundle_facts(doc, store=InMemoryObjectStore())
        assert manifest.variant_refs[0].variant_id == "default"

    def test_accepts_a_string_encoded_v1_schema_version(self) -> None:
        """`bundle_facts_from_dict` normalizes `schema_version` via a bare
        `int(...)` call, so a v1 document spelling it `"1"` (still exactly
        what `int("1") == 1` accepts) must keep loading here too (Codex
        review) -- this adapter claims to accept what that canonical reader
        accepts, not a narrower set."""
        doc = _bundle_document()
        del doc["artifact_type"]
        doc["schema_version"] = "1"
        manifest = import_bundle_facts(doc, store=InMemoryObjectStore())
        assert manifest.variant_refs[0].variant_id == "default"

    @pytest.mark.parametrize("bad_value", [1, 1.5, ["default"], {"x": "y"}])
    def test_rejects_a_non_string_variant_fingerprint(self, bad_value: Any) -> None:
        doc = _bundle_document(variant_fingerprint=bad_value)
        with pytest.raises(ValueError, match="variant_fingerprint"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_an_explicit_empty_variant_fingerprint_is_preserved_not_defaulted(
        self,
    ) -> None:
        doc = _bundle_document(variant_fingerprint="")
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["variant_fingerprint"] == ""

    def test_a_missing_filesystem_aliases_defaults_to_empty(self) -> None:
        doc = _bundle_document()
        del doc["filesystem_aliases"]
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["filesystem_aliases"] == {}

    @pytest.mark.parametrize("bad_value", [[], "", 0])
    def test_rejects_a_non_mapping_filesystem_aliases(self, bad_value: Any) -> None:
        """A falsey-but-present non-mapping is malformed input, not an
        empty collection -- `or {}` would otherwise make it
        indistinguishable from a producer that genuinely captured no
        aliases (Codex review)."""
        doc = _bundle_document(filesystem_aliases=bad_value)
        with pytest.raises(ValueError, match="filesystem_aliases"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_a_filesystem_aliases_value_that_is_not_a_string_list(
        self,
    ) -> None:
        doc = _bundle_document(filesystem_aliases={"liba.so": "not-a-list"})
        with pytest.raises(ValueError, match="filesystem_aliases"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_a_filesystem_aliases_list_with_a_non_string_entry(self) -> None:
        doc = _bundle_document(filesystem_aliases={"liba.so": ["ok", 1]})
        with pytest.raises(ValueError, match="filesystem_aliases"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    @pytest.mark.parametrize("bad_value", [[], "", 0])
    def test_rejects_a_non_mapping_library_filenames(self, bad_value: Any) -> None:
        doc = _bundle_document(library_filenames=bad_value)
        with pytest.raises(ValueError, match="library_filenames"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_rejects_a_non_string_library_filenames_value(self) -> None:
        doc = _bundle_document(library_filenames={"liba.so": 123})
        with pytest.raises(ValueError, match="library_filenames"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_a_missing_manifest_is_preserved_as_none(self) -> None:
        doc = _bundle_document(manifest=None)
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["manifest"] is None

    @pytest.mark.parametrize(
        "bad_manifest",
        [[], "not-a-mapping", {"no_provides_key": True}, {"provides": {}}],
    )
    def test_rejects_a_malformed_manifest(self, bad_manifest: Any) -> None:
        """The one shape `bundle_manifest.manifest_from_dict` requires at
        its own top level -- a mapping with a list-valued `provides` key
        -- is checked here too, without importing that flat-root module
        (Codex review)."""
        doc = _bundle_document(manifest=bad_manifest)
        with pytest.raises(ValueError, match="manifest"):
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
