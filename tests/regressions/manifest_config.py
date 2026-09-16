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
        fixed_by=(),
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
        fixed_by=(),
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
        fixed_by=(),
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
                reference="docs/contribute/plans/one-comparison-product.md#phase-7l",
            ),
        ),
    ),
)
