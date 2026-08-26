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

from .guards import (
    decision_key as _decision_key,
    diagnostics_from as _diagnostics_from,
    instance_of as _instance_of,
    mapping as _mapping,
    provenance_text as _provenance_text,
)

__all__ = [
    "AvailabilityLedger",
    "Confidence",
    "FactAvailability",
    "FactStatus",
]


class FactStatus(enum.Enum):
    """Why a fact family is, or is not, present in stored evidence.

    Deliberately six *distinct* members rather than a boolean plus a note:
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

#: Statuses that mean "evidence is missing for a reason". Deliberately not the
#: complement of :data:`_COMPARABLE_STATUSES`: ``NOT_APPLICABLE`` is neither
#: usable evidence nor a gap — it is a ledger stating that there is nothing
#: here to be missing, which is an answer rather than an absence.
_GAP_STATUSES = frozenset(
    {FactStatus.NOT_COLLECTED, FactStatus.UNSUPPORTED, FactStatus.FAILED}
)

#: The status asserting that no producer ran, so a merge must not attach one.
#: Distinct from :data:`_GAP_STATUSES` on purpose: ``UNSUPPORTED`` and
#: ``FAILED`` are gaps *with* a producer worth naming — one was asked and
#: could not answer, or ran and failed.
#:
#: ``NOT_APPLICABLE`` was in this set for one round and was removed after a
#: test written for it failed. It reads like a member ("nothing to run, so
#: nobody ran"), but it is the *least* worse status in ``_STATUS_ORDER``, so
#: it can only survive a merge against itself — and then the other record is
#: also ``NOT_APPLICABLE``, whose provenance is a peer's legitimate statement
#: rather than something inherited across a disagreement. Blanking it would
#: discard information, which is the one direction this package may not err
#: in. Kept as a set rather than a scalar because the reasoning is about
#: *which* statuses qualify, and a future one might.
_ASSERTS_NO_PRODUCER = frozenset({FactStatus.NOT_COLLECTED})


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
            confidence=_worse_confidence(self.confidence, other.confidence),
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
            producer=_provenance_text(data.get("producer", ""), "producer"),
            producer_version=_provenance_text(
                data.get("producer_version", ""), "producer_version"
            ),
            recipe=_provenance_text(data.get("recipe", ""), "recipe"),
            scope=_provenance_text(data.get("scope", ""), "scope"),
            confidence=confidence,
            diagnostics=_diagnostics_from(data.get("diagnostics", ())),
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

    That fallback must never be *comparable*, and this is enforced rather
    than assumed (Codex review). ``from_dict`` reads the field from the
    stored package, so a malformed or hand-edited ledger stating
    ``unknown_family_default: {status: present}`` made every undeclared
    family read as usable and ``missing_families`` report no gap at all —
    one field silently defeating the rule this whole module exists for,
    across every family at once. A non-comparable fallback is still freely
    choosable (``NOT_APPLICABLE`` for an artifact kind where most families
    are meaningless, ``UNSUPPORTED`` for a producer that cannot answer);
    only the two statuses that license a conclusion are refused.
    """

    families: dict[str, FactAvailability] = field(default_factory=dict)
    #: ``(family, entity key) -> override``. The entity key is opaque here;
    #: callers pass whatever identity they index by (typically an
    #: ``EntityId.key``).
    overrides: dict[tuple[str, str], FactAvailability] = field(default_factory=dict)
    unknown_family_default: FactAvailability = field(
        default_factory=lambda: FactAvailability(FactStatus.NOT_COLLECTED)
    )

    def __setattr__(self, name: str, value: Any) -> None:
        """Validate a field wherever it is assigned, construction included.

        The initial mappings bypassed every key check `from_dict`, `declare`
        and `override` apply, so `AvailabilityLedger(families={1: failed,
        "1": present})` was accepted: it reported the string family as
        comparable while the failed row stayed reachable only through the int
        spelling, and `to_dict` then raised while sorting mixed keys — a
        ledger that lookup and serialization handled inconsistently (Codex
        review).

        The container itself is checked before its keys, because iterating is
        not the same question as indexing: `families=["layout"]` yields a
        perfectly valid family *name* from a list, so every key check passed
        and the ledger constructed — then `for_family` and `to_dict` raised
        `AttributeError` on the missing `get`/`items` (Codex review). Values
        are checked one step further in for the same reason: nothing reads a
        family's record until a decision needs it, so a non-record value
        surfaces from inside `narrowed`/`comparable` rather than at the door
        that accepted it.

        This lives in `__setattr__` rather than `__post_init__` because this
        is a mutable dataclass and the fields are public: `ledger.
        unknown_family_default = "bad"` reproduced the record-slot defect
        after construction, past guards that only ran once (Codex review).
        Every assignment is the same door.

        **Residual, stated rather than closed:** mutating *inside* an already
        validated mapping (`ledger.families["x"] = "bad"`) still bypasses
        this, since only the rebinding is observable here. Closing it needs a
        validating mapping type rather than a `dict`, which is a change to
        the field's public type; `declare` and `override` are the supported
        mutators and both validate.
        """
        if name == "families":
            _mapping(value, "families")
            for family, availability in value.items():
                _decision_key(family, "family name")
                _availability(availability, f"families[{family!r}]")
        elif name == "overrides":
            _mapping(value, "overrides")
            for key, availability in value.items():
                if not isinstance(key, tuple) or len(key) != 2:
                    raise TypeError(
                        f"override key must be a (family, entity) pair, got {key!r}"
                    )
                _decision_key(key[0], "override family")
                _decision_key(key[1], "override entity")
                _availability(availability, f"overrides[{key!r}]")
        elif name == "unknown_family_default":
            _availability(value, "unknown_family_default")
            if value.comparable:
                raise ValueError(
                    "unknown_family_default must not be comparable "
                    f"(got {value.status.value!r}): a family no availability "
                    "record mentions cannot license a conclusion"
                )
        object.__setattr__(self, name, value)

    def declare(self, family: str, availability: FactAvailability) -> None:
        """Set the family-level default. Last declaration wins.

        The family name is validated for the same reason ``from_dict``
        validates it: a non-string key coerces to one that collides with a
        real family. Last-wins itself is unchanged — see :meth:`override` for
        why that contract is left to whoever owns it.
        """
        _availability(availability, f"availability for family {family!r}")
        self.families[_decision_key(family, "family name")] = availability

    def override(
        self, family: str, entity_key: str, availability: FactAvailability
    ) -> None:
        """Record that one entity's availability differs from its family's.

        Refuses a key already recorded. Two extraction passes calling this for
        one entity is a real disagreement, and plain assignment discarded the
        first: a `FAILED` observation followed by `PRESENT` made
        :meth:`for_entity` comparable and dropped the failure from
        :meth:`to_dict` permanently, while the reverse call order reached the
        opposite conclusion — producer traversal order deciding availability
        (Codex review).

        This is the same rule :meth:`from_dict` applies to duplicate override
        *rows*, at the in-memory door rather than the document one, and
        rejecting rather than combining is deliberate for the same reason
        recorded there: :meth:`FactAvailability.narrowed` is symmetric in
        status and confidence but not in the identifying fields, so a merged
        record's producer and recipe would still depend on which call came
        first. Refusing removes the order dependence; combining only moves it.

        :meth:`declare` is deliberately *not* changed to match. It is
        documented "last declaration wins", which is a stated contract rather
        than an accident, and it carries the same hazard — a later `PRESENT`
        can bury an earlier `FAILED` for a whole family. Changing a documented
        behaviour is a decision for whoever owns that contract, so it is
        flagged here rather than altered in passing.
        """
        family = _decision_key(family, "override family")
        entity_key = _decision_key(entity_key, "override entity")
        # Before the duplicate check: a malformed argument is wrong whatever
        # the ledger already holds, and reporting the duplicate first would
        # answer a question the caller did not get to ask.
        _availability(
            availability, f"availability for override {(family, entity_key)!r}"
        )
        if (family, entity_key) in self.overrides:
            raise ValueError(
                f"an availability override for family {family!r} and entity "
                f"{entity_key!r} is already recorded; overwriting it would let "
                "the order two extraction passes ran in decide availability"
            )
        self.overrides[family, entity_key] = availability

    @property
    def effective_unknown_family_default(self) -> FactAvailability:
        """The fallback as it actually behaves, not as the field holds it.

        The fallback is re-checked rather than trusted. ``__post_init__``
        already refuses a comparable one, but this ledger is mutable by
        design (``declare``/``override`` mutate it), so the field can also be
        reassigned after construction — a plain attribute assignment runs no
        ``__post_init__``. Coercing — rather than raising — keeps the
        guarantee total at the point a comparison actually asks, and errs
        toward "no conclusion", which is the safe direction.

        This is a property rather than a branch inside :meth:`for_family`
        because lookup was not the only consumer, and the three that existed
        disagreed: the constructor refused a comparable fallback,
        :meth:`for_family` coerced one, and :meth:`to_dict` wrote the raw
        field — so a ledger reassigned to ``PRESENT`` answered
        ``not_collected`` while serializing ``present``, producing a document
        that :meth:`from_dict` then refused to reload, and that a consumer
        without that validation would read as available evidence (Codex
        review). One definition, every consumer, is the same rule the
        versioning axes needed for the same reason: a second, differently
        placed notion of the same fact is what lets two of them drift.
        """
        fallback = self.unknown_family_default
        if fallback.comparable:
            return FactAvailability(FactStatus.NOT_COLLECTED)
        return fallback

    def for_family(self, family: str) -> FactAvailability:
        """Resolve a family's availability, declared or not."""
        declared = self.families.get(family)
        if declared is not None:
            return declared
        return self.effective_unknown_family_default

    def for_entity(self, family: str, entity_key: str) -> FactAvailability:
        """Resolve one entity's availability.

        The family default is *narrowed* by the override rather than replaced
        by it, so an override cannot claim availability the family never had
        — see :meth:`FactAvailability.narrowed`.

        For an **undeclared** family, a ``NOT_APPLICABLE`` fallback has its
        *status* substituted before narrowing, and that narrow scope is the
        point. ``NOT_APPLICABLE`` is the only permitted fallback that sits
        *below* ``PRESENT`` in the status order — deliberately, so an entity
        that genuinely carries a fact supersedes a family declared
        inapplicable. That supersession rule is sound only when a record
        actually says "inapplicable"; with no record at all it became an
        upgrade path, letting an override manufacture availability for a
        family nobody declared.

        The substitution is conditioned on the override actually being an
        upgrade — ``override.comparable`` — because it is only upgrades the
        guard exists to block. Applying it unconditionally made an override
        that *agrees* with the fallback contradict it: a ``NOT_APPLICABLE``
        fallback with a ``NOT_APPLICABLE`` override resolved to
        ``NOT_COLLECTED``, so :meth:`for_family` said "nothing here to be
        missing" while :meth:`for_entity` reported a gap for the same
        explicit status (Codex review). That is the same conflation
        :meth:`missing_families` had, reached from the other side, and this
        function's own reasoning below already ruled it out — the guard was
        broader than the argument for it.

        ``NOT_COLLECTED``, ``UNSUPPORTED`` and ``FAILED`` need no special
        case for the same reason: each already ranks *worse* than
        ``PRESENT``, so ``narrowed`` blocks the upgrade on its own. A first version of this guard replaced
        the base with a bare ``NOT_COLLECTED`` for every undeclared family,
        which blocked the upgrade but also discarded the fallback's own
        status, producer and diagnostics — a ``FAILED`` fallback carrying a
        parse error resolved to an unannotated ``NOT_COLLECTED`` while
        :meth:`for_family` still reported the real failure (Codex review).
        Substituting only the one status that can be upgraded, via
        :func:`dataclasses.replace`, keeps every other field intact.

        The guard belongs here rather than in
        :meth:`FactAvailability.narrowed` because this is the only layer that
        can tell a declared status from a fallback: ``narrowed`` sees two
        records and cannot know whether the first came from a declaration or
        from a default.
        """
        override = self.overrides.get((family, entity_key))
        if override is None:
            return self.for_family(family)
        base = self.for_family(family)
        if (
            family not in self.families
            and base.status is FactStatus.NOT_APPLICABLE
            and override.comparable
        ):
            base = replace(base, status=FactStatus.NOT_COLLECTED)
        return base.narrowed(override)

    def comparable_families(self) -> frozenset[str]:
        """Declared families a comparison may draw conclusions from."""
        return frozenset(
            name for name, avail in self.families.items() if avail.comparable
        )

    def missing_families(self, required: Iterable[str]) -> tuple[str, ...]:
        """Required families whose evidence is *missing*, per :data:`_GAP_STATUSES`.

        A family explicitly declared ``NOT_APPLICABLE`` is not reported. A
        generic evidence profile naturally requires families that do not apply
        to every artifact — vtable facts for a C-only library — and the point
        of recording ``NOT_APPLICABLE`` is to answer that, not to defer it. A
        predicate of ``not comparable`` folded the two together and produced a
        coverage gap for a ledger that had explicitly established none (Codex
        review).

        Worth recording how this one hid: the distinction already existed in
        this module's own tests, spelled exactly this way and with a comment
        saying why, while the production predicate kept asking the weaker
        question. The test module now imports :data:`_GAP_STATUSES` from here
        rather than restating it, so the two cannot drift apart again.

        An *undeclared* required family is still reported, because
        :meth:`for_family` resolves it through the fallback, which cannot be
        comparable and defaults to ``NOT_COLLECTED``. Only an explicit
        declaration can say "not applicable".

        Sorted, so a caller rendering the result into a diagnostic or a
        coverage row gets a stable message rather than one that depends on
        the caller's own iteration order.
        """
        return tuple(
            sorted(
                name
                for name in set(required)
                if self.for_family(name).status in _GAP_STATUSES
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
            # The *effective* fallback, so the document says what this ledger
            # answers. Writing the raw field let a post-construction
            # reassignment serialize a comparable fallback that `for_family`
            # would never return — see `effective_unknown_family_default`.
            "unknown_family_default": self.effective_unknown_family_default.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AvailabilityLedger:
        if not isinstance(data, Mapping):
            raise TypeError(
                f"an availability ledger must be a mapping, not "
                f"{type(data).__name__} ({data!r})"
            )
        raw_families = data.get("families", {})
        if not isinstance(raw_families, Mapping):
            # `dict()` accepts a sequence of pairs and collapses duplicate
            # names *before* any key validation runs, so rows declaring one
            # family `failed` then `present` resolved as comparable while the
            # reverse order resolved as non-comparable — serialized order
            # discarding failed evidence and deciding whether a conclusion is
            # licensed (Codex review).
            #
            # This is the previous round's finding entering through a
            # different door: rejecting non-string *keys* does nothing when
            # the container itself dedupes first. The shape has to be checked
            # before the keys are.
            raise TypeError(
                f"families must be a mapping, not {type(raw_families).__name__} "
                f"({raw_families!r}); a sequence of pairs would let duplicate "
                "family names collapse before validation, with row order "
                "deciding which record survives"
            )
        families = {
            _decision_key(name, "family name"): FactAvailability.from_dict(raw)
            for name, raw in raw_families.items()
        }
        overrides: dict[tuple[str, str], FactAvailability] = {}
        for raw in data.get("overrides", []):
            key = (
                _decision_key(raw["family"], "override family"),
                _decision_key(raw["entity"], "override entity"),
            )
            if key in overrides:
                # Rejected rather than resolved. Assignment silently kept the
                # last row, so a ledger holding both a `failed` and a
                # `present` override for one entity answered differently
                # depending on which came first in the array — reversing it
                # flipped `for_entity` from non-comparable to comparable and
                # could license a compatibility conclusion (Codex review).
                #
                # Combining them conservatively was the alternative, and it
                # does not actually remove the order dependence: `narrowed`
                # is symmetric in status and confidence but not in the
                # identifying fields, where a non-empty override value wins,
                # so the merged producer/recipe would still depend on row
                # order. Refusing is consistent with how this module already
                # treats malformed *availability* data — `FactAvailability.
                # from_dict` raises on an unknown status rather than
                # downgrading it — as distinct from the informational version
                # axes, which parse defensively because no decision reads
                # them.
                raise ValueError(
                    f"duplicate availability override for family {key[0]!r} "
                    f"and entity {key[1]!r}; availability must not depend on "
                    "the order rows were serialized in"
                )
            overrides[key] = FactAvailability.from_dict(raw["availability"])
        raw_default = data.get("unknown_family_default")
        default = (
            FactAvailability.from_dict(raw_default)
            if raw_default is not None
            else FactAvailability(FactStatus.NOT_COLLECTED)
        )
        return cls(
            families=families, overrides=overrides, unknown_family_default=default
        )
