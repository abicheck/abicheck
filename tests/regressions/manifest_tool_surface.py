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

"""Bug classes about abicheck's own delivery surface, not its ABI analysis.

The CLI a user types, the CI that ships it, and the developer tooling
around both -- as opposed to every other entry in this registry, which is
about the ABI/API analysis abicheck *performs*. The distinction is real
rather than a convenience: a defect here is found by running the tool's own
interfaces (a `compare` invocation, a workflow job, a path key on another
platform), never by comparing two libraries.

Split out of ``manifest.py`` when that module hit its
`architecture/debt.yaml` ``no_growth`` baseline (Codex review, PR #1184).
``manifest.py`` keeps the registry's behavior -- ``get``/``all_ids``, the
id-uniqueness and seed-test-existence checks -- and becomes an assembly
point over per-axis entry lists, the same shape ``abicheck/
change_registry.py`` already has over ``model/change_catalog/``'s taxonomy
modules. Adding a class here needs no edit to ``manifest.py`` at all.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["TOOL_SURFACE_BUG_CLASSES"]

#: Registered in this order, appended after ``manifest.py``'s own list.
TOOL_SURFACE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="trust_boundary.shell_workflow_injection",
        invariant=(
            "Every scalar input to a shell script or composite-Action "
            "step arrives as exactly one argument with exactly the same "
            "bytes, and untrusted data cannot create additional commands, "
            "$GITHUB_OUTPUT records, paths, or side effects."
        ),
        fixed_by=(705, 758, 836, 919, 1165),
        seed_tests=(
            "tests/test_reusable_workflow_execution.py",
            "tests/test_check_project_workflow_execution.py",
            "tests/test_action_run_sh_helpers.py",
            # PR #1165: `action/validate-inputs.sh`'s own annotation
            # emitters. Every message there interpolates a
            # workflow-controlled INPUT_* value into a line-delimited
            # GitHub annotation, so a value carrying a newline forged an
            # `::error::`/`::set-output::` command of its own -- a live
            # defect on the pre-existing `build-info` site, not only on
            # the tombstones the same PR added. Fixed once in the shared
            # `_warn`/`_fail` helpers.
            "tests/test_action_validate_inputs.py",
        ),
        public_surfaces=("github-action",),
        axes={
            "adversarial-shape": (
                "path-traversal",
                "shell-metacharacters",
                "command-substitution",
                "spaces",
                "tab",
                "leading-dash-flag-shaped",
                "multiple-flags-shaped",
                "quotes",
                "redirects",
                "newline-record-injection",
                "non-ascii",
                "empty-string",
            )
        },
        known_gaps=(
            KnownGap(
                description=(
                    "`action/run.sh` emits its own annotations with "
                    'inline `echo "::error::..."` calls rather than '
                    "through a shared helper, and several interpolate an "
                    "INPUT_* value the same way validate-inputs.sh did. "
                    "PR #1165 fixed validate-inputs.sh at its two "
                    "emitters (covering every site in that file at once) "
                    "but deliberately did not widen into run.sh's ~20 "
                    "inline sites, which need their own pass and their "
                    "own executing corpus."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "The hostile-input execution corpus (shared via "
                    "`_workflow_exec.HOSTILE_SCALAR_CORPUS`, Phase 8) now "
                    "covers two independently-maintained real sanitizer "
                    "copies (`check-single.yml`/`check-project.yml`) plus "
                    "`action/run.sh`'s word-splitting-sensitive `add_flag`/"
                    "`add_sided_flag` helpers — not every scalar input "
                    "across the repository's other shell scripts and "
                    "composite-action steps (e.g. the other workflows' "
                    "`run:` steps enumerated in the plan's own target-"
                    "script inventory), which is still the full scope "
                    "Phase 8's invariant is stated over."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-8",
            ),
        ),
    ),
    BugClass(
        id="ci.unrelated_apt_source_gates_the_job",
        invariant=(
            "A CI lane never fails because of a package repository none "
            "of its packages come from. `apt-get update` fails as a whole "
            "when any configured source fails -- including the "
            "third-party vendor repositories pre-baked into GitHub's "
            "runner images -- so its exit status must never gate a step; "
            "`apt-get install` is the gate, and a post-install check "
            "confirms the packages are actually present so relaxing the "
            "first gate cannot turn into a silent false success."
        ),
        fixed_by=(1182,),
        seed_tests=("tests/test_apt_install_hardening.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The workflow-wide half of the invariant is "
                    "structural (parsed YAML), not executed: a step's own "
                    "wiring needs a real GitHub Actions runner. The "
                    "script's behaviour is executed against a simulated "
                    "apt; that a given lane calls the script is asserted "
                    "over the workflow files only. A lane could still "
                    "install packages through a mechanism this scan does "
                    "not model (a Makefile, a setup script it invokes)."
                ),
                reference="tests/test_apt_install_hardening.py",
            ),
        ),
    ),
    BugClass(
        id="test_fixture.host_artifact_assumed_capability",
        invariant=(
            "A test that needs a real binary artifact must acquire one that "
            "actually has the property under test, on every platform the "
            "suite runs, and must fail in *setup* when it cannot -- never "
            "grab whatever the host happens to have on PATH and let a "
            "downstream assertion report the mismatch. The two halves of "
            "this both bite: an artifact's *format* varies by host (the "
            "same `shutil.which` name is ELF, Mach-O or PE), and so does "
            "its *capability* (an ELF or Mach-O executable exposes "
            "symbols; a PE executable has no export directory at all). A "
            'fixture that conflates "a binary" with "a shared '
            'library" therefore passes on two platforms and fails on the '
            "third, and the failure surfaces as the tool refusing the "
            "input -- which reads as a bug in the tool rather than in the "
            "fixture. Renaming the artifact does not help and actively "
            "misleads, because abicheck sniffs content, not extensions: a "
            "PE file called `libfoo.so` is still a PE file."
        ),
        # 1197 fixed the instance on `main` independently and in the same
        # shape (Windows -> a real System32 DLL, POSIX -> the executable),
        # which is corroboration of the class rather than a competing
        # claim: two sessions hit it separately and converged. This entry
        # and its seed test are what `main` still lacks -- the fix landed
        # there with no registry entry, so the class was closed without
        # being named.
        fixed_by=(1188, 1197),
        seed_tests=("tests/test_compare_no_baseline_cli.py",),
        # A real Click invocation: the seed test drives `compare
        # --no-baseline` through `CliRunner().invoke(abicheck_main, ...)`,
        # which is exactly what this field's rule asks for (CodeRabbit).
        public_surfaces=("cli",),
        known_gaps=(
            KnownGap(
                description=(
                    "The Windows and macOS halves of the fix are not "
                    "verified on those platforms from the environment that "
                    "wrote them. The Windows branch was exercised by "
                    "forcing `sys.platform`, which proves it builds the "
                    "expected `System32` paths and skips diagnosably rather "
                    "than crashing -- not that abicheck dumps "
                    "`kernel32.dll` and honours `--version` there. The "
                    "specific defect cannot recur (a real DLL has an export "
                    "directory), but a different platform-specific failure "
                    "in the same test would be found by CI, not by this "
                    "registry entry."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1188",
            ),
            KnownGap(
                description=(
                    "Nothing detects the *silent fallthrough* shape "
                    "generally. The first version of this fix checked "
                    '`Path(ctypes.util.find_library("m")).is_file()` -- '
                    "false, because that call answers a soname rather than "
                    "a path -- and fell back to the very executable it was "
                    "written to replace. It passed on Linux and proved "
                    "nothing, and only a manual check of which artifact was "
                    "actually selected caught it. The helper now asserts "
                    "its chosen candidate is a binary abicheck recognises, "
                    "which closes that hole for this one helper; no gate "
                    "checks the other fixtures in this suite for a "
                    "candidate-list fallback that quietly lands on the "
                    "wrong element."
                ),
                reference="tests/test_compare_no_baseline_cli.py",
            ),
            KnownGap(
                description=(
                    "The fixture this class's fix *should* use -- a real "
                    "ELF shared library -- cannot be used yet, so the POSIX "
                    "branch still copies an executable. Auditing any real "
                    "ELF library raises an uncaught "
                    "`NoBaselineInvariantError`, because "
                    "`diff_platform_elf_dynamic._diff_visibility_leak` is "
                    "single-sided (`del new`) yet emits its finding with "
                    "neither candidate-side marker, so ADR-068's partition "
                    "files it as an identity-diff finding. That is a real "
                    "open defect on `main`, not a fixture problem, and it is "
                    "recorded in `docs/contribute/known-gaps.md` with a fix "
                    "shape (audit every detector that ignores `new`, plus a "
                    "gate, since neither the marker design nor the "
                    "`ChangeKind` allowlist it rejected is self-enforcing). "
                    "This class stays open in that sense: when the crash is "
                    "fixed the POSIX branch should move to a real library, "
                    "and nothing here will fail to remind anyone."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="ci.shared_budget_over_heterogeneous_matrix",
        invariant=(
            "A resource budget a CI job declares once -- `timeout-minutes` "
            "above all -- must resolve per matrix leg whenever the legs' "
            "real costs differ by more than the budget's own headroom. One "
            "shared number over a heterogeneous matrix has no correct "
            "value: sized for the slowest leg it discards the gate on every "
            "faster one, and sized for the faster ones it cancels the "
            "slowest leg's healthy runs. The failure is silent in the worst "
            "way -- a job-level timeout reports `cancelled`, which reads as "
            "a superseded run or a reclaimed runner rather than as a "
            "failure, so it can recur for days on `main` without being "
            "diagnosed. Raising the shared number is not a fix but a "
            "deferral: the budget's headroom decays as the suite grows, so "
            "the same cancellation returns once the slowest leg catches up "
            "again."
        ),
        # 1197 landed the same per-leg expression on `main` independently,
        # for the same stated reason (a job-level `timeout-minutes` hands
        # every other matrix cell the slow leg's budget). Convergent design,
        # differing only in the number; `main`'s 90 is better-evidenced than
        # this branch's original 75 and is what the merge took.
        fixed_by=(1188, 1197),
        seed_tests=("tests/test_verify_profiles.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "The invariant's *quantitative* half is only partly "
                    "executable. Whether a leg's budget clears its real "
                    "cost needs wall-clock measurements from completed CI "
                    "runs, which this repository does not ingest, so the "
                    "seed test pins hand-read figures as a literal oracle "
                    "(40.4/41.3/45.2/49.4/65.2 minutes). That rejects a "
                    "budget at or below the worst figure, but two holes "
                    "remain. (1) It cannot encode *margin*: the 65m14s "
                    "entry is a run that was KILLED, not completed, so its "
                    "true cost is unknown and only bounded below -- a "
                    "max-of-observed check therefore accepts 75, which "
                    "PR #1197 judged insufficient when it chose 90 over "
                    '65. Encoding "enough headroom for a length nobody has '
                    'seen" would need an arbitrary multiplier, which is a '
                    "guess dressed as a gate. (2) Nothing detects the decay "
                    "recurring above the pinned set: when the Windows leg "
                    "reaches 90 the test still passes against its stale "
                    "65.2. Closing both needs per-leg durations published "
                    "as a queryable artifact -- the canonical lane emits "
                    "`test-durations.json` for individual tests, but no "
                    "lane records its own total against its budget."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1188",
            ),
            KnownGap(
                description=(
                    "Scoped to `unit-tests`, the one job observed "
                    "cancelling. No audit was run over this repository's "
                    "other matrix jobs (`integration-tests`, the PE/Mach-O "
                    "and packaging lanes, `check-project.yml`'s own "
                    "`plan`/`check`/`aggregate`) to find which of them also "
                    "share one budget across legs of materially different "
                    "cost -- the same defect, unlooked-for, in every job "
                    "this fix did not touch."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1188",
            ),
        ),
    ),
    BugClass(
        id="tooling.platform_dependent_path_key",
        invariant=(
            "A repo-relative path computed to serve as a lookup/comparison "
            "key -- an allowlist entry, a cache key, anything compared "
            "against a hand-written or previously-persisted forward-slash "
            "string -- must render with `.as_posix()`, never bare "
            "`str(Path)`. `str()` of a relative `Path` renders with the "
            "*host's native* separator (backslash on Windows), so a key "
            "computed that way silently fails to match every "
            "forward-slash-spelled entry on Windows alone, in both "
            "directions: an already-reviewed, allowlisted call site reads "
            "as a fresh, unreviewed violation, and a genuinely live "
            "allowlist entry reads as stale. Distinct from the "
            "canonical-identity `environment_taint` shape this registry "
            "otherwise tracks -- this is a path-*rendering* bug, not an "
            "identity or environment-capture one, and it fails "
            "deterministically on every Windows run rather than "
            "intermittently."
        ),
        fixed_by=(995, 1004),
        seed_tests=("tests/test_fact_bridged_replace_guard.py",),
        known_gaps=(
            KnownGap(
                description=(
                    "This class's fix and seed test are scoped to the one "
                    "file CI actually reported as broken "
                    "(`tests/test_fact_bridged_replace_guard.py`). No "
                    "repo-wide audit was run against the other AST-scan "
                    "gates that compute a similar path-shaped lookup key "
                    "(`scripts/check_ai_readiness.py`, "
                    "`scripts/fact_detector_misuse.py`/`fact_field_"
                    "readers.py` and their siblings) to confirm none of "
                    "them share the identical `str(path.relative_to(...))` "
                    "spelling -- a real, separate follow-up this class "
                    "does not yet close."
                ),
                reference="https://github.com/abicheck/abicheck/pull/1004",
            ),
        ),
    ),
    BugClass(
        id="cli_surface.retired_spelling_in_remediation",
        invariant=(
            "Remediation a tool prints must actually work when run: every "
            "`--flag` token in a user-facing message must be a live option "
            "of the command being advised, AND any concrete example it "
            "offers must resolve the condition it is offered for -- so "
            "following the tool's own advice can never itself be a usage "
            "error. Flag existence alone is not the invariant: a live flag "
            "aimed at the wrong operand fails just as hard."
        ),
        fixed_by=(1184,),
        # PR #1187: these tests moved out of
        # `test_cli_compare_release_project_snapshot_package.py` when that
        # module was split at the architecture contract's test-size limit.
        # Pointing this at the old module still passed the registry's
        # integrity check -- it only verifies the named file *exists*, not
        # that it exercises the invariant -- while sending a future
        # bug-class audit to a module with zero coverage of it (Codex
        # review).
        seed_tests=("tests/test_cli_compare_variant_selection.py",),
        public_surfaces=("cli",),
        axes={
            "error_path": (
                "ambiguous-variant",
                "unknown-variant-id",
                "empty-variant-id",
                "unknown-both-sides-id",
                "zero-variants-declared",
            ),
            "ambiguous_side": ("old", "new"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test applies the oracle only to the variant "
                    "family's own error paths. A repo-wide AST sweep run "
                    "while fixing this (non-docstring string literals under "
                    "`abicheck/` containing any spelling in "
                    "`scripts/retired_surfaces.py`'s RETIRED_SURFACES) "
                    "reports 44 hits across 25 modules, and several look "
                    "like the same defect already sitting in the tree -- "
                    "`pdb_utils.py`'s 'use --pdb-path to override', "
                    "`reporter_markdown.py`'s 'Unknown --show-only token', "
                    "`dumper.py`'s '--dwarf-only requested but ...'. They "
                    "are NOT mechanically decidable: a flag retired from "
                    "`compare`/`dump` can still be live on `scan` (which "
                    "kept the whole compile-context and debug-resolution "
                    "families), and `cli_compare_release.py`'s unregistered "
                    "release engine legitimately still *defines* several of "
                    "them, so a sweep-turned-gate would need ~25 "
                    "hand-judged allowlist entries -- the shape AGENTS.md "
                    "warns is itself a smell. Each site needs reading "
                    "against the command that emits it. Deliberately not "
                    "attempted in PR #1184: it is a separate change from "
                    "the CLI option audit, and a hasty sweep would ship a "
                    "large unreviewed allowlist rather than close the class."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="cli_surface.capability_guard_diverged_from_pipeline",
        invariant=(
            "A front-end guard may reject an input the pipeline behind it "
            "would silently drop, but never a *capability* the pipeline can "
            "answer for itself from real evidence. Where the pipeline "
            "already decides per operand -- an evidence rung it floor-checks "
            "and projects, a derivation it performs whenever its inputs are "
            "present -- the front end forwards and lets it decide, so the "
            "same request means the same thing at every operand cardinality "
            "(AGENTS.md's 'One model, any cardinality'). Corollary, and the "
            "half that actually bites: a capability claim recorded only in a "
            "comment or plan document is unfalsifiable, so every such claim "
            "needs an executed test whose oracle is the other surface, not a "
            "pinned constant and not the same table the implementation "
            "consults."
        ),
        fixed_by=(1195,),
        seed_tests=(
            "tests/test_cli_compare_release_depth.py",
            "tests/test_cli_compare_release_header_graph.py",
        ),
        public_surfaces=("cli",),
        axes={
            "guard": ("depth-rung-allow-list", "header-graph-attach-claim"),
            "depth_rung": ("binary", "headers", "build", "source"),
            "member_evidence": ("binary", "headers", "build"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Two instances of this class were found in one place "
                    "(`compare`'s set-input guards) and both are now "
                    "executed. No sweep was run for the rest: the pattern "
                    "is 'a `_reject_*_for_set_inputs` guard whose message "
                    "asserts what the fan-out cannot do', and "
                    "`cli_compare_options.py`/`cli_resolve.py` carry "
                    "several more of exactly that shape "
                    "(`--require-complete-analysis`, `--budget`, "
                    "`--env-matrix`, `--use-cases`, the sided "
                    "`--ast-frontend`). Each needs reading against what its "
                    "pipeline does *today*, one at a time -- the two closed "
                    "here were both stale, so the base rate is not "
                    "reassuring, but a mechanical gate cannot tell a stale "
                    "claim from a live one."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "Closing the depth guard exposed a real "
                    "cardinality-dependent divergence it had been hiding "
                    "(all four measured rows differed, not just hard-vs-"
                    "soft). Closed in the same PR: `resolve_compare_request` "
                    "no longer calls `enforce_requested_depth`, so ADR-064's "
                    "exit-7 axis governs every `compare` surface, and the "
                    "seed test's matrix now uses the single-pair path as its "
                    "oracle. **Residual:** `workflows.bundle_stored_pair_"
                    "compare` keeps its own `enforce_requested_depth` call, "
                    "so a stored bundle-facts pair still hard-fails on the "
                    "`headers` rung -- a third answer to the same question, "
                    "pinned by `test_cli_compare_bundle_facts_stored_pair.py` "
                    "as a deliberate contract from PR #1060 and therefore "
                    "left alone rather than unified on this review round."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
