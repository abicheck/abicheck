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

"""Explicit fact availability — ADR-062 D3.

The problem this closes is that a plain ``bool``, ``[]``, ``{}``, or ``None``
in a stored snapshot cannot distinguish five genuinely different situations:

* the producer ran and established the fact is absent (a real, usable fact);
* the producer never ran for this family (not collected);
* the producer cannot express this family at all (unsupported);
* the producer ran and failed (failed);
* the family does not apply to this artifact kind (not applicable).

Today's snapshots answer this with whole-snapshot ``*_facts_reliable`` flags
reconstructed from ``SCHEMA_VERSION`` history. Those flags are correct for
the specific historical producer defects they name, but they scale by adding
one flag per defect, they are per-snapshot rather than per fact family, and
they say nothing when one side of a comparison was captured at L2 and the
other at L4.

The rule this module exists to enforce is one sentence:

    A comparison may never infer safety from an empty collection.

``FactAvailability.comparable`` is that rule as a single predicate, so a call
site cannot accidentally invent its own convention for what a missing value
means.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "AvailabilityLedger",
    "Confidence",
    "FactAvailability",
    "FactStatus",
]


class FactStatus(enum.Enum):
    """Why a fact family is, or is not, present in stored evidence.

    Deliberately five *distinct* members rather than a boolean plus a note:
    a reader that collapses ``UNSUPPORTED`` and ``FAILED`` into one "no data"
    case cannot tell a permanent producer limitation (re-running changes
    nothing) from a transient extraction error (re-running may well fix it),
    and a reader that collapses either into ``PRESENT`` reintroduces the bug
    this whole module exists to close.
    """

    #: The producer ran, covered the requested scope, and established the
    #: facts — including establishing that a collection is legitimately empty.
    PRESENT = "present"
    #: The producer ran but covered only part of the requested scope. Usable,
    #: with reduced confidence; the uncovered part is unknown, not absent.
    PARTIAL = "partial"
    #: The producer was never invoked for this family (e.g. the run was
    #: capped at a shallower evidence level). Says nothing about the facts.
    NOT_COLLECTED = "not_collected"
    #: This producer cannot express this family at all. Re-running the same
    #: producer will never help; a different producer might.
    UNSUPPORTED = "unsupported"
    #: The producer was invoked and errored. Diagnostics should say how.
    FAILED = "failed"
    #: The family is meaningless for this artifact kind (e.g. vtables for a
    #: C-only artifact). Not a gap — nothing is missing.
    NOT_APPLICABLE = "not_applicable"


class Confidence(enum.Enum):
    """How much weight a consumer may put on a ``PRESENT``/``PARTIAL`` fact."""

    HIGH = "high"
    REDUCED = "reduced"
    UNKNOWN = "unknown"


#: Statuses a comparison may draw a compatibility conclusion from. Every other
#: status means the evidence is absent for a reason, so a comparison that
#: needs the family must degrade explicitly (``NOT_COMPARABLE`` or a
#: reduced-confidence result) rather than read an empty collection as "fine".
_COMPARABLE_STATUSES = frozenset({FactStatus.PRESENT, FactStatus.PARTIAL})


@dataclass(frozen=True)
class FactAvailability:
    """One fact family's availability, with the evidence for the claim.

    Frozen because an availability record is a statement about a completed
    extraction. A consumer that wants to narrow one (a per-entity override)
    calls :meth:`narrowed`, which returns a new record, rather than mutating
    a record another consumer may already have read.
    """

    status: FactStatus
    #: Producer that answered for this family (``"clang"``, ``"castxml"``,
    #: ``"dwarf"``, ``"pdb"``, ``"elf"``, ...). Empty only when nothing ran.
    producer: str = ""
    producer_version: str = ""
    #: Normalization/extraction recipe id, so two ``PRESENT`` records from
    #: different recipes are not silently treated as interchangeable.
    recipe: str = ""
    #: What was actually covered — free-form, producer-defined (a TU set, a
    #: header root, a symbol table). Meaningful mainly for ``PARTIAL``.
    scope: str = ""
    confidence: Confidence = Confidence.HIGH
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, FactStatus):  # pragma: no cover - guard
            raise TypeError(f"status must be a FactStatus, got {self.status!r}")
        if not isinstance(self.confidence, Confidence):  # pragma: no cover - guard
            raise TypeError(f"confidence must be a Confidence, got {self.confidence!r}")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def comparable(self) -> bool:
        """Whether a comparison may draw a conclusion from this family.

        This is the single predicate ADR-062 D3 asks call sites to route
        through. It is deliberately *not* ``status is not FactStatus.FAILED``
        or any other negative spelling: adding a sixth status later must make
        the new status non-comparable by default, so that a status this
        predicate has never heard of can never be mistaken for usable
        evidence.
        """
        return self.status in _COMPARABLE_STATUSES

    @property
    def establishes_absence(self) -> bool:
        """Whether an empty collection under this record means "really empty".

        Only ``PRESENT`` licenses that reading. ``PARTIAL`` explicitly does
        not: the covered part is known, the rest is unknown, and an empty
        collection under partial coverage is exactly the ambiguity this
        module exists to keep visible.
        """
        return self.status is FactStatus.PRESENT

    def narrowed(self, other: FactAvailability) -> FactAvailability:
        """Combine a family-level default with a per-entity override.

        Narrowing, never widening: an entity may report *worse* availability
        than its family (this one record failed while the family succeeded),
        but may never claim better. A per-entity ``PRESENT`` under a family
        that was never collected is a producer bug, and silently honouring it
        would let one optimistic override defeat the family-level statement.
        Diagnostics accumulate from both sides, since both explain the result.
        """
        status = _worse_status(self.status, other.status)
        confidence = _worse_confidence(self.confidence, other.confidence)
        merged_diagnostics = self.diagnostics + tuple(
            d for d in other.diagnostics if d not in self.diagnostics
        )
        # Identifying fields come from the override where it states one, since
        # the override is the more specific observation.
        return replace(
            other if other.producer else self,
            status=status,
            confidence=confidence,
            diagnostics=merged_diagnostics,
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical mapping form. Keys with default values are omitted.

        Omission keeps a package's per-entity override records small — the
        common override states a status and nothing else — and round-trips
        exactly, because :meth:`from_dict` restores the same defaults.
        """
        out: dict[str, Any] = {"status": self.status.value}
        if self.producer:
            out["producer"] = self.producer
        if self.producer_version:
            out["producer_version"] = self.producer_version
        if self.recipe:
            out["recipe"] = self.recipe
        if self.scope:
            out["scope"] = self.scope
        if self.confidence is not Confidence.HIGH:
            out["confidence"] = self.confidence.value
        if self.diagnostics:
            out["diagnostics"] = list(self.diagnostics)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FactAvailability:
        """Rebuild from :meth:`to_dict`.

        An unrecognized status is a hard error rather than a fallback to
        ``NOT_COLLECTED``: a reader that silently downgrades a status it does
        not understand would report "no evidence" for a package that
        genuinely carries evidence, which is a wrong answer dressed as a
        conservative one. ``check_reader_compatibility`` in ``versioning.py``
        is where a too-new package is supposed to be refused, with a message
        that says so.
        """
        raw_status = data.get("status")
        try:
            status = FactStatus(raw_status)
        except ValueError as exc:
            raise ValueError(
                f"unknown fact status {raw_status!r}; "
                "the package may have been written by a newer abicheck"
            ) from exc
        raw_confidence = data.get("confidence", Confidence.HIGH.value)
        try:
            confidence = Confidence(raw_confidence)
        except ValueError as exc:
            raise ValueError(f"unknown confidence {raw_confidence!r}") from exc
        return cls(
            status=status,
            producer=str(data.get("producer", "")),
            producer_version=str(data.get("producer_version", "")),
            recipe=str(data.get("recipe", "")),
            scope=str(data.get("scope", "")),
            confidence=confidence,
            diagnostics=tuple(str(d) for d in data.get("diagnostics", ())),
        )


#: Status severity order, worst last. ``narrowed`` picks the later of two.
#: ``NOT_APPLICABLE`` sits *below* ``PRESENT`` rather than at the top: a family
#: that does not apply to an artifact is not a gap, so a per-entity
#: "not applicable" must not drag a family-level ``PRESENT`` down to it, and a
#: family-level "not applicable" is superseded by an entity that really does
#: carry the fact.
_STATUS_ORDER: tuple[FactStatus, ...] = (
    FactStatus.NOT_APPLICABLE,
    FactStatus.PRESENT,
    FactStatus.PARTIAL,
    FactStatus.NOT_COLLECTED,
    FactStatus.UNSUPPORTED,
    FactStatus.FAILED,
)

_CONFIDENCE_ORDER: tuple[Confidence, ...] = (
    Confidence.HIGH,
    Confidence.REDUCED,
    Confidence.UNKNOWN,
)


def _worse_status(left: FactStatus, right: FactStatus) -> FactStatus:
    return max(left, right, key=_STATUS_ORDER.index)


def _worse_confidence(left: Confidence, right: Confidence) -> Confidence:
    return max(left, right, key=_CONFIDENCE_ORDER.index)


@dataclass
class AvailabilityLedger:
    """Family-level defaults plus per-entity overrides for one artifact.

    The two-level shape is what keeps availability affordable: a real
    artifact has a handful of fact families and potentially hundreds of
    thousands of entities, so stating availability once per family and
    overriding only where an entity genuinely differs is the difference
    between a few records and one per entity.

    Lookup is total by construction — :meth:`for_entity` always answers,
    falling back to :attr:`unknown_family_default` for a family nobody
    declared. That default is ``NOT_COLLECTED``, not ``PRESENT``: a family
    this ledger has never heard of is precisely the case where inferring
    availability would be a guess.
    """

    families: dict[str, FactAvailability] = field(default_factory=dict)
    #: ``(family, entity key) -> override``. The entity key is opaque here;
    #: callers pass whatever identity they index by (typically an
    #: ``EntityId.key``).
    overrides: dict[tuple[str, str], FactAvailability] = field(default_factory=dict)
    unknown_family_default: FactAvailability = field(
        default_factory=lambda: FactAvailability(FactStatus.NOT_COLLECTED)
    )

    def declare(self, family: str, availability: FactAvailability) -> None:
        """Set the family-level default. Last declaration wins."""
        self.families[family] = availability

    def override(
        self, family: str, entity_key: str, availability: FactAvailability
    ) -> None:
        """Record that one entity's availability differs from its family's."""
        self.overrides[family, entity_key] = availability

    def for_family(self, family: str) -> FactAvailability:
        return self.families.get(family, self.unknown_family_default)

    def for_entity(self, family: str, entity_key: str) -> FactAvailability:
        """Resolve one entity's availability.

        The family default is *narrowed* by the override rather than replaced
        by it, so an override cannot claim availability the family never had
        — see :meth:`FactAvailability.narrowed`.
        """
        base = self.for_family(family)
        override = self.overrides.get((family, entity_key))
        if override is None:
            return base
        return base.narrowed(override)

    def comparable_families(self) -> frozenset[str]:
        """Declared families a comparison may draw conclusions from."""
        return frozenset(
            name for name, avail in self.families.items() if avail.comparable
        )

    def missing_families(self, required: Iterable[str]) -> tuple[str, ...]:
        """Required families this ledger cannot support a conclusion for.

        Sorted, so a caller rendering the result into a diagnostic or a
        coverage row gets a stable message rather than one that depends on
        the caller's own iteration order.
        """
        return tuple(
            sorted(
                name for name in set(required) if not self.for_family(name).comparable
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical mapping form.

        Overrides are a *list* of records rather than a nested mapping: their
        natural key is the pair ``(family, entity)``, which no JSON object key
        can carry without inventing a separator that an entity name could
        itself contain. ADR-062 D5's rule is that a map is only used where its
        keys are genuinely unique and order-free.
        """
        return {
            "families": {
                name: avail.to_dict() for name, avail in sorted(self.families.items())
            },
            "overrides": [
                {
                    "family": family,
                    "entity": entity_key,
                    "availability": avail.to_dict(),
                }
                for (family, entity_key), avail in sorted(self.overrides.items())
            ],
            "unknown_family_default": self.unknown_family_default.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AvailabilityLedger:
        families = {
            str(name): FactAvailability.from_dict(raw)
            for name, raw in dict(data.get("families", {})).items()
        }
        overrides: dict[tuple[str, str], FactAvailability] = {}
        for raw in data.get("overrides", []):
            overrides[str(raw["family"]), str(raw["entity"])] = (
                FactAvailability.from_dict(raw["availability"])
            )
        raw_default = data.get("unknown_family_default")
        default = (
            FactAvailability.from_dict(raw_default)
            if raw_default is not None
            else FactAvailability(FactStatus.NOT_COLLECTED)
        )
        return cls(
            families=families, overrides=overrides, unknown_family_default=default
        )
