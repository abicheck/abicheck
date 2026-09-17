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
)
