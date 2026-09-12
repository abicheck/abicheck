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
    # Two entries from one CI audit, sharing one shape: a configured
    # limit that is silently inert (a concurrency key that can never
    # cancel) or silently catastrophic (a safety ceiling handed to the
    # allocator as a size) on a platform the fast lane does not run on.
    # Both are found by running abicheck's own delivery surface -- a
    # workflow job, and the wall clock of a Windows test lane -- never by
    # comparing two libraries, which is this module's own scope line.
    BugClass(
        id="storage.safety_limit_used_as_allocation_size",
        invariant=(
            "A safety *ceiling* may bound how much a reader accepts; it "
            "may never be handed to an allocator as a *size*. Concretely, "
            "for every supported storage envelope and every content size: "
            "the peak allocation of a read is a function of the file's "
            "content, not of the configured cap -- sweeping the cap "
            "across four orders of magnitude with the content fixed "
            "leaves the peak flat, while the read still round-trips the "
            "content exactly and still refuses a file past the cap. "
            "`read_snapshot_bytes` violated this with "
            "`f.read(cap + 1 - len(prefix))`, allocating ~1 GiB to read an "
            "8 KiB snapshot; Linux's overcommitting allocator hid it "
            "entirely, so it surfaced only as Windows CI wall clock "
            "(118.03s -> 1.80s on one stored-package comparison test). "
            "The complement direction is part of the invariant: a reader "
            "that allocated a constant tiny buffer and truncated would "
            "satisfy the flatness half alone."
        ),
        fixed_by=(1240,),
        seed_tests=("tests/test_snapshot_read_allocation.py",),
        # `()` per the field's own rule: the seed test calls
        # `abicheck.snapshot_io` directly, never through `abicheck.
        # service`, so it reaches no documented public surface. A claimed
        # surface a seed test does not reach conceals exactly the missing
        # cross-surface coverage this registry exists to surface
        # (CodeRabbit review).
        public_surfaces=(),
        axes={
            "algorithm": ("none", "gzip", "zstd"),
            "cap_magnitude": ("4MiB", "64MiB", "1GiB", "2GiB"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The invariant is stated and tested only for the "
                    "snapshot storage reader. Other bounded readers in "
                    "the tree take a limit-shaped argument the same way "
                    "(`snapshot_cache.py`'s archive member reads, the "
                    "PE/Mach-O section readers, "
                    "`workflows/bundle_compare_operand.py`'s probe); none "
                    "is tested against the allocation half of this class, "
                    "and the platform that exposes it (Windows, a "
                    "committing allocator) is not where the fast unit "
                    "lane runs, so a sibling regression would again be "
                    "invisible on Linux."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="ci.path_filter_omits_own_build_infrastructure",
        invariant=(
            "A workflow's `paths:` filter must cover the files that decide "
            "*how* it runs, not only the inputs it validates: the package "
            "metadata its jobs pip-install from, the composite actions it "
            "calls, and the scripts those actions execute in turn. "
            "Otherwise a change to one of them merges without a single job "
            "that depends on it having run, and nothing fails -- the "
            "filter looks careful and is silently incomplete. `ci.yml`'s "
            "`heavy-parity-gate` already named `pyproject.toml`, "
            "`action/**`, `.github/actions/**` and `scripts/verify.py` for "
            "this reason, citing the sharpest case (re-pinning CastXML in "
            "`action/install-castxml.sh` landing with no CastXML job run); "
            "three other path-filtered workflows had the identical "
            "dependency and none of the entries. The dependency set is "
            "derived from each workflow rather than listed, so a workflow "
            "that starts using a new script is covered when it does."
        ),
        fixed_by=(1240,),
        seed_tests=("tests/test_workflow_path_filter_closure.py",),
        # `()` per the field's own rule: these seed tests read and parse
        # workflow/config files, they do not invoke the CLI, `abicheck.
        # service`, or a real workflow run. A claimed surface a seed test
        # does not reach conceals exactly the missing cross-surface
        # coverage this registry exists to surface (CodeRabbit review
        # raised it for the storage entry; the same rule decides all of
        # them).
        public_surfaces=(),
        axes={
            "dependency": ("package-metadata", "composite-action", "action-script"),
            "event": ("push", "pull_request"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Dependencies are derived from `run:` text by regex -- "
                    "an interpreter invocation naming an existing repo "
                    "file. A script reached indirectly (a Makefile target, "
                    "a script that execs a sibling, a tool resolved from "
                    "PATH after `pip install`) is not seen, and neither is "
                    "a data file a job merely reads. `pixi.lock` is a "
                    "concrete instance: a pixi-driven job's real dependency "
                    "set is not derived at all. The `**` glob is also "
                    "matched permissively on purpose, so an over-broad "
                    "filter entry is never reported."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="ci.instrumentation_without_a_consumer",
        invariant=(
            "Measurement a CI lane collects must have a reader: an upload, "
            "an artifact, or a gate. Coverage instrumentation costs ~60% "
            "wall time on every lane that carries it, so a report nobody "
            "reads is pure loss -- and it reads as diligence, which is why "
            "it survives review. The macOS unit lane wrote a "
            "`coverage.xml` the Codecov step (gated to Linux/3.13) never "
            "took, and the `slow` lane wrote a `coverage-slow.xml` that "
            "nothing in the repository referenced at all. The sibling half "
            "of the same class is configuration that cannot take effect: "
            "`COVERAGE_CORE=sysmon` requested under `branch = true` below "
            "Python 3.14, where coverage.py warns and silently falls back "
            "to CTracer -- so what is requested and what runs must either "
            "agree or the disagreement must be stated where the setting "
            "is."
        ),
        fixed_by=(1240,),
        seed_tests=(
            "tests/test_coverage_core_effectiveness.py",
            # The consumer half, left as a known gap by PR #1240 and closed
            # immediately after. Closing it found a third site that PR's own
            # inspection had missed -- the integration lane, whose producer
            # runs on Linux *and* macOS while its Codecov upload is gated to
            # ubuntu -- which is the argument for making a rule executable
            # rather than writing it down.
            "tests/test_workflow_coverage_consumers.py",
            # The evaluator both guards rest on, with its own contract
            # suite -- chiefly *which way it fails*.
            "tests/test_gha_expression_evaluator.py",
        ),
        # `()` per the field's own rule: these seed tests read and parse
        # workflow/config files, they do not invoke the CLI, `abicheck.
        # service`, or a real workflow run. A claimed surface a seed test
        # does not reach conceals exactly the missing cross-surface
        # coverage this registry exists to surface (CodeRabbit review
        # raised it for the storage entry; the same rule decides all of
        # them).
        public_surfaces=(),
        axes={
            "half": (
                "requested-vs-selected-core",
                "documented-fallback",
                "report-has-a-consumer",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The consumer half checks each *matrix combination*, "
                    "which needs every step's `if:` evaluated against it, so "
                    "it inherits `_gha_expressions`' deliberate subset: an "
                    "unmodelled reference or function evaluates as *absent*. "
                    "That direction is deliberate but not uniformly safe. "
                    "For a consumer it fails closed (the step is not counted "
                    "as reading the report, so at worst a real consumer is "
                    "reported as missing); for a *producer* it fails open "
                    "(the step is not counted as writing one, so an orphaned "
                    "report under an unmodelled `if:` is missed). Both beat "
                    "the original defect, where an unmodelled token returned "
                    "truthy and made any such step read as active. A matrix "
                    "built from an expression cannot be expanded statically "
                    "at all and is scanned as one nameless combination. The "
                    "core-effectiveness test also probes the interpreter "
                    "running the suite, not the pinned Python of each CI "
                    "lane, so a lane on a different version is checked only "
                    "through the stated caveat, not by measurement."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="tests.locale_dependent_repo_text_read",
        invariant=(
            "A test that reads the repository's own checked-in text must "
            "state the encoding. `Path.read_text()` and `open(path)` with no "
            "`encoding=` decode using the *host's* preferred encoding -- "
            "UTF-8 on the Linux and macOS lanes, `cp1252` on a default "
            "Windows runner -- so a UTF-8 file carrying any non-ASCII byte "
            "reads fine on two platforms and raises `UnicodeDecodeError` on "
            "the third. This repository's own workflow comments are full of "
            "em dashes and section signs, so the condition is not "
            "hypothetical: four `test_workflow_*.py` guards each read "
            "`.github/workflows/*.yml` at import time with a bare "
            "`read_text()` and took down the Windows unit lane at "
            "*collection*, before any assertion ran. The guard is structural "
            "(no repository-rooted read leaves the encoding to the platform) "
            "plus non-vacuity (some checked-in workflow really does contain "
            "a byte cp1252 rejects), because the structural rule alone would "
            "pass the day the repository became pure ASCII."
        ),
        fixed_by=(1244,),
        seed_tests=("tests/test_repo_text_reads_state_their_encoding.py",),
        # `()` per the field's own rule: the seed test parses repository
        # files with `ast`; it invokes no CLI, no `abicheck.service`, and no
        # real workflow run.
        public_surfaces=(),
        axes={
            "half": ("structural-scan", "non-vacuity-of-the-content"),
            "read_form": ("Path.read_text", "open"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The scan recognises a repository-rooted path only when "
                    "it is built from a root constant this suite actually "
                    "uses (`REPO_ROOT`, `WORKFLOW_DIR`, `ROOT`, "
                    "`PROJECT_ROOT`). A test that re-derives the root into a "
                    "differently-named local, or reaches checked-in content "
                    "through a fixture-returned path, is not covered -- the "
                    "same shape as reading it with `subprocess` or "
                    "`importlib.resources`. Nothing here runs on a real "
                    "cp1252 host either: the failure is reproduced locally "
                    "under `LC_ALL=C`, which is a stricter ASCII default, "
                    "not cp1252 itself."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="ci.inert_concurrency_group_key",
        invariant=(
            "A concurrency group keyed off a value that is unique per run "
            "makes `cancel-in-progress: true` a no-op: every run lands in "
            "its own group, so a superseded run is never cancelled and "
            "instead holds a runner or a queue slot until it finishes. "
            "Stated behaviourally, for every workflow that opts into "
            "cancellation: two successive events for the *same* logical "
            "unit (the same pull request, or the same branch ref) must "
            "render the same group string even though they differ in "
            "`run_id` and `sha`, and two *different* units must not. Six "
            "read-only validation workflows used "
            "`${{ github.event.pull_request.number || github.run_id }}`, "
            "correct for a pull request and silently inert for a `push` "
            "-- a push carries no PR number, so it fell through to "
            "`run_id`. The complement half of the invariant is load-"
            'bearing: the literal group `"x"` satisfies the first half '
            "while cancelling every unrelated run in the repository."
        ),
        fixed_by=(1240,),
        seed_tests=("tests/test_workflow_concurrency_grouping.py",),
        # `()` per the field's own rule: these seed tests read and parse
        # workflow/config files, they do not invoke the CLI, `abicheck.
        # service`, or a real workflow run. A claimed surface a seed test
        # does not reach conceals exactly the missing cross-surface
        # coverage this registry exists to surface (CodeRabbit review
        # raised it for the storage entry; the same rule decides all of
        # them).
        public_surfaces=(),
        axes={
            "event": ("pull_request", "push"),
            "unit": ("same", "different"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The guard evaluates a small GitHub-expression subset "
                    "(parenthesised groups, `||`, `&&`, `==` against a "
                    "literal) and models a fixed set of `github.*` context "
                    "fields; an unmodelled field evaluates as absent "
                    "rather than raising, so a future group expression "
                    "using `github.event.merge_group.*`, a function call "
                    "such as `format(...)`, or a job-level `concurrency:` "
                    "block (not surveyed at all -- only the workflow-level "
                    "key is) would be checked loosely or not checked. The "
                    "class is also asserted only against synthesized "
                    "contexts; nothing here verifies GitHub's real "
                    "grouping semantics on a hosted run."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
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
            # PR #1240: the same class one layer out -- the *workflow*
            # rather than a script it calls. Attacker-controlled free text
            # (a PR title/body/head ref, an issue or comment body) is
            # substituted by `${{ }}` before the shell parses the script,
            # so it must reach a `run:` block only as an `env:` value.
            # Both existing sites already did that, correctly and with a
            # comment saying why, but nothing enforced it: the next
            # workflow to add such a step had no guard. Pairs a repo-wide
            # structural scan with a real execution of the real step
            # against hostile payloads, plus a control proving the harness
            # can detect an injection at all.
            "tests/test_workflow_untrusted_text_interpolation.py",
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
        id="cli_surface.copied_option_table_went_stale",
        invariant=(
            "Where a surface outside the CLI enumerates CLI options in its "
            "own source (the composite Action's `extra-args` tokenizer), "
            "that enumeration is checked against live Click introspection "
            "in BOTH directions and over EVERY command the surface can "
            "invoke. A missing entry and a surplus entry are different "
            "bugs with asymmetric costs, not two spellings of one: a "
            "missing entry under-recognizes an option, so a value that "
            "resembles a flag is read as one and a guard rejects argv the "
            "real CLI accepts -- a false REJECTION of a valid check, which "
            "is the shipped harm (#1222). A surplus entry -- a name the CLI "
            "no longer takes a value for -- has the reverse shape: it "
            "swallows the following real flag as its value, so a guard "
            "reading for that flag answers 'absent'; the run still dies on "
            "the unknown option, so its cost is a degraded diagnostic "
            "rather than a false pass. Neither direction is the 'safe' one, "
            "contrary to what both lists' own docstrings claimed. A "
            "one-directional or single-command check is therefore not a "
            "weaker form of this invariant; it is a check that cannot see "
            "either defect. Corollary: an enumeration justified by 'the CLI "
            "is not available to introspect here' must have that premise "
            "checked against the real execution order -- it was false for "
            "both of this class's own call sites, which run post-install."
        ),
        # Both halves of the escape history, in order (Codex review, PR
        # #1234): #1222 is where the first instance surfaced (one missing
        # `--used-by-manifest`) and where the one-directional, `compare`-only
        # guard was written; #1234 is where the generalized fix landed -- the
        # bidirectional, multi-command invariant plus the 17 discrepancies it
        # found (12 surplus, 4 non-`compare` omissions, the stale `-j`
        # cluster terminal). Recording only #1222 would point a future audit
        # at the change that did NOT contain the generalized fix.
        fixed_by=(1222, 1234),
        seed_tests=(
            "tests/test_extra_args_is_value_option_completeness.py",
            "tests/test_action_run_sh_helpers.py",
        ),
        # Deliberately `()` (Codex review, PR #1234). An earlier revision
        # claimed `("action",)`, which this field's own contract forbids
        # twice over: the vocabulary is `github-action`, and that value is
        # reserved for "a real execution of a workflow/composite-action
        # step". Neither seed test is one -- the completeness test reads the
        # two shells' source and introspects Click, and the helper test
        # `source`s individual functions out of `run.sh` and calls them
        # directly. A claimed surface no seed test reaches conceals exactly
        # the missing cross-surface coverage this field exists to expose
        # (the same rule Codex's PR #885 review established), so the gap is
        # recorded below instead of papered over here.
        public_surfaces=(),
        axes={
            "direction": ("missing-entry", "surplus-entry"),
            "command": ("compare", "dump", "deps tree", "deps compare"),
            "spelling": ("long-option", "short-option", "short-cluster"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No seed test executes the real composite Action, so "
                    "`public_surfaces` is `()`: the invariant is proven "
                    "against the shells' source and against individually "
                    "sourced helper functions, never against a workflow "
                    "step. `.github/workflows/test-action.yml` is where an "
                    "end-to-end cell would live (an `extra-args` string "
                    "whose value resembles a flag, asserting the step "
                    "neither mis-tokenizes nor falsely rejects it). Until "
                    "one exists, a defect that only manifests through the "
                    "composite Action's own environment -- `INPUT_*` "
                    "quoting, the `CMD+=($INPUT_EXTRA_ARGS)` word-split "
                    "under a real runner's IFS -- is outside what these "
                    "tests can see."
                ),
                reference="docs/contribute/plans/action-cli-surface-drift.md",
            ),
            KnownGap(
                description=(
                    "Closed for the tokenizers (ADR-070 D3, plan Phase 3a): "
                    "both hand-maintained `case` lists are deleted, and each "
                    "tokenizer now queries the *installed* abicheck once per "
                    "run -- scoped to the command it will actually invoke, so "
                    "a union cannot reintroduce the surplus-entry failure "
                    "mode. An enumeration that does not exist cannot drift, "
                    "and a live query is additionally correct for the "
                    "abicheck version the workflow installed, which no "
                    "committed snapshot can be. **Residual:** the derivation "
                    "must fail closed when the installed abicheck cannot be "
                    "introspected and `extra-args` is non-empty, because an "
                    "opaque-token fallback is NOT safe: "
                    "`extra-args: --version --dry-run` is argv the CLI "
                    "accepts as --version's own value, so treating tokens as "
                    "opaque invents a --dry-run, skips this script's own "
                    "output/sidecar injection, and lets a full comparison run "
                    "with no report written (Codex review, PR #1234 -- it "
                    "disproved this entry's own first formulation). That path "
                    "is exercised only by tests, so a future edit adding "
                    '"just a small static list" there would reinstate this '
                    "class where nothing would notice -- which is why tests "
                    "assert both that it stays empty and that 'undetermined' "
                    "stays distinguishable from 'no option takes a value'. "
                    "`action/validate-inputs.sh`'s pre-install format choice "
                    "sets remain transcribed (plan Phase 3b); they have never "
                    "drifted, so that is prevention rather than a fix."
                ),
                reference="docs/contribute/plans/action-cli-surface-drift.md",
            ),
            KnownGap(
                description=(
                    "Scoped to option *tables*. The sibling class -- a guard "
                    "whose prose reasoning about the CLI went stale -- is "
                    "`cli_surface.capability_guard_diverged_from_pipeline`, "
                    "and that entry's own first known gap (no sweep run "
                    "beyond `compare`'s set-input guards) is what the "
                    "Action-layer audit in the plan above partially "
                    "discharges: it found two more stale guards, in "
                    "`action/run.sh` rather than in the CLI, plus five "
                    "justification comments citing deleted modules. Those "
                    "fixes are owned separately and are NOT closed by this "
                    "entry's seed tests."
                ),
                reference="docs/contribute/plans/action-cli-surface-drift.md",
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
            "consults. The front end need not be the CLI: an adapter over "
            "the CLI (the composite Action's `run.sh`/`validate-inputs.sh`) "
            "restating a CLI restriction is the same defect one layer out, "
            "and decays the same way -- silently, because its own tests pin "
            "its prose rather than measuring what it adapts."
        ),
        fixed_by=(1195, 1233),
        seed_tests=(
            "tests/test_cli_compare_release_depth.py",
            "tests/test_cli_compare_release_header_graph.py",
            # PR #1233: the *adapter* half. `action/run.sh` and
            # `action/validate-inputs.sh` are front ends over the `abicheck`
            # CLI, and both carried a guard restating a CLI restriction that
            # had already been lifted -- the compile-context rejection and
            # the `--depth headers` drop for a directory/package operand.
            # Same invariant, one layer out, and the same oracle discipline:
            # the other operand shape, never the adapter's own table. Note
            # the class reproducing itself inside its own fix: the first
            # revision of that module kept `--depth build`/`source`
            # rejected for a release operand -- a stale CLI claim of
            # exactly the kind being removed -- and only review caught it,
            # which is the strongest evidence yet that the mechanical
            # sweep in this class's gap note is the thing actually needed.
            "tests/test_action_run_sh_release_capability_parity.py",
        ),
        # "cli" is earned by the two CLI seeds above (real `CliRunner`
        # invocations). "github-action" is deliberately NOT claimed: the
        # adapter seed executes `action/run.sh`/`validate-inputs.sh`
        # directly against a fake `abicheck` on `$PATH`, which reaches
        # neither `action.yml`'s composite step nor the real CLI -- and
        # this field's own contract is that a claimed surface a seed does
        # not reach conceals exactly the missing cross-surface coverage a
        # contributor is supposed to discover here (CodeRabbit review, PR
        # #1233). The gap is real and worth closing by a seed that runs
        # the composite step end to end; overstating the tuple would hide
        # it instead.
        public_surfaces=("cli",),
        axes={
            "guard": (
                "depth-rung-allow-list",
                "header-graph-attach-claim",
                "action-compile-context-rejection",
                "action-depth-headers-drop",
                "action-depth-build-source-rejection",
            ),
            "depth_rung": ("binary", "headers", "build", "source"),
            "member_evidence": ("binary", "headers", "build"),
            "adapter_surface": ("action/run.sh", "action/validate-inputs.sh"),
            "compile_context_input": (
                "lang",
                "ast-frontend",
                "gcc-path",
                "gcc-prefix",
                "gcc-options",
                "sysroot",
                "nostdinc",
            ),
            "operand_shape": ("dir-new", "dir-old", "rpm-new", "whl-new"),
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
                    "claim from a live one. PR #1233 is the same finding at "
                    "the adapter layer -- two more stale guards, in "
                    "`action/run.sh`, each pinned by a test asserting the "
                    "adapter's own message -- which moves that base rate "
                    "further the wrong way. A systematic executable check "
                    "for the adapter half (every capability claim in "
                    "`action/**` measured against the CLI it adapts) is "
                    "deliberately not built there: the new seed test is "
                    "shaped so such a check can absorb it, with the "
                    "CLI-side residue stated as data rather than prose."
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
