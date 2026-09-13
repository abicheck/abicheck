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

"""Bug classes about a *guard's own predicate*, not the analysis it gates.

The defects here are found by reading a condition against the argument
written next to it: what the code tests versus what its justification
claims, and what the branch it rejects into then asserts. That makes them
invisible to the kind of test that exercises the surrounding feature --
every such test passes, because the feature works; it is the population the
guard admits, and the claim the fallback makes, that are wrong.

Its own module for the reason ``manifest_report.py``/
``manifest_tool_surface.py`` are: ``manifest.py`` sits at its
``architecture/debt.yaml`` ``no_growth`` baseline and is an assembly point
over per-axis entry lists, so a new class goes in a sibling rather than
growing it (PR #1228).
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["GUARD_BUG_CLASSES"]


GUARD_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="guard.proxy_predicate_overshoots_justification",
        invariant=(
            "A guard's predicate must test the property its own "
            "justification names, and must be checked against the "
            "population it targets. Two failures follow when it does not: "
            "a narrowing guard can eliminate the entire population it was "
            "meant to discriminate within (the outcome becomes reachable "
            "from no real input, and nothing fails anywhere), and the "
            "fallback it hands those inputs to can assert a STRICTLY "
            "STRONGER claim than the one the guard declined — declining a "
            "weak claim for lack of evidence, then making a strong one on "
            "the same absent evidence. An early return justified by a "
            "semantic property (content identity) but implemented via a "
            "proxy (object identity) is the same defect from the other "
            "side: the claim the return exists to reject is made anyway, "
            "on the shape users actually hit."
        ),
        # #1228: `graph_reconcile._classify_outcome`'s declaring-file guard
        # made OUTCOME_COORDINATES_ONLY unreachable (0 occurrences
        # repo-wide) and sent every eligible pair to OUTCOME_RECONCILED's
        # "both name and location evidence changed" — on absent location
        # evidence. Same PR: `schema_staleness_status`'s `old is new`
        # self-diff return, whose own argument ("comparing a value against
        # itself can never produce a false pairwise finding") never
        # depended on object identity, reported one stored snapshot loaded
        # twice as `degraded` and flipped `assurance.status` to `partial`.
        fixed_by=(1228,),
        seed_tests=(
            "tests/test_graph_reconcile_coordinate_outcome.py",
            "tests/test_analysis_assurance_content_identity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "evidence_presence": ("both_sides", "one_side", "absent"),
            "claim_strength": ("declined", "weak_claim", "strong_claim"),
            "identity_test": ("object", "content", "differing"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Reachability is asserted per-classifier in the seed "
                    "tests, not swept mechanically: another outcome "
                    "elsewhere in the codebase that a later guard made "
                    "unproducible would still be found by hand. The "
                    "residuals on the first instance are narrower and "
                    "both stated rather than hidden: a pair with no "
                    "positive evidence on EITHER axis still falls through "
                    "to OUTCOME_RECONCILED's overstated prose (ADR-048's "
                    "accepted case197 'no clean split'), and a "
                    "coordinate-only pair resting on qualified-name "
                    "evidence alone cannot rule out a move that kept the "
                    "same file basename -- recorded as "
                    "`ReconciledPair.coordinate_evidence` and disclosed in "
                    "the finding's own text, since separating that from a "
                    "declaring-file-confirmed shift at the *kind* level "
                    "would need a fifth outcome and a new ChangeKind."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="gate.per_member_axis_fold",
        invariant=(
            "An orthogonal 0/1 gate axis computed per compared member of a "
            "directory/package (release) comparison folds across members "
            "with max() and nothing else: the fold is the identity over one "
            "member (so a one-member package gates and reports exactly as "
            "the scalar path does for that pair -- AGENTS.md's 'One model, "
            "any cardinality'), is independent of member order, never lets a "
            "clean sibling mask a member that fell short, and is monotonic "
            "(adding a member never lowers the contribution or improves the "
            "aggregate status). The decision's reported status and its exit "
            "contribution are resolved together and must agree, so a report "
            "can never contradict its own exit code. The axis is also "
            "strictly additive: a caller that never opts in gets a decision "
            "byte-identical to one passing an explicit 0."
        ),
        fixed_by=(1237,),
        seed_tests=(
            "tests/test_release_assurance_properties.py",
            "tests/test_release_assurance_cli.py",
        ),
        public_surfaces=("cli",),
        known_gaps=(
            KnownGap(
                description=(
                    "Stated and exercised for ADR-071's analysis-assurance "
                    "axis specifically. The two sibling axes with the same "
                    "shape -- ADR-049's contract-coverage floor and ADR-065 "
                    "D6's incomplete-scope floor -- are covered by their own "
                    "example-level tests only; neither has an order-"
                    "independence or monotonicity property test of its own, "
                    "so a regression in *their* fold would not be caught "
                    "here. Generalizing this class's properties over all "
                    "three (one shared property harness parameterized by "
                    "axis) is the real remaining work, not another per-axis "
                    "copy."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="guard.differential_test_shares_state_with_itself",
        invariant=(
            "A test whose claim is that two configurations AGREE must "
            "establish, within that same comparison, that each "
            "configuration actually executed. Otherwise a shared cache, "
            "memo or fixture lets the second run be served the first's "
            "result, the test compares one configuration with itself, and "
            "it passes identically whether the second code path is "
            "correct, broken, or never entered at all. A sibling test "
            "proving the mechanism CAN engage on the same input does not "
            "discharge this: it says nothing about whether this "
            "comparison engaged it. The executable form of the invariant "
            "is to observe the mechanism (a spy on the real entry point, "
            "asserting it ran and what it reported) rather than to infer "
            "it from the outputs being compared -- outputs are exactly "
            "what a stale cache makes indistinguishable. The same "
            "reasoning bounds fixture sharing: sharing immutable INPUTS "
            "between two configurations is sound, sharing the OUTPUT "
            "whose equivalence is the claim is this bug."
        ),
        # PR #1243: both streaming-pruner tests in
        # tests/test_clang_header_backend_integration.py derived their
        # "fresh" AST-cache root from the same `tmp_path`, so the
        # pruning-on run hit the pruning-off run's on-disk cache. The
        # pruner never parsed anything (proven by reintroducing the shared
        # root: the loader's call count is 0), so the public-model
        # equivalence assertion and the method-shaped-node count equality
        # were both vacuously true. The comment on the second call
        # asserted "a *different*, still-fresh cache dir"; it never was.
        fixed_by=(1243,),
        seed_tests=(
            "tests/test_clang_header_backend_integration.py",
            "tests/test_conftest_cache_isolation.py",
        ),
        # Neither seed test reaches a documented public entry point: the
        # pruner tests call `dump()`/`_clang_header_dump` directly and the
        # isolation tests inspect a pytest fixture's own allocation, so
        # per this field's rule this stays empty rather than claiming
        # "cli"/"python-api".
        public_surfaces=(),
        axes={
            "shared_state": ("on_disk_cache", "in_process_memo"),
            "configuration_pair": ("pruning_off_vs_on", "allocator_old_vs_new"),
            "observation": ("outputs_only", "mechanism_spy"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the two known instances are repaired and pinned; "
                    "nothing sweeps the suite for other differential tests "
                    "that share a cache, memo, fixture or temp root "
                    "between the two configurations they compare. The "
                    "shape is not mechanically detectable from a test's "
                    "text -- a second call to one isolation helper is not "
                    "by itself a defect -- so a sibling instance elsewhere "
                    "would still be found by hand, the same residual the "
                    "`guard.proxy_predicate_overshoots_justification` "
                    "entry above records for reachability."
                ),
                reference="PR #1243",
            ),
        ),
    ),
    BugClass(
        id="guard.platform_convention_without_a_gate",
        invariant=(
            "A platform-portability rule that every call site must follow "
            "-- resolve the interpreter, quote the path, state the "
            "encoding -- is enforced by a repository-wide scan for the "
            "banned *shape*, not by a shared helper other modules are "
            "expected to remember to import. A convention with no gate "
            "regresses on the next module written by someone who did not "
            "read the helper, and it regresses on the lane that is not the "
            "author's own, so the failure surfaces in CI rather than "
            "locally. Two corollaries the bash instance made concrete. "
            "First, a clone is not merely untidy: it freezes the rule at "
            "the moment it was copied, so a later fix to the shared helper "
            "reaches every importer and none of the copies -- the twenty-"
            "nine clones here all predated the resolver learning to reject "
            "the stub, and each kept handing it to subprocess afterwards. "
            "Second, the gate must cover the whole rule and not its most "
            "visible half: banning the literal while allowing a resolved "
            "call with no availability guard moves the defect rather than "
            "removing it, since the resolver's own documented fallback is "
            "the banned value. The scan must be structural (an AST walk "
            "that distinguishes a real call site from the same token in "
            "prose, a comment, or an availability probe) and must itself "
            "be shown non-vacuous: one assertion per rule that it fires on "
            "the banned shape, and one that each helper it points at "
            "really does something other than the banned value."
        ),
        # The Windows unit lane: `bash_executable()` had existed for many
        # PRs and was the documented convention, yet twenty-nine modules
        # carried private `_bash_executable` clones of it and several more
        # spelled a bare `["bash", ...]` argv, which resolves to the WSL
        # launcher stub on windows-latest -- it prints UTF-16LE text and
        # exits 1, so the whole calling module fails at once with no
        # bash-level diagnostic. Two modules
        # (`test_extra_args_is_value_option_completeness`,
        # `test_action_cli_surface_generated`) were red on main for exactly
        # this, having been written after the convention existed; #1250
        # repaired those two by hand and hardened the resolver, which is
        # what made the twenty-nine unrepaired copies of the old rule the
        # remaining exposure and the gate the actual fix.
        fixed_by=(1250, 1255),
        seed_tests=("tests/test_subprocess_bash_is_resolved.py",),
        # A test-suite portability rule: it reaches no shipped CLI or
        # Python-API surface, so this stays empty per the field's rule.
        public_surfaces=(),
        axes={
            "call_shape": (
                "literal_argv",
                "private_clone",
                "resolved_unguarded",
                "resolved_guarded",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Stated and swept for one convention (bash "
                    "resolution) over one tree (`tests/`). The sibling "
                    "conventions with the same shape -- the encoding rule "
                    "`test_repo_text_reads_state_their_encoding.py` "
                    "already gates, and the POSIX-shell `pytestmark` an "
                    "`action/run.sh` harness consumer must apply, which "
                    "is still a convention with no scan -- are each "
                    "guarded (or not) on their own. One shared "
                    "banned-shape harness parameterized by convention is "
                    "the real remaining work, not a third hand-written "
                    "scan."
                ),
                reference="PR #1255",
            ),
        ),
    ),
    BugClass(
        id="guard.absent_capability_vs_real_failure",
        invariant=(
            "A test-support helper may report 'skipped' only for a capability "
            "that is genuinely absent (no platform, no tool, an explicitly "
            "named optional feature). A tool that is present, was configured, "
            "was actually run, and then failed is a test FAILURE with the "
            "command, input and stderr attached -- for every nonzero exit "
            "status, signal-kill included, and whatever the stderr's encoding. "
            "The rule generalizes past compilers: the failure mode is any "
            "helper that widens 'this environment cannot do X' to cover "
            "'doing X went wrong', because the silent-skip guard "
            "(ABICHECK_MIN_EXECUTED) cannot see it -- the sibling fixtures "
            "that still build satisfy the floor while the broken ones vanish."
        ),
        fixed_by=(1252,),
        seed_tests=("tests/test_compile_failure_contract.py",),
        public_surfaces=(),
        axes={
            "exit_status": ("zero", "nonzero", "signal"),
            "optional_feature": ("named", "absent"),
            "stderr": ("empty", "large", "non_utf8"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only `tests/test_cross_platform_integration.py`'s two "
                    "fixture-building helpers are migrated to the contract. "
                    "A repo-wide grep finds roughly thirty other "
                    "`if result.returncode != 0: pytest.skip(...)` sites "
                    "across the integration/parity suites; each needs its own "
                    "reading (several are genuine optional-feature probes -- "
                    "a -gdwarf-5 or BTF-capable toolchain -- where a skip is "
                    "the right answer), so they are not converted "
                    "mechanically. No structural gate rejects a new site yet; "
                    "the structural half of the seed test pins only the two "
                    "migrated helpers."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="test_infra.autouse_allocator_cannot_recover",
        invariant=(
            "A fixture every test enters must not convert a transient "
            "failure of its own environment into a failure of the whole "
            "session. Concretely, for an autouse allocator that creates a "
            "directory under pytest's temp tree: the allocation must succeed "
            "again after ANY part of the tree above it is removed -- the "
            "allocated directory, its parent basetemp, or an ancestor -- "
            "because the allocator cannot know, and must not depend on, what "
            "removed it. The hazard is specific to being autouse: the first "
            "deletion is transient, but with nothing recreating the tree "
            "every subsequent test enters through the same failing path, so "
            "one lost directory reads as tens of thousands of unrelated "
            "errors. Recovery must also stay idempotent -- a version that "
            "heals by allocating a fresh directory on every call silently "
            "discards the per-process reuse the allocator exists for."
        ),
        # Runs 34729579282: `unit-tests (ubuntu-latest, 3.12)` reported 2
        # failed + 20,867 errors and 3.14 reported 29,164, every one of them
        # `FileNotFoundError: .../popen-gwN/snapshot-caches-XXXXXXXX` out of
        # the autouse `_isolate_snapshot_cache`. The bucket's own `is_dir()`
        # check handled the bucket disappearing but not its PARENT, so once a
        # worker's basetemp was gone `mkdtemp(dir=basetemp)` failed for the
        # rest of the run -- each test reporting a different freshly-generated
        # bucket name, which is what disguised one root cause as a flood of
        # unrelated ones. The same signature appeared in a dev container where
        # an unrelated process was pruning `/tmp`; that second environment is
        # why the invariant is written about the tree vanishing rather than
        # about any particular deleter.
        fixed_by=(1270,),
        seed_tests=("tests/test_conftest_cache_isolation.py",),
        # The seed test inspects a pytest fixture's own allocator directly, so
        # per this field's rule it claims no public surface.
        public_surfaces=(),
        axes={
            "removed_level": ("bucket", "basetemp", "ancestor"),
            "recovery": ("fresh_directory", "empty", "reused_when_intact"),
            "oracle": ("unfixed_allocator_reproduction",),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only this one allocator is repaired and pinned. Nothing "
                    "sweeps conftest for other autouse fixtures that would "
                    "fail the same way -- `_isolate_ast_memo` and the "
                    "hypothesis profile hooks touch no filesystem today, so "
                    "there is no second instance to fix, but a future autouse "
                    "fixture that creates a directory would not be caught by "
                    "any gate. The shape is also not mechanically detectable "
                    "from a fixture's text: creating a directory under "
                    "basetemp is not by itself a defect, only doing so without "
                    "recovery is."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
)
