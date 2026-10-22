"""ADR-062 D2 source-axis amendment: the source-tier generation is its own axis.

Bug class: L4/L5 extractor semantics had no generation of their own, so a
package stored with source-tier facts could not say which source semantics
produced them, and a change to the L4/L5 extractors either drifted nothing or
(if folded into the extractor axis) drifted every L2 package. These tests pin
the behaviour the separate axis must have:

* a package that carries source-tier facts and was produced under different
  source semantics is readable and reported as ``semantics_differ``, never
  refused (it is a derived-answer caveat, not a container or contract break);
* a package that states no source generation (an L2-only package, or any
  package written before this axis existed) is never drifted by it;
* the serialized form is unchanged when the axis is unstated, so existing
  packages keep their exact bytes and digests.

The oracles are the literal values the spec states, not the implementation's
own helpers.
"""

from __future__ import annotations

import pytest

from abicheck.storage import versioning
from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
    PACKAGE_FORMAT_VERSION,
    RESOLVER_GENERATION,
    SOURCE_GENERATION,
    StorageVersions,
    check_reader_compatibility,
    reader_generation_compatibility,
    this_build_generations,
)


def _package(**overrides: int) -> StorageVersions:
    """A readable package stating every fail-closed axis validly."""
    base: dict[str, int] = {
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
    }
    base.update(overrides)
    return StorageVersions(**base)  # type: ignore[arg-type]


def test_this_build_generations_is_a_triple_with_the_source_axis_last() -> None:
    assert this_build_generations() == (
        EXTRACTOR_GENERATION,
        RESOLVER_GENERATION,
        0,
    )
    assert this_build_generations(carries_source_facts=True) == (
        EXTRACTOR_GENERATION,
        RESOLVER_GENERATION,
        SOURCE_GENERATION,
    )


def test_source_drift_is_readable_and_reported_not_refused() -> None:
    package = _package(source_generation=SOURCE_GENERATION)
    result = check_reader_compatibility(
        package, reader_source_generation=SOURCE_GENERATION + 1
    )
    assert result.readable is True
    assert result.semantics_differ is True
    assert GENERATION_DRIFT_NOTICE_MARKER in result.reason
    assert "source" in result.reason


def test_unstated_source_generation_is_never_drift() -> None:
    package = _package(source_generation=0)
    result = check_reader_compatibility(package, reader_source_generation=7)
    assert result.readable is True
    assert result.semantics_differ is False
    assert result.reason == ""


def test_matching_source_generation_is_not_drift() -> None:
    package = _package(source_generation=3)
    result = check_reader_compatibility(package, reader_source_generation=3)
    assert result.semantics_differ is False


def test_production_reader_compares_against_this_builds_source_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(source_generation=SOURCE_GENERATION)
    assert reader_generation_compatibility(package).semantics_differ is False
    monkeypatch.setattr(versioning, "SOURCE_GENERATION", SOURCE_GENERATION + 1)
    result = reader_generation_compatibility(package)
    assert result.readable is True
    assert result.semantics_differ is True


def test_l2_only_package_does_not_drift_when_source_semantics_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason the axis exists: an L4/L5 change must not drift L2 packages."""
    generations = this_build_generations(carries_source_facts=False)
    _, _, stated_source = generations
    assert stated_source == 0
    package = _package(
        extractor_generation=generations[0],
        resolver_generation=generations[1],
        source_generation=stated_source,
    )
    monkeypatch.setattr(versioning, "SOURCE_GENERATION", SOURCE_GENERATION + 5)
    result = reader_generation_compatibility(package)
    assert result.readable is True
    assert result.semantics_differ is False


def test_written_by_this_build_stamps_source_only_when_asked() -> None:
    l2 = StorageVersions.written_by_this_build(
        section_schema_versions={},
        source_schema_version=0,
        generations=this_build_generations(carries_source_facts=False),
    )
    with_source = StorageVersions.written_by_this_build(
        section_schema_versions={},
        source_schema_version=0,
        generations=this_build_generations(carries_source_facts=True),
    )
    assert l2.source_generation == 0
    assert with_source.source_generation == SOURCE_GENERATION


def test_unstated_source_generation_is_omitted_from_the_document() -> None:
    """Existing L2 packages keep their exact serialized form."""
    assert "source_generation" not in _package().to_dict()
    assert "source_generation" not in _package(source_generation=0).to_dict()


def test_source_generation_round_trips_and_absent_reads_as_unstated() -> None:
    stated = _package(source_generation=4)
    document = stated.to_dict()
    assert document["source_generation"] == 4
    assert StorageVersions.from_dict(document).source_generation == 4
    assert StorageVersions.from_dict({}).source_generation == 0


@pytest.mark.parametrize("raw", [-1, "x", None, 1.5, True])
def test_malformed_source_generation_degrades_to_unstated(raw: object) -> None:
    """Informational parsing degrades; it never aborts a load or drifts."""
    parsed = StorageVersions.from_dict({"source_generation": raw})
    assert parsed.source_generation == 0


def test_positional_construction_keeps_the_existing_field_meanings() -> None:
    """A positional caller's seventh argument is still the comparison contract.

    The source axis is declared last. Declared among the generations, it shifted
    ``comparison_contract_version`` and both provenance fields for every
    positional caller, and an old ``1`` read as a source generation.
    """
    positional = StorageVersions(
        PACKAGE_FORMAT_VERSION,
        {},
        "",
        versioning.ProducerIdentity(),
        0,
        0,
        COMPARISON_CONTRACT_VERSION,
    )
    assert positional.comparison_contract_version == COMPARISON_CONTRACT_VERSION
    assert positional.source_generation == 0
