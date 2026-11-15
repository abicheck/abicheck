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
and ``buildsource/cross_source_checks.py``/``cross_source_checks_coherence.py`` (excluding
that pair's two build-context-reconciliation kinds, which are ``build.py``'s
-- see that module's own docstring) -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)
from .source_2 import SOURCE_ENTRIES_2
from .source_3 import SOURCE_ENTRIES_3

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

SOURCE_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "api_depends_on_consumer_env",
        _R,
        impact="A public declaration is present under one consumer build "
        "configuration (compiler, language standard, macro set) "
        "and absent under another. Source that compiled on the "
        "library author's machine may not compile on the consumer's. "
        "Detected only when abicheck is given a probe matrix "
        "(snapshots taken under multiple configurations).",
        description_template="{detail} '{name}' is present in configurations {old} but absent in {new}. Consumers compiling under different toolchains see different public APIs.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "build_option_reaches_public_symbol",
        _R,
        impact="A changed ABI-relevant build option feeds a compile unit that "
        "produces an exported public symbol (per the build/source graph). "
        "It localizes a flag-drift risk to the public surface it can affect; "
        "a risk to review, never on its own an artifact-proven ABI break.",
        entity=_ENT.BUILD,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
    ),
    _E(
        "declaration_coordinates_shifted",
        _C,
        impact="The L5 source graph matched an old and a new declaration/type "
        "node as the same real-world entity where, after "
        "closure_location_free_identity normalization, NEITHER the "
        "qualified name NOR the declaring-file evidence changed -- the "
        "structurally opposite case from declaration_identity_reconciled "
        "(which requires both to have changed together). What matched "
        "them is pure :line:col-coordinate churn (or an equivalent "
        "no-op attribute difference), never a rename or a move. "
        "Deliberately COMPATIBLE, not RISK: from the model's own "
        "normalized-identity point of view the declaration did not "
        "change at all, so scoring it as risk would inflate "
        "risk_changes for entities the reconciliation itself proves are "
        "unchanged. Still recorded (never dropped) per this repo's "
        "record-before-disposing rule -- it explains what would "
        "otherwise look like an unrelated remove-then-add pair in the "
        "graph diff, the same way its RISK-tier siblings do.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        is_informational=True,
    ),
    _E(
        "declaration_identity_reconciled_unresolved",
        _R,
        impact="The L5 source graph matched an old and a new declaration/type "
        "node as the same real-world entity, and SOME identity evidence "
        "differs -- but not in a way the classifier can attribute to "
        "either dimension: an ambiguous marker basename, a reordering of "
        "template arguments, a declaring file contradicting its own "
        "markers, a differing normalized signature tail, or a name "
        "present on one side only. It is neither a rename (the "
        "location-free names agree), nor a move (no two-sided "
        "declaring-file difference, and the marker fallback was "
        "unusable), nor a pure coordinate shift, nor unchanged. "
        "Deliberately its own kind rather than the combined "
        "declaration_identity_reconciled: that kind's claim is that both "
        "the name AND the declaring-file evidence changed together, and "
        "asserting it here would re-make in the catalog exactly the "
        "claim the classifier declined to make. RISK-tier like its "
        "reconciliation siblings -- an unresolved difference is weaker "
        "evidence, which narrows the conclusion; it does not license a "
        "clean one. Informational: does not by itself indicate a break.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "declaration_identity_unchanged",
        _C,
        impact="The L5 source graph matched an old and a new declaration/type "
        "node as the same real-world entity through non-name evidence "
        "(structural context, canonical id, or an alias) where NO "
        "identity dimension differs at all: the qualified name, the "
        "declaring-file evidence, and the normalized signature tail are "
        "each equal on both sides, and no embedded marker differs or "
        "was reordered. Strictly less of a change than "
        "declaration_coordinates_shifted, which at least has a "
        ":line:col shift to report -- so it is COMPATIBLE for the same "
        "reason and more so: there is nothing about the identity for a "
        "consumer to act on, and scoring it as risk would inflate "
        "risk_changes with pairs the reconciliation itself proves "
        "identical. It exists because the alternative was falling "
        "through to declaration_identity_reconciled, whose claim (both "
        "the name and the declaring-file evidence changed together) is "
        "false for every pair in this population. Still recorded, never "
        "dropped, per this repo's record-before-disposing rule -- it "
        "explains what would otherwise look like an unrelated "
        "remove-then-add pair in the graph diff.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
        is_informational=True,
    ),
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
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
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
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
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
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "evidence_required_missing",
        _A,
        impact="A policy require_evidence layer (build context, source ABI, or "
        "source graph) was declared mandatory but is absent from this "
        "compare, so the run is failed rather than passing on a silently "
        "degraded scan. Supply the missing evidence pack or "
        "relax the policy.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "exported_symbol_source_owner_changed",
        _R,
        impact="An exported symbol present in both versions is now declared by a "
        "different file (the file owning its declaration moved in the source "
        "graph — e.g. a public declaration relocated to another header, or "
        "its declaring translation unit changed). The symbol name and "
        "signature are unchanged, so the artifact diff is quiet, but the "
        "declaration behind a stable public symbol moved — a refactor that "
        "can change consumers' include paths, inlining, or introduce an ODR "
        "risk if the old location still declares it. A source-graph risk to "
        "review, never on its own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "generated_header_changed",
        _R,
        impact="A generated public configuration header changed between versions. "
        "Generated headers encode build-time configuration into the public "
        "API surface, so a change can alter declarations or macro contracts "
        "seen by consumers. Policy may escalate to an API break; by default "
        "a risk to review.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "generated_header_reaches_public_api",
        _R,
        impact="A generated file newly participates in the public declaration "
        "closure (it is a public header, or it declares a reachable public "
        "entity). Build-time-generated content now shapes the public API "
        "surface, so its provenance and reproducibility warrant review.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "identity_collision_detected",
        _R,
        impact="Two distinct declarations were linked onto the same L4 identity key "
        "(SourceEntity.identity(): the mangled name, else "
        "qualified_name#signature_hash, else the bare qualified name) — proven "
        "distinct because each carries a different clang-computed USR. The "
        "identity fallback chain accepts this rare collision by design for "
        "unmangled cross-scope declarations; when it happens, "
        "the two declarations were folded together in the linked surface, so "
        "any L4/L5 finding attributed to that identity may actually describe "
        "either one. A source-tooling-confidence risk, never an artifact-proven "
        "ABI break — no action is required unless a finding under that name "
        "looks wrong, in which case treat it as ambiguous between the two USRs "
        "named in the finding detail.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "include_graph_public_header_drift",
        _R,
        impact="The transitive include closure behind a public header changed "
        "(per the depfile/-M include graph). Consumers may now pull in "
        "different declarations or macros; a source/API risk to review, "
        "never on its own an artifact-proven ABI break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "odr_source_conflict",
        _R,
        impact="The same type name resolves to different definitions across "
        "translation units (One Definition Rule conflict). Linking or "
        "loading code that mixes the definitions is undefined behavior; a "
        "correctness risk surfaced by comparing per-TU source surfaces.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "odr_type_variant",
        _A,
        impact="One type has divergent definitions across translation units (the L4 "
        "source-replay surface recorded an ODR conflict: the same qualified name "
        "resolves to different per-TU layouts). Linking code that mixes the "
        "definitions is undefined behavior — a consumer compiled against one "
        "layout silently reads a struct laid out the other way. A source/API "
        "break surfaced from one merged snapshot's L4 evidence; never on its own "
        "an artifact-proven shipped-ABI break. Reconcile the conflicting "
        "definitions (usually a macro/flag that changes the type per TU).",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "public_reachability_changed",
        _R,
        impact="A public declaration entered or left the public-API reachability "
        "closure (target → public header → declaration → exported symbol) "
        "between versions. Explains and prioritizes impact derived from the "
        "source graph; never on its own decides an ABI break.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "source_binary_provenance_mismatch",
        _R,
        impact="A large fraction of the source tree's public declarations fail to "
        "map to any exported binary symbol, which strongly suggests the "
        "source checkout does not correspond to the shipped binary (e.g. a "
        "wrong tag/commit). All L4/L5 source findings for this pair are then "
        "untrustworthy; re-check the source out at the binary's build tag. "
        "This is a context risk, never a proven binary break.",
        entity=_ENT.SOURCE,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "source_fact_coverage_incomplete",
        _R,
        impact="The L4 source-fact evidence for this comparison is incomplete or "
        "produced by incompatible producers/fact-set versions — a mandatory "
        "fact family (functions, macros, templates, inline bodies, "
        "constexpr values, ...) was 'partial' or 'failed' on one or both "
        "sides, or the old/new fact-set version or producer differ. "
        "Absence of another L4 finding must not be read as "
        "proof nothing changed in that family; treat this pair's other "
        "source-replay findings as unreliable until re-collected with a "
        "consistent, complete fact set.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
]

SOURCE_ENTRIES.extend(SOURCE_ENTRIES_2)
SOURCE_ENTRIES.extend(SOURCE_ENTRIES_3)
