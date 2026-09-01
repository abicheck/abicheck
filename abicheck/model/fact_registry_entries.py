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

"""The fact/capability registry's own entry data (ADR-063 D7, Phase 5) --
every registered ``FactDefinition``, split out of ``fact_registry.py``
once that module's own literal ``FACT_REGISTRY = FactRegistry([...])``
list grew past this repo's 800-line new-file cap (AI-readiness
``new-file-size`` gate). Mechanical extraction, unchanged content.

Imports its vocabulary from ``fact_registry_schema.py`` (not from
``fact_registry.py`` itself) so the dependency stays one-directional --
``fact_registry.py`` is a thin facade that imports ``FACT_REGISTRY`` back
from here and re-exports it, and a facade importing its own entries while
those entries import the facade would be a real cycle. Every existing
``from .fact_registry import FACT_REGISTRY`` call site is unaffected.
"""

from __future__ import annotations

from .fact_registry_schema import FactDefinition, FactLifecycle, FactRegistry

__all__ = ["FACT_REGISTRY"]

_E = FactDefinition

FACT_REGISTRY = FactRegistry(
    [
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
        _E(
            owner="Param",
            field="is_va_list",
            value_type="bool",
            producing_backends=("clang",),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "CastXML never populates this fact at all — its blanket "
                "False is unconditionally correct-as-not-collected, on "
                "any schema version. Guarded by "
                "AbiSnapshot.clang_va_list_facts_reliable for the clang "
                "producer only (deliberately excludes hybrid — see the "
                "field's own docstring)."
            ),
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
        # ── Phase 5's fourth batch: Variable's own case-(b) fields ─────────
        _E(
            owner="Variable",
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
                "mirroring RecordType.source_header_fact/EnumType."
                "source_header_fact exactly, DWARF/PDB producers included."
            ),
        ),
        _E(
            owner="Variable",
            field="alignment_bits",
            value_type="int | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Declared alignment in bits (explicit alignas/"
                "__attribute__((aligned)), else the type's natural "
                "alignment when a dumper can resolve it). None already "
                "unambiguously means 'not captured' — a plain case (b) "
                "conversion."
            ),
        ),
        _E(
            owner="Variable",
            field="elf_binding",
            value_type="SymbolBinding | None",
            producing_backends=("castxml", "clang", "elf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "ELF symbol linkage (st_info.bind), populated from "
                ".dynsym by dumper_elf_symbols._populate_elf_visibility -- "
                "a post-construction attribute assignment site kept in "
                "sync with elf_binding_fact explicitly (Codex-review-style "
                "mutation trap, same shape as tu_merge.py's/provenance.py's "
                "own fixes). Lists 'castxml'/'clang' alongside 'elf' since "
                "either header-backend-driven snapshot ends up carrying "
                "this fact once the elf pass runs, mirroring source_header/"
                "elf_visibility's own OTHER_LAYER convention. The decoded "
                "Fact[...].value is reconstructed as a real SymbolBinding "
                "member (storage/fact_codec.py's decode_variable_facts), "
                "not left as a bare JSON string, since existing readers "
                "(diff_symbols.py, diff_platform.py) unconditionally access "
                "'.value' on it."
            ),
        ),
        # ── Phase 5's fifth batch: Function's own ten case-(b) fields ──────
        _E(
            owner="Function",
            field="contract_attributes",
            value_type="list[str] | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Semantic contract attributes (nonnull, noreturn, format, "
                "alloc_size, malloc, returns_nonnull, warn_unused_result, "
                "sentinel, ...), normalized spellings. None already "
                "unambiguously means 'not captured' -- a plain case (b) "
                "conversion. dumper_hybrid.py's merge backfill and "
                "tu_merge.py's trivial-redeclaration merge both route "
                "their contract_attributes update through "
                "replace_with_fact_sync (not raw dataclasses.replace()) "
                "so this sibling cannot go stale (Codex-review-style "
                "mutation trap, same shape as elf_binding_fact's own fix)."
            ),
        ),
        _E(
            owner="Function",
            field="is_explicit",
            value_type="bool | None",
            producing_backends=("castxml", "clang", "dwarf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "explicit specifier on constructors/conversion operators. "
                "Tri-state like RecordType.is_final -- None already "
                "unambiguously means 'not captured'. Plain case (b) "
                "conversion. dwarf_snapshot.py reads DW_AT_explicit and "
                "passes a real bool too, so DWARF is a genuine producer "
                "(Codex review, fresh evidence)."
            ),
        ),
        _E(
            owner="Function",
            field="is_hidden_friend",
            value_type="bool | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes="Hidden-friend marker. Same shape as is_explicit -- plain case (b) conversion.",
        ),
        _E(
            owner="Function",
            field="is_variadic",
            value_type="bool | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes="C ellipsis (...) marker. Same shape as is_explicit -- plain case (b) conversion.",
        ),
        _E(
            owner="Function",
            field="is_override",
            value_type="bool | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Explicit C++11 override specifier. Same shape as "
                "is_explicit -- plain case (b) conversion. "
                "dumper_hybrid.py's own castxml/clang backfill for this "
                "field routes through replace_with_fact_sync (Codex-"
                "review-style mutation trap fix, see contract_attributes's "
                "own note)."
            ),
        ),
        _E(
            owner="Function",
            field="is_compiler_generated",
            value_type="bool | None",
            producing_backends=("castxml",),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "True when a declaration was never written by the user "
                "(compiler-synthesized implicit special member). Same "
                "shape as is_explicit -- plain case (b) conversion. "
                "clang is deliberately NOT listed (Codex review raised "
                "this; investigated and declined): extract/headers/clang/"
                "functions.py does construct every retained Function with "
                "is_compiler_generated=False, but backend_capabilities.py's "
                "own AST-verified matrix (test_matrix_claims_match_parser_"
                "source) draws a real, established, load-bearing "
                "distinction between a real extracted expression and a "
                "hardcoded literal -- clang's own AST walk skips "
                "`isImplicit` nodes entirely, so False here is structurally "
                "guaranteed, never derived per-declaration, matching that "
                "row's own NONE/clang claim. Listing 'clang' here would "
                "contradict that already-tested convention, not correct it."
            ),
        ),
        _E(
            owner="Function",
            field="hidden_friend_owner",
            value_type="str | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Qualified name of the class whose body declares this "
                "friend. Same shape as is_explicit -- plain case (b) "
                "conversion."
            ),
        ),
        _E(
            owner="Function",
            field="elf_binding",
            value_type="SymbolBinding | None",
            producing_backends=("castxml", "clang", "elf"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "ELF symbol linkage (st_info.bind), mirroring "
                "Variable.elf_binding_fact exactly -- populated from "
                ".dynsym by dumper_elf_symbols._populate_elf_visibility, "
                "a post-construction attribute assignment site kept in "
                "sync with elf_binding_fact explicitly. The decoded "
                "Fact[...].value is reconstructed as a real SymbolBinding "
                "member (storage/fact_codec.py's decode_function_facts), "
                "not left as a bare JSON string, since existing readers "
                "unconditionally access '.value' on it."
            ),
        ),
        _E(
            owner="Function",
            field="exception_spec",
            value_type="str | None",
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
            notes=(
                "Dynamic exception specification spelling. Same shape as "
                "is_explicit -- plain case (b) conversion."
            ),
        ),
        _E(
            owner="Function",
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
                "mirroring RecordType/EnumType/Variable.source_header_fact "
                "exactly, DWARF/PDB producers included."
            ),
        ),
    ]
)
