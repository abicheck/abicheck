# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Separated storage version axes — ADR-062 D2.

``AbiSnapshot.SCHEMA_VERSION`` is one integer (25 today) carrying at least
four independent facts: JSON field layout, producer implementation epoch,
per-fact reliability, and comparison-critical contract compatibility. That
conflation is why several bumps exist for reasons that are not field-layout
changes at all — v9, v19-v23, and v25 each mark a point where a producer
began emitting a *correct* value where it previously emitted a real-but-wrong
default, which the loader then reconstructs into whole-snapshot
``*_facts_reliable`` flags.

Two consequences follow, and both are load-bearing:

* a normalization or resolver change can alter what a stored fact *means*
  without changing any field, so a layout-shaped version cannot express it;
* adding an optional display field forces a bump that reads, to every
  consumer, like a new evidence recipe.

So the axes are split. Exactly two fail closed, for two different reasons:
:attr:`StorageVersions.package_format_version` (a reader may not be able to
*locate* a newer container's structures) and
:attr:`StorageVersions.comparison_contract_version` (comparing without
understanding the change could produce a *wrong verdict*). Each also fails
closed when the package does not state it validly, since an axis that exists
to refuse unknown semantics cannot treat "unknown" as agreement.

The remaining five are informational to a reader that does not recognize
them, which is exactly what lets a display-only addition ship without
locking out existing readers, and they parse defensively: a malformed
informational value must never abort a load, because no decision reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "UNSTATED_VERSION",
    "PACKAGE_FORMAT_VERSION",
    "COMPARISON_CONTRACT_VERSION",
    "ProducerIdentity",
    "ReaderCompatibility",
    "StorageVersions",
    "check_reader_compatibility",
]

#: Container/manifest layout. Bumped when the package *shape* changes.
PACKAGE_FORMAT_VERSION = 1

#: What a reader must understand to compare safely. Bumped only when reading a
#: package without understanding the change could produce a *wrong verdict* —
#: never for a field a reader can ignore.
COMPARISON_CONTRACT_VERSION = 1

#: A version axis the package did not state, or stated unusably. Distinct from
#: any real version so that "unknown" can never be mistaken for "the same as
#: mine".
UNSTATED_VERSION = 0


def _stated_version(raw: object) -> int:
    """A package's version value, or :data:`UNSTATED_VERSION` if unusable.

    A real version is a positive integer. Anything else — absent, fractional,
    zero, negative, or not a number at all — is recorded as unstated, so a
    fail-closed axis refuses it rather than acting on a value it cannot mean.

    ``int()`` alone was not enough (Codex review). A package stating ``1.5``
    became ``1`` and read as this build's own supported version, and ``-1``
    survived as ``-1``, which is neither equal to ``UNSTATED_VERSION`` nor
    greater than the supported version — so both malformed values failed
    *open*, which is the one direction a fail-closed axis must never fail in.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return UNSTATED_VERSION
    if isinstance(raw, float) and not raw.is_integer():
        return UNSTATED_VERSION
    value = int(raw)
    return value if value > 0 else UNSTATED_VERSION


def _stated_count(raw: object) -> int:
    """A generation/count an informational axis stated, or ``0`` if unusable.

    The informational axes must parse *defensively* — this repo's convention
    is that a hand-edited or newer package never aborts a load, and a refusal
    belongs at the decision point. Bare ``int()``/``dict()`` broke that
    contract in four ways at once (CodeRabbit review):
    ``extractor_generation: "x"`` raised ``ValueError``,
    ``resolver_generation: null`` raised ``TypeError``,
    ``section_schema_versions: 5`` raised ``TypeError``, and
    ``producer: "clang"`` raised ``AttributeError`` — so a malformed
    *informational* field, one no decision even reads, could abort loading a
    package whose real evidence was intact.

    Unlike :func:`_stated_version` this accepts ``0``, since ``0`` is these
    axes' own "unset" value rather than an invalid one.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    if isinstance(raw, float) and not raw.is_integer():
        return 0
    value = int(raw)
    return value if value > 0 else 0


@dataclass(frozen=True)
class ProducerIdentity:
    """What emitted a set of facts.

    ``binary_digest`` is the producer *executable's* content digest where one
    is knowable (castxml, clang, a compiler driver). It is what distinguishes
    two runs of "the same version" of a tool that was rebuilt underneath a
    path — the case a version string alone cannot see. Empty when the
    producer is abicheck's own in-process code, where the abicheck version
    already answers the question.
    """

    name: str = ""
    version: str = ""
    binary_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.name:
            out["name"] = self.name
        if self.version:
            out["version"] = self.version
        if self.binary_digest:
            out["binary_digest"] = self.binary_digest
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProducerIdentity:
        if not isinstance(data, Mapping):
            # A scalar where an object belongs is malformed, not fatal.
            return cls()
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            binary_digest=str(data.get("binary_digest", "")),
        )


@dataclass(frozen=True)
class StorageVersions:
    """The seven independent axes a stored package carries.

    ``source_schema_version`` and ``source_producer_generation`` are the
    import-provenance pair: when a legacy v1-v25 snapshot is adapted into
    this model, they record what it *was*, so a migration or an audit can
    answer "which producer epoch actually emitted this fact" without
    reverse-engineering it from the presence of a field. Leaving that
    unrecorded is what forces one special case per newly discovered
    historical producer defect.
    """

    package_format_version: int = PACKAGE_FORMAT_VERSION
    #: Per-section field layout. Keyed by section kind, since sections evolve
    #: independently — that independence is the whole point of D8's split.
    section_schema_versions: dict[str, int] = field(default_factory=dict)
    normalization_recipe: str = ""
    producer: ProducerIdentity = field(default_factory=ProducerIdentity)
    extractor_generation: int = 0
    resolver_generation: int = 0
    comparison_contract_version: int = COMPARISON_CONTRACT_VERSION
    #: Import provenance for an adapted legacy snapshot; 0/"" when native.
    source_schema_version: int = 0
    source_producer_generation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Canonical mapping form.

        The two fail-closed axes are written through the same
        :func:`_stated_version` rule the reader applies, not as the field
        holds them. Writing the raw value let a directly-constructed
        ``StorageVersions(package_format_version=1.5)`` emit ``1.5``, which
        ``from_dict`` then restored as ``UNSTATED_VERSION`` — a document that
        does not round-trip and that this build refuses to read, describing an
        object whose own guard had already ruled the value out (Codex review).

        Same rule as ``AvailabilityLedger.to_dict``, and for the same reason:
        a serializer that disagrees with its own reader emits documents that
        mean something other than the object they came from. Refusing at write
        time was the alternative and is rejected for the same reason it was
        there — it would be a *third* behaviour, failing a write for an object
        the guard already handles safely. Normalizing states plainly what the
        package can be read as: nothing usable.
        """
        out: dict[str, Any] = {
            "package_format_version": _stated_version(self.package_format_version),
            "comparison_contract_version": _stated_version(
                self.comparison_contract_version
            ),
        }
        if self.section_schema_versions:
            out["section_schema_versions"] = dict(
                sorted(self.section_schema_versions.items())
            )
        if self.normalization_recipe:
            out["normalization_recipe"] = self.normalization_recipe
        producer = self.producer.to_dict()
        if producer:
            out["producer"] = producer
        if self.extractor_generation:
            out["extractor_generation"] = self.extractor_generation
        if self.resolver_generation:
            out["resolver_generation"] = self.resolver_generation
        if self.source_schema_version:
            out["source_schema_version"] = self.source_schema_version
        if self.source_producer_generation:
            out["source_producer_generation"] = self.source_producer_generation
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StorageVersions:
        return cls(
            # Absent defaults to UNSTATED, not to this reader's own version —
            # the same rule as the comparison contract below, for the same
            # reason. An earlier round validated both axes against *malformed*
            # values but left this one defaulting to `PACKAGE_FORMAT_VERSION`,
            # so a package stating a valid contract version while omitting its
            # format version was interpreted as the current container layout
            # and read (Codex review). That was an incompletely applied
            # principle rather than a deliberate exception: a fail-closed axis
            # must treat "not validly stated" identically whether the value is
            # wrong or simply missing.
            package_format_version=_stated_version(
                data.get("package_format_version", UNSTATED_VERSION)
            ),
            section_schema_versions=(
                {
                    str(k): _stated_count(v)
                    for k, v in data["section_schema_versions"].items()
                }
                if isinstance(data.get("section_schema_versions"), Mapping)
                else {}
            ),
            normalization_recipe=str(data.get("normalization_recipe", "")),
            producer=ProducerIdentity.from_dict(data.get("producer", {})),
            extractor_generation=_stated_count(data.get("extractor_generation")),
            resolver_generation=_stated_count(data.get("resolver_generation")),
            # Absence is recorded as UNSTATED, never synthesized as this
            # reader's own version. Defaulting to `COMPARISON_CONTRACT_VERSION`
            # made a malformed or pre-versioned package claim to share this
            # build's comparison semantics, so `check_reader_compatibility`
            # then reported it readable — bypassing the one axis that exists
            # to fail closed exactly when those semantics are unknown (Codex
            # review). Parsing stays defensive, per this repo's convention
            # that a hand-edited package must not abort a load; the refusal
            # belongs at the decision point, not here.
            comparison_contract_version=_stated_version(
                data.get("comparison_contract_version", UNSTATED_VERSION)
            ),
            source_schema_version=_stated_count(data.get("source_schema_version")),
            source_producer_generation=str(data.get("source_producer_generation", "")),
        )


@dataclass(frozen=True)
class ReaderCompatibility:
    """Whether this build may read a package, and what it must say if not.

    Carries a ``reason`` even when compatible, so a caller rendering a
    diagnostic never has to reconstruct one — and so the "readable but
    produced by different semantics" case can explain itself, which is the
    case a bare boolean cannot express.
    """

    readable: bool
    reason: str = ""
    #: True when the package is readable but was produced under different
    #: extraction/resolution semantics, so a *derived* answer (a rebuilt
    #: resolution graph, a reachability walk) may differ from the one the
    #: package's own producer would have computed. Readable, not equivalent.
    semantics_differ: bool = False


def check_reader_compatibility(
    versions: StorageVersions,
    *,
    supported_package_format: int = PACKAGE_FORMAT_VERSION,
    supported_comparison_contract: int = COMPARISON_CONTRACT_VERSION,
    reader_extractor_generation: int | None = None,
    reader_resolver_generation: int | None = None,
) -> ReaderCompatibility:
    """Decide whether this build may read a package, per ADR-062 D2.

    Both fail-closed axes are re-validated here through the same
    :func:`_stated_version` rule ``from_dict`` applies, rather than being
    compared as given. ``StorageVersions`` is public and constructible
    directly, so a loader or migration adapter that builds one without going
    through ``from_dict`` hands this function whatever it was given — and two
    successive review rounds found the guard here failing open on exactly
    that path. First a negative version, which is neither equal to the
    sentinel nor newer than supported; comparing ``<= UNSTATED_VERSION``
    instead of ``==`` closed that one but not the class, because the
    remaining malformed shapes are not *ordered* the way that fix assumed:
    ``0.5`` is greater than the sentinel and not greater than the supported
    version, so it read as compatible; ``True`` is ``1`` under comparison, so
    it was accepted as v1; and a string raised ``TypeError`` out of the
    comparison itself instead of failing closed (Codex review).

    Sharing one rule with the sanitizer is the point rather than an
    optimization: a second, differently-spelled notion of "a usable version"
    at the decision point is what let these three diverge from the one
    ``_stated_version`` already stated. The sanitizer and the decision point
    must each be safe on their own, and they must agree on what safe means.

    Two axes fail closed and the rest do not, which is the whole point of
    splitting them:

    * a newer ``package_format_version`` means the container itself may hold
      structures this reader cannot locate;
    * a newer ``comparison_contract_version`` means comparing without
      understanding the change could produce a *wrong verdict*.

    A newer ``extractor_generation``/``resolver_generation`` is reported as
    ``semantics_differ`` rather than refused. A stored baseline stays
    readable across a resolver correction — provider selection, alias
    normalization, symbol-version handling and reachability have each been
    corrected over time — but a graph *derived* from it under today's
    semantics is not necessarily the answer its original producer would have
    given, and a consumer that cares must be able to see that rather than
    have the package silently refused or silently reinterpreted.
    """
    package_format = _stated_version(versions.package_format_version)
    comparison_contract = _stated_version(versions.comparison_contract_version)
    if package_format > supported_package_format:
        return ReaderCompatibility(
            readable=False,
            reason=(
                f"package format v{package_format} is newer than "
                f"this build's v{supported_package_format}; upgrade abicheck to read it"
            ),
        )
    if package_format <= UNSTATED_VERSION:
        return ReaderCompatibility(
            readable=False,
            reason=(
                "package does not state a usable package format version, so its "
                "layout is unknown; this reader may not locate its structures"
            ),
        )
    if comparison_contract <= UNSTATED_VERSION:
        return ReaderCompatibility(
            readable=False,
            reason=(
                "package does not state a usable comparison contract version, so "
                "its comparison semantics are unknown; comparing against it could "
                "produce a wrong verdict"
            ),
        )
    if comparison_contract > supported_comparison_contract:
        return ReaderCompatibility(
            readable=False,
            reason=(
                f"comparison contract v{comparison_contract} is newer "
                f"than this build's v{supported_comparison_contract}; comparing without "
                "it could produce a wrong verdict"
            ),
        )
    drifted = [
        name
        for name, reader_value, package_value in (
            ("extractor", reader_extractor_generation, versions.extractor_generation),
            ("resolver", reader_resolver_generation, versions.resolver_generation),
        )
        if reader_value is not None and package_value != reader_value
    ]
    if drifted:
        return ReaderCompatibility(
            readable=True,
            semantics_differ=True,
            reason=(
                f"package was produced under different {'/'.join(drifted)} semantics; "
                "derived results may differ from the original producer's"
            ),
        )
    return ReaderCompatibility(readable=True)
