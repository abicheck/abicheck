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

"""The curated L2 header-backend fact table (see ``backend_capabilities``).

Split out of ``backend_capabilities.py`` for the file-size gate. The
RecordType-and-later rows live in ``backend_capability_rows_records``.
"""

from __future__ import annotations

from backend_capability_model import (
    _COMPANION,
    _DYNSYM,
    _FULL,
    _NONE,
    _OTHER,
    _PARTIAL,
    _PROVENANCE_PASS,
    Capability,
    FactRow,
)
from backend_capability_rows_records import RECORD_AND_LATER_ROWS

LEADING_ROWS: tuple[FactRow, ...] = (
    # ── Function ───────────────────────────────────────────────────────────
    FactRow("Function", "name", _FULL, _FULL),
    FactRow(
        "Function",
        "mangled",
        _FULL,
        _FULL,
        note=(
            "castxml cannot always recover a real mangled name for a "
            "constructor/destructor and synthesizes a placeholder key; a "
            "hybrid merge reconciles those against clang's real Itanium name "
            "(`workflows.dump.hybrid_identity._match_synthetic_ctor_dtor`)."
        ),
    ),
    FactRow("Function", "return_type", _FULL, _FULL),
    FactRow("Function", "params", _FULL, _FULL),
    FactRow("Function", "visibility", _FULL, _FULL),
    FactRow("Function", "is_virtual", _FULL, _FULL),
    FactRow("Function", "is_noexcept", _FULL, _FULL),
    FactRow("Function", "is_extern_c", _FULL, _FULL),
    FactRow(
        "Function",
        "vtable_index",
        _FULL,
        _NONE,
        note=(
            "castxml numbers each virtual's slot; the clang backend passes "
            "`None` even though it reconstructs the vtable itself "
            "(`extract/headers/clang/vtable.py`), so slot-order findings need castxml."
        ),
    ),
    FactRow("Function", "source_location", _FULL, _FULL),
    FactRow("Function", "is_static", _FULL, _FULL),
    FactRow("Function", "is_const", _FULL, _FULL),
    FactRow("Function", "is_volatile", _FULL, _FULL),
    FactRow("Function", "is_pure_virtual", _FULL, _FULL),
    FactRow("Function", "is_deleted", _FULL, _FULL),
    FactRow(
        "Function",
        "deleted_from_dwarf",
        _OTHER,
        _OTHER,
        note="Set from DWARF's `DW_AT_deleted` (`dwarf_snapshot.py`).",
    ),
    FactRow(
        "Function",
        "is_inline",
        _FULL,
        _FULL,
        note=(
            "Inline linkage, explicit or implicit. castxml's frontend resolves "
            "implicit inline before emitting; clang's JSON emits its `inline` "
            "key only for the written keyword, so the clang backend "
            "reconstructs the implicit forms (`extract/headers/clang/"
            "inline_semantics.py`). This row read _FULL/_FULL while the two "
            "actually disagreed on every constexpr/in-class declaration."
        ),
    ),
    FactRow("Function", "access", _FULL, _FULL),
    FactRow("Function", "return_pointer_depth", _FULL, _FULL),
    FactRow("Function", "elf_visibility", _OTHER, _OTHER, note=_DYNSYM),
    FactRow("Function", "ref_qualifier", _FULL, _FULL),
    FactRow("Function", "is_explicit", _FULL, _FULL),
    FactRow(
        "Function",
        "is_explicit_fact",
        _FULL,
        _FULL,
        note=(
            "Fact[bool | None] sibling of is_explicit. "
            "Both backends now construct it directly as an explicit kwarg "
            "-- Fact.present(is_explicit) for a Constructor/Method/"
            "Converter (castxml) or CXXConstructorDecl/CXXConversionDecl "
            "(clang), Fact.not_applicable() otherwise -- since a kind "
            "where `explicit` is conceptually inapplicable is a confirmed "
            "non-gap, not missing evidence the generic bridge should read "
            "as NOT_COLLECTED (Codex review, PR #982)."
        ),
    ),
    FactRow("Function", "is_hidden_friend", _FULL, _FULL),
    FactRow(
        "Function",
        "is_hidden_friend_fact",
        _NONE,
        _NONE,
        note="Same shape as is_explicit_fact -- see that row's own note.",
    ),
    FactRow("Function", "source_header", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "Function",
        "source_header_fact",
        _OTHER,
        _OTHER,
        note=(
            "Fact[str | None] sibling of "
            "source_header, mirroring RecordType/EnumType/Variable."
            "source_header_fact exactly -- another layer "
            "(provenance.tag_provenance()) owns it."
        ),
    ),
    FactRow("Function", "origin", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow("Function", "is_variadic", _FULL, _FULL),
    FactRow(
        "Function",
        "is_variadic_fact",
        _NONE,
        _NONE,
        note="Same shape as is_explicit_fact -- see that row's own note.",
    ),
    FactRow("Function", "contract_attributes", _FULL, _FULL),
    FactRow(
        "Function",
        "contract_attributes_fact",
        _NONE,
        _NONE,
        note="Same shape as is_explicit_fact -- see that row's own note.",
    ),
    FactRow("Function", "exception_spec", _FULL, _FULL),
    FactRow(
        "Function",
        "exception_spec_fact",
        _NONE,
        _NONE,
        note="Same shape as is_explicit_fact -- see that row's own note.",
    ),
    FactRow(
        "Function",
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "Function",
        "deprecated_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[str | None] sibling of "
            "deprecated. NONE for both backends because neither names the "
            "keyword -- each passes the real value and the dataclass's own "
            "__post_init__ bridge derives the Fact, the same honest reading "
            "this matrix already records for every other bridge-derived "
            "sibling. Availability is carried by AbiSnapshot.clang_"
            "deprecation_facts_reliable."
        ),
    ),
    FactRow(
        "Function",
        "is_override",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note=(
            "clang side wired in G31 Phase C backend audit, from a real "
            "`OverrideAttr` child node (`dumper_clang._clang_method_is_override`)."
        ),
    ),
    FactRow(
        "Function",
        "is_override_fact",
        _FULL,
        _FULL,
        note=(
            "Same shape as is_explicit_fact above: both backends now "
            "construct it directly as Fact.present(is_override) for a "
            "kind in their own OVERRIDE_ELIGIBLE_KINDS set, "
            "Fact.not_applicable() otherwise (Codex review, PR #982)."
        ),
    ),
    FactRow("Function", "hidden_friend_owner", _FULL, _FULL),
    FactRow(
        "Function",
        "hidden_friend_owner_fact",
        _FULL,
        _FULL,
        note=(
            "Same shape as is_explicit_fact -- both backends now construct "
            "it directly as Fact.present(owner) for a real hidden friend, "
            "Fact.not_applicable() otherwise (Codex review, PR #982)."
        ),
    ),
    FactRow("Function", "elf_binding", _OTHER, _OTHER, note=_DYNSYM),
    FactRow(
        "Function",
        "declared_in_headers_fact",
        _OTHER,
        _OTHER,
        note=(
            'The three facts `Visibility` used to conflate, split (see `abicheck/model/surface_facts.py`): this one is "a header this run parsed declares this entity". Both header-AST backends assert it (`extract/surface_fact_producers.header_ast_surface_facts`); a producer with no header evidence leaves it unknown, never False.'
        ),
    ),
    FactRow(
        "Function",
        "in_public_contract_fact",
        _OTHER,
        _OTHER,
        note=(
            "\"Belongs to the promised public contract for this run's scope selection.\" Both backends assert it positively from a confirmed export; the stronger, scope-aware evidence is added by `provenance.tag_provenance` from a real public-header set, which is another layer's contribution, and a non-public origin is never written as a confirmed negative."
        ),
    ),
    FactRow(
        "Function",
        "binary_exported_fact",
        _OTHER,
        _OTHER,
        note=(
            '"The artifact\'s export table carries a symbol for this entity." Both backends resolve it from the same export-table lookup their `visibility` already used; unknown -- not False -- for a header-only dump, which has no export table to have been absent from.'
        ),
    ),
    FactRow(
        "Function",
        "elf_binding_fact",
        _OTHER,
        _OTHER,
        note=(
            "Fact[SymbolBinding | None] "
            "sibling of elf_binding, mirroring Variable.elf_binding_fact "
            "exactly -- another layer "
            "(dumper_elf_symbols._populate_elf_visibility) owns it, kept "
            "in sync explicitly since it sets elf_binding by attribute "
            "assignment, never re-running __post_init__."
        ),
    ),
    FactRow(
        "Function",
        "is_inline_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[bool] sibling of is_inline, bridge-derived on both backends the "
            "same way is_restrict_fact is; guarded by the stale 'inline' fact "
            "family (model.snapshot_reliability)."
        ),
    ),
    FactRow(
        "Function",
        "is_compiler_generated",
        _FULL,
        _NONE,
        note=(
            'castxml reads its own `artificial="1"` XML attribute (any '
            "function-like element, not just Constructor/Destructor). clang "
            "never derives this per-declaration -- it hardcodes False for "
            "every Function it emits, which is still a correct answer, not "
            "a gap: its own AST walk skips a node entirely whenever it is "
            "`isImplicit`, so a node reaching `parse_functions()` is "
            "structurally guaranteed to have been written by the user."
        ),
    ),
    FactRow(
        "Function",
        "is_compiler_generated_fact",
        _NONE,
        _NONE,
        note="Same shape as is_explicit_fact -- see that row's own note.",
    ),
    FactRow(
        "Function",
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
        "Function",
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
    # ── Variable ───────────────────────────────────────────────────────────
    FactRow("Variable", "name", _FULL, _FULL),
    FactRow("Variable", "mangled", _FULL, _FULL),
    FactRow("Variable", "type", _FULL, _FULL),
    FactRow("Variable", "visibility", _FULL, _FULL),
    FactRow("Variable", "source_location", _FULL, _FULL),
    FactRow("Variable", "is_const", _FULL, _FULL),
    FactRow(
        "Variable",
        "is_static",
        _FULL,
        _FULL,
        note=(
            "PR #1024 review: mirrors Function.is_static exactly (same "
            'castxml static="1" XML attribute / clang storageClass == '
            '"static" AST field) -- closes tu_merge._variable_key\'s own '
            'documented plain-C/extern "C" static-vs-external identity '
            "gap."
        ),
    ),
    FactRow(
        "Variable",
        "value",
        _PARTIAL,
        _NONE,
        note=(
            "castxml side wired in G31 Phase C continued (schema v24), "
            "restricted to `is_const` (mirroring `_iter_public_constants`'s "
            "own filter a few methods below — an unevaluated, non-const "
            "initializer can be an arbitrary runtime expression like "
            '`init="f()"`, verified empirically, which is not the '
            '"compile-time constant" this field\'s own docstring promises). '
            "No reliability flag needed: `diff_types_abicc_parity."
            "_diff_var_values` already declines per-pair unless BOTH sides "
            "are non-`None`, so a legacy blanket-`None` side is silently "
            "skipped rather than misread. A public constant's value ALSO "
            "reaches a snapshot through `AbiSnapshot.constants` — the two "
            "are independent paths, not a duplication of one another."
        ),
    ),
    FactRow(
        "Variable",
        "access",
        _FULL,
        _NONE,
        note=(
            "castxml side wired in G31 Phase C continued (schema v24), "
            "reusing the same structured `access` attribute already read "
            "for `Function`/`TypeField` — verified against real castxml "
            "output that a static class member's `<Variable>` element "
            "carries it too. `diff_symbols._diff_var_access` requires "
            '`ast_producer == "castxml"` specifically (not "hybrid" — '
            "see `the stale 'castxml_var_access' fact family (model.snapshot_reliability)`'s own "
            "docstring) and gates on that reliability flag for the "
            "pre-v24-legacy-baseline case."
        ),
    ),
    FactRow(
        "Variable",
        "access_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[AccessLevel] sibling of "
            "access. NONE for both backends because neither names the "
            "keyword -- each passes the real value and Variable."
            "__post_init__'s bridge derives the Fact. Availability is "
            "carried by the stale 'castxml_var_access' fact family (model.snapshot_reliability)."
        ),
    ),
    FactRow("Variable", "elf_visibility", _OTHER, _OTHER, note=_DYNSYM),
    FactRow("Variable", "source_header", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "Variable",
        "source_header_fact",
        _OTHER,
        _OTHER,
        note=(
            "Fact[str | None] sibling of "
            "source_header, mirroring RecordType.source_header_fact/"
            "EnumType.source_header_fact exactly -- another layer "
            "(provenance.tag_provenance()) owns it."
        ),
    ),
    FactRow("Variable", "origin", _OTHER, _OTHER, note=_PROVENANCE_PASS),
    FactRow(
        "Variable",
        "alignment_bits",
        _FULL,
        _PARTIAL,
        note=(
            "castxml reports the compiler-computed alignment for any variable; "
            "clang reads only an explicit `alignas`/`__attribute__((aligned))` "
            "override, never a type's natural alignment — a tracked gap that "
            "leaves `exported_object_alignment_reduced` without corroboration "
            "on a clang-only host (see `dumper_clang.py`'s module docstring)."
        ),
    ),
    FactRow(
        "Variable",
        "alignment_bits_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[int | None] sibling of "
            "alignment_bits. Deliberately NOT constructed as an explicit "
            "keyword the way qualified_name_fact is -- neither backend "
            "passes it literally at Variable(...) construction; it is "
            "correctly derived by the generic bridge_legacy_and_fact "
            "bridge in __post_init__ from whatever legacy value the "
            "constructor call actually supplies, so NONE here reflects "
            "literal-keyword evidence only, not runtime behavior (same "
            "shape as RecordType.data_size_bits_fact/is_abstract_fact)."
        ),
    ),
    FactRow(
        "Variable",
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "Variable",
        "deprecated_fact",
        _NONE,
        _NONE,
        note=("Same shape as Function.deprecated_fact -- see that row's own note."),
    ),
    FactRow("Variable", "elf_binding", _OTHER, _OTHER, note=_DYNSYM),
    FactRow(
        "Variable",
        "declared_in_headers_fact",
        _OTHER,
        _OTHER,
        note=(
            'The three facts `Visibility` used to conflate, split (see `abicheck/model/surface_facts.py`): this one is "a header this run parsed declares this entity". Both header-AST backends assert it (`extract/surface_fact_producers.header_ast_surface_facts`); a producer with no header evidence leaves it unknown, never False.'
        ),
    ),
    FactRow(
        "Variable",
        "in_public_contract_fact",
        _OTHER,
        _OTHER,
        note=(
            "\"Belongs to the promised public contract for this run's scope selection.\" Both backends assert it positively from a confirmed export; the stronger, scope-aware evidence is added by `provenance.tag_provenance` from a real public-header set, which is another layer's contribution, and a non-public origin is never written as a confirmed negative."
        ),
    ),
    FactRow(
        "Variable",
        "binary_exported_fact",
        _OTHER,
        _OTHER,
        note=(
            '"The artifact\'s export table carries a symbol for this entity." Both backends resolve it from the same export-table lookup their `visibility` already used; unknown -- not False -- for a header-only dump, which has no export table to have been absent from.'
        ),
    ),
    FactRow(
        "Variable",
        "elf_binding_fact",
        _OTHER,
        _OTHER,
        note=(
            "Fact[SymbolBinding | None] "
            "sibling of elf_binding -- another layer "
            "(dumper_elf_symbols._populate_elf_visibility) owns it, kept "
            "in sync explicitly since it sets elf_binding by attribute "
            "assignment, never re-running __post_init__."
        ),
    ),
    FactRow(
        "Variable",
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
        "Variable",
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
    # ── TypeField ──────────────────────────────────────────────────────────
    FactRow("TypeField", "name", _FULL, _FULL),
    FactRow("TypeField", "type", _FULL, _FULL),
    FactRow(
        "TypeField",
        "offset_bits",
        _FULL,
        _COMPANION,
        hybrid_backfilled=True,
        note=(
            "clang's JSON AST computes no record layout; the field offset "
            "arrives only from the optional layout companion tool, or from "
            "DWARF via `dumper_layout_backfill.py`."
        ),
    ),
    FactRow("TypeField", "is_bitfield", _FULL, _FULL),
    FactRow("TypeField", "bitfield_bits", _FULL, _FULL),
    FactRow("TypeField", "is_const", _FULL, _FULL),
    FactRow(
        "TypeField",
        "is_const_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[bool] sibling of is_const, "
            "and the phase's first case-(a) conversion. NONE for both "
            "backends because neither names the keyword: each passes the "
            "real is_const value and TypeField.__post_init__'s generic "
            "bridge derives Fact.present(...) from it -- the same honest "
            "reading this matrix already records for RecordType's own "
            "bridge-derived case-(b) siblings. Availability for this field "
            "is carried by the stale 'header_cv' fact family (model.snapshot_reliability), not by "
            "either backend's own construction."
        ),
    ),
    FactRow("TypeField", "is_volatile", _FULL, _FULL),
    FactRow(
        "TypeField",
        "is_volatile_fact",
        _NONE,
        _NONE,
        note="Same shape as is_const_fact -- see that row's own note.",
    ),
    FactRow("TypeField", "is_mutable", _FULL, _FULL),
    FactRow(
        "TypeField",
        "is_mutable_fact",
        _NONE,
        _NONE,
        note=(
            "Same shape as is_const_fact -- see that row's own note. DWARF "
            "(outside this matrix's own header-AST scope) states Fact."
            "unsupported() explicitly here: it has no DW_AT for `mutable` "
            "at all, so its blanket False is a producer limitation, not a "
            "determined fact."
        ),
    ),
    FactRow("TypeField", "access", _FULL, _FULL),
    FactRow(
        "TypeField",
        "default",
        _FULL,
        _PARTIAL,
        hybrid_backfilled=True,
        note=(
            "Both populate it (clang side in G31 Phase C, schema v20), but the "
            "VALUES are not cross-comparable: castxml keeps the verbatim "
            "source expression where clang falls back to a structural "
            "fingerprint, so the detector requires the same producer on both "
            "sides rather than merely a known one."
        ),
    ),
    FactRow(
        "TypeField",
        "default_fact",
        _NONE,
        _NONE,
        note=(
            "Fact[str | None] sibling of "
            "default. NONE for both backends for the same reason as "
            "is_const_fact -- each passes the real value and TypeField."
            "__post_init__'s bridge derives the Fact. Availability is "
            "carried by AbiSnapshot.clang_field_initializer_facts_"
            "reliable."
        ),
    ),
    FactRow(
        "TypeField",
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow(
        "TypeField",
        "deprecated_fact",
        _NONE,
        _NONE,
        note=(
            "Same shape as default_fact -- see that row's own note; "
            "guarded by the stale 'clang_deprecation' fact family (model.snapshot_reliability) "
            "instead."
        ),
    ),
)

#: One row per field of every class in :data:`COVERED_MODEL_CLASSES`, in
#: declaration order. Verified against the two parsers by
#: :func:`scan_backend_evidence` (see this module's docstring).
FACT_ROWS: tuple[FactRow, ...] = (*LEADING_ROWS, *RECORD_AND_LATER_ROWS)
