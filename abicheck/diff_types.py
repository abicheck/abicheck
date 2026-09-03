# Copyright 2026 Nikolay Petrov
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

"Type-level ABI diff detectors (structs, enums, unions, typedefs, fields)."

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_cxx_rules import itanium_qualified_name
from .diff_helpers import (
    build_type_map as _build_type_map,
    fact_known_qualified,
    is_sentinel_enum_member,
    lookup_matched_type as _lookup_matched_type,
    make_change,
    type_map_key,
    typedef_diff_maps as _typedef_diff_maps,
)
from .diff_symbols import (
    _PUBLIC_VIS,
    _public_functions,
    _should_filter_transitive_runtime_symbols,
)
from .diff_types_abicc_parity import (
    # Re-exported (`as`-aliased, not just imported) so both the registration
    # side effect fires AND `from .diff_types import _diff_const_overloads`
    # elsewhere (e.g. checker.py) resolves under mypy strict's
    # implicit_reexport=false — mirrors diff_platform.py's own
    # diff_platform_templates import block.
    _diff_const_overloads as _diff_const_overloads,
    _diff_reserved_fields as _diff_reserved_fields,
    _diff_type_kind_changes as _diff_type_kind_changes,
    _diff_var_values as _diff_var_values,
)
from .diff_types_field_facts import (
    _diff_enum_deprecated as _diff_enum_deprecated,
    _diff_enum_renames as _diff_enum_renames,
    _diff_field_default_initializer as _diff_field_default_initializer,
    _diff_field_deprecated as _diff_field_deprecated,
    _diff_field_qualifiers as _diff_field_qualifiers,
    _diff_field_renames as _diff_field_renames,
    _diff_type_deprecated as _diff_type_deprecated,
)
from .diff_types_surface import (
    _RESERVED_FIELD_RE as _RESERVED_FIELD_RE,
    _directly_referenced as _directly_referenced,
    _is_abi_surface_type as _is_abi_surface_type,
)
from .diff_types_vtable import (
    # Re-exported (`as`-aliased) so `from .diff_types import
    # _vtable_transition_is_evidenced` and similar call sites keep resolving
    # — mirrors the diff_types_abicc_parity re-export block above.
    _diff_type_vtable as _diff_type_vtable,
    _layout_evidence_is_unverifiable as _layout_evidence_is_unverifiable,
    _owned_virtual_signatures as _owned_virtual_signatures,
    _owned_virtual_signatures_for_record as _owned_virtual_signatures_for_record,
    _vtable_transition_is_evidenced as _vtable_transition_is_evidenced,
    _vtable_transition_rests_on_unresolved_evidence as _vtable_transition_rests_on_unresolved_evidence,
)
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from .fact_provenance import (
    both_known_backed_fact,
    enum_fact_key,
    type_fact_key,
)
from .model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    TypeField,
    canonicalize_type_name,
    compare_facts,
    cv_qualifiers_only_differ,
    func_signature_cv_only_differ,
    is_non_abi_surface_type as _is_non_abi_surface_type,
    resolved_fact_value,
    stdlib_namespaces_excluded as _exclude_stdlib_namespaces,
)
from .model.identity import EntityId


def _field_type_genuinely_changed(
    old_type: str, new_type: str, *, cv_facts_reliable: bool
) -> bool:
    """True when a struct/union field's type spelling differs in a way that
    should be reported.

    Layered on top of the existing pointer/reference cv neutralization
    (``cv_qualifiers_only_differ``): when *either* snapshot in the pair
    predates the CastXML CV-fact fix (``cv_facts_reliable=False``, see
    ``AbiSnapshot.header_cv_facts_reliable``), a BY-VALUE cv-only difference
    is *also* neutralized here — reusing ``func_signature_cv_only_differ``'s
    strip-and-compare logic, even though its own docstring warns against
    that for fields. That warning is about unconditionally neutralizing a
    field's own cv change, which is a real, intentionally-visible,
    breaking source change (``case30_field_qualifiers`` ground truth). This
    is different: it only applies when the calling detector has already
    established that this specific snapshot pair's cv facts cannot be
    trusted, because a persisted pre-fix CastXML snapshot's field type
    spelling may have silently dropped a real ``const``/``volatile`` token
    — comparing it against a fresh dump of genuinely UNCHANGED headers
    would otherwise misreport a false type change purely from the tool
    upgrade. This intentionally also means a REAL by-value cv change goes
    undetected in that mixed legacy/fresh pairing — the two are
    indistinguishable, and (like ``_both_castxml_backed`` elsewhere) losing
    that one axis of detection is the safer trade-off (Codex review, PR
    #582).
    """
    if canonicalize_type_name(old_type) == canonicalize_type_name(new_type):
        return False
    if cv_qualifiers_only_differ(old_type, new_type):
        return False
    if not cv_facts_reliable and func_signature_cv_only_differ(old_type, new_type):
        return False
    return True


def _exported_elf_symbol_names(
    snap: AbiSnapshot, *, symbol_types: Collection[str]
) -> set[str]:
    """Return dynamic-export names from ELF metadata when available.

    DWARF-primary snapshots can contain public-looking subprogram DIEs that are
    not dynamic exports.  For stripped-side retention checks, the real question
    is whether the dynamic ABI surface stayed intact, so prefer ``snap.elf`` over
    the DWARF-derived Function/Variable lists. Transitive runtime/compiler
    exports are excluded so they can't inflate retention.
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    return exported_symbol_names(
        getattr(snap, "elf", None),
        symbol_types,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
    )


def _has_type_evidence(snap: AbiSnapshot) -> bool:
    """True if *snap* carries any type-level surface (records/enums/typedefs/DWARF).

    A stripped, symbols-only snapshot has none of these.
    """
    if snap.types or snap.enums or snap.typedefs:
        return True
    dwarf = getattr(snap, "dwarf", None)
    # has_dwarf alone is not enough: a stripped binary can carry an empty
    # .debug_* section (has_dwarf=True, zero structs/enums). Require real content.
    return bool(dwarf is not None and (dwarf.structs or dwarf.enums))


def _removals_are_unconfirmed(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """True when type-level *removals* must be suppressed because the new side
    has no type evidence to confirm them (RD2-5).

    When the old binary carries DWARF/header type info but the new one is
    *stripped, symbols-only* (``elf_only_mode``), every old type/typedef/enum
    appears "missing" — but that is absence of *evidence*, not evidence of
    *removal*. Reporting hundreds of phantom ``type_removed``/``typedef_removed``
    for types that obviously still exist (``_xmlNode``, ``_IO_FILE`` …) is a
    false-positive avalanche on an otherwise compatible bump. The ``dwarf``
    detector already emits ``DWARF_INFO_MISSING`` for this asymmetry, so the
    coverage gap is still disclosed; here we simply decline to manufacture
    removal findings from it.

    Detecting a genuinely *stripped* new side (vs. a build that really removed
    types) requires care: the dumper marks small DWARF libraries ``elf_only_mode``
    too, so that flag alone is not enough. The distinguishing fact is that a
    stripped binary is otherwise *intact* — it still exports essentially all of
    the old side's symbols; only its debug info is gone. A build that genuinely
    removed a class (e.g. examples/case107) also drops that class's exported
    methods, so symbol retention is low and the removal is still reported.
    """
    new_stripped_of_types = (
        getattr(new, "elf_only_mode", False)
        and bool(new.functions or new.variables)
        and not _has_type_evidence(new)
        and _has_type_evidence(old)
    )
    if not new_stripped_of_types:
        return False
    # Corroborate with exported-symbol retention: a stripped-but-intact
    # binary keeps (almost) all of the old side's exports; if most are gone,
    # removals are real. Only *exported* symbols (PUBLIC/ELF_ONLY) — a
    # DWARF-primary old snapshot also records internal/static subprograms the
    # stripped side's exports never carry. Fall back to variables for
    # data-only DSOs (CodeRabbit review on PR #275).
    old_funcs = _exported_elf_symbol_names(old, symbol_types=FUNCTION_SYMBOL_TYPES)
    new_funcs = _exported_elf_symbol_names(new, symbol_types=FUNCTION_SYMBOL_TYPES)
    if not old_funcs or not new_funcs:
        old_funcs = {
            k for k, v in old.function_map.items() if v.visibility in _PUBLIC_VIS
        }
        new_funcs = {
            k for k, v in new.function_map.items() if v.visibility in _PUBLIC_VIS
        }
    if old_funcs:
        return len(old_funcs & new_funcs) / len(old_funcs) >= 0.9
    old_vars = _exported_elf_symbol_names(old, symbol_types=VARIABLE_SYMBOL_TYPES)
    new_vars = _exported_elf_symbol_names(new, symbol_types=VARIABLE_SYMBOL_TYPES)
    if not old_vars or not new_vars:
        old_vars = {
            k for k, v in old.variable_map.items() if v.visibility in _PUBLIC_VIS
        }
        new_vars = {
            k for k, v in new.variable_map.items() if v.visibility in _PUBLIC_VIS
        }
    if not old_vars:
        return True  # no exported surface to corroborate; absence of types is just stripping
    return len(old_vars & new_vars) / len(old_vars) >= 0.9


@registry.detector("types")
def _diff_types(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    # Include ALL types (including unions) for size/alignment/base/vtable checks.
    # TYPE_FIELD_* for unions is skipped below — handled by _diff_unions() instead.
    excl = _exclude_stdlib_namespaces(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_map = _build_type_map(
        t
        for t in old.types
        if _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_map = _build_type_map(
        t
        for t in new.types
        if _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    # RD2-5: don't manufacture phantom TYPE_REMOVED when the new side is stripped.
    suppress_removed = _removals_are_unconfirmed(old, new)

    old_funcs = old.function_map
    new_funcs = new.function_map
    # Unfiltered (unlike old_map/new_map above) so a private/internal base
    # class still resolves when _transitive_bases walks the hierarchy for
    # vtable_slot_is_override_reuse() -- mirrors diff_symbols._diff_functions'
    # own old_types/new_types for the identical virtual_method_addition() walk.
    old_types = _build_type_map(t for t in old.types)
    new_types = _build_type_map(t for t in new.types)
    cv_facts_reliable = old.header_cv_facts_reliable and new.header_cv_facts_reliable
    vtable_facts_reliable = (
        old.clang_vtable_facts_reliable and new.clang_vtable_facts_reliable
    )

    # Tracked by object identity, not key membership: a legacy-schema-vs-fresh
    # pair can match through diff_helpers.TypeMap's bare-name alias even though the two
    # sides' canonical keys differ (qualified vs. bare) — a "key not in
    # old_map" check on new_map's own canonical keys would then falsely see
    # no match and manufacture a phantom TYPE_ADDED (Codex review, PR #608).
    matched_new_ids: set[int] = set()

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        # Emitted Change objects (symbol=/description=) and fact-provenance
        # lookups use the bare declaration name, not the qualified matching
        # key (diff_helpers.type_map_key) that lookup_matched_type/build_type_map
        # use internally. Keeping emitted symbols bare preserves the identity
        # every other consumer already keys on: dumper_hybrid's per-fact
        # provenance dict (type_fact_key/field_fact_key, Codex review PR #608)
        # and diff_filtering._dedup_cross_kind's DWARF<->AST redundancy
        # correlation (FIX-F), which bridges a namespaced type's bare-vs-
        # qualified spelling via the separate ``qualified_name`` field rather
        # than requiring ``symbol`` itself to be qualified.
        name = t_old.name
        if t_new is None:
            if suppress_removed:
                continue
            changes.append(
                make_change(
                    ChangeKind.TYPE_REMOVED,
                    symbol=name,
                    description=f"Type removed: {name}",
                    entity_id=t_old.entity_id,
                )
            )
            continue
        matched_new_ids.add(id(t_new))
        changes.extend(
            _diff_type_pair(
                name,
                t_old,
                t_new,
                old_funcs,
                new_funcs,
                old_types,
                new_types,
                # Per-type key, not a whole-snapshot gate: correctly supports
                # a --ast-frontend hybrid snapshot (G28 Phase 3), where
                # is_abstract's producer is recorded per declaration, not
                # uniformly across the whole snapshot. both_known_backed_fact
                # (not the narrower both_castxml_backed_fact): G31 Phase C's
                # backend audit wired real is_abstract extraction into the
                # direct-clang backend too (dumper_clang._clang_record_is_
                # abstract, from clang's own definitionData.isAbstract), so
                # this is now a cross-producer, directly-comparable bool.
                castxml_backed=both_known_backed_fact(
                    old, new, type_fact_key(name, "is_abstract")
                ),
                cv_facts_reliable=cv_facts_reliable,
                vtable_facts_reliable=vtable_facts_reliable,
            )
        )

    for t_new in new_map.values():
        if id(t_new) not in matched_new_ids:
            changes.append(
                make_change(
                    ChangeKind.TYPE_ADDED,
                    symbol=t_new.name,
                    name=t_new.name,
                    entity_id=t_new.entity_id,
                )
            )

    return changes


def _overload_group_key(f: Function) -> str:
    """A scope-qualified identity for grouping overloads of the same name.

    Two declarations are overloads of one another iff they share this key but
    have distinct mangled names. The key is the fully scope-qualified name parsed
    structurally from the *mangled* symbol (no external demangler — see
    ``itanium_qualified_name``), so it is stable across dumpers and platforms: a
    castxml/header snapshot records ``Function.name`` without namespace/class
    scope, so grouping on ``name`` alone would both collapse unrelated
    declarations like ``A::size`` and ``B::size`` into one group *and* hide a
    genuine ``A::size`` overload behind an unrelated ``B::size``. When the symbol
    is not a recognised C++ mangled name, fall back to the *full mangled name*
    rather than the bare leaf — distinct symbols then land in distinct groups, so
    an unparseable form can never manufacture a false overload (at worst a
    genuine overload goes unreported).
    """
    return itanium_qualified_name(f.mangled) or f.mangled


@registry.detector("overload_additions")
def _diff_overload_additions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect an overload added to a previously *unique* public name.

    KDE's C++ binary-compatibility policy treats adding an overload to a
    non-overloaded function as a change to avoid: it is binary-compatible but
    not source-compatible (`&Foo::bar` becomes ambiguous, and overload
    resolution at existing call sites may shift). We only fire when a
    scope-qualified name had exactly one public declaration in the old snapshot,
    still has that same declaration (same mangled name) in the new snapshot, and
    gains at least one additional overload — so a plain signature change
    (remove+add of the same name) does not masquerade as an overload addition,
    and an unrelated same-leaf declaration in a different scope does not either.

    Grouping is on the scope-qualified key, not the bare display name, so the
    uniqueness test is per scope: ``A::size`` stays distinct from ``B::size``
    even when the dumper recorded both as the leaf ``size``.
    """
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    old_by_key: dict[str, list[Function]] = {}
    for f in old_map.values():
        old_by_key.setdefault(_overload_group_key(f), []).append(f)
    new_by_key: dict[str, list[Function]] = {}
    for f in new_map.values():
        new_by_key.setdefault(_overload_group_key(f), []).append(f)

    changes: list[Change] = []
    for key, olds in old_by_key.items():
        if key.endswith("{ctor}") or key.endswith("{dtor}"):
            # Constructors/destructors can't be named or have their address
            # taken (`&C::C` is invalid), so the address-of-ambiguity rationale
            # doesn't apply; adding a constructor overload stays a compatible
            # FUNC_ADDED. (Destructors can't be overloaded at all.)
            continue
        if len(olds) != 1:
            continue  # already overloaded → KDE allows adding further overloads
        original = olds[0]
        news = new_by_key.get(key, [])
        if len(news) < 2:
            continue  # no new overload under this qualified name
        new_mangleds = {f.mangled for f in news}
        if original.mangled not in new_mangleds:
            continue  # original declaration gone → a replacement/rename, not an addition
        if not (new_mangleds - {original.mangled}):
            continue  # nothing genuinely new
        changes.append(
            make_change(
                ChangeKind.OVERLOAD_ADDED,
                symbol=original.mangled,
                name=key,
                old_value="1 overload",
                new_value=f"{len(news)} overloads",
                entity_id=original.entity_id,
            )
        )
    return changes


def _diff_type_pair(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: dict[str, Function],
    new_funcs: dict[str, Function],
    old_types: Mapping[str, RecordType],
    new_types: Mapping[str, RecordType],
    *,
    castxml_backed: bool = False,
    cv_facts_reliable: bool = True,
    vtable_facts_reliable: bool = True,
) -> list[Change]:
    changes: list[Change] = []

    # TYPE_BECAME_OPAQUE: was complete, now forward-decl only
    if not t_old.is_opaque and t_new.is_opaque:
        changes.append(
            make_change(
                ChangeKind.TYPE_BECAME_OPAQUE,
                symbol=name,
                name=name,
                old_value="complete",
                new_value="opaque",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )
        return changes  # no further checks meaningful for opaque type

    _append_type_size_and_alignment_changes(changes, name, t_old, t_new)
    if not t_old.is_union:
        changes.extend(
            _diff_type_fields(name, t_old, t_new, cv_facts_reliable=cv_facts_reliable)
        )
    changes.extend(_diff_type_bases(name, t_old, t_new))
    changes.extend(
        _diff_type_vtable(
            name,
            t_old,
            t_new,
            old_funcs,
            new_funcs,
            old_types,
            new_types,
            vtable_facts_reliable=vtable_facts_reliable,
        )
    )
    _append_type_finality_changes(changes, name, t_old, t_new)
    if castxml_backed:
        _append_type_abstract_changes(changes, name, t_old, t_new)
    return changes


def _append_type_abstract_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
) -> None:
    """Detect `abstract` (>=1 pure virtual) transitions.

    Tri-state, same rationale as ``_append_type_finality_changes``: only fire
    when BOTH sides record it; ``None`` means the dumper couldn't determine
    it (DWARF/symbols-only mode, older snapshots, or an opaque record with no
    member list to have judged it from) rather than "not abstract". Both the
    castxml and direct-clang header backends now populate ``is_abstract``
    (G31 Phase C backend audit; previously clang-only-``None``) — the caller
    (``_diff_type_pair``) still gates this call on ``castxml_backed`` (now
    computed via ``both_known_backed_fact``, not the narrower
    ``both_castxml_backed_fact``) so a legacy or unresolved-producer side's
    unconditional ``None`` is never misread as "no longer abstract" (Codex
    review, PR #582).
    """
    if t_old.is_abstract is None or t_new.is_abstract is None:
        return
    if t_old.is_abstract == t_new.is_abstract:
        return
    if t_new.is_abstract:
        changes.append(
            make_change(
                ChangeKind.TYPE_BECAME_ABSTRACT,
                symbol=name,
                name=name,
                old_value="instantiable",
                new_value="abstract",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )
    else:
        changes.append(
            make_change(
                ChangeKind.TYPE_LOST_ABSTRACT,
                symbol=name,
                name=name,
                old_value="abstract",
                new_value="instantiable",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )


def _append_type_finality_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
) -> None:
    """Detect `final` class-key transitions.

    Tri-state: only fire when BOTH sides record finality (header/castxml mode).
    ``None`` means the dumper couldn't determine it — DWARF/symbols-only mode
    carries no `final` information, and older snapshots predate the field —
    so skipping avoids false findings from a tier downgrade or schema
    evolution rather than a real source change.
    """
    if t_old.is_final is None or t_new.is_final is None:
        return
    if t_old.is_final == t_new.is_final:
        return
    if t_new.is_final:
        changes.append(
            make_change(
                ChangeKind.TYPE_BECAME_FINAL,
                symbol=name,
                name=name,
                old_value="non-final",
                new_value="final",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )
    else:
        changes.append(
            make_change(
                ChangeKind.TYPE_LOST_FINAL,
                symbol=name,
                name=name,
                old_value="final",
                new_value="non-final",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )


def _append_type_size_and_alignment_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
) -> None:
    # This caller knows the matched RecordType pair directly, so it can
    # stamp real identity even when record_canonical_names' bare-name
    # bridge can't (an unrelated `a::Widget`/`b::Widget` collision
    # elsewhere in the snapshot -- Codex review).
    qualified = t_new.qualified_name or t_old.qualified_name
    if (
        t_old.size_bits is not None
        and t_new.size_bits is not None
        and t_old.size_bits != t_new.size_bits
    ):
        changes.append(
            make_change(
                ChangeKind.TYPE_SIZE_CHANGED,
                symbol=name,
                name=name,
                old=str(t_old.size_bits),
                new=str(t_new.size_bits),
                qualified_name=qualified,
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )

    if (
        t_old.alignment_bits is not None
        and t_new.alignment_bits is not None
        and t_old.alignment_bits != t_new.alignment_bits
    ):
        changes.append(
            make_change(
                ChangeKind.TYPE_ALIGNMENT_CHANGED,
                symbol=name,
                name=name,
                old=str(t_old.alignment_bits),
                qualified_name=qualified,
                new=str(t_new.alignment_bits),
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )


def _try_match_reserved_field(
    fname: str,
    f_old: TypeField,
    name: str,
    added_by_offset: dict[int, list[TypeField]],
    added_by_type: dict[str, list[TypeField]],
    reserved_matched_added: set[str],
    renamed_type_changed_added: set[str],
    *,
    entity_id: EntityId | None = None,
) -> Change | None:
    """Check if a removed field is a reserved field put into use.

    Returns a USED_RESERVED_FIELD Change if matched, or None.
    """
    if not _RESERVED_FIELD_RE.match(fname):
        return None

    candidate: TypeField | None = None
    # Primary: match by offset + type (when available). Excludes candidates
    # already consumed by an earlier reserved-field or rename match at this
    # offset (distinct added fields can share an offset_bits, e.g.
    # overlapping anonymous-union members) — else two removed fields could
    # claim the same added field, hiding a genuine drop (caught in review).
    if f_old.offset_bits is not None:
        candidate = next(
            (
                c
                for c in added_by_offset.get(f_old.offset_bits, [])
                if (
                    c.name not in reserved_matched_added
                    and c.name not in renamed_type_changed_added
                    and f_old.type == c.type
                )
            ),
            None,
        )
    # Fallback: match by type when offsets unavailable (DWARF-only)
    if candidate is None and f_old.offset_bits is None:
        candidates = added_by_type.get(f_old.type, [])
        for c in candidates:
            if (
                c.name not in reserved_matched_added
                and c.name not in renamed_type_changed_added
            ):
                candidate = c
                break
    if candidate is not None and not _RESERVED_FIELD_RE.match(candidate.name):
        # Reserved field -> real field at same offset/type -> COMPATIBLE
        reserved_matched_added.add(candidate.name)
        return make_change(
            ChangeKind.USED_RESERVED_FIELD,
            symbol=name,
            name=name,
            old=fname,
            new=candidate.name,
            entity_id=entity_id,
        )
    return None


def _index_added_fields(
    old_fields: dict[str, TypeField],
    new_fields: dict[str, TypeField],
) -> tuple[dict[int, list[TypeField]], dict[str, list[TypeField]]]:
    """Index fields present only in new_fields by offset and by type for reserved-field matching."""
    # Build index of added fields by offset for reserved-field matching. A
    # list per offset, not a single value: distinct added fields can share
    # an offset_bits (e.g. overlapping anonymous-union members), and a plain
    # ``dict[int, TypeField]`` would silently keep only the last one (caught
    # in review).
    added_by_offset: dict[int, list[TypeField]] = {}
    # Also build an ordered list of added non-reserved fields for fallback
    # when offset_bits is unavailable (e.g. DWARF-only mode). Iterate
    # new_fields (insertion == declaration order) rather than a set of names, so
    # the per-type fallback lists are deterministic across runs.
    added_by_type: dict[str, list[TypeField]] = {}
    for fname, f in new_fields.items():
        if fname in old_fields:
            continue
        if f.offset_bits is not None:
            added_by_offset.setdefault(f.offset_bits, []).append(f)
        if not _RESERVED_FIELD_RE.match(fname):
            added_by_type.setdefault(f.type, []).append(f)
    return added_by_offset, added_by_type


def _trailing_fam_name(fields: list[TypeField]) -> str | None:
    """Return the name of the trailing flexible array member, or None if absent."""
    if fields and _is_flexible_array_member(fields[-1]):
        return fields[-1].name
    return None


def _diff_removed_field(
    name: str,
    fname: str,
    f_old: TypeField,
    added_by_offset: dict[int, list[TypeField]],
    added_by_type: dict[str, list[TypeField]],
    reserved_matched_added: set[str],
    renamed_type_changed_added: set[str],
    *,
    qualified_name: str | None = None,
    entity_id: EntityId | None = None,
) -> Change | None:
    """Classify a field missing from the new type as reserved-use, rename(+retype), or removal.

    Returns FIELD_RENAMED directly for a pure rename (same offset, identical
    type, different name) instead of TYPE_FIELD_REMOVED, which would overstate
    the severity. Doesn't rely on ``_diff_field_renames`` also matching the
    pair — that detector keys on the raw ``(offset_bits, type)`` tuple, which
    a canonical-equal-but-differently-spelled type (``struct Foo`` vs ``Foo``)
    can miss (caught in review); emitting the identical shape here is safe
    either way, since the post-processing dedup pass collapses a duplicate.
    """
    # Check if this is a reserved field put into use
    matched = _try_match_reserved_field(
        fname,
        f_old,
        name,
        added_by_offset,
        added_by_type,
        reserved_matched_added,
        renamed_type_changed_added,
        entity_id=entity_id,
    )
    if matched is not None:
        return matched
    if f_old.offset_bits is not None:
        # Excludes candidates already consumed by an earlier reserved-field
        # or rename match at this offset — distinct added fields can share
        # an offset_bits (overlapping anonymous-union members), and without
        # this two removed fields could both claim the same target, hiding
        # that one was genuinely dropped (caught in review).
        candidates = [
            c
            for c in added_by_offset.get(f_old.offset_bits, [])
            if (
                c.name not in reserved_matched_added
                and c.name not in renamed_type_changed_added
                and not _RESERVED_FIELD_RE.match(fname)
                and not _RESERVED_FIELD_RE.match(c.name)
            )
        ]
        # Prefer an exact pure-rename match over an arbitrary first candidate:
        # with several fields sharing an offset (anonymous-union/overlap
        # layouts), the first unconsumed one might be an unrelated retype
        # while a later one is the true rename target — picking the retype
        # first would both misreport TYPE_FIELD_TYPE_CHANGED here and leave
        # `_diff_field_renames` to separately claim FIELD_RENAMED for the
        # same old field (caught in review).
        f_new = next(
            (
                c
                for c in candidates
                if (
                    canonicalize_type_name(f_old.type) == canonicalize_type_name(c.type)
                    # A bit-field's width isn't captured by the type spelling
                    # — require it to also match, or a real
                    # FIELD_BITFIELD_CHANGED break gets masked as a rename
                    # (caught in review).
                    and f_old.is_bitfield == c.is_bitfield
                    and f_old.bitfield_bits == c.bitfield_bits
                )
            ),
            None,
        )
        if f_new is not None:
            renamed_type_changed_added.add(f_new.name)
            return make_change(
                ChangeKind.FIELD_RENAMED,
                symbol=name,
                name=name,
                old=fname,
                new=f_new.name,
                entity_id=entity_id,
            )
        f_new = candidates[0] if candidates else None
        if f_new is not None and not cv_qualifiers_only_differ(f_old.type, f_new.type):
            renamed_type_changed_added.add(f_new.name)
            return make_change(
                ChangeKind.TYPE_FIELD_TYPE_CHANGED,
                symbol=name,
                name=name,
                detail=f"{fname} -> {f_new.name}",
                old=f_old.type,
                new=f_new.type,
                qualified_name=qualified_name,
                field_name=fname,
                entity_id=entity_id,
            )
    return make_change(
        ChangeKind.TYPE_FIELD_REMOVED,
        symbol=name,
        name=name,
        detail=fname,
        qualified_name=qualified_name,
        field_name=fname,
        entity_id=entity_id,
    )


def _added_field_changes(
    name: str,
    t_new: RecordType,
    old_fields: dict[str, TypeField],
    new_fields: dict[str, TypeField],
    reserved_matched_added: set[str],
    renamed_type_changed_added: set[str],
    new_trailing_fam: str | None,
    *,
    entity_id: EntityId | None = None,
) -> list[Change]:
    """Emit added-field changes for new fields not consumed by reserved/rename matching."""
    changes: list[Change] = []
    for fname in new_fields:
        if (
            fname not in old_fields
            and fname not in reserved_matched_added
            and fname not in renamed_type_changed_added
        ):
            # Skip trailing FAM additions — handled by _diff_flexible_array_member
            if fname == new_trailing_fam:
                continue
            changes.append(
                make_change(
                    _new_field_change_kind(t_new),
                    symbol=name,
                    name=name,
                    detail=fname,
                    entity_id=entity_id,
                )
            )
    return changes


def _diff_type_fields(
    name: str, t_old: RecordType, t_new: RecordType, *, cv_facts_reliable: bool = True
) -> list[Change]:
    """Diff the field lists of two record types, emitting field-level changes."""
    changes: list[Change] = []
    old_fields = {f.name: f for f in t_old.fields}
    new_fields = {f.name: f for f in t_new.fields}
    # Same qualified-identity stamp as _append_type_size_and_alignment_changes
    # above, threaded down to the field-level emitters (Codex review).
    qualified = t_new.qualified_name or t_old.qualified_name
    # The containing record's own identity, not any one field's -- TypeField
    # carries no entity_id of its own (mirrors diff_types_field_facts.py's
    # identical threading for its own field-level detectors).
    entity_id = t_old.entity_id or t_new.entity_id

    added_by_offset, added_by_type = _index_added_fields(old_fields, new_fields)
    # Track which added fields were matched as reserved-field activations
    # so we skip emitting TYPE_FIELD_ADDED for them.
    reserved_matched_added: set[str] = set()
    renamed_type_changed_added: set[str] = set()

    # Identify trailing FAM fields so we can skip them from generic checks
    # and let _diff_flexible_array_member handle them exclusively.
    old_trailing_fam = _trailing_fam_name(t_old.fields)
    new_trailing_fam = _trailing_fam_name(t_new.fields)

    for fname, f_old in old_fields.items():
        f_new = new_fields.get(fname)
        if f_new is None:
            # Skip trailing FAM removals — handled by _diff_flexible_array_member
            if fname == old_trailing_fam:
                continue
            removed_change = _diff_removed_field(
                name,
                fname,
                f_old,
                added_by_offset,
                added_by_type,
                reserved_matched_added,
                renamed_type_changed_added,
                qualified_name=qualified,
                entity_id=entity_id,
            )
            if removed_change is not None:
                changes.append(removed_change)
            continue
        # Skip trailing FAM type changes — handled by _diff_flexible_array_member
        if fname == old_trailing_fam and fname == new_trailing_fam:
            continue
        changes.extend(
            _diff_type_field_pair(
                name,
                fname,
                f_old,
                f_new,
                cv_facts_reliable=cv_facts_reliable,
                qualified_name=qualified,
                entity_id=entity_id,
            )
        )

    changes.extend(
        _added_field_changes(
            name,
            t_new,
            old_fields,
            new_fields,
            reserved_matched_added,
            renamed_type_changed_added,
            new_trailing_fam,
            entity_id=entity_id,
        )
    )

    # FLEXIBLE_ARRAY_MEMBER_CHANGED: detect changes to trailing flexible array members.
    # A FAM is the last field with type matching "T []" or "T [0]" — zero static size.
    changes.extend(_diff_flexible_array_member(name, t_old, t_new))

    return changes


def _diff_type_field_pair(
    name: str,
    fname: str,
    f_old: TypeField,
    f_new: TypeField,
    *,
    cv_facts_reliable: bool = True,
    qualified_name: str | None = None,
    entity_id: EntityId | None = None,
) -> list[Change]:
    changes: list[Change] = []
    # Use canonical form for type comparison to avoid false positives from
    # "struct Foo" vs "Foo" or "const int" vs "int const". A pointee/by-value
    # cv-qualifier change (``char *`` -> ``const char *``) leaves size/offset
    # unchanged -- source churn, not a layout break (ISSUE-30/35/65); see
    # _field_type_genuinely_changed for cv_facts_reliable handling.
    if _field_type_genuinely_changed(
        f_old.type, f_new.type, cv_facts_reliable=cv_facts_reliable
    ):
        changes.append(
            make_change(
                ChangeKind.TYPE_FIELD_TYPE_CHANGED,
                symbol=name,
                name=name,
                detail=fname,
                old_value=f_old.type,
                new_value=f_new.type,
                qualified_name=qualified_name,
                field_name=fname,
                entity_id=entity_id,
            )
        )
    if (
        f_old.offset_bits is not None
        and f_new.offset_bits is not None
        and f_old.offset_bits != f_new.offset_bits
    ):
        changes.append(
            make_change(
                ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
                symbol=name,
                name=name,
                detail=fname,
                old=str(f_old.offset_bits),
                new=str(f_new.offset_bits),
                qualified_name=qualified_name,
                field_name=fname,
                entity_id=entity_id,
            )
        )
    if (
        f_old.is_bitfield != f_new.is_bitfield
        or f_old.bitfield_bits != f_new.bitfield_bits
    ):
        changes.append(
            make_change(
                ChangeKind.FIELD_BITFIELD_CHANGED,
                symbol=name,
                name=name,
                detail=fname,
                old_value=f"bits={f_old.bitfield_bits}",
                new_value=f"bits={f_new.bitfield_bits}",
                entity_id=entity_id,
            )
        )
    return changes


_FAM_TYPE_RE = re.compile(r"^(.+)\s*\[\s*0?\s*\]$")


def _is_flexible_array_member(field: TypeField) -> bool:
    """Return True if *field* looks like a C99 flexible array member (T[] or T[0])."""
    return bool(_FAM_TYPE_RE.match(field.type))


def _diff_flexible_array_member(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
) -> list[Change]:
    """Detect changes to trailing flexible array members.

    libabigail's BENIGN_INFINITE_ARRAY_CHANGE_CATEGORY: array type size changed
    in a global variable or struct field, but the ELF symbol size is unchanged
    (typical for flexible array members / zero-length arrays).
    """
    changes: list[Change] = []
    old_fam = (
        t_old.fields[-1]
        if t_old.fields and _is_flexible_array_member(t_old.fields[-1])
        else None
    )
    new_fam = (
        t_new.fields[-1]
        if t_new.fields and _is_flexible_array_member(t_new.fields[-1])
        else None
    )

    if old_fam is None and new_fam is None:
        return changes

    if old_fam is not None and new_fam is None:
        # FAM removed — already caught by TYPE_FIELD_REMOVED, but emit specific kind
        changes.append(
            make_change(
                ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
                symbol=name,
                description=f"Flexible array member removed: {name}::{old_fam.name}",
                old_value=old_fam.type,
                new_value="(removed)",
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )
    elif old_fam is None and new_fam is not None:
        # FAM added
        changes.append(
            make_change(
                ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
                symbol=name,
                description=f"Flexible array member added: {name}::{new_fam.name}",
                old_value="(none)",
                new_value=new_fam.type,
                entity_id=t_old.entity_id or t_new.entity_id,
            )
        )
    elif old_fam is not None and new_fam is not None:
        # Both have FAM — check if element type changed
        old_elem = _FAM_TYPE_RE.match(old_fam.type)
        new_elem = _FAM_TYPE_RE.match(new_fam.type)
        if old_elem and new_elem:
            old_base = canonicalize_type_name(old_elem.group(1))
            new_base = canonicalize_type_name(new_elem.group(1))
            if old_base != new_base:
                changes.append(
                    make_change(
                        ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
                        symbol=name,
                        description=(
                            f"Flexible array member element type changed: "
                            f"{name}::{old_fam.name} ({old_fam.type} → {new_fam.type})"
                        ),
                        old_value=old_fam.type,
                        new_value=new_fam.type,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

    return changes


def _new_field_change_kind(t_new: RecordType) -> ChangeKind:
    # Field addition is BREAKING for polymorphic types or types with vtables;
    # COMPATIBLE only for standard-layout types without virtual functions.
    new_vtable = resolved_fact_value(t_new.vtable_fact, [])
    new_virtual_bases = resolved_fact_value(t_new.virtual_bases_fact, [])
    is_polymorphic = bool(new_vtable or new_virtual_bases)
    return (
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
        if not is_polymorphic and t_new.kind == "struct"
        else ChangeKind.TYPE_FIELD_ADDED
    )


def _diff_type_bases(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    """``bases``/``virtual_bases`` findings — ADR-063 Phase 5B's first
    ``FactStatus``-aware cohort.

    Each side's evidence is gated through :func:`compare_facts` *before*
    the two lists are compared: an incomplete ``bases_fact``/
    ``virtual_bases_fact`` on either side (``NOT_COLLECTED``/``FAILED``/
    an ``UNSUPPORTED`` producer) used to fall through
    ``resolved_fact_value(..., [])`` and read as "this side has no bases" —
    fabricating ``TYPE_BASE_CHANGED``/``BASE_CLASS_VIRTUAL_CHANGED`` findings
    against real bases on the other side purely from a capture gap, not a
    real hierarchy change. This declines to compare instead (the same
    "decline rather than fabricate" discipline ``diff_types_vtable.
    _vtable_transition_is_evidenced`` already applies to the sibling vtable
    signal), rather than changing what a *fully-evidenced* pair reports —
    behavior-preserving whenever both sides' facts are actually
    ``PRESENT``/``PARTIAL``.

    The two comparisons are gated independently: a comparable
    ``virtual_bases`` pair with an incomplete ``bases`` pair still reports a
    virtual-base-only hierarchy change (just without the finer
    became-virtual/lost-virtual classification, which needs the
    non-virtual-base set too) rather than being withheld entirely.
    """
    changes: list[Change] = []
    entity_id = t_old.entity_id or t_new.entity_id

    bases_cmp = compare_facts(t_old.bases_fact, t_new.bases_fact, [])
    virtual_bases_cmp = compare_facts(
        t_old.virtual_bases_fact, t_new.virtual_bases_fact, []
    )

    old_bases_set: set[str] = set()
    new_bases_set: set[str] = set()
    if bases_cmp.is_comparable:
        old_bases = bases_cmp.old_value or []
        new_bases = bases_cmp.new_value or []
        # BASE_CLASS_POSITION_CHANGED: same set of non-virtual bases, different order
        # This shifts this-pointer adjustments for all bases → old binaries call wrong method.
        old_bases_set = set(old_bases)
        new_bases_set = set(new_bases)
        if old_bases_set == new_bases_set and old_bases != new_bases:
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_POSITION_CHANGED,
                    symbol=name,
                    name=name,
                    old_value=str(old_bases),
                    new_value=str(new_bases),
                    entity_id=entity_id,
                )
            )
        elif old_bases_set != new_bases_set:
            # General base class set change (add/remove base) → TYPE_BASE_CHANGED
            changes.append(
                make_change(
                    ChangeKind.TYPE_BASE_CHANGED,
                    symbol=name,
                    description=f"Base classes changed: {name}",
                    old_value=str(old_bases),
                    new_value=str(new_bases),
                    entity_id=entity_id,
                )
            )

    if virtual_bases_cmp.is_comparable:
        old_virtual_bases = virtual_bases_cmp.old_value or []
        new_virtual_bases = virtual_bases_cmp.new_value or []
        # BASE_CLASS_VIRTUAL_CHANGED: a base moved between virtual and non-virtual
        old_virt_set = set(old_virtual_bases)
        new_virt_set = set(new_virtual_bases)
        # Bases that moved from non-virtual to virtual or vice versa. Empty
        # (rather than wrong) when `bases_cmp` was incomplete above: the
        # intersection against an empty `old_bases_set`/`new_bases_set` is
        # always empty, so this falls through to the plain hierarchy-change
        # branch below instead of guessing at a virtuality flip it has no
        # non-virtual-base evidence to support.
        became_virtual = (new_virt_set - old_virt_set) & old_bases_set
        lost_virtual = (old_virt_set - new_virt_set) & new_bases_set
        if became_virtual or lost_virtual:
            desc_parts = []
            if became_virtual:
                desc_parts.append(f"became virtual: {sorted(became_virtual)}")
            if lost_virtual:
                desc_parts.append(f"lost virtual: {sorted(lost_virtual)}")
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,
                    symbol=name,
                    name=name,
                    detail="; ".join(desc_parts),
                    old_value=str(sorted(old_virtual_bases)),
                    new_value=str(sorted(new_virtual_bases)),
                    entity_id=entity_id,
                )
            )
        elif old_virt_set != new_virt_set:
            # Pure add/remove of a virtual base (not a migration from non-virtual):
            # e.g. class D : virtual A  →  class D : virtual A, virtual B
            # → TYPE_BASE_CHANGED (hierarchy changed, not just virtuality toggled)
            if (
                not changes
            ):  # don't duplicate if TYPE_BASE_CHANGED already emitted above
                changes.append(
                    make_change(
                        ChangeKind.TYPE_BASE_CHANGED,
                        symbol=name,
                        description=f"Virtual base classes changed: {name}",
                        old_value=str(old_virtual_bases),
                        new_value=str(new_virtual_bases),
                        entity_id=entity_id,
                    )
                )

    return changes


@registry.detector("enums")
def _diff_enums(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    # Namespace-qualified matching (build_type_map/lookup_matched_type,
    # generalized from RecordType in PR #608 follow-up): two distinct enums
    # sharing a bare leaf name in different namespaces must not cross-match
    # across old/new the way a plain ``{e.name: e}`` dict would.
    old_map = _build_type_map(
        e
        for e in old.enums
        if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    )
    new_map = _build_type_map(
        e
        for e in new.enums
        if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    )
    # A wholly-removed enum is stored in snap.enums, NOT snap.types, so the
    # TYPE_REMOVED loop in _diff_types() never sees it — the old "TYPE_REMOVED
    # covers this" comment was wrong and the removal went silently undetected
    # (case78). Emit it here, guarded by the same stripped-binary suppression
    # _diff_types uses so a stripped new side can't manufacture phantom removals.
    suppress_removed = _removals_are_unconfirmed(old, new)

    for e_old in old_map.values():
        name = e_old.name
        e_new = _lookup_matched_type(old_map, new_map, e_old)
        if e_new is None:
            if not suppress_removed:
                changes.append(
                    make_change(
                        ChangeKind.TYPE_REMOVED,
                        symbol=name,
                        description=f"Enum removed: {name}",
                        entity_id=e_old.entity_id,
                    )
                )
            continue
        # Per-enum key, not a whole-snapshot gate: supports --ast-frontend
        # hybrid (G28 Phase 3); both backends populate is_scoped (G31 Phase C).
        if fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            name,
            enum_fact_key(type_map_key(e_old), "is_scoped"),
            enum_fact_key(type_map_key(e_new), "is_scoped"),
            enum_fact_key(name, "is_scoped"),
        ):
            _append_enum_scoped_changes(changes, name, e_old, e_new)
        old_members = {m.name: m.value for m in e_old.members}
        new_members = {m.name: m.value for m in e_new.members}
        # Build inverse map: new_value → new_name for values that are "new" (not in old names).
        # Only keep entries where the value maps to exactly one new name —
        # aliases (multiple names with same value) must not suppress true removals.
        _new_val_candidates: dict[int, list[str]] = {}
        for nname, nval in new_members.items():
            if nname not in old_members:
                _new_val_candidates.setdefault(nval, []).append(nname)
        new_val_to_newname = {
            nval: nnames[0]
            for nval, nnames in _new_val_candidates.items()
            if len(nnames) == 1
        }

        for mname, mval in old_members.items():
            if mname not in new_members:
                # rename-only -> skip removed emission
                if mval in new_val_to_newname:
                    continue
                # Value truly removed
                changes.append(
                    make_change(
                        ChangeKind.ENUM_MEMBER_REMOVED,
                        symbol=f"{name}::{mname}",
                        name=name,
                        detail=mname,
                        old_value=str(mval),
                        entity_id=e_old.entity_id or e_new.entity_id,
                    )
                )
            elif new_members[mname] != mval:
                kind = (
                    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED
                    if is_sentinel_enum_member(mname)
                    else ChangeKind.ENUM_MEMBER_VALUE_CHANGED
                )
                changes.append(
                    make_change(
                        kind,
                        symbol=f"{name}::{mname}",
                        name=name,
                        detail=mname,
                        old_value=str(mval),
                        new_value=str(new_members[mname]),
                        entity_id=e_old.entity_id or e_new.entity_id,
                    )
                )

        # Skip additions whose values exist in the old enum:
        # those will be handled as ENUM_MEMBER_RENAMED by _diff_enum_renames,
        # Skip additions whose value matches a removed old member (likely a rename).
        # Use only *removed* old member values — if the old member still exists under
        # the same name, the new member is a genuine addition (alias/duplicate), not
        # a rename, and must be reported. (CodeRabbit P1: one-to-one guard)
        # One-to-one guard: only suppress additions when the removed old value
        # maps to exactly one removed member (aliases must not suppress).
        _removed_val_candidates: dict[int, list[str]] = {}
        for mname_r, v in old_members.items():
            if mname_r not in new_members:
                _removed_val_candidates.setdefault(v, []).append(mname_r)
        removed_old_values = {
            str(v) for v, names in _removed_val_candidates.items() if len(names) == 1
        }
        for mname, mval in new_members.items():
            if mname not in old_members:
                if str(mval) in removed_old_values:
                    continue  # same value as a removed old member — rename candidate
                changes.append(
                    make_change(
                        ChangeKind.ENUM_MEMBER_ADDED,
                        symbol=f"{name}::{mname}",
                        name=name,
                        detail=mname,
                        new_value=str(mval),
                        entity_id=e_old.entity_id or e_new.entity_id,
                    )
                )

    return changes


def _append_enum_scoped_changes(
    changes: list[Change],
    name: str,
    e_old: EnumType,
    e_new: EnumType,
) -> None:
    """Detect `enum class`/`enum struct` (C++11 scoped enum) transitions.

    Tri-state, same rationale as ``_append_type_finality_changes``: only fire
    when BOTH sides record it; ``None`` means the dumper couldn't determine
    it (DWARF/symbols-only mode, older snapshots) rather than "not scoped".
    Unlike ``is_final`` (populated by both the castxml and clang header
    backends), ``is_scoped`` is castxml-only today — the caller
    (``_diff_enums``) additionally gates the call on
    ``_both_castxml_backed`` so a clang-parsed side's unconditional ``None``
    is never misread as "scoping was removed" (Codex review, PR #582).
    """
    if e_old.is_scoped is None or e_new.is_scoped is None:
        return
    if e_old.is_scoped == e_new.is_scoped:
        return
    if e_new.is_scoped:
        changes.append(
            make_change(
                ChangeKind.ENUM_BECAME_SCOPED,
                symbol=name,
                name=name,
                old_value="unscoped",
                new_value="scoped",
                entity_id=e_old.entity_id or e_new.entity_id,
            )
        )
    else:
        changes.append(
            make_change(
                ChangeKind.ENUM_LOST_SCOPED,
                symbol=name,
                name=name,
                old_value="scoped",
                new_value="unscoped",
                entity_id=e_old.entity_id or e_new.entity_id,
            )
        )


def _sig_key(f: Function) -> tuple[str, tuple[str, ...]]:
    """Normalized match key: (function_name, param_types).

    cv-qualifiers (const/volatile on `this`) and static-ness are NOT encoded in
    the mangled name lookup table, so we use the unqualified name + params tuple
    to find matching pairs across qualifier changes.
    """
    return (f.name, tuple(p.type for p in f.params))


@registry.detector("method_qualifiers")
def _diff_method_qualifiers(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect cv-qualifier, static, and pure-virtual changes.

    NOTE: Changing const/volatile/static causes the mangled symbol name to
    change (e.g. Foo::bar() const → _ZNK3Foo3barEv vs _ZN3Foo3barEv).  We
    therefore match functions by (name, param_types) rather than mangled name
    to find cross-qualifier pairs in the removed/added sets.
    """
    changes: list[Change] = []
    old_by_mangled = _public_functions(old)
    new_by_mangled = _public_functions(new)

    # --- Same-mangled checks: pure_virtual and is_static don't change mangling ---
    # is_static: Itanium ABI does NOT encode static-ness in the mangled name
    # (void Widget::bar() and static void Widget::bar() share the same mangled symbol).
    # is_pure_virtual: pure_virtual is not part of the mangled name either.
    for mangled, f_old in old_by_mangled.items():
        if mangled not in new_by_mangled:
            continue
        f_new = new_by_mangled[mangled]

        if f_old.is_static != f_new.is_static:
            changes.append(
                make_change(
                    ChangeKind.FUNC_STATIC_CHANGED,
                    symbol=mangled,
                    name=f_old.name,
                    old_value=str(f_old.is_static),
                    new_value=str(f_new.is_static),
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )

        if not f_old.is_pure_virtual and f_new.is_pure_virtual:
            kind = (
                ChangeKind.FUNC_VIRTUAL_BECAME_PURE
                if f_old.is_virtual
                else ChangeKind.FUNC_PURE_VIRTUAL_ADDED
            )
            changes.append(
                make_change(
                    kind,
                    symbol=mangled,
                    name=f_old.name,
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )

    # --- cv/static detection: match removed functions against added functions by sig ---
    old_mangles = set(old_by_mangled)
    new_mangles = set(new_by_mangled)
    removed_funcs = [old_by_mangled[m] for m in (old_mangles - new_mangles)]
    added_funcs = [new_by_mangled[m] for m in (new_mangles - old_mangles)]

    # Build sig-keyed lookup over newly-added functions
    added_by_sig: dict[tuple[str, tuple[str, ...]], Function] = {}
    for f in added_funcs:
        added_by_sig[_sig_key(f)] = f

    for f_old in removed_funcs:
        key = _sig_key(f_old)
        if key not in added_by_sig:
            continue
        f_new = added_by_sig[key]
        # Same (name, params) — only qualifiers changed; report instead of REMOVED+ADDED
        if f_old.is_static != f_new.is_static:
            changes.append(
                make_change(
                    ChangeKind.FUNC_STATIC_CHANGED,
                    symbol=f_old.mangled,
                    name=f_old.name,
                    old_value=str(f_old.is_static),
                    new_value=str(f_new.is_static),
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )
        if f_old.is_const != f_new.is_const or f_old.is_volatile != f_new.is_volatile:
            changes.append(
                make_change(
                    ChangeKind.FUNC_CV_CHANGED,
                    symbol=f_old.mangled,
                    name=f_old.name,
                    old_value=f"const={f_old.is_const} volatile={f_old.is_volatile}",
                    new_value=f"const={f_new.is_const} volatile={f_new.is_volatile}",
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )

        # Ref-qualifier change (&/&&) — also changes mangled name
        old_rq = f_old.ref_qualifier or ""
        new_rq = f_new.ref_qualifier or ""
        if old_rq != new_rq:
            changes.append(
                make_change(
                    ChangeKind.FUNC_REF_QUAL_CHANGED,
                    symbol=f_old.mangled,
                    name=f_old.name,
                    old=repr(old_rq),
                    new=repr(new_rq),
                    old_value=old_rq or "(none)",
                    new_value=new_rq or "(none)",
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )

    return changes


@registry.detector("unions")
def _diff_unions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_unions = _build_type_map(
        t
        for t in old.types
        if t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_unions = _build_type_map(
        t
        for t in new.types
        if t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    # Same legacy-snapshot cv-fact concern as _diff_type_field_pair (Codex
    # review, PR #582).
    cv_facts_reliable = old.header_cv_facts_reliable and new.header_cv_facts_reliable

    for t_old in old_unions.values():
        t_new = _lookup_matched_type(old_unions, new_unions, t_old)
        if t_new is None:
            continue  # covered by TYPE_REMOVED
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        for fname, f_old in old_fields.items():
            if fname not in new_fields:
                changes.append(
                    make_change(
                        ChangeKind.UNION_FIELD_REMOVED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old_value=f_old.type,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )
            elif _field_type_genuinely_changed(
                f_old.type, new_fields[fname].type, cv_facts_reliable=cv_facts_reliable
            ):
                changes.append(
                    make_change(
                        ChangeKind.UNION_FIELD_TYPE_CHANGED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old_value=f_old.type,
                        new_value=new_fields[fname].type,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

        for fname, f_new in new_fields.items():
            if fname not in old_fields:
                changes.append(
                    make_change(
                        ChangeKind.UNION_FIELD_ADDED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        new_value=f_new.type,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

    return changes


_VERSION_STAMPED_TYPEDEF_RE = re.compile(r"^(.*?)_version_\d+_\d+_\d+$", re.IGNORECASE)
"""Pattern for version-stamped compile-time sentinel typedefs.

Some libraries (e.g. libpng) define typedefs whose names encode the library
version, e.g. ``typedef char* png_libpng_version_1_6_46``.  The name changes
every release by design — this is NOT a binary ABI break because the typedef
is never exported as an ELF symbol; it exists solely to produce a
compile-time error if headers from different versions are mixed.

When such a typedef disappears (``typedef_removed``), abicheck would
otherwise report BREAKING.  This guard downgrades the change to
TYPEDEF_VERSION_SENTINEL (COMPATIBLE) instead.
"""


def _is_version_stamped_typedef(name: str) -> bool:
    """Return True if *name* looks like a version-stamped sentinel typedef."""
    return bool(_VERSION_STAMPED_TYPEDEF_RE.match(name))


def _has_version_family_successor(name: str, new_typedefs: dict[str, str]) -> bool:
    """Return True if *new_typedefs* contains another version-stamped typedef
    with the same family prefix (e.g. ``png_libpng_version_``).

    This distinguishes a sentinel rotation (old version removed, new version
    added) from a genuine typedef removal where the name happens to match the
    version-stamp pattern.
    """
    m = _VERSION_STAMPED_TYPEDEF_RE.match(name)
    if not m:
        return False
    prefix = m.group(1).lower()
    # Require a non-empty family prefix to avoid matching unrelated sentinels
    # when the name itself starts with _version_ (e.g. ``_version_1_0_0``).
    if not prefix:
        return False
    prefix = prefix + "_version_"
    return any(k.lower().startswith(prefix) for k in new_typedefs)


@registry.detector("typedefs")
def _diff_typedefs(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    # RD2-5: don't manufacture phantom TYPEDEF_REMOVED when the new side is stripped.
    suppress_removed = _removals_are_unconfirmed(old, new)
    old_typedefs, new_typedefs = _typedef_diff_maps(old, new)
    for alias, old_type in old_typedefs.items():
        # Full alias: correct for both legacy-DWARF's and the qualified map's keys.
        if _is_non_abi_surface_type(alias, exclude_stdlib_namespaces=excl):
            continue
        # symbol/name stay bare like _diff_types (else _enrich_affected_symbols
        # breaks); qualified_suffix disambiguates description so dedup can't collapse collisions.
        bare_alias = alias.rsplit("::", 1)[-1]
        qualified_suffix = f" ({alias})" if alias != bare_alias else ""
        new_type = new_typedefs.get(alias)
        if new_type is None and suppress_removed:
            continue
        eid = old.typedef_entity_ids.get(alias) or new.typedef_entity_ids.get(alias)
        if new_type is None:
            # Version-stamped typedefs (e.g. png_libpng_version_1_6_46) are
            # compile-time sentinels that change every release by design and
            # are never exported as ELF symbols -- not a binary ABI break.
            # Require a same-family successor to avoid hiding a genuine
            # TYPEDEF_REMOVED for a name that merely matches the pattern.
            if _is_version_stamped_typedef(alias) and _has_version_family_successor(
                alias, new_typedefs
            ):
                changes.append(
                    make_change(
                        ChangeKind.TYPEDEF_VERSION_SENTINEL,
                        symbol=bare_alias,
                        name=bare_alias,
                        old_value=old_type,
                        entity_id=eid,
                    )
                )
                continue
            # Typedef removed — breaking for consumers that used the alias
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_REMOVED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    entity_id=eid,
                    description=f"Typedef removed: {bare_alias}{qualified_suffix}",
                )
            )
        elif new_type != old_type:
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_BASE_CHANGED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    new_value=new_type,
                    entity_id=eid,
                    description=f"Typedef base type changed: {bare_alias}{qualified_suffix}",
                )
            )
    return changes
