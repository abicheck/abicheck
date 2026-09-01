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

"""Fine-grained class-layout descriptor diff (layout-closure work).

The coarse type detectors compare ``sizeof`` (``TYPE_SIZE_CHANGED``) and field
offsets, but a class layout has more moving parts that those under-represent:

* a **base-class subobject** can move within the derived object (e.g. an
  empty-base optimization is lost, or a member/base is inserted ahead of it)
  without the *declaration order* of bases changing;
* a previously non-polymorphic class can gain its first virtual function, so the
  compiler **prepends a vtable pointer** — sizeof grows and every member shifts;
* a type can stop being **trivially copyable** (changing how it is passed/returned
  by value — in registers vs. via a hidden reference) or **standard-layout**
  (changing ``offsetof`` / C interop / tail-padding-reuse rules);
* the **data size** (``dsize``: the bytes the object's own members occupy,
  excluding trailing tail padding) can change while ``sizeof`` stays the same — a
  derived class may reuse a base's tail padding, so this silently shifts a
  derived layout even when the base's ``sizeof`` is unchanged.

This detector reads the optional layout fields on :class:`~abicheck.model.RecordType`
(``base_offsets``, ``vptr_offset_bits``, ``data_size_bits``, ``is_standard_layout``,
``is_trivially_copyable``) and emits the corresponding ``ChangeKind``. Every
comparison is **tri-state guarded** — it fires only when *both* sides carry the
relevant evidence — so an evidence-tier downgrade (DWARF-only or symbols-only
dump, or an older snapshot whose schema predates these fields) never fabricates a
finding. On any snapshot that doesn't populate the descriptor (all fields default
to ``None``/empty), the detector is completely inert.

``LAYOUT_UNVERIFIABLE`` is the calm, non-escalating counterpart: when one side
carries a populated layout descriptor but the other has *no* layout evidence at
all (``size_bits is None``), we cannot confirm or rule out a layout change, so we
say so without raising an alarm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import build_type_map, lookup_matched_type, make_change
from .model import resolved_fact_value
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .diff_helpers import TypeMap
    from .model import AbiSnapshot, RecordType


def _index(snap: AbiSnapshot, *, exclude_stdlib: bool) -> TypeMap[RecordType]:
    """Index a snapshot's record types, skipping non-ABI-surface types.

    Standard-library / compiler-internal records (``std::``, ``__gnu_cxx::`` …)
    are toolchain-owned and excluded from public-surface reasoning, mirroring the
    other type detectors. ``exclude_stdlib`` is threaded from
    :func:`abicheck.model.stdlib_namespaces_excluded`: when abicheck compares the
    C++ runtime *itself* (e.g. ``libstdc++.so`` / ``libc++.so``) that toggle is
    False, so the runtime's own ``std::`` records stay in the surface and their
    layout changes are reported (Codex review #345).

    Keyed via :class:`~abicheck.diff_helpers.TypeMap` (namespace-qualified
    identity, not the bare declaration name) rather than a plain
    ``{rec.name: rec}`` dict -- two distinct records sharing only a bare leaf
    name in different namespaces (e.g. ``a::Foo``/``b::Foo``) would otherwise
    silently collide, with the later one winning and the earlier one's
    layout facts never compared at all (Codex review, fresh evidence: this
    surfaced once ``RecordType.is_standard_layout``/``is_trivially_copyable``
    started being populated for real, G31 Phase C, but the same collision
    already existed for ``base_offsets``/``vptr_offset_bits`` too).

    The stdlib check itself needs the QUALIFIED identity, not the bare one
    (Codex review, fresh evidence, second round): castxml/clang keep
    ``rec.name`` bare (e.g. ``"vector"``) and carry the real namespace in
    ``rec.qualified_name`` (e.g. ``"std::vector"``) -- filtering on the bare
    name never matches the ``std::``/``__gnu_cxx::``/etc. prefix, so a
    retained dependency-header stdlib record leaked into this detector's
    surface once ``is_standard_layout``/``is_trivially_copyable`` gave it
    something to fire on. Same ``identity = qualified_name or name`` split
    ``diff_types._is_abi_surface_type`` already uses.
    """
    from .model import is_non_abi_surface_type

    def _keep(rec: RecordType) -> bool:
        identity = rec.qualified_name or rec.name
        if exclude_stdlib and identity.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            return False
        return not is_non_abi_surface_type(rec.name, exclude_stdlib_namespaces=False)

    return build_type_map(rec for rec in snap.types if _keep(rec))


def _has_layout_descriptor(
    rec: RecordType, *, vtable_facts_reliable: bool = True
) -> bool:
    """Return True if any v7 REAL-LAYOUT descriptor field is populated on
    ``rec`` (size/offset evidence only — see below for why
    ``is_standard_layout``/``is_trivially_copyable`` are deliberately
    excluded).

    Used to gate ``LAYOUT_UNVERIFIABLE`` so it only activates once a dump
    actually carries the richer descriptor — keeping the detector inert on
    snapshots that predate it.

    ``is_standard_layout``/``is_trivially_copyable`` do NOT count as layout
    evidence here (Codex review, fresh evidence): since G31 Phase C the
    direct-clang backend populates these two SEMANTIC traits independent of
    any real layout pass (`dumper_clang.py` never sets `size_bits`/
    `data_size_bits`/`base_offsets` without the optional
    `ABICHECK_CLANG_LAYOUT_TOOL` companion), so a persisted pre-v19
    direct-clang snapshot compared against a freshly-dumped one of
    UNCHANGED headers has these two traits appear from `None` to a real
    value on the new side alone — with this function counting them,
    that flip alone made `descriptor_in_play`/`old_has != new_has` true
    and fired a phantom `LAYOUT_UNVERIFIABLE` on every record, purely from
    a tool/schema upgrade. `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST`
    (`_check_standard_layout_lost`/`_check_trivially_copyable_lost`) already
    self-gate correctly on this exact asymmetry (`old_rec.is_X is True`
    requires a real old value, so `None` on the old side stays silent) —
    only this cruder "did any layout evidence appear/disappear" heuristic
    was affected, since it conflated "we now know a semantic trait" with
    "we now know the type's actual size/offsets," two different kinds of
    evidence.

    ``vptr_offset_bits`` IS a real-layout-evidence field in general (unlike
    the two semantic traits above) — but this docstring's own claim that
    `dumper_clang.py` "never sets" it without `ABICHECK_CLANG_LAYOUT_TOOL`
    stopped being true the moment G31 Phase C's clang vtable reconstruction
    landed (`vptr_offset_bits=0 if vtable else None`, unconditional). That
    means the exact same tool-upgrade phantom this docstring already
    documents for the two semantic traits reproduces for `vptr_offset_bits`
    too, on a persisted pre-v21 clang baseline: it goes from `None` (every
    record, unconditionally) to a real `0`/`None` split on a fresh dump of
    UNCHANGED headers, purely from the schema upgrade (Codex review, fresh
    evidence). Guarded the same way `_check_vptr_introduced` is: when
    `vtable_facts_reliable` is False, `vptr_offset_bits` is excluded from
    this evidence check entirely, so a legacy clang baseline's blanket
    `None` doesn't manufacture a phantom "evidence appeared" transition —
    `data_size_bits`/`base_offsets` are untouched, since those still require
    the unrelated `ABICHECK_CLANG_LAYOUT_TOOL` opt-in either way.
    """
    vptr_offset_bits = resolved_fact_value(rec.vptr_offset_bits_fact, None)
    return (
        rec.data_size_bits is not None
        or (vtable_facts_reliable and vptr_offset_bits is not None)
        or bool(rec.base_offsets)
    )


def _check_base_offsets(
    name: str, old_rec: RecordType, new_rec: RecordType
) -> list[Change]:
    """Emit BASE_CLASS_OFFSET_CHANGED for each base whose offset shifted."""
    changes: list[Change] = []
    for base, new_off in new_rec.base_offsets.items():
        old_off = old_rec.base_offsets.get(base)
        if old_off is not None and old_off != new_off:
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_OFFSET_CHANGED,
                    symbol=name,
                    name=name,
                    detail=base,
                    old=str(old_off),
                    new=str(new_off),
                )
            )
    return changes


def _check_vptr_introduced(
    name: str,
    old_rec: RecordType,
    new_rec: RecordType,
    *,
    vtable_facts_reliable: bool = True,
) -> list[Change]:
    """Emit VPTR_INTRODUCED when the type gains its first virtual function.

    Use the long-standing ``vtable`` list (populated by every dump path) as
    the polymorphism witness, NOT ``size_bits``: a pre-layout-descriptor
    snapshot has ``size_bits`` set but ``vptr_offset_bits`` defaulting to
    None even for an already-polymorphic type, so keying on size would
    falsely report an introduction against such a baseline (Codex #345).
    An empty old vtable is positive evidence the old side was
    non-polymorphic; require the new side to be positively polymorphic too.

    ``vtable_facts_reliable=False`` (Codex review, fresh evidence) means
    either side is a persisted, pre-v21 direct-clang snapshot whose vtable
    was unconditionally empty for EVERY record, polymorphic or not -- an old
    side reading "non-polymorphic" there is real but WRONG, not genuine
    positive evidence, so this detector must decline entirely rather than
    fire on what looks like a first-vptr introduction
    (AbiSnapshot.clang_vtable_facts_reliable's own docstring).
    """
    if not vtable_facts_reliable:
        return []
    old_vtable = resolved_fact_value(old_rec.vtable_fact, [])
    new_vtable = resolved_fact_value(new_rec.vtable_fact, [])
    old_vptr_offset_bits = resolved_fact_value(old_rec.vptr_offset_bits_fact, None)
    new_vptr_offset_bits = resolved_fact_value(new_rec.vptr_offset_bits_fact, None)
    if (
        not old_vtable
        and old_vptr_offset_bits is None
        and new_vtable
        and new_vptr_offset_bits is not None
    ):
        return [
            make_change(
                ChangeKind.VPTR_INTRODUCED,
                symbol=name,
                name=name,
                old_value="non-polymorphic",
                new_value=f"vptr@{new_vptr_offset_bits}",
            )
        ]
    return []


def _check_trivially_copyable_lost(
    name: str, old_rec: RecordType, new_rec: RecordType
) -> list[Change]:
    """Emit TRIVIALLY_COPYABLE_LOST when the trait is removed."""
    if old_rec.is_trivially_copyable is True and new_rec.is_trivially_copyable is False:
        return [
            make_change(
                ChangeKind.TRIVIALLY_COPYABLE_LOST,
                symbol=name,
                name=name,
                old_value="trivially_copyable",
                new_value="non_trivially_copyable",
            )
        ]
    return []


def _check_standard_layout_lost(
    name: str, old_rec: RecordType, new_rec: RecordType
) -> list[Change]:
    """Emit STANDARD_LAYOUT_LOST when the trait is removed."""
    if old_rec.is_standard_layout is True and new_rec.is_standard_layout is False:
        return [
            make_change(
                ChangeKind.STANDARD_LAYOUT_LOST,
                symbol=name,
                name=name,
                old_value="standard_layout",
                new_value="non_standard_layout",
            )
        ]
    return []


def _check_tail_padding_reuse(
    name: str, old_rec: RecordType, new_rec: RecordType
) -> list[Change]:
    """Emit TAIL_PADDING_REUSE_CHANGED when dsize changes at stable sizeof."""
    if (
        old_rec.data_size_bits is not None
        and new_rec.data_size_bits is not None
        and old_rec.data_size_bits != new_rec.data_size_bits
        and old_rec.size_bits is not None
        and new_rec.size_bits is not None
        and old_rec.size_bits == new_rec.size_bits
    ):
        return [
            make_change(
                ChangeKind.TAIL_PADDING_REUSE_CHANGED,
                symbol=name,
                name=name,
                old=str(old_rec.data_size_bits),
                new=str(new_rec.data_size_bits),
                detail=str(new_rec.size_bits),
            )
        ]
    return []


def _layout_evidence_asymmetric(
    old_rec: RecordType, new_rec: RecordType, *, vtable_facts_reliable: bool
) -> bool:
    """True when *old_rec*/*new_rec* carry layout evidence on exactly one
    side -- the one asymmetric-evidence condition ``_check_layout_unverifiable``
    below keys off, extracted so ``diff_types_vtable._layout_evidence_is_unverifiable``
    can consult the *identical* predicate for its cross-detector correlation
    instead of a hand-duplicated copy that could silently drift out of sync
    with this function (Codex review).

    ``vtable_facts_reliable`` is threaded straight into
    ``_has_layout_descriptor`` — see that function's own docstring for why a
    persisted, pre-v21 direct-clang baseline's blanket-``None``
    ``vptr_offset_bits`` must not count as "no evidence" turning into
    "evidence appeared" on a fresh dump of unchanged headers.
    """
    old_has = old_rec.size_bits is not None or _has_layout_descriptor(
        old_rec, vtable_facts_reliable=vtable_facts_reliable
    )
    new_has = new_rec.size_bits is not None or _has_layout_descriptor(
        new_rec, vtable_facts_reliable=vtable_facts_reliable
    )
    descriptor_in_play = _has_layout_descriptor(
        old_rec, vtable_facts_reliable=vtable_facts_reliable
    ) or _has_layout_descriptor(new_rec, vtable_facts_reliable=vtable_facts_reliable)
    return descriptor_in_play and old_has != new_has


def _check_layout_unverifiable(
    name: str,
    old_rec: RecordType,
    new_rec: RecordType,
    *,
    vtable_facts_reliable: bool = True,
) -> list[Change]:
    """Emit LAYOUT_UNVERIFIABLE when evidence is present on one side only.

    One side carries a populated layout descriptor, the other has no layout
    evidence at all (size unknown). Calm, non-escalating: we cannot confirm
    or rule out a change. Gated on the descriptor so it never fires on
    snapshots predating the v7 layout fields.
    """
    if _layout_evidence_asymmetric(
        old_rec, new_rec, vtable_facts_reliable=vtable_facts_reliable
    ):
        return [
            make_change(
                ChangeKind.LAYOUT_UNVERIFIABLE,
                symbol=name,
                name=name,
                # Precise type identity (not the bare ``symbol`` two distinct
                # same-named records in different namespaces would share) --
                # lets diff_types.py's TYPE_VTABLE_CHANGED-covers-this-gap
                # cross-reference check
                # (post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged)
                # correlate against the *exact* RecordType this finding is
                # about, rather than a same-named-but-different one.
                qualified_name=new_rec.qualified_name or new_rec.name,
            )
        ]
    return []


@registry.detector("layout_descriptor")
def _diff_layout_descriptor(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Emit fine-grained class-layout findings from the schema-v7 descriptor.

    Each comparison is tri-state guarded (both sides must carry the evidence),
    so the detector is inert on snapshots without the layout descriptor.
    """
    from .model import stdlib_namespaces_excluded

    changes: list[Change] = []
    # Respect the runtime-self-comparison toggle: when comparing libstdc++/libc++
    # to itself this is False, keeping the runtime's own std:: records in surface.
    excl = stdlib_namespaces_excluded(old, new)
    old_idx = _index(old, exclude_stdlib=excl)
    new_idx = _index(new, exclude_stdlib=excl)
    vtable_facts_reliable = (
        old.clang_vtable_facts_reliable and new.clang_vtable_facts_reliable
    )

    # .items() iterates one entry per type under its qualified TypeMap key
    # (never the bare-name alias — see TypeMap's own docstring), so this
    # loop can't double-process a type the way iterating a raw dict with a
    # bare-name alias mixed in would.
    for _key, new_rec in new_idx.items():
        old_rec = lookup_matched_type(new_idx, old_idx, new_rec)
        if old_rec is None:
            continue  # added type — handled by the structural type diff
        # Opaque/forward-declared types carry no real layout; skip them so the
        # incomplete-type detectors own that signal.
        if old_rec.is_opaque or new_rec.is_opaque:
            continue

        # Bare, not the qualified matching key — matches every other type
        # detector's Change.symbol convention (see diff_types.py). This is a
        # known, deliberately-deferred sharp edge: two distinct records that
        # share the same bare name in different namespaces (already matched
        # correctly above via the qualified TypeMap key) still emit findings
        # under the same bare Change.symbol/name, so a downstream consumer
        # keying off those alone (e.g. dedup) can't tell them apart. Fixing
        # it needs a codebase-wide convention change to Change.symbol across
        # diff_types.py/diff_symbols.py/diff_layout.py together, not a
        # drive-by fix here — see the G31 Phase C plan doc for the full
        # investigation and reasoning.
        name = new_rec.name
        changes.extend(_check_base_offsets(name, old_rec, new_rec))
        changes.extend(
            _check_vptr_introduced(
                name, old_rec, new_rec, vtable_facts_reliable=vtable_facts_reliable
            )
        )
        changes.extend(_check_trivially_copyable_lost(name, old_rec, new_rec))
        changes.extend(_check_standard_layout_lost(name, old_rec, new_rec))
        changes.extend(_check_tail_padding_reuse(name, old_rec, new_rec))
        changes.extend(
            _check_layout_unverifiable(
                name, old_rec, new_rec, vtable_facts_reliable=vtable_facts_reliable
            )
        )

    return changes
