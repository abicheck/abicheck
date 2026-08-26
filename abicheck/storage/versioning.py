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
from types import MappingProxyType
from typing import Any

from .guards import instance_of as _instance_of

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


def _stated_text(raw: object) -> str:
    """An informational *text* field, or ``""`` if it is not one.

    The text counterpart of :func:`_stated_count`, and it was `str()` at both
    doors until a review round showed what that buys (Codex review):

    * ``producer.name: null`` became the literal producer name ``"None"`` — a
      fabricated identity, persisted by ``to_dict`` and indistinguishable
      from a producer that really is called that;
    * a mapping became its **insertion-ordered** ``repr``, so
      ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` — the same document to
      any reader — produced different field values, different documents, and
      therefore **different semantic digests**. In a content-addressed store
      that is the one failure mode the canonical form exists to rule out, and
      it arrived through a field nothing about it looked dangerous.

    Degrading rather than rejecting keeps this module's informational
    contract: no decision reads these, and a malformed one must not abort a
    load. Degrading to *empty* rather than to a stringification is what makes
    the degrade honest — "not stated" instead of a value invented from the
    shape of the input.
    """
    return raw if isinstance(raw, str) else ""


def _frozen_sections(raw: object) -> Mapping[str, int]:
    """:func:`_stated_sections`, wrapped so the stored field cannot be mutated."""
    return MappingProxyType(_stated_sections(raw))


def _stated_sections(raw: object) -> dict[str, int]:
    """The section-version mapping, normalized identically at both doors.

    Two findings, one function (Codex review). Each door had half of it:

    * ``to_dict`` called ``.items()`` on whatever it held, so a directly
      constructed ``section_schema_versions=["layout"]`` constructed fine and
      then raised ``AttributeError`` on serialization — while the reader
      degraded that exact shape to ``{}``. The container is checked before it
      is dereferenced, the same rule the ledger's mappings follow.
    * ``from_dict`` inserted normalized keys in *source iteration order*, so
      ``{1: 1, "1": 2}`` and ``{"1": 2, 1: 1}`` — which collapse to one key —
      kept different values, reserialized to different documents, and
      **addressed differently**. ``to_dict`` already sorted for exactly this
      reason; the reader did not, so the two doors disagreed about which of a
      colliding pair survives.

    Sorting is what makes the collapse deterministic: the surviving value is
    decided by the ``(key, count)`` pair's own order, not by how the caller's
    mapping happened to be traversed. Keys keep their ``str()`` rather than
    being degraded to empty — a key *names* its entry, so every degraded key
    would name the same entry.

    **Only ``str`` keys are kept; every other key is dropped.** That is the
    fourth answer this field has had to the question "how do you stringify a
    key safely", and the previous three each survived one review round:

    * plain ``str(k)`` let two keys collide with the survivor decided by
      traversal order;
    * sorting fixed the collapse but not the conversion, so a ``frozenset``
      key — whose ``str()`` renders members in hash order — still gave one
      logical block three section names and three digests across three
      interpreters;
    * an allowlist of ``str``/``bool``/``int``/``float``/``None`` fixed that
      and left ``{1: 2}``, ``{1.0: 2}`` and ``{True: 2}`` — which are *the
      same mapping* in Python — emitting ``"1"``, ``"1.0"`` and ``"True"``,
      with three digests for one value. ``0.0`` and ``-0.0`` likewise.

    Each fix drew the next instance, which is this repo's own signal to
    change the mechanism rather than patch the rule again. There is no
    stringification of a non-``str`` key that is both injective and
    order-independent, because Python's key *equality* does not distinguish
    the spellings its ``str()`` does. Keeping only what JSON can actually
    carry removes the question.

    A document parsed from JSON is unaffected: its keys are always strings.
    A caller hand-constructing a numeric key loses an informational entry,
    which is the lesser harm against a content address that disagrees with
    the mapping's own notion of equality.
    """
    if not isinstance(raw, Mapping):
        return {}
    stated = sorted(
        (key, _stated_count(value))
        for key, value in raw.items()
        if isinstance(key, str)
    )
    # A zero count is `_stated_count`'s own "unstated", and an entry stating
    # nothing is dropped rather than written as `0` — otherwise
    # `{"layout": "bad"}` reserialized as `{"layout": 0}` and took a
    # different digest from a document that states no section version at all,
    # though neither states one (Codex review). The scalar axes already omit
    # an unstated value; this is the same rule on the field that is a mapping.
    return {key: count for key, count in stated if count}


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

    def __post_init__(self) -> None:
        """Canonicalize the state, for the reason :class:`StorageVersions` does.

        A record whose ``to_dict`` normalizes but whose fields do not is a
        record that serializes equal and compares unequal.
        """
        for field_name in ("name", "version", "binary_digest"):
            object.__setattr__(
                self, field_name, _stated_text(getattr(self, field_name))
            )

    def to_dict(self) -> dict[str, Any]:
        """Normalized the way :meth:`from_dict` reads it.

        Writing the fields raw meant `ProducerIdentity(name=1)` emitted `1`
        and reloaded as `"1"` — a record that emits a document interpreted
        differently from itself (Codex review). Same rule as the outer
        `StorageVersions` axes, which was applied there and not here: these
        are informational, so they are normalized rather than rejected, and
        the truthiness test reads the normalized value.
        """
        out: dict[str, Any] = {}
        for field_name in ("name", "version", "binary_digest"):
            text = _stated_text(getattr(self, field_name))
            if text:
                out[field_name] = text
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProducerIdentity:
        if not isinstance(data, Mapping):
            # A scalar where an object belongs is malformed, not fatal.
            return cls()
        return cls(
            name=_stated_text(data.get("name")),
            version=_stated_text(data.get("version")),
            binary_digest=_stated_text(data.get("binary_digest")),
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
    #: Read-only: a frozen record must not expose a mutable field. The
    #: normalization in `__post_init__` was bypassable by
    #: `versions.section_schema_versions["x"] = "bad"`, which left the object
    #: serializing like a normalized twin while comparing unequal to it
    #: (Codex review) — the non-canonical in-memory state that normalization
    #: exists to remove, reintroduced through the one field whose value is a
    #: container. The proxy wraps a dict built inside `_stated_sections`, so
    #: no caller holds a reference to what it wraps.
    section_schema_versions: Mapping[str, int] = field(default_factory=dict)
    normalization_recipe: str = ""
    producer: ProducerIdentity = field(default_factory=ProducerIdentity)
    extractor_generation: int = 0
    resolver_generation: int = 0
    comparison_contract_version: int = COMPARISON_CONTRACT_VERSION
    #: Import provenance for an adapted legacy snapshot; 0/"" when native.
    source_schema_version: int = 0
    source_producer_generation: str = ""

    def __post_init__(self) -> None:
        """Validate the record slot, and canonicalize the state itself.

        ``producer`` is a nested :class:`ProducerIdentity`, and a scalar in
        that slot survived construction and then raised ``AttributeError``
        from ``to_dict`` — the object could not reach its own serialized form
        (Codex review). That is a record slot, and record slots are checked
        where they are assigned, the same rule the ledger and the occurrence
        identities apply.

        Every other axis is informational or fail-closed *by value*, so a
        malformed one degrades — and the degrade belongs in the **state**,
        not only in ``to_dict``. Normalizing on the way out alone left
        ``StorageVersions(normalization_recipe=1)`` and
        ``StorageVersions(normalization_recipe=None)`` comparing unequal with
        different ``repr``s while serializing to one document and one digest
        (Codex review), so equality and every diagnostic depended on
        malformed input the format deliberately discards.

        This is `AGENTS.md` invariant 4 in its own words — "a claim about the
        stored *state*, not only about accessors: a canonical view over
        non-canonical state leaves ``__eq__`` and ``repr`` exposed" — and it
        is the second time this branch has hit it. ``OccurrenceSet`` kept
        insertion order in state behind a sorted ``__iter__`` for exactly the
        same reason, and a property test caught that one. The invariant was
        written down from that round and then not applied here.
        """
        _instance_of(self.producer, ProducerIdentity, "producer")
        for field_name, normalize in (
            ("package_format_version", _stated_version),
            ("comparison_contract_version", _stated_version),
            ("section_schema_versions", _frozen_sections),
            ("normalization_recipe", _stated_text),
            ("extractor_generation", _stated_count),
            ("resolver_generation", _stated_count),
            ("source_schema_version", _stated_count),
            ("source_producer_generation", _stated_text),
        ):
            object.__setattr__(self, field_name, normalize(getattr(self, field_name)))

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
        # Truthiness on the *normalized* value, same as the scalar axes
        # below: a non-mapping is truthy but normalizes to `{}`, and writing
        # an empty entry where the reader would report the field absent is
        # the same disagreement in miniature.
        sections = _stated_sections(self.section_schema_versions)
        if sections:
            # Normalized the way this field's own reader normalizes it, so a
            # document emits what it reads back. Writing the mapping verbatim
            # meant `{1: 1}` reloaded as `{"1": 1}` and `{"x": "bad"}` as
            # `{"x": 0}`, while mixed integer and string keys raised from
            # `sorted` mid-serialization (Codex review) — the same
            # "serialization agrees with the reader's verdict" property the
            # two fail-closed axes above already hold to, on the one field
            # that is a mapping rather than a scalar.
            #
            # `str()` on the key rather than a rejection is deliberate and is
            # this module's documented exception: the informational axes parse
            # defensively because no decision reads them, and aborting a write
            # over one would break that contract. Everything a decision reads
            # rejects instead. Two keys colliding under `str()` therefore
            # still collapse — but through `sorted`, so which survives is
            # decided by the pair's own order rather than by how the mapping
            # was traversed.
            out["section_schema_versions"] = sections
        # Every remaining informational axis is normalized the same way, for
        # the same reason: `normalization_recipe=1` wrote `1` and reloaded as
        # `"1"`, and a fractional or non-numeric generation wrote itself
        # verbatim and reloaded as `0` (Codex review). Fixing only the mapping
        # field in the round before this one was the usual mistake — the site
        # that was reported rather than the rule that covers all of them.
        #
        # `_stated_count`/`str()` rather than a rejection stays the documented
        # informational-axis exception; the truthiness test is applied to the
        # *normalized* value, so a field that reads back as absent is written
        # as absent instead of as an unreadable value.
        recipe = _stated_text(self.normalization_recipe)
        if recipe:
            out["normalization_recipe"] = recipe
        producer = self.producer.to_dict()
        if producer:
            out["producer"] = producer
        for name in ("extractor_generation", "resolver_generation"):
            count = _stated_count(getattr(self, name))
            if count:
                out[name] = count
        source_schema = _stated_count(self.source_schema_version)
        if source_schema:
            out["source_schema_version"] = source_schema
        source_generation = _stated_text(self.source_producer_generation)
        if source_generation:
            out["source_producer_generation"] = source_generation
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StorageVersions:
        if not isinstance(data, Mapping):
            # Degraded rather than refused, matching `ProducerIdentity`: every
            # axis this reads is either fail-closed by *value* (an unstated
            # version is `UNSTATED_VERSION`, which the reader already refuses)
            # or informational, and this module's contract is that a malformed
            # informational field never aborts a load. Reaching `.get` on a
            # scalar instead raised `AttributeError` mid-parse, which is not
            # the same thing as degrading (Codex review).
            #
            # It degrades to what an *empty document* parses to, not to
            # `cls()`. Writing `cls()` first was wrong in the one direction
            # that matters: the dataclass defaults are the current *writer's*
            # versions, so a malformed versions block would have read as "this
            # package was written by exactly this build" and passed
            # `check_reader_compatibility`, while an empty mapping stating the
            # same nothing correctly yields `UNSTATED_VERSION` and is refused.
            # A degrade must land on the fail-closed value, not the
            # optimistic one.
            return cls.from_dict({})
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
            section_schema_versions=_stated_sections(
                data.get("section_schema_versions")
            ),
            normalization_recipe=_stated_text(data.get("normalization_recipe")),
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
            source_producer_generation=_stated_text(
                data.get("source_producer_generation")
            ),
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

    The record is checked before it is read. This is the package's one
    public decision point, so an untyped loader or migration adapter handing
    it a parsed mapping used to leak `AttributeError` from the first
    attribute access — which is neither arm of the `TypeError`/`ValueError`
    pair this package documents as "the package is malformed", so a caller
    separating a corrupt package from a broken reader read it as the second
    (Codex review). The malformed *contents* of a real record still degrade
    rather than raise; it is the record itself that must be one.
    """
    _instance_of(versions, StorageVersions, "versions")
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
    # `_stated_count` on the package's own value, because that is what both
    # doors of the document already apply: a directly-constructed
    # `extractor_generation="1"` compared raw here and reported drift against
    # a reader generation of `0`, while the *same object after its documented
    # round trip* reported none — so whether a rebuild was advised depended on
    # whether it had been serialized first (Codex review). A decision reading
    # a field must read the same value the format stores.
    drifted = [
        name
        for name, reader_value, package_value in (
            (
                "extractor",
                reader_extractor_generation,
                _stated_count(versions.extractor_generation),
            ),
            (
                "resolver",
                reader_resolver_generation,
                _stated_count(versions.resolver_generation),
            ),
        )
        if reader_value is not None and package_value != _stated_count(reader_value)
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
