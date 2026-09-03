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


"""ADR-061 D9 taxonomy: source-evidence-level ChangeKind entries.

Facts sourced from the optional L4 (source-ABI replay) and L5 (semantic
source graph) evidence layers: public/private surface reconciliation
between source declarations and the binary's actual export table,
declaration identity (rename/move) tracking across the source graph,
reachability (call-graph, include-graph) facts, and template/inline-
function source-level facts that have no binary-symbol counterpart to
diff directly.

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in
``buildsource/source_diff.py``, ``buildsource/source_graph_findings.py``,
and ``buildsource/crosscheck.py``/``crosscheck_coherence.py`` (excluding
that pair's two build-context-reconciliation kinds, which are ``build.py``'s
-- see that module's own docstring) -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

SOURCE_ENTRIES: list[ChangeKindMeta] = [
    _E("api_depends_on_consumer_env", _R,
       impact="A public declaration is present under one consumer build "
              "configuration (compiler, language standard, macro set) "
              "and absent under another. Source that compiled on the "
              "library author's machine may not compile on the consumer's. "
              "Detected only when abicheck is given a probe matrix "
              "(snapshots taken under multiple configurations).",
       description_template="{detail} '{name}' is present in configurations {old} but absent in {new}. Consumers compiling under different toolchains see different public APIs."),
    _E("build_option_reaches_public_symbol", _R,
       impact="A changed ABI-relevant build option feeds a compile unit that "
              "produces an exported public symbol (per the build/source graph). "
              "It localizes a flag-drift risk to the public surface it can affect; "
              "a risk to review, never on its own an artifact-proven ABI break."),
    _E("call_graph_public_entry_reachability_changed", _C,
       impact="The set of implementation declarations statically reachable from "
              "an exported entry point changed (per the approximate Clang call "
              "graph). A quality/behavioral signal that the implementation behind "
              "a stable public symbol moved; never an ABI break on its own."),
    _E("concept_tightened", _A,
       impact="A public C++20 concept became more constrained; consumer templates or calls that satisfied the old constraint may no longer compile against the new headers.",
       description_template="Concept constraint tightened: {name}"),
    _E("constexpr_value_changed", _A,
       impact="The value of a public constexpr constant changed. Like a macro "
              "constant, the old value may be baked into consumer code; a "
              "source/API break until consumers are recompiled against the new "
              "headers."),
    _E("consumer_required_symbol_removed", _B,
       impact="A real consumer binary's own dynamic-symbol table (ELF undefined "
              "symbol / PE import / Mach-O undefined symbol, collected via "
              "--used-by, ADR-005/043) required this exact symbol from the "
              "library at load time — empirical ground truth independent of "
              "any header/namespace/visibility reasoning. The new library no "
              "longer exports it: the consumer's existing binary will fail to "
              "load, or crash the first time it calls the symbol, with no "
              "recompilation involved.",
       description_template="Consumer '{name}' requires symbol '{symbol}', which the new library no longer exports"),
    _E(
        "declaration_identity_reconciled",
        _R,
        impact="The L5 source graph reconciled an old and a new declaration/type "
        "node as the same real-world entity where both the qualified "
        "name and the declaring-file evidence changed together (a "
        "combined rename+move, or a canonical-id/alias match with no "
        "clean rename/move split). Without this reconciliation the "
        "change would show up as an unrelated remove-then-add pair in "
        "the graph diff. Informational: does not by itself indicate a "
        "break.",
    ),
    _E(
        "declaration_moved",
        _R,
        impact="The L5 source graph reconciled an old and a new declaration/type "
        "node as the same real-world entity that moved to a different "
        "declaring file (same qualified name, unambiguous evidence). "
        "Without this reconciliation the move would show up as an "
        "unrelated remove-then-add pair in the graph diff. Informational: "
        "does not by itself indicate a break.",
    ),
    _E(
        "declaration_renamed",
        _R,
        impact="The L5 source graph reconciled an old and a new declaration/type "
        "node as the same real-world entity under a new qualified name "
        "(same declaring file, unambiguous canonical-id/alias/structural "
        "evidence — never a bare short-name guess). Without this "
        "reconciliation the rename would show up as an unrelated "
        "remove-then-add pair in the graph diff. Informational: does not "
        "by itself indicate a break — any artifact-level finding for "
        "either spelling stands on its own evidence.",
    ),
    _E("default_argument_changed", _A,
       impact="A default argument of a public function changed (e.g. f(int x=1) "
              "to x=2). The signature is unchanged, so old binaries link, but "
              "newly compiled callers that omit the argument get a different "
              "value — a source-visible behavioral break. Build-context replay "
              "adds provenance over header-only detection."),
    _E("evidence_required_missing", _A,
       impact="A policy require_evidence layer (build context, source ABI, or "
              "source graph) was declared mandatory but is absent from this "
              "compare, so the run is failed rather than passing on a silently "
              "degraded scan (ADR-033 D7). Supply the missing evidence pack or "
              "relax the policy."),
    _E("exported_not_public", _R,
       impact="A symbol is exported by the binary but no public header declares it "
              "(EXPORT_ONLY provenance). It is reachable ABI surface that consumers "
              "can link against yet was never promised by the API, so it is easy to "
              "change or remove by accident — and equally a sign of a missing "
              "visibility annotation. Hide it (`-fvisibility=hidden` / a version "
              "script) or document it; respects the ABI-relevant-symbol filter and "
              "public-surface scoping so intentional internal exports can be "
              "suppressed."),
    _E("exported_symbol_source_owner_changed", _R,
       impact="An exported symbol present in both versions is now declared by a "
              "different file (the file owning its declaration moved in the source "
              "graph — e.g. a public declaration relocated to another header, or "
              "its declaring translation unit changed). The symbol name and "
              "signature are unchanged, so the artifact diff is quiet, but the "
              "declaration behind a stable public symbol moved — a refactor that "
              "can change consumers' include paths, inlining, or introduce an ODR "
              "risk if the old location still declares it. A source-graph risk to "
              "review, never on its own an artifact-proven ABI break."),
    _E("generated_header_changed", _R,
       impact="A generated public configuration header changed between versions. "
              "Generated headers encode build-time configuration into the public "
              "API surface, so a change can alter declarations or macro contracts "
              "seen by consumers. Policy may escalate to an API break; by default "
              "a risk to review."),
    _E("generated_header_reaches_public_api", _R,
       impact="A generated file newly participates in the public declaration "
              "closure (it is a public header, or it declares a reachable public "
              "entity). Build-time-generated content now shapes the public API "
              "surface, so its provenance and reproducibility warrant review."),
    _E(
        "identity_collision_detected",
        _R,
        impact="Two distinct declarations were linked onto the same L4 identity key "
        "(SourceEntity.identity(): the mangled name, else "
        "qualified_name#signature_hash, else the bare qualified name) — proven "
        "distinct because each carries a different clang-computed USR. The "
        "identity fallback chain accepts this rare collision by design for "
        "unmangled cross-scope declarations (ADR-041 P1 #5); when it happens, "
        "the two declarations were folded together in the linked surface, so "
        "any L4/L5 finding attributed to that identity may actually describe "
        "either one. A source-tooling-confidence risk, never an artifact-proven "
        "ABI break — no action is required unless a finding under that name "
        "looks wrong, in which case treat it as ambiguous between the two USRs "
        "named in the finding detail.",
    ),
    _E("include_graph_public_header_drift", _R,
       impact="The transitive include closure behind a public header changed "
              "(per the depfile/-M include graph). Consumers may now pull in "
              "different declarations or macros; a source/API risk to review, "
              "never on its own an artifact-proven ABI break."),
    _E("inline_body_changed", _R,
       impact="The body of a public inline function changed while no exported "
              "binary symbol changed. Callers that inlined the old body keep the "
              "old behavior until recompiled, so a mixed-build deployment can run "
              "two versions of the same function. A deployment/ODR risk, not a "
              "proven binary break."),
    _E("inline_function_removed", _A,
       impact="A public header-only inline function was removed. Because it was "
              "inline it had no exported binary symbol, so the artifact diff (L0) "
              "sees nothing; only source replay observes the lost declaration. "
              "Source that called the inline no longer compiles. A source/API "
              "break — keep a compatible declaration or move the removal behind a "
              "documented deprecation."),
    _E("internal_template_leaks_via_public_api", _B,
       impact="An internal-namespace function template (e.g. "
              "`acme::detail::__pattern_walk2<...>`) changed "
              "signature, and its instantiations appear in consumer "
              "symbol tables because public algorithms inline-dispatch "
              "through it. The internal helper is part of the effective "
              "public ABI — every consumer must be rebuilt. Function-"
              "template analogue of INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API.",
       description_template="Internal-namespace function template '{name}' has changed instantiations: {detail}. These mangled names participate in consumer symbol tables; every consumer must rebuild."),
    _E("internal_type_leaks_via_public_api", _B,
       impact="A type in an internal namespace (e.g. ::detail::, ::impl::, ::internal::) "
              "changed and is reachable from a public exported type or symbol "
              "(via inheritance, embedded-by-value field, or template argument). "
              "Although the type is conceptually 'internal', it is part of the "
              "effective public ABI: changes to it propagate into the layout, "
              "vtable, or compiled code of every consumer of the public type. "
              "Common in libraries that wrap implementation in a "
              "'detail' namespace (for example oneDAL)."),
    _E("odr_source_conflict", _R,
       impact="The same type name resolves to different definitions across "
              "translation units (One Definition Rule conflict). Linking or "
              "loading code that mixes the definitions is undefined behavior; a "
              "correctness risk surfaced by comparing per-TU source surfaces."),
    _E("odr_type_variant", _A,
       impact="One type has divergent definitions across translation units (the L4 "
              "source-replay surface recorded an ODR conflict: the same qualified name "
              "resolves to different per-TU layouts). Linking code that mixes the "
              "definitions is undefined behavior — a consumer compiled against one "
              "layout silently reads a struct laid out the other way. A source/API "
              "break surfaced from one merged snapshot's L4 evidence; never on its own "
              "an artifact-proven shipped-ABI break. Reconcile the conflicting "
              "definitions (usually a macro/flag that changes the type per TU)."),
    _E("private_header_leak", _R,
       impact="A public header exposes (and so transitively pulls in) a type declared "
              "only in a private / non-installed header — detected from declaration "
              "provenance (origin) and, when present, the L5 include graph. Downstream "
              "consumers that include the public header reference a declaration that is "
              "not shipped, so their build breaks once the private header is absent "
              "from the install tree — a packaging-hygiene risk. Make the public header "
              "self-contained or install the leaked header."),
    _E("public_api_internal_dependency_added", _R,
       impact="A public/exported entry point newly reaches an internal "
              "(non-public-header) declaration through the L5 source graph — a "
              "public API now calls or references an entity that lives only in a "
              "private header or source file, where it did not in the prior "
              "version. The public surface has taken on an undeclared dependency, "
              "so a later change to that internal entity becomes a hidden "
              "behavioral risk to the API. The version-over-version analogue of the "
              "intra-version public-to-internal cross-check; a risk to review, "
              "never on its own an artifact-proven ABI break."),
    _E("public_macro_removed", _A,
       impact="A macro that was part of the public header surface was removed. "
              "Macros never reach the binary, so no artifact layer can see the "
              "removal — only source replay does. Source that referenced the macro "
              "(a constant, a feature guard, or a function-like macro) no longer "
              "compiles. A source/API break; provide a replacement or a deprecation "
              "shim, or document the removal for consumers."),
    _E("public_macro_value_changed", _A,
       impact="The value of a macro constant in a public header changed (e.g. "
              "FOO_SIZE). Source that bakes the old value into compiled code "
              "(array sizes, switch labels, struct layout) silently mismatches a "
              "library built with the new value. A source/API break; recompile "
              "consumers against the new headers."),
    _E("public_not_exported", _R,
       impact="A public header declares an entity that promises an external symbol "
              "(an exported, non-inline, non-template, default-visibility function or "
              "variable) but the binary does not export it. Consumers that compile "
              "against the header get an undefined-symbol link error. Narrowly scoped "
              "to declarations with a real export obligation — inline/templated/"
              "constexpr/hidden-visibility decls are public source surface that "
              "legitimately emit no dynamic symbol and are excluded."),
    _E("public_reachability_changed", _R,
       impact="A public declaration entered or left the public-API reachability "
              "closure (target → public header → declaration → exported symbol) "
              "between versions. Explains and prioritizes impact derived from the "
              "source graph; never on its own decides an ABI break."),
    _E("public_surface_grew", _C,
       impact="The aggregate count of public declarations (functions, variables, "
              "types, enums) increased between versions. Informational only — the "
              "individual additions are reported separately; this is the net "
              "signal for CI dashboards and release notes. Emitted only with "
              "--surface-metrics.",
       description_template="public surface grew: {old} → {new} declarations (+{detail})"),
    _E("public_surface_shrank", _C,
       impact="The aggregate count of public declarations decreased between "
              "versions. Informational roll-up only — individual removals are "
              "reported (and may be breaking) on their own. Emitted only with "
              "--surface-metrics.",
       description_template="public surface shrank: {old} → {new} declarations ({detail})"),
    _E("public_to_internal_dependency", _R,
       impact="A public/exported declaration reaches an internal (non-public-header) "
              "entity through the L5 source graph — a public API calls, references, or "
              "embeds a type that lives only in a private header or source file. The "
              "public surface depends on a declaration consumers cannot see, so a "
              "change to that internal entity is an undeclared behavioral risk to the "
              "API. Elevated when the internal entity is among the revision's changed "
              "files. Explains and localizes risk from the source graph; never on its "
              "own an artifact-proven ABI break."),
    _E("public_typedef_removed", _A,
       impact="A public typedef/alias was removed from the headers. A bare typedef "
              "emits no symbol of its own, so the artifact diff is blind; source "
              "replay surfaces the removal. Consumer source that named the alias "
              "(variables, casts, template arguments) no longer compiles. A "
              "source/API break; retain the alias or provide a replacement name."),
    _E("public_typedef_target_changed", _A,
       impact="A public typedef/alias now resolves to a different underlying type "
              "(e.g. `typedef int32_t handle_t;` became `typedef int64_t "
              "handle_t;`). Source that relied on the old aliased type — overload "
              "resolution, template specialization, or the type's size in a "
              "consumer-owned struct — can change meaning or fail to compile. "
              "Surfaced by source replay because a bare typedef leaves no exported "
              "symbol of its own; a source/API break until consumers recompile."),
    _E("rtti_for_internal_type", _R,
       impact="The binary exports RTTI (typeinfo/vtable, `_ZTI`/`_ZTV`/`_ZTS`) for a "
              "polymorphic type that is declared only in a private / non-installed "
              "header. The type's run-time type information leaks onto the ABI surface "
              "even though consumers cannot name the type, which both bloats the export "
              "set and risks cross-module RTTI/`dynamic_cast` coupling to an internal "
              "class. Hide the internal type (anonymous namespace / "
              "`-fvisibility=hidden`) or stop exporting its typeinfo. A single-release "
              "hygiene risk, never on its own an artifact-proven ABI break."),
    _E("runtime_symbol_provider_changed", _R,
       impact="A consumer's reference to this symbol resolves to a different "
              "provider DSO than it did in the baseline environment — neither "
              "DSO's own export table necessarily changed, so a per-library ABI "
              "diff is silent. Caused by dependency reordering, a sibling "
              "library gaining/losing the export, or interposition drift. "
              "Whether this actually breaks the consumer depends on whether the "
              "new provider's signature is compatible; review the new "
              "provider's own diff for this symbol.",
       description_template="Runtime binding for '{symbol}' in consumer '{name}' moved from provider '{old}' to '{new}' between the baseline and candidate environments."),
    _E("runtime_weak_resolution_changed", _R,
       impact="A weak symbol reference's resolution status flipped between the "
              "baseline and candidate environments — a reference that used to "
              "resolve is now unresolved (acceptable at runtime for a weak ref, "
              "but the consumer loses the optional functionality it gated on "
              "it), or one that was unresolved now binds to a live "
              "implementation (the consumer's optional-feature code path "
              "activates for the first time).",
       description_template="Weak symbol '{symbol}' resolution for consumer '{name}' changed from '{old}' to '{new}' between the baseline and candidate environments."),
    _E("source_binary_provenance_mismatch", _R,
       impact="A large fraction of the source tree's public declarations fail to "
              "map to any exported binary symbol, which strongly suggests the "
              "source checkout does not correspond to the shipped binary (e.g. a "
              "wrong tag/commit). All L4/L5 source findings for this pair are then "
              "untrustworthy; re-check the source out at the binary's build tag. "
              "Per ADR-028 D3 this is a context risk, never a proven binary break."),
    _E("source_decl_binary_symbol_mismatch", _R,
       impact="A public source declaration no longer maps to an exported binary "
              "symbol — the declaration is present in the headers but absent from "
              "the library's exports. With artifact backing this escalates to the "
              "authoritative removed-export finding; on its own it is a "
              "surface/export consistency risk to investigate."),
    _E("source_fact_coverage_incomplete", _R,
       impact="The L4 source-fact evidence for this comparison is incomplete or "
              "produced by incompatible producers/fact-set versions — a mandatory "
              "fact family (functions, macros, templates, inline bodies, "
              "constexpr values, ...) was 'partial' or 'failed' on one or both "
              "sides, or the old/new fact-set version or producer differ. Per "
              "ADR-038 C.8, absence of another L4 finding must not be read as "
              "proof nothing changed in that family; treat this pair's other "
              "source-replay findings as unreliable until re-collected with a "
              "consistent, complete fact set."),
    _E(
        "source_surface_dso_mismatch",
        _R,
        impact="The linked L4 source surface carries reachable declarations but its "
        "decl->export linking matched none of the analyzed binary's exported "
        "symbols. The surface almost certainly describes a different or "
        "shared DSO (e.g. one surface folded from every target's sources and "
        "reused across libraries), so any L4/L5 finding attributed to this "
        "binary may be mis-scoped (AC-009). A source-tooling risk, never an "
        "artifact-proven ABI break: relink/rebuild the source surface "
        "per-DSO against this binary's own exports.",
    ),
    _E("source_to_binary_mapping_changed", _R,
       impact="A declaration present in both versions now maps to a different "
              "exported binary symbol (or its source↔symbol mapping changed) "
              "without a clear artifact ABI diff. A surface/mapping consistency "
              "risk to investigate, surfaced by comparing source graph summaries."),
    _E("suppression_reachability_unknown", _R,
       impact="A suppression rule using `reachability: proven-unreachable-only` "
              "matched this change, but it was not applied because graph "
              "coverage was insufficient to prove the change unreachable from "
              "the public ABI surface — the change stays in the report instead "
              "of being silently hidden by absence-of-evidence. Add "
              "`allow_unknown_reachability: true` to the rule to suppress it "
              "anyway once you have manually confirmed it is safe."),
    _E("suppression_would_hide_public_break", _R,
       impact="A namespace/source_location suppression rule matched this change, "
              "but it was not applied because the change is reachable from the "
              "public ABI surface (ADR-044) — suppressing it would hide a real "
              "break rather than internal noise. Review the finding; if the "
              "suppression is intentional even though the symbol is "
              "public-reachable, add `allow_public_break: true` to that rule."),
    _E("target_dependency_added", _R,
       impact="The library gained an inter-target build/link dependency (a new "
              "TARGET_DEPENDS_ON edge in the build graph). The shipped artifact may "
              "now require an additional library at load time, so a deployment that "
              "only shipped the old dependency set can fail to resolve at runtime, "
              "and the added dependency's own ABI now transitively affects "
              "consumers. A packaging/deployment risk to review; the artifact's "
              "DT_NEEDED diff proves any concrete new load-time dependency."),
    _E("template_body_changed", _R,
       impact="The implementation of an uninstantiated public template changed. "
              "No binary symbol exists to compare (the ADR-026 case122 residual), "
              "so this is invisible to artifact comparison; consumers that "
              "instantiate the template pick up the new body on recompile. A "
              "source-visible risk surfaced only by source replay."),
    _E("undocumented_export_ratio_increased", _C,
       impact="The fraction of exported symbols with no public-header declaration "
              "(EXPORT_ONLY origin) rose between versions — a packaging-hygiene "
              "regression: a symbol was exported without a corresponding public "
              "header. Informational; emitted only with --surface-metrics.",
       description_template="undocumented-export ratio rose: {old} → {new} (symbols exported without a public header)"),
    _E("uninstantiated_template_removed", _A,
       impact="A public template that was never instantiated into a binary symbol "
              "was removed from the headers. Source that instantiates it no longer "
              "compiles; there is no binary footprint, so only source replay sees "
              "it. A source/API break."),
    _E("unversioned_exported_symbol", _R,
       impact="The library defines a symbol-versioning scheme (a version script /"
              " .gnu.version_d table) yet exports this symbol without a version node. "
              "Unversioned exports cannot be evolved compatibly later — consumers bind "
              "to the bare name with no version guarantee, so a future versioned "
              "release silently changes what they resolve to. Add the symbol to the "
              "version script (or hide it if it is not public API). A single-release "
              "hygiene risk, never on its own an artifact-proven ABI break."),
]
