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

"""Bug classes from the CI-integration surface: workflows, Actions, shell.

A themed sibling of ``manifest.py``, the same split ``manifest_guards.py``/
``manifest_report.py``/``manifest_evidence.py``/``manifest_test_harness.py``
already establish -- so registering a class here costs the root module the
import and the one concatenation line, rather than growing a file already at
its recorded ``no_growth`` baseline.

What belongs here: a class whose mechanism lives in the layer where this
repository's Python meets GitHub Actions and the shell -- a workflow input,
a composite Action's ``run:`` block, a value crossing into ``$GITHUB_OUTPUT``,
a checked-in declaration filled in at build time. `tooling.platform_dependent_
record_separator` and `trust_boundary.shell_workflow_injection` are that
layer's existing residents elsewhere in the registry; these three arrived
together from PR #1325, which closed four upstream gaps a downstream consumer
had reimplemented.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

INTEGRATION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="identity.name_and_referent_compared_as_one",
        invariant=(
            "A name and the object that name resolves to are two values, "
            "and an equality between them is a category error however "
            "plausible the two spellings look side by side. The reported "
            "instance: a release publisher compared a captured baseline's "
            "own `project_ref` -- a 40-hex commit, because a capture "
            "records the revision it built -- against the publication "
            "*tag*, so it rejected every genuine cross-run capture with "
            "'records capture revision <sha> but is being published "
            "against 1.5.2'. The comparison cannot succeed for a real "
            "capture and cannot fail for the one shape it was written "
            "against, which is why it survived: it was only ever "
            "exercised where the publisher had captured the set itself. "
            "Generalized, the rule has two halves, and the second is "
            "where the near-misses live: resolution from a name to its "
            "referent must be *explicit* -- one owner, stating what it "
            "expects as a closed set -- and must REFUSE rather than "
            "guess on every near miss the underlying endpoint can "
            "produce. GitHub's ref API answers a prefix match, so asking "
            "for `refs/tags/v1.2` returns `v1.2.1`; a `refs/heads/` ref "
            "of the same name is not the tag; an annotated tag's object "
            "is a tag, not the commit, and must be peeled; a tag object "
            "may itself point at a tree, a blob, or another tag. Each is "
            "a distinct refusal with its own code, never a fallback to "
            "whichever value is at hand."
        ),
        fixed_by=(1325,),
        seed_tests=(
            "tests/test_action_tag_resolution.py",
            # The same invariant at the surface that had the bug: the
            # workflow step, executed for real against a `gh` stub that
            # answers only the endpoints it may call.
            "tests/test_publish_baseline_tag_resolution_step.py",
        ),
        public_surfaces=("workflow",),
        axes={
            "tag_shape": ("lightweight", "annotated"),
            "object_type": ("commit", "tag", "tree", "blob", "missing"),
            "declared_expectation": ("commit", "tag", "explicit_sha"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No mechanical sweep for the class. The seed tests "
                    "state the invariant for tag-to-commit resolution, "
                    "which now has one owner (`frontends/action/"
                    "tag_resolution.py`, which `baseline_source."
                    "resolve_tag` delegates to -- closing a second, "
                    "latent instance where a ref pointing at a tree "
                    "resolved as a release baseline). Nothing checks "
                    "that a *future* name/referent pair elsewhere routes "
                    "through an explicit resolver rather than an "
                    "equality: the two values are ordinary strings, so "
                    "no type distinguishes them, and a gate would need "
                    "to know which strings are names. Recorded rather "
                    "than left implied."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1325",
            ),
        ),
    ),
    BugClass(
        id="config.textual_substitution_into_a_structured_document",
        invariant=(
            "Filling a build-decided value into a structured declaration "
            "is a substitution on the *document*, walked node by node, "
            "never on its serialized text. A `sed`-style pass over the "
            "JSON is not wrong for one unlucky value; it is wrong for a "
            "family of them, differently in each member, and every "
            "member is an ordinary filesystem path: `&` means the whole "
            "match in a replacement, a quote closes a string, a "
            "backslash is eaten, the delimiter character fails outright. "
            "None of that is the caller's to constrain. Three properties "
            "come with it, because each is a question a textual pass "
            "cannot answer: the substitution is ONE PASS, so a bound "
            "value containing `${...}` is data and there is no fixed "
            "point, no recursion bound and no cycle check to get right; "
            "the allowlist is CLOSED, so an unbound placeholder refuses "
            "rather than surviving as text to fail later as a confusing "
            "'no such file' or, worse, being filled from the "
            "environment, which would make every variable on the runner "
            "an input; and the walk is INJECTIVE on each object's keys, "
            "so two distinct declared keys that bind to one key refuse "
            "rather than silently dropping whichever was written first."
        ),
        fixed_by=(1325,),
        seed_tests=("tests/test_action_library_spec_binding.py",),
        public_surfaces=("action", "cli"),
        axes={
            "value_class": (
                "plain",
                "ampersand",
                "quote",
                "backslash",
                "pipe",
                "space",
                "empty",
                "shell_metacharacter",
            ),
            "placeholder_form": ("value", "key", "nested", "repeated"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The binder is one owner and is tested as a "
                    "primitive, per root AGENTS.md's rule for a reusable "
                    "substitution/merge primitive. What has no gate is "
                    "the negative: nothing stops a future caller "
                    "serializing a declaration and running its own "
                    "textual pass beside this one. The `_relative`-loop "
                    "incident in this same PR is the warning -- a second "
                    "owner for one rule was added on the same branch "
                    "that added the first, disagreed with it about a "
                    "legal `report..json`, and was invisible to tests "
                    "that exercised the extracted function rather than "
                    "the whole script."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1325",
            ),
        ),
    ),
    BugClass(
        id="shell.empty_array_expansion_under_nounset",
        invariant=(
            'An array that can be empty is never expanded as `"${a[@]}"`. '
            "macOS ships bash **3.2**, GPLv2-frozen; under `set -u` that "
            "shell treats expanding an *empty* array that way as an "
            "unbound-variable reference and aborts, which bash 4.4+ "
            "special-cased away. So the failure is invisible on every "
            "Linux runner and on a developer's own machine, and it "
            "lands on whichever path leaves the array empty -- "
            "routinely the COMMON one: no `--gate`, no annotated tag to "
            "peel, no optional selector. The guarded spelling "
            '`${a[@]+"${a[@]}"}` is identical on every bash when the '
            "array is non-empty, so there is no trade-off to weigh and "
            "no case where the bare form is preferable. This is a "
            "*product* rule, not a test-only one: the published Actions "
            "run on whatever shell a consumer's runner has."
        ),
        fixed_by=(1325,),
        seed_tests=("tests/test_action_shell_empty_array_expansion.py",),
        public_surfaces=("action", "workflow"),
        axes={
            "shell_carrier": ("standalone_sh", "workflow_run", "action_run"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test is a static scan, and a static scan "
                    "is not a behavioural test -- reproducing the abort "
                    "needs a bash 3.2 binary, which no lane has. Two "
                    "things compensate and neither closes it: the "
                    "detector is a real function exercised against "
                    "crafted safe and unsafe inputs (including the ways "
                    "a naive version gets it wrong -- a mention inside a "
                    "comment, the guarded form's own inner "
                    '`"${a[@]}"`, an escaped quote, a quoted string '
                    "spanning lines), and the rule is scoped to arrays "
                    "*declared empty*, which is precise rather than "
                    "noisy and needs no allowlist to curate. What "
                    "remains unproven is the shell's actual behaviour; "
                    "the class is held by the repository's own history "
                    "instead, where it has now shipped twice."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1325",
            ),
        ),
    ),
    BugClass(
        id="identity.second_node_key_scheme",
        invariant=(
            "A graph that names declarations and types keys each one by the "
            "Phase 1 entity node id (`model.graph_entity_identity."
            "snapshot_identities`, invariant I1), never by a key scheme of "
            "its own: the canonical node set equals the I1 id set, so two "
            "entities I1 keeps apart (an ODR-duplicate record pair, two "
            "unmangled overloads, a C typedef beside an unrelated same-named "
            "tag) never share a node, and two it merges (`typedef struct A "
            "A`) never split. The reported instance was the persisted "
            "`compare --contract` replay graph, whose own `decl:`/`record:` "
            "keys merged the ODR pair and could not be joined with any other "
            "graph. A persisted graph written under an older scheme is read "
            "as written -- never remapped onto I1 ids, since the old key "
            "does not carry the evidence to pick one -- and must replay to "
            "the decisions its producer gave."
        ),
        fixed_by=(1358,),
        seed_tests=(
            # Exhaustive small-domain enumeration (all 1-3 element subsets of
            # eleven identity shapes) against `snapshot_identities` as the
            # oracle, plus a pre-change report recorded from origin/main and
            # replayed under the new reader.
            "tests/test_contract_replay_node_ids.py",
        ),
        axes={
            "entity_shape": (
                "mangled_function",
                "unmangled_overload",
                "odr_duplicate_record",
                "qualified_record",
                "enum",
                "typedef_unrelated_tag",
                "typedef_same_tag",
                "variable",
            ),
            "persisted_encoding": ("schema1", "schema2"),
        },
    ),
    BugClass(
        id="imports.side_effect_import_reorders_registration",
        invariant=(
            "Detector execution order is detector priority order (the order "
            "findings are produced and reported in, and the tie-break "
            "cross-detector dedup keeps), and it is the same whichever "
            "abicheck module a process imports first: it is declared "
            "(`detector_registry.DETECTOR_MODULE_ORDER`), never implied by the "
            "registration counter. Reported twice in one PR: deleting a "
            "`checker.py` re-export import of `diff_filtering` (whose side "
            "effect imported `diff_symbols`) reordered every report, and the "
            "mutation lane's traced run -- where some test imported "
            "`diff_symbols` before `checker` -- rendered every golden with "
            "`kabi`/`elf_layout` in another position."
        ),
        fixed_by=(1564,),
        seed_tests=(
            # Every detector-hosting module (and four transitive importers)
            # imported first in a fresh interpreter must give the checker-first
            # order; the checker-first order is checked against anchor pairs
            # recorded from main, an oracle independent of the declared tuple.
            "tests/test_detector_registration_order.py",
        ),
    ),
    BugClass(
        id="identity.edge_matched_on_coarse_key",
        invariant=(
            "A graph diff matches edges on their full identity "
            "(`GraphEdge.relation_key()`: src, dst, kind, role), never on a "
            "coarser projection: a dependency kept on both sides whose role "
            "set differs is reported as a changed relation, never dropped "
            "and never split into a removed+added pair, and identical role "
            "sets report nothing. When exactly one side carries no role "
            "data (a graph stored before roles were stamped) the relation "
            "is a coverage gap, never a fabricated change. The reported "
            "instance was `diff_source_graph` keying on `(src, dst, kind)`, "
            "so `param` -> `return` on a public declaration diffed empty."
        ),
        fixed_by=(1557,),
        seed_tests=(
            # Exhaustive domain: every pair of role sets of size 0-2 over
            # each role-bearing kind's roles plus the role-less case, against
            # the raw per-side relation_key() sets as oracle; plus real gcc
            # libraries through `abicheck dump`/`compare`.
            "tests/test_source_graph_role_diff.py",
            # Harness H3 cell: role-distinct relations keep distinct keys.
            "tests/test_family_f3_identity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "edge_kind": (
                "DECL_HAS_TYPE",
                "TYPE_HAS_FIELD_TYPE",
                "TYPE_INHERITS",
                "DECL_REFERENCES_DECL",
            ),
            "role_data": ("stamped", "role_less_one_side", "role_less_both_sides"),
            "entry_visibility": ("public", "private"),
        },
    ),
    BugClass(
        id="evidence.depth_projection_drops_lower_layer_detector_input",
        invariant=(
            "A depth projection that clears a field for being evidence of a "
            "higher layer must keep the lower-layer facts the same field "
            "carries for a detector still enabled at that depth: a lower "
            "depth never invents a finding the higher depth rejects. "
            "`--depth headers` cleared `surface_graph` as L5 evidence and "
            "with it the L2 template declarations the template-instance-"
            "absence rule reads, so a vanished implicit instance of a still-"
            "declared member template read as `inline_function_removed`; "
            "separately, an overloaded primary was excluded at every depth."
        ),
        # 1661 introduced the rule; append the H2 fix PR number on merge.
        fixed_by=(1661,),
        seed_tests=(
            # Generator: member/free x namespace depth 0/2 x overloads 1-3 x
            # primary kept/deleted x depth headers/source, ground-truth
            # oracle plus the headers-subset-of-source metamorphic check and
            # a compiled -fvisibility-inlines-hidden reproduction.
            "tests/test_template_instance_absence_shapes.py",
            # The class: every detector's declared projectable inputs vs
            # every rung's clearable facts (pure table, projection behaviour,
            # AST read oracle, registry "disabled" coverage), and the
            # catalog-wide lower-rung-never-invents-a-removal relation.
            "tests/test_depth_detector_input_contract.py",
            "tests/test_depth_metamorphic_examples.py",
            # F1 harness: records the runtime-only projection fact.
            "tests/test_family_f1_evidence_ablation.py",
        ),
        public_surfaces=("cli",),
        axes={
            "template": ("member", "free"),
            "namespace_depth": ("0", "2"),
            "overloads": ("1", "2", "3"),
            "primary": ("kept", "deleted"),
            "depth": ("headers", "source"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Overload shape is not matched: a primary's graph id "
                    "carries a one-way hash of its dependent signature, so "
                    "with several overloads the rule requires every old "
                    "overload to be kept (or, with no old header graph, any "
                    "one). The old side's clang template-origin edges "
                    "(`_graph_origins`) are still dropped at headers depth; "
                    "only castxml's line reading proves a class-template "
                    "specialization's origin there."
                ),
                reference="abicheck/compare/template_instance_absence.py",
            ),
        ),
    ),
)
