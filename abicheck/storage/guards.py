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

"""The value guards every storage module applies at its own doors.

Each of these was written once per module, because the modules are leaves
that deliberately share nothing — and three copies of one rule is how the
rule drifts. Review found that drift four separate times on this branch,
always as one site missing a check its siblings already had, so `AGENTS.md`
invariant 6 recorded the plan to unify them once there was a leaf to hold
them. This is that leaf.

They stay separate *functions* rather than one `text()` taking a message: the
reason a field may not be coerced differs per field — an identity collapses
two occurrences into one, a decision key collapses two records with iteration
order picking the survivor, a provenance field makes incomparable evidence
look comparable — and that reason is what a future reader needs. The type
check is one line either way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Deliberately *not* re-exported from the package. These are the doors'
#: internal instruments, not part of what a consumer of `abicheck.storage`
#: reads or writes — but they carry an `__all__` anyway so the same
#: landed-surface check that pins the four public modules pins this one too,
#: and a new guard cannot appear here unadvertised.
__all__ = [
    "decision_key",
    "enum_member",
    "instance_of",
    "diagnostics_from",
    "identity_text",
    "mapping",
    "provenance_text",
]


def identity_text(value: Any, field_name: str) -> str:
    """An identity-bearing field, rejected rather than coerced if not a string.

    ``str()`` looks harmless on a field that is a string in every well-formed
    document, and it is not: ``1`` and ``"1"`` are two distinct values in a
    JSON document that both become ``"1"`` here, so two genuinely different
    occurrences produce one key and :meth:`OccurrenceSet.add` drops the second
    as an exact duplicate (Codex review). That is this module's one invariant
    — never discard an observation — defeated by a type coercion at the
    document boundary rather than by any logic in the set.

    Rejecting matches what the neighbouring primitives already do with
    malformed identity-bearing input: ``canonical_form`` refuses a non-string
    mapping key, and ``FactAvailability.from_dict`` raises on an unknown
    status rather than downgrading it. The informational version axes parse
    defensively instead, and the distinction is deliberate — no decision reads
    those, whereas everything here keys on these.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string, not {type(value).__name__} "
            f"({value!r}); identity-bearing fields are never coerced, because "
            "two distinct values sharing one string form would silently "
            "collapse into one occurrence"
        )
    return value


def decision_key(raw: Any, field_name: str) -> str:
    """A key a decision is looked up by, rejected rather than coerced.

    ``str()`` on a mapping key is silently lossy in the one way that matters
    here: ``{1: {"status": "failed"}, "1": {"status": "present"}}`` — which a
    YAML loader or a Python adapter can produce — collapses to one entry, and
    *which* record survives depends on iteration order. Reversing it flips
    ``for_family("1")`` from non-comparable to comparable, so a discarded
    ``FAILED`` record can license a conclusion (Codex review).

    This is the same defect ``canonical_form`` already rejects for mapping
    keys, in the one place that had not adopted the rule. The two modules are
    leaves and share nothing by design, so the rule is restated rather than
    imported — which is exactly the drift this branch keeps finding, and the
    reason both sites now carry a test instead of a comment promising they
    agree.

    ``versioning``'s ``section_schema_versions`` deliberately keeps its
    ``str()``: it is one of the five *informational* axes, which parse
    defensively because no decision reads them, and aborting a load over one
    would break that contract. Everything here is read by a decision.
    """
    if not isinstance(raw, str):
        raise TypeError(
            f"{field_name} must be a string, not {type(raw).__name__} "
            f"({raw!r}); coercing it would let two distinct keys collapse into "
            "one record, with iteration order deciding which survives"
        )
    return raw


def provenance_text(raw: Any, field_name: str) -> str:
    """A provenance field, rejected rather than coerced if not a string.

    ``str()`` made ``recipe: 1`` and ``recipe: "1"`` deserialize and serialize
    identically, so two records that a package distinguished became
    interchangeable (Codex review). That matters more here than it looks:
    ``recipe`` and ``producer`` are exactly the fields that decide whether two
    ``PRESENT`` records may be compared, so erasing a distinction between them
    makes invalid evidence look equivalent to valid evidence.

    This is the third module to restate the same rule — ``canonical_form``
    refuses a non-string mapping key, ``identity._identity_text`` refuses a
    coerced identity field, and ``_decision_key`` below refuses a coerced
    ledger key. They are deliberate restatements rather than one shared
    helper, because these modules are leaves that import nothing from each
    other, and a shared private module would have to be declared in the
    published Phase 0 surface to satisfy the landed-surface check.

    That is a real cost and it is recorded rather than hidden: every site now
    carries its own test, and ``AGENTS.md`` names the rule so Phase 1 can
    unify it once there is a shared leaf to put it in. A restated rule drifts,
    and this branch found four sites where it already had.
    """
    if not isinstance(raw, str):
        raise TypeError(
            f"{field_name} must be a string, not {type(raw).__name__} "
            f"({raw!r}); coercing it would make two records a package "
            "distinguished compare as interchangeable"
        )
    return raw


def diagnostics_from(raw: Any) -> tuple[str, ...]:
    """Parse a ``diagnostics`` field, refusing a scalar rather than splitting it.

    A string is a ``Sequence``, so ``tuple(str(d) for d in raw)`` turned a
    hand-edited ``"diagnostics": "parse error"`` into eleven single-character
    diagnostics and serialized it back as a list of characters — destroying the
    extraction error a reader needs for auditing, with no error anywhere (Codex
    review).

    This is the only field in the package where that failure is *silent*. The
    sibling record lists (``overrides`` here, ``occurrences`` in
    ``identity``) already reject a scalar, but only incidentally: their
    elements must be mappings, so iterating a string fails on the first one.
    Diagnostics are strings, so char-iteration succeeds and looks like data.
    ``identity._attribute_pair`` guards the same shape explicitly, and this is
    that rule applied to the one place it was missing.

    Rejecting rather than wrapping is deliberate and matches how this class
    already treats malformed input — ``FactStatus(data["status"])`` raises on
    an unknown status rather than downgrading it. Silently promoting a scalar
    to a one-element list would make a malformed package indistinguishable from
    a well-formed one, which is how the original defect went unnoticed.

    The *members* are rejected too, and this was the half the container guard
    left open: `str()` turned `diagnostics: [1, null]` into
    `("1", "None")` and wrote it back as apparently valid diagnostic text
    (Codex review). Preserving the extraction error a reader audits with is
    the entire reason this field is guarded, so silently manufacturing a
    plausible one defeats it exactly as the character-splitting did.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError(
            f"diagnostics must be a sequence of strings, not "
            f"{type(raw).__name__} ({raw!r}); a bare string would be split "
            "into one diagnostic per character"
        )
    entries = tuple(raw)
    for index, entry in enumerate(entries):
        if not isinstance(entry, str):
            raise TypeError(
                f"diagnostics[{index}] must be a string, not "
                f"{type(entry).__name__} ({entry!r}); coercing it would write "
                "a manufactured diagnostic back as if a producer had reported it"
            )
    return entries


def mapping(raw: Any, field_name: str) -> None:
    """A container a decision is looked up *in*, checked before its contents.

    Validating the keys a container yields is not the same as validating the
    container: a list, a tuple or a string yields values that pass every key
    check, so ``AvailabilityLedger(families=["layout"])`` constructed
    successfully and only failed later, inside ``for_family``/``to_dict``,
    with an ``AttributeError`` about a missing ``get``/``items`` — while
    ``from_dict`` refused the same shape at the door (Codex review). A
    ``Mapping`` is the weakest thing that answers both questions the ledger
    asks of these fields.
    """
    if not isinstance(raw, Mapping):
        raise TypeError(
            f"{field_name} must be a mapping, not {type(raw).__name__} "
            f"({raw!r}); a sequence yields valid-looking keys but answers "
            "neither lookup nor serialization"
        )


def enum_member(raw: Any, enum_class: type, field_name: str) -> Any:
    """A closed vocabulary, checked where it is assigned.

    `from_dict` builds these by calling the enum, so a document naming an
    unknown member is refused there. A direct constructor took the raw value:
    `EntityId(kind="type", ...)` was accepted, and both `key` and `to_dict`
    then failed with `AttributeError` on the missing `.value` — the same
    boundary-only-guard shape as the text fields, on the fields whose type is
    the vocabulary rather than a string (Codex review).
    """
    if not isinstance(raw, enum_class):
        raise TypeError(
            f"{field_name} must be a {enum_class.__name__}, not "
            f"{type(raw).__name__} ({raw!r}); the string spelling is what a "
            "document carries, not what an in-memory record holds"
        )
    return raw


def instance_of(raw: Any, expected: type, field_name: str) -> Any:
    """A stored record, checked where it is assigned rather than where read.

    Nothing consults a nested record until a decision needs it, so a value of
    the wrong type survives construction and surfaces as an `AttributeError`
    from inside whichever accessor happens to reach it first — `key`,
    `to_dict`, `comparable` — far from the assignment that accepted it
    (Codex review).
    """
    if not isinstance(raw, expected):
        raise TypeError(
            f"{field_name} must be a {expected.__name__}, not "
            f"{type(raw).__name__} ({raw!r})"
        )
    return raw
