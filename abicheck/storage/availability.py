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

The vocabulary itself — :class:`~abicheck.storage.availability_status.FactStatus`,
:class:`~abicheck.storage.availability_status.Confidence`, and the severity
orders that decide which of two survives a narrowing — lives next door in
:mod:`abicheck.storage.availability_status`, and is re-exported here so a
caller still has one import to reach for. This module owns the stored record
and the ledger that indexes it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .availability_status import (
    GAP_STATUSES as _GAP_STATUSES,
    Confidence,
    FactStatus,
)
from .fact_availability import FactAvailability, _availability
from .guards import (
    decision_key as _decision_key,
    key_collection as _key_collection,
    mapping as _mapping,
    required_field as _required_field,
    row_sequence as _row_sequence,
)

__all__ = [
    "AvailabilityLedger",
    "Confidence",
    "FactAvailability",
    "FactStatus",
]


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
        if name in ("families", "overrides"):
            # Stored as an owned `dict`, not as whatever mapping arrived. A
            # read-only `Mapping` passed every check here and then made the
            # documented mutators fail: `AvailabilityLedger(families=
            # MappingProxyType({}))` constructed, and `declare` raised
            # `TypeError: 'mappingproxy' object does not support item
            # assignment` (Codex review). A container admitted at the
            # assignment boundary has to support the operations this class
            # advertises.
            #
            # Copying also stops the caller's own mapping aliasing the state:
            # mutating it afterwards used to reach straight past every guard
            # above. `StorageVersions` answers the same question by freezing,
            # because its records are immutable; this one is mutable by
            # design — `declare`/`override` are its API — so it owns a copy
            # instead.
            #
            # Sorted, so the *state* is canonical and not merely its views.
            # Two producers declaring identical records in different orders
            # built ledgers that compared equal and serialized identically
            # while their `repr`s differed, making a diagnostic depend on
            # collection order (Codex review). This is the third time this
            # branch has broken the same invariant — `OccurrenceSet` kept
            # insertion order behind a sorted `__iter__`, `StorageVersions`
            # normalized only in `to_dict` — so the rule is worth stating
            # plainly: for a value type, canonicalize the state, never the
            # view. `__eq__` happened to agree here only because dict
            # equality ignores key order; `repr` had nothing to hide behind.
            value = dict(sorted(value.items()))
        object.__setattr__(self, name, value)

    def declare(self, family: str, availability: FactAvailability) -> None:
        """Set the family-level default. Last declaration wins.

        The family name is validated for the same reason ``from_dict``
        validates it: a non-string key coerces to one that collides with a
        real family. Last-wins itself is unchanged — see :meth:`override` for
        why that contract is left to whoever owns it.
        """
        _availability(availability, f"availability for family {family!r}")
        # Reassigned rather than mutated in place, so the sort in
        # `__setattr__` actually runs — an in-place write appends at the end
        # and leaves the state in collection order, which is exactly the
        # defect the sort exists to close.
        self.families = {
            **self.families,
            _decision_key(family, "family name"): availability,
        }

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
        # Reassigned for the reason `declare` gives.
        self.overrides = {**self.overrides, (family, entity_key): availability}

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
        """Resolve a family's availability, declared or not.

        The lookup key is validated, not merely used. Every *write* door —
        the constructor, ``from_dict``, ``declare``, ``override`` — already
        refuses a non-string key, but a read door that skips the check does
        not fail safely: a key that can never match resolves through the
        fallback or past an override, and the answer it returns can license
        a conclusion the ledger does not support (Codex review).
        """
        _decision_key(family, "family")
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

        Both halves of the lookup key are validated for the reason
        :meth:`for_family` gives. This one is the sharper case: with a
        ``PRESENT`` family and a ``FAILED`` override stored under
        ``("layout", "1")``, ``for_entity("layout", 1)`` missed the override
        and answered ``PRESENT``/comparable — the failed evidence silently
        skipped rather than reported.
        """
        # Both halves checked here rather than leaning on the `for_family`
        # call below: that path is reached only when no override matches, so
        # relying on it would make the family check conditional on the very
        # lookup it is meant to validate.
        _decision_key(family, "family")
        _decision_key(entity_key, "entity")
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

        The collection itself is checked before it is iterated. A bare
        ``str`` satisfies every per-item key check while yielding
        characters, so this reported six families that do not exist and
        omitted the one that does — a coverage check answering confidently
        about the wrong thing.
        """
        _key_collection(required, "required")
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
        # Required, not defaulted. `to_dict` writes both collections
        # unconditionally, so an absent one means the document did not come
        # from this writer — and defaulting it silently asserted the one
        # thing this package exists to stop asserting: that the producer ran
        # and established there is nothing here (`AGENTS.md` invariant 3).
        # A truncated ledger keeping a `PRESENT` family while losing its
        # override rows then answered `for_entity` with the comparable
        # family record, licensing a compatibility conclusion from damage
        # (Codex review). Codex named `overrides`; `families` is the same
        # shape, where the loss instead reads as "no family was declared"
        # and falls through to the unknown-family default.
        #
        # `unknown_family_default` is deliberately *not* required: absent, it
        # resolves to `NOT_COLLECTED`, which is a gap status — the explicit
        # "we do not know" that invariant 3 asks for, not a default standing
        # in for evidence.
        raw_families = _required_field(data, "families", "an availability ledger")
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
        for raw in _row_sequence(
            _required_field(data, "overrides", "an availability ledger"), "overrides"
        ):
            # Each *row* is checked, not only the array holding them. The
            # outer guard above says nothing about what the rows are, and the
            # identifying fields are read here by subscript — so a
            # `__getitem__`-only row supplied its family and entity, was
            # accepted, and got reserialized as valid storage, while the
            # `FactAvailability.from_dict` call below (which does guard) never
            # got the chance to refuse it (Codex review).
            #
            # The preceding round guarded every `from_dict`'s own parameter
            # and made that executable; this is the level underneath, which
            # that sweep did not reach. `test_no_from_dict_reads_a_row_field_
            # without_guarding_the_row` now covers both levels.
            _mapping(raw, "an override document")
            key = (
                _decision_key(
                    _required_field(raw, "family", "an override document"),
                    "override family",
                ),
                _decision_key(
                    _required_field(raw, "entity", "an override document"),
                    "override entity",
                ),
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
            overrides[key] = FactAvailability.from_dict(
                _required_field(raw, "availability", "an override document")
            )
        # Required too, reversing the previous round's call. That round
        # argued absence is safe here because it resolves to `NOT_COLLECTED`,
        # a gap status that claims nothing — true, but a weaker rule than the
        # one actually available: `to_dict` writes this key unconditionally,
        # so its absence means the document is not this writer's output.
        # A `null` value is still accepted below and still reads as unstated;
        # what is refused is the key being gone.
        raw_default = _required_field(
            data, "unknown_family_default", "an availability ledger"
        )
        default = (
            FactAvailability.from_dict(raw_default)
            if raw_default is not None
            else FactAvailability(FactStatus.NOT_COLLECTED)
        )
        return cls(
            families=families, overrides=overrides, unknown_family_default=default
        )
