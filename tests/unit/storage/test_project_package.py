# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`abicheck.storage.package` — the `ProjectSnapshot` object model (A1.1).

Covers `ObjectRef`/`VariantRef`/`ArtifactRef`/`PackageManifest`'s guards and
round trips, the D6 path-layout functions, and `ObjectStore`'s
`put`/`get`/`has` contract via `InMemoryObjectStore` — the same
"contract as invariants, not only example cases" convention the sibling
Phase 0 modules use (root `AGENTS.md`'s "Primitive-level property tests").
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import semantic_digest
from abicheck.storage.package import (
    MANIFEST_RELPATH,
    ArtifactRef,
    InMemoryObjectStore,
    ObjectRef,
    ObjectStore,
    PackageManifest,
    VariantRef,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from abicheck.storage.versioning import StorageVersions


class TestObjectRef:
    def test_round_trips(self) -> None:
        ref = ObjectRef(kind="declarations", digest="sha256:" + "ab" * 32, size=1024)
        assert ObjectRef.from_dict(ref.to_dict()) == ref

    def test_size_is_omitted_when_zero(self) -> None:
        ref = ObjectRef(kind="graph", digest="sha256:" + "cd" * 32)
        assert "size" not in ref.to_dict()

    @pytest.mark.parametrize("field_name", ["kind", "digest"])
    def test_empty_identity_field_is_refused(self, field_name: str) -> None:
        kwargs: dict[str, Any] = {"kind": "graph", "digest": "sha256:ab"}
        kwargs[field_name] = ""
        with pytest.raises(ValueError):
            ObjectRef(**kwargs)

    @pytest.mark.parametrize("value", [1, 1.0, None, ["x"], {"k": "v"}])
    def test_a_non_string_identity_field_is_rejected_not_coerced(
        self, value: Any
    ) -> None:
        with pytest.raises(TypeError):
            ObjectRef(kind=value, digest="sha256:ab")
        with pytest.raises(TypeError):
            ObjectRef(kind="graph", digest=value)

    def test_from_dict_refuses_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            ObjectRef.from_dict("not a mapping")  # type: ignore[arg-type]

    def test_from_dict_requires_kind_and_digest(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            ObjectRef.from_dict({"digest": "sha256:ab"})
        with pytest.raises(ValueError, match="digest"):
            ObjectRef.from_dict({"kind": "graph"})

    @pytest.mark.parametrize("bad_size", [-1, "big", None, 1.5, True])
    def test_a_malformed_size_degrades_to_unknown_rather_than_aborting(
        self, bad_size: Any
    ) -> None:
        # `size` is informational -- no decision reads it -- so it degrades,
        # unlike `kind`/`digest`.
        loaded = ObjectRef.from_dict(
            {"kind": "graph", "digest": "sha256:ab", "size": bad_size}
        )
        assert loaded.size == 0

    def test_size_alone_does_not_change_identity(self) -> None:
        a = ObjectRef(kind="graph", digest="sha256:ab", size=1)
        b = ObjectRef(kind="graph", digest="sha256:ab", size=2)
        # Not asserting `a == b` -- the dataclass equality is field-by-field
        # and legitimately differs on `size`. What must never differ is what
        # the reference resolves to.
        assert a.digest == b.digest


class TestPathLayout:
    def test_manifest_relpath_is_fixed(self) -> None:
        assert MANIFEST_RELPATH == "manifest.json"

    def test_object_relpath_shards_by_the_first_two_hex_characters(self) -> None:
        digest = semantic_digest({"x": 1})
        path = object_relpath(digest)
        _, _, hexdigest = digest.partition(":")
        assert path == f"objects/sha256/{hexdigest[:2]}/{hexdigest}.json"

    @pytest.mark.parametrize(
        "digest",
        [
            "not-a-digest",
            "sha256:",
            ":deadbeef",
            "sha256:not-hex-at-all-zz",
            "sha256:a",
            "",
        ],
    )
    def test_object_relpath_refuses_a_malformed_digest(self, digest: str) -> None:
        with pytest.raises(ValueError):
            object_relpath(digest)

    def test_object_relpath_refuses_a_non_string(self) -> None:
        with pytest.raises(TypeError):
            object_relpath(1)  # type: ignore[arg-type]

    def test_variant_and_artifact_relpaths(self) -> None:
        assert variant_ref_relpath("cpu-gcc") == "refs/variants/cpu-gcc.json"
        assert artifact_ref_relpath("libfoo") == "refs/artifacts/libfoo.json"

    @pytest.mark.parametrize(
        "bad_id",
        ["", ".", "..", "a/b", "a\\b", "../escape", "a\x00b"],
    )
    def test_a_ref_id_cannot_escape_its_directory(self, bad_id: str) -> None:
        with pytest.raises(ValueError):
            variant_ref_relpath(bad_id)
        with pytest.raises(ValueError):
            artifact_ref_relpath(bad_id)


class TestVariantRef:
    def test_round_trips(self) -> None:
        variant = VariantRef(
            variant_id="cpu-gcc",
            declared={"target": "x86_64", "compiler_family": "gcc"},
            captured={"compiler_version": "14.2"},
            artifact_ids=("libb", "liba"),
        )
        assert VariantRef.from_dict(variant.to_dict()) == variant

    def test_artifact_ids_are_deduplicated_and_sorted(self) -> None:
        variant = VariantRef(variant_id="v", artifact_ids=("b", "a", "a", "b", "c"))
        assert variant.artifact_ids == ("a", "b", "c")

    def test_coordinate_maps_are_sorted_regardless_of_insertion_order(self) -> None:
        first = VariantRef(variant_id="v", declared={"b": "2", "a": "1"})
        second = VariantRef(variant_id="v", declared={"a": "1", "b": "2"})
        assert first == second
        assert list(first.declared.items()) == [("a", "1"), ("b", "2")]

    def test_the_coordinate_map_is_read_only(self) -> None:
        variant = VariantRef(variant_id="v", declared={"a": "1"})
        with pytest.raises(TypeError):
            variant.declared["b"] = "2"  # type: ignore[index]

    def test_empty_variant_id_is_refused(self) -> None:
        with pytest.raises(ValueError):
            VariantRef(variant_id="")

    @pytest.mark.parametrize("value", [1, None, "s", ["a", "1"], {("a",): "1"}])
    def test_a_malformed_coordinate_map_is_refused(self, value: Any) -> None:
        with pytest.raises(TypeError):
            VariantRef(variant_id="v", declared=value)

    def test_a_non_string_coordinate_key_or_value_is_rejected_not_coerced(
        self,
    ) -> None:
        with pytest.raises(TypeError):
            VariantRef(variant_id="v", declared={1: "x"})
        with pytest.raises(TypeError):
            VariantRef(variant_id="v", declared={"x": 1})

    def test_from_dict_refuses_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            VariantRef.from_dict([])  # type: ignore[arg-type]

    def test_from_dict_requires_variant_id(self) -> None:
        with pytest.raises(ValueError, match="variant_id"):
            VariantRef.from_dict({})

    def test_optional_fields_default_to_empty(self) -> None:
        variant = VariantRef.from_dict({"variant_id": "v"})
        assert variant.declared == {}
        assert variant.captured == {}
        assert variant.artifact_ids == ()
        assert variant.to_dict() == {"variant_id": "v"}


class TestArtifactRef:
    _OBJ = ObjectRef(kind="declarations", digest="sha256:" + "11" * 32)

    def test_round_trips(self) -> None:
        artifact = ArtifactRef(
            artifact_id="libfoo",
            variant_id="cpu-gcc",
            kind="elf",
            native_identity={"build_id": "deadbeef"},
            sections={"declarations": self._OBJ},
        )
        assert ArtifactRef.from_dict(artifact.to_dict()) == artifact

    def test_a_header_only_artifact_has_no_binary_section(self) -> None:
        artifact = ArtifactRef(
            artifact_id="headeronly",
            variant_id="v",
            kind="header_only",
            sections={"declarations": self._OBJ},
        )
        assert "binary" not in artifact.sections
        assert ArtifactRef.from_dict(artifact.to_dict()) == artifact

    def test_sections_are_sorted_regardless_of_insertion_order(self) -> None:
        other = ObjectRef(kind="types", digest="sha256:" + "22" * 32)
        first = ArtifactRef(
            artifact_id="a",
            variant_id="v",
            kind="elf",
            sections={"types": other, "declarations": self._OBJ},
        )
        second = ArtifactRef(
            artifact_id="a",
            variant_id="v",
            kind="elf",
            sections={"declarations": self._OBJ, "types": other},
        )
        assert first == second
        assert list(first.sections) == ["declarations", "types"]

    @pytest.mark.parametrize(
        "value", [1, "not-a-ref", None, {"kind": "x", "digest": "y"}]
    )
    def test_a_section_value_must_be_an_object_ref(self, value: Any) -> None:
        with pytest.raises(TypeError):
            ArtifactRef(
                artifact_id="a", variant_id="v", kind="elf", sections={"binary": value}
            )

    def test_from_dict_refuses_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            ArtifactRef.from_dict(1)  # type: ignore[arg-type]

    def test_from_dict_requires_identity_fields(self) -> None:
        with pytest.raises(ValueError, match="artifact_id"):
            ArtifactRef.from_dict({"variant_id": "v", "kind": "elf"})
        with pytest.raises(ValueError, match="variant_id"):
            ArtifactRef.from_dict({"artifact_id": "a", "kind": "elf"})
        with pytest.raises(ValueError, match="kind"):
            ArtifactRef.from_dict({"artifact_id": "a", "variant_id": "v"})

    def test_from_dict_refuses_a_malformed_sections_container(self) -> None:
        with pytest.raises(TypeError):
            ArtifactRef.from_dict(
                {
                    "artifact_id": "a",
                    "variant_id": "v",
                    "kind": "elf",
                    "sections": "not-a-mapping",
                }
            )


class TestPackageManifest:
    _V = VariantRef(variant_id="cpu-gcc")
    _A = ArtifactRef(artifact_id="libfoo", variant_id="cpu-gcc", kind="elf")

    def test_round_trips(self) -> None:
        manifest = PackageManifest(
            versions=StorageVersions(),
            variant_refs=(self._V,),
            artifact_refs=(self._A,),
        )
        assert PackageManifest.from_dict(manifest.to_dict()) == manifest

    def test_an_empty_manifest_still_round_trips(self) -> None:
        manifest = PackageManifest()
        assert PackageManifest.from_dict(manifest.to_dict()) == manifest
        assert manifest.to_dict() == {"versions": StorageVersions().to_dict()}

    def test_duplicate_variant_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate variant_id"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id="v"),
                    VariantRef(variant_id="v", declared={"a": "1"}),
                )
            )

    def test_duplicate_artifact_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate artifact_id"):
            PackageManifest(
                variant_refs=(self._V,),
                artifact_refs=(
                    self._A,
                    ArtifactRef(artifact_id="libfoo", variant_id="cpu-gcc", kind="pe"),
                ),
            )

    def test_an_artifact_naming_an_undeclared_variant_is_refused(self) -> None:
        with pytest.raises(ValueError, match="undeclared"):
            PackageManifest(artifact_refs=(self._A,))

    def test_refs_are_sorted_regardless_of_construction_order(self) -> None:
        v_a = VariantRef(variant_id="a")
        v_b = VariantRef(variant_id="b")
        first = PackageManifest(variant_refs=(v_b, v_a))
        second = PackageManifest(variant_refs=(v_a, v_b))
        assert first == second
        assert [v.variant_id for v in first.variant_refs] == ["a", "b"]

    def test_from_dict_refuses_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            PackageManifest.from_dict(None)  # type: ignore[arg-type]

    def test_a_null_versions_block_degrades_to_unstated(self) -> None:
        from abicheck.storage.versioning import UNSTATED_VERSION

        manifest = PackageManifest.from_dict({"versions": None})
        assert manifest.versions.package_format_version == UNSTATED_VERSION

    def test_a_malformed_refs_container_is_refused(self) -> None:
        with pytest.raises(TypeError, match="sequence of rows"):
            PackageManifest.from_dict({"variant_refs": {"not": "a-list"}})


class TestVariantConstructionOrderNeverAffectsTheDigest:
    """D5's rule, stated at the level this module actually operates at.

    Randomizing which order a caller passed `variant_refs`/`artifact_refs`
    or built a coordinate mapping in must never change the manifest's
    canonical form or digest.
    """

    @given(st.permutations(["a", "b", "c", "d"]))
    def test_variant_order_does_not_affect_the_digest(self, order: list[str]) -> None:
        variants = tuple(VariantRef(variant_id=name) for name in order)
        manifest = PackageManifest(variant_refs=variants)
        baseline = PackageManifest(
            variant_refs=tuple(VariantRef(variant_id=n) for n in sorted(order))
        )
        assert semantic_digest(manifest.to_dict()) == semantic_digest(
            baseline.to_dict()
        )

    @given(
        st.dictionaries(
            st.sampled_from(["target", "compiler_family", "toggle_lto"]),
            st.text(min_size=1, max_size=6, alphabet="abcxyz01"),
            min_size=0,
            max_size=3,
        )
    )
    def test_coordinate_insertion_order_does_not_affect_the_digest(
        self, coordinates: dict[str, str]
    ) -> None:
        shuffled = dict(reversed(list(coordinates.items())))
        first = VariantRef(variant_id="v", declared=coordinates)
        second = VariantRef(variant_id="v", declared=shuffled)
        assert semantic_digest(first.to_dict()) == semantic_digest(second.to_dict())


class TestObjectStoreContract:
    """`InMemoryObjectStore`'s behaviour is the contract any `ObjectStore`
    implementation must satisfy -- exercised here as the fixture other
    implementations get checked against (per the class's own docstring).
    """

    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryObjectStore(), ObjectStore)

    def test_put_returns_the_semantic_digest(self) -> None:
        store = InMemoryObjectStore()
        content = {"declarations": [1, 2, 3]}
        assert store.put(content) == semantic_digest(content)

    def test_storing_identical_content_twice_returns_one_digest(self) -> None:
        store = InMemoryObjectStore()
        first = store.put({"a": 1, "b": 2})
        second = store.put({"b": 2, "a": 1})  # same content, different order
        assert first == second

    def test_get_returns_the_canonical_form(self) -> None:
        store = InMemoryObjectStore()
        digest = store.put({"b": 2, "a": 1})
        assert store.get(digest) == {"a": 1, "b": 2}

    def test_get_raises_key_error_for_an_unknown_digest(self) -> None:
        store = InMemoryObjectStore()
        with pytest.raises(KeyError):
            store.get("sha256:" + "00" * 32)

    def test_has_reflects_what_was_put(self) -> None:
        store = InMemoryObjectStore()
        digest = store.put({"x": 1})
        assert store.has(digest)
        assert not store.has("sha256:" + "ff" * 32)

    @pytest.mark.parametrize("value", [1, None, 1.0, ["sha256:ab"]])
    def test_get_and_has_reject_a_non_string_digest(self, value: Any) -> None:
        store = InMemoryObjectStore()
        with pytest.raises(TypeError):
            store.get(value)
        with pytest.raises(TypeError):
            store.has(value)

    def test_a_reference_built_from_a_stored_digest_resolves(self) -> None:
        """The whole point of D7: an `ObjectRef` and a store must agree."""
        store = InMemoryObjectStore()
        digest = store.put({"kind": "graph", "nodes": []})
        ref = ObjectRef(kind="graph", digest=digest)
        assert store.get(ref.digest) == {"kind": "graph", "nodes": []}
