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

"""Per-kind facts change identity needs: which kinds name a symbol, which
are batch-shaped, which pairs are one logical event, which carry a type."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...model.change import Change


#: ChangeKind slug *prefixes* that are unambiguously "about one
#: function/variable symbol" (see ``checker_policy.py``'s
#: ``func_*``/``var_*``/``ifunc_*``/``symbol_*`` naming convention). The
#: ``symbol_*`` family (``symbol_binding_changed``, ``symbol_type_changed``,
#: ``symbol_size_changed``, ...) is the ELF-level symbol-table diff --
#: equally unambiguous, added after Codex review flagged it missing.
_SYMBOL_LEVEL_KIND_PREFIXES = (
    "func_",
    "var_",
    "ifunc_",
    "symbol_",
    # A function's own parameters/return value are never independently
    # type-level -- ``param_*``/``return_*`` always describe one function's
    # signature (verified against every current param_*/return_* slug).
    "param_",
    "return_",
)

#: Individually-named kinds that don't share one of the prefixes above but
#: are still unambiguously about one function/variable symbol (Codex
#: review, five rounds: ``VIRTUAL_METHOD_ADDED``/``CALLING_CONVENTION_CHANGED``/
#: ``METHOD_ACCESS_CHANGED``/``DEFAULT_ARGUMENT_CHANGED``/the two
#: function-template ``TEMPLATE_*_TYPE_CHANGED`` kinds/``TLS_VAR_SIZE_CHANGED``/
#: ``PROTECTED_VISIBILITY_CHANGED`` were flagged as missing in turn -- the
#: last two verified against ``diff_platform.py``'s ``_diff_tls_symbols``/
#: ``_diff_protected_visibility``, both of which pass a real exported
#: variable's mangled name as ``symbol=sym_name``).
#:
#: This allowlist-of-individual-kinds approach has a structural limit: it
#: can only ever be as complete as whatever review has spot-checked so far
#: against ~395 `ChangeKind` values, and each round has found another real
#: gap in the same way. That is an accepted, deliberate property of this
#: primitive, not an oversight to keep closing case-by-case -- the module's
#: own contract (see :func:`_is_symbol_level_kind`'s docstring) is that
#: missing an entry here only causes an unnecessary degrade to NORMALIZED,
#: never a wrong CANONICAL promotion, so incompleteness is safe by
#: construction. Extend this set when a *concrete* wiring change (Phase 2's
#: remaining work: actually consuming this identity in
#: ``diff_symbols.py``/``diff_filtering.py``) needs a specific kind
#: reclassified, rather than continuing to enumerate hypothetical gaps
#: against an unwired primitive.
_SYMBOL_LEVEL_KIND_SLUGS = frozenset(
    {
        "virtual_method_added",
        "calling_convention_changed",
        "value_abi_trait_changed",
        "hidden_friend_added",
        "hidden_friend_removed",
        "method_access_changed",
        "default_argument_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        "tls_var_size_changed",
        "protected_visibility_changed",
        # diff_platform_elf_symbols.py's alignment check passes the real
        # exported data object's symbol (symbol=sym_name), the same shape
        # as tls_var_size_changed/protected_visibility_changed above
        # (Codex review).
        "exported_object_alignment_reduced",
    }
)

#: ``symbol_*`` kinds that are the one exception to "``symbol_*`` is always
#: entity-bearing" -- their ``Change.symbol`` is a version-node/requirement
#: label (``diff_versioning.py``/``diff_platform_elf_symbols.py``: e.g.
#: ``symbol=node`` where ``node`` is a string like ``"GLIBC_2.17"``) or a
#: synthetic batch identifier (``diff_symbols_renames.py``'s
#: ``emit_prefix_batch_rename``/``emit_namespace_move_batches``:
#: ``symbol=f"batch_rename:{prefix}*"`` and
#: ``symbol=f"batch_rename:{old_segment}->{new_segment}"``), never a real exported function or
#: variable name (Codex review: a version label or batch id that happens to
#: resemble a mangling must not be promoted to CANONICAL and aliased
#: alongside an actual function's mangled name). Verified per-kind against
#: the actual ``make_change(..., symbol=...)`` call site, not inferred from
#: the slug alone -- ``symbol_moved_version_node``/
#: ``symbol_version_alias_changed`` also live in ``diff_versioning.py``/
#: ``diff_platform.py`` but pass a real exported symbol name and are
#: correctly NOT in this set.
_NON_ENTITY_SYMBOL_KIND_SLUGS = frozenset(
    {
        "symbol_version_defined_removed",
        "symbol_version_defined_added",
        "symbol_version_required_added",
        "symbol_version_required_added_compat",
        "symbol_version_required_removed",
        "symbol_version_node_removed",
        "symbol_renamed_batch",
    }
)


#: ``resolve_change_identity`` only attempts to interpret ``change.symbol``
#: as a mangled name when its kind matches one of the two sets above (and
#: is not in :data:`_NON_ENTITY_SYMBOL_KIND_SLUGS`) -- a type-level kind's
#: ``symbol`` is a type name (e.g. a type named ``_Zebra`` structurally
#: resembles an Itanium mangling but is not one), and
#: ``change.qualified_name`` -- the signal that would normally catch this
#: via ``normalize_mangled_name``'s mangled-vs-plain-name check -- is
#: documented as unset for exactly this case (``Change.qualified_name``'s
#: docstring: "None when no matching Function record was found (e.g.
#: type-level changes)"), so it cannot be relied on alone (Codex review).
#: Missing a real symbol-level kind here only means an unnecessary degrade
#: to the NORMALIZED tier, never a wrong CANONICAL promotion -- the same
#: ambiguity-safe-fallback bias as everywhere else in this module.
def _is_symbol_level_kind(kind_value: str) -> bool:
    if kind_value in _NON_ENTITY_SYMBOL_KIND_SLUGS:
        return False
    return kind_value.startswith(_SYMBOL_LEVEL_KIND_PREFIXES) or (
        kind_value in _SYMBOL_LEVEL_KIND_SLUGS
    )


#: A handful of ``symbol_*`` kinds are emitted by *two* different detectors
#: under the same :class:`~abicheck.checker_policy.ChangeKind` -- one
#: per-symbol (``change.symbol`` names the one real entity that transitioned),
#: the other library-level/batch (an arbitrary, alphabetically-first affected
#: symbol is sampled into ``change.symbol`` as a "spokesperson" for the whole
#: release). ``diff_platform_elf_symbols.py``'s ``_check_gained_gnu_unique``
#: is the concrete case: it fires once per release when a build newly turns
#: on ``-fgnu-unique``, reporting *how many* exports gained
#: ``STB_GNU_UNIQUE`` binding rather than describing one specific symbol's
#: transition (Codex review). Promoting that arbitrary sample to CANONICAL
#: would alias it against whatever *other*, unrelated finding genuinely owns
#: that mangled name.
#:
#: The two shapes share a kind but never share ``old_value``: only the batch
#: call site passes this literal sentinel (verified against the one
#: ``make_change`` call that produces it); the sibling per-symbol detector
#: (``_check_binding_change``) always passes a real ``SymbolBinding`` enum
#: value (``"GLOBAL"``, ``"WEAK"``, ``"LOCAL"``, ...) as ``old``, never this
#: sentence -- so matching on it can't misclassify a legitimate per-symbol
#: finding.
_BATCH_SHAPED_OLD_VALUES = {
    "symbol_binding_became_unique": "(no GNU_UNIQUE exports)",
}

#: Kinds with no per-symbol sibling detector at all -- every emission is
#: batch-shaped, so no value-based sentinel is needed to tell it apart from
#: a legitimate per-entity finding (unlike :data:`_BATCH_SHAPED_OLD_VALUES`'s
#: kinds, which share a slug with a genuine per-symbol detector).
#: ``diff_platform_elf_symbols.py``'s ``_diff_allocator_replacement`` is the
#: concrete case (Codex review): it fires once per release when a build
#: newly gains/loses a global allocator replacement, sampling the
#: alphabetically-first affected allocator export into both ``symbol`` and
#: (via ``detail``) ``description`` -- the exact same "arbitrary sample as
#: spokesperson" shape as the GNU_UNIQUE case above, just with no
#: SymbolBinding-style sibling to disambiguate against, since these two
#: ``ChangeKind`` values have exactly one producer each (verified: no other
#: ``make_change`` call site in the codebase emits either kind).
#:
#: ``visibility_leak`` joins this set for the same reason (Codex review,
#: fresh evidence): ``diff_platform_elf_dynamic.py``'s ``_diff_visibility_leak``
#: is the sole producer, fires once per release with a fixed
#: ``symbol="<visibility>"`` sentinel (never a real exported name), and
#: embeds up to five names sampled from unsorted ``old.functions`` into
#: ``description`` -- two semantically identical snapshots whose functions
#: happen to serialize in a different order would otherwise get different
#: discriminators (via ``description``) and fragment into separate
#: ``primary_id``s for the same logical finding.
#:
#: ``header_binary_context_mismatch`` is deliberately NOT in this set,
#: unlike the two cases above -- see :data:`_STRUCTURED_EVIDENCE_KIND_SLUGS`
#: below for why blanket batch-shaped treatment is wrong for it (Codex
#: review, fresh evidence, round 2: it would collapse two genuinely
#: different findings -- old-side and new-side -- into one identity).
_ALWAYS_BATCH_SHAPED_KIND_SLUGS = frozenset(
    {
        "allocator_replacement_added",
        "allocator_replacement_removed",
        "visibility_leak",
    }
)

#: ``header_binary_context_mismatch``: unlike the always-batch-shaped kinds
#: above, ``diff_layout_coherence.py``'s ``_mismatch_change`` can legitimately
#: emit TWO findings from a single comparison -- one for the old snapshot,
#: one for the new -- each with its OWN, possibly entirely different,
#: mismatched-record set, but the same ``symbol=snapshot.library`` value on
#: both (Codex review, fresh evidence, round 2). Treating this kind as
#: batch-shaped (clearing ``entity_symbol``/dropping ``description``, as
#: this module already does for the two kinds above) would make both
#: findings resolve to the identical primary_id regardless of their actual
#: content -- a real dedup collision between two distinct findings, not
#: just a specificity degrade. ``Change.affected_symbols`` (unlike
#: ``description``, whose up-to-five-name sample is order-dependent prose)
#: carries the finding's COMPLETE mismatched-record set as structured data
#: -- :func:`_change_discriminator` sorts it for these kinds instead of
#: using ``description``, so two orderings of the *same* set still collide
#: (fixing the original order-sensitivity report) while two *different*
#: sets (e.g. the old-side vs. new-side case) reliably do not. The residual
#: gap when both sides happen to have the exact same mismatched-record set
#: (Codex review, fresh evidence, round 2) is closed too:
#: :data:`_MISMATCH_SIDE_RE` extracts the side word from ``description``'s
#: stable, fixed-wording lead-in ("The old/new snapshot's...") -- unlike
#: the order-dependent five-name sample later in that same string, this
#: prefix never varies for a given side, so it's safe to fold into the
#: discriminator without reintroducing the original order-sensitivity bug.
_STRUCTURED_EVIDENCE_KIND_SLUGS = frozenset({"header_binary_context_mismatch"})

#: Matches ``diff_layout_coherence.py``'s ``_mismatch_change`` description
#: lead-in ("The old snapshot's ..." / "The new snapshot's ...") -- the
#: ONLY place ``Change`` carries which snapshot side a
#: ``header_binary_context_mismatch`` finding is about, since ``side`` is
#: never passed as ``old_value``/``new_value`` (both left ``None``) or any
#: other structured field. Anchored to this specific detector's exact,
#: hand-verified wording (not a general free-text heuristic) -- a
#: description that doesn't match this prefix (a future producer, a
#: hand-built test ``Change``) degrades ``side`` to ``""``, this module's
#: usual ambiguity-safe fallback.
_MISMATCH_SIDE_RE = re.compile(r"\AThe (old|new) snapshot's")


def _is_batch_shaped_change(change: Change, kind_value: str) -> bool:
    """True when *change* is a library-level/batch finding, not a per-entity one.

    See :data:`_BATCH_SHAPED_OLD_VALUES`/:data:`_ALWAYS_BATCH_SHAPED_KIND_SLUGS`
    -- gates :func:`resolve_change_identity` against promoting a batch
    finding's sampled symbol to a CANONICAL entity identity.
    """
    if kind_value in _ALWAYS_BATCH_SHAPED_KIND_SLUGS:
        return True
    sentinel = _BATCH_SHAPED_OLD_VALUES.get(kind_value)
    return sentinel is not None and change.old_value == sentinel


#: Change kinds ``diff_filtering`` already treats as one logical event
#: reported by two different detectors -- mirrored here (this leaf module
#: still doesn't import ``diff_filtering``, so the dependency direction stays
#: one-way: ``diff_filtering`` depends on this module for its
#: ``_deduplicate_cross_detector`` dedup key, ADR-049 Phase 2 wiring, not the
#: reverse). Keep in sync with the two mappings this generalizes:
#: ``finding_dedup.DEDUP_CATEGORY_BY_KIND``
#: (rich-vs-L0 function/variable add/remove, symbol-version-node pairs --
#: wired) and module-level ``_DWARF_TO_AST_EQUIV``'s two *whole-type* pairs
#: (``STRUCT_SIZE_CHANGED``/``TYPE_SIZE_CHANGED``,
#: ``STRUCT_ALIGNMENT_CHANGED``/``TYPE_ALIGNMENT_CHANGED``) -- safe to
#: collapse because ``symbol`` names the whole type on both sides, with no
#: field-level substructure to lose.
#:
#: Deliberately excludes ``_DWARF_TO_AST_EQUIV``'s three *field-level*
#: pairs (``STRUCT_FIELD_OFFSET_CHANGED``/``STRUCT_FIELD_REMOVED``/
#: ``STRUCT_FIELD_TYPE_CHANGED`` vs. their ``TYPE_FIELD_*`` counterparts):
#: the DWARF side field-qualifies ``symbol`` (``"Point::x"``,
#: ``diff_platform.py``), but the AST side does not (``symbol="Point"``,
#: the field name only in ``description`` via ``detail=fname``,
#: ``diff_types.py``) -- collapsing these to a bare category would make
#: two *different* AST-side field findings on the same struct (``Point::x``
#: vs. ``Point::y``) collide with each other, not just with their DWARF
#: counterpart (Codex review: caught exactly this for
#: ``TYPE_FIELD_OFFSET_CHANGED``). Safely fixing this needs a real
#: per-field discriminator this module doesn't
#: have a reliable source for (the two detectors don't encode field
#: identity in a common field) -- left as the conservative default
#: (full kind/old/new/description discriminator, no cross-detector
#: collision for these three kinds) rather than a fix that risks losing a
#: distinct field-level fact, matching this module's ambiguity-safe bias.
#:
#: The three enum entries (self-mapped: same kind on both sides, not a
#: kind pair) mirror ``finding_dedup._dedup_enum_same_kind``'s own
#: ``(kind, symbol)`` dedup key exactly (Codex review): its AST detector
#: (``diff_types.py``) uses the registry description template with no
#: embedded values, while its DWARF detector (``diff_platform.py``) passes
#: a bespoke ``description`` embedding ``"({old} → {new})"`` -- the same
#: "one logical event, two detectors' own wording" shape as the kind-pair
#: entries above, just sharing one kind slug instead of two. Both
#: detectors populate ``old_value``/``new_value`` identically
#: (``str(old_val)``/``str(new_val)``), so only ``description`` actually
#: needed dropping, but collapsing the full discriminator (like every
#: other entry here) matches ``_dedup_enum_same_kind``'s own key precisely
#: rather than assuming every current and future producer of these kinds
#: populates old/new consistently.
_EQUIVALENT_CHANGE_CATEGORIES = {
    "func_removed": "func_removal",
    "func_removed_elf_only": "func_removal",
    "func_added": "func_addition",
    # The addition counterpart of the `func_removed`/`func_removed_elf_only`
    # pair above, and paired for the identical reason: which of the two a
    # symbol gets depends only on which evidence tier observed it (a header
    # declaration vs. the export table alone), not on what happened. A
    # `finding_id:` suppression written against one must keep matching when a
    # later run has header evidence it did not, or vice versa.
    "func_added_elf_only": "func_addition",
    "var_removed": "var_removal",
    "var_added": "var_addition",
    # The data counterpart of the `func_added`/`func_added_elf_only` pair
    # above, for the identical reason: which of the two an exported data
    # symbol gets depends only on whether a public header declared it, not on
    # what the release did.
    "var_added_elf_only": "var_addition",
    # And the removal side of the same pair: `var_removed` and
    # `var_removed_elf_only` are one observation at two evidence tiers, so a
    # `finding_id:` suppression written against either must keep matching
    # when a later run has header evidence it did not, or vice versa.
    "var_removed_elf_only": "var_removal",
    "symbol_version_node_removed": "version_def_removal",
    "symbol_version_defined_removed": "version_def_removal",
    "struct_size_changed": "type_size_change",
    "type_size_changed": "type_size_change",
    "struct_alignment_changed": "type_alignment_change",
    "type_alignment_changed": "type_alignment_change",
    "enum_member_value_changed": "enum_member_value_changed",
    "enum_member_removed": "enum_member_removed",
    "enum_last_member_value_changed": "enum_last_member_value_changed",
    # diff_types._diff_enums (L2 header tier, bare EnumType.name) and
    # diff_platform._diff_enum_layouts (L1 DWARF tier, fully-qualified DWARF
    # dict key) both emit this same kind for the same enum's underlying-type
    # size change — self-mapped so its discriminator collapses across
    # producers the same way the three sibling enum-member kinds above
    # already do; see finding_dedup.deduplicate_cross_detector's own
    # docstring for the qualified-name bridge this also depends on.
    "enum_underlying_size_changed": "enum_underlying_size_changed",
    # Schema v69: a retype between ABI-identical types (compare/abi_equivalent_
    # retype.py and diff_atomic.py) is one slot observed the same way by every
    # producer; its old/new spellings are the backend's own spelling of one type,
    # so its discriminator must not vary with them. Self-mapped, as the enum
    # kinds are.
    "abi_equivalent_type_changed": "abi_equivalent_type_changed",
    # diff_symbols._detect_newly_deleted_functions emits FUNC_DELETED
    # (castxml is_deleted attribute) or FUNC_DELETED_DWARF (DWARF
    # DW_AT_deleted) for the same symbol/callable->deleted transition --
    # which kind you get depends only on which evidence source (header
    # analysis vs. binary DWARF) observed the deletion, the same
    # evidence-tier-producer variance every other pair in this map
    # normalizes (Codex review).
    "func_deleted": "func_deletion",
    "func_deleted_dwarf": "func_deletion",
}

# Kinds whose old_value/new_value (and, for a handful, description) genuinely
# hold a C/C++ type spelling -- verified individually against each kind's
# emission call site and model/change_catalog/assembled.py description_template, not
# guessed. Used only by _change_discriminator's canonicalize_values=True path
# (report_canonical_finding_id) to decide which findings need
# canonicalize_type_name normalization at all (Codex review, PR #753, two
# follow-up rounds): applying it to every kind corrupted non-type old/new
# text (e.g. PUBLIC_MACRO_VALUE_CHANGED's raw macro replacement text, where
# whitespace is semantically meaningful), and unconditionally dropping
# description to avoid that same corruption instead collided distinct
# findings that share kind+symbol+old+new and differ only by a per-item
# identity description embeds via change_catalog.assembled's `{detail}` (e.g.
# TYPE_FIELD_TYPE_CHANGED's own sibling struct_field_type_changed embeds a
# field name this way). Deliberately not exhaustive -- every kind here was
# checked against a real call site; a kind absent from this set still has
# the pre-fix backend-spelling-mismatch gap on canonical_finding_id, and
# adding it needs the same per-kind verification, not a blanket sweep.
#
# Previously a known gap for ATOMIC_QUALIFIER_CHANGED, CHAR8T_MIGRATION, and
# BIT_INT_WIDTH_CHANGED (Codex review, PR #753, fifth round): all three
# detectors (`diff_atomic.py`, `diff_char8t.py`, `diff_bit_int.py`) pass
# `iter_type_slot_changes`'s raw type-slot spellings straight through as
# `old=`/`new=`, with no `{detail}`-carried per-item identity at stake --
# `detail` there is a fixed direction string ("qualifier added"/"char-family
# → char8_t"/"_BitInt width changed N1 → N2"), never a per-item identity like
# a field/parameter name, so canonicalizing old/new can't collide two
# distinct findings the way it would for e.g. TYPE_FIELD_TYPE_CHANGED.
# Verified individually against each kind's emission call site, same bar
# this set's own docstring above already commits to -- not the rejected
# *derived* classification (every kind whose emitter calls
# `iter_type_slot_changes`), added here as three more explicit, individually
# checked entries instead.
#
# A *derived* classification (e.g. every kind whose emitter calls
# `iter_type_slot_changes`/passes a `RecordType`/`Param.type`-sourced value
# as `old=`/`new=`) was considered instead of one more reactive addition --
# rejected: it needs the same per-kind call-site verification this file's
# own docstring above already commits to (a value's *source* doesn't by
# itself prove no `{detail}`-carried per-item identity is also at stake, the
# exact trap the earlier description-dropping attempt fell into), just
# automated instead of explicit, which trades a visible, auditable list for
# an implicit one that's harder to review. Matches this codebase's own
# "known gaps over risky reactive patches" convention (AGENTS.md) rather
# than a same-session revision of this exact function with no per-kind
# verification.
_TYPE_BEARING_DISCRIMINATOR_KINDS = frozenset(
    {
        "func_return_changed",
        "public_api_internal_type_renamed",
        "var_type_changed",
        "type_field_type_changed",
        "struct_field_type_changed",
        "union_field_type_changed",
        "typedef_base_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        # diff_atomic.py/diff_char8t.py/diff_bit_int.py: old_value/new_value
        # are iter_type_slot_changes()'s raw type-slot spellings, and
        # `detail` (folded into `description` via change_catalog.assembled's
        # `{detail}` placeholder) is always a fixed direction string, never
        # a per-item identity -- see this set's own comment above.
        "atomic_qualifier_changed",
        "char8t_migration",
        "bit_int_width_changed",
        # diff_symbols._check_params_change: old/new are
        # _format_params()'s comma-joined `Param.type` spellings, one
        # function-wide finding (no per-parameter `detail`, so nothing is
        # lost by canonicalizing the whole joined string as a *set of
        # parameters* -- see _canonicalize_params_list below, which splits
        # and canonicalizes each parameter individually rather than the
        # joined string as one type. A whole-string canonicalize_type_name
        # call only reorders const/strips a struct-prefix on the FIRST
        # parameter (both anchored to string start) -- a real change on
        # parameter N plus an unrelated const-spelling difference on some
        # OTHER parameter M would then still produce two different
        # discriminators for the identical function-wide event across
        # producers (Codex review, fresh evidence).
        "func_params_changed",
    }
)

#: Kinds whose ``change_catalog.assembled`` ``description_template`` embeds the raw
#: ``{old}``/``{new}`` type spelling verbatim, NOT at the start of the
#: sentence (Codex review, fresh evidence): ``struct_field_type_changed``
#: ("Field type changed: {name}::{detail} {old} → {new}"),
#: ``template_param_type_changed``/``template_return_type_changed``
#: ("... ({old} → {new})"). Canonicalizing the whole rendered sentence
#: with :func:`canonicalize_type_name` doesn't fix these -- its
#: struct-prefix-strip/const-reorder passes are anchored to string start,
#: so they never reach text embedded mid-sentence, and the embedded
#: spelling stays raw even after old_str/new_str themselves are correctly
#: canonicalized. ``atomic_qualifier_changed``/``char8t_migration``/
#: ``bit_int_width_changed`` have the identical shape --
#: ``"_Atomic {detail} on {name}: {old} → {new}. ..."`` and siblings
#: (``model/change_catalog/assembled.py``) all embed ``{old} → {new}`` after a leading
#: ``{detail}`` clause, never at sentence start. The remaining allowlisted
#: kinds' templates never embed {old}/{new} at all (`"Return type changed:
#: {name}"` and siblings), so this set is still narrower than
#: _TYPE_BEARING_DISCRIMINATOR_KINDS.
_DESCRIPTION_EMBEDS_VALUES_KINDS = frozenset(
    {
        "struct_field_type_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        "atomic_qualifier_changed",
        "char8t_migration",
        "bit_int_width_changed",
    }
)
