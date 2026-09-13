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

"""Bug classes about the *test harness itself*, not what it measures.

The defects here are invisible to every assertion written on top of them,
because the harness keeps reporting: a dead harness whose failure mode
looks exactly like its success signal, a repository read that decodes
correctly only on the maintainer's locale, an environment built from
scratch that drops the very variable the surrounding job set to make the
run survivable. What they share is that the suite stays green while the
thing it claims to check is not being checked -- so they are found by
reading the harness against the conditions it actually runs under, never
by exercising the feature under test.

Its own module for the reason ``manifest_guards.py``/``manifest_report.py``
do: ``manifest.py`` sits at its ``architecture/debt.yaml`` ``no_growth``
baseline and is an assembly point over per-axis entry lists, and
``manifest_tool_surface.py`` -- where these three classes lived, among the
delivery-surface entries they are not really about -- reached its own
1200-line test cap. Splitting by theme rather than trimming the entries is
what that cap is for (root ``AGENTS.md``: move responsibility out, never
shrink the file to fit).
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["TEST_HARNESS_BUG_CLASSES"]


TEST_HARNESS_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="tests.dead_harness_reads_as_a_passing_control",
        invariant=(
            "A test harness whose failure mode produces the same observation "
            "as its success signal must prove it can execute at all, or the "
            "suite reports safety it never checked. "
            "`test_workflow_untrusted_text_interpolation` executes hostile PR "
            "bodies against the real workflow step and reads the attack as "
            '"the file was not written verbatim" -- which is also exactly '
            "what a harness that cannot run a shell produces. On the Windows "
            "lane it handed Git bash a POSIX-only `PATH`, so no step ran, "
            'every payload came back as "nothing written", and the negative '
            "controls read that as the attack firing: a fully green "
            "injection-defence module testing nothing. The fix is a "
            "precondition that runs a payload-free script whose only job is "
            "to write the file the other assertions look for, so a dead "
            "harness fails loudly instead of reassuring."
        ),
        fixed_by=(1244,),
        seed_tests=("tests/test_workflow_untrusted_text_interpolation.py",),
        # A real `bash` execution of a real workflow step body.
        public_surfaces=("github-action",),
        axes={
            "half": ("harness-liveness-precondition", "hostile-payload-corpus"),
            "platform": ("posix", "windows-git-bash"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The precondition proves the harness can run *a* script "
                    "and write *a* file; it does not prove the environment it "
                    "hands the step matches a real runner's. The POSIX lanes "
                    "still get a deliberately hermetic environment while "
                    "Windows inherits the host's, so the two platforms do not "
                    "execute under identical conditions -- a difference that "
                    "is stated rather than closed. Nothing here runs on a "
                    "real Windows host either; the dead-harness shape was "
                    "reproduced locally by pointing the resolver at a binary "
                    "that exits cleanly without writing."
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
            "pass the day the repository became pure ASCII. The scan must "
            "also follow *aliases*: a review found it walking only the call "
            "target, so `path = REPO_ROOT / x` and `for path in "
            "workflow_paths():` -- the two spellings this suite reaches for "
            "first -- went unflagged, and adding alias tracking immediately "
            "turned up two more unencoded reads of checked-in files."
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
                    "root constant it does not know, or reaches checked-in "
                    "content through a fixture-returned path, is not covered -- the "
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
        id="tests.constructed_environment_drops_ambient_mitigation",
        invariant=(
            "A harness that builds a subprocess environment FROM SCRATCH "
            "(so a test cannot pass on what the developer's shell exported) "
            "must still carry, deliberately, every variable the surrounding "
            "job sets as a MITIGATION -- or the code under test runs under "
            "exactly the condition that job moved away from, silently "
            "undoing it for the one population that executes real scripts. "
            "`tests/_workflow_exec.py`'s `run_step` passed five GitHub "
            "variables and nothing else, so after `.github/workflows/ci.yml` "
            "pointed the unit-test job's `TMPDIR` at `$RUNNER_TEMP` -- "
            "something prunes `/tmp` on the hosted runners mid-job -- every "
            "executed step's own `mktemp` still resolved against the shared "
            "`/tmp`. `actions/check-target/action.yml`'s assurance-overlay "
            "step then failed with `cd: /tmp/tmp.XjdPkLm7IE: No such file "
            "or directory`: its private `mktemp -d` directory was pruned "
            "between two uses inside one step body."
        ),
        fixed_by=(1298,),
        seed_tests=("tests/test_workflow_exec_harness.py",),
        # `()` per the field's own rule, even though this class is *about*
        # executing composite-action steps: the seed tests drive `run_step`
        # with synthetic `{"run": ...}` bodies, and the one that reaches for
        # the repository's own steps still executes a generated body padded
        # to the real length rather than the checked-in step. The public-
        # surface reproduction -- the real `actions/check-target/action.yml`
        # assurance-overlay step, which is what actually went red -- lives in
        # `tests/test_reusable_workflows_assurance_overlay_extra_args_config.
        # py`; it is named here rather than listed as a seed test, since it is
        # that step's own domain test and carries none of this class's
        # generalized suite (Codex review, PR #1299).
        public_surfaces=(),
        axes={
            "claim": (
                "tmpdir-is-the-directory-we-made",
                "step-can-write-into-it",
                "not-inside-the-workspace",
                "a-bare-mktemp-lands-in-it",
            ),
            "platform": ("linux", "macos-darwin-user-temp", "windows-git-bash"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "`TMPDIR` is the one ambient mitigation carried today; "
                    "no sweep proves the constructed environment tracks "
                    "future job-level ones, so the next such variable is "
                    "dropped here again and found the same way -- a red lane "
                    "naming a path nobody in the test tree wrote."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
            KnownGap(
                description=(
                    "The mitigation reaches a step's `mktemp` only where "
                    "`mktemp` reads `TMPDIR`, and macOS's does not: with no "
                    "template it resolves through "
                    "`confstr(_CS_DARWIN_USER_TEMP_DIR)` (observed behavior, "
                    "recorded in `tests/test_action_run_sh_py_safe_path.py`). "
                    "So on the macOS lanes a step's own `mktemp` still "
                    "allocates in that per-user directory -- which is not the "
                    "shared `/tmp` this class is about, and which nothing in "
                    "the environment can redirect. `test_a_bare_mktemp_"
                    "lands_in_it` pins that platform's real behavior rather "
                    "than skipping it, so the day macOS starts reading "
                    "`TMPDIR` the branch fails and collapses; "
                    "`.github/workflows/ci.yml`'s own job-level `TMPDIR` "
                    "mitigation has exactly the same limit, and closing it "
                    "would mean interposing on what a step body executes, "
                    "not setting a variable."
                ),
                reference="PR #1298 (Codex review)",
            ),
        ),
    ),
)
