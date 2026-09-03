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

"""Field/type/enum-level fact detectors: enum member renames, field
const/volatile/mutable qualifiers, field default member initializers, the
four ``[[deprecated]]`` surfaces (field/type/enum — function/variable live
in ``diff_symbols.py``), and field renames (Sprint 7's original grouping).

Split out of ``diff_types.py`` to stay under its line-count cap — a genuine
leaf module (must not import from ``diff_types`` at all, to avoid an import
cycle: ``diff_types.py`` imports these detectors back for registration).
``_is_abi_surface_type``/``_directly_referenced``/``_RESERVED_FIELD_RE``
were originally private to ``diff_types.py``; an earlier version of this
split imported them back function-locally, which the AI-readiness
import-cycle-growth check correctly flagged as a real cycle in the static
import graph even though it never deadlocks at runtime — fixed by promoting
them to their own leaf module, ``diff_types_surface.py``, that both
``diff_types.py`` and this module import at the ordinary top level.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import (
    build_type_map as _build_type_map,
    fact_known_qualified,
    fact_same_producer_qualified,
    lookup_matched_type as _lookup_matched_type,
    make_change,
    type_map_key,
)
from .diff_symbols import _both_header_aware
from .diff_types_surface import (
    _RESERVED_FIELD_RE,
    _directly_referenced,
    _is_abi_surface_type,
)
from .fact_provenance import enum_fact_key, field_fact_key, type_fact_key
from .model import (
    AbiSnapshot,
    TypeField,
    is_non_abi_surface_type as _is_non_abi_surface_type,
    stdlib_namespaces_excluded as _exclude_stdlib_namespaces,
)
from .model.identity import EntityId


@registry.detector("enum_renames")
def _diff_enum_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect enum member renames: same value present under different name."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
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

    for e_old in old_map.values():
        name = e_old.name
        e_new = _lookup_matched_type(old_map, new_map, e_old)
        if e_new is None:
            continue
        old_by_name = {m.name: m.value for m in e_old.members}
        new_by_name = {m.name: m.value for m in e_new.members}
        # One-to-one guard: only treat as rename when the value maps to
        # exactly one new name (aliases must not collapse into renames).
        _new_val_groups: dict[int, list[str]] = {}
        for m in e_new.members:
            if m.name not in old_by_name:
                _new_val_groups.setdefault(m.value, []).append(m.name)
        new_by_val: dict[int, str] = {
            v: names[0] for v, names in _new_val_groups.items() if len(names) == 1
        }

        for old_mname, old_mval in old_by_name.items():
            if old_mname in new_by_name:
                continue  # still present by name
            # Name gone — check if the value still exists under exactly one new name
            if old_mval in new_by_val:
                new_mname = new_by_val[old_mval]
                if new_mname not in old_by_name:
                    changes.append(
                        make_change(
                            ChangeKind.ENUM_MEMBER_RENAMED,
                            symbol=name,
                            name=name,
                            detail=str(old_mval),
                            old=old_mname,
                            new=new_mname,
                            entity_id=e_old.entity_id or e_new.entity_id,
                        )
                    )

    return changes


def _check_field_qualifier_pair(
    name: str,
    fname: str,
    f_old: TypeField,
    f_new: TypeField,
    *,
    entity_id: EntityId | None = None,
) -> list[Change]:
    """Check const/volatile/mutable qualifier changes for a single field pair.

    *entity_id* is the containing record's own identity, not the field's --
    a field carries no EntityId of its own (EntityKind.FIELD is declared but
    unimplemented), so every field-level finding here is stamped with the
    record's identity instead, threaded down from the caller.
    """
    changes: list[Change] = []

    if not f_old.is_const and f_new.is_const:
        changes.append(
            make_change(
                ChangeKind.FIELD_BECAME_CONST,
                symbol=name,
                name=name,
                detail=fname,
                old_value="non-const",
                new_value="const",
                entity_id=entity_id,
            )
        )
    elif f_old.is_const and not f_new.is_const:
        changes.append(
            make_change(
                ChangeKind.FIELD_LOST_CONST,
                symbol=name,
                name=name,
                detail=fname,
                old_value="const",
                new_value="non-const",
                entity_id=entity_id,
            )
        )

    if not f_old.is_volatile and f_new.is_volatile:
        changes.append(
            make_change(
                ChangeKind.FIELD_BECAME_VOLATILE,
                symbol=name,
                name=name,
                detail=fname,
                old_value="non-volatile",
                new_value="volatile",
                entity_id=entity_id,
            )
        )
    elif f_old.is_volatile and not f_new.is_volatile:
        changes.append(
            make_change(
                ChangeKind.FIELD_LOST_VOLATILE,
                symbol=name,
                name=name,
                detail=fname,
                old_value="volatile",
                new_value="non-volatile",
                entity_id=entity_id,
            )
        )

    if not f_old.is_mutable and f_new.is_mutable:
        changes.append(
            make_change(
                ChangeKind.FIELD_BECAME_MUTABLE,
                symbol=name,
                name=name,
                detail=fname,
                old_value="non-mutable",
                new_value="mutable",
                entity_id=entity_id,
            )
        )
    elif f_old.is_mutable and not f_new.is_mutable:
        changes.append(
            make_change(
                ChangeKind.FIELD_LOST_MUTABLE,
                symbol=name,
                name=name,
                detail=fname,
                old_value="mutable",
                new_value="non-mutable",
                entity_id=entity_id,
            )
        )

    return changes


@registry.detector("field_qualifiers")
def _diff_field_qualifiers(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect field-level const/volatile/mutable qualifier changes.

    Gated on ``header_cv_facts_reliable`` on both sides: a snapshot
    persisted before the CastXML field-CV-fact fix has
    ``is_const``/``is_volatile``/``is_mutable`` permanently False — real
    (not absent) data that reads identically to a genuine "not const"
    fact. Comparing such a snapshot against a fresh dump of unchanged
    headers would otherwise misreport a false ``FIELD_BECAME_CONST``/
    ``VOLATILE``/``MUTABLE`` purely from the tool upgrade (Codex review,
    PR #582).
    """
    if not (old.header_cv_facts_reliable and new.header_cv_facts_reliable):
        return []
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_map = _build_type_map(
        t
        for t in old.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_map = _build_type_map(
        t
        for t in new.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        entity_id = t_old.entity_id or t_new.entity_id
        for fname, f_old in old_fields.items():
            f_new = new_fields.get(fname)
            if f_new is None:
                continue
            changes.extend(
                _check_field_qualifier_pair(
                    name, fname, f_old, f_new, entity_id=entity_id
                )
            )

    return changes


@registry.detector("field_default_initializer")
def _diff_field_default_initializer(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a field's default member initializer being removed or changed.

    Header-tier only (like ``param_defaults``): ``TypeField.default`` is
    ``None`` both for "no initializer" and "not captured" — see its
    docstring in model.py. Gaining an initializer is not tracked (matches
    ``PARAM_DEFAULT_VALUE_*``'s convention).

    Gates per-field on :func:`diff_helpers.fact_same_producer_qualified`, in
    addition to ``_both_header_aware`` up front (the producer check alone
    would treat an unconfirmed/legacy side's unset ``ast_producer`` as
    "unknown, don't skip"). Used to be ``both_castxml_backed_fact`` because
    clang did not populate this fact at all (Codex review, PR #582); G31
    Phase C closed that gap (``dumper_clang_expr._field_initializer_value``), but
    the two backends' VALUE representations still differ (castxml: verbatim
    source expression; clang: a literal/structural fingerprint) — the same
    shape ``Param.default`` has, hence ``_diff_param_defaults``'s
    SAME-producer gate rather than ``both_known_backed_fact``'s
    any-known-producer check (only correct for a cross-comparable fact like
    ``deprecated``). The provenance key is namespace-qualified with the
    ``deprecated``/``is_scoped`` call sites' bare-key fallback, since the
    hybrid merge now stamps a ``default`` producer for clang-only fields.

    Unions are NOT excluded (unlike most field-level detectors, which leave
    them to ``_diff_unions``): a union variant may carry a default member
    initializer, and ``_diff_unions`` never checks ``default``.

    KNOWN, deliberately-deferred gap (Codex review, fresh evidence, three
    rounds): a real ``FIELD_DEFAULT_INITIALIZER_REMOVED`` against a hybrid
    NEW snapshot where the two matched sides' per-field producers are
    positively known but DIFFER is currently declined by the same-producer
    gate above, even when the removal is genuine. The tempting relaxation
    — trust a hybrid merge's ``"castxml"`` provenance stamp on a ``None``
    value as "both backends independently confirmed absence" — is UNSOUND:
    ``fact_provenance.backfill_fact``'s own docstring already discloses that
    this stamp means only "the final value is castxml-sourced," and
    confirmed empirically that it fires identically whether clang genuinely
    examined the field and agreed, OR never matched the field/type at all
    (a clang-side parse gap). No signal in the current
    ``AbiSnapshot``/``fact_provenance`` data model distinguishes the two
    cases from outside ``dumper_hybrid.py``'s own merge closure. A real fix
    needs a new, more precise provenance value recorded AT MERGE TIME (e.g.
    distinguishing "both backends matched and both said None" from "only
    castxml matched"), not a read-side heuristic in this detector — three
    increasingly narrow attempts at the latter were each caught by a
    fresh Codex review round finding the next counter-example, which is
    itself the signal that this needs a data-model change, not another
    heuristic.
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
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

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        for fname, f_old in old_fields.items():
            f_new = new_fields.get(fname)
            if f_new is None or f_old.default is None:
                continue
            fact_key_args = (
                old,
                new,
                old_map,
                new_map,
                name,
                field_fact_key(type_map_key(t_old), fname, "default"),
                field_fact_key(type_map_key(t_new), fname, "default"),
                field_fact_key(name, fname, "default"),
            )
            if f_new.default is None:
                # KNOWN, deliberately-deferred relaxation (Codex review,
                # fresh evidence, third round): a REMOVED comparison carries
                # no representation-mismatch risk the way CHANGED below does
                # -- None means "confirmed absent" regardless of which
                # backend confirmed it, in principle. Two earlier attempts
                # at exploiting this tried to trust a hybrid merge's
                # "castxml" provenance stamp on the NEW side as dual-backend
                # confirmation (fact_provenance.backfill_fact's docstring:
                # "Provenance is recorded as castxml even when own_value is
                # None and no clang value was available to backfill from").
                # That docstring line is exactly the trap: it explicitly
                # documents that "castxml" here means ONLY "the final value
                # is castxml-sourced," not "clang also examined and agreed"
                # -- confirmed empirically that _merge_field's own
                # `clang_fields_by_name.get(f.name)` can be `None` (clang's
                # dump never matched this field/type AT ALL, e.g. a
                # clang-side parse gap) and still produces the identical
                # "castxml" stamp as a genuine dual-examined absence. No
                # signal in the current AbiSnapshot/fact_provenance data
                # model distinguishes the two cases from outside
                # dumper_hybrid.py's merge closure. A real fix needs a new,
                # more precise provenance value recorded AT MERGE TIME (not
                # a narrow read-side heuristic in this detector) -- see the
                # deferred hybrid-dual-confirmation note in this function's
                # own docstring. Until then this stays the plain
                # same-producer gate CHANGED uses below.
                if not fact_same_producer_qualified(*fact_key_args):
                    continue
                changes.append(
                    make_change(
                        ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old_value=f_old.default,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )
            elif f_old.default != f_new.default:
                # CHANGED compares two REAL values, so the representation-
                # mismatch risk fact_same_producer_qualified guards against
                # (castxml's verbatim source text vs. clang's fingerprint)
                # is live here -- same producer, not merely known, required.
                if not fact_same_producer_qualified(*fact_key_args):
                    continue
                changes.append(
                    make_change(
                        ChangeKind.FIELD_DEFAULT_INITIALIZER_CHANGED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old=f_old.default,
                        new=f_new.default,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

    return changes


@registry.detector("field_deprecated")
def _diff_field_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a struct/class field gaining or losing `[[deprecated]]`.

    ``TypeField.deprecated`` is populated and serialized (castxml's ``Field``
    element carries the same ``deprecation``/bare-``attributes`` marker as
    a function/variable/type/enum declaration), but — unlike those four
    other surfaces, each of which already has its own dedicated detector —
    nothing previously read it: a public field changing from `int x;` to
    `[[deprecated]] int x;` (or losing the marker) went completely
    undetected on a CastXML-backed header comparison (Codex review, PR
    #582).

    Header-tier only, gated per-field on
    :func:`fact_provenance.both_known_backed_fact_qualified` like the other four
    deprecated detectors (both backends populate ``TypeField.deprecated``
    today, G31 Phase C, with directly cross-comparable values; per-field
    gating is what correctly supports a ``--ast-frontend hybrid`` snapshot,
    G28 Phase 3). Unions are NOT excluded — matching
    ``FIELD_DEFAULT_INITIALIZER_REMOVED``/``_CHANGED``'s reasoning: a union
    variant can carry `[[deprecated]]` too, and ``_diff_unions`` never
    checks ``deprecated`` at all.
    """
    changes: list[Change] = []
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

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}

        for fname, f_old in old_fields.items():
            f_new = new_fields.get(fname)
            if f_new is None:
                continue
            if not fact_known_qualified(
                old,
                new,
                old_map,
                new_map,
                name,
                field_fact_key(type_map_key(t_old), fname, "deprecated"),
                field_fact_key(type_map_key(t_new), fname, "deprecated"),
                field_fact_key(name, fname, "deprecated"),
            ):
                continue
            if f_old.deprecated is None and f_new.deprecated is not None:
                changes.append(
                    make_change(
                        ChangeKind.FIELD_DEPRECATED_ADDED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        new=f_new.deprecated,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )
            elif f_old.deprecated is not None and f_new.deprecated is None:
                changes.append(
                    make_change(
                        ChangeKind.FIELD_DEPRECATED_REMOVED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old_value=f_old.deprecated,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

    return changes


@registry.detector("type_deprecated")
def _diff_type_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a class/struct/union gaining or losing `[[deprecated]]`.

    Header-tier only — see ``FIELD_DEFAULT_INITIALIZER_REMOVED``'s docstring
    above / ``Function.deprecated``'s in model.py for why this gates
    per-type on :func:`fact_provenance.both_known_backed_fact_qualified` rather than a
    per-pair None check or plain ``_both_header_aware`` (both backends
    populate ``RecordType.deprecated`` today, G31 Phase C, with directly
    cross-comparable values; per-type gating is what correctly supports a
    ``--ast-frontend hybrid`` snapshot, G28 Phase 3).
    """
    changes: list[Change] = []
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

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare for Change.symbol; fact_known_qualified handles the key below.
        name = t_old.name
        if not fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            name,
            type_fact_key(type_map_key(t_old), "deprecated"),
            type_fact_key(type_map_key(t_new), "deprecated"),
            type_fact_key(name, "deprecated"),
        ):
            continue
        if t_old.deprecated is None and t_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.TYPE_DEPRECATED_ADDED,
                    symbol=name,
                    name=name,
                    detail=t_new.deprecated,
                    new_value=t_new.deprecated,
                    entity_id=t_old.entity_id or t_new.entity_id,
                )
            )
        elif t_old.deprecated is not None and t_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.TYPE_DEPRECATED_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=t_old.deprecated,
                    entity_id=t_old.entity_id or t_new.entity_id,
                )
            )

    return changes


@registry.detector("enum_deprecated")
def _diff_enum_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect an enum gaining or losing `[[deprecated]]` (header-tier only).

    Gates per-enum on :func:`fact_provenance.both_known_backed_fact_qualified` — see
    ``TYPE_DEPRECATED_ADDED``'s docstring above (both backends populate
    ``EnumType.deprecated`` today, G31 Phase C, with directly cross-
    comparable values; per-enum gating supports ``--ast-frontend hybrid``).
    """
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
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

    for e_old in old_map.values():
        name = e_old.name
        e_new = _lookup_matched_type(old_map, new_map, e_old)
        if e_new is None:
            continue
        if not fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            name,
            enum_fact_key(type_map_key(e_old), "deprecated"),
            enum_fact_key(type_map_key(e_new), "deprecated"),
            enum_fact_key(name, "deprecated"),
        ):
            continue
        if e_old.deprecated is None and e_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.ENUM_DEPRECATED_ADDED,
                    symbol=name,
                    name=name,
                    detail=e_new.deprecated,
                    new_value=e_new.deprecated,
                    entity_id=e_old.entity_id or e_new.entity_id,
                )
            )
        elif e_old.deprecated is not None and e_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.ENUM_DEPRECATED_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=e_old.deprecated,
                    entity_id=e_old.entity_id or e_new.entity_id,
                )
            )

    return changes


@registry.detector("field_renames")
def _diff_field_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect field renames: same offset+type, different name."""
    changes: list[Change] = []
    excl = _exclude_stdlib_namespaces(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_map = _build_type_map(
        t
        for t in old.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_map = _build_type_map(
        t
        for t in new.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None or t_new.is_opaque:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        old_names = {f.name for f in t_old.fields}
        new_names = {f.name for f in t_new.fields}

        removed = [f for f in t_old.fields if f.name not in new_names]
        added = [f for f in t_new.fields if f.name not in old_names]

        # Match by (offset, type, bitfield width) — a rename is when the same
        # slot has a different name. A bit-field's width is a layout property
        # the type spelling alone doesn't capture (two "unsigned int"
        # bit-fields at the same offset can still differ in width), so it
        # must be part of the match key or a real width change gets masked
        # as a bare rename (caught in review). A list per signature, not a
        # single value: distinct added fields can share a signature (e.g.
        # overlapping anonymous-union members collapsing to one field), and
        # a plain single-value dict would let every removed field with that
        # signature claim the same added field as its rename target instead
        # of just the first (caught in review).
        added_by_sig: dict[tuple[int, str, bool, int | None], list[TypeField]] = {}
        for f in added:
            if f.offset_bits is not None:
                sig = (f.offset_bits, f.type, f.is_bitfield, f.bitfield_bits)
                added_by_sig.setdefault(sig, []).append(f)
        renamed_to: set[str] = set()
        for f_old in removed:
            if f_old.offset_bits is None:
                continue
            # Skip reserved→real transitions — handled by _diff_reserved_fields
            # as USED_RESERVED_FIELD (compatible), not FIELD_RENAMED (API break).
            if _RESERVED_FIELD_RE.match(f_old.name):
                continue
            sig = (
                f_old.offset_bits,
                f_old.type,
                f_old.is_bitfield,
                f_old.bitfield_bits,
            )
            f_new = next(
                (c for c in added_by_sig.get(sig, []) if c.name not in renamed_to),
                None,
            )
            if f_new is not None:
                renamed_to.add(f_new.name)
                changes.append(
                    make_change(
                        ChangeKind.FIELD_RENAMED,
                        symbol=name,
                        name=name,
                        old=f_old.name,
                        new=f_new.name,
                        entity_id=t_old.entity_id or t_new.entity_id,
                    )
                )

    return changes
