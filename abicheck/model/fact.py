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

"""``Fact[T]`` — one representation of "do we know this, and how" (ADR-063 D2/Phase 0).

A detector must not be able to observe a field's *value* without first
observing its *availability*. Before this type, availability was folded
into the value itself — ``None``, ``[]``, or a boolean reliability flag all
had to mean both "confirmed absent/false" and "not collected", and a
detector reading the raw field could not tell which. ``Fact[T]`` makes
that distinction a first-class part of the type instead of a convention a
reader has to already know about.

``Fact[T]`` has three fields, not two: ``status``, ``value``, and
``diagnostics``. A two-field design (status + value) cannot hold a
diagnostic without either violating the declared ``T`` or silently
dropping it — ``diagnostics`` is where a producer records *why* (e.g.
"depth capped at headers-only") without that becoming a smuggled value.

There is deliberately no ``Fact.absent_confirmed()``: per ``FactStatus.
PRESENT``'s own meaning, a confirmed absence is ``PRESENT`` carrying an
empty/``None`` value — ``Fact.present(None)``/``Fact.present([])`` — not a
distinct status. That is the *one* legitimate way to spell "present,
empty"; a bare sentinel construction a reader could mistake for "not
collected" is not offered.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar

from .availability import FactStatus

__all__ = ["Fact", "replace_with_fact_sync"]

T = TypeVar("T")


class _Omitted:
    """A unique marker object, never equal to any real field value by identity.

    Used as a dataclass field's own default (via ``cast()`` to that field's
    real type, so the declared type never widens) to distinguish "caller
    never touched this field" from "caller explicitly passed the value that
    happens to be the field's normal resting value" (``False``, ``[]``,
    ``None``) — see the owning dataclass's own ``__post_init__`` for the
    bridge that resolves this against the field's ``Fact[T]`` sibling.
    """

    __slots__ = ()


def bridge_legacy_and_fact(
    legacy: T, explicit_fact: Fact[T] | None, omitted: T, normalized_default: T
) -> tuple[T, Fact[T]]:
    """Resolve a legacy-field/``Fact[T]``-sibling compatibility bridge.

    Called from an owning dataclass's ``__post_init__`` for one field pair.
    ``legacy`` is the field's value as constructed (either a real value, or
    ``omitted`` — the field's own private sentinel, identity-compared, if the
    caller never supplied it). ``explicit_fact`` is the sibling ``Fact[T]``
    field's value (``None`` if the caller didn't supply that either).

    Whichever of the two the caller actually supplied is authoritative and
    is written back to *both* representations, so they cannot disagree
    afterward: an explicit ``Fact[T]`` (even one asserting no evidence, and
    even alongside a legacy value that looks inconsistent with it — the
    caller's stated availability is trusted over a value that may itself be
    stale/placeholder) overwrites the legacy field; a genuinely-omitted
    legacy field with no explicit ``Fact[T]`` backfills to
    ``Fact.not_collected()``; an explicitly-supplied legacy value with no
    competing ``Fact[T]`` backfills to ``Fact.present(legacy)``.

    This is deliberately *not* "whichever value looks newer" — there is no
    such signal available here, by construction. ``dataclasses.replace(obj,
    legacy=new_value)`` re-invokes this same ``__init__`` path with *every*
    field of ``obj``, including its own already-resolved
    ``explicit_fact`` sibling carried forward unchanged, which is
    indistinguishable, from inside ``__post_init__``, from a fresh
    construction genuinely supplying both fields together (Codex review,
    both directions independently confirmed against real repros: trusting
    the Fact unconditionally silently reverts an ordinary
    ``replace(obj, bases=new)`` caller's update; trusting the *legacy* value
    on disagreement instead silently discards an explicit
    ``RecordType(bases=["old"], bases_fact=Fact.not_collected())`` construction's
    stated availability). Neither direction can be made safe by inference
    alone — this function does not attempt to guess.

    **Use :func:`replace_with_fact_sync` instead of a raw
    ``dataclasses.replace()`` call for any of these fields.** It closes the
    gap the un-decidable case above cannot: for a legacy field named in the
    update whose ``Fact[T]`` sibling isn't *also* explicitly given, it
    derives ``Fact.present(new_value)`` and passes both into ``replace()``,
    so the two representations cannot drift out of sync at the one call
    site capable of keeping them honest — the one making the change.

    **A second, related trap this function cannot see either (Codex
    review, confirmed to already reproduce identically on ``bases`` — not
    something a later conversion introduces): plain post-construction
    attribute mutation.** ``record.is_final = True`` (or ``record.bases =
    [...]``) never re-invokes ``__post_init__`` at all — this bridge only
    runs at construction time, so the sibling ``Fact[T]`` is never
    re-derived, and the pair is left holding the *new* legacy value beside
    the *stale* fact. Both directions of the earlier gap are true again for
    the identical reason: the pair is now internally inconsistent, and
    ``encode_fact_fields``/``decode_fact`` (``storage/fact_codec.py``) trust
    the ``Fact[T]`` sibling over the legacy field on the next
    encode-then-decode round trip, silently reverting the mutation.
    ``replace_with_fact_sync`` does not help here — it only wraps
    ``dataclasses.replace()`` calls, not attribute assignment. There is no
    mechanical guard against this today; treat every bridged field
    (anything with a ``<field>_fact`` sibling) as effectively immutable
    after construction — build a fresh instance (or use
    ``replace_with_fact_sync``) instead of assigning to it directly.
    """
    if explicit_fact is not None:
        value = (
            explicit_fact.value
            if explicit_fact.value is not None
            else normalized_default
        )
        return value, explicit_fact
    if legacy is omitted:
        return normalized_default, Fact.not_collected()
    return legacy, Fact.present(legacy)


def replace_with_fact_sync(obj: T, **updates: object) -> T:
    """``dataclasses.replace(obj, **updates)``, safe for ``Fact[T]``-bridged fields.

    See :func:`bridge_legacy_and_fact`'s docstring for why a raw
    ``dataclasses.replace()`` call is unsafe whenever ``updates`` touches a
    legacy field with a ``<field>_fact`` sibling but not the sibling itself:
    ``__post_init__`` cannot tell the old, carried-forward sibling apart
    from a fresh, deliberate one, and it must resolve that ambiguity in
    *some* direction — the one this bridge picks is "trust an explicit
    Fact", which means the stale sibling silently wins and the caller's
    update to the legacy field is lost.

    This wrapper closes that gap for exactly this call site — the one
    place a caller's actual intent (updating the legacy field's real value)
    is still known — without changing the bridge's own inference rule
    anywhere else: for every keyword in ``updates`` naming a legacy field
    that has a ``<field>_fact`` attribute on ``obj`` and whose sibling
    ``<field>_fact`` is *not itself* also present in ``updates``, it derives
    ``Fact.present(value)`` and adds it to the call — so the two
    representations are supplied together and cannot disagree. A caller
    that already knows the fact it wants (e.g. downgrading to
    ``Fact.not_collected()``) still passes ``<field>_fact=`` explicitly, and
    that explicit value is never second-guessed here.
    """
    resolved = dict(updates)
    for name, value in updates.items():
        fact_name = f"{name}_fact"
        if fact_name in resolved or not hasattr(obj, fact_name):
            continue
        resolved[fact_name] = Fact.present(value)
    return replace(obj, **resolved)  # type: ignore[type-var]


@dataclass(frozen=True)
class Fact(Generic[T]):
    """A value paired with why we do or don't have it.

    ``value_or(default)`` is **not** a detector-safe way to read this — it
    is reserved for non-semantic presentation code (a report renderer
    choosing a display fallback), where collapsing "not collected" and
    "confirmed absent" into the same rendered text is an acceptable UI
    simplification, not a detection decision. ``old.vtable_fact.value_or([])
    != new.vtable_fact.value_or([])`` reintroduces, by a different
    spelling, the exact ambiguity this type exists to make unrepresentable.
    A detector reads a ``Fact[...]``-typed field only by inspecting
    ``.status`` (typically via a full ``match``/``if`` over every
    ``FactStatus`` member).

    ``__bool__`` is defined to raise: plain absence of ``__bool__`` would
    leave every instance truthy regardless of status, silently defeating
    the no-implicit-truthiness invariant this type exists to enforce.

    ``__eq__``/``__ne__`` are **not** overridden — they stay the ordinary,
    dataclass-generated structural comparison. ``Fact[T]`` is itself a
    field on ``RecordType``/``Param``/every other fact-bearing dataclass; a
    raising ``__eq__`` on a *field* would poison the *containing*
    dataclass's own generated ``__eq__`` the instant comparison reaches
    that field, so two otherwise-identical ``RecordType`` instances would
    raise on an ordinary equality check instead of comparing. The guard
    against comparing two ``Fact[...]`` values directly *inside detector
    logic* (rather than unwrapping first) is a static check, not a runtime
    one — see ``scripts/check_ai_readiness.py``'s ``fact-detector-misuse``
    check.
    """

    status: FactStatus
    value: T | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        raise TypeError(
            "Fact[T] has no truth value — read .is_present or .value_or(...)"
        )

    @property
    def is_present(self) -> bool:
        """``True`` for ``PRESENT``/``PARTIAL`` — usable evidence exists."""
        return self.status in (FactStatus.PRESENT, FactStatus.PARTIAL)

    def value_or(self, default: T) -> T:
        """Read the value, or ``default`` if not present. Not detector-safe.

        See the class docstring: reserved for presentation code, never for
        a detection decision — collapsing "not collected" and "confirmed
        absent/empty" to the same default is exactly the ambiguity
        ``Fact[T]`` exists to make unrepresentable in detector logic.
        """
        if self.is_present and self.value is not None:
            return self.value
        if self.is_present:
            # PRESENT/PARTIAL with a legitimately-None/empty value: the
            # caller-supplied value *is* T here (e.g. a confirmed-empty
            # list), so returning it (rather than `default`) is correct.
            return self.value  # type: ignore[return-value]
        return default

    @classmethod
    def present(cls, value: T, *diagnostics: str) -> Fact[T]:
        """Usable evidence — including a confirmed-empty/None value."""
        return cls(status=FactStatus.PRESENT, value=value, diagnostics=diagnostics)

    @classmethod
    def partial(cls, value: T, *diagnostics: str) -> Fact[T]:
        """Usable evidence covering only part of the requested scope."""
        return cls(status=FactStatus.PARTIAL, value=value, diagnostics=diagnostics)

    @classmethod
    def not_collected(cls, *diagnostics: str) -> Fact[T]:
        """The producer was never invoked for this family."""
        return cls(status=FactStatus.NOT_COLLECTED, value=None, diagnostics=diagnostics)

    @classmethod
    def unsupported(cls, *diagnostics: str) -> Fact[T]:
        """This producer cannot express this family at all."""
        return cls(status=FactStatus.UNSUPPORTED, value=None, diagnostics=diagnostics)

    @classmethod
    def failed(cls, reason: str, *more_diagnostics: str) -> Fact[T]:
        """The producer was invoked and errored."""
        return cls(
            status=FactStatus.FAILED,
            value=None,
            diagnostics=(reason, *more_diagnostics),
        )

    @classmethod
    def not_applicable(cls, *diagnostics: str) -> Fact[T]:
        """The family is meaningless for this artifact kind."""
        return cls(
            status=FactStatus.NOT_APPLICABLE, value=None, diagnostics=diagnostics
        )
