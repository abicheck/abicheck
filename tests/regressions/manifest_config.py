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

"""Bug classes about *configuration input*: the values a run is given.

A themed sibling of `manifest.py`, following the same per-area split
`manifest_guards.py`/`manifest_report.py`/`manifest_tool_surface.py`/
`manifest_evidence.py` already establish. The question these classes share
is what a supplied value means -- across front ends, across consumers, and
across the whole domain of values a user might plausibly write.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

CONFIG_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="config.front_end_default_divergence",
        invariant=(
            "An option offered by more than one front end carries the same "
            "default in each, so a caller that states nothing gets the same "
            "behavior whichever front end it used."
        ),
        fixed_by=(1258,),
        seed_tests=(
            "tests/test_front_end_default_parity.py",
            # F2 route parity (PR #TBD): whole-report CLI vs typed API vs
            # release member, plus a strict xfail on the one default still
            # divergent (`pattern_verdicts`, below).
            "tests/test_family_f2_route_parity.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "`pattern_verdicts` still diverges: the native compare CLI "
                    "and the release fan-out pass True, while CompareRequest/"
                    "service.run_compare (and compare_snapshots/checker."
                    "compare/no_baseline_compare) default False. ADR-027 keeps "
                    "the default-on flip deferred pending release-cycle "
                    "FP-rate/parity validation, while ADR-068 D4 (accepted) "
                    "calls modulation automatic; reconciling them is an ADR-027 "
                    "amendment plus a typed-API default change (and every "
                    "typed-API effective_config_digest's policy.pattern_verdicts "
                    "field), not a parity fix. Pinned by "
                    "test_pattern_verdicts_default_matches_cli's strict xfail."
                ),
                reference="PR #TBD (fix/family-findings-b)",
            ),
            KnownGap(
                description=(
                    "The *typed surfaces* are now derived, not listed: the "
                    "sweep walks abicheck.service.__all__ plus the InputSpec "
                    "field/of and run_dump's synthetic signature, so a new or "
                    "renamed public entry point carrying the option is covered "
                    "the moment it exists. That half was a real gap and it bit "
                    "immediately -- the first revision listed three surfaces "
                    "and review found resolve_input and run_compare still "
                    "defaulting the other way, run_compare writing its value "
                    "into both InputSpecs and so overriding the field default "
                    "the test did check. What remains hand-maintained is "
                    "SHARED_OPTIONS, the list of *options* reachable from more "
                    "than one front end (one entry today). Deriving that too "
                    "means matching Click dests against typed parameter names "
                    "across every command, which would also sweep in "
                    "coincidental name collisions; left listed deliberately."
                ),
                reference="PR #1258",
            ),
        ),
    ),
    BugClass(
        id="config.option_dropped_at_a_dispatch_branch",
        invariant=(
            "An option the front end accepts reaches every dispatch branch "
            "that can act on it, or the branch rejects it. A branch that "
            "silently discards a value the parser accepted is the one "
            "failure mode nothing announces: the flag parses, the run "
            "proceeds, and the only symptom is that the thing the flag was "
            "asked to prevent happens anyway -- while the run still records "
            "the option as part of its resolved configuration. Concretely: "
            "given the same arguments, a set-shaped operand (a directory, a "
            "package, a fan-out) and a scalar operand resolve the same "
            "option to the same value, and the option contributes to the "
            "effective-configuration identity either way, so two runs "
            "configured differently cannot fingerprint identically."
        ),
        fixed_by=(1321,),
        seed_tests=(
            "tests/test_release_header_exclusions.py",
            # The identity half of the same invariant: an option that
            # reaches every branch still fails it if two differently
            # configured runs render one identity. Three ways that
            # happened here -- a non-injective join over arbitrary glob
            # text, an asymmetric pair read from one side only, and a
            # release identity taken from the request rather than from
            # what its members observed.
            "tests/test_header_exclusion_primitives.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "No mechanical sweep exists for the class. The seed "
                    "test states the parity invariant for "
                    "`--exclude-header` -- the reported instance, where "
                    "`compare`'s directory/package branch forwarded every "
                    "other header input and not this one, so an MKL release "
                    "tree failed all 28 libraries under the exact arguments "
                    "that made the single-library comparison exit 0 -- but "
                    "the release fan-out re-states roughly forty parameters "
                    "by hand, and nothing compares that list against "
                    "`compare`'s own. A gate would mean pinning the two "
                    "parameter sets against each other, which is real but "
                    "larger than this fix: several of `compare`'s options "
                    "are legitimately rejected rather than forwarded for a "
                    "set operand (`--view leaf`, `-o sarif=`), so the "
                    "correct invariant is 'forwarded or rejected', not "
                    "'forwarded'."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="config.input_granted_unrelated_authority",
        invariant=(
            "An input consulted for one purpose does not silently grant "
            "authority for another. A path given so a tool can *find* "
            "something is not a declaration that the thing found is owned, "
            "public, or promised; a value read to *resolve* a reference is "
            "not a claim about what that reference means. Where the two "
            "questions genuinely overlap, the overlap is established "
            "structurally -- by what the caller separately declared -- not "
            "by the fact that one input happened to be consulted."
        ),
        fixed_by=(1321,),
        seed_tests=("tests/test_dependency_include_root_ownership.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test covers the reported instance and the "
                    "containment primitive behind it: a dependency's `-I` "
                    "root (compile context) was promoted wholesale into the "
                    "public-provenance set (ownership), so Intel MKL -- "
                    "which passes an MPI include directory solely so "
                    "`mkl_cdft.h` can parse `#include <mpi.h>` -- reported "
                    "2,211 `public_not_exported` findings about an API it "
                    "does not own. What is not mechanized is the *search* "
                    "for other inputs carrying two meanings at once; each "
                    "is found by noticing that a consumer reads a value for "
                    "a question the value was never answering."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="config.sided_shared_input_dropped",
        invariant=(
            "A repeatable option modelled as 'a both-sides value plus "
            "per-side additions' composes additively on every front end: a "
            "side's effective list is its own entries followed by the "
            "shared ones, never its own entries instead of them. Naming "
            "something on one side must not silently discard what the "
            "caller declared for both -- and since a caller wanting "
            "disjoint lists simply names nothing shared, the additive rule "
            "expresses everything replacement did and one thing more."
        ),
        fixed_by=(1314,),
        seed_tests=(
            "tests/test_sided_include_composition.py",
            "tests/test_cov95_cli.py",
        ),
        public_surfaces=("compare", "compare --dry-run", "release fan-out"),
        axes={
            "role": ("--header", "--include"),
            "front_end": (
                "single-pair compare",
                "release/directory fan-out",
                "no-baseline",
                "bundle-facts",
                "dry-run receipt",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Reported independently from a PVXS run and an Intel MKL "
                    "run: a shared --include holding a dependency header, "
                    "plus per-side roots, resolved to the per-side roots "
                    "alone and the parse failed on the dependency it had "
                    "been given. `split_sided_paths` documented the additive "
                    "contract while every consumer implemented replacement, "
                    "and several tests and comments encoded the replacing "
                    "behavior as intended -- which is why this is registered "
                    "as a class rather than a single call-site fix. The one "
                    "composition rule now lives in "
                    "`abicheck/model/sided_inputs.py`; a new consumer that "
                    "hand-rolls `side or shared` re-opens the class, and "
                    "nothing mechanically prevents that today."
                ),
                reference="abicheck/model/sided_inputs.py",
            ),
        ),
    ),
    BugClass(
        id="config.rule_language_class_collapsed",
        invariant=(
            "A rule language with several matching *classes* is matched by "
            "all of them or by none: implementing one class and applying it "
            "to every rule makes every out-of-class rule match nothing, "
            "silently, while the run still produces a confident verdict. "
            "And a rule that matched nothing is never recorded as an "
            "achieved narrowing of the analyzed surface."
        ),
        fixed_by=(1314,),
        seed_tests=(
            "tests/test_descriptor_skip_rules.py",
            "tests/test_compat_dump_descriptor_expansion.py",
        ),
        public_surfaces=("compat check", "compat dump"),
        axes={
            "rule_class": ("name", "path", "directory", "pattern", "absolute"),
            "separator": ("posix", "windows"),
            "element": ("skip_headers", "skip_including"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "ABICC's `<skip_headers>` means 'do not include and do "
                    "not analyze'. Both it and `<skip_including>` now "
                    "correctly drop a header from the *direct* -H operand "
                    "list, and only `<skip_headers>` is recorded as a real "
                    "narrowing -- but neither can stop a header being parsed "
                    "when another header reaches it through its own "
                    "`#include`. The native `--exclude-header` path shares "
                    "that limitation (both filter the resolved header list, "
                    "post-walk), so closing it means a post-parse filter by "
                    "defining header, not a change to either rule language. "
                    "Until then the docs must not claim complete support for "
                    "`<skip_headers>`."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="config.inferred_root_bypassed_by_a_second_entry_point",
        invariant=(
            "A resolution step that every entry point owes its inputs is "
            "performed by every entry point. A front end that reaches past "
            "the shared resolver into the low-level extractor gets a "
            "different -- and usually worse -- answer than the identical "
            "request through any other route, and the failure names neither "
            "the step nor the front end."
        ),
        fixed_by=(1314,),
        seed_tests=("tests/test_descriptor_include_inference.py",),
        public_surfaces=("compat check", "compat dump"),
        known_gaps=(
            KnownGap(
                description=(
                    "`resolve_inferred_header_roots` already existed and "
                    "already returned the right root; the ABICC compat path "
                    "expanded the descriptor's <headers> directory into "
                    "individual files and called `dumper.dump` directly, so "
                    "nothing inferred anything and MKL's own descriptor "
                    "could not compile without a hand-added <include_paths>. "
                    "The `cli-contract` AI-readiness check gates exactly this "
                    "shape for `checker.compare`/`dumper.dump`/"
                    "`service.resolve_input`, but `compat/cli.py` is on its "
                    "reviewed legacy allowlist, so the gate did not catch "
                    "this one. Shrinking that allowlist is the durable close."
                ),
                reference="scripts/check_ai_readiness.py CLI_CONTRACT_ALLOWLIST",
            ),
        ),
    ),
    BugClass(
        id="config.command_specific_discovery",
        invariant=(
            "Every command selects its project config with one rule "
            "(config_paths.resolve_project_config: --config > --sources "
            "root > nearest config above the front end's search root), and "
            "trust follows how the config was chosen, never whether a path "
            "was passed: a discovered config contributes passive settings "
            "but never runs build.query."
        ),
        fixed_by=(1346,),
        seed_tests=("tests/test_project_config_resolution.py",),
        public_surfaces=("dump",),
    ),
    BugClass(
        id="config.env_flag_value_domain",
        invariant=(
            "Every ABICHECK_* boolean environment knob answers the same "
            "value domain: 1/true/yes/on is on, 0/false/no/off is off "
            "(case- and whitespace-insensitive) whichever way the knob's own "
            "default points, and unset/empty/unrecognized resolves to that "
            "default -- never to its opposite. One registered parser, no "
            "per-call-site token set."
        ),
        fixed_by=(1278,),
        seed_tests=("tests/test_env_flags.py",),
        axes={
            "default_polarity": ("opt-in", "opt-out"),
            "value": (
                "unset",
                "empty",
                "whitespace",
                "1/true/yes/on",
                "0/false/no/off",
                "mixed-case",
                "arbitrary-text",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Two ABICHECK_* booleans (ALLOW_AST_FALLBACK, "
                    "ALLOW_UNSUPPORTED_CASTXML) are ALSO reachable as Click "
                    "`envvar=` flags, where Click's own BOOL conversion -- "
                    "not this parser -- reads them, and it rejects an "
                    "unrecognized value as a usage error instead of falling "
                    "back to the default. The two agree on all ten tokens; "
                    "they diverge only on arbitrary text, and only on the "
                    "commands still registering those flags. Unifying means "
                    "a custom Click ParamType, which is a change to the "
                    "option layer rather than to the readers this class "
                    "covers."
                ),
                reference="docs/contribute/plans/one-comparison-product.md#phase-7l-external-cli-audit-2026-09-12-reconciled",
            ),
        ),
    ),
    BugClass(
        id="config.propagation_completeness",
        invariant=(
            "An accepted configuration value either reaches every "
            "relevant consumer with identical semantics, or is rejected "
            "at the public boundary — no third state."
        ),
        fixed_by=(860, 883, 886, 906),
        seed_tests=(
            "tests/test_run_plan.py",
            "tests/test_run_plan_consumer_compile_active.py",
            "tests/test_project_targets_consumer_compile.py",
            "tests/test_cli_compare_release_bundle_signature_wiring.py",
            "tests/test_reusable_workflows_project_evidence.py",
            "tests/test_action_compile_context_parity.py",
            "tests/test_gha_expr.py",
            "tests/test_consumer_compile_full_chain_propagation.py",
            "tests/test_l4_frontend_propagation.py",
            # castxml compiler emulation: a -std/--sysroot/-m*/feature -f
            # flag reached castxml's parser but not the emulated compiler's
            # macro/include query. Oracle: the real compiler's -dM output.
            "tests/test_castxml_compiler_emulation.py",
            # `compare --no-baseline` resolved the project config and read
            # 6 of its fields. Oracle: the written `.abicheck.yml`.
            "tests/test_no_baseline_config_settings.py",
            # `compat check -source`/`-src-report-path` never reached the
            # HTML report's kind. Oracle: ABICC's own flag table.
            "tests/test_compat_report_kind.py",
            # `project history --policy DOC` read the path as a profile name.
            # Oracle: the deprecation-window rule as the docs state it.
            "tests/test_cli_project_history_policy.py",
            # The pimpl inline-accessor detector never received the policy's
            # internal_namespaces. Oracle: the documented convention, and
            # agreement with internal_type_leaks_via_public_api.
            "tests/test_inline_body_internal_namespaces.py",
            # The S2 pre-scan never received the configured compiler.
            # Oracle: the option's documented selection rules, and L4's pick.
            "tests/test_preprocessor_scan_compiler.py",
            # The coverage and scope notices advised the setting the run
            # already had. Oracle: the settings' documented meaning.
            "tests/test_notice_advice_matches_setting.py",
            # `compare --dry-run` priced a compile DB and a source scope the
            # run does not use. Oracle: the run's own L3 collector.
            "tests/test_compare_dry_run_compile_db.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "Still open: (a) this closed chain reaches config -> "
                    "generate_run_plan() -> the composite-Action/reusable-"
                    "workflow path only — no seed test drives the same "
                    "consumer_compile value through the native Python API "
                    "or a `project`/`aggregate` CLI invocation end to end; "
                    "(b) Phase 6 names eight other configurable concerns "
                    "(policy/policy-file, frontend/compiler as a general "
                    "per-entry-point concern beyond this one profile field, "
                    "include roots, evidence-pack/target attribution, "
                    "safety budgets, suppression/filtering, per-library "
                    "override, output/report options) — of these only the "
                    "frontend concern has since had any of this treatment, "
                    "and only for one chain: `--ast-frontend` -> the L4 "
                    "source-ABI replay backend that `compare`/`dump` select "
                    "through `effective_frontend` "
                    "(tests/test_l4_frontend_propagation.py, exhaustive over "
                    "the frontend x env domain, grounded in "
                    "_make_source_extractor). `scan` once ignored the value "
                    "for L4; its separate resolver went with `scan` "
                    "(ADR-068 Phase 6) and the dead-code plan's Stage D, "
                    "and the end-to-end half of that coverage "
                    "(tests/test_dump_scan_l3_comparability.py) went with "
                    "it. The same concern's *other* consumers (the L2 "
                    "header parse, the "
                    "preprocessor/pattern pre-scans) are untouched. "
                    "consumer_compile was chosen as the first "
                    "worked example specifically because #860/#883's own "
                    "history and this class's pre-existing seed tests "
                    "already pointed at it, not because it's necessarily "
                    "representative of the others' own chain shapes."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-6",
            ),
        ),
    ),
)
