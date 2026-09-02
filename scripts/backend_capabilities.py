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

"""backend_capabilities.py — the L2 header-backend x ABI-fact capability matrix.

G31 Phase D asks for "a backend capability matrix (CastXML vs. direct-clang
vs. hybrid, which facts/edges each can and cannot see)". This module is that
matrix's source of truth: a hand-curated, pure-stdlib data structure (the same
pattern as ``platform_capabilities.py``/``evidence_tiers.py``), rendered into
``docs/reference/header-backend-capabilities.md`` by
``scripts/gen_backend_capability_matrix.py``.

**Why the data lives here and not in prose.** A hand-typed table of "which
backend populates which field" is exactly the kind of documentation that goes
stale silently — the G31 plan records several rounds where a fact's real
backend coverage had moved on while a doc (or the plan itself) still described
the old world. So this module carries two halves that a test cross-checks
against each other:

1. :data:`FACT_ROWS` — the curated claim, one row per field of the six
   declaration dataclasses, with the reasoning a table cell can't hold.
2. :func:`scan_backend_evidence` — the *observed* answer, read straight out of
   ``dumper_castxml.py``/``dumper_clang.py`` (plus any entity-parsing modules
   ADR-061 D9 has since split out of either facade under
   ``extract/headers/{castxml,clang}/``) with :mod:`ast`: which keyword each
   backend actually passes when it constructs one of those dataclasses, and
   whether the value is a real expression or a hardcoded literal (a
   ``size_bits=None`` or ``is_opaque=False`` is *not* extraction).

``tests/test_backend_capability_matrix.py`` asserts the two agree, and that
every model field is covered exactly once — so a backend that starts (or stops)
populating a fact fails a test rather than quietly contradicting the published
matrix.

The hybrid column is **derived, never hand-typed** — see
:func:`hybrid_capability`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_DIR / "abicheck"

#: The six declaration dataclasses this matrix covers — the model types the two
#: header-AST backends construct. Everything else in ``AbiSnapshot`` is either
#: whole-snapshot metadata or a binary-format sub-model no header parse touches.
COVERED_MODEL_CLASSES: tuple[str, ...] = (
    "Function",
    "Variable",
    "TypeField",
    "RecordType",
    "EnumType",
    "Param",
)


class Capability(str, Enum):
    """What one backend can say about one fact."""

    #: The backend's own parse populates it.
    FULL = "full"
    #: Populated, but narrower/less precise than the sibling backend's value.
    #: The row's note says exactly how.
    PARTIAL = "partial"
    #: Populated only when the optional LibTooling companion tool
    #: (``ABICHECK_CLANG_LAYOUT_TOOL``, G28 Phase 4) is configured — the plain
    #: ``clang -ast-dump=json`` parse computes no record layout at all.
    COMPANION = "companion"
    #: Never populated by this backend; the field keeps its model default.
    NONE = "none"
    #: No header-AST backend populates it. Another layer owns the fact
    #: (DWARF, the ELF symbol table, or the post-parse provenance pass) — or,
    #: for a few fields, nothing does. The note says which.
    OTHER_LAYER = "other_layer"


#: Ranking used to derive the hybrid column. Only meaningful between the three
#: "this backend does populate it" values; the two absent states never win.
_RANK: dict[Capability, int] = {
    Capability.NONE: 0,
    Capability.COMPANION: 1,
    Capability.PARTIAL: 2,
    Capability.FULL: 3,
}


@dataclass(frozen=True)
class FactRow:
    """One model field's capability across the two header-AST backends."""

    owner: str  # one of COVERED_MODEL_CLASSES
    field: str  # the dataclass field name
    castxml: Capability
    clang: Capability
    #: True when ``dumper_hybrid.py`` explicitly backfills this field from the
    #: clang side of a merge. A hybrid snapshot is castxml-based, so a field
    #: NOT on that list keeps castxml's answer even when clang knows better —
    #: which is a real, documented consequence, not an oversight to paper over
    #: (see the generated "not inherited" note and the doc's Known gaps).
    hybrid_backfilled: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        """Reject a row that contradicts itself before it can be published."""
        if self.owner not in COVERED_MODEL_CLASSES:
            raise ValueError(f"unknown owner {self.owner!r}")
        other = Capability.OTHER_LAYER
        if (self.castxml is other) != (self.clang is other):
            raise ValueError(
                f"{self.owner}.{self.field}: OTHER_LAYER describes a fact no "
                "header backend populates, so it must hold for both columns"
            )
        if self.castxml is other and self.hybrid_backfilled:
            raise ValueError(
                f"{self.owner}.{self.field}: a non-header fact cannot be "
                "backfilled by the hybrid merge"
            )
        if not self.note and (self.castxml is not self.clang):
            raise ValueError(
                f"{self.owner}.{self.field}: a row whose two backends differ "
                "must explain the difference in its note"
            )

    @property
    def populated_by_any_backend(self) -> bool:
        """Whether a header parse carries this fact at all.

        False means another layer owns it (DWARF, the symbol table, the
        provenance pass) or nothing does — such a row is a real field of the
        dataclass, but not a *header-backend* capability, so the summary
        counts exclude it.
        """
        return Capability.OTHER_LAYER not in (self.castxml, self.clang)


def hybrid_capability(row: FactRow) -> Capability:
    """The ``--ast-frontend hybrid`` column, derived from the other two.

    ``dumper_hybrid.merge_snapshots`` is castxml-based by construction: it
    walks castxml's declarations and takes clang's value only for the specific
    facts its backfill lists name (``_backfill_function_facts``,
    ``_merge_variable``, ``_merge_field``, ``_merge_record_type``'s
    ``_LAYOUT_SCALAR_ATTRS``, ``_merge_enum_type``). So:

    - a backfilled field resolves to whichever backend knows more, since
      castxml wins when it has a value and clang fills in when it doesn't;
    - every other field keeps **castxml's** answer, even one clang populates
      and castxml doesn't.

    The second rule is the one worth reading twice: hybrid is not the union of
    the two backends. A clang-only fact that nothing backfills reaches a hybrid
    snapshot **only** for a declaration clang alone saw (those are appended
    verbatim); for a declaration both backends saw — the common case — it is
    dropped.
    """
    if not row.populated_by_any_backend:
        return Capability.OTHER_LAYER
    if row.hybrid_backfilled:
        return max((row.castxml, row.clang), key=lambda c: _RANK[c])
    return row.castxml


# ---------------------------------------------------------------------------
# The curated matrix
# ---------------------------------------------------------------------------

_FULL = Capability.FULL
_PARTIAL = Capability.PARTIAL
_COMPANION = Capability.COMPANION
_NONE = Capability.NONE
_OTHER = Capability.OTHER_LAYER

_PROVENANCE_PASS = (
    "Set after parsing by `provenance.apply_provenance()` from the "
    "public-header set (`-H`/`--header`, plus `scan --public-header-dir`), "
    "not by either backend."
)
_DYNSYM = "Read from the binary's own symbol table (`dumper_elf_symbols.py`)."

#: One row per field of every class in :data:`COVERED_MODEL_CLASSES`, in
#: declaration order. Verified against the two parsers by
#: :func:`scan_backend_evidence` (see this module's docstring).
FACT_ROWS: tuple[FactRow, ...] = (
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
            "(`dumper_hybrid._match_synthetic_ctor_dtor`)."
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
            "(`dumper_clang_vtable.py`), so slot-order findings need castxml."
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
    FactRow("Function", "is_inline", _FULL, _FULL),
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
            "ADR-063 Phase 5: Fact[bool | None] sibling of is_explicit. "
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
            "ADR-063 Phase 5 (fifth batch): Fact[str | None] sibling of "
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
        "elf_binding_fact",
        _OTHER,
        _OTHER,
        note=(
            "ADR-063 Phase 5 (fifth batch): Fact[SymbolBinding | None] "
            "sibling of elf_binding, mirroring Variable.elf_binding_fact "
            "exactly -- another layer "
            "(dumper_elf_symbols._populate_elf_visibility) owns it, kept "
            "in sync explicitly since it sets elf_binding by attribute "
            "assignment, never re-running __post_init__."
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
            "ADR-063 Phase 2: the parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
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
            "see `AbiSnapshot.castxml_var_access_facts_reliable`'s own "
            "docstring) and gates on that reliability flag for the "
            "pre-v24-legacy-baseline case."
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
            "ADR-063 Phase 5 (fourth batch): Fact[str | None] sibling of "
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
            "ADR-063 Phase 5 (fourth batch): Fact[int | None] sibling of "
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
    FactRow("Variable", "elf_binding", _OTHER, _OTHER, note=_DYNSYM),
    FactRow(
        "Variable",
        "elf_binding_fact",
        _OTHER,
        _OTHER,
        note=(
            "ADR-063 Phase 5 (fourth batch): Fact[SymbolBinding | None] "
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
            "ADR-063 Phase 2: the parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
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
            "ADR-063 Phase 5 (eighth batch): Fact[bool] sibling of is_const, "
            "and the phase's first case-(a) conversion. NONE for both "
            "backends because neither names the keyword: each passes the "
            "real is_const value and TypeField.__post_init__'s generic "
            "bridge derives Fact.present(...) from it -- the same honest "
            "reading this matrix already records for RecordType's own "
            "bridge-derived case-(b) siblings. Availability for this field "
            "is carried by AbiSnapshot.header_cv_facts_reliable, not by "
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
            "ADR-063 Phase 5 (eighth batch): Fact[str | None] sibling of "
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
            "guarded by AbiSnapshot.clang_deprecation_facts_reliable "
            "instead."
        ),
    ),
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
            "ADR-063 Phase 0: `Fact[list[str]]` sibling of `bases`. Both "
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
        note="ADR-063 Phase 0: `Fact[list[str]]` sibling of `virtual_bases` — see `bases_fact`.",
    ),
    FactRow(
        "RecordType",
        "vtable",
        _FULL,
        _FULL,
        note=(
            "clang's is a reconstruction over the AST "
            "(`dumper_clang_vtable.py`, G31 Phase C, schema v21) rather than a "
            "compiler-emitted table; castxml's comes from its own bundled "
            "compiler. Both are transitively inherited across bases."
        ),
    ),
    FactRow(
        "RecordType",
        "vtable_fact",
        _FULL,
        _FULL,
        note="ADR-063 Phase 0: `Fact[list[str]]` sibling of `vtable` — see `bases_fact`.",
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
            "ADR-063 Phase 5: `Fact[bool | None]` sibling of `is_final`. "
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
            "ADR-063 Phase 5: `Fact[str | None]` sibling of `source_header`. "
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
            "ADR-063 Phase 5: `Fact[int | None]` sibling of `data_size_bits`. "
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
            "ADR-063 Phase 5: `Fact[bool | None]` sibling of "
            "`is_standard_layout`. Same shape as `data_size_bits_fact` "
            "above — derived by the generic `__post_init__` bridge, not a "
            "literal constructor keyword, so `NONE` reflects scan evidence "
            "only."
        ),
    ),
    FactRow(
        "RecordType",
        "is_trivially_copyable",
        _NONE,
        _FULL,
        hybrid_backfilled=True,
        note="Same shape as `is_standard_layout`; activated `TRIVIALLY_COPYABLE_LOST`.",
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
            "ADR-063 Phase 0: `Fact[int | None]` sibling of `vptr_offset_bits`. "
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
            "ADR-063 Phase 5 (Codex review, second pass): `Fact[str | "
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
            "ADR-063 Phase 5: `Fact[bool | None]` sibling of `is_abstract`. "
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
        "entity_id",
        _FULL,
        _FULL,
        note=(
            "ADR-063 Phase 2: the parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
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
            "ADR-063 Phase 5 (third batch): Fact[str | None] sibling of "
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
        "deprecated",
        _FULL,
        _FULL,
        hybrid_backfilled=True,
        note="clang side wired in G31 Phase C (schema v19).",
    ),
    FactRow("EnumType", "qualified_name", _FULL, _FULL),
    FactRow(
        "EnumType",
        "qualified_name_fact",
        _FULL,
        _FULL,
        note=(
            "ADR-063 Phase 5 (third batch): Fact[str | None] sibling of "
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
            "ADR-063 Phase 2: the parse-time `model.identity.EntityId` carrier. "
            "Both backends resolve one from the typed scope path they record "
            "during their own walk. Runtime-only -- never serialized, so a "
            "reloaded snapshot carries None; a hybrid merge does not backfill "
            "it, so a hybrid snapshot keeps castxml's."
        ),
    ),
    # ── Param ──────────────────────────────────────────────────────────────
    FactRow("Param", "name", _FULL, _FULL),
    FactRow("Param", "type", _FULL, _FULL),
    FactRow(
        "Param",
        "kind",
        _OTHER,
        _OTHER,
        note=(
            "The value/pointer/reference/rvalue-ref classification is derived "
            "from DWARF type tags (`dwarf_snapshot.py`); a header-parsed "
            "parameter keeps the `value` default and is distinguished by "
            "`pointer_depth` and its type spelling instead."
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
            "ADR-063 Phase 0: `Fact[bool]` sibling of `is_va_list`, now "
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
)


# ---------------------------------------------------------------------------
# Observed evidence — what the two parsers actually do
# ---------------------------------------------------------------------------


class Evidence(str, Enum):
    """What a backend's source says about one field, structurally."""

    #: The backend passes this keyword with a real expression.
    EXTRACTED = "extracted"
    #: The keyword is passed, but as a hardcoded literal (``None``/``False``/
    #: ``[]``), which is a placeholder rather than an extracted fact.
    LITERAL = "literal"
    #: The backend never passes this keyword at all.
    ABSENT = "absent"


#: `Fact` factory methods that never carry a determined value — a call to one
#: of these states a fixed *status* regardless of what the parse actually saw,
#: so it is a placeholder exactly like a hardcoded ``None``/``False`` literal
#: would be, even though structurally it is an `ast.Call`, not an
#: `ast.Constant`. `present`/`partial` are the opposite: their value *is* the
#: extracted fact, so a call to one of those is examined by recursing into its
#: own first argument instead (see `_is_placeholder_literal`).
_FACT_STATUS_ONLY_METHODS = frozenset(
    {"unsupported", "not_collected", "not_applicable", "failed"}
)
_FACT_VALUE_METHODS = frozenset({"present", "partial"})


def _is_placeholder_literal(node: ast.expr) -> bool:
    """Whether a keyword's value is a hardcoded default rather than a parse.

    ``Param(is_restrict=False)`` names the field but extracts nothing, and is
    exactly what a claim of "this backend populates it" must not be built on.
    A constant, or an empty container, is that shape; anything else (a name, a
    call, a comprehension, a conditional) reads a real value off the AST —
    *except* a `Fact.unsupported()`-shaped call (`_FACT_STATUS_ONLY_METHODS`),
    which is a placeholder no matter how it's spelled, since it carries no
    value at all; and a `Fact.present(...)`/`Fact.partial(...)` call
    (`_FACT_VALUE_METHODS`), which is examined by recursing into its own
    wrapped value instead of being treated as extraction on the strength of
    merely being a call.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Fact"
    ):
        if node.func.attr in _FACT_STATUS_ONLY_METHODS:
            return True
        if node.func.attr in _FACT_VALUE_METHODS and node.args:
            return _is_placeholder_literal(node.args[0])
    return False


def scan_backend_evidence(
    module_paths: Path | Iterable[Path],
) -> dict[str, dict[str, Evidence]]:
    """``{class name: {field: Evidence}}`` for one backend's module(s).

    Reads the module(s) with :mod:`ast` rather than by regex: a keyword's
    value spans several lines often enough (``bases=[] if is_opaque else
    [...]``) that a line-oriented match reports the wrong answer — an early
    version of this scanner called both `bases` and `virtual_bases`
    hardcoded-empty on the castxml backend for exactly that reason.

    A single path scans one module; a backend that has started splitting
    entity parsing out of its flat facade (ADR-061 D9 — see
    ``abicheck/extract/AGENTS.md``) passes every module a constructor could
    now live in, since the facade's own AST no longer shows evidence for a
    fact an ``extract``-owned entity module now populates.

    A field constructed in more than one place counts as ``EXTRACTED`` if ANY
    construction extracts it (``dumper_castxml`` builds ``TypeField`` from two
    call sites) — this holds across files too, so a field's home moving from
    the facade to an entity module changes nothing about the merged verdict.

    **Constructor keywords only.** An earlier version also counted an
    attribute assignment (``field.access = ...``) as evidence, on the theory
    that a backend might finish a declaration after constructing it. Static
    analysis cannot tell *which* class such a target belongs to, so that
    evidence was applied to every covered class carrying a same-named field —
    real cross-class contamination (CodeRabbit review). It was also inert:
    removing it changes no verdict for either backend today. Should a backend
    start populating a field post-construction, this scanner reports the
    constructor's answer and the row's claim fails
    ``test_matrix_claims_match_parser_source`` — a loud, locatable failure
    rather than a silently wrong published matrix.
    """
    paths = [module_paths] if isinstance(module_paths, Path) else list(module_paths)
    out: dict[str, dict[str, Evidence]] = {c: {} for c in COVERED_MODEL_CLASSES}

    def record(cls: str, field: str, evidence: Evidence) -> None:
        """Keep the strongest evidence seen, so one placeholder among several
        construction sites (in one file or across files) cannot mask a real
        extraction elsewhere."""
        if out[cls].get(field) is not Evidence.EXTRACTED:
            out[cls][field] = evidence

    for module_path in paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in out
            ):
                for kw in node.keywords:
                    if kw.arg is None:
                        continue
                    record(
                        node.func.id,
                        kw.arg,
                        Evidence.LITERAL
                        if _is_placeholder_literal(kw.value)
                        else Evidence.EXTRACTED,
                    )
    return out


def _entity_module_paths(header_backend_dir: Path) -> list[Path]:
    """Every entity-parsing module already split out of a backend's facade.

    Sorted for deterministic scan order; excludes ``__init__.py`` (package
    marker, never a construction site) and non-parsing leaves that construct
    nothing (``context.py`` holds only per-parser state, not entity output —
    harmless either way, since a module with no covered-class constructor
    call contributes no evidence, but excluding it keeps this scanner's own
    intent — "entity modules" — honest).
    """
    if not header_backend_dir.is_dir():
        return []
    excluded = {"__init__.py", "context.py"}
    return sorted(p for p in header_backend_dir.glob("*.py") if p.name not in excluded)


def castxml_evidence() -> dict[str, dict[str, Evidence]]:
    """What ``dumper_castxml.py`` and its split-out entity modules populate."""
    return scan_backend_evidence(
        [PKG_DIR / "dumper_castxml.py"]
        + _entity_module_paths(PKG_DIR / "extract" / "headers" / "castxml")
    )


def clang_evidence() -> dict[str, dict[str, Evidence]]:
    """What ``dumper_clang.py`` and its split-out entity modules populate."""
    return scan_backend_evidence(
        [PKG_DIR / "dumper_clang.py"]
        + _entity_module_paths(PKG_DIR / "extract" / "headers" / "clang")
    )


def rows_for(owner: str) -> tuple[FactRow, ...]:
    """Every row describing one declaration dataclass, in declaration order."""
    return tuple(r for r in FACT_ROWS if r.owner == owner)
