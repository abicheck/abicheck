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

"""Which of a snapshot's fact families its own writer could not vouch for.

ADR-063 Phase 0/10 retired the eight ``AbiSnapshot.*_facts_reliable``
booleans. Their one remaining job -- recording, for a document an older
abicheck wrote, which families of persisted values are known placeholders --
is now a single load-time record, ``AbiSnapshot.stale_fact_families``, set by
``storage/snapshot_reliability_flags.py`` and read only through
:func:`family_reliable`.

Where a family has a per-declaration ``Fact[T]`` (``Param.is_restrict_fact``,
``RecordType.vtable_fact``, ...), loading a stale document also demotes every
such fact to ``NOT_COLLECTED`` (``storage/fact_backfill.py``), and a detector
gates on that per-declaration status instead of on this record. The record is
what remains for the evidence no per-declaration fact carries -- CV spelling
inside a type *string*, a ``Param.default``/constant value fingerprint -- and
for assurance reporting, which states which families a run could not trust.

**Scope, deliberately narrow (Codex review, PR #1209 round 7):** this module
answers only "what is this fact" (ADR-061 D1) -- which families exist and
which a snapshot records as stale. Whether a detector actually consults a
family for a given producer is ``policy.analysis_assurance_degraded_facts``'s
question, not this module's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = [
    "FACT_FAMILIES",
    "RELIABILITY_FLAG_NAMES",
    "family_reliable",
    "flag_name",
    "raw_unreliable_facts",
]

# Per-family rationale, moved verbatim from the retired ``AbiSnapshot``
# fields (each paragraph ends with the family it described). "True" below
# means "not in ``stale_fact_families``".
#
# True when TypeField.is_const/is_volatile/is_mutable and CV-qualifier
# type spelling are known-reliable for this snapshot's fields. The
# castxml parser silently left these permanently False/unqualified
# before a fix (see CHANGELOG); a *persisted* snapshot dumped before
# that fix has real "false" data, not merely absent data, so it cannot
# be told apart from a genuine "not const" field by the value alone —
# only a snapshot-level marker can. False only for a snapshot rehydrated
# from a persisted schema_version predating the fix (a fresh in-memory
# snapshot -- dump(), or one never round-tripped through JSON -- always
# defaults True; see serialization.SCHEMA_VERSION; Codex review, PR #582).
#   ==> family "header_cv" (formerly ``the stale 'header_cv' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's deprecated (every surface kind) and
# EnumType.is_scoped facts are known-reliable when its own
# ``ast_producer`` is ``"clang"`` -- G31 Phase C (schema v19) wired real
# extraction of both into the direct-clang backend, previously
# unconditionally None/False. Same "real but WRONG data" shape as
# ``header_cv_facts_reliable`` above: a pre-v19 clang-producer snapshot's
# ``deprecated=None``/``is_scoped=False`` is indistinguishable by value
# alone from a genuine "not deprecated"/"not scoped" fact, so only a
# snapshot-level marker can tell them apart. False only for a snapshot
# rehydrated from a persisted pre-v19, clang-producer schema (a fresh
# in-memory snapshot always defaults True; see
# serialization.SCHEMA_VERSION). Does NOT need to be checked for "castxml" or "hybrid"
# producers: castxml's own deprecated/is_scoped extraction predates this
# field entirely (G28 Phase 1, always reliable), and a hybrid snapshot's
# per-declaration ``fact_provenance`` already resolves to "castxml" for
# these two facts under the OLD (pre-fix) merge code — the old
# backfill's own "prefer castxml, backfill from clang only when
# castxml's own value is null" policy always recorded "castxml"
# provenance for a fact clang could never populate, so a legacy hybrid
# snapshot carries no equivalent false-reliability risk.
#   ==> family "clang_deprecation" (formerly ``the stale 'clang_deprecation' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's TypeField.default (default member initializer)
# facts are known-reliable when its own ``ast_producer`` is ``"clang"`` OR
# ``"hybrid"`` -- G31 Phase C (schema v20) wired real extraction into the
# direct-clang backend (``dumper_clang_expr._field_initializer_value``),
# previously unconditionally None. Exactly the shape of
# ``clang_deprecation_facts_reliable`` above, and needed for the same
# reason: ``TypeField.default`` is documented (see the field itself) as
# ``None`` both for "no initializer" and "this dumper doesn't capture
# it", so a pre-v20 clang snapshot's blanket ``None`` is indistinguishable
# by value alone from a genuine "this field has no initializer". Without
# this marker, comparing a fresh clang dump against a persisted pre-v20
# clang baseline in the new-side-legacy direction reads as every
# initializer having been REMOVED. Tracked separately from the
# deprecation flag rather than folded into it because the two facts
# landed in different schema versions -- a v19 snapshot has reliable
# deprecated/is_scoped but unreliable field defaults, which a single
# shared flag could not express. Not needed for "castxml" (its own
# extraction predates both flags, G28 Phase 1). UNLIKE
# ``clang_deprecation_facts_reliable``, this one DOES need to cover a
# legacy "hybrid" snapshot too (Codex review, fresh evidence, second
# round): a pre-v20 hybrid merge's clang-only-APPENDED record types
# (``merge_snapshots()``'s ``clang_only_types`` loop) never had
# ``default`` provenance stamped at all -- only ``deprecated`` was, since
# clang couldn't populate ``default`` yet -- so an absent provenance
# entry for such a field on a pre-v20 hybrid snapshot is real-but-WRONG
# data (the field's own value is unconditionally None), not genuinely
# unrecorded. A MATCHED field's ``default`` provenance is unaffected
# either way -- it's unconditionally stamped "castxml" regardless of
# schema version (``_backfill_fact`` records provenance for every
# matched declaration; clang's own value was always None pre-fix, so
# there was nothing to ever backfill from), so it always has a real,
# trusted provenance entry and never depends on this flag.
#   ==> family "clang_field_initializer" (formerly ``the stale 'clang_field_initializer' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's RecordType.vtable/vptr_offset_bits facts are
# known-reliable when its own ``ast_producer`` is ``"clang"`` -- G31
# Phase C (schema v21) wired real virtual-method-table reconstruction
# into the direct-clang backend (``extract/headers/clang/vtable.py``), previously
# unconditionally ``vtable=[]``/``vptr_offset_bits=None`` for EVERY
# record regardless of whether it was actually polymorphic. Same
# "real but WRONG data" shape as ``clang_field_initializer_facts_reliable``
# above: a pre-v21 clang-producer record's blanket empty vtable is
# indistinguishable by value alone from a genuine "this class has no
# virtuals", so only a snapshot-level marker can tell them apart. Without
# this flag, comparing a fresh clang dump of an UNCHANGED, already-
# polymorphic header against a persisted pre-v21 clang baseline reads as
# every polymorphic class gaining its first vptr (Codex review, fresh
# evidence, real end-to-end repro: a persisted schema-v20 clang snapshot
# of ``struct A { virtual void f(); };`` compared against a fresh dump of
# the identical, unchanged header emitted a false ``VPTR_INTRODUCED`` --
# and, for a class whose vtable differs in slot count/order from the
# blanket-empty legacy reading, a false ``TYPE_VTABLE_CHANGED`` too).
# False only for a snapshot rehydrated from a persisted pre-v21,
# clang-producer schema (a fresh in-memory snapshot always defaults
# True -- see serialization.SCHEMA_VERSION). Not needed for "castxml" or
# "hybrid" producers: castxml's own vtable reconstruction predates this
# field entirely (always reliable), and DWARF's own vtable/vptr
# extraction (``dwarf_snapshot.py``) is a wholly separate code path this
# flag does not describe.
#   ==> family "clang_vtable" (formerly ``the stale 'clang_vtable' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's Param.is_restrict facts are known-reliable
# when its own ``ast_producer`` is ``"clang"`` OR ``"hybrid"`` -- G31
# Phase C (schema v22) wired real extraction into the direct-clang
# backend (``dumper_clang._clang_param_is_restrict``), previously
# unconditionally False for EVERY parameter regardless of its actual
# qualification. Same "real but WRONG data" shape as the three flags
# above: ``Param.is_restrict`` is a plain bool with no "not collected"
# state, so a pre-v22 clang-producer parameter's blanket False is
# indistinguishable by value alone from a genuine "not restrict-
# qualified" parameter, and comparing a fresh clang dump of UNCHANGED
# headers against a persisted pre-v22 clang baseline reads as every
# restrict qualifier having been ADDED (and, in the other direction,
# REMOVED). Covers "hybrid" too, for the same reason as
# ``clang_field_initializer_facts_reliable``: a hybrid merge keeps
# castxml's own ``params`` verbatim for every MATCHED function (there is
# no per-param backfill), so only a clang-ONLY function -- appended
# verbatim by ``dumper_hybrid._merge_functions`` -- carries clang's
# parameters, and on a pre-v22 hybrid snapshot those are exactly the
# blanket-False ones. Not needed for "castxml": its own
# ``_resolve_cv_restrict`` extraction predates this field entirely.
# False only for a snapshot rehydrated from a persisted pre-v22,
# clang/hybrid-producer schema (a fresh in-memory snapshot always
# defaults True -- see serialization.SCHEMA_VERSION).
#   ==> family "clang_restrict" (formerly ``the stale 'clang_restrict' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's Param.is_va_list facts are known-reliable
# when its own ``ast_producer`` is ``"clang"`` -- G31 Phase C continued
# (schema v23) wired real extraction into the direct-clang backend
# (``dumper_clang._clang_param_is_va_list``, x86-64 System V spelling
# only), previously unconditionally False for EVERY parameter on every
# backend. Identical "real but WRONG data" shape as
# ``clang_restrict_facts_reliable`` immediately above, for the identical
# reason: ``Param.is_va_list`` is a plain bool with no "not collected"
# state, so a pre-v23 clang-producer parameter's blanket False is
# indistinguishable by value alone from a genuine non-``va_list``
# parameter, and comparing a fresh clang dump of UNCHANGED headers
# against a persisted pre-v23 clang baseline would read as every
# ``va_list`` parameter having just been added.
#
# Deliberately does NOT cover "hybrid" the way
# ``clang_restrict_facts_reliable`` does (Codex review, fresh evidence):
# a hybrid merge keeps castxml's own ``params`` verbatim for every
# MATCHED function, and unlike ``is_restrict`` -- where castxml IS a
# real producer -- castxml has NEVER populated ``is_va_list`` at all, so
# a matched function's param reads a permanent, version-independent
# False regardless of schema version, not a legacy-baseline artifact
# this flag could describe. ``diff_symbols._diff_param_va_list``
# excludes "hybrid" from its producer gate entirely rather than
# consulting this flag for it; see that detector's and
# ``diff_param_qualifiers.param_va_list_changes``'s docstrings for the
# full reasoning. Not needed for "castxml" either, for the ordinary
# reason: it has never populated this fact at all (still true after
# this change — see ``dumper_castxml.py``), so a castxml snapshot's
# blanket False is unconditionally correct-as-"not collected" the same
# way it always was, on any schema version. False only for a snapshot
# rehydrated from a persisted pre-v23, clang-producer schema (a fresh
# in-memory snapshot always defaults True -- see
# serialization.SCHEMA_VERSION).
#   ==> family "clang_va_list" (formerly ``the stale 'clang_va_list' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's Variable.access facts are known-reliable
# when its own ``ast_producer`` is ``"castxml"`` -- G31 Phase C continued
# (schema v24) wired real extraction into the castxml backend
# (``dumper_castxml._CastxmlParser._access_level``, already used for
# ``Function``/``TypeField.access`` -- verified against real castxml
# output that a static class member's ``<Variable>`` element carries the
# identical structured ``access`` attribute), previously unconditionally
# ``AccessLevel.PUBLIC`` for EVERY variable on every backend.
# ``Variable.access`` is a plain enum with no "not collected" state, so
# a pre-v24 castxml-producer variable's blanket PUBLIC is
# indistinguishable by value alone from a genuine public variable, and
# comparing a fresh castxml dump of UNCHANGED headers against a
# persisted pre-v24 castxml baseline would read every real
# private/protected static member as newly WIDENED to public.
#
# Deliberately does NOT cover "clang" (it has never populated this fact
# at all, so its blanket PUBLIC is unconditionally correct-as-"not
# collected" the same way it always was) or "hybrid" (mirroring
# ``clang_va_list_facts_reliable``'s own reasoning: a hybrid merge keeps
# castxml's own ``Variable`` verbatim for a matched declaration -- so
# THAT part is genuinely reliable once castxml itself is fixed -- but a
# clang-only-appended variable carries no access signal at all, and
# nothing distinguishes the two per-declaration today).
# ``diff_symbols._diff_var_access`` requires ``ast_producer == "castxml"``
# on both sides rather than consulting this flag for any other producer.
# False only for a snapshot rehydrated from a persisted pre-v24,
# castxml-producer schema (a fresh in-memory snapshot always defaults
# True -- see serialization.SCHEMA_VERSION).
#   ==> family "castxml_var_access" (formerly ``the stale 'castxml_var_access' fact family (model.snapshot_reliability)``)
#
# True when this snapshot's Param.kind facts are known-reliable when its
# own ``from_headers`` is True (schema v45) -- both header-AST backends
# left every parameter at the resting ``ParamKind.VALUE`` before this
# fix, unlike DWARF (always a real producer). See ``diff_symbols.
# _params_differ`` and ``docs/reference/fact-registry.md``.
#   ==> family "param_kind" (formerly ``the stale 'param_kind' fact family (model.snapshot_reliability)``)
#

#: Every fact family a snapshot can record as stale, in the historical order
#: of the retired fields (which is also their wire order).
FACT_FAMILIES: tuple[str, ...] = (
    "header_cv",
    "clang_deprecation",
    "clang_field_initializer",
    "clang_vtable",
    "clang_restrict",
    "clang_va_list",
    "castxml_var_access",
    "param_kind",
)


def flag_name(family: str) -> str:
    """The persisted key (and historical field name) for *family*."""
    return f"{family}_facts_reliable"


#: The persisted keys, one per :data:`FACT_FAMILIES` entry.
RELIABILITY_FLAG_NAMES: tuple[str, ...] = tuple(flag_name(f) for f in FACT_FAMILIES)


def family_reliable(snap: AbiSnapshot, family: str) -> bool:
    """True unless *snap* recorded *family*'s persisted values as stale."""
    if family not in FACT_FAMILIES:
        raise ValueError(f"unknown fact family {family!r}")
    return family not in snap.stale_fact_families


def raw_unreliable_facts(snap: AbiSnapshot) -> list[str]:
    """The sorted persisted keys of *snap*'s stale families -- with no
    judgment about whether any detector reads that family for *this*
    snapshot's producer. That narrowing is ``policy.
    analysis_assurance_degraded_facts.degraded_reliability_facts``'s job.
    """
    return sorted(flag_name(f) for f in FACT_FAMILIES if f in snap.stale_fact_families)
