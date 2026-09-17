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

"""Bug classes about a *classifier* asserting more than its inputs show.

A sibling of `manifest_evidence.py`/`manifest_guards.py`/
`manifest_report.py`/`manifest_tool_surface.py` (see `manifest.py` for what
this registry is and is not). Distinct from `manifest_evidence.py`: those
classes are about a detector reaching for an observation that does not
answer its question. These are about a detector holding the right
observations and then attaching a label those observations do not entail --
most often at a default branch, where the absence of a positive match is
silently reported as the most specific outcome in the vocabulary rather
than as "cannot place".
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["CLASSIFICATION_BUG_CLASSES"]


CLASSIFICATION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="classification.declaration_existence_as_export_obligation",
        invariant=(
            "That an entity's owner is still *declared* never proves that "
            "losing one of the owner's exported symbols is harmless. Three "
            "separate facts have to hold before an observed export "
            "disappearance may be handed to another detector: the owner "
            "resolves to exactly one declaration (a bare, unqualified name "
            "match is a collision, not a resolution), the declaration is "
            "unchanged on both sides, and the header itself offers the "
            "definition every consumer would otherwise have to bind to. An "
            "already-linked client cannot emit a definition it was never "
            "given, so neither 'the class is still there', nor 'it is a "
            "constructor', nor 'it is a template', nor 'the symbol was "
            "weak' licenses dropping the finding -- each of those was "
            "falsified by a loader running a client built once against OLD "
            "against NEW."
        ),
        fixed_by=(1308,),
        seed_tests=(
            "tests/test_export_owner_resolution.py",
            "tests/test_export_reconciliation_and_obligations.py",
            "tests/test_cross_compiler_fp.py",
        ),
        public_surfaces=("compare -H", "compare --depth binary"),
        axes={
            "owner": (
                "namespaced class",
                "class template specialization",
                "inheriting constructor (CI1/CI2)",
                "same bare name in two namespaces",
                "owner not declared at all",
            ),
            "linkage": ("strong/GLOBAL", "weak/COMDAT"),
            "definition site": (
                "in-class (inline)",
                "out-of-line in the library's own source",
                "explicit/extern instantiation",
            ),
            "lost symbol": ("C1/C2", "D0/D1/D2", "vtable/RTTI", "data export"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The inline-definition fact is read from the "
                    "declaration's `is_inline`, which is a fact about the "
                    "*declaration's linkage*, not proof that a consumer "
                    "emitted a copy. A snapshot with no header evidence "
                    "reports False and the finding is kept, which is the "
                    "safe direction; but a header that is inline and a "
                    "consumer that nonetheless bound the library's copy "
                    "(an `extern template` on a non-template member, or a "
                    "consumer built against an older, out-of-line header) "
                    "is not distinguishable from the snapshots alone. "
                    "Template specializations are excluded outright for "
                    "exactly this reason; the non-template case rests on "
                    "the declaration."
                ),
                reference="abicheck/compare/export_owner_resolution.py",
            ),
            KnownGap(
                description=(
                    "`itanium_special_member_owner` recovers the owner path "
                    "with template-argument text stripped, so it names the "
                    "primary template and never the specialization. That is "
                    "sufficient here (specializations are never exempt) but "
                    "means no consumer can use it to match a specialization "
                    "against a model type spelled in C++ (`api::Box<int>`); "
                    "doing so needs an Itanium type decoder this repository "
                    "deliberately does not have."
                ),
                reference="abicheck/model/mangled_name.py",
            ),
        ),
    ),
    BugClass(
        id="classification.two_agreeing_sources_read_as_unknown",
        invariant=(
            "A subject that two independent evidence sources jointly place "
            "-- one proving it exists, the other proving it is not in the "
            "declared contract -- is classified, not treated as unknown. "
            "The conservative-unknown rule protects cases where evidence is "
            "*missing*; applying it where both sources answered turns a "
            "provable disposition into permanent noise."
        ),
        fixed_by=(1314,),
        seed_tests=("tests/test_undocumented_export_surface_scope.py",),
        public_surfaces=("compare --scope-public-headers",),
        axes={
            "platform": ("elf", "elf-versioned", "macho", "pe"),
            "evidence": (
                "no header provenance",
                "no export table",
                "one side unresolvable",
            ),
            "overlay": ("--no-scope-public-headers", "--public-symbol"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Reported as twenty-one `exported_object_alignment_"
                    "reduced` rows against Intel MKL internals no header "
                    "declares. `PublicSurface.all_symbols` held only modeled "
                    "declarations, so an export-table-only symbol fell "
                    "outside the surface universe and the conservative-"
                    "unknown fallback kept every finding about it. The fix "
                    "seeds the universe rather than special-casing one "
                    "ChangeKind, so every binary-level kind on such a symbol "
                    "is classified. What is *not* closed: the same "
                    "two-sources-agree shape may exist elsewhere in the "
                    "classifier (type-level reachability has its own "
                    "unknown fallback), and nothing mechanically enumerates "
                    "where a fallback is reached with evidence in hand."
                ),
                reference="abicheck/policy/public_surface_closure.py",
            ),
        ),
    ),
    BugClass(
        id="classification.demotion_applied_beyond_its_justification",
        invariant=(
            "A demotion rule reaches only the cases its own justification "
            "covers. A rule argued for one direction of a change -- churn "
            "on a thing that already existed -- does not silently apply to "
            "the other, where its premise is simply absent: a symbol that "
            "newly appears has no prior state to have churned, and the "
            "evidence that placed it in the classifier's universe at all is "
            "the evidence that it is now reachable. Concretely, a rule "
            "keyed on a *set* answers the direction-appropriate side: a "
            "removal against the side that still had the subject, an "
            "addition against the side that newly has it -- never a union "
            "of both, which for an addition asks nothing at all. A reason "
            "code must also not contradict the evidence it rests on."
        ),
        fixed_by=(1321,),
        seed_tests=("tests/test_undocumented_export_surface_scope.py",),
        public_surfaces=("compare --scope-public-headers",),
        axes={
            "direction": ("added", "removed", "property churn"),
            "entity": ("function", "variable"),
            "visibility": ("elf-only", "declared"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The sibling class above seeds an export-table-only "
                    "symbol into the surface universe so *property* churn "
                    "on it can be demoted. Two separate follow-ups were "
                    "needed because that demotion also swallowed the "
                    "symbol's removal (caught by the example matrix) and "
                    "then its addition (caught in the field: `Additions: 0` "
                    "and a `PATCH` recommendation for a release whose "
                    "exported surface had genuinely grown). Both were the "
                    "same mistake in two directions, which is why the class "
                    "is stated over the existence axis rather than "
                    "enumerating kinds. What is not mechanized: nothing "
                    "sweeps the classifier for other rules whose "
                    "justification is narrower than their predicate, and a "
                    "kind added to one of the seeded families without being "
                    "placed on the existence axis would not be caught."
                ),
                reference="abicheck/surface.py",
            ),
        ),
    ),
    BugClass(
        id="classification.name_shape_as_contract_membership",
        invariant=(
            "A symbol's spelling answers how it is *represented* and what "
            "convention it follows -- never whether it is in the "
            "compatibility contract. Two corollaries the report layer must "
            "honour: (a) a name-derived bucket (RTTI/vtable, "
            "internal-namespace-by-convention, version segment) may be "
            "counted and described, but never presented as evidence that a "
            "finding is not a public break -- a vtable change on a public, "
            "user-derivable class is one, and a `vN` segment states an API "
            "version, not a stability promise; (b) when a name *is* parsed "
            "for a scope-convention answer, only the scope that OWNS the "
            "entity counts. A mangled name embeds its parameter types, so a "
            "whole-string scan attributes a parameter's namespace to the "
            "function, and an entity merely named like a convention "
            "namespace is not in one."
        ),
        fixed_by=(1231,),
        seed_tests=(
            "tests/test_symbol_origin_ownership_properties.py",
            "tests/test_surface_breakdown.py",
            "tests/test_policy_experimental_namespaces.py",
            "tests/test_diff_namespaces.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Corollary (b) is now structural (the real Itanium "
                    "nested-name parser resolves the owner) and generated "
                    "against an independent oracle -- the seed test builds "
                    "each mangled name from known components with its own "
                    "encoder rather than re-deriving the answer through the "
                    "parser under test. Two residual gaps. First, the "
                    "fallback for shapes that parser does not model "
                    "(constructors, destructors, operators) is still a "
                    "textual scan of the region before the first `E`; it errs "
                    "safe (a template argument's own `E` truncates it early, "
                    "under-detecting rather than over-detecting) and is "
                    "covered only by enumerated sibling cases -- teaching the "
                    "structural parser those productions is the real fix. "
                    "Second, and larger: corollary (a) is enforced only by "
                    "making the report stop *claiming* membership it cannot "
                    "establish. The positive half -- classifying these "
                    "findings against a resolved contract -- is ADR-049's "
                    "`--contract` machinery, which the surface breakdown does "
                    "not consult at all; the JSON `abi_surface_breakdown` "
                    "block still uses the `rtti_churn`/`internal_churn` key "
                    "names, kept for report-schema stability and now "
                    "contradicted by the prose beside them. A third, adjacent "
                    "defect from the same report is tracked separately in "
                    "docs/contribute/known-gaps.md: `Visibility.PUBLIC` used "
                    "as a proxy for declaration presence, which is an "
                    "evidence-selection problem rather than a naming one."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="classification.default_branch_asserts_more_than_inputs",
        invariant=(
            "A classifier's final, unguarded `return` may not be the "
            "strongest claim in its vocabulary. Every outcome must be an "
            "affirmative classification whose claim is entailed by the "
            "inputs; a pair that cannot be placed gets its own explicit "
            '"cannot place" outcome, never the label with the most '
            "specific prose. The guard is a property over generated "
            "input pairs asserting entailment (an outcome claiming a "
            "name or location change may only be returned where the "
            "names or locations actually differ), not a fixed example "
            "per branch \u2014 and it must hold for the *catalog* text and "
            "verdict tier of the kind emitted, not only for the rendered "
            "sentence, since a render-time rewrite leaves the claim "
            "standing everywhere else."
        ),
        # v19 oneAPI scan: `graph_reconcile_outcome.classify` fell through
        # to OUTCOME_RECONCILED ("both the qualified name and the
        # declaring-file evidence changed together", severity risk) for
        # every pair that was neither renamed, moved, nor coordinate-only.
        # All 234 of a oneTBB header graph's reconciled calls were
        # `source_decl` pairs whose qualified name, source_relative and
        # two-sided declaring file were byte-identical; 13 findings had
        # `old_value == new_value`. PR #1232 corrected the rendered prose
        # for that population and the claim survived in the ChangeKind's
        # own `impact` text and RISK verdict, which is why prose was not
        # the fix.
        fixed_by=(1232, 1321),
        seed_tests=(
            "tests/test_graph_reconcile_outcome_properties.py",
            "tests/test_graph_reconcile_coordinate_outcome.py",
            # Same shape in provenance: an include root the run cannot
            # place as public fell through to PRIVATE_HEADER -- the label
            # that licenses dropping a finding -- rather than to UNKNOWN,
            # the vocabulary's own "cannot place" outcome. Fixed once in
            # `provenance`, then a second time in `buildsource.header_graph`,
            # which called the shared fold and discarded the half carrying
            # the answer.
            "tests/test_dependency_include_root_ownership.py",
        ),
        public_surfaces=("cli",),
        axes={
            "name_evidence": ("identical", "coordinates_only", "renamed"),
            "location_evidence": (
                "identical",
                "differing",
                "absent_both",
                "absent_one",
            ),
            "kind": ("type", "source_decl"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The entailment property is stated for this one "
                    "classifier. No mechanical sweep finds other "
                    "classifiers whose final branch returns the "
                    "strongest label in their vocabulary; a second "
                    "instance elsewhere would still be found by hand."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="release.cartesian_product_contract",
        invariant=(
            "A comparison whose candidate is a *set* of artifacts sharing one "
            "declared contract judges the contract against the set, not "
            "against each member independently. Concretely, for N members "
            "sharing one public header surface: an obligation the set "
            "satisfies is satisfied for every member (no member is short of "
            "a sibling's declaration), a fact several members observe "
            "identically is reported once with every member attributed, and "
            "the reported finding count therefore does not grow with the "
            "number of members that merely share the surface. The failure "
            "this closes is a cardinality law, not a bad finding: N members "
            "x |surface| manufactured findings, none of them a real "
            "compatibility signal. Its honesty half: when a member's "
            "evidence was never read, an obligation nothing else satisfies "
            "is recorded as unresolved with the coverage gap named -- never "
            "concluded as missing from the set, which would rest a "
            "high-confidence conclusion on unread evidence."
        ),
        fixed_by=(),
        seed_tests=(
            "tests/test_release_public_surface.py",
            "tests/test_release_surface_acquisition_context.py",
            "tests/test_release_public_surface_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "members": ("2", "3", "8", "28"),
            "coverage": ("complete", "failed_member", "no_export_table"),
            "evolution": ("introduced", "resolved", "persistent", "not_evaluated"),
            "export_binding": ("default", "non_default_version_alias"),
            # The set-level acquisition must parse under the configuration
            # the per-member pass honors, not only key on it.
            "acquisition_context": ("absent", "resolved_compile_context"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The invariant is stated for the one set-shaped "
                    "comparison that exists (the directory/package release "
                    "fan-out). No mechanical sweep finds another place where "
                    "a shared declared contract is judged per member; a "
                    "second instance would still be found by hand."
                ),
                reference="docs/learn/products-not-libraries.md",
            ),
            KnownGap(
                description=(
                    "Moving a check to the set level moves what its answer "
                    "depends on: the set-level acquisition must honor every "
                    "AST-affecting input the per-member pass honors, or the "
                    "move turns a reported finding into a silently lost one. "
                    "Covered for `CompileContext` by an exhaustive field "
                    "sweep; an AST-affecting input that is *not* a field of "
                    "that object (a future config key read elsewhere) would "
                    "still be found by hand."
                ),
                reference="tests/test_release_public_surface.py",
            ),
            KnownGap(
                description=(
                    "Output cardinality is closed; storage cardinality is "
                    "not. Each member snapshot still persists its own copy "
                    "of the shared header evidence, so a stored product "
                    "baseline remains O(surface x members) on disk even "
                    "though the report and the reconciliation are not."
                ),
                reference="docs/learn/products-not-libraries.md",
            ),
        ),
    ),
)
