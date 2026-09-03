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

"""Symbol-declaration fact entries for the registry (ADR-063 D7, Phase 5)
-- every ``FactDefinition`` whose owner is ``Function``, ``Variable`` or
``Param``. See ``fact_registry_entries_types.py`` for the split's own
rationale."""

from __future__ import annotations

from .fact_registry_schema import FactDefinition, FactLifecycle

__all__ = ["SYMBOL_FACTS"]

_E = FactDefinition

SYMBOL_FACTS: list[FactDefinition] = [
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
    # ── Phase 5's fourth batch: Variable's own case-(b) fields ─────────
    _E(
        owner="Variable",
        field="source_header",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Provenance (ADR-015, schema v6): defining header. Unlike "
            "RecordType.source_header_fact/EnumType.source_header_fact, "
            "dwarf/pdb are NOT producers here: dwarf_snapshot.py "
            "constructs every Variable without source_location (its "
            "RecordType/EnumType construction sites do set it), and no "
            "pdb module constructs a Variable at all, so "
            "apply_provenance()/tag_provenance() never has anything to "
            "derive source_header from on a debug-only snapshot -- the "
            "fact stays NOT_COLLECTED there (Codex review, PR #982)."
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
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Provenance (ADR-015, schema v6): defining header. Unlike "
            "RecordType.source_header_fact/EnumType.source_header_fact, "
            "dwarf/pdb are NOT producers here: dwarf_snapshot.py "
            "constructs every Function without source_location, and no "
            "pdb module constructs a Function at all, so "
            "apply_provenance()/tag_provenance() never has anything to "
            "derive source_header from on a debug-only snapshot -- the "
            "fact stays NOT_COLLECTED there (Codex review, PR #982)."
        ),
    ),
    _E(
        owner="Function",
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
        owner="Variable",
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
        owner="Param",
        field="is_restrict",
        value_type="bool",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the parameter is a restrict-qualified pointer. Case "
            "(a): a plain bool whose False cannot distinguish "
            "\"not restrict\" from \"never determined\" -- a pre-v22 clang "
            "snapshot reported False for every parameter, which "
            "AbiSnapshot.clang_restrict_facts_reliable is what marks."
        ),
    ),
    _E(
        owner="Variable",
        field="access",
        value_type="AccessLevel",
        producing_backends=("castxml",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "public/protected/private for a static class member. Case (a), "
            "and the one registered fact whose value type is neither a "
            "bool, a number, a string nor a list: AccessLevel.PUBLIC is "
            "both this field's resting value and a real answer, so only "
            "AbiSnapshot.castxml_var_access_facts_reliable can mark a "
            "pre-v24 castxml snapshot's blanket PUBLIC as a placeholder. "
            "Decoded back into a real AccessLevel member (storage/"
            "fact_codec.decode_variable_facts), the same reconstruction "
            "elf_binding_fact needs."
        ),
    ),
]
