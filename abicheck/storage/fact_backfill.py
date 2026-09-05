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

"""Legacy-load corrections for case-(a) ``Fact[T]`` fields (ADR-063 Phase 5).

Split out of ``fact_codec.py`` (which re-exports everything here unchanged,
so every existing ``from .fact_codec import apply_legacy_fact_backfill``
call site is unaffected) once that module crossed ADR-061's 800-line
production ceiling. The two concerns are genuinely different: ``fact_codec``
encodes and decodes what a document *says*, while this module answers the
narrower question a case-(a) field forces -- whether a document that
predates that field's own ``Fact[T]`` conversion may be believed at all,
given the snapshot-level ``*_facts_reliable`` flag guarding it.

Depends only on ``model`` and on ``fact_schema_versions.py`` -- the leaf
module holding the per-field thresholds both this module and ``fact_codec``
need. Importing them back from ``fact_codec`` instead would be a real import
cycle (``fact_codec`` re-exports this module's three public names for
compatibility), which the ``import-cycle-growth`` gate rejects.

**T9 / ADR-063 Phase 6 item 4 (the "legacy-hybrid backfill blocker"):** the
whole-snapshot ``reliable``/``evidenced`` framework above cannot see the one
ambiguity that only exists on a ``--ast-frontend hybrid`` snapshot: seven
fields (``RecordType.deprecated``, ``EnumType.deprecated``/``is_scoped``,
``Function.deprecated``, ``Variable.deprecated``, ``TypeField.deprecated``/
``default``) are gated, at COMPARE time, by ``fact_provenance.py``'s
per-declaration ``AbiSnapshot.fact_provenance`` map rather than by a
snapshot-level flag -- because a hybrid merge's own two backends can
disagree on which of them actually populated any one declaration's fact,
which no whole-snapshot boolean can express. ``clang_deprecation_facts_
reliable`` reads ``True`` for a hybrid producer unconditionally (an
ordinary, fresh hybrid dump states this fact explicitly per declaration, so
the flag has no reason to distrust it), so the ``unreliable``/
``unproduceable`` checks above both pass for a legacy (pre-``min_schema_
version``) hybrid document, and the pre-existing ``__post_init__`` bridge's
``Fact.present(raw_value)`` is left standing -- even for a declaration
neither backend actually confirmed on that document, since the legacy JSON
format always serializes SOME value (there is no "omitted" concept once a
dict round-trips through a dataclass constructor). ``fact_provenance_kind``
on :class:`CaseAFactRule`, consulted only when the
document's ``ast_producer`` is exactly ``"hybrid"`` and only for a rule
that names one, resolves this the same way ``fact_provenance.
resolved_fact_producer`` already does at compare time: probe the
declaration's namespace-qualified provenance key first, falling back to the
bare pre-qualification key only when no OTHER declaration on this same
snapshot shares that bare name (an ambiguous bare fallback would misattribute
one declaration's real provenance to an unrelated sibling). No entry under
either key means neither backend's merge recorded ever having looked --
which downgrades the *claim* exactly like an ``evidenced``-only miss above
(never the value, and only when the value is already the field's own
resting default -- the existing "downgrade the claim, never the value"
convention this module's own callers already rely on).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..model import AccessLevel, Fact
from ..model.fact_registry import FACT_REGISTRY
from .fact_schema_versions import (
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
)

if TYPE_CHECKING:
    from ..model import Function, RecordType

__all__ = [
    "CaseAFactRule",
    "apply_case_a_fact_backfill",
    "apply_legacy_fact_backfill",
    "evidenced_producers",
]


@dataclass(frozen=True)
class CaseAFactRule:
    """One case-(a) field's legacy-load correction (ADR-063 Phase 5).

    ``owner``/``field`` name the legacy field; ``min_schema_version`` is the
    schema_version that field's own ``<field>_fact`` sibling started being
    persisted at; ``reliable`` is this snapshot's already-resolved answer to
    "is the flag guarding this field's availability trustworthy here"
    (``serialization.py`` computes every ``*_facts_reliable`` value, folding
    in any producer gate — see ``apply_legacy_fact_backfill``'s own
    ``ast_producer`` note); ``normalized_default`` is the value the legacy
    field is reset to when the fact is downgraded, so the pair cannot be
    left holding a placeholder beside a NOT_COLLECTED status.

    ``fact_provenance_kind`` (see this module's own "legacy-hybrid backfill
    blocker" docstring section) names which per-declaration
    ``fact_provenance`` key shape this field's provenance is recorded under
    on a hybrid merge — ``"type"``/``"enum"``/``"field"`` (namespace-
    qualified with a bare-name fallback, keyed by the owning
    ``RecordType``/``EnumType``'s own identity) or ``"func"``/``"var"``
    (keyed by the declaration's own mangled name, already unique — no
    bare/qualified split to resolve). ``None`` (the default) means this
    field carries no such per-declaration mechanism, so the check is never
    consulted for it — every rule converted before this one, and
    ``is_va_list``/``is_restrict``/``access``, which have their own
    independent reliability signals instead.
    """

    owner: str
    field: str
    min_schema_version: int
    reliable: bool
    normalized_default: Any
    fact_provenance_kind: str | None = None


#: ``(qualified key, bare key)`` for the entity a rule's per-declaration
#: ``fact_provenance`` lookup is scoped by -- the object itself for a
#: ``RecordType``/``EnumType`` rule, its OWNING record for a ``TypeField``
#: rule (a field's provenance key is scoped by its type, not the field's own
#: name -- see ``dumper_hybrid._merge_field``), and the declaration's own
#: mangled name twice over for ``Function``/``Variable`` (already unique, so
#: there is no bare/qualified split to fall back through). ``(None, None)``
#: for ``Param``, which carries no per-declaration provenance mechanism at
#: all -- its one case-(a) field (``is_va_list``) is gated by a snapshot-level
#: producer check instead (``apply_legacy_fact_backfill``'s own
#: ``ast_producer_value == "clang"`` gate), so no rule ever sets
#: ``fact_provenance_kind`` for it and this value is never consulted.
_OwnerKey = tuple[str, str] | tuple[None, None]


def _owner_pairs(
    d: dict[str, Any],
    owner: str,
    decoded: dict[str, list[Any]],
) -> Iterator[tuple[dict[str, Any], Any, _OwnerKey]]:
    """Every ``(raw dict, decoded object, owner_key)`` triple for one *owner*
    dataclass.

    The one place this module knows how a given owner's instances are
    reached from the raw snapshot document — two owners (``TypeField``,
    ``Param``) live one level below a collection rather than in one, and a
    per-field ``zip`` open-coded at each call site is exactly the kind of
    duplication a later owner's conversion would get subtly wrong.

    ``owner_key`` (added for the module docstring's "legacy-hybrid backfill
    blocker" section) is threaded through unconditionally rather than
    computed only when a rule needs it — every owner already has it for
    free from the same objects this generator already walks, and computing
    it lazily per-rule would mean re-deriving the same TypeField-owning
    RecordType walk a second time.
    """
    if owner == "RecordType":
        for raw, obj in zip(d.get("types", []), decoded.get("types", []), strict=False):
            yield raw, obj, (obj.qualified_name or obj.name, obj.name)
    elif owner == "EnumType":
        for raw, obj in zip(d.get("enums", []), decoded.get("enums", []), strict=False):
            yield raw, obj, (obj.qualified_name or obj.name, obj.name)
    elif owner == "Variable":
        for raw, obj in zip(
            d.get("variables", []), decoded.get("variables", []), strict=False
        ):
            yield raw, obj, (obj.mangled, obj.mangled)
    elif owner == "Function":
        for raw, obj in zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        ):
            yield raw, obj, (obj.mangled, obj.mangled)
    elif owner == "TypeField":
        for type_dict, record in zip(
            d.get("types", []), decoded.get("types", []), strict=False
        ):
            owner_key: _OwnerKey = (record.qualified_name or record.name, record.name)
            for raw, field in zip(
                type_dict.get("fields", []), record.fields, strict=False
            ):
                yield raw, field, owner_key
    elif owner == "Param":
        for func_dict, func in zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        ):
            for raw, param in zip(
                func_dict.get("params", []), func.params, strict=False
            ):
                yield raw, param, (None, None)
    else:  # pragma: no cover - guarded by the caller's own closed rule set
        raise ValueError(f"no raw-document navigation known for owner {owner!r}")


#: The two header-AST producers -- evidenced only by a snapshot whose header
#: provenance is *recorded* (see :func:`evidenced_producers`).
_HEADER_AST_BACKENDS: frozenset[str] = frozenset({"castxml", "clang"})

#: Every ``ast_producer`` spelling this build understands. ``serialization``'s
#: own reliability computations record that the field has always held exactly
#: these three, verified against every write site -- so a *fourth* string is
#: not a producer this build can reason about (see :func:`evidenced_producers`).
_KNOWN_AST_PRODUCERS: frozenset[str] = _HEADER_AST_BACKENDS | {"hybrid"}


def evidenced_producers(
    *,
    header_provenance_confirmed: bool,
    ast_producer: str | None,
    platform: str | None,
) -> frozenset[str]:
    """Which fact producers this raw document shows actually ran.

    "Which backends *could* produce this fact in principle" is the wrong
    question for a legacy load, and answering it is how two review rounds
    found the same defect twice (Codex, PR #993): a header-AST-only fact
    survived on a non-header snapshot, and then ``TypeField.is_const``/
    ``is_volatile`` survived on a **PE/PDB** snapshot because their registry
    entry names ``dwarf`` -- a producer that document has no trace of, and
    one whose fresh equivalent (``pdb_model._record_from_layout``) states
    ``UNSUPPORTED`` outright. The question is which producers *this
    document* evidences:

    - ``castxml``/``clang`` only when header provenance is **recorded**
      (an inferred ``from_headers`` is a guess a legacy DWARF-only dump
      satisfies too); a recorded ``ast_producer`` narrows to that one
      backend, and ``"hybrid"`` admits both.

      An **absent** ``ast_producer`` also admits both, but an
      *unrecognized* one admits neither (Codex review, PR #995): those are
      different states, not one. ``None`` means the document format did not
      carry the field yet, and its recorded ``from_headers`` still says a
      header-AST backend ran -- only which one is unknown, and each field's
      own ``*_facts_reliable`` computation in ``serialization.py`` is what
      answers that conservatively. A fourth *spelling* means the document
      names a producer this build cannot identify (``ast_producer`` has held
      exactly ``castxml``/``clang``/``hybrid`` at every write site), so this
      build cannot say what it produces, and the falsy ``""`` is the same
      malformed case. Crediting both there let a header-only fact stay
      ``PRESENT`` on a document naming neither backend.
    - ``elf``/``pe``/``macho`` from the document's own ``platform``.

    **No debug-info producer is ever credited from a debug block.** The
    ``dwarf``/``dwarf_advanced`` blocks are this codebase's debug *storage*,
    not a format claim, and every non-DWARF producer writes into them with
    ``has_dwarf=True``: ``btf_metadata.BtfMetadata.to_dwarf_metadata`` and its
    CTF sibling (via ``dumper_debug._resolve_debug_metadata``) and
    ``pdb_metadata.parse_pdb_debug_info``. Nothing inside a legacy document
    says which of them ran -- ``dwarf_advanced.has_dwarf`` is not the
    discriminator it looks like either, since
    ``dwarf_presence._section_presence_metadata`` sets it for BTF and CTF on
    the ``--debug-presence-only`` path. Two review rounds were spent
    narrowing this inference (Codex, PR #995: first the placeholder block's
    truthiness, then PE); the third finding is that the inference itself is
    the bug, because the storage layer cannot answer a question the payload
    does not record. So a populated debug block evidences *nothing*, and a
    fact resting on a debug producer alone downgrades.

    Four rules in :func:`apply_legacy_fact_backfill`'s table name ``dwarf``
    -- ``TypeField.is_const``/``is_volatile`` and ``RecordType.vtable``/
    ``vptr_offset_bits`` (Codex review, PR #995, eighth round: an earlier
    draft of this docstring claimed only the first two, having enumerated
    the fields ADR-063 Phase 5 converted rather than the whole table).
    Every one of them also names both header-AST backends, so a *recorded*
    header snapshot is unaffected; the reach is a legacy **non-header**
    document still holding the resting default, where the claim narrows and
    the value never does -- a record with a real vtable keeps it and stays
    ``PRESENT``.

    The vtable pair is deliberately not carved out. A DWARF-derived
    ``vtable: []`` on a non-polymorphic record is a real "the producer ran
    and established nothing is there" observation, and losing it is a cost.
    But it is not *recoverable* here: the only way to keep it would be to
    read ``platform == "elf"`` plus a populated debug block as DWARF, which
    is precisely the inference the round above removed, and which a BTF or
    CTF snapshot satisfies identically while carrying no vtable information
    at all. On ``main`` today that same ``vtable: []`` reads ``PRESENT`` on a
    PE/PDB and a symbols-only ELF document too, so the uniform answer
    replaces a broader over-claim rather than a narrower correct one.

    A **fresh** snapshot is unaffected in every case: it persists each
    ``<field>_fact`` directly and :func:`apply_case_a_fact_backfill` skips
    any entry that already carries one. Recording the resolved format
    (``dumper.resolved_debug_format``) in the snapshot would not change that
    -- it would name the producer only for documents that never needed this
    correction.

    So the evidenced set is exactly the two bullets above: ``dwarf``,
    ``pdb``, ``btf`` and ``ctf`` are never inferred. No fact in the registry
    names any of them as its *only* producer, so nothing is left with no
    reachable producer at all -- and a future one would fail closed
    (downgraded to ``NOT_COLLECTED``) rather than silently claim itself.

    Takes no raw document by design: needing one back would mean this layer
    is reading the payload to decide what it means again.
    """
    evidenced: set[str] = set()
    if header_provenance_confirmed:
        if ast_producer in _HEADER_AST_BACKENDS:
            evidenced.add(ast_producer)
        elif ast_producer is None or ast_producer == "hybrid":
            evidenced |= _HEADER_AST_BACKENDS
    if platform in {"elf", "pe", "macho"}:
        evidenced.add(platform)
    return frozenset(evidenced)


def _unproduceable(owner: str, field: str, evidenced: frozenset[str]) -> bool:
    """Whether no producer this document evidences can have observed the fact.

    Answered from the fact's own ``FACT_REGISTRY`` entry rather than a
    second hand-maintained list -- the registry ADR-063 Phase 5 exists to
    build is the one place a fact's producers are declared, so a future
    change to them reaches this decision automatically. An unregistered
    field answers ``False``: this correction only ever *narrows* a claim,
    so an unknown fact keeps the pre-existing behaviour rather than being
    downgraded on a guess.
    """
    entry = FACT_REGISTRY.get(f"{owner}.{field}")
    if entry is None:
        return False
    return not (set(entry.producing_backends) & evidenced)


def _bare_name_ambiguous(entities: list[Any]) -> dict[str, bool]:
    """Per bare ``.name``, whether more than one DISTINCT qualified identity
    on this document's side shares it.

    The per-snapshot-side counterpart of
    ``fact_provenance.resolved_fact_producer``'s own ``bare_unambiguous``
    guard, computed once here instead of once per rule/declaration:
    :func:`_hybrid_provenance_confirms` must not fall back from a
    qualified-but-absent provenance key to the bare one when two distinct
    types/enums on this same side happen to share a leaf name, since the
    fallback would then attribute one declaration's real provenance entry
    to an unrelated sibling.
    """
    qualified_by_bare: dict[str, set[str]] = {}
    for e in entities:
        qualified_by_bare.setdefault(e.name, set()).add(e.qualified_name or e.name)
    return {bare: len(qualified) > 1 for bare, qualified in qualified_by_bare.items()}


def _hybrid_provenance_confirms(
    rule: CaseAFactRule,
    obj: Any,
    owner_key: _OwnerKey,
    fact_provenance: Mapping[str, str],
    bare_ambiguous: bool,
) -> bool:
    """Whether *this declaration's* ``fact_provenance`` entry (module
    docstring's "legacy-hybrid backfill blocker" section) confirms that some
    backend's merge actually recorded looking at *rule.field* here.

    Key formats are inlined rather than imported from ``fact_provenance.py``
    (a flat-root, ``model``-only-adjacent module): ``storage`` may depend on
    ``model`` only (``architecture/modules.yaml``), and the scheme itself is
    small, public, and stable across a serialize/deserialize round-trip
    (``fact_provenance.py``'s own module docstring) -- a second literal
    formatting of the same three-token scheme, not a duplicated algorithm.

    Probes the namespace-qualified key first, falling back to the former
    bare key only when *bare_ambiguous* says no OTHER declaration on this
    side shares that bare name -- the same qualified-then-bare-with-
    ambiguity-guard shape ``fact_provenance.resolved_fact_producer`` already
    applies at compare time, so a hybrid baseline persisted before the
    provenance-key qualification fix (real data recorded under the bare key
    alone) is not wrongly treated as unconfirmed.
    """
    qualified, bare = owner_key
    kind = rule.fact_provenance_kind
    if kind == "func":
        return f"func:{qualified}:{rule.field}" in fact_provenance
    if kind == "var":
        return f"var:{qualified}:{rule.field}" in fact_provenance
    if kind == "type":
        prefix = "type"
    elif kind == "enum":
        prefix = "enum"
    elif kind == "field":
        prefix = "type"
        # A field's own key names both the owning type and the field itself
        # (`fact_provenance.field_fact_key`) -- the field's bare NAME is not
        # part of the ambiguity question above (only the owning type's is).
        suffix = f":field:{obj.name}:{rule.field}"
        if f"{prefix}:{qualified}{suffix}" in fact_provenance:
            return True
        if bare != qualified and not bare_ambiguous:
            return f"{prefix}:{bare}{suffix}" in fact_provenance
        return False
    else:  # pragma: no cover - guarded by the caller's own closed rule set
        raise ValueError(f"unknown fact_provenance_kind {kind!r}")
    suffix = f":{rule.field}"
    if f"{prefix}:{qualified}{suffix}" in fact_provenance:
        return True
    if bare != qualified and not bare_ambiguous:
        return f"{prefix}:{bare}{suffix}" in fact_provenance
    return False


def apply_case_a_fact_backfill(
    d: dict[str, Any],
    *,
    schema_version: int,
    rules: tuple[CaseAFactRule, ...],
    # Required, never defaulted: a default here would be a value standing in
    # for missing evidence (storage/AGENTS.md rule 3), and the value it stood
    # in for -- both header-AST backends -- is the most permissive one there
    # is, so an omitted argument would keep a header-only fact PRESENT on a
    # document with no header provenance at all (CodeRabbit review, PR #995).
    # A caller with nothing to report passes `frozenset()`.
    evidenced: frozenset[str],
    # Both optional and both `None` by default -- a caller that never heard
    # of the legacy-hybrid provenance check (every pre-existing call site)
    # gets exactly the old behavior, since no rule it passes can carry a
    # `fact_provenance_kind` it doesn't already set. See the module
    # docstring's "legacy-hybrid backfill blocker" section.
    fact_provenance: Mapping[str, str] | None = None,
    ast_producer: str | None = None,
    **decoded: list[Any],
) -> None:
    """Downgrade every case-(a) fact a legacy document cannot vouch for.

    A document below a field's own ``min_schema_version`` carries no
    ``<field>_fact`` key at all, so the owning dataclass's ``__post_init__``
    bridge already backfilled ``Fact.present(raw_value)`` — correct when the
    snapshot-level reliability flag guarding that field says this producer's
    values are trustworthy, and exactly the "placeholder read as a confirmed
    fact" bug ``Fact[T]`` exists to prevent when it doesn't. This is the one
    correction for that whole class: :func:`apply_legacy_fact_backfill` (the
    three fields ADR-063 Phase 0 converted) is a thin wrapper over it, and
    every case-(a) field a later batch converts adds a rule rather than
    another hand-written loop.

    Only ever *downgrades*, and only for a document that predates the
    field's own conversion: a v(N)+ document's ``<field>_fact`` was decoded
    explicitly at construction time and is authoritative. ``evidenced``
    carries the second downgrade reason (see the body and
    :func:`evidenced_producers`): a producer this document shows no trace of
    cannot have observed a fact only it produces, which no reliability flag
    expresses.

    ``fact_provenance``/``ast_producer`` carry the third downgrade reason
    (module docstring, "legacy-hybrid backfill blocker"): for a rule whose
    ``fact_provenance_kind`` is set, on a document whose ``ast_producer`` is
    exactly ``"hybrid"``, the whole-snapshot ``reliable``/``evidenced``
    checks above both pass unconditionally, but the per-DECLARATION question
    they cannot ask -- did either backend's merge actually record having
    looked at this one declaration's fact -- is exactly what
    ``fact_provenance`` answers. Absent on both callers, this check is
    inert, matching every pre-existing rule and call site exactly.
    """
    # Bare-name ambiguity, precomputed once per document rather than
    # per-rule: whether a given bare `RecordType`/`EnumType` name is shared
    # by more than one distinct qualified identity on THIS side (needed only
    # by the hybrid-provenance check below, mirroring
    # `fact_provenance.resolved_fact_producer`'s own `bare_unambiguous`
    # guard -- an ambiguous bare fallback would misattribute one
    # declaration's real provenance to an unrelated sibling sharing its leaf
    # name). Harmless (and unused) when no rule sets `fact_provenance_kind`.
    type_bare_ambiguous = _bare_name_ambiguous(decoded.get("types", []))
    enum_bare_ambiguous = _bare_name_ambiguous(decoded.get("enums", []))

    for rule in rules:
        if schema_version >= rule.min_schema_version:
            continue
        # Two independent reasons a pre-conversion document's value is not
        # evidence, and the second is not covered by the first (Codex
        # review, PR #993): every ``*_facts_reliable`` flag resolves True
        # for a snapshot whose producer never parsed a header, since the
        # describes never ran -- "trusted by irrelevance". That is the right
        # answer to "is this value a wrong placeholder", and the wrong
        # answer to "did anyone observe it": a legacy DWARF/PDB/symbols-only
        # document's `deprecated: null` / `is_restrict: false` /
        # `access: "public"` would otherwise bridge to PRESENT, claiming a
        # confirmed fact the fresh equivalent of that same snapshot reports
        # as NOT_COLLECTED.
        #
        # "Unproduceable" is answered against the producers this DOCUMENT
        # evidences, not against the ones that could produce the fact in
        # principle -- see `evidenced_producers`, which two review rounds
        # shaped: recorded (never inferred) header provenance, a real DWARF
        # block, the document's own platform.
        unreliable = not rule.reliable
        unproduceable = _unproduceable(rule.owner, rule.field, evidenced)
        # Third reason, hybrid-only (module docstring, "legacy-hybrid
        # backfill blocker"): a rule naming a `fact_provenance_kind`, on a
        # document whose merge recorded per-declaration provenance at all,
        # needs every declaration probed even when `unreliable`/
        # `unproduceable` are both False -- so this must join the rule-level
        # skip below, not just gate what happens inside the loop.
        provenance_gated = (
            rule.fact_provenance_kind is not None
            and ast_producer == "hybrid"
            and fact_provenance is not None
        )
        if not (unreliable or unproduceable or provenance_gated):
            continue
        fact_key = f"{rule.field}_fact"
        bare_ambiguous: dict[str, bool]
        if rule.owner in ("RecordType", "TypeField"):
            bare_ambiguous = type_bare_ambiguous
        elif rule.owner == "EnumType":
            bare_ambiguous = enum_bare_ambiguous
        else:
            bare_ambiguous = {}
        for raw, obj, owner_key in _owner_pairs(d, rule.owner, decoded):
            # Skip only an entry carrying a *usable* fact, not merely the
            # key. A `"<field>_fact": {}` or `: null` decodes to nothing
            # (`decode_fact`'s own `if not raw`), so the owning dataclass's
            # bridge derives the fact from the legacy value instead -- and
            # keying this on presence let such an entry past the producer
            # gate entirely, resolving PRESENT on a document with no
            # evidence for it at all (CodeRabbit review, PR #995). Falsy is
            # the same "no fact here" test the decoder already applies, so
            # the two halves cannot disagree about what the document says.
            if raw.get(fact_key):
                continue
            if unreliable:
                setattr(obj, rule.field, rule.normalized_default)
                setattr(obj, fact_key, Fact.not_collected())
                continue
            downgrade_claim_only = unproduceable
            bare_name = owner_key[1]
            if (
                not downgrade_claim_only
                and provenance_gated
                and fact_provenance is not None  # narrows for mypy
                and not _hybrid_provenance_confirms(
                    rule,
                    obj,
                    owner_key,
                    fact_provenance,
                    bare_ambiguous.get(bare_name, False)
                    if bare_name is not None
                    else False,
                )
            ):
                downgrade_claim_only = True
            if (
                downgrade_claim_only
                and getattr(obj, rule.field) == rule.normalized_default
            ):
                # Downgrade the *claim*, never the value, in either
                # remaining case: a non-header document (unproduceable) or a
                # hybrid document whose merge never recorded provenance for
                # this declaration (provenance_gated) carrying a non-resting
                # value for one of these fields got it from somewhere this
                # correction doesn't model, and discarding it would lose
                # real data -- unlike the unreliable case above, where the
                # value is known to be a placeholder.
                setattr(obj, fact_key, Fact.not_collected())


def apply_legacy_fact_backfill(
    d: dict[str, Any],
    types: list[RecordType],
    funcs: list[Function],
    schema_version: int,
    clang_vtable_facts_reliable_value: bool,
    clang_va_list_facts_reliable_value: bool,
    ast_producer_value: str | None,
    *,
    # Required, never defaulted: a default here would be a value standing in
    # for missing evidence (storage/AGENTS.md rule 3), and the value it stood
    # in for -- both header-AST backends -- is the most permissive one there
    # is, so an omitted argument would keep a header-only fact PRESENT on a
    # document with no header provenance at all (CodeRabbit review, PR #995).
    # A caller with nothing to report passes `frozenset()`.
    evidenced: frozenset[str],
    variables: list[Any] | None = None,
    enums: list[Any] | None = None,
    header_cv_facts_reliable_value: bool = True,
    clang_restrict_facts_reliable_value: bool = True,
    castxml_var_access_facts_reliable_value: bool = True,
    clang_field_initializer_facts_reliable_value: bool = True,
    clang_deprecation_facts_reliable_value: bool = True,
    # T9 / ADR-063 Phase 6 item 4: the raw `AbiSnapshot.fact_provenance` map,
    # threaded through so the seven rules below carrying a
    # `fact_provenance_kind` can resolve the legacy-hybrid backfill blocker
    # (see `apply_case_a_fact_backfill`'s own docstring). `None` (the
    # default) leaves every rule's existing behavior exactly unchanged.
    fact_provenance: Mapping[str, str] | None = None,
) -> None:
    """Correct the legacy backfill for every case-(a) fact a document predates.

    A pre-v26 snapshot carries no ``vtable_fact``/``vptr_offset_bits_fact``/
    ``is_va_list_fact`` keys at all, so each ``RecordType``/``Param``'s own
    ``__post_init__`` bridge already backfilled these to
    ``Fact.present(raw_value)`` unconditionally (there is no sentinel to
    distinguish "legacy, key absent" from "legacy, key present" here — both
    look like an ordinary explicit value to that bridge). That is correct
    for ``bases``/``virtual_bases`` (no independent reliability signal —
    see AGENTS.md's ``type_base_changed`` entry), but wrong for
    ``vtable``/``vptr_offset_bits``/``is_va_list`` when the *existing*
    reliability flags say this producer's own facts for this snapshot are
    untrustworthy: ``Fact.present(raw)`` would misread a placeholder value
    as a confirmed fact, exactly the bug this phase exists to make
    unrepresentable. Only runs for a legacy (pre-v26) load — a fresh v26+
    snapshot's ``*_fact`` keys were decoded explicitly at construction time
    via :func:`decode_fact` and must not be overridden here.

    Phase 5's own case-(a) batches extend the same correction to the fields
    they convert, each with its own ``min_schema_version`` and its own
    guarding flag (``header_cv_facts_reliable_value`` for ``TypeField``'s
    CV facts, schema v39) — one rule added to the tuple below, never a
    second hand-written loop. The keyword-only spelling keeps every
    pre-existing caller (and every test constructing this call) unchanged:
    a flag left at its default ``True`` states "trustworthy", which is what
    a caller that never heard of that field was already asserting by not
    correcting it at all.

    ``is_va_list`` needs an extra gate ``vtable``/``vptr_offset_bits`` don't
    (Codex review, fresh evidence): CastXML never determines va_list-ness at
    all — its own ``is_va_list`` is always a blanket ``False`` placeholder,
    not a computed fact the way CastXML's vtable *is* one (see
    ``clang_vtable_facts_reliable_value``'s own computation in
    ``serialization.py``: "a castxml... snapshot's own vtable extraction
    predates this field entirely, so it's always reliable"). But
    ``clang_va_list_facts_reliable_value`` reads ``True`` for a CastXML
    snapshot too, since that flag's actual meaning is "safe to trust
    `False` as not-wrong" (CastXML never reports a real va_list parameter
    as anything but `False`, so the polarity is never wrong) — a different
    question from "was this fact actually collected". Reusing that flag
    alone would silently turn "never observed" into "confirmed not
    va_list" on every legacy CastXML load. Gated here on
    ``ast_producer_value == "clang"`` in addition to the reliability flag,
    so only an actual clang-family load can reach ``Fact.present(...)``.
    """
    apply_case_a_fact_backfill(
        d,
        schema_version=schema_version,
        rules=(
            CaseAFactRule(
                "RecordType",
                "vtable",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                [],
            ),
            CaseAFactRule(
                "RecordType",
                "vptr_offset_bits",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "Param",
                "is_va_list",
                _FACT_FIELDS_SCHEMA_VERSION,
                ast_producer_value == "clang" and clang_va_list_facts_reliable_value,
                False,
            ),
            # ADR-063 Phase 5 (eighth batch, schema v39): TypeField's own CV
            # facts. A pre-v39 document carries no is_const_fact/
            # is_volatile_fact/is_mutable_fact key, so its blanket False
            # values were bridged to Fact.present(False);
            # header_cv_facts_reliable is exactly the signal saying whether
            # that reading is a real fact or a pre-fix castxml placeholder.
            CaseAFactRule(
                "TypeField",
                "is_const",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_volatile",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_mutable",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            # TypeField's other two case-(a) fields, each with its own
            # guarding flag: a pre-v39 clang document's blanket `None`
            # default-initializer/deprecation is the same placeholder shape
            # the CV facts have, and the same two flags the detectors
            # already consult say so.
            CaseAFactRule(
                "TypeField",
                "default",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_field_initializer_facts_reliable_value,
                None,
                fact_provenance_kind="field",
            ),
            CaseAFactRule(
                "TypeField",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="field",
            ),
            # ADR-063 Phase 5 (ninth batch, schema v40): the other four
            # `deprecated` surfaces plus EnumType.is_scoped, all guarded by
            # the same flag TypeField.deprecated is -- one rule each, which
            # is the whole point of the rule table.
            #
            # T9 / ADR-063 Phase 6 item 4: all seven rules in this batch
            # (including the two TypeField ones above) also carry a
            # `fact_provenance_kind` -- the whole-snapshot
            # `clang_deprecation_facts_reliable`/`clang_field_initializer_
            # facts_reliable` flags above both read True unconditionally for
            # a hybrid producer (an ordinary fresh hybrid dump states these
            # facts explicitly per declaration), so on a document that
            # predates this fact family's own schema version, only a real
            # per-declaration `fact_provenance` lookup -- not either flag --
            # can tell "neither backend's merge ever recorded looking here"
            # apart from "confirmed, genuinely resting-default". See
            # `apply_case_a_fact_backfill`'s own docstring.
            CaseAFactRule(
                "Function",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="func",
            ),
            CaseAFactRule(
                "Variable",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="var",
            ),
            CaseAFactRule(
                "RecordType",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="type",
            ),
            CaseAFactRule(
                "EnumType",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="enum",
            ),
            CaseAFactRule(
                "EnumType",
                "is_scoped",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
                fact_provenance_kind="enum",
            ),
            # ADR-063 Phase 5 (tenth batch, schema v41): the last two
            # case-(a) fields, each with its own guarding flag.
            CaseAFactRule(
                "Param",
                "is_restrict",
                _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
                clang_restrict_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "Variable",
                "access",
                _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
                castxml_var_access_facts_reliable_value,
                AccessLevel.PUBLIC,
            ),
        ),
        evidenced=evidenced,
        fact_provenance=fact_provenance,
        ast_producer=ast_producer_value,
        types=types,
        functions=funcs,
        variables=variables or [],
        enums=enums or [],
    )
