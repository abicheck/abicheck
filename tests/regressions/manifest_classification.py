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
        fixed_by=(1232,),
        seed_tests=(
            "tests/test_graph_reconcile_outcome_properties.py",
            "tests/test_graph_reconcile_coordinate_outcome.py",
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
)
