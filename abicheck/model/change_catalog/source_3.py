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
"""ADR-093 WS-1: entries of ``source.py`` split out at the 800-line cap.

Whole entries moved unchanged from ``source.py`` (each gains only its
``observable_at``). Registered in ``source.py``.
"""

from __future__ import annotations

from .observability import observable
from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

_OBS_B = observable(("B",))
_OBS_BH = observable(
    ("B", "H"),
    rationale="declared in the public header and present in the export table",
)
_OBS_H = observable(
    ("B", "H"), rationale="declaration-only fact: visible in the public header"
)
_OBS_BHSG = observable(
    ("B", "H", "S", "G"),
    rationale="call edges across translation units: needs sources and the graph",
)
_OBS_L5 = observable(("B", "H", "BD", "S", "G"), rationale="needs the source graph (G)")

SOURCE_ENTRIES_3: list[ChangeKindMeta] = [
    _E(
        "call_graph_public_entry_reachability_changed",
        _C,
        impact="The set of implementation declarations statically reachable from "
        "an exported entry point changed (per the approximate Clang call "
        "graph). A quality/behavioral signal that the implementation behind "
        "a stable public symbol moved; never an ABI break on its own.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_BHSG,),
    ),
    _E(
        "public_virtual_dispatch_set_changed",
        _R,
        impact="The set of override implementations a virtual call through a "
        "stable public virtual method may reach changed between versions (per "
        "the approximate source-graph dispatch join). A call through the same "
        "symbol can now land in different code; a source-graph risk to review, "
        "never on its own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "concept_tightened",
        _A,
        impact="A public C++20 concept became more constrained; consumer templates or calls that satisfied the old constraint may no longer compile against the new headers.",
        description_template="Concept constraint tightened: {name}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "concept_constraint_changed",
        _R,
        impact="A public C++20 concept's constraint changed, but the comparison could not prove it became stricter: it was relaxed, rewritten into a form that is not a superset of the old conjunctive requirements, or is not decomposable. A relaxed concept can still change overload resolution (subsumption), so consumers may select a different overload; no compile failure is proven.",
        description_template="Concept constraint changed: {name}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "constexpr_value_changed",
        _A,
        impact="The value of a public constexpr constant changed. Like a macro "
        "constant, the old value may be baked into consumer code; a "
        "source/API break until consumers are recompiled against the new "
        "headers.",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "default_argument_changed",
        _A,
        impact="A default argument of a public function changed (e.g. f(int x=1) "
        "to x=2). The signature is unchanged, so old binaries link, but "
        "newly compiled callers that omit the argument get a different "
        "value — a source-visible behavioral break. Build-context replay "
        "adds provenance over header-only detection.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "exported_not_public",
        _R,
        impact="A symbol is exported by the binary but no public header declares it "
        "(EXPORT_ONLY provenance). It is reachable ABI surface that consumers "
        "can link against yet was never promised by the API, so it is easy to "
        "change or remove by accident — and equally a sign of a missing "
        "visibility annotation. Hide it (`-fvisibility=hidden` / a version "
        "script) or document it; respects the ABI-relevant-symbol filter and "
        "public-surface scoping so intentional internal exports can be "
        "suppressed.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_BH,),
    ),
    _E(
        "inline_body_changed",
        _R,
        impact="The body of a public inline function changed while no exported "
        "binary symbol changed. Callers that inlined the old body keep the "
        "old behavior until recompiled, so a mixed-build deployment can run "
        "two versions of the same function. A deployment/ODR risk, not a "
        "proven binary break.",
        # FUNCTION, not SOURCE: `_diff_inline_bodies` iterates only
        # `reachable_inline_bodies` and uses the function's own qualified name
        # as the finding symbol, exactly like the sibling
        # `inline_function_removed` -- so `output.view show=functions` omitted a
        # function-level finding and the machine reports spelled the wrong
        # entity (Codex review, PR #1284). `SOURCE` describes the *evidence
        # layer* the finding came from, which is not the display dimension.
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "header_only_function_params_changed",
        _A,
        impact="The parameter list of a public header declaration changed, and "
        "both library builds confirm it has no exported symbol (an "
        "uninstantiated function template or a header-only inline): the "
        "library binary cannot break, but consumer source that calls it with "
        "an exact-type expectation (taking its address as a specific function "
        "pointer type, explicit template arguments, overload selection) may no "
        "longer compile, and consumer code compiled against the old header "
        "instantiates a different signature. A source/API break; it becomes a "
        "binary break only for an instantiation the library exports, which is "
        "reported as func_params_changed.",
        description_template="Parameters changed (no exported symbol on either side): {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "inline_function_removed",
        _A,
        impact="A public header-only inline function was removed. Because it was "
        "inline it had no exported binary symbol, so the artifact diff (L0) "
        "sees nothing; only source replay observes the lost declaration. "
        "Source that called the inline no longer compiles. A source/API "
        "break — keep a compatible declaration or move the removal behind a "
        "documented deprecation.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "internal_template_leaks_via_public_api",
        _B,
        impact="An internal-namespace function template (e.g. "
        "`acme::detail::__pattern_walk2<...>`) changed "
        "signature, and its instantiations appear in consumer "
        "symbol tables because public algorithms inline-dispatch "
        "through it. The internal helper is part of the effective "
        "public ABI — every consumer must be rebuilt. Function-"
        "template analogue of INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API.",
        description_template="Internal-namespace function template '{name}' has changed instantiations: {detail}. These mangled names participate in consumer symbol tables; every consumer must rebuild.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "internal_type_leaks_via_public_api",
        _B,
        impact="A type in an internal namespace (e.g. ::detail::, ::impl::, ::internal::) "
        "changed and is reachable from a public exported type or symbol "
        "(via inheritance, embedded-by-value field, or template argument). "
        "Although the type is conceptually 'internal', it is part of the "
        "effective public ABI: changes to it propagate into the layout, "
        "vtable, or compiled code of every consumer of the public type. "
        "Common in libraries that wrap implementation in a "
        "'detail' namespace (for example oneDAL).",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "private_header_leak",
        _R,
        impact="A public header exposes (and so transitively pulls in) a type declared "
        "only in a private / non-installed header — detected from declaration "
        "provenance (origin) and, when present, the L5 include graph. Downstream "
        "consumers that include the public header reference a declaration that is "
        "not shipped, so their build breaks once the private header is absent "
        "from the install tree — a packaging-hygiene risk. Make the public header "
        "self-contained or install the leaked header.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_api_internal_type_renamed",
        _R,
        impact="A public entry still reaches the same internal declaration/type under a new name (reconciled as one entity, already reached before): reported once instead of an add plus a remove; not an ABI break by itself.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "public_api_internal_dependency_added",
        _R,
        impact="A public/exported entry point newly reaches an internal "
        "(non-public-header) declaration through the L5 source graph — a "
        "public API now calls or references an entity that lives only in a "
        "private header or source file, where it did not in the prior "
        "version. The public surface has taken on an undeclared dependency, "
        "so a later change to that internal entity becomes a hidden "
        "behavioral risk to the API. The version-over-version analogue of the "
        "intra-version public-to-internal cross-check; a risk to review, "
        "never on its own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "public_dependency_role_changed",
        _R,
        impact="A public entry point still depends on the same entity in the L5 "
        "source graph, but uses it in a different role: a type that was a "
        "parameter is now the return type, a field type became an alias, a "
        "role was added beside an existing one. Callers that pass, receive, "
        "or embed that entity are affected differently than before. A risk "
        "to review; the artifact diff decides whether the declaration's "
        "signature or layout actually broke.",
        description_template="Public dependency role changed: {name}",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "public_dependency_role_unknown",
        _C,
        impact="One side's L5 source graph records a public entry's dependency "
        "without the role it plays (a graph stored by an abicheck version "
        "that did not stamp edge roles), so whether that dependency changed "
        "role (parameter, return, field, base, ...) cannot be confirmed or "
        "ruled out. A coverage note, not a change: re-collect the older "
        "graph with the current abicheck to compare roles.",
        description_template="Public dependency role not comparable: {name}",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "public_macro_removed",
        _A,
        impact="A macro that was part of the public header surface was removed. "
        "Macros never reach the binary, so no artifact layer can see the "
        "removal — only source replay does. Source that referenced the macro "
        "(a constant, a feature guard, or a function-like macro) no longer "
        "compiles. A source/API break; provide a replacement or a deprecation "
        "shim, or document the removal for consumers.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_macro_value_changed",
        _A,
        impact="The value of a macro constant in a public header changed (e.g. "
        "FOO_SIZE). Source that bakes the old value into compiled code "
        "(array sizes, switch labels, struct layout) silently mismatches a "
        "library built with the new value. A source/API break; recompile "
        "consumers against the new headers.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_not_exported",
        _R,
        impact="A public header declares an entity that promises an external symbol "
        "(an exported, non-inline, non-template, default-visibility function or "
        "variable) but the binary does not export it. Consumers that compile "
        "against the header get an undefined-symbol link error. Narrowly scoped "
        "to declarations with a real export obligation — inline/templated/"
        "constexpr/hidden-visibility decls are public source surface that "
        "legitimately emit no dynamic symbol and are excluded.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_BH,),
    ),
    _E(
        "public_surface_grew",
        _C,
        impact="The aggregate count of public declarations (functions, variables, "
        "types, enums) increased between versions. Informational only — the "
        "individual additions are reported separately; this is the net "
        "signal for CI dashboards and release notes. Computed "
        "unconditionally; "
        "--surface-metrics is accepted for compatibility but no longer "
        "changes whether this finding is emitted.",
        description_template="public surface grew: {old} → {new} declarations (+{detail})",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
        is_informational=True,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_surface_shrank",
        _C,
        impact="The aggregate count of public declarations decreased between "
        "versions. Informational roll-up only — individual removals are "
        "reported (and may be breaking) on their own. Computed "
        "unconditionally; "
        "--surface-metrics is accepted for compatibility but no longer "
        "changes whether this finding is emitted.",
        description_template="public surface shrank: {old} → {new} declarations ({detail})",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
        is_informational=True,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_to_internal_dependency",
        _R,
        impact="A public/exported declaration reaches an internal (non-public-header) "
        "entity through the L5 source graph — a public API calls, references, or "
        "embeds a type that lives only in a private header or source file. The "
        "public surface depends on a declaration consumers cannot see, so a "
        "change to that internal entity is an undeclared behavioral risk to the "
        "API. Elevated when the internal entity is among the revision's changed "
        "files. Explains and localizes risk from the source graph; never on its "
        "own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_L5,),
    ),
    _E(
        "public_typedef_removed",
        _A,
        impact="A public typedef/alias was removed from the headers. A bare typedef "
        "emits no symbol of its own, so the artifact diff is blind; source "
        "replay surfaces the removal. Consumer source that named the alias "
        "(variables, casts, template arguments) no longer compiles. A "
        "source/API break; retain the alias or provide a replacement name.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_typedef_target_changed",
        _A,
        impact="A public typedef/alias now resolves to a different underlying type "
        "(e.g. `typedef int32_t handle_t;` became `typedef int64_t "
        "handle_t;`). Source that relied on the old aliased type — overload "
        "resolution, template specialization, or the type's size in a "
        "consumer-owned struct — can change meaning or fail to compile. "
        "Surfaced by source replay because a bare typedef leaves no exported "
        "symbol of its own; a source/API break until consumers recompile.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "rtti_for_internal_type",
        _R,
        impact="The binary exports RTTI (typeinfo/vtable, `_ZTI`/`_ZTV`/`_ZTS`) for a "
        "polymorphic type that is declared only in a private / non-installed "
        "header. The type's run-time type information leaks onto the ABI surface "
        "even though consumers cannot name the type, which both bloats the export "
        "set and risks cross-module RTTI/`dynamic_cast` coupling to an internal "
        "class. Hide the internal type (anonymous namespace / "
        "`-fvisibility=hidden`) or stop exporting its typeinfo. A single-release "
        "hygiene risk, never on its own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_BH,),
    ),
    _E(
        "runtime_symbol_provider_changed",
        _R,
        impact="A consumer's reference to this symbol resolves to a different "
        "provider DSO than it did in the baseline environment — neither "
        "DSO's own export table necessarily changed, so a per-library ABI "
        "diff is silent. Caused by dependency reordering, a sibling "
        "library gaining/losing the export, or interposition drift. "
        "Whether this actually breaks the consumer depends on whether the "
        "new provider's signature is compatible; review the new "
        "provider's own diff for this symbol.",
        description_template="Runtime binding for '{symbol}' in consumer '{name}' moved from provider '{old}' to '{new}' between the baseline and candidate environments.",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "runtime_weak_resolution_changed",
        _R,
        impact="A weak symbol reference's resolution status flipped between the "
        "baseline and candidate environments — a reference that used to "
        "resolve is now unresolved (acceptable at runtime for a weak ref, "
        "but the consumer loses the optional functionality it gated on "
        "it), or one that was unresolved now binds to a live "
        "implementation (the consumer's optional-feature code path "
        "activates for the first time).",
        description_template="Weak symbol '{symbol}' resolution for consumer '{name}' changed from '{old}' to '{new}' between the baseline and candidate environments.",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "source_decl_binary_symbol_mismatch",
        _R,
        impact="A public source declaration no longer maps to an exported binary "
        "symbol — the declaration is present in the headers but absent from "
        "the library's exports. With artifact backing this escalates to the "
        "authoritative removed-export finding; on its own it is a "
        "surface/export consistency risk to investigate.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_BH,),
    ),
]
