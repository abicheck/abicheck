#!/usr/bin/env python3
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

"""The RecordType and later rows of the L2 header-backend fact table.

Split out of ``backend_capability_rows.py`` for the file-size cap. Composed
into ``FACT_ROWS`` there, in order.
"""

from __future__ import annotations

from backend_capability_model import (
    _COMPANION,
    _FULL,
    _NONE,
    _OTHER,
    _PARTIAL,
    _PROVENANCE_PASS,
    Capability,
    FactRow,
)

RECORD_AND_LATER_ROWS: tuple[FactRow, ...] = (
    # ── RecordType ─────────────────────────────────────────────────────────
    FactRow("RecordType", "name", _FULL, _FULL),
    FactRow("RecordType", "kind", _FULL, _FULL),
    FactRow(
        "RecordType",
        "size_bits",
        _FULL,
        _COMPANION,
        hybrid_backfilled=True,
        note=(
            "castxml runs a real compiler and computes layout; the plain clang "
            "AST parse leaves it `None` so the layout detectors skip an "
            "unknown-vs-unknown comparison (DWARF stays the layout authority)."
        ),
    ),
    FactRow(
        "RecordType",
        "alignment_bits",
        _FULL,
        _COMPANION,
        hybrid_backfilled=True,
        note="Same layout split as `size_bits`.",
    ),
    FactRow("RecordType", "fields", _FULL, _FULL),
    FactRow("RecordType", "bases", _FULL, _FULL),
    FactRow(
        "RecordType",
        "bases_fact",
        _FULL,
        _FULL,
        note=(
            "`Fact[list[str]]` sibling of `bases`. Both "
            "backends construct it directly (`model.record_layout_facts()`) "
            "alongside `bases` itself, opaque records included."
        ),
    ),
    FactRow("RecordType", "virtual_bases", _FULL, _FULL),
    FactRow(
        "RecordType",
        "virtual_bases_fact",
        _FULL,
        _FULL,
        note="`Fact[list[str]]` sibling of `virtual_bases` — see `bases_fact`.",
    ),
    FactRow(
        "RecordType",
        "vtable",
        _FULL,
        _FULL,
        note=(
            "clang's is a reconstruction over the AST "
            "(`extract/headers/clang/vtable.py`, G31 Phase C, schema v21) rather than a "
            "compiler-emitted table; castxml's comes from its own bundled "
            "compiler. Both are transitively inherited across bases."
        ),
    ),
    FactRow(
        "RecordType",
        "vtable_fact",
        _FULL,
        _FULL,
        note="`Fact[list[str]]` sibling of `vtable` — see `bases_fact`.",
    ),
    FactRow("RecordType", "source_location", _FULL, _FULL),
    FactRow("RecordType", "is_union", _FULL, _FULL),
    FactRow(
        "RecordType",
        "is_opaque",
        _FULL,
        _FULL,
        note=(
            "Closed in PR #719: `parse_types` previously skipped every "
            "non-definition record entirely (so an opaque handle type "
            "`struct A;` alone was *absent* from a clang snapshot rather than "
            "present with `is_opaque=True`, unlike castxml). Now emits an "
            "opaque stub for a forward-declaration-only identity, collapsing "
            "to the definition (never opaque) when both a forward decl and a "
            "definition share one identity in the same TU — verified against "
            "real clang 18 output that both land as separate "
            "`CXXRecordDecl`/`RecordDecl` nodes. No explicit hybrid backfill "
            "needed: castxml sees the identical header text, so its own real "
            "`is_opaque` already answers correctly for a type present on "
            "both backends; a clang-only opaque entity reaches a hybrid "
            "snapshot correctly via the ordinary clang-only-append path."
        ),
    ),
    FactRow("RecordType", "is_final", _FULL, _FULL),
    FactRow(
        "RecordType",
        "is_final_fact",
        _FULL,
        _FULL,
        note=(
            "`Fact[bool | None]` sibling of `is_final`. "
            "Same convention as `bases_fact`/`vtable_fact` above: both "
            "backends construct it directly, as an explicit kwarg "
            "(`Fact.present(is_final)`) rather than relying on "
            "`RecordType.__post_init__`'s generic legacy-value bridge, "
            "since `is_final` is always a concrete bool on the header-AST "
            "path (never a placeholder), matching `is_final`'s own row."
        ),
    ),
    FactRow(
        "RecordType",
        "is_template_pattern",
        _NONE,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "castxml emits template *instantiations*, never the uninstantiated "
            "pattern, so it has nothing to mark. OR-merged by the hybrid merge "
            "since PR #719 (a plain bool, not an Optional tri-state -- castxml's "
            "own `False` is never itself the backfill trigger the way a `None` "
            "is elsewhere in this table) -- verified against real castxml 0.6.3 "
            "+ clang 18 output that this is empirically inert for the current "
            "producer pair, since a clang template pattern never shares a "
            "type_map_key with any castxml-matched concrete type; a pattern "
            "reaches a hybrid snapshot only through the clang-only append path, "
            "which already preserves the flag on its own."
        ),
    ),
    FactRow(
        "RecordType",
        "has_anonymous_aggregate_fields",
        _NONE,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "clang flattens an anonymous struct/union into its parent and "
            "records that it did, which `dumper_layout_backfill.py` uses when "
            "matching DWARF fields. OR-merged by the hybrid merge since PR "
            "#719 (same plain-bool OR-merge as `is_template_pattern` above, "
            "not the `None`-check pattern) -- unlike that field this one is "
            "not provably inert: an opaque/incomplete castxml record could "
            "legitimately reach the merge with an empty `fields` list for a "
            "genuinely anonymous-aggregate-only record, where clang's `True` "
            "is the only signal available."
        ),
    ),
    FactRow("RecordType", "source_header", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "RecordType",
        "source_header_fact",
        _OTHER,
        _OTHER,
        note=(
            "`Fact[str | None]` sibling of `source_header`. "
            "Same non-header ownership as the legacy field — `provenance."
            "tag_provenance()` keeps both representations in sync via an "
            "explicit post-construction update (mirroring "
            "`resolve_vptr_offset_bits()`'s pattern), since it sets "
            "`source_header` by plain attribute assignment, which never "
            "re-runs `RecordType.__post_init__`'s bridge."
        ),
    ),
    FactRow("RecordType", "origin", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "RecordType",
        "data_size_bits",
        _NONE,
        _COMPANION,
        hybrid_backfilled=True,
        note=(
            "The tail-padding-excluded size. Neither backend's own parse "
            "computes it; it arrives from the layout companion tool or DWARF."
        ),
    ),
    FactRow(
        "RecordType",
        "data_size_bits_fact",
        _NONE,
        _NONE,
        note=(
            "`Fact[int | None]` sibling of `data_size_bits`. "
            "Unlike `bases_fact`/`vtable_fact`/`is_final_fact`, neither "
            "backend passes this keyword literally at `RecordType(...)` "
            "construction — it is correctly derived by the generic "
            "`bridge_legacy_and_fact` bridge in `__post_init__` from "
            "whatever legacy value the constructor call (or a later "
            "`replace_with_fact_sync` layout backfill — "
            "`dumper_layout_backfill.py`, `clang_layout_tool.py`) actually "
            "supplies, so `NONE` here reflects literal-keyword evidence, "
            "not runtime behavior (see fact_registry.py's own entry for "
            "this field's real, correct availability semantics)."
        ),
    ),
    FactRow(
        "RecordType",
        "is_standard_layout",
        _NONE,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "A semantic trait clang computes independent of any layout pass "
            "(`definitionData.isStandardLayout`, present only when true), and "
            "one castxml's schema genuinely does not expose. Wiring it in G31 "
            "Phase C activated `STANDARD_LAYOUT_LOST`, dead code until then."
        ),
    ),
    FactRow(
        "RecordType",
        "is_standard_layout_fact",
        _NONE,
        _NONE,
        note=(
            "`Fact[bool | None]` sibling of "
            "`is_standard_layout`. Same shape as `data_size_bits_fact` "
            "above — derived by the generic `__post_init__` bridge, not a "
            "literal constructor keyword, so `NONE` reflects scan evidence "
            "only."
        ),
    ),
    FactRow(
        "RecordType",
        "is_trivially_copyable",
        _PARTIAL,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "clang computes the trait; castxml derives it tri-state from the "
            "special members it emits (`extract/headers/castxml/"
            "record_traits.py`), unknown for a `= default` member. Activated "
            "`TRIVIALLY_COPYABLE_LOST`."
        ),
    ),
    FactRow(
        "RecordType",
        "is_trivially_copyable_fact",
        _NONE,
        _NONE,
        note="Same shape as `is_standard_layout_fact` — see that row's own note.",
    ),
    FactRow(
        "RecordType",
        "vptr_offset_bits",
        _PARTIAL,
        _PARTIAL,
        hybrid_backfilled=True,
        note=(
            "Both backends apply the same `0`-if-polymorphic heuristic — the "
            "Itanium primary-base rule — so neither tracks a secondary "
            "vtable's placement under multiple inheritance. Only DWARF reads "
            "the compiler's own artificial vptr member for a real offset."
        ),
    ),
    FactRow(
        "RecordType",
        "vptr_offset_bits_fact",
        _PARTIAL,
        _PARTIAL,
        note=(
            "`Fact[int | None]` sibling of `vptr_offset_bits`. "
            "Unlike `bases_fact`/`virtual_bases_fact`/`vtable_fact`, this one is "
            "`PARTIAL` on both backends, not `FULL` — `Fact.partial(...)`, not "
            "`Fact.present(...)` — matching `vptr_offset_bits`'s own row exactly: "
            "the wrapped value is still the `0`-if-polymorphic Itanium "
            "primary-base heuristic, which does not track a secondary vtable's "
            "placement under multiple inheritance, so the wrapper states that "
            "caveat rather than asserting full determination (a review round on "
            "the PR that added this row caught the earlier `FULL` claim "
            "contradicting the legacy field's own row one line above it)."
        ),
    ),
    FactRow(
        "RecordType",
        "base_offsets",
        _FULL,
        _COMPANION,
        hybrid_backfilled=True,
        note="Same layout split as `size_bits`.",
    ),
    FactRow("RecordType", "qualified_name", _FULL, _FULL),
    FactRow(
        "RecordType",
        "qualified_name_fact",
        _FULL,
        _FULL,
        note=(
            "`Fact[str | "
            "None]` sibling of `qualified_name`. Both backends construct "
            "it directly (`Fact.present(qualified_name)`), unlike "
            "`data_size_bits_fact`/`is_standard_layout_fact`/etc — "
            "`qualified_name`'s own `None` return is overwhelmingly a "
            "genuine, confirmed 'no enclosing scope' determination on "
            "both header-AST paths (a real cycle/depth-cap walk failure "
            "on the castxml side is a pathological, essentially "
            "unobserved edge case — see the construction site's own "
            "comment), so relying on the generic bridge's coarser "
            "None-means-omitted default would misreport confirmed "
            "evidence as not collected for the common case."
        ),
    ),
    FactRow(
        "RecordType",
        "is_abstract",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "clang side wired in G31 Phase C backend audit, from "
            "`definitionData.isAbstract` (`dumper_clang._clang_record_is_abstract`) "
            "— real semantic computation (an inherited-and-unoverridden pure "
            "virtual counts), not just a direct-declaration check."
        ),
    ),
    FactRow(
        "RecordType",
        "is_abstract_fact",
        _NONE,
        _NONE,
        note=(
            "`Fact[bool | None]` sibling of `is_abstract`. "
            "Deliberately NOT constructed as an explicit "
            "`Fact.present(is_abstract)` keyword the way `is_final_fact` is "
            "-- `is_abstract` is genuinely `None` on castxml for an opaque/"
            "incomplete record (no member list to judge from), so an "
            "explicit `Fact.present(None)` there would misreport a real "
            "not-collected case as a confirmed determination. The generic "
            "`__post_init__` bridge already gets this right (`None` legacy "
            "value -> `not_collected()`, a real bool -> `present(...)`), so "
            "`NONE` here reflects literal-keyword scan evidence only, not "
            "runtime behavior."
        ),
    ),
    FactRow(
        "RecordType",
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "RecordType",
        "deprecated_fact",
        _NONE,
        _NONE,
        note=("Same shape as Function.deprecated_fact -- see that row's own note."),
    ),
    FactRow(
        "RecordType",
        "specialization_declared_fact",
        _FULL,
        _NONE,
        note=(
            "Schema v61. castxml gives an implicit instantiation the primary "
            "template's location and an explicit/partial specialization its "
            "own, so the declaring header line says which one a `Box<double>` "
            "record is (`extract/headers/castxml/specialization.py`). The clang "
            "backend does not set it; NOT_COLLECTED keeps the existing "
            "removal path."
        ),
    ),
    FactRow(
        "RecordType",
        "template_param_defaults_fact",
        _NONE,
        _PARTIAL,
        note=(
            "Schema v62. clang records each type parameter's spelled default for "
            "a class-template pattern whose parameters are all type parameters "
            "(`extract/headers/clang/template_param_indexes.py`); a template with "
            "a non-type or template-template parameter stays NOT_COLLECTED. "
            "castxml emits no pattern record for an uninstantiated template, so "
            "it never sets this."
        ),
    ),
    FactRow(
        "RecordType",
        "entity_id",
        _FULL,
        _FULL,
        note=(
            "The parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
        ),
    ),
    FactRow(
        "RecordType",
        "ownership_fact",
        Capability.OTHER_LAYER,
        Capability.OTHER_LAYER,
        note=(
            "Owner/contract/rule id, stamped after the parse by "
            "`extract.ownership_stamp` from the declaring file -- neither "
            "backend's parser sets it, and it is identical for both. "
            "Persisted in `AbiSnapshot.extraction_scope`."
        ),
    ),
    # ── EnumType ───────────────────────────────────────────────────────────
    FactRow("EnumType", "name", _FULL, _FULL),
    FactRow("EnumType", "members", _FULL, _FULL),
    FactRow(
        "EnumType",
        "underlying_type",
        _FULL,
        _PARTIAL,
        note=(
            "clang reads `fixedUnderlyingType` — the REAL, correct value "
            'for a fixed enum (`enum E : short`) — but hard-codes `"int"` '
            "for an unfixed enum (Codex review, PR #719, follow-up), since "
            "clang's AST JSON exposes no compiler-selected-underlying-type "
            "fact for the unfixed case at all; the true value can differ "
            "(e.g. `unsigned int`, chosen from the member value range). "
            "castxml reads the `<Enumeration type=...>` id, which resolves "
            "to the real compiler-picked underlying integer type either way "
            "— fixed or implementation-chosen (verified against real "
            "castxml 0.6.3 output) — so it stays `_FULL`. Not "
            "hybrid-backfilled (see `hybrid_backfilled` above), but since "
            "castxml itself is now a real producer, hybrid inherits a real "
            "answer instead of the previous silent `int` default. No diff "
            "detector reads this field — `enum_underlying_size_changed` is "
            "computed from DWARF — but `tu_merge.py`'s ODR conflict check "
            "does."
        ),
    ),
    FactRow("EnumType", "source_location", _FULL, _FULL),
    FactRow("EnumType", "source_header", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "EnumType",
        "source_header_fact",
        _OTHER,
        _OTHER,
        note=(
            "Fact[str | None] sibling of "
            "source_header, mirroring RecordType.source_header_fact -- "
            "another layer (provenance.tag_provenance()) owns it, kept in "
            "sync explicitly since it sets source_header by attribute "
            "assignment, never re-running __post_init__."
        ),
    ),
    FactRow("EnumType", "origin", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "EnumType",
        "is_scoped",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "EnumType",
        "is_scoped_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[bool | None] sibling of "
            "is_scoped, guarded by the same clang_deprecation_facts_"
            "reliable flag its own conversion batch shares. NONE for the "
            "same bridge-derived reason as Function.deprecated_fact."
        ),
    ),
    FactRow(
        "EnumType",
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "EnumType",
        "deprecated_fact",
        _NONE,
        _NONE,
        note=("Same shape as Function.deprecated_fact -- see that row's own note."),
    ),
    FactRow("EnumType", "qualified_name", _FULL, _FULL),
    FactRow(
        "EnumType",
        "qualified_name_fact",
        _FULL,
        _FULL,
        note=(
            "Fact[str | None] sibling of "
            "qualified_name, mirroring RecordType.qualified_name_fact -- "
            "both backends construct it directly as "
            "Fact.present(qualified_name)."
        ),
    ),
    FactRow(
        "EnumType",
        "entity_id",
        _FULL,
        _FULL,
        note=(
            "The parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
        ),
    ),
    FactRow(
        "EnumType",
        "ownership_fact",
        Capability.OTHER_LAYER,
        Capability.OTHER_LAYER,
        note=(
            "Owner/contract/rule id, stamped after the parse by "
            "`extract.ownership_stamp` from the declaring file -- neither "
            "backend's parser sets it, and it is identical for both. "
            "Persisted in `AbiSnapshot.extraction_scope`."
        ),
    ),
    # ── Param ──────────────────────────────────────────────────────────────
    FactRow("Param", "name", _FULL, _FULL),
    FactRow("Param", "type", _FULL, _FULL),
    FactRow(
        "Param",
        "kind",
        _FULL,
        _FULL,
        note=(
            "Schema v45: the value/pointer/"
            "reference/rvalue-ref classification. castxml resolves it "
            "structurally from its own type graph "
            "(`extract/headers/castxml/type_resolution.top_level_param_kind`); "
            "clang has only the rendered `qualType` spelling to work from "
            "(`extract/headers/clang/param_kind.py`, same spelling-heuristic "
            "status as its sibling `pointer_depth`). Before this batch, "
            "neither backend populated it at all -- see "
            "`the stale 'param_kind' fact family (model.snapshot_reliability)`."
        ),
    ),
    FactRow(
        "Param",
        "kind_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[ParamKind] sibling of "
            "kind, bridge-derived on both backends the same way "
            "is_restrict_fact is; guarded by "
            "the stale 'param_kind' fact family (model.snapshot_reliability)."
        ),
    ),
    FactRow(
        "Param",
        "default",
        _FULL,
        _PARTIAL,
        note=(
            "Same representation split as `TypeField.default`: a real source "
            "expression from castxml, a placeholder/fingerprint from clang. "
            "Parameters are never merged field-by-field, so a hybrid snapshot "
            "carries castxml's `params` verbatim for any matched function."
        ),
    ),
    FactRow("Param", "pointer_depth", _FULL, _FULL),
    FactRow(
        "Param",
        "is_restrict",
        _FULL,
        _FULL,
        note=(
            "clang side wired in G31 Phase C (schema v22). castxml was the "
            "only producer before that, so a cross-backend comparison of "
            "unchanged headers reported `param_restrict_changed` for every "
            "`restrict` parameter."
        ),
    ),
    FactRow(
        "Param",
        "is_restrict_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[bool] sibling of "
            "is_restrict, bridge-derived on both backends the same way "
            "Variable.access_fact is; guarded by AbiSnapshot."
            "clang_restrict_facts_reliable."
        ),
    ),
    FactRow(
        "Param",
        "is_va_list",
        _NONE,
        _PARTIAL,
        note=(
            "clang side wired in G31 Phase C continued (schema v23), "
            "x86-64 System V spelling only — the one ABI verified there; "
            "an unrecognized target's real `va_list` still reads `False`. "
            "castxml has never populated this fact, so "
            '`diff_symbols._diff_param_va_list` only trusts a `"clang"`-'
            'producer pair — NOT `"hybrid"` either, unlike the now-'
            "symmetric `is_restrict` above, since a hybrid merge keeps "
            "castxml's own params verbatim for every matched function and "
            "castxml never populates this fact at all (see its own "
            "docstring)."
        ),
    ),
    FactRow(
        "Param",
        "is_va_list_fact",
        _NONE,
        _PARTIAL,
        note=(
            "`Fact[bool]` sibling of `is_va_list`, now "
            "constructed directly by both backends. castxml is `NONE`: it "
            "explicitly states `Fact.unsupported()` — a deliberate status, "
            "not a hardcoded default, but a status carrying no determined "
            "value is not extraction, the same distinction this matrix "
            "already draws for a hardcoded `False`/`None` legacy field (a "
            "review round on the PR that added this row caught the earlier "
            "`PARTIAL` claim conflating 'the wrapper is constructed' with "
            "'a fact was determined'). clang is `PARTIAL`, matching "
            "`is_va_list` above exactly: the wrapped value is the same "
            "x86-64-System-V-only determination, `Fact.present(bool)` "
            "around it doesn't change its precision."
        ),
    ),
    FactRow(
        "Function",
        "return_type_identities_fact",
        _FULL,
        _NONE,
        note=(
            "Schema v54: the qualified names of the records/enums this type slot resolves to, walked through castxml's own id-referenced type graph (`extract/headers/castxml/type_resolution.type_identities`). clang's `-ast-dump=json` gives a declaration's type only as a `qualType` string with no reference to the declaration it names, so there is nothing to record without a name-lookup heuristic; a clang slot stays NOT_COLLECTED and the exact public-surface walk falls back to the spelling-only answer (docs/contribute/known-gaps.md)."
        ),
    ),
    FactRow(
        "Param",
        "type_identities_fact",
        _FULL,
        _NONE,
        note=(
            "Schema v54: the qualified names of the records/enums this type slot resolves to, walked through castxml's own id-referenced type graph (`extract/headers/castxml/type_resolution.type_identities`). clang's `-ast-dump=json` gives a declaration's type only as a `qualType` string with no reference to the declaration it names, so there is nothing to record without a name-lookup heuristic; a clang slot stays NOT_COLLECTED and the exact public-surface walk falls back to the spelling-only answer (docs/contribute/known-gaps.md)."
        ),
    ),
    FactRow(
        "Variable",
        "type_identities_fact",
        _FULL,
        _NONE,
        note=(
            "Schema v54: the qualified names of the records/enums this type slot resolves to, walked through castxml's own id-referenced type graph (`extract/headers/castxml/type_resolution.type_identities`). clang's `-ast-dump=json` gives a declaration's type only as a `qualType` string with no reference to the declaration it names, so there is nothing to record without a name-lookup heuristic; a clang slot stays NOT_COLLECTED and the exact public-surface walk falls back to the spelling-only answer (docs/contribute/known-gaps.md)."
        ),
    ),
    FactRow(
        "TypeField",
        "type_identities_fact",
        _FULL,
        _NONE,
        note=(
            "Schema v54: the qualified names of the records/enums this type slot resolves to, walked through castxml's own id-referenced type graph (`extract/headers/castxml/type_resolution.type_identities`). clang's `-ast-dump=json` gives a declaration's type only as a `qualType` string with no reference to the declaration it names, so there is nothing to record without a name-lookup heuristic; a clang slot stays NOT_COLLECTED and the exact public-surface walk falls back to the spelling-only answer (docs/contribute/known-gaps.md)."
        ),
    ),
)
