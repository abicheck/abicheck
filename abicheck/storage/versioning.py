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

So the axes are split. Only :attr:`StorageVersions.comparison_contract_version`
fails closed on a newer value — the others are informational to a reader
that does not recognize them, which is exactly what lets a display-only
addition ship without locking out existing readers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PACKAGE_FORMAT_VERSION",
    "COMPARISON_CONTRACT_VERSION",
    "ProducerIdentity",
    "ReaderCompatibility",
    "StorageVersions",
    "check_reader_compatibility",
]

#: Container/manifest layout. Bumped when the package *shape* changes.
PACKAGE_FORMAT_VERSION = 1

#: What a reader must understand to compare safely. This is the only axis a
#: reader refuses on, so it is bumped only when reading a package without
#: understanding the change could produce a *wrong verdict* — never for a
#: field a reader can ignore.
COMPARISON_CONTRACT_VERSION = 1


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
        out: dict[str, Any] = {
            "package_format_version": self.package_format_version,
            "comparison_contract_version": self.comparison_contract_version,
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
            package_format_version=int(
                data.get("package_format_version", PACKAGE_FORMAT_VERSION)
            ),
            section_schema_versions={
                str(k): int(v)
                for k, v in dict(data.get("section_schema_versions", {})).items()
            },
            normalization_recipe=str(data.get("normalization_recipe", "")),
            producer=ProducerIdentity.from_dict(data.get("producer", {})),
            extractor_generation=int(data.get("extractor_generation", 0)),
            resolver_generation=int(data.get("resolver_generation", 0)),
            comparison_contract_version=int(
                data.get("comparison_contract_version", COMPARISON_CONTRACT_VERSION)
            ),
            source_schema_version=int(data.get("source_schema_version", 0)),
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
    if versions.package_format_version > supported_package_format:
        return ReaderCompatibility(
            readable=False,
            reason=(
                f"package format v{versions.package_format_version} is newer than "
                f"this build's v{supported_package_format}; upgrade abicheck to read it"
            ),
        )
    if versions.comparison_contract_version > supported_comparison_contract:
        return ReaderCompatibility(
            readable=False,
            reason=(
                f"comparison contract v{versions.comparison_contract_version} is newer "
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
