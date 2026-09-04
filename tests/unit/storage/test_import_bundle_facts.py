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
from abicheck.storage.package import ArtifactRef, InMemoryObjectStore, PackageManifest


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

    def test_rejects_an_explicitly_null_artifact_type(self) -> None:
        """`"artifact_type" in bundle_facts_document`, not `.get(...) is
        not None` -- a document explicitly declaring `"artifact_type":
        null` is a malformed marker to the canonical reader, not an absent
        one (Codex review)."""
        doc = _bundle_document(artifact_type=None)
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

    def test_case_colliding_library_names_get_opaque_artifact_ids(self) -> None:
        """`libFoo.so`/`libfoo.so` are two distinct, valid libraries to
        `BundleFacts` and its canonical reader, but would collide as
        `ArtifactRef` ids on a case-insensitive filesystem -- the import
        still succeeds (rather than failing outright) by giving the
        non-canonical spelling (`libFoo.so`) an opaque id, while the
        already-canonical one (`libfoo.so`) keeps its own literal spelling
        (`resolve_ref_ids`'s own membership-independent design; Codex
        review)."""
        doc = _bundle_document(
            per_library_snapshots={
                "libFoo.so": snapshot_to_dict(
                    AbiSnapshot(library="libFoo.so", version="1.0")
                ),
                "libfoo.so": snapshot_to_dict(
                    AbiSnapshot(library="libfoo.so", version="1.0")
                ),
            }
        )
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert len(manifest.artifact_refs) == 2
        artifact_ids = {a.artifact_id for a in manifest.artifact_refs}
        assert "libFoo.so" not in artifact_ids
        assert "libfoo.so" in artifact_ids
        roundtrip = export_bundle_facts(manifest, store=store)
        assert set(roundtrip["per_library_snapshots"]) == {"libFoo.so", "libfoo.so"}

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

    def test_rejects_an_overflowing_schema_version_as_malformed(self) -> None:
        """A JSON exponent like `1e999` decodes to the float `inf`, and
        `int(inf)` raises `OverflowError`, not `ValueError`/`TypeError` --
        `bundle_facts_serialization.looks_like_bundle_facts_document`
        already handles this exact representation; this adapter must
        raise the same documented `ValueError` rather than letting
        `OverflowError` escape unhandled (Codex review, fresh evidence)."""
        doc = _bundle_document(schema_version=float("inf"))
        with pytest.raises(ValueError, match="schema_version"):
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

    @pytest.mark.parametrize("bad_value", [[], "", 0, None])
    def test_rejects_a_non_mapping_filesystem_aliases(self, bad_value: Any) -> None:
        """A falsey-but-present non-mapping -- `None` included -- is
        malformed input, not an empty collection: `... or {}` would
        otherwise make an explicit `null` indistinguishable from a
        producer that genuinely captured no aliases, and
        `validated_alias_map` itself rejects a present `None` the same way
        it rejects `[]`/`""`/`0` (Codex review, fresh evidence: an earlier
        fix covered the non-`None` falsey values but still let `raw is
        None` mean "absent" for a present-and-null key too)."""
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

    @pytest.mark.parametrize("bad_value", [[], "", 0, None])
    def test_rejects_a_non_mapping_library_filenames(self, bad_value: Any) -> None:
        """Same `None`-is-not-absence distinction as
        `test_rejects_a_non_mapping_filesystem_aliases` (Codex review)."""
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

    @pytest.mark.parametrize(
        "bad_entry",
        [
            None,
            {},
            {"symbol": "a", "pattern": "b"},
            {"symbol": "a", "optional_provider": "yes"},
            {"template": "t"},
            {"template": "t", "instantiations": []},
            {"template": "t", "instantiations": ["not-a-mapping"]},
        ],
    )
    def test_rejects_a_malformed_manifest_provides_entry(self, bad_entry: Any) -> None:
        """`manifest_from_dict`'s own per-entry rules
        (`_validate_manifest_entry_shape`/`_parse_template_instantiations`)
        are checked here too, not just the outer `manifest` container's
        top-level shape (Codex review, fresh evidence beyond the earlier
        `test_rejects_a_malformed_manifest` finding)."""
        doc = _bundle_document(manifest={"provides": [bad_entry]})
        with pytest.raises(ValueError, match="manifest"):
            import_bundle_facts(doc, store=InMemoryObjectStore())

    def test_a_symbol_entrys_ignored_instantiations_field_still_exports(self) -> None:
        """A `symbol`/`pattern` entry's own `instantiations` key, if
        present, is an ignored extra field never rewritten at import time
        -- `_validated_manifest_entry` only produces the order-preserving
        pair-list encoding for a `template`-shaped entry. Export must not
        unconditionally try to decode every entry's `instantiations` as
        that encoding, or an entry the canonical reader itself accepts
        (extra fields are never rejected) would crash on export instead of
        round-tripping unchanged (Codex review, fresh evidence)."""
        doc = _bundle_document(
            manifest={"provides": [{"symbol": "x", "instantiations": 3}]}
        )
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["manifest"]["provides"][0]["instantiations"] == 3

    def test_a_float_manifest_entry_symbol_is_coerced_to_a_string(self) -> None:
        """`_parse_manifest_entry` unconditionally coerces `symbol` via
        `str(...)` -- a raw non-string value (e.g. the float `1.0`) must be
        stored as that exact coerced string, not passed through
        unvalidated: `SectionDTO` canonicalization would otherwise silently
        rewrite `1.0` to the int `1`, so a later `str(1)` reads `"1"` where
        the canonical parser itself would have read `"1.0"` (Codex review,
        fresh evidence)."""
        doc = _bundle_document(manifest={"provides": [{"symbol": 1.0}]})
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["manifest"]["provides"] == [{"symbol": "1.0"}]

    def test_a_falsey_manifest_entry_library_is_dropped(self) -> None:
        """`_parse_manifest_entry`'s own
        `str(raw["library"]) if raw.get("library") else None` -- a falsey
        `library` (e.g. `0`) means "no library" to the canonical parser,
        never an explicit `"0"`."""
        doc = _bundle_document(manifest={"provides": [{"symbol": "a", "library": 0}]})
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert "library" not in roundtrip["manifest"]["provides"][0]

    def test_a_truthy_non_string_manifest_entry_library_is_coerced(self) -> None:
        doc = _bundle_document(manifest={"provides": [{"symbol": "a", "library": 7}]})
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["manifest"]["provides"][0]["library"] == "7"

    def test_a_float_template_instantiation_value_is_coerced_to_a_string(
        self,
    ) -> None:
        """`_parse_template_instantiations` unconditionally coerces every
        instantiation key/value via `str(...)` -- the identical
        canonicalization risk as a bare `symbol`/`pattern`/`template`
        (Codex review, fresh evidence)."""
        doc = _bundle_document(
            manifest={
                "provides": [{"template": "t", "instantiations": [{"Float": 1.0}]}]
            }
        )
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert roundtrip["manifest"]["provides"][0]["instantiations"] == [
            {"Float": "1.0"}
        ]

    def test_a_non_lexical_instantiation_parameter_order_is_preserved(self) -> None:
        """`_expand_instantiations` (`bundle_manifest.py`) builds each
        template's expanded signature from `inst.values()` in *insertion*
        order, not sorted order -- storing an instantiation as a plain
        dict would let `SectionDTO` canonicalization sort its keys
        alphabetically, silently reordering `{"Z": "first", "A": "second"}`
        into `{"A": "second", "Z": "first"}` and changing
        `T<first, second>` into `T<second, first>` on round-trip (Codex
        review, fresh evidence)."""
        doc = _bundle_document(
            manifest={
                "provides": [
                    {
                        "template": "t",
                        "instantiations": [{"Z": "first", "A": "second"}],
                    }
                ]
            }
        )
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        instantiation = roundtrip["manifest"]["provides"][0]["instantiations"][0]
        assert list(instantiation.items()) == [("Z", "first"), ("A", "second")]

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

    def test_export_looks_up_each_artifact_by_id_not_by_linear_scan_position(
        self,
    ) -> None:
        """The artifact lookup must resolve every `variant.artifact_ids`
        entry to the *matching* `ArtifactRef` regardless of where in
        `manifest.artifact_refs` it sits -- a regression a naive rewrite of
        the removed `next(a for a in manifest.artifact_refs if ...)` scan
        into a single dict built once could get wrong (e.g. building the
        dict keyed by list position instead of `artifact_id`). Two
        libraries (`_bundle_document`'s own default `liba.so`/`libb.so`
        pair), with `variant_refs[0].artifact_ids` deliberately reversed
        from `artifact_refs`' own order, so a position-keyed lookup would
        recover the wrong library name for at least one of them."""
        import dataclasses

        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert len(manifest.artifact_refs) == 2
        reversed_variant = dataclasses.replace(
            manifest.variant_refs[0],
            artifact_ids=tuple(reversed(manifest.variant_refs[0].artifact_ids)),
        )
        reversed_manifest = dataclasses.replace(
            manifest, variant_refs=(reversed_variant,)
        )
        exported = export_bundle_facts(reversed_manifest, store=store)
        assert set(exported["per_library_snapshots"]) == {"liba.so", "libb.so"}

    def test_export_rejects_duplicate_recovered_library_names(self) -> None:
        """`PackageManifest` enforces unique `artifact_id`s but not unique
        recovered `native_identity['library_name']` values -- a manifest
        built or loaded some other way (not `import_bundle_facts` itself)
        can still carry two artifacts that recover the same library name.
        Export must raise rather than silently dropping one artifact's
        snapshot via a dict-key overwrite (Codex review, fresh evidence)."""
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
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
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_an_artifact_carrying_an_unadvertised_section(
        self,
    ) -> None:
        """A hand-edited package that keeps a recognized, validly-encoded
        section (here: `declarations`) on an artifact while dropping it from
        the package-wide `section_schema_versions` map must be rejected --
        `check_reader_compatibility()` alone does not catch this, and
        without this check the section's own real, still-decodable content
        would otherwise reach the reconstructed `BundleFacts` unversioned
        (Codex review). Uses a real, valid section digest (not corrupted
        content) so the *only* thing that can catch this is the
        artifact-vs-manifest cross-check under test, not an incidental
        decode failure."""
        import dataclasses

        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert "declarations" in manifest.versions.section_schema_versions
        doctored_versions = dataclasses.replace(
            manifest.versions,
            section_schema_versions={
                kind: version
                for kind, version in manifest.versions.section_schema_versions.items()
                if kind != "declarations"
            },
        )
        doctored = dataclasses.replace(manifest, versions=doctored_versions)
        with pytest.raises(ValueError, match="declarations"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_variant_section_carrying_an_unadvertised_kind(
        self,
    ) -> None:
        """The identical risk one level up from the artifact case above: a
        hand-edited package that keeps the variant's own recognized, still
        validly-encoded `bundle_composition` section while dropping it from
        the package-wide `section_schema_versions` map must be rejected
        *before* that section is ever decoded -- unversioned contract
        evidence (the variant fingerprint, the instantiation manifest) must
        never reach a comparison even transiently (Codex review)."""
        import dataclasses

        from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND

        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        assert (
            BUNDLE_COMPOSITION_SECTION_KIND in manifest.versions.section_schema_versions
        )
        doctored_versions = dataclasses.replace(
            manifest.versions,
            section_schema_versions={
                kind: version
                for kind, version in manifest.versions.section_schema_versions.items()
                if kind != BUNDLE_COMPOSITION_SECTION_KIND
            },
        )
        doctored = dataclasses.replace(manifest, versions=doctored_versions)
        with pytest.raises(ValueError, match=BUNDLE_COMPOSITION_SECTION_KIND):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_an_artifact_with_no_library_name_evidence(
        self,
    ) -> None:
        """Falling back to `artifact_id` when `native_identity` carries no
        `library_name` (an earlier version's behavior) would export an
        opaque hash as a library name whenever the id happens to be one --
        `import_bundle_facts` itself always sets this key, so its absence
        means this manifest wasn't produced by it (CodeRabbit review)."""
        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        doctored_artifacts = tuple(
            ArtifactRef(
                artifact_id=artifact.artifact_id,
                variant_id=artifact.variant_id,
                kind=artifact.kind,
                native_identity={},
                sections=artifact.sections,
            )
            for artifact in manifest.artifact_refs
        )
        doctored = PackageManifest(
            versions=manifest.versions,
            variant_refs=manifest.variant_refs,
            artifact_refs=doctored_artifacts,
        )
        with pytest.raises(ValueError, match="library_name"):
            export_bundle_facts(doctored, store=store)

    def test_an_empty_string_library_name_round_trips(self) -> None:
        """The empty string is itself a valid `per_library_snapshots` key
        -- the canonical `bundle_facts_from_dict` reader has no key-shape
        validation for it at all -- so `import_bundle_facts` can produce a
        real `native_identity[_LIBRARY_NAME_KEY] == ""`. Rejecting it by
        truthiness (rather than by key presence) would make an
        import-produced package unexportable (Codex review, fresh
        evidence: this is the concrete counterexample to the previous
        missing-library-name fix)."""
        doc = _bundle_document(
            per_library_snapshots={
                "": snapshot_to_dict(AbiSnapshot(library="", version="1.0"))
            }
        )
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        roundtrip = export_bundle_facts(manifest, store=store)
        assert set(roundtrip["per_library_snapshots"]) == {""}

    def test_export_rejects_a_variant_with_no_composition_section(self) -> None:
        from abicheck.storage.import_v1 import import_legacy_snapshot
        from abicheck.storage.package import VariantRef

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

    def test_export_rejects_a_composition_ref_whose_kind_does_not_match(self) -> None:
        """`variant.sections`' own key and the `ObjectRef.kind` it maps to
        are two independent fields -- a hand-assembled package could map
        `BUNDLE_COMPOSITION_SECTION_KIND` to an `ObjectRef` declaring a
        different `kind` while still pointing at a real, validly-encoded
        digest, defeating the ref's own purpose of identifying its content
        before it is fetched (Codex review)."""
        import dataclasses

        doc = _bundle_document()
        store = InMemoryObjectStore()
        manifest = import_bundle_facts(doc, store=store)
        real_ref = manifest.variant_refs[0].sections[BUNDLE_COMPOSITION_SECTION_KIND]
        doctored_variant = dataclasses.replace(
            manifest.variant_refs[0],
            sections={
                BUNDLE_COMPOSITION_SECTION_KIND: dataclasses.replace(
                    real_ref, kind="not_bundle_composition"
                )
            },
        )
        doctored = dataclasses.replace(manifest, variant_refs=(doctored_variant,))
        with pytest.raises(ValueError, match="not_bundle_composition"):
            export_bundle_facts(doctored, store=store)

    def _doctored_composition_manifest(
        self, payload: dict[str, Any]
    ) -> tuple[PackageManifest, InMemoryObjectStore]:
        """A manifest whose `BUNDLE_COMPOSITION_SECTION_KIND` section holds
        *payload* verbatim -- unlike every other test here, built by storing
        a `SectionDTO` directly rather than through `import_bundle_facts`,
        so its own on-import validation (which `import_bundle_facts` itself
        always applies) cannot have run. Simulates a hand-assembled or
        corrupted package, exactly the class of input `export_bundle_facts`
        must not trust."""
        from abicheck.storage.dto import bundle_composition_to_dto
        from abicheck.storage.package import ObjectRef, VariantRef

        store = InMemoryObjectStore()
        doc = _bundle_document()
        real_manifest = import_bundle_facts(doc, store=store)
        composition_dto = bundle_composition_to_dto(payload)
        digest = store.put(composition_dto.to_dict())
        doctored_variant = VariantRef(
            variant_id="default",
            artifact_ids=real_manifest.variant_refs[0].artifact_ids,
            sections={
                BUNDLE_COMPOSITION_SECTION_KIND: ObjectRef(
                    kind=BUNDLE_COMPOSITION_SECTION_KIND, digest=digest
                )
            },
        )
        doctored = PackageManifest(
            versions=real_manifest.versions,
            variant_refs=(doctored_variant,),
            artifact_refs=real_manifest.artifact_refs,
        )
        return doctored, store

    def test_export_rejects_a_non_string_stored_variant_fingerprint(self) -> None:
        """`import_bundle_facts` itself rejects a non-string
        `variant_fingerprint` outright, but that check only runs for a
        document that actually went through it -- a composition section
        built or stored some other way is untrusted content too.
        `bundle_facts_from_dict()` unconditionally coerces this field via
        `str(...)`, so a stored non-string value would otherwise silently
        become a different string, possibly colliding with a genuinely
        distinct, already-string fingerprint elsewhere and letting
        `pair_variants()` compare the wrong variants (Codex review)."""
        doctored, store = self._doctored_composition_manifest(
            {"variant_fingerprint": 1, "manifest": None}
        )
        with pytest.raises(ValueError, match="variant_fingerprint"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_stored_instantiation_with_a_duplicate_parameter(
        self,
    ) -> None:
        """A stored template instantiation naming the same parameter twice
        (e.g. `[["T", "int"], ["T", "float"]]`) must be rejected, not
        silently collapsed to its last value by a plain `dict(pairs)`
        conversion -- the identical risk `_validated_manifest_entry`'s own
        import-time coercion guards against, reapplied on the read side for
        content that never went through it (Codex review)."""
        doctored, store = self._doctored_composition_manifest(
            {
                "variant_fingerprint": "default",
                "manifest": {
                    "provides": [
                        {
                            "template": "acme::train_ops",
                            "instantiations": [
                                [["T", "int"], ["T", "float"]],
                            ],
                        }
                    ]
                },
            }
        )
        with pytest.raises(ValueError, match="more than once"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_non_list_stored_instantiations_value(self) -> None:
        """A stored `template` entry whose `instantiations` value is a
        scalar (e.g. `1`) rather than a list must raise the documented
        `ValueError`, not an unhandled `TypeError` from the list
        comprehension that otherwise tries to iterate it directly
        (CodeRabbit review)."""
        doctored, store = self._doctored_composition_manifest(
            {
                "variant_fingerprint": "default",
                "manifest": {
                    "provides": [
                        {
                            "template": "acme::train_ops",
                            "instantiations": 1,
                        }
                    ]
                },
            }
        )
        with pytest.raises(ValueError, match="instantiations"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_non_mapping_stored_manifest_entry(self) -> None:
        """A stored `provides` entry that is a JSON list of `[key, value]`
        pairs (e.g. `[["symbol", "foo"]]`) would otherwise convert via a
        plain `dict(entry)` into a valid-looking mapping, silently
        accepting contract evidence `manifest_from_dict` itself rejects
        outright -- the identical risk the duplicate-instantiation-
        parameter check above guards against one level down, here one
        level up (Codex review, fresh evidence)."""
        doctored, store = self._doctored_composition_manifest(
            {
                "variant_fingerprint": "default",
                "manifest": {"provides": [[["symbol", "liba_init"]]]},
            }
        )
        with pytest.raises(ValueError, match="mapping"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_stored_manifest_with_no_provides_list(self) -> None:
        """Mirrors `_validated_manifest`'s own import-side shape check,
        applied symmetrically on export: a stored `manifest` that is not a
        mapping with a list-valued `provides` key must raise the
        documented `ValueError`, not an unhandled `TypeError`/`KeyError`
        from the dict-spread/subscript that follows (Codex review)."""
        doctored, store = self._doctored_composition_manifest(
            {"variant_fingerprint": "default", "manifest": {"not_provides": []}}
        )
        with pytest.raises(ValueError, match="provides"):
            export_bundle_facts(doctored, store=store)

    def test_export_rejects_a_non_mapping_stored_manifest(self) -> None:
        doctored, store = self._doctored_composition_manifest(
            {"variant_fingerprint": "default", "manifest": "not-a-mapping"}
        )
        with pytest.raises(ValueError, match="provides"):
            export_bundle_facts(doctored, store=store)
