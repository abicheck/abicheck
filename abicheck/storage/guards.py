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
from typing import Any, cast

#: Deliberately *not* re-exported from the package. These are the doors'
#: internal instruments, not part of what a consumer of `abicheck.storage`
#: reads or writes — but they carry an `__all__` anyway so the same
#: landed-surface check that pins the four public modules pins this one too,
#: and a new guard cannot appear here unadvertised.
__all__ = [
    "binary_buffer",
    "decision_key",
    "item_iterable",
    "row_sequence",
    "required_field",
    "key_collection",
    "enum_member",
    "instance_of",
    "diagnostics_from",
    "identity_text",
    "mapping",
    "provenance_text",
    "strict_int",
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
    entries = row_sequence(raw, "diagnostics")
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


def key_collection(raw: Any, field_name: str) -> None:
    """A collection *of* keys, checked before it is iterated.

    A bare ``str`` is an iterable of ``str``, so it satisfies both the
    parameter's annotation and every per-item key check — it just yields
    characters. ``missing_families("layout")`` answered
    ``('a', 'l', 'o', 't', 'u', 'y')`` and omitted the real failed family,
    so the coverage check that exists to *find* gaps reported six that do
    not exist and missed the one that does (Codex review).

    Worth being precise about the scope, because it is narrower than it
    looks. A per-item guard already covers a collection whose items are
    *not* strings: ``OccurrenceSet.extend("abc")`` raises on the first
    character, since ``"a"`` is not an ``OccurrenceId``. Only a collection
    of *strings* is defeated this way, because there a character is a
    perfectly valid item. ``bytes`` is rejected alongside ``str`` for the
    same reason in reverse — it yields ``int``, which today raises from the
    item guard, but that is the item guard's accident rather than this
    one's intent.
    """
    if isinstance(raw, str) or binary_buffer(raw):
        raise TypeError(
            f"{field_name} must be a collection of keys, not a bare "
            f"{type(raw).__name__} ({raw!r}); iterating it yields characters, "
            "each of which is a valid key, so the result would look answered "
            "rather than wrong"
        )


def row_sequence(raw: Any, field_name: str) -> tuple[Any, ...]:
    """A document field holding *rows*, checked before it is iterated.

    A JSON array is the only shape this can be, but Python is happy to
    iterate several others into something plausible, and each failure is
    silent rather than loud:

    * a ``Mapping`` yields its **keys**, so
      ``attributes={("size", "8"): "discarded"}`` was read as the attribute
      ``("size", "8")`` with the value dropped — identity manufactured from
      one half of a mapping;
    * a ``str`` yields characters;
    * a ``set`` yields in an order that varies by process, which for a field
      whose rows become part of a key is a determinism bug;
    * and every one of them, when empty, yields nothing — so a malformed or
      unparsed field became the *claim* that the producer established there
      are no rows. That is precisely the "absence is not evidence" reading
      this package exists to prevent (Codex review).

    A ``Mapping`` and a ``set`` are not ``Sequence``s, so the one check
    covers all of it. This rule already existed inline in three places
    (:func:`diagnostics_from`, ``_attribute_pair``, ``IdentityConflict``)
    before it was a guard, which is the drift this module exists to stop.
    """
    if isinstance(raw, str) or binary_buffer(raw) or not isinstance(raw, Sequence):
        raise TypeError(
            f"{field_name} must be a sequence of rows, not "
            f"{type(raw).__name__} ({raw!r}); iterating a mapping yields its "
            "keys and an empty one would read as 'the producer established "
            "there are none'"
        )
    return tuple(raw)


def binary_buffer(value: Any) -> bool:
    """Whether a value is a binary payload rather than a container of records.

    Tested by the **buffer protocol**, not against a list of types, because
    an enumerated list is only ever as complete as the list. This module
    learned that the expensive way: three guards here checked
    ``(str, bytes)`` and so accepted ``bytearray`` and ``memoryview``. An
    empty one yielded nothing — the "producer established there are none"
    reading — and a non-empty one sailed past ``row_sequence`` and
    ``key_collection`` outright, since both are ``Sequence``s that are not
    ``bytes`` (Codex review).

    ``memoryview(value)`` succeeds exactly when a value exposes a buffer, so
    it asks the question the rule is actually about, and covers
    ``array.array`` and ``mmap.mmap`` without naming them. ``str`` exposes
    no buffer, so every caller checks it separately.

    **This predicate is shared with** :mod:`abicheck.storage.canonical`,
    which reached the same rule first for its own binary-payload branch and
    kept a private copy. An earlier note in this module argued the two leaves
    should restate rules rather than import them, with tests pinning that
    they agree — and then the enumerated-list version drifted here anyway,
    which is the outcome that note itself predicted. One definition, in the
    module whose job is shared guards.
    """
    try:
        memoryview(value)
    except TypeError:
        return False
    return True


def item_iterable(raw: Any, field_name: str) -> Any:
    """An iterable of records, for a door that must still accept a generator.

    The weaker sibling of :func:`row_sequence`, and the difference is the
    contract rather than the caution: a parameter annotated ``Iterable`` has
    generators as legitimate callers, and ``row_sequence`` rejects one. So
    this refuses only the containers whose *shape* changes what the call
    means — a ``str``/``bytes``, and a ``Mapping`` whose values would be
    silently dropped — and passes everything else through untouched.

    The empty case is the whole reason this is a container check and not an
    item check. ``extend("abc")`` really does raise, from the per-item
    guard, on the first character — which is what led me to claim a bare
    string was "already loud" here. ``extend("")`` iterates zero times, so
    no per-item guard can ever fire, and the set then serializes as
    ``{"occurrences": []}``: malformed input made indistinguishable from a
    producer that established there were none (Codex review).

    **A per-item guard is never a container guard, because an empty
    container has no items.** That is the general form of the mistake, and
    it is worth stating once rather than rediscovering per door.
    """
    if isinstance(raw, str) or binary_buffer(raw):
        raise TypeError(
            f"{field_name} must be an iterable of records, not a bare "
            f"{type(raw).__name__} ({raw!r}); a non-empty one yields "
            "characters or bytes and an empty one yields nothing, which "
            "would read as 'the producer established there are none'"
        )
    if isinstance(raw, Mapping):
        raise TypeError(
            f"{field_name} must not be a mapping ({raw!r}); iterating one "
            "yields its keys and silently drops every value"
        )
    return raw


def required_field(document: Any, key: str, record: str) -> Any:
    """A field a stored document must carry, missing cleanly rather than raw.

    ``document[key]`` raises ``KeyError``, which is a ``LookupError`` and so
    matches neither arm of the ``TypeError``/``ValueError`` pair this package
    documents as "the package is malformed" (see this module's own
    ``mapping`` and the storage ``AGENTS.md``). A caller separating a corrupt
    package from a broken reader therefore reported a truncated document as
    an internal crash (Codex review).

    ``ValueError`` rather than ``TypeError``: the document is the right
    *kind* of thing and is simply incomplete, which is the distinction the
    two already carry elsewhere here — ``mapping`` raises ``TypeError``
    because a list is the wrong kind of thing entirely.

    The message names the record as well as the field, since a nested
    document surfaces through its parent's ``from_dict`` and "missing
    ``kind``" alone does not say which entity was short of one.

    Membership is tested *before* subscripting, because ``KeyError`` is not
    what "absent" means for every mapping. A ``defaultdict`` — a ``Mapping``,
    so the container guard passes it — runs ``__missing__`` and returns an
    invented value instead of raising, so ``EntityId.from_dict(defaultdict(
    lambda: "fabricated", {"kind": "function"}))`` accepted a fabricated
    ``qualified_name`` and reserialized it as genuine identity, which
    occurrences would then have deduplicated under (Codex review). Catching
    ``KeyError`` answers "did the subscript fail", and only ``in`` answers
    the question actually being asked.
    """
    if key not in document:
        raise ValueError(
            f"{record} is missing required field {key!r}; a stored document "
            "short of a required field is malformed, not absent"
        )
    try:
        return document[key]
    except KeyError:
        # Unreachable for a well-behaved mapping, kept because `in` and
        # `[]` are two different methods and a container is free to
        # disagree with itself between them.
        raise ValueError(
            f"{record} is missing required field {key!r}; a stored document "
            "short of a required field is malformed, not absent"
        ) from None


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


def strict_int(raw: Any, field_name: str) -> int:
    """An ``int``-typed field, rejecting ``bool`` even though ``bool``
    subclasses ``int`` in Python — plain ``instance_of(raw, int, ...)``
    would accept it, so a document carrying JSON ``true``/``false`` for a
    field like an ordinal or a schema version silently parsed as ``1``/``0``,
    letting two structurally distinct wire values collapse onto one meaning
    (a `true` ordinal and a literal `1` ordinal reconstructing to equal,
    same-hash records; a `true`/`2.0` schema version comparing equal to a
    real version number and dispatching to the wrong parser) — the identical
    boundary-only-guard shape this module's other doors already close, just
    for the one type where Python's own subclass relationship is the trap
    rather than a missing check (Codex review — this was independently
    reinvented at two call sites in `storage/entity_ids.py` before landing
    here, which is exactly the drift this module exists to stop).

    ``float`` is rejected too, for the identical "compares equal to a real
    int without being one" reason (``2.0 == 2``).
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(
            f"{field_name} must be an int, not {type(raw).__name__} "
            f"({raw!r}); a bool or float can compare equal to a real int "
            "without being interchangeable with one"
        )
    return cast(int, raw)
