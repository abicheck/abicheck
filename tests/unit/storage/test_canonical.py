# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D5's canonical encoding, and D2's version axes.

The digest invariant, stated once: `semantic_digest` is invariant under
mapping key order, set iteration order, and pretty-printing, and is *not*
invariant under sequence order. Everything here tests one half of that.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import (
    VOLATILE_KEYS,
    canonical_form,
    canonical_json,
    semantic_digest,
    strip_volatile,
)
from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    ProducerIdentity,
    StorageVersions,
    check_reader_compatibility,
)

_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.text(max_size=16),
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=12,
)


class TestDigestIgnoresIncidentalOrder:
    @given(_json_values)
    def test_mapping_key_order_never_changes_a_digest(self, value: object) -> None:
        shuffled = json.loads(json.dumps(value, sort_keys=True))

        assert semantic_digest(value) == semantic_digest(shuffled)

    def test_a_mapping_built_in_two_orders_hashes_the_same(self) -> None:
        forward = {"a": 1, "b": 2, "c": 3}
        backward = {"c": 3, "b": 2, "a": 1}

        assert semantic_digest(forward) == semantic_digest(backward)

    def test_set_iteration_order_never_changes_a_digest(self) -> None:
        assert semantic_digest({"tags": {"x", "y", "z"}}) == semantic_digest(
            {"tags": {"z", "y", "x"}}
        )

    def test_a_set_of_mixed_types_still_orders_deterministically(self) -> None:
        """Sorting by canonical text, not by value.

        A direct `sorted()` raises on a set holding both ints and strings —
        which a real facts payload can easily produce.
        """
        digest = semantic_digest({"mixed": {1, "1", True}})

        assert semantic_digest({"mixed": {"1", True, 1}}) == digest

    def test_equal_sets_hash_equally_even_across_the_bool_int_collapse(self) -> None:
        """`{1} == {True}` in Python, so their digests must agree too.

        Which spelling survives set construction depends only on insertion
        order, so emitting `true` for one and `1` for the other would
        reintroduce an incidental-order dependence — just hidden inside
        `set.__hash__` rather than in a producer's traversal. The
        distinction is unrecoverable here by construction; agreement is the
        only available answer.
        """
        assert {1} == {True}
        assert semantic_digest({1}) == semantic_digest({True})
        # Outside a set, the distinction is real and is preserved.
        assert semantic_digest([1]) != semantic_digest([True])

    @given(st.integers(min_value=0, max_value=4))
    def test_pretty_printing_never_changes_a_digest(self, indent: int) -> None:
        value = {"b": [1, 2], "a": {"nested": True}}

        canonical_json(value, indent=indent or None)

        assert semantic_digest(value) == semantic_digest(
            json.loads(canonical_json(value, indent=indent or None))
        )


class TestDigestRespectsRealOrder:
    def test_sequence_order_does_change_a_digest(self) -> None:
        """A sequence is the shape that *means* order is significant.

        This is the other half of the `BundleFacts` lesson: template
        arguments must be an array of explicit entries, so that sorting
        mappings elsewhere is safe.
        """
        assert semantic_digest({"args": [1, 2]}) != semantic_digest({"args": [2, 1]})

    def test_explicit_ordered_entries_survive_canonicalization(self) -> None:
        template_args = [
            {"parameter": "Precision", "value": "double"},
            {"parameter": "Method", "value": "defaultDense"},
        ]

        assert canonical_form(template_args) == template_args
        assert semantic_digest(template_args) != semantic_digest(
            list(reversed(template_args))
        )

    def test_the_insertion_ordered_mapping_antipattern_is_lossy(self) -> None:
        """Why the array shape is required, demonstrated rather than asserted.

        A mapping that encodes argument order by insertion collapses to one
        digest under canonicalization — so a format relying on it cannot tell
        `<double, defaultDense>` from `<defaultDense, double>`.
        """
        as_map = {"Precision": "double", "Method": "defaultDense"}
        reordered_map = {"Method": "defaultDense", "Precision": "double"}

        assert semantic_digest(as_map) == semantic_digest(reordered_map)


class TestVolatileMetadata:
    @given(st.sampled_from(sorted(VOLATILE_KEYS)))
    def test_a_volatile_key_never_reaches_the_digest(self, key: str) -> None:
        assert semantic_digest({"facts": [1], key: "a"}) == semantic_digest(
            {"facts": [1], key: "b"}
        )

    def test_volatile_keys_are_stripped_at_any_depth(self) -> None:
        nested = {"outer": {"inner": {"created_at": "now", "real": 1}}}

        assert strip_volatile(nested) == {"outer": {"inner": {"real": 1}}}

    def test_a_content_field_that_merely_looks_temporal_is_kept(self) -> None:
        """Exact key names, not a suffix heuristic.

        "Anything ending in `_at`" would swallow a real fact such as
        `deprecated_at`, and a digest that quietly ignores content is far
        worse than one that includes an extra timestamp.
        """
        assert "deprecated_at" in strip_volatile({"deprecated_at": "1.2"})
        assert semantic_digest({"deprecated_at": "1.2"}) != semantic_digest(
            {"deprecated_at": "1.3"}
        )

    def test_canonical_json_can_keep_volatile_keys_for_storage(self) -> None:
        """Excluded from *hashing*, not from the stored document."""
        payload = canonical_json({"created_at": "now", "x": 1}, drop_volatile=False)

        assert "created_at" in payload


class TestNumberNormalization:
    @pytest.mark.parametrize(
        ("left", "right"),
        [(0.0, -0.0), (2.0, 2), (1e3, 1000)],
    )
    def test_equal_numbers_encode_identically(self, left: float, right: float) -> None:
        assert semantic_digest(left) == semantic_digest(right)

    def test_a_genuinely_fractional_value_is_preserved(self) -> None:
        assert canonical_form(1.5) == 1.5
        assert semantic_digest(1.5) != semantic_digest(1.25)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_are_refused(self, value: float) -> None:
        """`json` emits these as bare literals that are not valid JSON.

        A document containing one is unreadable by a conforming parser, so it
        must not be written at all — failing at the boundary beats writing a
        package no other tool can read.
        """
        with pytest.raises(ValueError, match="non-finite"):
            canonical_form(value)


class TestUnsupportedTypesAreRefused:
    def test_an_arbitrary_object_is_a_type_error(self) -> None:
        """No `str()` fallback.

        A silent coercion would let an object whose `repr` embeds a memory
        address into the hash domain, so the digest of identical content
        would differ run to run — the one thing a content-addressed store
        cannot tolerate.
        """

        class Opaque:
            pass

        with pytest.raises(TypeError, match="no canonical storage form"):
            canonical_form({"x": Opaque()})

    def test_bytes_are_refused_rather_than_guessed_at(self) -> None:
        """`bytes` is a `Sequence`, so it needs an explicit guard.

        Without one it falls through to the sequence branch and encodes as a
        list of integers — a silent, lossy reinterpretation rather than an
        error, and one that would then hash as if it were content.
        """
        with pytest.raises(TypeError, match="bytes have no canonical storage form"):
            canonical_form(b"raw")

        with pytest.raises(TypeError):
            canonical_form({"payload": bytearray(b"raw")})


class TestCanonicalFormBasics:
    @given(_json_values)
    def test_canonical_form_is_idempotent(self, value: object) -> None:
        once = canonical_form(value)

        assert canonical_form(once) == once

    @given(_json_values)
    def test_canonical_json_parses_back_to_canonical_form(self, value: object) -> None:
        assert json.loads(canonical_json(value)) == canonical_form(value)

    def test_a_digest_names_its_algorithm(self) -> None:
        """So a stored digest never leaves a reader to assume sha256."""
        assert semantic_digest({"a": 1}).startswith("sha256:")
        assert semantic_digest({"a": 1}, algorithm="sha512").startswith("sha512:")

    def test_non_string_mapping_keys_are_normalized(self) -> None:
        assert canonical_form({1: "a", "1": "b"}) in (
            {"1": "a"},
            {"1": "b"},
        )


class TestVersionAxes:
    def test_the_axes_are_independent_fields(self) -> None:
        versions = StorageVersions(
            normalization_recipe="norm-v2",
            extractor_generation=3,
            resolver_generation=4,
            producer=ProducerIdentity(name="clang", version="18.1.0"),
        )

        payload = versions.to_dict()

        assert payload["normalization_recipe"] == "norm-v2"
        assert payload["extractor_generation"] == 3
        assert payload["resolver_generation"] == 4
        assert payload["producer"]["name"] == "clang"

    def test_import_provenance_is_preserved(self) -> None:
        """What forces one special case per historical producer defect.

        Recording what a legacy snapshot *was* lets a migration answer
        "which producer epoch emitted this" without inferring it from which
        fields happen to be present.
        """
        versions = StorageVersions(
            source_schema_version=25, source_producer_generation="castxml-0.6.11"
        )

        restored = StorageVersions.from_dict(versions.to_dict())

        assert restored.source_schema_version == 25
        assert restored.source_producer_generation == "castxml-0.6.11"

    def test_sections_version_independently(self) -> None:
        versions = StorageVersions(
            section_schema_versions={"graph": 2, "binary": 1},
        )

        assert StorageVersions.from_dict(
            versions.to_dict()
        ).section_schema_versions == {"binary": 1, "graph": 2}

    @given(st.permutations(["graph", "binary", "types"]))
    def test_section_versions_serialize_in_a_stable_order(
        self, names: list[str]
    ) -> None:
        versions = StorageVersions(section_schema_versions={name: 1 for name in names})
        reference = StorageVersions(
            section_schema_versions={"binary": 1, "graph": 1, "types": 1}
        )

        assert versions.to_dict() == reference.to_dict()

    def test_round_trip_preserves_every_axis(self) -> None:
        versions = StorageVersions(
            section_schema_versions={"graph": 2},
            normalization_recipe="norm-v2",
            producer=ProducerIdentity("castxml", "0.7.0", "sha256:abc"),
            extractor_generation=1,
            resolver_generation=2,
            source_schema_version=25,
            source_producer_generation="gen-a",
        )

        assert StorageVersions.from_dict(versions.to_dict()) == versions


class TestReaderCompatibility:
    def test_a_newer_package_format_fails_closed(self) -> None:
        result = check_reader_compatibility(
            StorageVersions(package_format_version=PACKAGE_FORMAT_VERSION + 1)
        )

        assert not result.readable
        assert "newer than" in result.reason

    def test_a_newer_comparison_contract_fails_closed(self) -> None:
        """The axis where reading on could produce a wrong verdict."""
        result = check_reader_compatibility(
            StorageVersions(comparison_contract_version=COMPARISON_CONTRACT_VERSION + 1)
        )

        assert not result.readable
        assert "wrong verdict" in result.reason

    @pytest.mark.parametrize("delta", [-1, 1])
    def test_an_extractor_or_resolver_difference_does_not_fail_closed(
        self, delta: int
    ) -> None:
        """A stored baseline stays readable across a resolver correction.

        Provider selection, alias normalization, symbol-version handling and
        reachability have each been corrected over time. Refusing the
        baseline would strand it; silently reinterpreting it would hide that
        a derived graph is no longer the producer's own answer.
        """
        result = check_reader_compatibility(
            StorageVersions(resolver_generation=2),
            reader_resolver_generation=2 + delta,
        )

        assert result.readable
        assert result.semantics_differ
        assert "resolver" in result.reason

    def test_matching_generations_report_no_drift(self) -> None:
        result = check_reader_compatibility(
            StorageVersions(extractor_generation=1, resolver_generation=2),
            reader_extractor_generation=1,
            reader_resolver_generation=2,
        )

        assert result.readable
        assert not result.semantics_differ

    def test_generations_are_not_checked_when_the_reader_states_none(self) -> None:
        """A caller that does not care must not be forced to declare."""
        result = check_reader_compatibility(StorageVersions(resolver_generation=9))

        assert result.readable
        assert not result.semantics_differ

    def test_both_generations_drifting_are_named_together(self) -> None:
        result = check_reader_compatibility(
            StorageVersions(extractor_generation=1, resolver_generation=1),
            reader_extractor_generation=2,
            reader_resolver_generation=2,
        )

        assert "extractor/resolver" in result.reason

    def test_a_format_refusal_outranks_a_semantics_difference(self) -> None:
        """Unreadable is a stronger answer than "readable but different"."""
        result = check_reader_compatibility(
            StorageVersions(
                package_format_version=PACKAGE_FORMAT_VERSION + 1,
                resolver_generation=1,
            ),
            reader_resolver_generation=2,
        )

        assert not result.readable


class TestVolatileKeysAreUnambiguous:
    """Codex review: `host` was excluded and is not unambiguously volatile.

    In this codebase `host` is as likely to name real platform or
    frontend-context content as a hostname, so excluding it collapsed
    genuinely different content to one digest — the exact failure
    `VOLATILE_KEYS`' own comment warns about.
    """

    def test_host_is_content_and_reaches_the_digest(self) -> None:
        assert "host" not in VOLATILE_KEYS
        assert semantic_digest({"host": "linux"}) != semantic_digest(
            {"host": "windows"}
        )
        assert semantic_digest({"host": "linux"}) != semantic_digest({})

    def test_hostname_remains_volatile(self) -> None:
        """The unambiguous spelling keeps its exclusion."""
        assert "hostname" in VOLATILE_KEYS
        assert semantic_digest({"hostname": "runner-1"}) == semantic_digest(
            {"hostname": "runner-2"}
        )

    @pytest.mark.parametrize("key", sorted(VOLATILE_KEYS))
    def test_no_excluded_key_has_a_plausible_content_reading(self, key: str) -> None:
        """A guard on the bar itself, not just on today's list.

        Every excluded name must be one no reasonable payload would use for
        content. These stems are the ones that carry a real ABI meaning
        elsewhere in this codebase, so a future addition containing one
        should be argued for explicitly rather than added to the set.
        """
        ambiguous_stems = ("host", "target", "arch", "platform", "version", "path")
        assert not any(
            key == stem or key.startswith(stem + "_") for stem in ambiguous_stems
        )
