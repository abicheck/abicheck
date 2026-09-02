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

"""User-defined-type fact entries for the registry (ADR-063 D7, Phase 5)
-- every ``FactDefinition`` whose owner is ``RecordType``, ``TypeField``
or ``EnumType``.

One of three sibling entry modules ``fact_registry_entries.py`` assembles
``FACT_REGISTRY`` from; the split is by *owner family*, mirroring
``model/change_catalog/``'s own types/symbols/platform division, and was
forced by ADR-061's 800-line per-module ceiling once Phase 5's field-by-
field conversion filled the single list. Content is unchanged by the
split. Imports only ``fact_registry_schema.py``, so no cycle is possible."""

from __future__ import annotations

from .fact_registry_schema import FactDefinition, FactLifecycle

__all__ = ["TYPE_FACTS"]

_E = FactDefinition

TYPE_FACTS: list[FactDefinition] = [
    # ── Phase 0's original four RecordType layout facts ────────────
    _E(
        owner="RecordType",
        field="bases",
        value_type="list[str]",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Base class names, declaration order. No independent "
            "reliability flag ever guarded this field (unlike vtable/"
            "vptr_offset_bits below) — see AGENTS.md's "
            "type_base_changed entry for the accepted residual gap."
        ),
    ),
    _E(
        owner="RecordType",
        field="virtual_bases",
        value_type="list[str]",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
    ),
    _E(
        owner="RecordType",
        field="vtable",
        value_type="list[str]",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes="Guarded by AbiSnapshot.clang_vtable_facts_reliable for the clang producer.",
    ),
    _E(
        owner="RecordType",
        field="vptr_offset_bits",
        value_type="int | None",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes="Guarded by AbiSnapshot.clang_vtable_facts_reliable for the clang producer.",
    ),
    # ── Phase 5's first worked-example conversion ───────────────────
    _E(
        owner="RecordType",
        field="is_final",
        value_type="bool | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "`final` class-key specifier. No separate reliability "
            "flag: the field's own None already unambiguously means "
            "'dumper/loader could not determine' (DWARF/symbols-only "
            "mode, older snapshots) — a plain case (b) conversion."
        ),
    ),
    # ── Phase 5's second batch: RecordType's remaining case-(b) fields ──
    _E(
        owner="RecordType",
        field="is_abstract",
        value_type="bool | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Declares >=1 pure virtual function. Tri-state like "
            "is_final; None already unambiguously means 'dumper/loader "
            "could not determine' — a plain case (b) conversion."
        ),
    ),
    _E(
        owner="RecordType",
        field="data_size_bits",
        value_type="int | None",
        producing_backends=("clang",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Itanium 'data size' (dsize/nvsize), excluding trailing "
            "tail padding. Neither castxml's own parse nor DWARF "
            "computes this (dwarf_snapshot.py deliberately leaves it "
            "None — no sound DWARF-only signal, see that module's own "
            "comment); it arrives from the layout companion tool under "
            "clang (backend_capabilities.py's COMPANION row) — a plain "
            "case (b) conversion."
        ),
    ),
    _E(
        owner="RecordType",
        field="is_standard_layout",
        value_type="bool | None",
        producing_backends=("clang",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Governs tail-padding reuse. castxml's schema does not "
            "expose this trait at all, and DWARF has no sound signal "
            "for it either (dwarf_snapshot.py's own comment) — clang "
            "only. Plain case (b) conversion."
        ),
    ),
    _E(
        owner="RecordType",
        field="is_trivially_copyable",
        value_type="bool | None",
        producing_backends=("clang",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Governs how the type is passed by value. Same shape as "
            "is_standard_layout — clang only. Plain case (b) "
            "conversion."
        ),
    ),
    _E(
        owner="RecordType",
        field="qualified_name",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=True,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Namespace/enclosing-class-qualified spelling. None at "
            "global scope or when the dumper couldn't determine it. "
            "The DWARF backend folds the qualified spelling into "
            "RecordType.name itself (dwarf_snapshot.py) rather than "
            "populating this separate field, so it is not a producer "
            "here. Unlike the batch's other five fields, both header "
            "backends construct qualified_name_fact explicitly as "
            "Fact.present(...) rather than relying on the generic "
            "bridge (Codex review, second pass) — a None return is "
            "overwhelmingly a genuine, confirmed 'no enclosing scope' "
            "determination on both paths, not an absence of evidence. "
            "identity_relevant=True (Codex review, fresh evidence): "
            "tu_merge.merge_translation_units keys a record by "
            "`rt.qualified_name or rt.name`, so this field is part of "
            "the merge/matching identity, not just a display detail."
        ),
    ),
    _E(
        owner="RecordType",
        field="source_header",
        value_type="str | None",
        producing_backends=("castxml", "clang", "dwarf", "pdb"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Provenance (ADR-015, schema v6): defining header. "
            "provenance.apply_provenance()/tag_provenance() derive it "
            "unconditionally from source_location for any declaration, "
            "so a DWARF (DW_AT_decl_file, dwarf_snapshot.py) or PDB "
            "(pdb_model.py) producer populates it too, not only the two "
            "header-AST backends (Codex review, fresh evidence)."
        ),
    ),
    # ── Phase 5's third batch: EnumType's own case-(b) fields ──────────
    _E(
        owner="EnumType",
        field="qualified_name",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=True,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Namespace/enclosing-class-qualified spelling, mirroring "
            "RecordType.qualified_name_fact exactly (same fields, same "
            "explicit Fact.present(...) construction on both header-AST "
            "backends, same rationale). identity_relevant=True (Codex "
            "review, fresh evidence): tu_merge.merge_translation_units "
            "keys an enum by `en.qualified_name or en.name`."
        ),
    ),
    _E(
        owner="EnumType",
        field="source_header",
        value_type="str | None",
        producing_backends=("castxml", "clang", "dwarf", "pdb"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Provenance (ADR-015, schema v6): defining header, "
            "mirroring RecordType.source_header_fact exactly, DWARF/PDB "
            "producers included (Codex review, fresh evidence)."
        ),
    ),
    _E(
        owner="TypeField",
        field="is_const",
        value_type="bool",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the member is const-qualified. Case (a): guarded by "
            "AbiSnapshot.header_cv_facts_reliable, whose False marks a "
            "pre-fix castxml snapshot's blanket False values as "
            "placeholders rather than facts -- the plain bool cannot "
            "distinguish those from a genuinely unqualified member. "
            "DWARF resolves it from the member's own const/volatile "
            "type DIE."
        ),
    ),
    _E(
        owner="TypeField",
        field="is_volatile",
        value_type="bool",
        producing_backends=("castxml", "clang", "dwarf"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the member is volatile-qualified. Case (a): guarded by "
            "AbiSnapshot.header_cv_facts_reliable, whose False marks a "
            "pre-fix castxml snapshot's blanket False values as "
            "placeholders rather than facts -- the plain bool cannot "
            "distinguish those from a genuinely unqualified member. "
            "DWARF resolves it from the member's own const/volatile "
            "type DIE."
        ),
    ),
    _E(
        owner="TypeField",
        field="is_mutable",
        value_type="bool",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the member is declared `mutable`. Case (a): guarded "
            "by AbiSnapshot.header_cv_facts_reliable, whose False "
            "marks a pre-fix castxml snapshot's blanket False values "
            "as placeholders rather than facts. DWARF has no DW_AT for "
            "`mutable` at all and states Fact.unsupported() explicitly "
            "(dwarf_snapshot.py), so it is not a producer here."
        ),
    ),
    _E(
        owner="TypeField",
        field="default",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Default member initializer expression, verbatim. Case (a) "
            "even though the field is already `str | None`: None is a "
            'real value here ("this member has no initializer"), so '
            "availability is carried by AbiSnapshot.clang_field_"
            "initializer_facts_reliable, whose False marks a pre-v20 "
            "clang snapshot's blanket None as a placeholder."
        ),
    ),
    _E(
        owner="TypeField",
        field="deprecated",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "[[deprecated]] message string. Case (a) for the same "
            'reason as `default` above -- None means "not '
            'deprecated" as well as "not captured" -- guarded by '
            "AbiSnapshot.clang_deprecation_facts_reliable, the flag "
            "that also covers every other surface kind's own "
            "`deprecated` field."
        ),
    ),
    _E(
        owner="RecordType",
        field="deprecated",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "[[deprecated]] message string. Case (a): None is a real "
            'value here ("not deprecated"), so availability is carried '
            "by AbiSnapshot.clang_deprecation_facts_reliable -- whose "
            "False marks a pre-v19 clang snapshot's blanket None as a "
            "placeholder -- not by the value. Shares that flag with "
            "every other surface kind's own `deprecated` and with "
            "EnumType.is_scoped."
        ),
    ),
    _E(
        owner="EnumType",
        field="deprecated",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "[[deprecated]] message string. Case (a): None is a real "
            'value here ("not deprecated"), so availability is carried '
            "by AbiSnapshot.clang_deprecation_facts_reliable -- whose "
            "False marks a pre-v19 clang snapshot's blanket None as a "
            "placeholder -- not by the value. Shares that flag with "
            "every other surface kind's own `deprecated` and with "
            "EnumType.is_scoped."
        ),
    ),
    _E(
        owner="EnumType",
        field="is_scoped",
        value_type="bool | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "`enum class`/`enum struct` versus a plain C enum. Case "
            "(a) even though the field is already tri-state: a pre-v19 "
            "clang snapshot reported a blanket False for every enum, "
            "which no value can distinguish from a genuine unscoped "
            "one -- AbiSnapshot.clang_deprecation_facts_reliable (the "
            "same flag, since both facts landed together) is what "
            "says so."
        ),
    ),
]
