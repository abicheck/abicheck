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

"""One fact family's availability record — the leaf half of ADR-062 D3.

Split out of :mod:`abicheck.storage.availability` when that module crossed
the 800-line production cap, along the same line as
:mod:`abicheck.storage.entity_ids`: the record knows nothing about the
ledger that indexes it, while the ledger cannot be expressed without it.
Putting the leaf here and re-exporting keeps one import for a caller and
keeps the edge pointing one way.

:meth:`FactAvailability.narrowed` and its confidence rule live here too,
since combining two records is a question about records rather than about
the collection they were found in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .availability_status import (
    ASSERTS_NO_PRODUCER as _ASSERTS_NO_PRODUCER,
    COMPARABLE_STATUSES as _COMPARABLE_STATUSES,
    CONFIDENCE_ORDER as _CONFIDENCE_ORDER,
    Confidence,
    FactStatus,
    worse_confidence as _worse_confidence,
    worse_status as _worse_status,
)
from .guards import (
    diagnostics_from as _diagnostics_from,
    instance_of as _instance_of,
    provenance_text as _provenance_text,
    required_field as _required_field,
)

__all__ = ["FactAvailability"]


def _availability(raw: Any, field_name: str) -> None:
    """A stored record, checked at the door rather than where it is read.

    Thin wrapper over the shared guard so this module names its own record
    type once. See :func:`abicheck.storage.guards.instance_of` for why a
    record slot is checked where it is assigned.
    """
    _instance_of(raw, FactAvailability, field_name)


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
        """Validate a directly-constructed record, not only a parsed one.

        Every text guard in this module was first written for ``from_dict``,
        on the assumption that malformed input arrives as a parsed document.
        It also arrives from an adapter building these objects from
        dynamically sourced data, and the constructor reproduced both defects
        exactly: ``diagnostics="parse error"`` became eleven one-character
        diagnostics that ``to_dict`` then persisted, and ``recipe=1`` was
        stored as an int equal to no string (Codex review). Validating at the
        document boundary is not enough for a publicly constructible type.
        """
        if not isinstance(self.status, FactStatus):  # pragma: no cover - guard
            raise TypeError(f"status must be a FactStatus, got {self.status!r}")
        if not isinstance(self.confidence, Confidence):  # pragma: no cover - guard
            raise TypeError(f"confidence must be a Confidence, got {self.confidence!r}")
        object.__setattr__(
            self, "producer", _provenance_text(self.producer, "producer")
        )
        object.__setattr__(
            self,
            "producer_version",
            _provenance_text(self.producer_version, "producer_version"),
        )
        object.__setattr__(self, "recipe", _provenance_text(self.recipe, "recipe"))
        object.__setattr__(self, "scope", _provenance_text(self.scope, "scope"))
        object.__setattr__(
            self, "diagnostics", tuple(_diagnostics_from(self.diagnostics))
        )

    @property
    def comparable(self) -> bool:
        """Whether a comparison may draw a conclusion from this family.

        This is the single predicate ADR-062 D3 asks call sites to route
        through. It is deliberately *not* ``status is not FactStatus.FAILED``
        or any other negative spelling: adding a seventh status later must make
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
        # Identifying fields merge *field by field*, but they follow the
        # status that survives rather than always preferring the override.
        #
        # Two review rounds shaped this, and the order matters. First, an
        # earlier version selected the whole record — `other if other.producer
        # else self` — which lost information in both directions: an override
        # stating a narrower `scope` but no `producer` had that scope
        # discarded, describing coverage the entity never had; and an override
        # stating only a `producer` erased the inherited `recipe` and
        # `producer_version`, the fields that decide whether two `PRESENT`
        # records are interchangeable.
        #
        # The field-by-field merge that replaced it always let a non-empty
        # override value win, which is right when the override's status is
        # what survives, and wrong when it is not: narrowing
        # `FAILED(producer="dwarf")` with `PRESENT(producer="clang",
        # recipe="r1")` produced `FAILED(producer="clang", recipe="r1")` —
        # a dwarf parse failure attributed to clang, carrying clang's recipe
        # (Codex review). `NOT_COLLECTED` could likewise acquire a producer
        # while meaning that none ran.
        #
        # So provenance leads from whichever record's status won, and the
        # other fills only what the winner left blank. That keeps the earlier
        # fix intact — an override that narrows `PRESENT` to `PARTIAL` still
        # wins, and still inherits the family's `producer`/`recipe` — while
        # never naming a producer for a status it did not report.
        # One further clause, because the review's own second example survives
        # the rule above: `NOT_COLLECTED` narrowed by `PRESENT(producer=
        # "clang")` still inherited `clang`, since the winner left the field
        # blank. A status asserting that *nothing ran* must not acquire
        # provenance from the record it overrode — there is no producer, no
        # recipe and no scope to name. `UNSUPPORTED` and `FAILED` are
        # deliberately not in that set: a producer was asked and could not
        # answer, or ran and failed, so naming it is exactly the useful part.
        #
        # Residual, deliberately not closed here: a non-identifying field can
        # still cross between records naming different producers —
        # `FAILED(producer="dwarf")` narrowed by `PRESENT(producer="clang",
        # recipe="r1")` keeps `recipe="r1"`. Tightening that means "inherit
        # only when the two records agree on producer", which would also undo
        # the earlier reviewed fix in the opposite direction: that fix exists
        # precisely so an override stating only a new `producer` inherits the
        # family's `recipe`, on the grounds that `recipe` is what decides
        # whether two `PRESENT` records are interchangeable. Both arguments
        # have force, and this function has now been reshaped twice under
        # review; a third structural change picking a winner between them
        # belongs in its own pass with the decision recorded, not here. The
        # misattribution that mattered — naming the wrong *producer* for a
        # failure — is closed.
        # The operand, before the first attribute of it is read. Every
        # sibling that takes a record already checks one — `declare`,
        # `override`, `add`, `occurrences_of`, `is_ambiguous` — and this was
        # the one that did not, so `record.narrowed(None)` leaked
        # `AttributeError` from `other.status`. That is neither arm of the
        # `TypeError`/`ValueError` pair this package documents as "the
        # package is malformed", so a caller separating a corrupt package
        # from a broken reader read it as the second (Codex review).
        _availability(other, "other")
        status = _worse_status(self.status, other.status)
        winner, loser = (other, self) if other.status is status else (self, other)
        if status in _ASSERTS_NO_PRODUCER and loser.status is not status:
            # Only the record that did *not* report this status is blanked.
            # Without that clause the tie — both `NOT_COLLECTED`, one of them
            # naming a producer — dropped the stated producer in one operand
            # order and kept it in the other, so a merge's provenance depended
            # on which side it was written from (CodeRabbit review). The rule
            # this blanking exists for is "do not let provenance *cross* from
            # a record that made a different statement"; a record stating the
            # surviving status is stating its own, and there is nothing to
            # inherit from.
            loser = FactAvailability(status)
        return FactAvailability(
            status=status,
            producer=winner.producer or loser.producer,
            producer_version=winner.producer_version or loser.producer_version,
            recipe=winner.recipe or loser.recipe,
            scope=winner.scope or loser.scope,
            confidence=_merged_confidence(status, self, other),
            diagnostics=self.diagnostics
            + tuple(d for d in other.diagnostics if d not in self.diagnostics),
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

        A non-mapping is refused here rather than reaching ``.get`` and
        raising ``AttributeError`` from inside the parse. That is not
        cosmetic: a caller that catches malformed input catches ``TypeError``
        and ``ValueError``, so an ``AttributeError`` escaping this door reads
        as a crash rather than as "this package is malformed" — and the
        ledger reaches this method once per family and per override row, so
        one scalar in the wrong slot decided how the whole load failed
        (Codex review).
        """
        if not isinstance(data, Mapping):
            raise TypeError(
                "an availability record must be a mapping, not "
                f"{type(data).__name__} ({data!r})"
            )
        # `FactStatus(None)` already refused an absent status, but blamed it
        # on a newer writer ("unknown fact status None") when the document was
        # simply truncated. Same rule as its siblings: `to_dict` writes
        # `status` unconditionally, so absence is malformed and says so.
        raw_status = _required_field(data, "status", "an availability record")
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
            producer=_provenance_text(data.get("producer", ""), "producer"),
            producer_version=_provenance_text(
                data.get("producer_version", ""), "producer_version"
            ),
            recipe=_provenance_text(data.get("recipe", ""), "recipe"),
            scope=_provenance_text(data.get("scope", ""), "scope"),
            confidence=confidence,
            diagnostics=_diagnostics_from(data.get("diagnostics", ())),
        )


def _merged_confidence(
    status: FactStatus, left: FactAvailability, right: FactAvailability
) -> Confidence:
    """The confidence a merged record may claim.

    ``Confidence`` is documented as the weight a consumer may put on a
    ``PRESENT``/``PARTIAL`` fact, so a record carrying neither has no
    confidence worth propagating. Taking the worst of both unconditionally let
    a ``NOT_APPLICABLE`` family with ``UNKNOWN`` — a ledger saying "there is
    nothing here to be missing", not weak evidence — degrade a real
    ``PRESENT``/``HIGH`` entity override to ``UNKNOWN`` (Codex review).

    So when the surviving status *is* usable evidence, the confidence is the
    worst among the records that actually carry some. Two ``PRESENT`` records
    still narrow to the weaker of the two, which is the case the rule was
    written for; a record with no usable evidence simply stops voting on how
    much to trust one.

    When the surviving status is **not** usable evidence the old rule stands,
    deliberately. Confidence is inert on such a record, and "worst of both" is
    the reading that cannot overclaim — a `PRESENT`/`HIGH` narrowed by a
    `FAILED` must not leave a `FAILED` record advertising `HIGH`.
    """
    if status not in _COMPARABLE_STATUSES:
        return _worse_confidence(left.confidence, right.confidence)
    carrying = [record for record in (left, right) if record.comparable]
    return max((record.confidence for record in carrying), key=_CONFIDENCE_ORDER.index)
