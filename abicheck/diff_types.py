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
from collections import Counter
from collections.abc import Collection

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_cxx_rules import itanium_qualified_name
from .diff_symbols import (
    _PUBLIC_VIS,
    _public_functions,
    _public_variables,
    _should_filter_transitive_runtime_symbols,
)
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from .model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    TypeField,
    canonicalize_type_name,
    cv_qualifiers_only_differ,
)
from .model import is_non_abi_surface_type as _is_non_abi_surface_type
from .model import stdlib_namespaces_excluded as _exclude_stdlib_namespaces


def _exported_elf_symbol_names(snap: AbiSnapshot, *, symbol_types: Collection[str]) -> set[str]:
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


def _is_abi_surface_type(t: RecordType, *, exclude_stdlib: bool) -> bool:
    """True if record *t* is part of the inspected library's ABI surface.

    Shared by every type-level detector (records, unions, field/kind/reserved
    diffs) so std::/anonymous filtering (FP-1/FP-2) is applied consistently and
    cannot be bypassed via an alternate map-construction path.
    """
    return not _is_non_abi_surface_type(t.name, exclude_stdlib_namespaces=exclude_stdlib)


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
    # Corroborate with exported-symbol retention: a stripped-but-intact binary
    # keeps (almost) all of the old side's exports; if most are gone, the library
    # genuinely changed and removals are real.
    #
    # Only count *exported* symbols (PUBLIC/ELF_ONLY). A DWARF-primary old
    # snapshot also records internal/static subprograms, but the stripped new
    # side carries dynamic exports only — counting internals would deflate
    # retention and defeat the suppression for a genuinely intact comparison.
    # Prefer functions; fall back to variables for data-only DSOs so a changed
    # variable surface still surfaces removals (CodeRabbit review on PR #275).
    old_funcs = _exported_elf_symbol_names(old, symbol_types=FUNCTION_SYMBOL_TYPES)
    new_funcs = _exported_elf_symbol_names(new, symbol_types=FUNCTION_SYMBOL_TYPES)
    if not old_funcs or not new_funcs:
        old_funcs = {k for k, v in old.function_map.items() if v.visibility in _PUBLIC_VIS}
        new_funcs = {k for k, v in new.function_map.items() if v.visibility in _PUBLIC_VIS}
    if old_funcs:
        return len(old_funcs & new_funcs) / len(old_funcs) >= 0.9
    old_vars = _exported_elf_symbol_names(old, symbol_types=VARIABLE_SYMBOL_TYPES)
    new_vars = _exported_elf_symbol_names(new, symbol_types=VARIABLE_SYMBOL_TYPES)
    if not old_vars or not new_vars:
        old_vars = {k for k, v in old.variable_map.items() if v.visibility in _PUBLIC_VIS}
        new_vars = {k for k, v in new.variable_map.items() if v.visibility in _PUBLIC_VIS}
    if not old_vars:
        return True  # no exported surface to corroborate; absence of types is just stripping
    return len(old_vars & new_vars) / len(old_vars) >= 0.9


@registry.detector("types")
def _diff_types(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    # Include ALL types (including unions) for size/alignment/base/vtable checks.
    # TYPE_FIELD_* for unions is skipped below — handled by _diff_unions() instead.
    excl = _exclude_stdlib_namespaces(old, new)
    old_map = {t.name: t for t in old.types if _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if _is_abi_surface_type(t, exclude_stdlib=excl)}
    # RD2-5: don't manufacture phantom TYPE_REMOVED when the new side is stripped.
    suppress_removed = _removals_are_unconfirmed(old, new)

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            if suppress_removed:
                continue
            changes.append(Change(
                kind=ChangeKind.TYPE_REMOVED,
                symbol=name,
                description=f"Type removed: {name}",
            ))
            continue
        changes.extend(_diff_type_pair(name, t_old, t_new))

    for name in new_map:
        if name not in old_map:
            changes.append(Change(
                kind=ChangeKind.TYPE_ADDED,
                symbol=name,
                description=f"New type: {name}",
            ))

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
        changes.append(Change(
            kind=ChangeKind.OVERLOAD_ADDED,
            symbol=original.mangled,
            description=(
                f"Overload added to previously non-overloaded function: {key} "
                f"— `&{key}` becomes ambiguous and overload resolution may change"
            ),
            old_value="1 overload",
            new_value=f"{len(news)} overloads",
        ))
    return changes


def _diff_type_pair(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    changes: list[Change] = []

    # TYPE_BECAME_OPAQUE: was complete, now forward-decl only
    if not t_old.is_opaque and t_new.is_opaque:
        changes.append(Change(
            kind=ChangeKind.TYPE_BECAME_OPAQUE,
            symbol=name,
            description=f"Type became opaque (forward-declaration only): {name} — stack allocation no longer possible",
            old_value="complete",
            new_value="opaque",
        ))
        return changes  # no further checks meaningful for opaque type

    _append_type_size_and_alignment_changes(changes, name, t_old, t_new)
    if not t_old.is_union:
        changes.extend(_diff_type_fields(name, t_old, t_new))
    changes.extend(_diff_type_bases(name, t_old, t_new))
    changes.extend(_diff_type_vtable(name, t_old, t_new))
    _append_type_finality_changes(changes, name, t_old, t_new)
    return changes


def _append_type_finality_changes(
    changes: list[Change], name: str, t_old: RecordType, t_new: RecordType,
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
        changes.append(Change(
            kind=ChangeKind.TYPE_BECAME_FINAL,
            symbol=name,
            description=f"Class gained `final` specifier: {name} — consumers that derive from it no longer compile",
            old_value="non-final",
            new_value="final",
        ))
    else:
        changes.append(Change(
            kind=ChangeKind.TYPE_LOST_FINAL,
            symbol=name,
            description=f"Class lost `final` specifier: {name}",
            old_value="final",
            new_value="non-final",
        ))


def _append_type_size_and_alignment_changes(
    changes: list[Change], name: str, t_old: RecordType, t_new: RecordType,
) -> None:
    if t_old.size_bits is not None and t_new.size_bits is not None and t_old.size_bits != t_new.size_bits:
        changes.append(Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol=name,
            description=f"Size changed: {name} ({t_old.size_bits} → {t_new.size_bits} bits)",
            old_value=str(t_old.size_bits),
            new_value=str(t_new.size_bits),
        ))

    if (
        t_old.alignment_bits is not None
        and t_new.alignment_bits is not None
        and t_old.alignment_bits != t_new.alignment_bits
    ):
        changes.append(Change(
            kind=ChangeKind.TYPE_ALIGNMENT_CHANGED,
            symbol=name,
            description=f"Alignment changed: {name} ({t_old.alignment_bits} → {t_new.alignment_bits} bits)",
            old_value=str(t_old.alignment_bits),
            new_value=str(t_new.alignment_bits),
        ))


_RESERVED_FIELD_RE = re.compile(
    r"^_{0,2}(reserved|pad|padding|spare|unused|mbz|fill|filler)\d*$",
    re.IGNORECASE,
)



def _try_match_reserved_field(
    fname: str,
    f_old: TypeField,
    name: str,
    added_by_offset: dict[int, TypeField],
    added_by_type: dict[str, list[TypeField]],
    reserved_matched_added: set[str],
) -> Change | None:
    """Check if a removed field is a reserved field put into use.

    Returns a USED_RESERVED_FIELD Change if matched, or None.
    """
    if not _RESERVED_FIELD_RE.match(fname):
        return None

    candidate: TypeField | None = None
    # Primary: match by offset + type (when available)
    if f_old.offset_bits is not None:
        c = added_by_offset.get(f_old.offset_bits)
        if c is not None and f_old.type == c.type:
            candidate = c
    # Fallback: match by type when offsets unavailable (DWARF-only)
    if candidate is None and f_old.offset_bits is None:
        candidates = added_by_type.get(f_old.type, [])
        for c in candidates:
            if c.name not in reserved_matched_added:
                candidate = c
                break
    if candidate is not None and not _RESERVED_FIELD_RE.match(candidate.name):
        # Reserved field -> real field at same offset/type -> COMPATIBLE
        reserved_matched_added.add(candidate.name)
        return Change(
            kind=ChangeKind.USED_RESERVED_FIELD,
            symbol=name,
            description=f"Reserved field put into use: {name}::{fname} → {candidate.name}",
            old_value=fname,
            new_value=candidate.name,
        )
    return None


def _diff_type_fields(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    changes: list[Change] = []
    old_fields = {f.name: f for f in t_old.fields}
    new_fields = {f.name: f for f in t_new.fields}

    # Build index of added fields by offset for reserved-field matching.
    added_names = {fname for fname in new_fields if fname not in old_fields}
    added_by_offset: dict[int, TypeField] = {}
    # Also build an ordered list of added non-reserved fields for fallback
    # when offset_bits is unavailable (e.g. DWARF-only mode).
    added_by_type: dict[str, list[TypeField]] = {}
    for fname in added_names:
        f = new_fields[fname]
        if f.offset_bits is not None:
            added_by_offset[f.offset_bits] = f
        if not _RESERVED_FIELD_RE.match(fname):
            added_by_type.setdefault(f.type, []).append(f)
    # Track which added fields were matched as reserved-field activations
    # so we skip emitting TYPE_FIELD_ADDED for them.
    reserved_matched_added: set[str] = set()

    # Identify trailing FAM fields so we can skip them from generic checks
    # and let _diff_flexible_array_member handle them exclusively.
    old_trailing_fam = (
        t_old.fields[-1].name if t_old.fields and _is_flexible_array_member(t_old.fields[-1]) else None
    )
    new_trailing_fam = (
        t_new.fields[-1].name if t_new.fields and _is_flexible_array_member(t_new.fields[-1]) else None
    )

    for fname, f_old in old_fields.items():
        f_new = new_fields.get(fname)
        if f_new is None:
            # Skip trailing FAM removals — handled by _diff_flexible_array_member
            if fname == old_trailing_fam:
                continue
            # Check if this is a reserved field put into use
            matched = _try_match_reserved_field(
                fname, f_old, name, added_by_offset, added_by_type, reserved_matched_added,
            )
            if matched is not None:
                changes.append(matched)
                continue
            changes.append(Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol=name,
                description=f"Field removed: {name}::{fname}",
            ))
            continue
        # Skip trailing FAM type changes — handled by _diff_flexible_array_member
        if fname == old_trailing_fam and fname == new_trailing_fam:
            continue
        changes.extend(_diff_type_field_pair(name, fname, f_old, f_new))

    for fname in new_fields:
        if fname not in old_fields and fname not in reserved_matched_added:
            # Skip trailing FAM additions — handled by _diff_flexible_array_member
            if fname == new_trailing_fam:
                continue
            changes.append(Change(
                kind=_new_field_change_kind(t_new),
                symbol=name,
                description=f"Field added: {name}::{fname}",
            ))

    # FLEXIBLE_ARRAY_MEMBER_CHANGED: detect changes to trailing flexible array members.
    # A FAM is the last field with type matching "T []" or "T [0]" — zero static size.
    changes.extend(_diff_flexible_array_member(name, t_old, t_new))

    return changes


def _diff_type_field_pair(name: str, fname: str, f_old: TypeField, f_new: TypeField) -> list[Change]:
    changes: list[Change] = []
    # Use canonical form for type comparison to avoid false positives from
    # "struct Foo" vs "Foo" or "const int" vs "int const" differences.
    # A pointee/by-value cv-qualifier change (``char *`` -> ``const char *``)
    # leaves the field's size and offset unchanged — it is source-level churn,
    # not a binary layout break (ISSUE-30/35/65). Top-level field const/volatile
    # is reported separately by the ``field_qualifiers`` detector.
    if (
        canonicalize_type_name(f_old.type) != canonicalize_type_name(f_new.type)
        and not cv_qualifiers_only_differ(f_old.type, f_new.type)
    ):
        changes.append(Change(
            kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
            symbol=name,
            description=f"Field type changed: {name}::{fname}",
            old_value=f_old.type,
            new_value=f_new.type,
        ))
    if f_old.offset_bits is not None and f_new.offset_bits is not None and f_old.offset_bits != f_new.offset_bits:
        changes.append(Change(
            kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
            symbol=name,
            description=f"Field offset changed: {name}::{fname} ({f_old.offset_bits} → {f_new.offset_bits} bits)",
            old_value=str(f_old.offset_bits),
            new_value=str(f_new.offset_bits),
        ))
    if f_old.is_bitfield != f_new.is_bitfield or f_old.bitfield_bits != f_new.bitfield_bits:
        changes.append(Change(
            kind=ChangeKind.FIELD_BITFIELD_CHANGED,
            symbol=name,
            description=f"Bitfield layout changed: {name}::{fname}",
            old_value=f"bits={f_old.bitfield_bits}",
            new_value=f"bits={f_new.bitfield_bits}",
        ))
    return changes


_FAM_TYPE_RE = re.compile(r"^(.+)\s*\[\s*0?\s*\]$")


def _is_flexible_array_member(field: TypeField) -> bool:
    """Return True if *field* looks like a C99 flexible array member (T[] or T[0])."""
    return bool(_FAM_TYPE_RE.match(field.type))


def _diff_flexible_array_member(
    name: str, t_old: RecordType, t_new: RecordType,
) -> list[Change]:
    """Detect changes to trailing flexible array members.

    libabigail's BENIGN_INFINITE_ARRAY_CHANGE_CATEGORY: array type size changed
    in a global variable or struct field, but the ELF symbol size is unchanged
    (typical for flexible array members / zero-length arrays).
    """
    changes: list[Change] = []
    old_fam = t_old.fields[-1] if t_old.fields and _is_flexible_array_member(t_old.fields[-1]) else None
    new_fam = t_new.fields[-1] if t_new.fields and _is_flexible_array_member(t_new.fields[-1]) else None

    if old_fam is None and new_fam is None:
        return changes

    if old_fam is not None and new_fam is None:
        # FAM removed — already caught by TYPE_FIELD_REMOVED, but emit specific kind
        changes.append(Change(
            kind=ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
            symbol=name,
            description=f"Flexible array member removed: {name}::{old_fam.name}",
            old_value=old_fam.type,
            new_value="(removed)",
        ))
    elif old_fam is None and new_fam is not None:
        # FAM added
        changes.append(Change(
            kind=ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
            symbol=name,
            description=f"Flexible array member added: {name}::{new_fam.name}",
            old_value="(none)",
            new_value=new_fam.type,
        ))
    elif old_fam is not None and new_fam is not None:
        # Both have FAM — check if element type changed
        old_elem = _FAM_TYPE_RE.match(old_fam.type)
        new_elem = _FAM_TYPE_RE.match(new_fam.type)
        if old_elem and new_elem:
            old_base = canonicalize_type_name(old_elem.group(1))
            new_base = canonicalize_type_name(new_elem.group(1))
            if old_base != new_base:
                changes.append(Change(
                    kind=ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED,
                    symbol=name,
                    description=(
                        f"Flexible array member element type changed: "
                        f"{name}::{old_fam.name} ({old_fam.type} → {new_fam.type})"
                    ),
                    old_value=old_fam.type,
                    new_value=new_fam.type,
                ))

    return changes


def _new_field_change_kind(t_new: RecordType) -> ChangeKind:
    # Field addition is BREAKING for polymorphic types or types with vtables;
    # COMPATIBLE only for standard-layout types without virtual functions.
    is_polymorphic = bool(t_new.vtable or t_new.virtual_bases)
    return (
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
        if not is_polymorphic and t_new.kind == "struct"
        else ChangeKind.TYPE_FIELD_ADDED
    )


def _diff_type_bases(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    changes: list[Change] = []

    # BASE_CLASS_POSITION_CHANGED: same set of non-virtual bases, different order
    # This shifts this-pointer adjustments for all bases → old binaries call wrong method.
    old_bases_set = set(t_old.bases)
    new_bases_set = set(t_new.bases)
    if old_bases_set == new_bases_set and t_old.bases != t_new.bases:
        changes.append(Change(
            kind=ChangeKind.BASE_CLASS_POSITION_CHANGED,
            symbol=name,
            description=f"Base class order reordered: {name} — this-pointer adjustments changed",
            old_value=str(t_old.bases),
            new_value=str(t_new.bases),
        ))
    elif old_bases_set != new_bases_set:
        # General base class set change (add/remove base) → TYPE_BASE_CHANGED
        changes.append(Change(
            kind=ChangeKind.TYPE_BASE_CHANGED,
            symbol=name,
            description=f"Base classes changed: {name}",
            old_value=str(t_old.bases),
            new_value=str(t_new.bases),
        ))

    # BASE_CLASS_VIRTUAL_CHANGED: a base moved between virtual and non-virtual
    old_virt_set = set(t_old.virtual_bases)
    new_virt_set = set(t_new.virtual_bases)
    # Bases that moved from non-virtual to virtual or vice versa
    became_virtual = (new_virt_set - old_virt_set) & old_bases_set
    lost_virtual = (old_virt_set - new_virt_set) & new_bases_set
    if became_virtual or lost_virtual:
        desc_parts = []
        if became_virtual:
            desc_parts.append(f"became virtual: {sorted(became_virtual)}")
        if lost_virtual:
            desc_parts.append(f"lost virtual: {sorted(lost_virtual)}")
        changes.append(Change(
            kind=ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,
            symbol=name,
            description=f"Base class virtual inheritance changed: {name} — {'; '.join(desc_parts)}",
            old_value=str(sorted(t_old.virtual_bases)),
            new_value=str(sorted(t_new.virtual_bases)),
        ))
    elif old_virt_set != new_virt_set:
        # Pure add/remove of a virtual base (not a migration from non-virtual):
        # e.g. class D : virtual A  →  class D : virtual A, virtual B
        # → TYPE_BASE_CHANGED (hierarchy changed, not just virtuality toggled)
        if not changes:  # don't duplicate if TYPE_BASE_CHANGED already emitted above
            changes.append(Change(
                kind=ChangeKind.TYPE_BASE_CHANGED,
                symbol=name,
                description=f"Virtual base classes changed: {name}",
                old_value=str(t_old.virtual_bases),
                new_value=str(t_new.virtual_bases),
            ))

    return changes


def _diff_type_vtable(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    if t_old.vtable == t_new.vtable:
        return []
    description = (
        f"vtable reordered: {name}"
        if Counter(t_old.vtable) == Counter(t_new.vtable)
        else f"vtable changed: {name}"
    )
    return [Change(
        kind=ChangeKind.TYPE_VTABLE_CHANGED,
        symbol=name,
        description=description,
        old_value=", ".join(t_old.vtable),
        new_value=", ".join(t_new.vtable),
    )]


@registry.detector("enums")
def _diff_enums(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map: dict[str, EnumType] = {
        e.name: e for e in old.enums if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    }
    new_map: dict[str, EnumType] = {
        e.name: e for e in new.enums if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    }
    # A wholly-removed enum is stored in snap.enums, NOT snap.types, so the
    # TYPE_REMOVED loop in _diff_types() never sees it — the old "TYPE_REMOVED
    # covers this" comment was wrong and the removal went silently undetected
    # (case78). Emit it here, guarded by the same stripped-binary suppression
    # _diff_types uses so a stripped new side can't manufacture phantom removals.
    suppress_removed = _removals_are_unconfirmed(old, new)

    for name, e_old in old_map.items():
        if name not in new_map:
            if not suppress_removed:
                changes.append(Change(
                    kind=ChangeKind.TYPE_REMOVED,
                    symbol=name,
                    description=f"Enum removed: {name}",
                ))
            continue
        e_new = new_map[name]
        old_members = {m.name: m.value for m in e_old.members}
        new_members = {m.name: m.value for m in e_new.members}
        # Sentinel detection is name-pattern based to avoid accidental
        # downgrades of ordinary enum members with the maximum numeric value.
        # Recognized patterns: *_last, *_max, *_count (case-insensitive).
        _SENTINEL_SUFFIXES = ("_last", "_max", "_count")

        def _is_sentinel_member(member_name: str) -> bool:
            n = member_name.lower()
            return n.endswith(_SENTINEL_SUFFIXES) or n in {"last", "max", "count"}

        # Build inverse map: new_value → new_name for values that are "new" (not in old names).
        # Only keep entries where the value maps to exactly one new name —
        # aliases (multiple names with same value) must not suppress true removals.
        _new_val_candidates: dict[int, list[str]] = {}
        for nname, nval in new_members.items():
            if nname not in old_members:
                _new_val_candidates.setdefault(nval, []).append(nname)
        new_val_to_newname = {
            nval: nnames[0] for nval, nnames in _new_val_candidates.items()
            if len(nnames) == 1
        }

        for mname, mval in old_members.items():
            if mname not in new_members:
                # rename-only -> skip removed emission
                if mval in new_val_to_newname:
                    continue
                # Value truly removed
                changes.append(Change(
                    kind=ChangeKind.ENUM_MEMBER_REMOVED,
                    symbol=f"{name}::{mname}",
                    description=f"Enum member removed: {name}::{mname}",
                    old_value=str(mval),
                ))
            elif new_members[mname] != mval:
                kind = (
                    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED
                    if _is_sentinel_member(mname)
                    else ChangeKind.ENUM_MEMBER_VALUE_CHANGED
                )
                changes.append(Change(
                    kind=kind,
                    symbol=f"{name}::{mname}",
                    description=f"Enum member value changed: {name}::{mname}",
                    old_value=str(mval),
                    new_value=str(new_members[mname]),
                ))

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
            str(v) for v, names in _removed_val_candidates.items()
            if len(names) == 1
        }
        for mname, mval in new_members.items():
            if mname not in old_members:
                if str(mval) in removed_old_values:
                    continue  # same value as a removed old member — rename candidate
                changes.append(Change(
                    kind=ChangeKind.ENUM_MEMBER_ADDED,
                    symbol=f"{name}::{mname}",
                    description=f"Enum member added: {name}::{mname}",
                    new_value=str(mval),
                ))

    return changes


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
            changes.append(Change(
                kind=ChangeKind.FUNC_STATIC_CHANGED,
                symbol=mangled,
                description=f"Static qualifier changed: {f_old.name}",
                old_value=str(f_old.is_static),
                new_value=str(f_new.is_static),
            ))

        if not f_old.is_pure_virtual and f_new.is_pure_virtual:
            kind = (
                ChangeKind.FUNC_VIRTUAL_BECAME_PURE
                if f_old.is_virtual
                else ChangeKind.FUNC_PURE_VIRTUAL_ADDED
            )
            changes.append(Change(
                kind=kind,
                symbol=mangled,
                description=f"Function became pure virtual: {f_old.name}",
            ))

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
            changes.append(Change(
                kind=ChangeKind.FUNC_STATIC_CHANGED,
                symbol=f_old.mangled,
                description=f"Static qualifier changed: {f_old.name}",
                old_value=str(f_old.is_static),
                new_value=str(f_new.is_static),
            ))
        if f_old.is_const != f_new.is_const or f_old.is_volatile != f_new.is_volatile:
            changes.append(Change(
                kind=ChangeKind.FUNC_CV_CHANGED,
                symbol=f_old.mangled,
                description=f"CV qualifier changed: {f_old.name}",
                old_value=f"const={f_old.is_const} volatile={f_old.is_volatile}",
                new_value=f"const={f_new.is_const} volatile={f_new.is_volatile}",
            ))

        # Ref-qualifier change (&/&&) — also changes mangled name
        old_rq = f_old.ref_qualifier or ""
        new_rq = f_new.ref_qualifier or ""
        if old_rq != new_rq:
            changes.append(Change(
                kind=ChangeKind.FUNC_REF_QUAL_CHANGED,
                symbol=f_old.mangled,
                description=f"Ref-qualifier changed: {f_old.name} ({old_rq!r} → {new_rq!r})",
                old_value=old_rq or "(none)",
                new_value=new_rq or "(none)",
            ))

    return changes


@registry.detector("unions")
def _diff_unions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_unions = {t.name: t for t in old.types if t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_unions = {t.name: t for t in new.types if t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}

    for name, t_old in old_unions.items():
        if name not in new_unions:
            continue  # covered by TYPE_REMOVED
        t_new = new_unions[name]
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        for fname, f_old in old_fields.items():
            if fname not in new_fields:
                changes.append(Change(
                    kind=ChangeKind.UNION_FIELD_REMOVED,
                    symbol=name,
                    description=f"Union field removed: {name}::{fname}",
                    old_value=f_old.type,
                ))
            elif (
                canonicalize_type_name(f_old.type) != canonicalize_type_name(new_fields[fname].type)
                and not cv_qualifiers_only_differ(f_old.type, new_fields[fname].type)
            ):
                changes.append(Change(
                    kind=ChangeKind.UNION_FIELD_TYPE_CHANGED,
                    symbol=name,
                    description=f"Union field type changed: {name}::{fname}",
                    old_value=f_old.type,
                    new_value=new_fields[fname].type,
                ))

        for fname, f_new in new_fields.items():
            if fname not in old_fields:
                changes.append(Change(
                    kind=ChangeKind.UNION_FIELD_ADDED,
                    symbol=name,
                    description=f"Union field added: {name}::{fname}",
                    new_value=f_new.type,
                ))

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
    for alias, old_type in old.typedefs.items():
        if _is_non_abi_surface_type(alias, exclude_stdlib_namespaces=excl):
            continue
        new_type = new.typedefs.get(alias)
        if new_type is None and suppress_removed:
            continue
        if new_type is None:
            # Version-stamped typedefs (e.g. png_libpng_version_1_6_46) are
            # compile-time sentinels — their name encodes the version and
            # changes every release intentionally.  They are never exported as
            # ELF symbols, so their removal is NOT a binary ABI break.
            # Require a same-family successor in new_typedefs to avoid hiding
            # genuine TYPEDEF_REMOVED breaks for names that merely match the
            # version-stamp pattern.
            if _is_version_stamped_typedef(alias) and _has_version_family_successor(alias, new.typedefs):
                changes.append(Change(
                    kind=ChangeKind.TYPEDEF_VERSION_SENTINEL,
                    symbol=alias,
                    description=(
                        f"Version-stamped typedef removed (compile-time sentinel, "
                        f"not an ABI break): {alias}"
                    ),
                    old_value=old_type,
                ))
                continue
            # Typedef removed — breaking for consumers that used the alias
            changes.append(Change(
                kind=ChangeKind.TYPEDEF_REMOVED,
                symbol=alias,
                description=f"Typedef removed: {alias}",
                old_value=old_type,
            ))
        elif new_type != old_type:
            changes.append(Change(
                kind=ChangeKind.TYPEDEF_BASE_CHANGED,
                symbol=alias,
                description=f"Typedef base type changed: {alias}",
                old_value=old_type,
                new_value=new_type,
            ))
    return changes



# ── Sprint 7: enum rename, field qualifier, pointer level, access, param default ─


@registry.detector("enum_renames")
def _diff_enum_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect enum member renames: same value present under different name."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map: dict[str, EnumType] = {
        e.name: e for e in old.enums if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    }
    new_map: dict[str, EnumType] = {
        e.name: e for e in new.enums if not _is_non_abi_surface_type(e.name, exclude_stdlib_namespaces=excl)
    }

    for name, e_old in old_map.items():
        if name not in new_map:
            continue
        e_new = new_map[name]
        old_by_name = {m.name: m.value for m in e_old.members}
        new_by_name = {m.name: m.value for m in e_new.members}
        # One-to-one guard: only treat as rename when the value maps to
        # exactly one new name (aliases must not collapse into renames).
        _new_val_groups: dict[int, list[str]] = {}
        for m in e_new.members:
            if m.name not in old_by_name:
                _new_val_groups.setdefault(m.value, []).append(m.name)
        new_by_val: dict[int, str] = {
            v: names[0] for v, names in _new_val_groups.items()
            if len(names) == 1
        }

        for old_mname, old_mval in old_by_name.items():
            if old_mname in new_by_name:
                continue  # still present by name
            # Name gone — check if the value still exists under exactly one new name
            if old_mval in new_by_val:
                new_mname = new_by_val[old_mval]
                if new_mname not in old_by_name:
                    changes.append(Change(
                        kind=ChangeKind.ENUM_MEMBER_RENAMED,
                        symbol=name,
                        description=f"Enum member renamed: {name}::{old_mname} → {new_mname} (value={old_mval})",
                        old_value=old_mname,
                        new_value=new_mname,
                    ))

    return changes


def _check_field_qualifier_pair(
    name: str, fname: str, f_old: TypeField, f_new: TypeField,
) -> list[Change]:
    """Check const/volatile/mutable qualifier changes for a single field pair."""
    changes: list[Change] = []

    if not f_old.is_const and f_new.is_const:
        changes.append(Change(
            kind=ChangeKind.FIELD_BECAME_CONST,
            symbol=name,
            description=f"Field became const: {name}::{fname}",
            old_value="non-const",
            new_value="const",
        ))
    elif f_old.is_const and not f_new.is_const:
        changes.append(Change(
            kind=ChangeKind.FIELD_LOST_CONST,
            symbol=name,
            description=f"Field lost const: {name}::{fname}",
            old_value="const",
            new_value="non-const",
        ))

    if not f_old.is_volatile and f_new.is_volatile:
        changes.append(Change(
            kind=ChangeKind.FIELD_BECAME_VOLATILE,
            symbol=name,
            description=f"Field became volatile: {name}::{fname}",
            old_value="non-volatile",
            new_value="volatile",
        ))
    elif f_old.is_volatile and not f_new.is_volatile:
        changes.append(Change(
            kind=ChangeKind.FIELD_LOST_VOLATILE,
            symbol=name,
            description=f"Field lost volatile: {name}::{fname}",
            old_value="volatile",
            new_value="non-volatile",
        ))

    if not f_old.is_mutable and f_new.is_mutable:
        changes.append(Change(
            kind=ChangeKind.FIELD_BECAME_MUTABLE,
            symbol=name,
            description=f"Field became mutable: {name}::{fname}",
            old_value="non-mutable",
            new_value="mutable",
        ))
    elif f_old.is_mutable and not f_new.is_mutable:
        changes.append(Change(
            kind=ChangeKind.FIELD_LOST_MUTABLE,
            symbol=name,
            description=f"Field lost mutable: {name}::{fname}",
            old_value="mutable",
            new_value="non-mutable",
        ))

    return changes


@registry.detector("field_qualifiers")
def _diff_field_qualifiers(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect field-level const/volatile/mutable qualifier changes."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map = {t.name: t for t in old.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            continue
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        for fname, f_old in old_fields.items():
            f_new = new_fields.get(fname)
            if f_new is None:
                continue
            changes.extend(_check_field_qualifier_pair(name, fname, f_old, f_new))

    return changes


@registry.detector("field_renames")
def _diff_field_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect field renames: same offset+type, different name."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map = {t.name: t for t in old.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None or t_new.is_opaque:
            continue
        old_names = {f.name for f in t_old.fields}
        new_names = {f.name for f in t_new.fields}

        removed = [f for f in t_old.fields if f.name not in new_names]
        added = [f for f in t_new.fields if f.name not in old_names]

        # Match by (offset, type) — a rename is when the same slot has a different name
        added_by_sig = {(f.offset_bits, f.type): f for f in added if f.offset_bits is not None}
        for f_old in removed:
            if f_old.offset_bits is None:
                continue
            # Skip reserved→real transitions — handled by _diff_reserved_fields
            # as USED_RESERVED_FIELD (compatible), not FIELD_RENAMED (API break).
            if _RESERVED_FIELD_RE.match(f_old.name):
                continue
            sig = (f_old.offset_bits, f_old.type)
            f_new = added_by_sig.get(sig)
            if f_new is not None:
                changes.append(Change(
                    kind=ChangeKind.FIELD_RENAMED,
                    symbol=name,
                    description=f"Field renamed: {name}::{f_old.name} → {f_new.name}",
                    old_value=f_old.name,
                    new_value=f_new.name,
                ))

    return changes


# ── ABICC full parity detectors ───────────────────────────────────────────────


@registry.detector("var_values")
def _diff_var_values(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data value changes (ABICC: Global_Data_Value_Changed).

    When a global const variable's initial value changes, old binaries may
    use stale compile-time-inlined values (constant propagation).
    """
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if (
            v_old.value is not None
            and v_new.value is not None
            and v_old.value != v_new.value
        ):
            changes.append(Change(
                kind=ChangeKind.VAR_VALUE_CHANGED,
                symbol=mangled,
                description=f"Global data value changed: {v_old.name} ({v_old.value!r} → {v_new.value!r})",
                old_value=v_old.value,
                new_value=v_new.value,
            ))
    return changes


@registry.detector("type_kind_changes")
def _diff_type_kind_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect struct↔union kind changes (ABICC: StructToUnion / DataType_Type)."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map = {t.name: t for t in old.types if _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if _is_abi_surface_type(t, exclude_stdlib=excl)}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            continue
        if t_old.kind != t_new.kind:
            # Union-involving transitions are binary-breaking (layout changes);
            # struct↔class transitions are source-level only (identical ABI).
            union_involved = t_old.kind == "union" or t_new.kind == "union"
            ck = ChangeKind.TYPE_KIND_CHANGED if union_involved else ChangeKind.SOURCE_LEVEL_KIND_CHANGED
            changes.append(Change(
                kind=ck,
                symbol=name,
                description=f"Aggregate kind changed: {name} ({t_old.kind} → {t_new.kind})",
                old_value=t_old.kind,
                new_value=t_new.kind,
            ))
    return changes


@registry.detector("reserved_fields")
def _diff_reserved_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect reserved fields put into use (ABICC: Used_Reserved_Field).

    NOTE: Primary detection is now integrated into _diff_type_fields() which
    suppresses TYPE_FIELD_REMOVED + TYPE_FIELD_ADDED for reserved-field renames.
    This standalone detector is kept for backward compatibility but now requires
    both offset AND type match to avoid false positives (M5 fix).
    """
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    old_map = {t.name: t for t in old.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}
    new_map = {t.name: t for t in new.types if not t.is_union and _is_abi_surface_type(t, exclude_stdlib=excl)}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None or t_new.is_opaque:
            continue

        old_names = {f.name for f in t_old.fields}
        new_names = {f.name for f in t_new.fields}

        removed = [f for f in t_old.fields if f.name not in new_names and _RESERVED_FIELD_RE.match(f.name)]
        added = [f for f in t_new.fields if f.name not in old_names and not _RESERVED_FIELD_RE.match(f.name)]

        added_by_offset = {f.offset_bits: f for f in added if f.offset_bits is not None}
        # Fallback index by type for DWARF-only mode (no offsets)
        added_by_type: dict[str, list[TypeField]] = {}
        for f in added:
            added_by_type.setdefault(f.type, []).append(f)
        matched: set[str] = set()
        for f_old in removed:
            candidate = None
            # Primary: match by offset + type
            if f_old.offset_bits is not None:
                c = added_by_offset.get(f_old.offset_bits)
                if c is not None and f_old.type == c.type:
                    candidate = c
            # Fallback: match by type when offsets unavailable (DWARF-only)
            if candidate is None and f_old.offset_bits is None:
                for c in added_by_type.get(f_old.type, []):
                    if c.name not in matched:
                        candidate = c
                        break
            if candidate is not None:
                matched.add(candidate.name)
                changes.append(Change(
                    kind=ChangeKind.USED_RESERVED_FIELD,
                    symbol=name,
                    description=f"Reserved field put into use: {name}::{f_old.name} → {candidate.name}",
                    old_value=f_old.name,
                    new_value=candidate.name,
                ))
    return changes


@registry.detector("const_overloads")
def _diff_const_overloads(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect removed const method overloads (ABICC: Removed_Const_Overload).

    A const overload removal occurs when both const and non-const versions
    existed in old, but only the non-const version remains in new.
    """
    changes: list[Change] = []
    old_funcs = [f for f in old.functions if f.visibility in _PUBLIC_VIS]
    new_funcs = [f for f in new.functions if f.visibility in _PUBLIC_VIS]

    # Group by (name, param_signature) to find const/non-const pairs
    from collections import defaultdict

    _ParamSig = tuple[str, int, str]  # (type, pointer_depth, kind)
    _GroupKey = tuple[str, tuple[_ParamSig, ...]]

    def _group_key(f: Function) -> _GroupKey:
        return (f.name, tuple((p.type, p.pointer_depth, p.kind.value) for p in f.params))

    old_groups: dict[_GroupKey, list[Function]] = defaultdict(list)
    new_groups: dict[_GroupKey, list[Function]] = defaultdict(list)
    for f in old_funcs:
        old_groups[_group_key(f)].append(f)
    for f in new_funcs:
        new_groups[_group_key(f)].append(f)

    for key, old_fns in old_groups.items():
        old_const = [f for f in old_fns if f.is_const]
        old_nonconst = [f for f in old_fns if not f.is_const]
        if not old_const or not old_nonconst:
            continue  # no const overload pair in old

        new_fns = new_groups.get(key, [])
        new_const = [f for f in new_fns if f.is_const]
        new_nonconst = [f for f in new_fns if not f.is_const]
        if not new_const and new_nonconst:
            # Const overload removed, non-const kept
            f_removed = old_const[0]
            changes.append(Change(
                kind=ChangeKind.REMOVED_CONST_OVERLOAD,
                symbol=f_removed.mangled,
                description=f"Const method overload removed: {f_removed.name} (non-const version still exists)",
                old_value="const overload present",
                new_value="const overload removed",
            ))
    return changes
