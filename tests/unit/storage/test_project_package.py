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

import hashlib
import unicodedata
from collections.abc import Mapping
from typing import Any

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import (
    raw_digest,
    semantic_digest,
    strip_capture_metadata,
)
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
        kwargs: dict[str, Any] = {
            "kind": "graph",
            "digest": "sha256:" + "ab" * 32,
        }
        kwargs[field_name] = ""
        with pytest.raises(ValueError):
            ObjectRef(**kwargs)

    @pytest.mark.parametrize("value", [1, 1.0, None, ["x"], {"k": "v"}])
    def test_a_non_string_identity_field_is_rejected_not_coerced(
        self, value: Any
    ) -> None:
        with pytest.raises(TypeError):
            ObjectRef(kind=value, digest="sha256:" + "ab" * 32)
        with pytest.raises(TypeError):
            ObjectRef(kind="graph", digest=value)

    def test_from_dict_refuses_a_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            ObjectRef.from_dict("not a mapping")  # type: ignore[arg-type]

    def test_from_dict_requires_kind_and_digest(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            ObjectRef.from_dict({"digest": "sha256:" + "ab" * 32})
        with pytest.raises(ValueError, match="digest"):
            ObjectRef.from_dict({"kind": "graph"})

    @pytest.mark.parametrize("bad_size", [-1, "big", None, 1.5, True])
    def test_a_malformed_size_degrades_to_unknown_rather_than_aborting(
        self, bad_size: Any
    ) -> None:
        # `size` is informational -- no decision reads it -- so it degrades,
        # unlike `kind`/`digest`.
        loaded = ObjectRef.from_dict(
            {"kind": "graph", "digest": "sha256:" + "ab" * 32, "size": bad_size}
        )
        assert loaded.size == 0

    @pytest.mark.parametrize(
        "digest",
        [
            "garbage",
            "sha256:not-hex-at-all-zz",
            "sha256:",
            ":deadbeef",
            "sha256:a",
            "sha256:" + "ab" * 32 + "cd",  # right algorithm, wrong length
            "notreal:" + "ab" * 32,  # unknown algorithm
            "shake_128:" + "ab" * 16,  # a real algorithm with no fixed size
            "SHA256:" + "ab" * 32,  # a real hashlib alias, not the canonical spelling
            "sha-256:" + "ab" * 32,  # another alias hashlib accepts
        ],
    )
    def test_a_malformed_digest_is_refused_at_construction(self, digest: str) -> None:
        # Not just at write time via `object_relpath` -- a reference that
        # can never address an object shouldn't be constructible at all.
        with pytest.raises(ValueError):
            ObjectRef(kind="graph", digest=digest)
        with pytest.raises(ValueError):
            ObjectRef.from_dict({"kind": "graph", "digest": digest})

    def test_an_algorithm_semantic_digest_can_actually_produce_is_accepted(
        self,
    ) -> None:
        # `sha3_256` contains an underscore, which a bare `isalnum()` check
        # would have rejected -- a real `semantic_digest(..., algorithm=
        # "sha3_256")` output must still be usable as a reference.
        digest = semantic_digest({"x": 1}, algorithm="sha3_256")
        assert digest.startswith("sha3_256:")
        ref = ObjectRef(kind="graph", digest=digest)
        assert ref.digest == digest

    def test_an_alias_that_resolves_but_is_never_written_is_refused(self) -> None:
        # hashlib accepts "SHA256" and normalizes it to "sha256", but
        # semantic_digest() only ever *writes* "sha256" -- an ObjectRef built
        # from the alias would compute a different objects/ path than the
        # one InMemoryObjectStore.put() actually used, so it could never
        # resolve through the store.
        with pytest.raises(ValueError, match="canonical spelling"):
            ObjectRef(kind="graph", digest="SHA256:" + "ab" * 32)

    def test_size_alone_does_not_change_identity(self) -> None:
        a = ObjectRef(kind="graph", digest="sha256:" + "ab" * 32, size=1)
        b = ObjectRef(kind="graph", digest="sha256:" + "ab" * 32, size=2)
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
            "sha256:" + "ab" * 32 + "cd",  # right algorithm, wrong length
            "sha256:" + "ab" * 16,  # right algorithm, half the real length
            "notreal:" + "ab" * 32,  # not a real hashlib algorithm
            "shake_128:" + "ab" * 16,  # real algorithm, no fixed digest size
            "sha256:" + ("AB" * 32),  # uppercase hex is not this format's spelling
            "SHA256:" + "ab" * 32,  # a real alias, not the canonical spelling
            "sha-256:" + "ab" * 32,  # another alias hashlib.new() accepts
        ],
    )
    def test_object_relpath_refuses_a_malformed_digest(self, digest: str) -> None:
        with pytest.raises(ValueError):
            object_relpath(digest)

    def test_object_relpath_refuses_a_non_string(self) -> None:
        with pytest.raises(TypeError):
            object_relpath(1)  # type: ignore[arg-type]

    def test_object_relpath_accepts_every_algorithm_semantic_digest_can_produce(
        self,
    ) -> None:
        # `algorithm.isalnum()` would have rejected `sha3_256` (it contains
        # an underscore) even though `semantic_digest` can genuinely produce
        # it -- the two must agree on what a real digest looks like.
        for algorithm in ("sha256", "sha3_256", "sha512", "md5", "blake2b"):
            digest = semantic_digest({"x": 1}, algorithm=algorithm)
            _, _, hexdigest = digest.partition(":")
            assert object_relpath(digest) == (
                f"objects/{algorithm}/{hexdigest[:2]}/{hexdigest}.json"
            )

    @pytest.mark.parametrize(
        "algorithm",
        sorted(hashlib.algorithms_available - hashlib.algorithms_guaranteed),
    )
    def test_object_relpath_refuses_an_available_but_unguaranteed_algorithm(
        self, algorithm: str
    ) -> None:
        """`hashlib.algorithms_available` is whatever this host's OpenSSL
        happens to expose (`sm3`/`ripemd160`/`sha512_224`/`sha512_256` on a
        typical Linux build) -- real, fixed-size algorithms that a naive
        `hashlib.new(algorithm)` check would accept, but not guaranteed
        present on every platform Python runs on. A package written where
        one is available must not fail to even load on a platform where it
        isn't (Codex review).
        """
        try:
            digest = (
                hashlib.new(algorithm).name
                + ":"
                + "ab" * hashlib.new(algorithm).digest_size
            )
        except ValueError:
            pytest.skip(f"{algorithm} not constructible on this host")
        if hashlib.new(algorithm).digest_size == 0:
            pytest.skip(f"{algorithm} has no fixed digest size")
        with pytest.raises(ValueError, match="algorithms_guaranteed"):
            object_relpath(digest)

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

    @pytest.mark.parametrize(
        "bad_id",
        [
            "CON",
            "con",
            "con.json",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "com9",
            "LPT1",
            "a:b",
            "a?b",
            "a*b",
            'a"b',
            "a<b",
            "a>b",
            "a|b",
            "trailing.",
            "trailing ",
            "control\x01char",
        ],
    )
    def test_a_ref_id_must_be_portable_to_windows(self, bad_id: str) -> None:
        # Real IDs a Windows filesystem cannot create, or would silently
        # reinterpret -- a manifest containing one would validate here and
        # then be unwritable, or written wrong, once a real D6 writer tries
        # to place it.
        with pytest.raises(ValueError):
            variant_ref_relpath(bad_id)
        with pytest.raises(ValueError):
            artifact_ref_relpath(bad_id)

    def test_a_windows_reserved_name_is_refused_regardless_of_extension(
        self,
    ) -> None:
        # The reserved-name check is on the stem, not the literal string --
        # "CON" and "CON.json" are refused for the identical reason.
        with pytest.raises(ValueError):
            variant_ref_relpath("CON.json")

    @pytest.mark.parametrize("bad_id", ["COM¹", "com¹", "COM².json", "LPT³"])
    def test_a_superscript_digit_device_name_is_refused(self, bad_id: str) -> None:
        # A real, documented Windows bypass: "COM¹" (with the single-
        # character Unicode superscript digit) renders identically to
        # "COM1" and was blocked as a reserved name for the same reason.
        with pytest.raises(ValueError):
            variant_ref_relpath(bad_id)
        with pytest.raises(ValueError):
            artifact_ref_relpath(bad_id)

    def test_an_ordinary_superscript_digit_id_is_not_refused(self) -> None:
        # The restriction is specifically the reserved device-name stems --
        # an id that merely contains a superscript digit elsewhere is fine.
        assert variant_ref_relpath("x²") == "refs/variants/x².json"

    def test_an_id_at_the_component_limit_is_accepted(self) -> None:
        # 250 bytes + len(".json") == 255, the common filesystem limit --
        # the boundary itself must still work.
        at_limit = "a" * 250
        assert variant_ref_relpath(at_limit) == f"refs/variants/{at_limit}.json"

    def test_an_id_over_the_component_limit_is_refused(self) -> None:
        with pytest.raises(ValueError):
            variant_ref_relpath("a" * 251)
        with pytest.raises(ValueError):
            artifact_ref_relpath("a" * 251)

    def test_the_limit_is_measured_in_encoded_bytes_not_characters(self) -> None:
        # Each "é" is two UTF-8 bytes, so 126 of them is 252 bytes -- over
        # the 250-byte id budget even though the character count (126) is
        # not.
        with pytest.raises(ValueError):
            variant_ref_relpath("é" * 126)
        # 125 of them is exactly 250 bytes -- right at the budget.
        assert variant_ref_relpath("é" * 125) == f"refs/variants/{'é' * 125}.json"


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

    @pytest.mark.parametrize("bad_id", ["..", "a/b", "a\\b", "../escape"])
    def test_a_path_unsafe_variant_id_is_refused(self, bad_id: str) -> None:
        # `variant_id` becomes the literal filename `variant_ref_relpath`
        # builds -- a value that helper refuses must be refused here too, or
        # a manifest could accept a variant a writer can never place.
        with pytest.raises(ValueError):
            VariantRef(variant_id=bad_id)

    @pytest.mark.parametrize("bad_id", ["..", "a/b", "a\\b", "../escape"])
    def test_a_path_unsafe_artifact_id_in_membership_is_refused(
        self, bad_id: str
    ) -> None:
        with pytest.raises(ValueError):
            VariantRef(variant_id="v", artifact_ids=(bad_id,))

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
        assert variant.sections == {}
        assert variant.to_dict() == {"variant_id": "v"}

    def test_sections_round_trips(self) -> None:
        """ADR-063 Track C 8B: `VariantRef.sections` mirrors
        `ArtifactRef.sections` -- content that belongs to the whole
        variant (a `BundleFacts`/baseline-set's own composition facts),
        not to any one artifact."""
        obj = ObjectRef(kind="bundle_composition", digest="sha256:" + "22" * 32)
        variant = VariantRef(
            variant_id="v", artifact_ids=("liba",), sections={"bundle_composition": obj}
        )
        assert VariantRef.from_dict(variant.to_dict()) == variant
        assert variant.sections["bundle_composition"] == obj

    def test_sections_is_read_only(self) -> None:
        obj = ObjectRef(kind="bundle_composition", digest="sha256:" + "22" * 32)
        variant = VariantRef(variant_id="v", sections={"bundle_composition": obj})
        with pytest.raises(TypeError):
            variant.sections["other"] = obj  # type: ignore[index]

    def test_a_malformed_sections_value_is_refused(self) -> None:
        with pytest.raises(TypeError):
            VariantRef(variant_id="v", sections={"bundle_composition": "not-a-ref"})


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

    @pytest.mark.parametrize("bad_id", ["..", "a/b", "a\\b", "../escape"])
    def test_a_path_unsafe_artifact_id_is_refused(self, bad_id: str) -> None:
        with pytest.raises(ValueError):
            ArtifactRef(artifact_id=bad_id, variant_id="v", kind="elf")

    @pytest.mark.parametrize("bad_id", ["..", "a/b", "a\\b", "../escape"])
    def test_a_path_unsafe_variant_id_is_refused(self, bad_id: str) -> None:
        with pytest.raises(ValueError):
            ArtifactRef(artifact_id="a", variant_id=bad_id, kind="elf")

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
    _V = VariantRef(variant_id="cpu-gcc", artifact_ids=("libfoo",))
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

    def test_case_only_variant_id_collision_is_refused(self) -> None:
        # Distinct strings, and distinct on a case-sensitive filesystem --
        # but `refs/variants/Foo.json`/`refs/variants/foo.json` are one file
        # on a case-insensitive one (Windows, default macOS), so the second
        # write would silently overwrite the first.
        with pytest.raises(ValueError, match="case-insensitive"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id="Foo"),
                    VariantRef(variant_id="foo"),
                )
            )

    def test_case_only_artifact_id_collision_is_refused(self) -> None:
        with pytest.raises(ValueError, match="case-insensitive"):
            PackageManifest(
                variant_refs=(self._V,),
                artifact_refs=(
                    self._A,
                    ArtifactRef(artifact_id="LIBFOO", variant_id="cpu-gcc", kind="pe"),
                ),
            )

    def test_unicode_normalized_variant_id_collision_is_refused(self) -> None:
        # "é" (one code point, U+00E9) and "e" followed by a combining
        # acute accent (U+0301) render identically and are canonically
        # equivalent text, but compare unequal even under casefold() alone.
        # A normalization-insensitive filesystem (macOS's usual default)
        # treats them as the same path component.
        precomposed = "café"
        decomposed = "café"
        assert precomposed != decomposed  # the premise: distinct Python strings
        with pytest.raises(ValueError, match="normalization-insensitive"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id=precomposed),
                    VariantRef(variant_id=decomposed),
                )
            )

    def test_unicode_normalized_artifact_id_collision_is_refused(self) -> None:
        precomposed = "café"
        decomposed = "café"
        with pytest.raises(ValueError, match="normalization-insensitive"):
            PackageManifest(
                variant_refs=(self._V,),
                artifact_refs=(
                    self._A,
                    ArtifactRef(
                        artifact_id=decomposed, variant_id="cpu-gcc", kind="pe"
                    ),
                    ArtifactRef(
                        artifact_id=precomposed, variant_id="cpu-gcc", kind="pe"
                    ),
                ),
            )

    def test_a_collision_only_visible_after_folding_is_still_refused(self) -> None:
        # Two distinct, already-normalized ids can still fold to
        # differently-normalized strings if the check only normalizes
        # *before* casefold() -- casefold() is not guaranteed to preserve
        # normalization. Both spellings here are already stable under a
        # single NFC/NFD pass on their own, so this exact collision is
        # invisible to a normalize-then-casefold check and only appears
        # once the *casefolded* result is itself renormalized.
        first = "\u0124\u0331"  # H WITH CIRCUMFLEX + COMBINING MACRON BELOW
        second = "\u0125\u0331"  # h WITH CIRCUMFLEX (lowercase) + same combiner
        naive = unicodedata.normalize("NFC", first).casefold()
        other_naive = unicodedata.normalize("NFC", second).casefold()
        assert naive != other_naive  # the premise: one normalize pass misses it
        with pytest.raises(ValueError, match="normalization-insensitive"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id=first),
                    VariantRef(variant_id=second),
                )
            )

    def test_an_artifact_naming_an_undeclared_variant_is_refused(self) -> None:
        with pytest.raises(ValueError, match="undeclared"):
            PackageManifest(artifact_refs=(self._A,))

    def test_a_variant_naming_a_nonexistent_artifact_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not in artifact_refs"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id="cpu-gcc", artifact_ids=("libfoo",)),
                ),
                artifact_refs=(),
            )

    def test_a_variant_naming_an_artifact_owned_by_another_variant_is_refused(
        self,
    ) -> None:
        other = ArtifactRef(artifact_id="libbar", variant_id="cpu-clang", kind="elf")
        with pytest.raises(ValueError, match="own variant_id"):
            PackageManifest(
                variant_refs=(
                    VariantRef(variant_id="cpu-gcc", artifact_ids=("libbar",)),
                    VariantRef(variant_id="cpu-clang"),
                ),
                artifact_refs=(other,),
            )

    def test_consistent_membership_on_both_sides_round_trips(self) -> None:
        variant = VariantRef(variant_id="cpu-gcc", artifact_ids=("libfoo",))
        manifest = PackageManifest(variant_refs=(variant,), artifact_refs=(self._A,))
        assert PackageManifest.from_dict(manifest.to_dict()) == manifest

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

    def test_put_stores_a_raw_binary_payload(self) -> None:
        """`canonical_form`/`semantic_digest` reject a binary buffer outright
        (by design -- it has no JSON-shaped structure to canonicalize), but
        this package's own job description is "stores and retrieves bytes a
        caller already produced" (`AGENTS.md`) -- so a raw extractor
        artifact (a `bytes`/`bytearray`/`memoryview` payload, per D7) must
        still be storable. Before the fix, `put()` routed every payload
        through `strip_capture_metadata`/`canonical_form`, so a raw payload
        raised `TypeError` instead of being stored.
        """
        store = InMemoryObjectStore()
        payload = b"\x00\x01\xffnot json at all"
        digest = store.put(payload)
        assert digest == raw_digest(payload)
        assert store.has(digest)
        assert store.get(digest) == payload

    def test_storing_identical_raw_payload_twice_returns_one_digest(self) -> None:
        store = InMemoryObjectStore()
        first = store.put(b"same raw bytes")
        second = store.put(b"same raw bytes")
        assert first == second
        assert store.get(first) == b"same raw bytes"

    @pytest.mark.parametrize("wrapper", [bytearray, memoryview])
    def test_put_accepts_bytearray_and_memoryview(self, wrapper: Any) -> None:
        store = InMemoryObjectStore()
        payload = wrapper(b"raw bytes")
        digest = store.put(payload)
        assert digest == raw_digest(bytes(payload))
        assert store.get(digest) == b"raw bytes"

    def test_a_raw_payload_reference_resolves_through_the_store(self) -> None:
        """The D7 round-trip guarantee for a raw artifact, mirroring
        `test_a_reference_built_from_a_stored_digest_resolves` below for
        JSON-shaped content.
        """
        store = InMemoryObjectStore()
        payload = b"raw extractor artifact bytes"
        digest = store.put(payload)
        ref = ObjectRef(kind="raw_refs", digest=digest)
        assert store.get(ref.digest) == payload

    def test_a_raw_payload_never_collides_with_an_equivalent_json_value(
        self,
    ) -> None:
        """`b"{}"` and `{}` both encode to the identical bytes `b"{}"` --
        before the domain-separation fix, storing both under one store gave
        them the same digest, and `get()` returned whichever was `put()`
        first regardless of which one a later caller asked for (Codex
        review).
        """
        store = InMemoryObjectStore()
        raw_digest_value = store.put(b"{}")
        json_digest_value = store.put({})
        assert raw_digest_value != json_digest_value
        assert store.get(raw_digest_value) == b"{}"
        assert store.get(json_digest_value) == {}

    @pytest.mark.parametrize(
        "algorithm",
        sorted(hashlib.algorithms_available - hashlib.algorithms_guaranteed),
    )
    def test_put_refuses_an_available_but_unguaranteed_algorithm(
        self, algorithm: str
    ) -> None:
        """`put()` must not mint a digest `ObjectRef` would then refuse --
        before this fix, `put(content, algorithm="sm3")` succeeded while
        `ObjectRef(kind=..., digest=that_digest)` raised, so the store could
        produce an object no reference could ever address (Codex review).
        """
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="algorithms_guaranteed"):
            store.put({"x": 1}, algorithm=algorithm)

    def test_put_honors_a_non_default_algorithm(self) -> None:
        """An `ObjectRef` built with a non-default algorithm (`object_relpath`
        accepts any real, fixed-size hashlib algorithm) must be resolvable
        through the store `put()`s the matching content into -- before the
        fix, `put()` always hashed with the default sha256 regardless of
        what algorithm the caller's own `ObjectRef` used, so such a
        reference could never actually be `has()`/`get()`-resolved.
        """
        store = InMemoryObjectStore()
        content = {"declarations": [1, 2, 3]}
        digest = store.put(content, algorithm="sha3_256")
        assert digest == semantic_digest(content, algorithm="sha3_256")
        ref = ObjectRef(kind="declarations", digest=digest)
        assert store.has(ref.digest)
        assert store.get(ref.digest) == content

    def test_storing_identical_content_twice_returns_one_digest(self) -> None:
        store = InMemoryObjectStore()
        first = store.put({"a": 1, "b": 2})
        second = store.put({"b": 2, "a": 1})  # same content, different order
        assert first == second

    def test_get_returns_the_canonical_form(self) -> None:
        store = InMemoryObjectStore()
        digest = store.put({"b": 2, "a": 1})
        assert store.get(digest) == {"a": 1, "b": 2}

    def test_mutating_a_returned_value_does_not_corrupt_the_store(self) -> None:
        store = InMemoryObjectStore()
        digest = store.put({"declarations": [1, 2, 3]})
        fetched = store.get(digest)
        fetched["declarations"].append(4)  # type: ignore[union-attr]
        fetched["poisoned"] = True
        # The store's own copy is untouched, and a second `get` -- the one a
        # different caller would make -- still sees the original content
        # under the digest it was actually stored with.
        assert store.get(digest) == {"declarations": [1, 2, 3]}
        assert store.has(digest)

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

    def test_put_hashes_and_stores_the_same_normalized_snapshot(self) -> None:
        """`put()` must normalize `content` exactly once, then derive both
        the returned digest and the stored value from that same snapshot --
        not re-traverse the caller's own `content` independently for each.
        A stateful custom `Mapping` (accepted by `canonical_form`) whose
        traversal yields different data each time it's iterated is the
        adversarial case: before the fix, the digest was computed from one
        traversal (this test's first, `{"x": 1}`) while the stored value
        came from a second, independent traversal (`{"x": 2}`), so the
        returned digest identified content that was never actually stored.
        """

        class _StatefulMapping(Mapping):
            # `canonical_form` calls `.items()` exactly once per top-level
            # `canonical_form(value)` invocation for a given Mapping object
            # (it never re-derives `.items()` mid-traversal), so overriding
            # `items()` itself -- rather than `__getitem__`/`__iter__`,
            # which the default `.items()` implementation may probe more
            # than once per call -- gives a call count that lines up
            # exactly with the number of times this object was normalized.
            def __init__(self) -> None:
                self.calls = 0

            def __getitem__(self, key: str) -> int:
                return {"x": 1}[key]

            def __iter__(self) -> Any:
                return iter({"x": 1})

            def __len__(self) -> int:
                return 1

            def items(self) -> Any:
                self.calls += 1
                data = {"x": 1} if self.calls == 1 else {"x": 2}
                return data.items()

        store = InMemoryObjectStore()
        digest = store.put(_StatefulMapping())
        assert digest == semantic_digest(store.get(digest))

    def test_get_never_depends_on_which_capture_variant_was_put_first(
        self,
    ) -> None:
        """Two values differing only in the reserved `capture` block share a
        digest (D3: capture metadata is provenance, not identity) -- so the
        digest alone must determine what `get()` returns, regardless of
        insertion order. Before the fix, whichever variant's `put()` ran
        first silently won, and re-`put`ting the other variant did nothing
        (its digest was already present) -- the address stopped uniquely
        determining the observable stored object.
        """
        store = InMemoryObjectStore()
        first = {"x": 1, "capture": {"timestamp": "A"}}
        second = {"x": 1, "capture": {"timestamp": "B"}}
        assert semantic_digest(first) == semantic_digest(second)

        digest_first = store.put(first)
        digest_second = store.put(second)
        assert digest_first == digest_second
        assert store.get(digest_first) == strip_capture_metadata(first)
        assert store.get(digest_first) == strip_capture_metadata(second)
        assert "capture" not in store.get(digest_first)

        # Order reversed: same outcome either way.
        other_store = InMemoryObjectStore()
        other_store.put(second)
        other_store.put(first)
        assert other_store.get(digest_first) == strip_capture_metadata(first)

    def test_a_reference_built_from_a_stored_digest_resolves(self) -> None:
        """The whole point of D7: an `ObjectRef` and a store must agree."""
        store = InMemoryObjectStore()
        digest = store.put({"kind": "graph", "nodes": []})
        ref = ObjectRef(kind="graph", digest=digest)
        assert store.get(ref.digest) == {"kind": "graph", "nodes": []}
