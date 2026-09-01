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

"""The fact/capability registry (ADR-063 D7, Phase 5).

Generalizes ``change_registry.py``'s single-declaration ``ChangeKindMeta``
pattern (already required for every ``ChangeKind``) from change *kinds* to
*facts*: every ``Fact[T]``-typed model field is declared exactly once here
— value type, producing backends, persistence/identity/comparability/
suppressibility/reportability, and lifecycle state — instead of the
"add a field, touch nine files" cost the ELF-binding incident (PR #734)
is the canonical example of.

**Scope, stated precisely (D7's own amendment).** This registry's initial
population is the *availability-bearing* subset D7 names: a field where
the plain resting value ("", ``None``, ``[]``, ``False``) cannot by itself
distinguish "not collected" from "confirmed absent" — exactly ``Fact[T]``'s
own reason for existing. An ordinary, always-present fact (an entity's
name, a type's size) has no such ambiguity and is out of this phase's
scope — registering the full, unambiguous field population is a real,
separately-justified future extension, not attempted here.

See ``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 5 section
for the full design discussion this module implements, including the
review-corrected eligibility rule ("field-based with an optional
availability source", not "one of the three annotation shapes Phase 0
happened to use").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "FACT_REGISTRY",
    "KNOWN_UNCONVERTED_ELIGIBLE_FACTS",
    "KNOWN_PRODUCING_BACKENDS",
    "REFERENCE_FLAG_COVERAGE",
    "FactDefinition",
    "FactLifecycle",
    "FactRegistry",
]


class FactLifecycle(Enum):
    """D7's explicit fact lifecycle: ``MODELLED -> ... -> PUBLIC``.

    A capability is documented or exposed as a CLI option only once it
    reaches ``PUBLIC`` — the stage that closes the repeated "shape shipped,
    wiring followed later" pattern AGENTS.md records for the L3->L2 fold.
    Every fact this registry declares today sits no higher than
    ``PERSISTED``: none has a detector reading its ``Fact[...]`` sibling
    yet (Phase 0's own status note — "no detector has migrated ... this is
    intentional"), so ``CONSUMED``/``REPORTED``/``PUBLIC`` have no real
    member yet and are declared for the vocabulary's own completeness, not
    because this registry already uses them.
    """

    MODELLED = "modelled"
    PRODUCED = "produced"
    NORMALIZED = "normalized"
    PERSISTED = "persisted"
    CONSUMED = "consumed"
    REPORTED = "reported"
    PUBLIC = "public"


#: Total order, least-advanced first — mirrors ``model/availability.py``'s
#: own ``STATUS_ORDER``/``CONFIDENCE_ORDER`` convention (a tuple stating the
#: order explicitly, not relying on declaration order alone).
LIFECYCLE_ORDER: tuple[FactLifecycle, ...] = (
    FactLifecycle.MODELLED,
    FactLifecycle.PRODUCED,
    FactLifecycle.NORMALIZED,
    FactLifecycle.PERSISTED,
    FactLifecycle.CONSUMED,
    FactLifecycle.REPORTED,
    FactLifecycle.PUBLIC,
)

#: Real fact producers this codebase's own ``ast_producer``/platform
#: vocabulary already uses (``AbiSnapshot.ast_producer`` for the two
#: header-AST backends, ``AbiSnapshot.platform`` for the three binary
#: formats, plus the DWARF/PDB/BTF/CTF debug-format producers D9 names).
#: ``FactRegistry`` validates every entry's ``producing_backends`` against
#: this set — a typo'd or invented backend name fails at import time
#: rather than silently documenting a producer that doesn't exist.
KNOWN_PRODUCING_BACKENDS: frozenset[str] = frozenset(
    {"castxml", "clang", "dwarf", "pdb", "btf", "ctf", "elf", "pe", "macho"}
)


@dataclass(frozen=True)
class FactDefinition:
    """All metadata for one availability-bearing model fact, in one place.

    ``owner``/``field`` together name the *legacy* field
    (``RecordType``/``is_final``); the ``Fact[T]`` sibling this entry
    describes is always ``f"{field}_fact"`` on the same dataclass — a
    derived name, not a second field to keep in sync (mirroring
    ``ChangeKindMeta.kind`` naming the ``ChangeKind`` enum value it
    describes rather than duplicating the enum member itself).
    """

    owner: str
    field: str
    value_type: str
    producing_backends: tuple[str, ...]
    persisted: bool
    identity_relevant: bool
    comparable: bool
    suppressible: bool
    reportable: bool
    lifecycle: FactLifecycle
    notes: str = ""

    @property
    def id(self) -> str:
        """``"<owner>.<field>"`` — this entry's unique registry key."""
        return f"{self.owner}.{self.field}"

    @property
    def fact_attr(self) -> str:
        """The ``Fact[T]``-typed sibling attribute name on ``owner``."""
        return f"{self.field}_fact"

    def __post_init__(self) -> None:
        if not self.owner or not self.field:
            raise ValueError("FactDefinition requires non-empty owner and field")
        if not self.producing_backends:
            raise ValueError(
                f"{self.id}: producing_backends must name at least one real "
                f"producer — a fact with no producer cannot ever reach "
                f"FactStatus.PRESENT"
            )
        unknown = set(self.producing_backends) - KNOWN_PRODUCING_BACKENDS
        if unknown:
            raise ValueError(
                f"{self.id}: producing_backends names unknown backend(s) "
                f"{sorted(unknown)}; valid backends are "
                f"{sorted(KNOWN_PRODUCING_BACKENDS)}"
            )


class FactRegistry:
    """Registry of :class:`FactDefinition` entries, keyed by ``.id``.

    Usage::

        entry = FACT_REGISTRY.get("RecordType.is_final")
        every = FACT_REGISTRY.entries
    """

    def __init__(self, entries: list[FactDefinition]) -> None:
        self._entries: dict[str, FactDefinition] = {}
        for e in entries:
            if e.id in self._entries:
                raise ValueError(f"Duplicate fact registry entry for {e.id!r}")
            self._entries[e.id] = e

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, fact_id: str) -> bool:
        return fact_id in self._entries

    def get(self, fact_id: str) -> FactDefinition | None:
        return self._entries.get(fact_id)

    def for_owner(self, owner: str) -> tuple[FactDefinition, ...]:
        """Every registered fact declared on dataclass ``owner``, in registration order."""
        return tuple(e for e in self._entries.values() if e.owner == owner)

    @property
    def entries(self) -> dict[str, FactDefinition]:
        return dict(self._entries)


#: Case (a): fields guarded by a snapshot-level ``*_facts_reliable`` boolean
#: flag on ``AbiSnapshot`` (``model/snapshot.py``) — the flag's own resting
#: value (``True``) can't distinguish "this run's producer is trustworthy"
#: from "a persisted pre-fix snapshot's blanket wrong placeholder", so only
#: the flag (not the field's own value) carries availability here. Built by
#: reading every ``*_facts_reliable`` flag's own docstring in
#: ``model/snapshot.py`` against the real gating detector for each field it
#: names (docs/contribute/plans/one-semantic-pipeline.md Phase 5 Design
#: section's own required inventory) — not derived from the flag's name by
#: pattern-matching, since the real many-to-one relationships (one flag
#: gating six fields) aren't name-derivable. Key: flag name. Value: every
#: ``(owner, field)`` pair that flag's own docstring names as covered.
REFERENCE_FLAG_COVERAGE: dict[str, tuple[tuple[str, str], ...]] = {
    "header_cv_facts_reliable": (
        ("TypeField", "is_const"),
        ("TypeField", "is_volatile"),
        ("TypeField", "is_mutable"),
    ),
    # Gates six fields, not the two ("Function.deprecated"/"is_scoped") its
    # own docstring narrates by name — every "deprecated" surface kind
    # (Function/Variable/TypeField/RecordType/EnumType) routes through the
    # same fact_provenance.fact_producer gate, keyed by a ":deprecated"
    # suffix shared across all five, plus EnumType.is_scoped.
    "clang_deprecation_facts_reliable": (
        ("Function", "deprecated"),
        ("Variable", "deprecated"),
        ("TypeField", "deprecated"),
        ("RecordType", "deprecated"),
        ("EnumType", "deprecated"),
        ("EnumType", "is_scoped"),
    ),
    "clang_field_initializer_facts_reliable": (("TypeField", "default"),),
    "clang_vtable_facts_reliable": (
        ("RecordType", "vtable"),
        ("RecordType", "vptr_offset_bits"),
    ),
    "clang_restrict_facts_reliable": (("Param", "is_restrict"),),
    "clang_va_list_facts_reliable": (("Param", "is_va_list"),),
    "castxml_var_access_facts_reliable": (("Variable", "access"),),
}


#: Case (a)/(b) fields the Phase 5 design section names as in scope
#: (availability-ambiguous, documented as such in a field-adjacent comment)
#: but **not yet converted** to a ``Fact[T]`` sibling — an allowlist-and-
#: shrink baseline, the identical convention ``fact_field_readers.
#: KNOWN_UNMIGRATED_READERS``/``IMPORT_CYCLE_ALLOWLIST`` already establish
#: elsewhere in this codebase. Every entry here is a real, currently-open
#: gap this ADR's own Governing Invariant requires naming rather than
#: silently converting to a fake-clean check — see ``scripts/
#: fact_registry_completeness.py`` for the scan that keeps this list
#: honest in both directions (an entry that no longer matches a real,
#: still-unconverted field fails the check; a real eligible field missing
#: from this list, or from ``FACT_REGISTRY``, also fails it).
#:
#: Case (a) — flag-backed (``REFERENCE_FLAG_COVERAGE`` minus the three
#: fields Phase 0 already converted: ``RecordType.vtable``/
#: ``vptr_offset_bits``, ``Param.is_va_list``):
_CASE_A_UNCONVERTED: tuple[tuple[str, str], ...] = (
    ("TypeField", "is_const"),
    ("TypeField", "is_volatile"),
    ("TypeField", "is_mutable"),
    ("Function", "deprecated"),
    ("Variable", "deprecated"),
    ("TypeField", "deprecated"),
    ("RecordType", "deprecated"),
    ("EnumType", "deprecated"),
    ("EnumType", "is_scoped"),
    ("TypeField", "default"),
    ("Param", "is_restrict"),
    ("Variable", "access"),
)

#: Case (b) — already tri-state (``X | None``) at the field's own declared
#: type, with a documented backend/schema-dependence comment, no separate
#: reliability flag. ``RecordType.is_final`` (the plan's own headline
#: example) is deliberately absent here — see ``FACT_REGISTRY`` below, it
#: is this phase's first real conversion.
_CASE_B_UNCONVERTED: tuple[tuple[str, str], ...] = (
    ("RecordType", "is_abstract"),
    ("RecordType", "data_size_bits"),
    ("RecordType", "is_standard_layout"),
    ("RecordType", "is_trivially_copyable"),
    ("RecordType", "qualified_name"),
    ("AbiSnapshot", "ast_resolved_standard"),
    ("EnumType", "qualified_name"),
    ("Function", "contract_attributes"),
    ("Function", "is_explicit"),
    ("Function", "is_hidden_friend"),
    ("Function", "is_variadic"),
    ("Function", "is_override"),
    ("Function", "is_compiler_generated"),
    ("Function", "hidden_friend_owner"),
    ("Function", "elf_binding"),
    ("Function", "exception_spec"),
    ("Variable", "alignment_bits"),
    ("Variable", "elf_binding"),
    # Provenance (ADR-015, schema v6): "missing on older snapshots and
    # default to None / UNKNOWN" — the identical ambiguity on all four
    # declaration dataclasses that carry it; only Function's own comment
    # states the full text (the other three cross-reference it via "see
    # Function.source_header" rather than repeating it), which is why
    # scripts/fact_registry_completeness.py's textual scan only
    # auto-discovers Function.source_header — the other three are named
    # here from manual inspection, not the scan.
    ("Function", "source_header"),
    ("Variable", "source_header"),
    ("RecordType", "source_header"),
    ("EnumType", "source_header"),
    # Schema-version-driven (not backend-driven) tri-state fields on the
    # three binary-format dataclasses — the identical "resting value can't
    # distinguish not-captured from confirmed-empty" shape, gated by a
    # persisted schema_version rather than a per-run backend choice. Named
    # here rather than silently left for the scan to rediscover, per this
    # ADR's own Governing Invariant.
    ("ElfMetadata", "dynamic_flags"),
    ("ElfMetadata", "has_init"),
    ("ElfMetadata", "has_fini"),
    ("PeMetadata", "delay_imports"),
    ("MachoMetadata", "rpaths"),
)

KNOWN_UNCONVERTED_ELIGIBLE_FACTS: frozenset[tuple[str, str]] = frozenset(
    _CASE_A_UNCONVERTED + _CASE_B_UNCONVERTED
)


_E = FactDefinition

FACT_REGISTRY = FactRegistry(
    [
        # ── Phase 0's original four RecordType layout facts ────────────
        _E(
            owner="RecordType",
            field="bases",
            value_type="list[str]",
            producing_backends=("castxml", "clang", "dwarf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Base class names, declaration order. No independent "
                "reliability flag ever guarded this field (unlike vtable/"
                "vptr_offset_bits below) — see AGENTS.md's "
                "type_base_changed entry for the accepted residual gap."
            ),
        ),
        _E(
            owner="RecordType",
            field="virtual_bases",
            value_type="list[str]",
            producing_backends=("castxml", "clang", "dwarf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
        ),
        _E(
            owner="RecordType",
            field="vtable",
            value_type="list[str]",
            producing_backends=("castxml", "clang", "dwarf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes="Guarded by AbiSnapshot.clang_vtable_facts_reliable for the clang producer.",
        ),
        _E(
            owner="RecordType",
            field="vptr_offset_bits",
            value_type="int | None",
            producing_backends=("castxml", "clang", "dwarf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes="Guarded by AbiSnapshot.clang_vtable_facts_reliable for the clang producer.",
        ),
        _E(
            owner="Param",
            field="is_va_list",
            value_type="bool",
            producing_backends=("clang",),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "CastXML never populates this fact at all — its blanket "
                "False is unconditionally correct-as-not-collected, on "
                "any schema version. Guarded by "
                "AbiSnapshot.clang_va_list_facts_reliable for the clang "
                "producer only (deliberately excludes hybrid — see the "
                "field's own docstring)."
            ),
        ),
        # ── Phase 5's first worked-example conversion ───────────────────
        _E(
            owner="RecordType",
            field="is_final",
            value_type="bool | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "`final` class-key specifier. No separate reliability "
                "flag: the field's own None already unambiguously means "
                "'dumper/loader could not determine' (DWARF/symbols-only "
                "mode, older snapshots) — a plain case (b) conversion."
            ),
        ),
    ]
)
