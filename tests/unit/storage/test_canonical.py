# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D5's canonical encoding, and D2's version axes.

The digest invariant, stated once: `semantic_digest` is invariant under
mapping key order, set iteration order, and pretty-printing, and is *not*
invariant under sequence order. Everything here tests one half of that.
"""

from __future__ import annotations

import json
import os

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import (
    CAPTURE_METADATA_KEY,
    canonical_form,
    canonical_json,
    semantic_digest,
    strip_capture_metadata,
)
from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    UNSTATED_VERSION,
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


class TestCaptureMetadata:
    """One reserved slot at the root, not a set of names at any depth."""

    def test_the_reserved_root_slot_is_excluded_from_the_digest(self) -> None:
        assert semantic_digest(
            {"facts": [1], CAPTURE_METADATA_KEY: {"hostname": "runner-1"}}
        ) == semantic_digest(
            {"facts": [1], CAPTURE_METADATA_KEY: {"hostname": "runner-2"}}
        )

    def test_it_is_excluded_only_at_the_root(self) -> None:
        """Position, not spelling, is what makes the exclusion sound."""
        nested_a = {"entities": {CAPTURE_METADATA_KEY: {"type": "int"}}}
        nested_b = {"entities": {}}

        assert semantic_digest(nested_a) != semantic_digest(nested_b)

    def test_strip_removes_only_the_root_slot(self) -> None:
        payload = {
            CAPTURE_METADATA_KEY: {"pid": 1},
            "entities": {CAPTURE_METADATA_KEY: {"real": 1}},
        }

        assert strip_capture_metadata(payload) == {
            "entities": {CAPTURE_METADATA_KEY: {"real": 1}}
        }

    def test_a_non_mapping_root_is_returned_unchanged(self) -> None:
        assert strip_capture_metadata([1, 2]) == [1, 2]

    def test_the_stored_document_keeps_its_capture_metadata(self) -> None:
        """Excluded from *hashing*, not from what is written."""
        payload = {CAPTURE_METADATA_KEY: {"hostname": "h"}, "x": 1}

        assert CAPTURE_METADATA_KEY in canonical_json(payload)
        assert CAPTURE_METADATA_KEY not in canonical_json(
            payload, drop_capture_metadata=True
        )


class TestNoContentKeyIsStrippedByName:
    """Codex review, twice. The name-based strip was the wrong mechanism.

    `host` was removed after it collapsed `{"host": "linux"}`,
    `{"host": "windows"}` and `{}` to one digest. Removing that one name did
    not fix the class — the next round found `pid` (an entirely ordinary C
    struct field) and `working_directory` (a real build input) doing the same
    thing. Each fix drew the next instance, which is the signal to change the
    mechanism rather than keep editing the list.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "host",
            "hostname",
            "pid",
            "created_at",
            "captured_at",
            "generated_at",
            "duration_seconds",
            "elapsed_seconds",
            "tmpdir",
            "scratch_dir",
            "working_directory",
            "wall_clock_seconds",
        ],
    )
    def test_every_previously_stripped_name_is_now_content(self, name: str) -> None:
        assert semantic_digest({name: "a"}) != semantic_digest({name: "b"})
        assert semantic_digest({name: "a"}) != semantic_digest({})

    def test_the_reported_pid_entity_survives(self) -> None:
        """The literal counterexample from review."""
        assert semantic_digest(
            {"entities": {"pid": {"type": "int"}}}
        ) != semantic_digest({"entities": {}})

    def test_a_working_directory_build_input_survives(self) -> None:
        assert semantic_digest(
            {"build": {"working_directory": "/a"}}
        ) != semantic_digest({"build": {"working_directory": "/b"}})


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

    def test_non_string_mapping_keys_are_refused(self) -> None:
        """Codex review. The previous version of this test pinned the bug.

        It asserted the result was *one of* `{"1": "a"}` / `{"1": "b"}` —
        which reads as a deliberate normalization choice but was really a
        silent loss: `{1: "a", "1": "b"}` has two entries and the digest
        matched a document that only ever had one. A test written to accept
        whichever value survived made a real gap look settled, which is the
        same trap the root `AGENTS.md` records for the forced-include work.
        """
        with pytest.raises(TypeError, match="not str"):
            canonical_form({1: "a", "1": "b"})

    def test_a_key_collision_cannot_silently_drop_an_entry(self) -> None:
        with pytest.raises(TypeError):
            canonical_form({1: "a"})

    def test_unorderable_values_under_colliding_keys_do_not_crash_sorting(
        self,
    ) -> None:
        """Sorting pairs fell through to comparing values when keys tied.

        `{1: {}, "1": []}` raised `TypeError: '<' not supported between
        instances of 'list' and 'dict'` from inside a digest call. Sorting by
        key alone makes value orderability irrelevant; the non-string keys are
        refused first, and the error names the key rather than the comparison.
        """
        with pytest.raises(TypeError, match="not str"):
            canonical_form({1: {}, "1": []})

    def test_string_keyed_mappings_with_unorderable_values_are_fine(self) -> None:
        assert canonical_form({"b": [], "a": {}}) == {"a": {}, "b": []}


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


class TestUnstatedComparisonContractFailsClosed:
    """Codex review: absence was synthesized as the reader's own version.

    `from_dict` defaulted the missing key to `COMPARISON_CONTRACT_VERSION`, so
    a malformed or pre-versioned package claimed to share this build's
    comparison semantics and `check_reader_compatibility` reported it
    readable — bypassing the one axis that exists to fail closed exactly when
    those semantics are unknown.
    """

    def test_a_package_omitting_the_axis_is_not_readable(self) -> None:
        # A valid package format is supplied so this isolates the comparison
        # contract axis; both axes now refuse absence, and checking them one
        # at a time keeps each test's subject unambiguous.
        versions = StorageVersions.from_dict(
            {"package_format_version": PACKAGE_FORMAT_VERSION}
        )

        assert versions.comparison_contract_version == UNSTATED_VERSION

        result = check_reader_compatibility(versions)

        assert not result.readable
        assert "usable comparison contract version" in result.reason

    def test_parsing_stays_defensive(self) -> None:
        """The refusal belongs at the decision point, not at the parse.

        This repo's convention is that a hand-edited or newer package never
        aborts a load; it is refused when a decision is actually made from it.
        """
        assert StorageVersions.from_dict({}) is not None
        assert StorageVersions.from_dict({"producer": {}}) is not None

    def test_a_stated_current_version_is_readable(self) -> None:
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
            }
        )

        assert check_reader_compatibility(versions).readable

    def test_a_round_tripped_package_always_states_the_axis(self) -> None:
        """A package this build writes can always be read back."""
        payload = StorageVersions().to_dict()

        assert "comparison_contract_version" in payload
        assert check_reader_compatibility(StorageVersions.from_dict(payload)).readable


class TestExactlyTwoAxesFailClosed:
    """Codex review flagged the docs and the code disagreeing here.

    ADR-062 D2 and plan A0.5 said only `comparison_contract_version` fails
    closed, while the implementation also refused a newer
    `package_format_version`. The prose was the wrong half — a reader that
    cannot locate a newer container's structures must refuse rather than
    misparse — so the documents were corrected to match. This test pins the
    resulting contract so the two cannot drift apart silently again.
    """

    def test_the_two_closing_axes_refuse(self) -> None:
        for versions in (
            StorageVersions(package_format_version=PACKAGE_FORMAT_VERSION + 1),
            StorageVersions(
                comparison_contract_version=COMPARISON_CONTRACT_VERSION + 1
            ),
        ):
            assert not check_reader_compatibility(versions).readable

    @pytest.mark.parametrize(
        "versions",
        [
            StorageVersions(section_schema_versions={"graph": 99}),
            StorageVersions(normalization_recipe="norm-v99"),
            StorageVersions(producer=ProducerIdentity("unknown-tool", "99")),
            StorageVersions(extractor_generation=99),
            StorageVersions(resolver_generation=99),
            StorageVersions(source_schema_version=99),
            StorageVersions(source_producer_generation="gen-99"),
        ],
    )
    def test_the_other_five_axes_stay_informational(
        self, versions: StorageVersions
    ) -> None:
        """A reader that does not recognize these must still read the package.

        This is what lets an optional display field ship without implying a
        new evidence recipe.
        """
        assert check_reader_compatibility(versions).readable


class TestMalformedVersionsFailClosed:
    """Codex review: `int()` let malformed values through as usable.

    A fail-closed axis must never fail *open*, and two shapes did: a package
    stating `1.5` became `1` and read as this build's own supported version,
    and `-1` survived intact — neither equal to `UNSTATED_VERSION` nor greater
    than the supported version, so it passed both guards.
    """

    @pytest.mark.parametrize("raw", [1.5, -1, 0, 0.9, "1", "x", None, True, [1]])
    def test_no_malformed_value_is_ever_treated_as_usable(self, raw: object) -> None:
        versions = StorageVersions.from_dict({"comparison_contract_version": raw})

        assert versions.comparison_contract_version == UNSTATED_VERSION
        assert not check_reader_compatibility(versions).readable

    def test_a_fractional_version_does_not_truncate_into_the_supported_one(
        self,
    ) -> None:
        """The literal counterexample: `1.5` must not read as v1."""
        versions = StorageVersions.from_dict({"comparison_contract_version": 1.5})

        assert versions.comparison_contract_version != COMPARISON_CONTRACT_VERSION
        assert not check_reader_compatibility(versions).readable

    def test_an_integral_float_is_accepted(self) -> None:
        """JSON has one number type, so a valid version can arrive as 1.0."""
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": float(COMPARISON_CONTRACT_VERSION),
            }
        )

        assert versions.comparison_contract_version == COMPARISON_CONTRACT_VERSION
        assert check_reader_compatibility(versions).readable

    @pytest.mark.parametrize("raw", [1.5, -1, 0, "x"])
    def test_the_package_format_axis_is_validated_the_same_way(
        self, raw: object
    ) -> None:
        """Both fail-closed axes must treat "not validly stated" alike.

        Validating one and not the other would leave the same hole in the
        axis whose job is to say whether this reader can locate the
        package's structures at all.
        """
        versions = StorageVersions.from_dict({"package_format_version": raw})

        assert not check_reader_compatibility(versions).readable

    def test_bool_is_not_a_version(self) -> None:
        """`True` is an `int` in Python; it is not a version number."""
        versions = StorageVersions.from_dict({"comparison_contract_version": True})

        assert versions.comparison_contract_version == UNSTATED_VERSION


class TestAnOmittedPackageFormatIsUnstated:
    """Codex review: the two fail-closed axes disagreed on *absence*.

    An earlier round validated both axes against malformed values but left
    `package_format_version` defaulting to this reader's own, so a package
    stating a valid contract version while omitting its format version was
    interpreted as the current container layout and read. That was an
    incompletely applied principle, not a deliberate exception.
    """

    def test_a_valid_contract_version_does_not_excuse_an_omitted_format(
        self,
    ) -> None:
        versions = StorageVersions.from_dict(
            {"comparison_contract_version": COMPARISON_CONTRACT_VERSION}
        )

        assert versions.package_format_version == UNSTATED_VERSION

        result = check_reader_compatibility(versions)

        assert not result.readable
        assert "usable package format version" in result.reason

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {"comparison_contract_version": COMPARISON_CONTRACT_VERSION},
                id="package-format-absent",
            ),
            pytest.param(
                {"package_format_version": PACKAGE_FORMAT_VERSION},
                id="comparison-contract-absent",
            ),
        ],
    )
    def test_both_fail_closed_axes_treat_absence_identically(
        self, payload: dict[str, int]
    ) -> None:
        assert not check_reader_compatibility(
            StorageVersions.from_dict(payload)
        ).readable

    def test_a_package_stating_both_is_readable(self) -> None:
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
            }
        )

        assert check_reader_compatibility(versions).readable

    def test_what_this_build_writes_is_always_readable_back(self) -> None:
        """The refusal must never strand our own packages."""
        assert check_reader_compatibility(
            StorageVersions.from_dict(StorageVersions().to_dict())
        ).readable


class TestInformationalAxesParseDefensively:
    """CodeRabbit review: malformed *informational* fields aborted the load.

    This repo's convention is that a hand-edited or newer package never aborts
    a load and the refusal belongs at the decision point. Bare `int()`/`dict()`
    broke that in four ways — and for fields no decision even reads, so a
    package whose real evidence was intact became unloadable over a typo in a
    field that changes nothing.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            {"extractor_generation": "x"},
            {"extractor_generation": None},
            {"resolver_generation": [1]},
            {"resolver_generation": 1.5},
            {"source_schema_version": "old"},
            {"section_schema_versions": 5},
            {"section_schema_versions": "graph"},
            {"section_schema_versions": {"graph": "bad"}},
            {"producer": "clang"},
            {"producer": None},
            {"normalization_recipe": 7},
        ],
    )
    def test_no_malformed_informational_field_aborts_a_load(
        self, payload: dict[str, object]
    ) -> None:
        versions = StorageVersions.from_dict(payload)

        assert isinstance(versions, StorageVersions)

    def test_a_malformed_informational_field_does_not_make_a_package_unreadable(
        self,
    ) -> None:
        """It is informational: it must not affect the fail-closed decision."""
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
                "extractor_generation": "nonsense",
                "producer": "not-an-object",
            }
        )

        assert check_reader_compatibility(versions).readable

    def test_valid_informational_values_still_round_trip(self) -> None:
        """The tolerance must not quietly discard good data."""
        versions = StorageVersions(
            section_schema_versions={"graph": 2, "binary": 1},
            normalization_recipe="norm-v2",
            producer=ProducerIdentity("castxml", "0.7.0", "sha256:abc"),
            extractor_generation=1,
            resolver_generation=2,
            source_schema_version=25,
        )

        assert StorageVersions.from_dict(versions.to_dict()) == versions


class TestTheDecisionPointIsSafeOnItsOwn:
    """Codex review: `from_dict` sanitized, the guard itself did not.

    `StorageVersions` is public and constructible directly, so a loader or
    migration adapter that builds one without `from_dict` could hand
    `check_reader_compatibility` a negative version — neither equal to the
    sentinel nor newer than supported, so it passed both guards as readable.
    A guard that relies on its callers having cleaned the input is not a
    fail-closed guard.
    """

    @pytest.mark.parametrize("value", [-1, -99, UNSTATED_VERSION])
    def test_a_non_positive_package_format_is_refused(self, value: int) -> None:
        versions = StorageVersions(
            package_format_version=value,
            comparison_contract_version=COMPARISON_CONTRACT_VERSION,
        )

        assert not check_reader_compatibility(versions).readable

    @pytest.mark.parametrize("value", [-1, -99, UNSTATED_VERSION])
    def test_a_non_positive_comparison_contract_is_refused(self, value: int) -> None:
        versions = StorageVersions(
            package_format_version=PACKAGE_FORMAT_VERSION,
            comparison_contract_version=value,
        )

        assert not check_reader_compatibility(versions).readable

    def test_the_reported_direct_construction_is_refused(self) -> None:
        """The literal case from review: both axes negative."""
        result = check_reader_compatibility(
            StorageVersions(package_format_version=-1, comparison_contract_version=-1)
        )

        assert not result.readable
        assert result.reason

    def test_a_refusal_always_carries_a_reason(self) -> None:
        """An empty reason was the tell — it meant no branch had fired."""
        for versions in (
            StorageVersions(package_format_version=-1),
            StorageVersions(comparison_contract_version=-1),
            StorageVersions(package_format_version=PACKAGE_FORMAT_VERSION + 1),
        ):
            result = check_reader_compatibility(versions)
            assert not result.readable
            assert result.reason

    def test_valid_versions_are_unaffected(self) -> None:
        assert check_reader_compatibility(StorageVersions()).readable


class TestTheDecisionPointRejectsNonIntegralVersions:
    """Codex review, second round on the same guard.

    Comparing `<= UNSTATED_VERSION` instead of `== ` closed the negative case
    but not the class, because the remaining malformed shapes are not
    *ordered* the way that fix assumed. `0.5` is greater than the sentinel and
    not greater than the supported version, so it read as compatible; `True`
    is `1` under comparison, so it was accepted as v1; and a string raised
    `TypeError` out of the comparison itself rather than failing closed.

    The fix shares one rule with `from_dict`'s sanitizer rather than spelling
    a second notion of "usable version" at the decision point — a second
    notion is exactly what let these three diverge from the first.
    """

    @pytest.mark.parametrize("value", [0.5, 1.5, -0.5, True, False])
    def test_a_non_integral_package_format_is_refused(self, value: object) -> None:
        versions = StorageVersions(
            package_format_version=value,  # type: ignore[arg-type]
            comparison_contract_version=COMPARISON_CONTRACT_VERSION,
        )

        result = check_reader_compatibility(versions)
        assert not result.readable
        assert result.reason

    @pytest.mark.parametrize("value", [0.5, 1.5, -0.5, True, False])
    def test_a_non_integral_comparison_contract_is_refused(self, value: object) -> None:
        versions = StorageVersions(
            package_format_version=PACKAGE_FORMAT_VERSION,
            comparison_contract_version=value,  # type: ignore[arg-type]
        )

        result = check_reader_compatibility(versions)
        assert not result.readable
        assert result.reason

    def test_the_reported_direct_construction_is_refused(self) -> None:
        """The literal case from review: both axes `0.5`."""
        result = check_reader_compatibility(
            StorageVersions(
                package_format_version=0.5,  # type: ignore[arg-type]
                comparison_contract_version=0.5,  # type: ignore[arg-type]
            )
        )

        assert not result.readable
        assert result.reason

    @pytest.mark.parametrize("value", ["2", "1", None, [1], {"v": 1}])
    @pytest.mark.parametrize(
        "axis", ["package_format_version", "comparison_contract_version"]
    )
    def test_a_non_numeric_version_fails_closed_rather_than_raising(
        self, axis: str, value: object
    ) -> None:
        """It must refuse, not raise: `'2' > 1` was a `TypeError` out of the guard."""
        axes: dict[str, object] = {
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
        }
        axes[axis] = value

        result = check_reader_compatibility(StorageVersions(**axes))  # type: ignore[arg-type]

        assert not result.readable
        assert result.reason

    def test_an_integral_float_is_still_a_real_version(self) -> None:
        """Refusing malformed values must not refuse an equal value spelled 1.0."""
        result = check_reader_compatibility(
            StorageVersions(
                package_format_version=float(PACKAGE_FORMAT_VERSION),  # type: ignore[arg-type]
                comparison_contract_version=float(  # type: ignore[arg-type]
                    COMPARISON_CONTRACT_VERSION
                ),
            )
        )

        assert result.readable

    @pytest.mark.parametrize("value", [0.5, True, "2", None, -1, 0, [1]])
    def test_the_guard_and_the_sanitizer_agree(self, value: object) -> None:
        """One rule, checked as one: whatever `from_dict` calls unusable, the
        guard must refuse when handed the same value directly."""
        sanitized = StorageVersions.from_dict(
            {
                "package_format_version": value,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
            }
        )
        direct = StorageVersions(
            package_format_version=value,  # type: ignore[arg-type]
            comparison_contract_version=COMPARISON_CONTRACT_VERSION,
        )

        assert (
            check_reader_compatibility(sanitized).readable
            == check_reader_compatibility(direct).readable
        )


class TestSurrogateEscapedContentIsHashable:
    """Codex review: a real POSIX path could make a package unaddressable.

    A filesystem path carrying a non-UTF-8 byte decodes through
    `surrogateescape` into a lone surrogate — `os.fsdecode(b"caf\\xe9")` is
    `"caf\\udce9"` — and encoding that to UTF-8 raises `UnicodeEncodeError`.
    The failure was asymmetric, which is what made it worse than a refusal:
    `canonical_json` accepted the value, so a document could be produced that
    `semantic_digest` could not address.
    """

    #: A real path shape, not a synthetic code point: a latin-1 byte in a
    #: POSIX filename, decoded the way `os` decodes every path it hands back.
    SURROGATE_PATH = os.fsdecode(b"/src/caf\xe9.h")

    def test_a_surrogate_escaped_path_can_be_digested(self) -> None:
        assert semantic_digest({"path": self.SURROGATE_PATH}).startswith("sha256:")

    def test_the_digest_is_stable_across_calls(self) -> None:
        again = os.fsdecode(b"/src/caf\xe9.h")

        assert semantic_digest({"path": self.SURROGATE_PATH}) == semantic_digest(
            {"path": again}
        )

    def test_it_does_not_collide_with_the_ascii_spelling(self) -> None:
        """Escaping must stay injective — the whole point of a content address."""
        assert semantic_digest({"path": self.SURROGATE_PATH}) != semantic_digest(
            {"path": "/src/cafe.h"}
        )

    @pytest.mark.parametrize(
        "text",
        [
            "café",  # ordinary non-ASCII
            "日本語",  # non-latin
            "😀",  # non-BMP, escapes as a surrogate pair
            os.fsdecode(b"\xff\xfe"),  # two lone surrogates
            "a\udce9b\udcffc",  # surrogates interleaved with ASCII
        ],
    )
    def test_every_string_shape_is_hashable_and_distinct(self, text: str) -> None:
        digest = semantic_digest({"k": text})

        assert digest.startswith("sha256:")
        assert digest != semantic_digest({"k": "placeholder"})

    def test_canonical_json_still_accepts_it(self) -> None:
        """Pinning the asymmetry rather than pretending it is gone.

        The stored document deliberately keeps `ensure_ascii=False` for
        readability, so this succeeds while a UTF-8 encode of its output would
        not. The digest path is the one that had to be made total; a Phase 1
        writer's handling of such a path is its own explicit decision.
        """
        rendered = canonical_json({"path": self.SURROGATE_PATH})

        assert "caf" in rendered
        with pytest.raises(UnicodeEncodeError):
            rendered.encode("utf-8")


class TestVersionSerializationAgreesWithTheReader:
    """Codex review: `to_dict` wrote a value its own reader rules out.

    A directly-constructed `StorageVersions(package_format_version=1.5)`
    serialized as `1.5`, which `from_dict` restored as `UNSTATED_VERSION` — a
    document that does not round-trip and that this build refuses to read,
    describing an object whose own guard had already ruled the value out.

    Same defect as `AvailabilityLedger.to_dict` earlier in this branch: a
    serializer disagreeing with its reader emits documents that mean something
    other than the object they came from.
    """

    @pytest.mark.parametrize("value", [1.5, 0.5, True, -1, 0, "2", None])
    def test_a_malformed_fail_closed_axis_serializes_as_unstated(
        self, value: object
    ) -> None:
        versions = StorageVersions(
            package_format_version=value,  # type: ignore[arg-type]
            comparison_contract_version=COMPARISON_CONTRACT_VERSION,
        )

        assert versions.to_dict()["package_format_version"] == UNSTATED_VERSION

    @pytest.mark.parametrize("value", [1.5, True, -1, "2", None])
    @pytest.mark.parametrize(
        "axis", ["package_format_version", "comparison_contract_version"]
    )
    def test_the_document_round_trips(self, axis: str, value: object) -> None:
        axes: dict[str, object] = {
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
        }
        axes[axis] = value
        emitted = StorageVersions(**axes).to_dict()  # type: ignore[arg-type]

        assert StorageVersions.from_dict(emitted).to_dict() == emitted

    def test_serialization_agrees_with_the_readers_verdict(self) -> None:
        """The general contract: what is written reads back the same way."""
        for value in (1.5, True, -1, 0, "2", None, PACKAGE_FORMAT_VERSION):
            versions = StorageVersions(
                package_format_version=value,  # type: ignore[arg-type]
                comparison_contract_version=COMPARISON_CONTRACT_VERSION,
            )
            reloaded = StorageVersions.from_dict(versions.to_dict())

            assert (
                check_reader_compatibility(versions).readable
                == check_reader_compatibility(reloaded).readable
            )

    def test_a_valid_version_is_written_unchanged(self) -> None:
        emitted = StorageVersions().to_dict()

        assert emitted["package_format_version"] == PACKAGE_FORMAT_VERSION
        assert emitted["comparison_contract_version"] == COMPARISON_CONTRACT_VERSION

    def test_an_integral_float_is_written_as_an_integer(self) -> None:
        """`1.0` is a real version; it must not survive as a float in JSON."""
        emitted = StorageVersions(
            package_format_version=float(PACKAGE_FORMAT_VERSION),  # type: ignore[arg-type]
        ).to_dict()

        assert emitted["package_format_version"] == PACKAGE_FORMAT_VERSION
        assert isinstance(emitted["package_format_version"], int)


class TestNestedBooleansInSetMembersAgree:
    """Codex review: the bool/int collapse stopped at the member's top level.

    Python considers `{(True,)}` and `{(1,)}` equal sets — which tuple survives
    construction depends only on which was inserted first — but they
    canonicalized to `[[true]]` and `[[1]]` and received different digests.
    That is exactly the defect the top-level collapse was written to fix, one
    level down: the fix had been scoped to the shape that was demonstrated
    rather than to the rule.
    """

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            pytest.param({(True,)}, {(1,)}, id="tuple"),
            pytest.param({((True,),)}, {((1,),)}, id="nested-tuple"),
            pytest.param({frozenset({True})}, {frozenset({1})}, id="frozenset"),
            pytest.param({(False,)}, {(0,)}, id="false-and-zero"),
            pytest.param({(1, (False, "x"))}, {(True, (0, "x"))}, id="mixed-depths"),
        ],
    )
    def test_equal_sets_receive_equal_digests(self, left: set, right: set) -> None:
        assert left == right, "fixture must be equal sets or this proves nothing"

        assert semantic_digest({"s": left}) == semantic_digest({"s": right})

    def test_a_boolean_outside_a_set_is_still_content(self) -> None:
        """The collapse must stay scoped to where equality forces it.

        `{"x": True}` and `{"x": 1}` are genuinely different documents; it is
        set *membership* that makes the distinction unrecoverable, so it is
        only there that agreeing is the sole option left.
        """
        assert semantic_digest({"x": True}) != semantic_digest({"x": 1})
        assert semantic_digest({"x": [True]}) != semantic_digest({"x": [1]})

    def test_distinct_sets_still_differ(self) -> None:
        """Collapsing must not merge sets that are not equal."""
        assert semantic_digest({"s": {(1,)}}) != semantic_digest({"s": {(2,)}})
        assert semantic_digest({"s": {(1, 2)}}) != semantic_digest({"s": {(1,)}})


class TestExtendableOutputAlgorithmsAreRefused:
    """Codex review: `hashlib.new` accepts SHAKE, `hexdigest()` does not.

    SHAKE is an extendable-output function, so a caller selecting one got a
    bare `TypeError: hexdigest() missing required argument 'length'` from
    inside a digest call. `algorithm` is public and exists so a future
    algorithm change is expressible, which makes an accepted-but-unusable
    value a real trap rather than a theoretical one.
    """

    @pytest.mark.parametrize("algorithm", ["shake_128", "shake_256"])
    def test_a_variable_length_algorithm_is_refused(self, algorithm: str) -> None:
        with pytest.raises(ValueError, match="extendable-output"):
            semantic_digest({"a": 1}, algorithm=algorithm)

    @pytest.mark.parametrize(
        "algorithm", ["sha256", "sha512", "sha3_256", "blake2b", "blake2s"]
    )
    def test_fixed_length_algorithms_still_work(self, algorithm: str) -> None:
        """Detection is by digest size, not an allowlist of names.

        An allowlist would also refuse a future fixed-length algorithm, which
        is the opposite of what this parameter is for — so every fixed-length
        algorithm hashlib offers must keep working, not just sha256.
        """
        digest = semantic_digest({"a": 1}, algorithm=algorithm)

        assert digest.startswith(f"{algorithm}:")
        assert len(digest.split(":", 1)[1]) > 0

    def test_an_unknown_algorithm_still_reports_itself(self) -> None:
        """The pre-existing error must not be swallowed by the new guard."""
        with pytest.raises(ValueError, match="unsupported hash type"):
            semantic_digest({"a": 1}, algorithm="definitely-not-a-hash")

    def test_the_digest_names_the_algorithm_that_produced_it(self) -> None:
        assert semantic_digest({"a": 1}, algorithm="sha512").split(":")[0] == "sha512"
