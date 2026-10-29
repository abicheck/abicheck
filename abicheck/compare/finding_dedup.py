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

"""Cross-tier and cross-detector deduplication of raw findings.

Two detectors (or two evidence tiers -- header AST and DWARF) can observe
the same change. :func:`deduplicate_ast_dwarf` collapses exact, same-kind
enum, and DWARF-vs-AST duplicates; :func:`deduplicate_cross_detector`
collapses findings whose canonical identity
(``finding_identity.resolve_change_identity``) coincides. Both return the
surviving list; the disposition pipeline records each dropped duplicate as
``deduplicated`` (``policy.finding_disposition.pipeline``).

The enum-spelling bridge (:func:`enum_canonical_names`) lives here because
cross-detector dedup is what needs it first; source-location enrichment reuses
it. Moved out of the legacy ``diff_filtering.py`` (ADR-061).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from ..diff_helpers import (
    canonicalize_record_symbol,
    cross_tier_transition,
    depth_aware_bare_name,
    record_canonical_names,
)
from ..model import AbiSnapshot
from ..model.change import SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER, Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId
from .dedup_key import hashable_value
from .finding_identity import resolve_change_identity
from .source_replay_twins import drop_replay_twins
from .variable_names import qualified_variable_name

# Mapping from DWARF change kinds to their AST equivalents for deduplication.
_DWARF_TO_AST_EQUIV: dict[ChangeKind, set[ChangeKind]] = {
    ChangeKind.STRUCT_SIZE_CHANGED: {ChangeKind.TYPE_SIZE_CHANGED},
    ChangeKind.STRUCT_ALIGNMENT_CHANGED: {ChangeKind.TYPE_ALIGNMENT_CHANGED},
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED: {ChangeKind.TYPE_FIELD_OFFSET_CHANGED},
    ChangeKind.STRUCT_FIELD_REMOVED: {ChangeKind.TYPE_FIELD_REMOVED},
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED: {ChangeKind.TYPE_FIELD_TYPE_CHANGED},
}

# Enum change kinds eligible for same-kind symbol-based dedup (FIX-C).
# Scoped to enum kinds only to avoid incorrectly merging legitimately
# different changes that share the same kind+symbol.
_ENUM_DEDUP_KINDS = frozenset(
    {
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
    }
)


#: The four enum kinds the bare/qualified bridge (`enum_canonical_names`/`canonicalize_enum_symbol`) applies to -- deliberately a NARROWER set than `_DEDUP_CATEGORIES` (which also covers function/variable/version dedup categories unrelated to enums). Codex review, fresh evidence: an earlier revision gated the bridging on `c.kind in _DEDUP_CATEGORIES` directly, so an unrelated symbol from any OTHER category whose spelling happened to coincide with a registered enum alias (e.g. an extern-C `FUNC_REMOVED` named "Color" when a `detail::Color` enum exists) had its `Change.qualified_name` incorrectly rewritten to the enum's qualified spelling -- corrupting an unrelated finding's identity, and doing so permanently, since the later `EnrichSourceLocations` step refuses to overwrite an already-populated `qualified_name`.
ENUM_QUALIFICATION_KINDS = frozenset(
    {
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
    }
)


def enum_canonical_names(snap: AbiSnapshot | None) -> dict[str, str]:
    """Bare/qualified enum-name -> canonical (qualified-if-known) spelling.

    Bridges two independent, individually-correct conventions this
    codebase's two enum detectors use for ``Change.symbol``:
    ``diff_types._diff_enums`` (header/castxml tier) always keys off
    ``EnumType.name``, which stays deliberately *bare* for DWARF-parity
    reasons (see ``EnumType.qualified_name``'s own docstring) — while
    ``diff_platform._diff_enum_layouts`` (DWARF tier) keys directly off the
    DWARF-derived dict, whose keys are always fully qualified
    (``dwarf_metadata._process_enum``: ``f"{scope_prefix}::{name}"``).
    Without this bridge, the exact same enum change reported by both tiers
    resolves to two different :mod:`finding_identity` identities (one
    bare, one qualified) and neither detector's own cross-detector dedup
    can ever recognize them as the same finding.

    Maps both a bare name and the qualified name to the same canonical
    (qualified) value, so a lookup succeeds regardless of which spelling a
    given tier's ``Change.symbol`` used.

    Only registers a bare-name entry when ``EnumType.qualified_name`` is
    actually known and unambiguous: an enum with no recorded qualification
    carries no bridging information at all, so mapping its bare name to
    itself would be a no-op that exists only to set ``Change.qualified_name``
    to a value identical to ``Change.symbol`` — and other consumers of that
    field (e.g. ``post_processing_reachability.py``'s internal-namespace
    check) treat "qualified_name is set" as meaningful evidence on its own,
    regardless of value (Codex review, fresh evidence).

    A bare name is registered only when it uniquely identifies one
    qualified enum -- two distinct enums sharing a bare name (``a::Color``/
    ``b::Color``) must never have one silently bridged to the other's
    spelling. The fully-qualified key is never ambiguous -- two enums
    cannot share it -- so it is always safe to register. An unqualified
    global enum sharing a bare name is itself a competitor here -- skipping
    it let a global ``Color`` alongside a namespaced ``ns::Color`` wrongly
    register ``Color -> ns::Color`` (Codex review, found on this function's
    ``RecordType`` sibling) -- including a DWARF-only enum, which
    contributes no ``snap.enums`` entry at all and needs
    ``snap.dwarf.enums``'s own keys scanned directly.
    """
    if snap is None:
        return {}
    by_bare: dict[str, set[str | None]] = {}
    out: dict[str, str] = {}
    for e in snap.declarations.enums:
        if e.qualified_name:
            by_bare.setdefault(e.name, set()).add(e.qualified_name)
            out[e.qualified_name] = e.qualified_name
        else:
            by_bare.setdefault(e.name, set()).add(None)
    from .debug_layout_view import debug_layout_view

    for key in debug_layout_view(snap).enums:
        bare = depth_aware_bare_name(key)
        if bare != key:
            by_bare.setdefault(bare, set()).add(key)
        else:
            by_bare.setdefault(key, set()).add(None)
    for bare, qualified_names in by_bare.items():
        if len(qualified_names) == 1:
            candidate = next(iter(qualified_names))
            if candidate is not None:
                out[bare] = candidate
    return out


def canonicalize_enum_symbol(symbol: str, enum_names: dict[str, str]) -> str | None:
    """Translate *symbol* (an enum's own name, or ``"Enum::member"``) to its
    canonical spelling via *enum_names* (see :func:`enum_canonical_names`).

    Tries a whole-symbol match first (``ENUM_UNDERLYING_SIZE_CHANGED``,
    ``ENUM_BECAME_SCOPED``, ... — ``symbol`` is the bare enum name itself),
    then splits off the trailing ``"::member"`` (``ENUM_MEMBER_REMOVED``,
    ``ENUM_MEMBER_VALUE_CHANGED``, ...) and translates only the enum-name
    prefix — ``rpartition`` splits on the *last* ``"::"``, which is always
    the member separator regardless of how many scope components the
    enum's own qualified name carries. Returns ``None`` when *enum_names*
    recognizes neither shape (an unrelated symbol, or genuinely no
    qualified spelling known from either tier).
    """
    canonical = enum_names.get(symbol)
    if canonical is not None:
        return canonical
    if "::" in symbol:
        prefix, _, member = symbol.rpartition("::")
        canonical_prefix = enum_names.get(prefix)
        if canonical_prefix:
            return f"{canonical_prefix}::{member}"
    return None


#: Kinds whose colliding-group handling can emit more than one finding for
#: an identical (kind, description, symbol, old_value, new_value) tuple --
#: the only kinds `_dedup_exact` needs `entity_id`/`disambiguator` to tell
#: apart (Codex review, PR #1078, twenty-first round). Not every kind:
#: `diff_types.py`'s AST-based `FIELD_RENAMED` sets a real `entity_id`, but
#: `diff_platform.py`'s DWARF-layout-based `FIELD_RENAMED` for the same
#: rename does not -- an unscoped key would stop that pair from collapsing
#: across evidence tiers, reintroducing a duplicate the pre-sixteenth-round
#: key never had.
_OCCURRENCE_AWARE_KINDS = frozenset(
    {
        ChangeKind.TYPEDEF_REMOVED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.TYPEDEF_VERSION_SENTINEL,
        ChangeKind.CONSTANT_ADDED,
        ChangeKind.CONSTANT_CHANGED,
        ChangeKind.CONSTANT_REMOVED,
    }
)


def _dedup_exact(
    changes: list[Change], ambiguous: Mapping[EntityId, str] | None = None
) -> list[Change]:
    """Pass 1: collapse entries with the same (kind, description, symbol,
    old_value, new_value), plus (entity_id, disambiguator) for
    :data:`_OCCURRENCE_AWARE_KINDS`.

    **Not just (kind, description)** (Codex review, PR #1078, sixteenth
    round): `compare.typedefs`/`compare.constants`'s occurrence-level
    collision handling deliberately emits one finding per contributing
    entity for a whole colliding group's removal/addition, and two such
    entities sharing a rendered alias produce byte-identical `description`
    text by construction -- the old key silently collapsed two distinct,
    independently-provable findings into one (`test_diff_layout.py`'s
    `test_second_type_still_compared_when_first_shares_its_bare_name` names
    a related, separately-tracked risk for bare-name-keyed `RecordType`
    findings that this key does not claim to close). `old_value`/`new_value`
    go through `hashable_value` since they are not always hashable scalars
    (`diff_python.py` stores lists).

    **`entity_id` alone still isn't enough** (Codex review, PR #1078,
    seventeenth round): two genuine ODR/multi-TU occurrences legitimately
    share one `EntityId`, distinguished only by `OccurrenceId.disambiguator`
    -- if both are removed with the same value, `entity_id.key` alone still
    collapses them. `Change.disambiguator` carries that value, closing the
    residual gap the sixteenth round's own fix left open.

    **Scoped to `_OCCURRENCE_AWARE_KINDS`, not every kind** (Codex review,
    PR #1078, twenty-first round): see that constant's own docstring --
    unlike `disambiguator`, `entity_id` predates this PR and is already
    asymmetrically populated across evidence tiers for other kinds.

    **Plus the qualified identity for a type whose bare name is ambiguous**
    (*ambiguous*: ``type_symbol_disambiguation.AmbiguousTypeNames.by_entity``).
    The header backends label type findings with the bare name, so two
    namesakes in different namespaces that change identically (both
    ``Ctx``es grow by one ``int``) render byte-identical findings, and this
    pass dropped the second -- a lost break, not a duplicate. Keyed only for
    those entities, so every other finding's key, and the cross-tier
    asymmetry above, is untouched."""
    result: list[Change] = []
    seen: set[tuple[object, ...]] = set()
    ambiguous = ambiguous or {}
    for c in changes:
        key: tuple[object, ...] = (
            c.kind.value,
            c.description,
            c.symbol,
            hashable_value(c.old_value),
            hashable_value(c.new_value),
        )
        if c.kind in _OCCURRENCE_AWARE_KINDS:
            key = key + (
                c.entity_id.key if c.entity_id is not None else None,
                c.disambiguator,
            )
        elif c.entity_id is not None and c.entity_id in ambiguous:
            key = key + (ambiguous[c.entity_id],)
        if key in seen:
            continue
        seen.add(key)
        result.append(c)
    return result


def _dedup_enum_same_kind(
    changes: list[Change], ambiguous: Mapping[EntityId, str] | None = None
) -> list[Change]:
    """Pass 2: for enum change kinds, keep the best entry per (kind, symbol).

    Prefers entries with populated ``old_value``/``new_value`` fields,
    using longer description as tiebreaker. Like :func:`_dedup_exact`, an
    enum whose bare name is *ambiguous* is keyed by its qualified identity
    too, so two namespaced enums sharing a bare name never collapse.
    """
    ambiguous = ambiguous or {}

    def _key(c: Change) -> tuple[str, str, str | None]:
        eid = c.entity_id
        return (c.kind.value, c.symbol, ambiguous.get(eid) if eid else None)

    best_enum: dict[tuple[str, str, str | None], Change] = {}
    for c in changes:
        if c.kind not in _ENUM_DEDUP_KINDS:
            continue
        key = _key(c)
        if key not in best_enum:
            best_enum[key] = c
        else:
            existing = best_enum[key]
            c_has_vals = bool(c.old_value) or bool(c.new_value)
            e_has_vals = bool(existing.old_value) or bool(existing.new_value)
            if c_has_vals and not e_has_vals:
                best_enum[key] = c
            elif not c_has_vals and e_has_vals:
                pass  # keep existing
            elif len(c.description) > len(existing.description):
                best_enum[key] = c

    result: list[Change] = []
    for c in changes:
        if c.kind in _ENUM_DEDUP_KINDS:
            if best_enum.get(_key(c)) is not c:
                continue  # not the winner — drop
        result.append(c)
    return result


def _dedup_cross_kind(
    changes: list[Change], record_names: dict[str, str] | None = None
) -> list[Change]:
    """Pass 3: drop a DWARF finding when an equivalent AST finding exists.

    Handles exact and parent-type matches for field-qualified symbols
    (FIX-F). *record_names* bridges a namespaced type's bare-vs-qualified
    spelling first. The parent-type match also requires ``field_name``
    agreement when both findings carry one (Codex review): an AST-tier
    field symbol names only the parent type, so without this a DWARF
    finding for one field could be dropped merely because a *different*
    field of the same type also changed at the AST tier. A match also
    requires the two findings' own old/new transitions to agree (see
    :func:`cross_tier_transition`) -- a mere kind+symbol match is not
    enough when the header and DWARF evidence disagree on the transition
    itself, e.g. a stale header vs. inconsistent extractor evidence. Only indexes the AST-tier kinds this dedup ever looks up (``_DWARF_TO_AST_EQUIV``'s values) -- indexing every kind unconditionally crashed on a kind (e.g. ``PYTHON_STABLE_ABI_VIOLATION``) whose ``old_value``/``new_value`` is a list, unhashable for the transition-agreement set below (Codex review).
    """
    names = record_names or {}
    _Transition = tuple[object, object] | None
    ast_findings: dict[tuple[str, str], set[_Transition]] = {}
    ast_field_findings: dict[tuple[str, str, str], set[_Transition]] = {}
    _relevant_ast = {ak for aks in _DWARF_TO_AST_EQUIV.values() for ak in aks}
    for c in (c for c in changes if c.kind in _relevant_ast):
        canon = canonicalize_record_symbol(
            c.symbol, names, c.qualified_name, c.field_name
        )
        transition = cross_tier_transition(c)
        ast_findings.setdefault((c.kind.value, canon), set()).add(transition)
        if c.field_name is not None:
            ast_field_findings.setdefault(
                (c.kind.value, canon, c.field_name), set()
            ).add(transition)

    def _matches(
        transitions: set[_Transition] | None,
        dwarf_transition: _Transition,
    ) -> bool:
        # None (no transitions recorded for a key never seen) -> not present. A None entry inside the set itself means "kind with no independent transition" (e.g. a removal) -- kind+symbol alone is agreement there, matching the pre-value-gate behavior.
        if not transitions:
            return False
        return dwarf_transition is None or dwarf_transition in transitions

    _DWARF_FIELD_LEVEL_KINDS = {
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    }

    result: list[Change] = []
    for c in changes:
        equiv_ast_kinds = _DWARF_TO_AST_EQUIV.get(c.kind)
        if equiv_ast_kinds:
            canon_symbol = canonicalize_record_symbol(
                c.symbol, names, None, c.field_name
            )
            dwarf_transition = cross_tier_transition(c)

            # Exact symbol match
            if any(
                _matches(ast_findings.get((ak.value, canon_symbol)), dwarf_transition)
                for ak in equiv_ast_kinds
            ):
                continue

            # Parent-type match (FIX-F): "Point::x" → check "Point". Only for
            # field-level changes; a type-level change (size, alignment) must
            # not match parent — "Outer::Inner" is a nested type, not a field.
            if c.kind in _DWARF_FIELD_LEVEL_KINDS and "::" in canon_symbol:
                parent = canon_symbol.rsplit("::", 1)[0]
                if c.field_name is not None:
                    if any(
                        _matches(
                            ast_field_findings.get((ak.value, parent, c.field_name)),
                            dwarf_transition,
                        )
                        for ak in equiv_ast_kinds
                    ):
                        continue
                elif any(
                    _matches(ast_findings.get((ak.value, parent)), dwarf_transition)
                    for ak in equiv_ast_kinds
                ):
                    continue

        result.append(c)
    return result


def deduplicate_ast_dwarf(
    changes: list[Change],
    old: AbiSnapshot | None = None,
    new: AbiSnapshot | None = None,
) -> list[Change]:
    """Remove DWARF findings that duplicate an AST finding for the same symbol.

    Three dedup passes: (1) exact dedup on ``(kind, description)``; (2)
    same-kind symbol dedup (FIX-C) for enum kinds; (3) cross-kind dedup,
    bridging a bare-vs-qualified spelling mismatch via *old*/*new* first
    (see :func:`_dedup_cross_kind`).
    """
    from .type_symbol_disambiguation import ambiguous_type_spellings

    ambiguous = ambiguous_type_spellings(old, new).by_entity
    stage1 = _dedup_exact(changes, ambiguous)
    stage2 = _dedup_enum_same_kind(stage1, ambiguous)
    record_names = {**record_canonical_names(old), **record_canonical_names(new)}
    return _dedup_cross_kind(stage2, record_names)


#: An ELF-only removal and a header-aware visibility finding name one export
#: loss. ``func_visibility_changed`` fires when the public declaration survives
#: but its export is gone (``diff_symbols._check_removed_function``); the L0
#: symbols-only fold (``l0_export_delta.fold_l0_hard_removals``) reports the
#: same loss again as ``func_removed_elf_only``, because it is appended to the
#: header-scoped diff without reading it. One event at two evidence tiers: the
#: header-aware finding is kept, since it also carries the declaration's
#: surface facts. The pair is keyed on the mangled symbol both producers share.
_EXPORT_LOSS_ELF_ONLY_PARTNER: dict[ChangeKind, ChangeKind] = {
    ChangeKind.FUNC_VISIBILITY_CHANGED: ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ChangeKind.VAR_VISIBILITY_CHANGED: ChangeKind.VAR_REMOVED_ELF_ONLY,
}


def _drop_elf_only_duplicates_of_visibility(changes: list[Change]) -> list[Change]:
    """Drop an ELF-only removal whose symbol a visibility finding already reports.

    Matches the exact ``(ELF-only kind, symbol)`` pair each visibility finding
    implies, so a variable visibility finding never covers a function removal.
    Only the ELF-only half is dropped; the visibility finding stays, and a
    symbol with no visibility partner is untouched. The drop is recorded as
    ``deduplicated`` by the disposition pipeline, so it stays auditable
    (``AGENTS.md``: record before disposing).
    """
    covered = {
        (_EXPORT_LOSS_ELF_ONLY_PARTNER[c.kind], c.symbol)
        for c in changes
        if c.kind in _EXPORT_LOSS_ELF_ONLY_PARTNER and c.symbol
    }
    if not covered:
        return changes
    return [c for c in changes if (c.kind, c.symbol) not in covered]


def _drop_constant_value_restatements(changes: list[Change]) -> list[Change]:
    """Drop a ``VAR_VALUE_CHANGED`` that restates a ``CONSTANT_CHANGED``.

    A ``const``/``constexpr`` variable with a compile-time initializer is one
    observation at two header-tier detectors: ``compare.constants`` reports the
    qualified constant (``K_S::K_V``), and ``diff_types_abicc_parity`` reports the
    same variable's value as ``VAR_VALUE_CHANGED`` under its mangled symbol. The
    constant is kept: it is the declaration-level owner (qualified name, raw
    values) and the finding the clang frontend emits alone. The variable copy is
    matched on its qualified name and its old/new value, one for one, so a
    different variable with the same bare name never pairs.
    """
    constant_values: Counter[tuple[str, object, object]] = Counter(
        (c.symbol, c.old_value, c.new_value)
        for c in changes
        if c.kind is ChangeKind.CONSTANT_CHANGED
    )
    result: list[Change] = []
    for c in changes:
        if c.kind is ChangeKind.VAR_VALUE_CHANGED and c.entity_id is not None:
            key = (
                qualified_variable_name(c.entity_id, c.symbol, mangled=c.symbol),
                hashable_value(c.old_value),
                hashable_value(c.new_value),
            )
            if constant_values[key] > 0:
                constant_values[key] -= 1
                continue
        result.append(c)
    return result


#: A deleted function is implicitly inline ([dcl.fct.def.delete]/4) and its
#: export is gone. Both facts are consequences of the one deletion that
#: ``FUNC_DELETED`` already reports, so they do not stand as separate findings.
_DELETION_CONSEQUENCE_KINDS = frozenset(
    {ChangeKind.FUNC_VISIBILITY_CHANGED, ChangeKind.FUNC_BECAME_INLINE}
)
_DELETION_PRIMARY_KINDS = frozenset(
    {ChangeKind.FUNC_DELETED, ChangeKind.FUNC_DELETED_DWARF}
)


def _drop_deletion_consequences(changes: list[Change]) -> list[Change]:
    """Fold the export-loss and became-inline findings of a deleted function
    into its ``FUNC_DELETED`` finding, matched on the mangled symbol."""
    deleted = {c.symbol for c in changes if c.kind in _DELETION_PRIMARY_KINDS}
    if not deleted:
        return changes
    return [
        c
        for c in changes
        if not (c.kind in _DELETION_CONSEQUENCE_KINDS and c.symbol in deleted)
    ]


#: The kinds whose cross-detector duplicates share one dedup identity, mapped
#: to their category label. :func:`deduplicate_cross_detector` collapses on
#: this table through :func:`dedup_identity_key`, and acknowledgment matching
#: (``policy.disposition_acknowledgment``) reads the same key, so the two agree
#: on which findings are one observation.
DEDUP_CATEGORY_BY_KIND: dict[ChangeKind, str] = {
    ChangeKind.FUNC_REMOVED: "func_removal",
    ChangeKind.FUNC_REMOVED_ELF_ONLY: "func_removal",
    ChangeKind.FUNC_ADDED: "func_addition",
    # Same category as `FUNC_ADDED`, mirroring the `func_removal` pair
    # above: the two are the same observation at two evidence tiers, so a
    # symbol that somehow reached both detectors must dedup to one
    # finding rather than be counted as two additions.
    ChangeKind.FUNC_ADDED_ELF_ONLY: "func_addition",
    ChangeKind.VAR_REMOVED: "var_removal",
    # Same category as `VAR_REMOVED`, mirroring the `func_removal` pair
    # above: the two are the same observation at two evidence tiers.
    ChangeKind.VAR_REMOVED_ELF_ONLY: "var_removal",
    ChangeKind.VAR_ADDED: "var_addition",
    # Version node removal and version definition removal both fire for
    # the same version string.  Keep the more specific node-level change.
    ChangeKind.SYMBOL_VERSION_NODE_REMOVED: "version_def_removal",
    ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED: "version_def_removal",
    # L1 (DWARF)/L2 (header) enum detectors independently report the
    # same ChangeKind for the same member change; resolve_change_identity
    # already self-maps these four kinds -- missing was including them
    # here, so identity resolution (and dedup) is even attempted.
    ChangeKind.ENUM_MEMBER_REMOVED: "enum_member_removed",
    ChangeKind.ENUM_MEMBER_VALUE_CHANGED: "enum_member_value_changed",
    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED: "enum_last_member_value_changed",
    ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED: "enum_underlying_size_changed",
}


def dedup_identity_key(change: Change) -> str | None:
    """The identity :func:`deduplicate_cross_detector` collapses *change* on.

    ``None`` for a kind outside :data:`DEDUP_CATEGORY_BY_KIND`: this pass never
    collapses it, so it has no duplicate to share an identity with. Acknowledgment
    matching uses this key so a deduplicated twin and its surviving finding count
    as one observed change.
    """
    if change.kind not in DEDUP_CATEGORY_BY_KIND:
        return None
    return resolve_change_identity(change).primary_id


def deduplicate_cross_detector(
    changes: list[Change],
    old: AbiSnapshot | None = None,
    new: AbiSnapshot | None = None,
) -> list[Change]:
    """Remove cross-detector duplicates that the per-detector guards may miss.

    Centralised dedup applied after all detectors have run. The dedup key is
    ``finding_identity.resolve_change_identity(c).primary_id`` (ADR-049 Phase 2
    wiring) rather than a hand-rolled ``(change_category, symbol)`` tuple --
    ``resolve_change_identity`` already collapses exactly these kind pairs
    onto one shared ``category:<name>`` discriminator via its own
    ``_EQUIVALENT_CHANGE_CATEGORIES`` table (kept in sync with the categories
    below by that module's own docstring), while additionally applying its
    tiered canonical/normalized/reduced identity to the entity portion of the
    key -- the same primitive ``diff_symbols._diff_functions`` already uses
    for its extern-C matching fallback, generalized here per
    ``docs/contribute/plans/public-contract-default.md``'s Phase 2 section.

    Categories:
    - "func_removal": FUNC_REMOVED, FUNC_REMOVED_ELF_ONLY
    - "func_addition": FUNC_ADDED, FUNC_ADDED_ELF_ONLY
    - "var_removal": VAR_REMOVED, VAR_REMOVED_ELF_ONLY
    - "var_addition": VAR_ADDED
    - "enum_member_removed"/"enum_member_value_changed"/
      "enum_last_member_value_changed"/"enum_underlying_size_changed": each
      is the *same* ``ChangeKind`` reported independently by the L2
      header-tier enum detector (``diff_types._diff_enums``, keyed by
      ``EnumType.name`` — deliberately bare, see that field's docstring)
      and the L1 DWARF-tier one (``diff_platform._diff_enum_layouts``,
      keyed by the DWARF dict's own fully-qualified name) whenever both
      tiers observe the same enum. Reaching this dict alone is not
      sufficient to collapse them — ``resolve_change_identity``'s
      NORMALIZED-tier identity also needs the two spellings bridged to the
      same qualified name, which ``_enrich_source_locations``'s
      ``enum_canonical_names``/``canonicalize_enum_symbol`` (this module,
      above) does before this step ever runs. The two module-import order
      in ``checker.py`` (``diff_platform`` before ``diff_types``,
      alphabetical) means the richer, qualified DWARF-tier finding is
      always the "first occurrence" kept below.

    Within each category, the first occurrence wins (preserving detector
    priority order from the ``compare()`` spec list).
    """
    # A symbol-version-node bump (e.g. LLVM_17 -> LLVM_18.1 on every symbol in a major release) makes BOTH version detectors fire per symbol with the same old->new transition: SYMBOL_MOVED_VERSION_NODE (node label moved) and SYMBOL_VERSION_ALIAS_CHANGED (default version changed, old not retained as an alias). Drop the alias-change duplicate where a node move already covers the same (symbol, old -> new) -- halves the version-bump noise on real libraries (libLLVM 17->18: ~46k instead of ~92k risk findings). Matches on (symbol, old_value, new_value); if that ever diverges the dedup simply no-ops (both findings kept).
    moved_transitions: set[tuple[str, str | None, str | None]] = {
        (c.symbol, c.old_value, c.new_value)
        for c in changes
        if c.kind is ChangeKind.SYMBOL_MOVED_VERSION_NODE
    }

    # Runs BEFORE EnrichSourceLocations, so the four enum categories above
    # cannot rely on it having bridged a bare/qualified enum-name mismatch
    # into `Change.qualified_name` -- bridge it here instead (a caller with
    # no `old`/`new` degrades to a missed dedup, never an incorrect one).
    if old is not None or new is not None:
        from .type_symbol_disambiguation import (
            ambiguous_type_spellings,
            qualified_type_label,
        )

        old_enum_names = enum_canonical_names(old)
        new_enum_names = enum_canonical_names(new)
        ambiguous = ambiguous_type_spellings(old, new).by_entity
        for c in changes:
            if c.kind in ENUM_QUALIFICATION_KINDS and not c.qualified_name:
                # The bridge below maps a bare name to *one* qualified name,
                # so two namesake enums would share an identity and the
                # second finding be dropped; their own entity says which.
                if c.entity_id is not None and c.entity_id in ambiguous:
                    c.qualified_name = qualified_type_label(c)
                    continue
                qual = canonicalize_enum_symbol(
                    c.symbol, old_enum_names
                ) or canonicalize_enum_symbol(c.symbol, new_enum_names)
                if qual:
                    c.qualified_name = qual

    changes = _drop_elf_only_duplicates_of_visibility(changes)
    changes = _drop_deletion_consequences(changes)
    changes = _drop_constant_value_restatements(changes)
    changes = drop_replay_twins(changes)
    seen: set[str] = set()
    result: list[Change] = []
    for c in changes:
        # Only collapse the alias-change into a co-reported node-move when the old default version is NOT retained as an alias — the case the node-move already fully describes. When the alias IS retained it carries distinct, compatible information the node-move's wording would otherwise misrepresent, so it must survive.
        if (
            c.kind is ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED
            and SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER in (c.description or "")
            and (c.symbol, c.old_value, c.new_value) in moved_transitions
        ):
            continue
        identity_key = dedup_identity_key(c)
        if identity_key is not None:
            if identity_key in seen:
                continue
            seen.add(identity_key)
        result.append(c)
    return result
