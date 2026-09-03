# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D2's version axes and reader compatibility.

Split out of `test_canonical.py` when that file crossed the 1200-line test
cap. The line is the one its own docstring already drew: it announced two
subjects ("canonical encoding, and D2's version axes"), so the split is the
file admitting what it was already doing rather than a new boundary.

The invariant these state, across many shapes: exactly two axes fail closed
— container layout, because a reader may not be able to locate a newer
package's structures at all, and the comparison contract, because an
unknown one could produce a wrong verdict. Every other axis is
informational and parses defensively.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    UNSTATED_VERSION,
    ProducerIdentity,
    StorageVersions,
    check_reader_compatibility,
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

    @pytest.mark.parametrize(
        "value", [1.5, True, -1, 0, "2", None, PACKAGE_FORMAT_VERSION]
    )
    def test_serialization_agrees_with_the_readers_verdict(self, value: object) -> None:
        """The general contract: what is written reads back the same way.

        Parametrized rather than looped: a loop stops at the first failing
        value, so the rest go unchecked and the failure does not name the one
        that broke.
        """
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
