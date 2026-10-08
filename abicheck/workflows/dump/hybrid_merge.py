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

"""Hybrid castxml+clang snapshot merge (G28 Phase 3, ``--ast-frontend hybrid``).

``dumper.dump`` (and the native ``run_dump``) run BOTH L2 header-AST backends over the same headers and hands
their two independent :class:`~abicheck.model.AbiSnapshot`\\ s to
:func:`merge_snapshots`, which combines them into one snapshot:

- **Ctor/dtor identity reconciliation** (the concrete motivating bug from the
  G28 plan): castxml sometimes cannot recover a real mangled name for a
  constructor/destructor and synthesizes a placeholder snapshot key instead
  (``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX`` / the ``"~ClassName"`` dtor
  form). That placeholder shares no identity with the SAME entity's real
  Itanium-mangled key on the clang side, so comparing a castxml-parsed
  snapshot against a clang-parsed snapshot of unchanged source reports a
  false ``FUNC_REMOVED``+``FUNC_ADDED`` pair for every such constructor/
  destructor (see
  ``tests/test_castxml_clang_parity_gate.py::TestCrossProducerUnmangledIdentityKnownLimitation``).
  This module fixes it by matching a synthetic key against a real clang
  mangled name via structural equivalence (same qualified enclosing class,
  compatible cv-normalized parameter signature for a constructor, same
  access) and rewriting the merged entry's key to the real mangled name.
- **Per-fact backfill**: facts that were originally castxml-only
  (``deprecated``/``is_override`` on functions, ``deprecated`` on
  variables, ``is_abstract``/``deprecated`` on types, ``default``/
  ``deprecated`` on fields, ``is_scoped``/``deprecated`` on enums) are taken
  from castxml when present, backfilled from clang only when castxml's own
  value is ``None``. G31 Phase C closed the gap this backfill originally
  anticipated for three of these facts specifically — ``deprecated`` (every
  surface kind), ``is_scoped``, and field ``default`` — by wiring real
  extraction into ``dumper_clang.py`` too, so this backfill is genuinely
  live for those three now, not forward-looking scaffolding;
  ``is_override``/``is_abstract`` remain castxml-only, so the backfill is
  still a no-op for those two specifically. Note that field ``default`` is
  cross-*producer* without being cross-*comparable*: castxml keeps the
  verbatim source expression where clang emits a literal or a structural
  fingerprint, so its detector gates on the two sides sharing one producer
  rather than on both merely having a known one. Every such fact records
  its source in the returned snapshot's ``fact_provenance`` map (see
  ``abicheck/fact_provenance.py``), so detectors can tell which backend
  backs a fact on a per-declaration basis.
- **Declaration-existence provenance** (G31 Phase C, hybrid-graph
  provenance-tagging): every merged function/variable also gets a
  ``"visibility"``-named ``fact_provenance`` entry recording which backend
  contributed the *declaration itself* (``"castxml"`` for a castxml-primary
  entry, ``"clang"`` for a clang-only-appended one) — not a per-field value
  merge like every other entry above, since this snapshot's own
  ``origin``/``ScopeOrigin`` classification (``provenance.apply_provenance()``)
  runs identically over both kinds of entry afterwards. The one consumer is
  :func:`abicheck.buildsource.header_graph.build_header_only_graph`, which
  reads it back to stamp each L2 graph node's own
  ``attrs["visibility_provenance"]`` — see that function's docstring for why
  the graph needed this and not the flat snapshot's other detectors.

**Layout facts**: castxml remains the PRIMARY layout source — its own real
size/alignment/offset/vtable data is never overridden. When the optional G28
Phase 4 companion tool (``ABICHECK_CLANG_LAYOUT_TOOL``) has already enriched
the clang sub-dump before this merge, its facts backfill ``data_size_bits``
and the offset/vptr facts castxml itself never computes at all, or left
empty for an opaque/incomplete castxml record — see
:func:`_merge_record_type`/:func:`_merge_field`. ``is_standard_layout``/
``is_trivially_copyable`` are the one exception to "without the layout tool
enabled, this is a no-op": G31 Phase C wired real extraction of both
directly into ``dumper_clang.py``'s plain ``-ast-dump=json`` parse (no
companion tool needed, since these are semantic type traits clang's AST
computes independent of any layout pass — see ``_clang_record_type_traits``'s
own docstring), so this backfill genuinely fires for those two even without
``ABICHECK_CLANG_LAYOUT_TOOL`` set. ``data_size_bits``/``size_bits``/
``alignment_bits``/``vptr_offset_bits`` still require the companion tool;
``dumper_clang.py``'s plain parse leaves those empty either way.

**``is_template_pattern``/``has_anonymous_aggregate_fields``** (G31 Phase C
fact-completeness, PR #719 follow-up): unlike every other backfilled fact
above, both are plain ``bool = False`` rather than an Optional tri-state, so
:func:`_merge_record_type` OR-merges them (never a null-check backfill) —
castxml's own ``False`` is always structurally correct by construction, not
a placeholder for "unknown". Verified against real castxml 0.6.3 + clang 18
output that ``is_template_pattern``'s backfill is empirically inert for the
current producer pair (a clang-recognized template pattern never shares a
type_map_key with any castxml-matched concrete type — it reaches the merged
snapshot via the clang-only-append path instead, already carrying the flag
correctly); kept anyway as a defense-in-depth/honesty measure, the same
precedent already set for ``RecordType.is_abstract``.
``has_anonymous_aggregate_fields`` is not provably inert the same way —
see :func:`_merge_record_type`'s own comment.

Everything not explicitly merged below (bare-keyed ``typedefs``, constants,
ELF/PE/Mach-O metadata, DWARF metadata, ...) is taken verbatim from the
castxml base (``dataclasses.replace``) -- except ``typedefs_qualified``
(schema v25), unioned from both sides (its own merge comment) to recover
an alias a single backend alone would miss.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

from ...comparability_fields import PROFILE_FIELD_KEYS, _sha256_of
from ...diff_helpers import type_map_key
from ...extract.semantic_ir_merge import merge_semantic_ir
from ...fact_provenance import (
    backfill_fact,
    enum_fact_key,
    field_fact_key,
    func_fact_key,
    type_fact_key,
    var_fact_key,
)
from ...model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Variable,
    replace_with_fact_sync,
)
from ...model.identity import EntityId, with_mangled_name
from ...model.synthetic_key import (
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .hybrid_identity import (
    _backfill_function_facts,
    _ctor_dtor_scope,
    _drop_unmatched_constant_occurrences,
    _macho_normalize_mangled,
    _macho_normalize_semantic_ir,
    _match_synthetic_ctor_dtor,
    _normalize_scope_for_matching,
    _rewrite_semantic_ir_entity_ids,
)

log = logging.getLogger(__name__)


def _merge_functions(
    castxml_funcs: list[Function],
    clang_funcs: list[Function],
    provenance: dict[str, str],
    entity_id_rewrites: dict[EntityId, EntityId] | None = None,
) -> list[Function]:
    """*entity_id_rewrites*, when given, is populated with every
    ``old_entity_id -> new_entity_id`` substitution made below for a
    castxml synthetic ctor/dtor key matched to a real clang mangled name --
    the caller applies the identical rewrite to `castxml_snap.semantic_ir`
    (:func:`_rewrite_semantic_ir_entity_ids`) so that representation isn't
    left keyed under the stale synthetic identity this function just
    retired from the flat `functions` list (Codex review, ADR-063 Phase 6
    third slice, fresh evidence)."""
    clang_ctor_dtor: dict[tuple[str, str], list[Function]] = {}
    for cf in clang_funcs:
        scope = _ctor_dtor_scope(cf.mangled)
        if scope is not None:
            marker, scope_str = scope
            key = (marker, _normalize_scope_for_matching(scope_str))
            clang_ctor_dtor.setdefault(key, []).append(cf)

    merged: list[Function] = []
    for f in castxml_funcs:
        if is_synthetic_ctor_key(f.mangled) or is_synthetic_dtor_key(f.mangled):
            match = _match_synthetic_ctor_dtor(f, clang_ctor_dtor)
            if match is not None:
                if (
                    entity_id_rewrites is not None
                    and f.entity_id is not None
                    and match.entity_id is not None
                ):
                    entity_id_rewrites[f.entity_id] = match.entity_id
                # Adopt match's entity_id too, or it keeps the synthetic key.
                f = replace(f, mangled=match.mangled, entity_id=match.entity_id)
        merged.append(f)

    clang_by_mangled = {cf.mangled: cf for cf in clang_funcs}
    merged = [
        _backfill_function_facts(f, clang_by_mangled.get(f.mangled), provenance)
        for f in merged
    ]
    # Every function actually present in castxml_funcs is castxml-backed for
    # this fact — even one whose synthetic ctor/dtor key got rewritten to a
    # clang mangled name above, since the *declaration* itself is still
    # castxml's. Both backends populate Param.default now, but their VALUE
    # representations aren't cross-comparable (castxml keeps the real source
    # expression; dumper_clang.py falls back to a structural fingerprint/
    # placeholder for anything beyond a bare literal), so this fact still
    # needs a producer tag per function — _diff_param_defaults uses it to
    # require the SAME producer on both sides of a pair, not specifically
    # "castxml" (Codex review: a clang-only function is still comparable
    # against ANOTHER clang-only declaration of itself, exactly like a plain
    # ``--ast-frontend clang`` run already does today).
    # "visibility" records which backend contributed the DECLARATION ITSELF
    # (castxml-primary vs. clang-only-appended), not a per-field value merge
    # like every other key this function writes — consumed by
    # buildsource.header_graph.build_header_only_graph() to stamp
    # GraphNode.attrs["visibility_provenance"] on the L2 header-only graph's
    # source_decl nodes (G31 Phase C hybrid-graph provenance-tagging;
    # docs/contribute/plans/g31-header-graph-default-on-followup.md). A
    # castxml-primary function's ScopeOrigin classification and a
    # clang-only-appended one's both go through the identical
    # provenance.apply_provenance() pass afterwards, so this key is not
    # itself the classification — it's which backend's declaration record
    # that classification was computed from, the same distinction
    # "param_defaults" above already tracks for a different consumer.
    for f in merged:
        provenance[func_fact_key(f.mangled, "param_defaults")] = "castxml"
        provenance[func_fact_key(f.mangled, "visibility")] = "castxml"

    merged_mangled = {f.mangled for f in merged}
    clang_only = [cf for cf in clang_funcs if cf.mangled not in merged_mangled]
    for cf in clang_only:
        provenance[func_fact_key(cf.mangled, "param_defaults")] = "clang"
        # A clang-only function's own deprecated value IS genuinely
        # clang-sourced -- without this, both_known_backed_fact(old, new,
        # func_fact_key(mangled, "deprecated")) sees no recorded provenance
        # for it at all and incorrectly declines to compare a real
        # deprecation transition on a declaration that exists on both sides
        # only via clang (Codex review, fresh evidence).
        provenance[func_fact_key(cf.mangled, "deprecated")] = "clang"
        # Same reasoning as "deprecated" immediately above, for
        # is_override (G31 Phase C's is_override/is_abstract backend
        # audit): a clang-only method's is_override IS genuinely
        # clang-sourced, and without this stamp
        # both_known_backed_fact(old, new, func_fact_key(mangled,
        # "is_override")) sees no recorded provenance for it and declines
        # to compare a real override-specifier transition on a method
        # that exists on both sides only via clang (Codex review, fresh
        # evidence).
        provenance[func_fact_key(cf.mangled, "is_override")] = "clang"
        provenance[func_fact_key(cf.mangled, "visibility")] = "clang"
    merged.extend(clang_only)
    return merged


def _merge_variable(
    v: Variable, clang_v: Variable | None, provenance: dict[str, str]
) -> Variable:
    key = var_fact_key(v.mangled, "deprecated")
    value = backfill_fact(
        v.deprecated, clang_v.deprecated if clang_v else None, key, provenance
    )
    # replace_with_fact_sync everywhere this module backfills a Fact[...]-
    # bridged attr (ADR-063 Phase 5): a bare replace() carries the stale
    # sibling forward and __post_init__'s "explicit Fact wins" rule then
    # reverts the backfill (model/fact.py's documented trap; the rule is
    # enforced for every call site by tests/test_fact_bridged_replace_guard.py).
    return replace_with_fact_sync(v, deprecated=value) if value != v.deprecated else v


#: G28 Phase 4 layout facts castxml either never populates at all
#: (data_size_bits/is_standard_layout/is_trivially_copyable) or leaves empty
#: for an opaque/incomplete record (size_bits/alignment_bits/
#: vptr_offset_bits) -- backfilled from an already-enriched clang_t below
#: only when castxml's own value is still None (Codex review). Since G31
#: Phase C, is_standard_layout/is_trivially_copyable no longer need the
#: optional ABICHECK_CLANG_LAYOUT_TOOL companion tool to backfill from --
#: dumper_clang.py's plain parse populates both directly (see
#: _clang_record_type_traits) -- the other four entries still do.
_LAYOUT_SCALAR_ATTRS = (
    "size_bits",
    "alignment_bits",
    "data_size_bits",
    "is_standard_layout",
    "is_trivially_copyable",
    "vptr_offset_bits",
)


def _merge_field(
    t: RecordType,
    f: TypeField,
    clang_f: TypeField | None,
    provenance: dict[str, str],
) -> TypeField:
    updates: dict[str, Any] = {}
    for attr in ("default", "deprecated"):
        # Both facts are genuinely cross-producer since G31 Phase C
        # ("deprecated" from that phase's first pass, "default" from
        # dumper_clang_expr._field_initializer_value), so both need the qualified
        # key: a clang-only sibling type sharing t's bare name independently
        # writes to this same provenance dict (see merge_snapshots'
        # clang-only-type append loop below), and a bare key would let one
        # writer's entry silently overwrite the other's (Codex review, fresh
        # evidence). "default" was deliberately left bare when only
        # "deprecated" got a clang-only-append write -- that exemption no
        # longer holds now that "default" gets one too. Legacy hybrid
        # baselines keyed bare are still read via
        # diff_helpers.fact_same_producer_qualified's bare fallback.
        key = field_fact_key(type_map_key(t), f.name, attr)
        value = backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    # G28 Phase 4: same layout backfill as _merge_record_type, for the
    # per-field offset the optional companion tool computes.
    if (
        clang_f is not None
        and f.offset_bits is None
        and clang_f.offset_bits is not None
    ):
        updates["offset_bits"] = clang_f.offset_bits
    # replace_with_fact_sync: see _merge_variable's comment above.
    return replace_with_fact_sync(f, **updates) if updates else f


def _merge_record_type(
    t: RecordType, clang_t: RecordType | None, provenance: dict[str, str]
) -> RecordType:
    updates: dict[str, Any] = {}
    for attr in ("is_abstract", "deprecated"):
        # Same bare-vs-qualified split as _merge_field above: is_abstract
        # stays bare (pre-existing castxml-only fact, no clang-only append
        # writes to it), deprecated is qualified (G31 Phase C).
        type_key = type_map_key(t) if attr == "deprecated" else t.name
        key = type_fact_key(type_key, attr)
        value = backfill_fact(
            getattr(t, attr), getattr(clang_t, attr, None), key, provenance
        )
        if value != getattr(t, attr):
            updates[attr] = value

    # G28 Phase 4 (optional ABICHECK_CLANG_LAYOUT_TOOL): clang_t may carry REAL ASTRecordLayout facts the companion tool already backfilled onto clang_snap BEFORE this merge (attach_clang_layout runs on clang_snap's own recursive dump). Without this, a type present on BOTH backends -- the common case -- lost every one of these facts in a hybrid merge even with the layout tool enabled, while a clang-ONLY type (appended verbatim below) kept them (Codex review). Never overrides an existing castxml value -- castxml's own real layout, when present, always wins.
    if clang_t is not None:
        for attr in _LAYOUT_SCALAR_ATTRS:
            if getattr(t, attr) is None and getattr(clang_t, attr) is not None:
                updates[attr] = getattr(clang_t, attr)
                # vptr_offset_bits_fact sibling: carry clang_t's own status so replace_with_fact_sync can't promote its real PARTIAL to present() (same bug class as dumper_layout_backfill.py's DWARF backfill, Codex review).
                if hasattr(clang_t, f"{attr}_fact"):
                    updates[f"{attr}_fact"] = getattr(clang_t, f"{attr}_fact")
        if not t.base_offsets and clang_t.base_offsets:
            updates["base_offsets"] = clang_t.base_offsets
        # G31 Phase C fact-completeness (verified against real castxml 0.6.3 +
        # clang 18 output, PR #719 follow-up): unlike every other backfilled
        # fact above, these two are plain `bool = False` -- not an Optional
        # tri-state -- so there is no null "castxml doesn't know" state to key
        # a backfill_fact()-style None-check off. castxml's own `False` is
        # ALWAYS structurally correct by construction rather than a placeholder
        # (castxml never emits an uninstantiated template pattern as a
        # declaration at all, and it always computes real per-field offsets
        # for an anonymous-aggregate flatten it can see), so this is a plain
        # OR-merge, not a None-guarded backfill.
        #
        # is_template_pattern is empirically INERT here, verified with a real
        # class-template dump: a clang-recognized template PATTERN never
        # shares a type_map_key with any castxml-visible concrete type (it's
        # a structurally distinct entity -- castxml only ever emits concrete
        # instantiations under their own instantiated name, e.g. "Box<int>",
        # never a bare "Box" pattern declaration), so `clang_t` for a
        # castxml-matched `t` is never itself the pattern; the True-carrying
        # clang entry instead reaches the merged snapshot verbatim via the
        # clang-only-append path below. Kept here anyway (not asserted
        # unreachable) both for defense in depth against a future clang
        # AST-shape change and because it is honest about the invariant this
        # merge is supposed to preserve, matching this module's own documented
        # precedent for RecordType.is_abstract (a backfill kept even though
        # the current producer pair makes it a no-op).
        #
        # has_anonymous_aggregate_fields is NOT provably inert the same way: a
        # castxml record with real, populated fields already carries
        # corroborating field-name-overlap evidence dumper_layout_backfill.py
        # prefers over this flag's own fallback path, but an opaque/incomplete
        # castxml record (or a future producer shape) could legitimately reach
        # this merge with an EMPTY `fields` list for a genuinely
        # anonymous-aggregate-only record, where clang's `True` is the only
        # signal available.
        if clang_t.is_template_pattern and not t.is_template_pattern:
            updates["is_template_pattern"] = True
        if (
            clang_t.has_anonymous_aggregate_fields
            and not t.has_anonymous_aggregate_fields
        ):
            updates["has_anonymous_aggregate_fields"] = True

    clang_fields_by_name = {cf.name: cf for cf in clang_t.fields} if clang_t else {}
    merged_fields = [
        _merge_field(t, f, clang_fields_by_name.get(f.name), provenance)
        for f in t.fields
    ]
    if merged_fields != t.fields:
        updates["fields"] = merged_fields

    from ...model import replace_with_fact_sync

    return replace_with_fact_sync(t, **updates) if updates else t


def _merge_enum_type(
    e: EnumType, clang_e: EnumType | None, provenance: dict[str, str]
) -> EnumType:
    updates: dict[str, Any] = {}
    # Both facts get clang-only-append writes (merge_snapshots' clang-only
    # enum loop below), so both need the qualified key uniformly -- unlike
    # RecordType's is_abstract/deprecated split above, there's no bare-only
    # fact here to preserve compatibility with.
    type_key = type_map_key(e)
    for attr in ("is_scoped", "deprecated"):
        key = enum_fact_key(type_key, attr)
        value = backfill_fact(
            getattr(e, attr), getattr(clang_e, attr, None), key, provenance
        )
        if value != getattr(e, attr):
            updates[attr] = value
    # replace_with_fact_sync: see _merge_variable's comment above. The
    # `**updates` spelling is why a name-based sweep missed this one site
    # (Codex review, PR #993) -- hence the guard test named there.
    return replace_with_fact_sync(e, **updates) if updates else e


def merge_snapshots(castxml_snap: AbiSnapshot, clang_snap: AbiSnapshot) -> AbiSnapshot:
    """Merge a castxml-produced and a clang-produced snapshot of the SAME
    headers into one hybrid :class:`AbiSnapshot`.

    castxml remains the base (layout facts, ELF/PE/Mach-O metadata, typedefs,
    constants, and everything not explicitly merged here all come from it
    verbatim) — only the facts documented in this module's docstring are
    actually reconciled/backfilled. The result's ``ast_producer`` is
    ``"hybrid"`` and its ``fact_provenance`` records, per declaration, which
    backend's value was used for each of those facts.

    If EITHER side never got confirmed header-AST evidence — no headers were
    supplied, the dump ran ``dwarf_only``/``symbols_only``, or one backend
    degraded to a non-header fallback (e.g. the PE/Mach-O header-scoped path
    falling back to export-table mode when clang is unavailable or nothing
    matched) — returns *castxml_snap* unchanged rather than unioning the
    other side's declarations into a falsely-upgraded, confirmed
    header-aware ``ast_producer="hybrid"`` result. A one-sided fallback is
    not just missing data to merge: unioning a non-header snapshot's much
    broader export-table-derived functions/types into a header-scoped result
    would also pull that noise back in, and header-tier detectors (param
    defaults, constants, param renames) would misread the merge's forced
    header-aware provenance when compared against a genuinely header-aware
    snapshot (Codex review, x2).
    """
    if not (castxml_snap.from_headers and clang_snap.from_headers):
        return castxml_snap

    provenance: dict[str, str] = {}

    # Mach-O: normalize clang's mangled names to castxml's prefix-free
    # convention BEFORE any mangled-keyed matching/dedup below (functions AND
    # variables) -- see _macho_normalize_mangled's docstring. Type/enum
    # merges key on the source-level NAME, not a mangled linker symbol, so
    # they carry no such platform-specific decoration and need no change.
    # entity_id's "mangled" tag is re-spelled too (Codex review).
    clang_decls = clang_snap.declarations
    clang_functions, clang_variables = clang_decls.functions, clang_decls.variables
    clang_semantic_ir = clang_snap.canonical_ir
    if castxml_snap.platform == "macho":
        clang_functions = [
            replace(cf, mangled=nm, entity_id=with_mangled_name(cf.entity_id, nm))
            for cf in clang_functions
            for nm in (_macho_normalize_mangled(cf.mangled),)
        ]
        clang_variables = [
            replace(cv, mangled=nm, entity_id=with_mangled_name(cv.entity_id, nm))
            for cv in clang_variables
            for nm in (_macho_normalize_mangled(cv.mangled),)
        ]
        # ADR-063 Phase 6 third slice (Codex review, fresh evidence): the
        # identical rewrite must reach `semantic_ir` too, or its FUNCTION/
        # VARIABLE occurrences stay keyed under clang's un-normalized
        # mangled name while the flat lists above (and castxml's own side)
        # use the normalized one -- see `_macho_normalize_semantic_ir`'s
        # own docstring for the exact double-counting this produced.
        clang_semantic_ir = _macho_normalize_semantic_ir(clang_semantic_ir)

    # Keyed by type_map_key (namespace-qualified identity), not the bare
    # RecordType.name/EnumType.name: two distinct types sharing only a bare
    # leaf name in different namespaces (e.g. a::Foo/b::Foo) would otherwise
    # silently collide here too -- one castxml record merging against the
    # WRONG clang record, and/or a genuinely clang-only record (that merely
    # shares its bare name with an unrelated castxml record) being dropped
    # instead of appended (Codex review, fresh evidence).
    clang_types_by_key = {type_map_key(t): t for t in clang_snap.declarations.types}
    clang_enums_by_key = {type_map_key(e): e for e in clang_snap.declarations.enums}
    clang_vars_by_mangled = {v.mangled: v for v in clang_variables}

    ctor_dtor_entity_id_rewrites: dict[EntityId, EntityId] = {}
    merged_functions = _merge_functions(
        castxml_snap.declarations.functions,
        clang_functions,
        provenance,
        ctor_dtor_entity_id_rewrites,
    )

    merged_types = [
        _merge_record_type(t, clang_types_by_key.get(type_map_key(t)), provenance)
        for t in castxml_snap.declarations.types
    ]
    castxml_type_keys = {type_map_key(t) for t in castxml_snap.declarations.types}
    clang_only_types = [
        t for t in clang_decls.types if type_map_key(t) not in castxml_type_keys
    ]
    for t in clang_only_types:
        # A clang-only type's own deprecated value IS genuinely clang-
        # sourced -- without this, both_known_backed_fact sees no recorded
        # provenance at all for a declaration that exists on both snapshot
        # sides only via clang, and incorrectly declines to compare a real
        # transition (Codex review, fresh evidence). Qualified key (not
        # bare t.name): two distinct types sharing only a bare leaf name in
        # different namespaces (e.g. a genuinely clang-only b::Foo and a
        # castxml+clang-matched a::Foo) would otherwise silently collide in
        # this shared provenance dict too -- one writer's entry
        # overwriting the other's (Codex review, fresh evidence, second
        # round) -- matching _merge_record_type/_merge_field's identical
        # qualification for this same fact above.
        type_key = type_map_key(t)
        provenance[type_fact_key(type_key, "deprecated")] = "clang"
        # Same reasoning as "deprecated" immediately above, for is_abstract
        # (G31 Phase C's is_override/is_abstract backend audit): a
        # clang-only type's own is_abstract value IS genuinely
        # clang-sourced, and without this stamp both_known_backed_fact
        # sees no recorded provenance for it and declines to compare a
        # real abstractness transition on a type that exists on both
        # sides only via clang (Codex review, fresh evidence). BARE key
        # (not qualified type_key, unlike "deprecated" above): is_abstract
        # is the one fact `_merge_record_type` deliberately keys bare
        # (see that function's own comment), because `diff_types._diff_types`
        # only ever looks it up via the bare `type_fact_key(t_old.name,
        # "is_abstract")` -- a qualified key here would silently mismatch
        # that lookup and make this stamp inert for a namespaced type
        # (Codex review, fresh evidence, third round).
        provenance[type_fact_key(t.name, "is_abstract")] = "clang"
        for f in t.fields:
            provenance[field_fact_key(type_key, f.name, "deprecated")] = "clang"
            # "default" joined "deprecated" as a genuinely clang-sourced field
            # fact in G31 Phase C (dumper_clang_expr._field_initializer_value).
            # Its detector gates on SAME producer rather than any-known
            # producer (the two backends' initializer representations aren't
            # cross-comparable), so this stamp is what lets a hybrid-vs-hybrid
            # pair compare a clang-only field's initializer at all -- and,
            # equally, what lets a mixed pair be correctly declined instead of
            # silently compared as if same-producer.
            provenance[field_fact_key(type_key, f.name, "default")] = "clang"
    merged_types.extend(clang_only_types)

    merged_enums = [
        _merge_enum_type(e, clang_enums_by_key.get(type_map_key(e)), provenance)
        for e in castxml_snap.declarations.enums
    ]
    castxml_enum_keys = {type_map_key(e) for e in castxml_snap.declarations.enums}
    clang_only_enums = [
        e for e in clang_decls.enums if type_map_key(e) not in castxml_enum_keys
    ]
    for e in clang_only_enums:
        # Qualified key -- same reasoning as clang_only_types above.
        type_key = type_map_key(e)
        provenance[enum_fact_key(type_key, "deprecated")] = "clang"
        provenance[enum_fact_key(type_key, "is_scoped")] = "clang"
    merged_enums.extend(clang_only_enums)

    merged_variables = [
        _merge_variable(v, clang_vars_by_mangled.get(v.mangled), provenance)
        for v in castxml_snap.declarations.variables
    ]
    # "visibility" mirrors _merge_functions' identical stamp above -- which
    # backend contributed the declaration itself, consumed by
    # header_graph.build_header_only_graph() for its graph-node provenance
    # tag, not a per-field value merge.
    for v in castxml_snap.declarations.variables:
        provenance[var_fact_key(v.mangled, "visibility")] = "castxml"
    castxml_var_mangled = {v.mangled for v in castxml_snap.declarations.variables}
    clang_only_variables = [
        v for v in clang_variables if v.mangled not in castxml_var_mangled
    ]
    for v in clang_only_variables:
        provenance[var_fact_key(v.mangled, "deprecated")] = "clang"
        provenance[var_fact_key(v.mangled, "visibility")] = "clang"
    merged_variables.extend(clang_only_variables)

    # ADR-063 Phase 6: the semantic IR needs the same base-plus-backfill
    # reconciliation every legacy field above already gets. Carrying
    # castxml's through unchanged, while `functions`/`types` include
    # clang-only and clang-backfilled data, would leave one freshly built
    # snapshot's two representations disagreeing (extract/semantic_ir_merge.py).
    # `_merge_functions`'s own ctor/dtor identity rewrite (above) must reach
    # `semantic_ir` too, or a matched castxml declaration stays keyed under
    # its retired synthetic identity there while `merged_functions` already
    # carries the real one (Codex review, fresh evidence).
    castxml_semantic_ir = _rewrite_semantic_ir_entity_ids(
        castxml_snap.canonical_ir, ctor_dtor_entity_id_rewrites
    )
    merged_ir, ir_conflicts = merge_semantic_ir(castxml_semantic_ir, clang_semantic_ir)
    # `merged.constants` deliberately stays castxml-only (see that field's
    # own comment below) -- `merged_constant_entity_ids` is exactly its
    # sidecar, computed here rather than inline in `replace()` below so
    # `_drop_unmatched_constant_occurrences` can use the identical kept-id
    # set to filter `merged_ir`'s own CONSTANT occurrences to match, instead
    # of a clang-only constant occurrence surviving in `semantic_ir` with no
    # corresponding flat entry at all (Codex review, fresh evidence).
    merged_constant_entity_ids = {
        key: value
        for key, value in {
            **clang_snap.declarations.constant_entity_ids,
            **castxml_snap.declarations.constant_entity_ids,
        }.items()
        if key in castxml_snap.declarations.constants
    }
    merged_ir, ir_conflicts = _drop_unmatched_constant_occurrences(
        merged_ir, ir_conflicts, set(merged_constant_entity_ids.values())
    )

    merged = replace(
        castxml_snap,
        semantic_ir=merged_ir,
        semantic_ir_conflicts={**castxml_snap.semantic_ir_conflicts, **ir_conflicts},
        functions=merged_functions,
        variables=merged_variables,
        types=merged_types,
        enums=merged_enums,
        # typedefs_qualified (schema v25, G31 Phase C continued, Codex
        # review): unlike bare `typedefs` (left verbatim from castxml_snap,
        # same as constants/ELF/PE/Mach-O metadata -- see this function's
        # own docstring), this field's whole purpose is to recover a
        # qualified typedef alias `type_reachability.py`'s scan would
        # otherwise miss. Leaving it castxml-only defeats that purpose for
        # a hybrid dump: a declaration only clang appended (or a typedef
        # only clang's own parse captured under this qualified key) would
        # never make it into `merged.typedefs_qualified`, so a public
        # signature referencing it through that alias could still miss a
        # reachable `std::` field. Union both sides -- qualified keys are
        # unique per declaration, so a real cross-backend disagreement on
        # the SAME key is not expected; castxml's own value wins on the
        # rare disagreement, matching "castxml remains the base" elsewhere
        # in this merge.
        typedefs_qualified={
            **clang_snap.declarations.typedefs_qualified,
            **castxml_snap.declarations.typedefs_qualified,
        },
        # The `EntityId` sidecars (ADR-063 Phase 2) union the same way, in the
        # same direction, so they cannot desync from the dicts they annotate.
        typedef_entity_ids={
            **clang_snap.declarations.typedef_entity_ids,
            **castxml_snap.declarations.typedef_entity_ids,
        },
        # `constants` itself (unlike `typedefs_qualified`) is NOT merged
        # above -- it stays castxml_snap's own, verbatim, same as every
        # other bare-keyed field this function's docstring lists. A clang-
        # only key unioned into the sidecar the same way `typedef_
        # entity_ids` is would therefore name a constant `merged.constants`
        # never retained -- a phantom identity violating the sidecar's own
        # exact-key contract with its partner dict (Codex review). Keyed
        # off `castxml_snap.constants` (== `merged.constants`) instead, so
        # every sidecar key has a real constant behind it; a key clang
        # alone resolved an identity for, that castxml also kept, still
        # wins clang's identity as it did before, since clang is spread
        # first in the union below. Computed above (`merged_constant_
        # entity_ids`), not inlined here, so `_drop_unmatched_constant_
        # occurrences` filters `semantic_ir` against the identical set.
        constant_entity_ids=merged_constant_entity_ids,
        ast_producer="hybrid",
        ast_toolchain={
            **{
                f"castxml_{key}": value
                for key, value in castxml_snap.ast_toolchain.items()
            },
            **{
                f"clang_{key}": value for key, value in clang_snap.ast_toolchain.items()
            },
        },
        ast_fallback_reason=None,
        fact_provenance=provenance,
        # from_headers/from_headers_inferred are inherited from castxml_snap
        # as-is via replace() (both already True/False here — the early
        # return above handles the case where they aren't).
        # Invalidate the lazy lookup caches (dataclasses.replace() otherwise
        # carries the OLD castxml-only indexes forward unchanged, since these
        # are ordinary fields with defaults, not something replace() knows to
        # reset just because functions/variables/types changed).
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )

    # ADR-050 D1 (Codex review, PR #624 follow-up): without this, the merged
    # "hybrid" snapshot's contract stays castxml_snap's alone, silently
    # dropping the clang leg's own compiler identity -- two hybrid dumps
    # differing only in which clang binary/version parsed the clang leg
    # (the castxml leg identical) would then share a profile_fingerprint
    # despite a genuinely different extraction context on that leg. Both
    # sub-contracts are already correctly computed (each dump_fn call in
    # run_hybrid_dump gets its own via dumper._attach_extraction_contract);
    # fold the clang leg's compiler identity into the merged contract's
    # existing compiler_version field and recompute just that one
    # dependent hash, rather than re-deriving identity from the raw,
    # prefix-merged ast_toolchain dict above.
    if merged.contract is not None and clang_snap.contract is not None:
        # json.dumps, not a raw join (same class of bug already fixed for
        # macro_ops/slot tokens in comparability.py): neither identity
        # string is guaranteed delimiter-free.
        combined_compiler_version = json.dumps(
            [
                merged.contract.profile_fields.get("compiler_version", ""),
                clang_snap.contract.profile_fields.get("compiler_version", ""),
            ]
        )
        new_profile_fields = {
            **merged.contract.profile_fields,
            "compiler_version": combined_compiler_version,
        }
        new_fingerprint = _sha256_of(
            *[new_profile_fields[k] for k in PROFILE_FIELD_KEYS]
        )
        merged = replace(
            merged,
            contract=replace(
                merged.contract,
                profile_fields=new_profile_fields,
                profile_fingerprint=new_fingerprint,
            ),
        )

    return merged
