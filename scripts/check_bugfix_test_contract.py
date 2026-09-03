#!/usr/bin/env python3
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

"""Bug-fix test contract — the four questions a fix has to answer.

Reviewing this repository's merged fixes turns up a consistent shape: the
regression test for the reported input is excellent, and the *class* of bug is
left open. The escapes that followed were all adjacent cases, not repeats:

* #699 -> #721: the zstd window size was tested against its own (wrong)
  formula and at a toy scale, so both tests passed before and after the bug.
* #753 -> #759: three ChangeKinds were missing from a hand-maintained list;
  a missing entry produces no failure anywhere.
* #705 -> #758: workspace shadowing was defended by asserting the *text* of a
  workflow file, not by executing it against a hostile workspace.

None of these needed a cleverer reviewer. They needed the fix to state which
invariant it restores and which neighbouring inputs it covers — which is what
this gate asks for, mechanically, at the point the claim is cheap to make.

The "general-invariant" row exists specifically to keep this from degrading
into "a test for the reported input, plus a paragraph describing the class":
the invariant must be backed by a test that exercises it with inputs beyond
the one reported, against a named oracle — not restated as prose alone. See
docs/contribute/plans/bug-class-regression-testing.md for the full analysis
and the named bug classes it identifies. ``tests/regressions/manifest.py``
(that plan's Phase 1) is the queryable registry: check it first for a
matching class before restating an invariant from scratch.

Two kinds of check, deliberately separated:

**Structural** (objective, always enforced): a fix that changes shipped code
must change a test too.

**Declared** (the contract block in the PR body): the four required answers,
plus any conditional requirement the diff itself triggers — a fix touching a
third-party boundary is asked about real-dependency coverage, a fix touching
identity/dedup logic is asked for the must-merge/must-not-merge pair, and so
on. The script cannot judge whether an answer is *true*; it can make the
omission visible, which is the failure mode above.

Bypass with the ``skip-test-contract`` label, matching ``skip-changelog``'s
role for genuinely non-user-facing changes.

Usage::

    python scripts/check_bugfix_test_contract.py --base <sha> --head <sha> \\
        --body-file pr-body.md
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

#: Returned when the structural half passed but no PR body was available, so
#: the declared half never ran. Callers that bundle this gate (verify.py) map
#: it to a partial/skipped result rather than a pass; a real failure is still
#: 1, so this can never mask one.
EXIT_STRUCTURAL_ONLY = 2

#: Returned when there was no diff to check at all — a local checkout with no
#: `origin/main`, say. Distinct from the code above because the remediation
#: and the amount actually checked differ: nothing ran here, and reporting the
#: missing-PR-body reason would send a reader to the wrong fix (Codex review).
EXIT_NO_DIFF = 3

#: Used when neither the flag nor the environment names a ref — the local
#: convenience path.
_DEFAULT_BASE = "origin/main"
_DEFAULT_HEAD = "HEAD"

#: Conventional-Commit subjects that mean "this is a correctness fix".
#: `feat:`/`refactor:`/`docs:` are deliberately out of scope — the contract is
#: about a bug that escaped, not about all change.
_FIX_SUBJECT = re.compile(r"^(fix|perf|security)(\([^)]*\))?!?:", re.IGNORECASE)

#: Shipped code. A fix here is what triggers the structural requirement.
#: Keyed by prefix *and* extension, because `action/` is the composite Action's
#: shell layer, not Python — requiring `.py` everywhere let a fix to
#: `action/run.sh` change the Action's runtime behaviour with no test at all
#: and still pass the objective half of this gate (Codex review). That layer is
#: covered by the `test_action_run_sh_*` suites, so there is a real test to
#: add.
_SHIPPED_SUFFIXES_BY_PREFIX = {
    # `.yaml` and `.json` are packaged *runtime* contracts, not build config:
    # abicheck/policies/*.yaml are the built-in policy profiles resolved by
    # bare name through `policy_file.builtin_policy_path()`, and
    # abicheck/schemas/*.json are the published report schemas. A correctness
    # fix to either changes shipped behaviour with no `.py` touched (Codex
    # review). `.md` stays out — the four CLAUDE.md files there are docs.
    "abicheck/": (".py", ".yaml", ".json"),
    "scripts/": (".py",),
    "action/": (".py", ".sh"),
    # The plural tree: five published composite actions, consumed directly as
    # `uses: abicheck/abicheck/actions/...` (see docs/reference/check-target.md
    # and resolve-baseline.md) and each with dedicated tests. `.yml` counts
    # here because an action's manifest *is* its interface, unlike a workflow
    # (Codex review).
    "actions/": (".py", ".sh", ".yml"),
    # AGENTS.md classifies the Clang facts plugin as a surrounding first-party
    # tree with its own AGENTS.md and its own tests; its runtime is C++/CMake,
    # so none of the suffixes above matched and a fix confined to the plugin
    # skipped the structural requirement entirely (Codex review).
    "contrib/abicheck-clang-plugin/": (".cpp", ".h", ".hpp", ".cmake", ".txt", ".py"),
}
_SHIPPED_PREFIXES = tuple(_SHIPPED_SUFFIXES_BY_PREFIX)

#: Individually-named shipped files that no prefix rule covers. The root
#: `action.yml` *is* the published composite Action — it declares its inputs
#: and its executable steps — and it has dedicated coverage in
#: `tests/test_action_reference.py` / `test_action_run_contract.py`, so a fix
#: to it can and should carry a test (Codex review).
#: `pyproject.toml` joins it for a different reason: it is build *config*, but
#: three of its sections are installed runtime behaviour — `[project]
#: .dependencies` (what a user's environment resolves), `[project.scripts]`
#: (the `abicheck` entry point), and `[tool.setuptools.package-data]` (whether
#: the packaged policy profiles and report schemas ship at all). A fix to any
#: of those changes what users run with no `.py` touched, and the structural
#: gate let it through (Codex review). `tests/test_toolchain_pins.py` and
#: `tests/test_cli_root_surface.py` are where such a fix is asserted; a genuinely
#: unassertable one takes the `skip-test-contract` label, same as any other.
_SHIPPED_FILES = frozenset({"action.yml", "pyproject.toml"})

#: Not shipped code, but the surface the malicious-fixture requirement is
#: about: a workflow runs with the repository's permissions.
#:
#: `.github/actions/` is the same boundary reached one level down. A local
#: composite action (`setup-castxml`, `cache-ast-dumps`) has no `on:` trigger
#: of its own, so nothing here matched it — but its steps execute inside
#: whichever caller invoked it, with that caller's token and permissions, and
#: `setup-castxml` alone is invoked from `ci.yml`, `publish.yml`,
#: `performance.yml`, `realworld-validation.yml` and both examples-validation
#: workflows. Its blast radius is therefore *wider* than any single workflow
#: file's, not narrower (Codex review). Deliberately trust-boundary rather
#: than shipped code, matching `.github/workflows/`: these run only in this
#: repository's CI and are not consumed by users, unlike the published
#: `actions/` tree.
_TRUST_BOUNDARY_PREFIXES = (".github/workflows/", ".github/actions/")

#: Directory component that marks a test tree.
_TEST_DIR = "tests"

#: Prose suffixes. A file under `tests/` with one of these is documentation,
#: not an executable test or a fixture — editing `tests/CLAUDE.md` alongside a
#: shipped-code fix must not satisfy "you changed a test" (Codex review).
_DOC_SUFFIXES = (".md", ".rst")
#: ...except under `golden/`, where `.md` files really are test data: the
#: golden report snapshots are compared byte-for-byte, so changing one is a
#: genuine test change. Verified against tests/golden/*.md.
_TEST_DATA_DIR = "golden"

#: `.py` files under a `tests/` tree that are real, executable tests despite
#: not matching pytest's `test_*.py`/`*_test.py` collection convention — each
#: is invoked directly as `python <path> ...` by a checked-in CI workflow,
#: not through pytest, so `_is_collected_python_test_module()`'s naming check
#: alone would credit zero test evidence to a fix confined to editing one of
#: these (Codex review, PR #885 — the reported gap was the two clang-plugin
#: entries; the `examples-validation`/nightly entries are the same bug class,
#: found by re-checking every `python .../tests/*.py` invocation across
#: `.github/workflows/*.yml`, not just the reported pair). Allowlist-and-grow,
#: mirroring `CLI_CONTRACT_ALLOWLIST`/`ENGINE_CLI_BOUNDARY_ALLOWLIST`'s own
#: convention in `check_ai_readiness.py`: a new standalone runner belongs here
#: only once it is genuinely invoked directly by a workflow step *whose exit
#: code actually gates that workflow* — verified by grepping
#: `.github/workflows/*.yml` for `python .../<name>.py` and confirming the
#: invocation does not end in `|| true` (or an equivalent always-succeed
#: guard). `tests/summarize_validate_results.py` was in this set once and
#: was removed: both of its invocations end in `|| true` (a Job Summary
#: formatting step whose own failure cannot fail the workflow), so editing
#: only it proved nothing was actually tested (Codex review, PR #885).
_STANDALONE_TEST_RUNNERS = frozenset(
    {
        # contrib/abicheck-clang-plugin/tests/ — clang-plugin.yml's C.6
        # differential-conformance and scan-flow legs (test_public_roots_
        # diagnostic.py, the third script that workflow runs, already
        # matches the test_*.py convention and needs no entry here).
        "contrib/abicheck-clang-plugin/tests/conformance.py",
        "contrib/abicheck-clang-plugin/tests/scan_flow.py",
        # tests/ — examples-validation.yml and examples-validation-nightly.yml
        # run these directly as the real-binary example-catalog validation
        # lane (as opposed to test_abi_examples.py's own pytest-collected
        # ground-truth checks, which import some of the same helpers). Each
        # invocation's exit code is unguarded, so a nonzero exit fails the
        # job — unlike summarize_validate_results.py, see above.
        "tests/validate_examples.py",
        "tests/check_validate_results.py",
        "tests/check_stripped_fp.py",
        "tests/example_shards.py",
    }
)


def _is_conditional_subject(path: str) -> bool:
    """Can this path carry the runtime behaviour a conditional asks about?

    Shipped code, plus the workflow/composite-action trust boundary — the
    latter is not "shipped" by the map above but is exactly what the
    malicious-fixture requirement exists for.

    Prose is excluded on *both* branches, not just the shipped one: the
    trust-boundary prefixes are directory prefixes, so without this a
    `.github/actions/<name>/README.md` would be asked for hostile-input
    evidence for the same reason `docs/reference/snapshot_io.md` used to be
    asked for real-dependency evidence — a conditional that fires on prose is
    boilerplate, not a signal.
    """
    if is_test_path(path):
        return False
    if path.endswith(_DOC_SUFFIXES):
        return False
    if path.startswith(_TRUST_BOUNDARY_PREFIXES):
        return True
    return touches_shipped_code([path])


@dataclass(frozen=True)
class Requirement:
    """One question the PR body must answer."""

    key: str
    prompt: str
    why: str
    #: ``None`` -> always required. Otherwise, required when any changed path
    #: matches one of these substrings.
    triggers: tuple[str, ...] | None = None
    #: Required when *another* requirement's answer admits the fix is not
    #: general. AGENTS.md's own rule is "generalize, or record the gap", so
    #: the gap has to be stated somewhere — and the template advertised a row
    #: for it that nothing could ever ask for (Codex review).
    required_when_gap_admitted_in: str | None = None

    def applies_to(self, paths: list[str]) -> bool:
        if self.triggers is None:
            return True
        # Only paths that carry *runtime* behaviour trigger a conditional.
        # Filtering merely on "not a test" was too wide in both directions: a
        # test named after its subject fired the conditional on a test-only
        # change, and `docs/reference/snapshot_io.md` fired the
        # real-dependency question on a documentation-only fix (Codex review).
        # A conditional that fires on prose is boilerplate, not a signal.
        subject = [p for p in paths if _is_conditional_subject(p)]
        return any(t in p for p in subject for t in self.triggers)

    def required_by(self, answers: dict[str, str]) -> bool:
        """Does another answer's admission of a gap pull this row in?"""
        if self.required_when_gap_admitted_in is None:
            return False
        answer = answers.get(self.required_when_gap_admitted_in, "")
        return _admits_a_gap(answer)


#: Bare answers that mean "there is no general invariant here" — recognised
#: only when they are the *whole* answer.
#:
#: This is the half that has to be anchored. Matched as substrings, "none"
#: read an ordinary universal invariant — "None of the malformed archive
#: members escape the root" — as an admission that the fix is narrow, and then
#: made a required PR check demand a gap row for a fix with no gap to record
#: (Codex review). A universal claim very often *starts* with the same word a
#: refusal does, so position is not enough either: only the complete answer
#: distinguishes "None." from "None of ...".
_GAP_ADMISSION_ANSWERS = frozenset({"n/a", "na", "no", "none", "nothing"})

#: Phrases that carry the admission wherever they appear in a sentence, so
#: they are matched anywhere — but as whole phrases, not substrings.
#:
#: Deliberately a short, explicit vocabulary rather than sentiment analysis:
#: this only has to recognise the admission the repository's own convention
#: asks an author to make ("generalize, or record the gap"). The trailing
#: ``\w*`` on the generali[sz]e forms is what lets one entry cover
#: "generalisable"/"generalizable"/"generally"; the bare "no general" that
#: used to be here is gone, because "no generalization was needed" says the
#: opposite of what the others say. A phrase this misses simply leaves the
#: follow-up row unrequested, which is where things stood before it existed —
#: a false *positive* is the expensive direction, since it blocks a PR on a
#: row its author has nothing true to write in.
_GAP_ADMISSION_PHRASES = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bnot general\w*",
        r"\bno general invariant\b",
        r"\bcould not generali[sz]\w*",
        r"\bcannot be generali[sz]\w*",
        r"\bknown gap\b",
        r"\bdocumented (?:as )?(?:a )?gap\b",
        r"\bthis instance only\b",
        r"\binstance[- ]only\b",
    )
)

#: Everything that is not a letter, a digit or a slash — so "None." and
#: "**none**" normalise onto "none", while "n/a" keeps the slash that
#: distinguishes it from an ordinary word.
_ANSWER_NOISE = re.compile(r"[^a-z0-9/]+")


def _admits_a_gap(answer: str) -> bool:
    text = answer.strip().lower()
    if not text:
        return False
    if _ANSWER_NOISE.sub("", text) in _GAP_ADMISSION_ANSWERS:
        return True
    return any(pattern.search(text) for pattern in _GAP_ADMISSION_PHRASES)


#: The four always-required answers, then the conditional ones. Each
#: conditional requirement exists because a real escape in this repository went
#: through that exact surface.
REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        "bug-class",
        "Bug class",
        "The class of defect, not the one input that exposed it. This is the "
        "question the whole contract turns on: every escape below was a fix "
        "that closed an instance and left the class open.",
    ),
    Requirement(
        "publicly-observable-failure",
        "Publicly observable failure",
        "What a user or CI would have seen. A fix that cannot be described "
        "this way is usually being verified against internals rather than "
        "against behaviour, which is how #699 passed its own tests.",
    ),
    Requirement(
        "regression-test-fails-on-base",
        "Regression test fails on base",
        "A test that passes without the fix is not a regression test. State it "
        "explicitly, because it is the one property that cannot be reviewed by "
        "reading the diff.",
    ),
    Requirement(
        "negative-control",
        "Negative control",
        "The neighbouring input that must NOT change behaviour. Without it a "
        "fix can 'work' by suppressing the whole class, which is how a "
        "false-positive fix turns into a false negative.",
    ),
    Requirement(
        "public-surface",
        "Public-surface test",
        "Exercised through the entry point a user actually calls (CLI, report, "
        "typed API), not only the internal helper. #699's zstd bug lived "
        "entirely between a helper's own arithmetic and the real chokepoint.",
    ),
    Requirement(
        "axes-covered",
        "Axes covered",
        "Which backend / platform / evidence tier / surface / schema this was "
        "checked on. A fix verified on one axis and silently assumed on the "
        "others is the shape of the repeated L2-then-L4 and "
        "CLI-then-Action follow-ups.",
    ),
    Requirement(
        "general-invariant",
        "General invariant",
        "The property that now holds for every input, not just the reported "
        "one — and the named regression test must exercise it with inputs "
        "beyond the one reported (generated/property-based, an exhaustive "
        "small-domain enumeration, or several independently-chosen sibling "
        "cases), against an oracle that is not the same formula/helper the "
        "implementation itself uses. Prose describing the class without such "
        "a test does not satisfy this row (AGENTS.md 'A bug fix's regression "
        "test targets the bug class, not the one reported input'; see "
        "docs/contribute/plans/bug-class-regression-testing.md for the "
        "analysis this requirement is based on). Check "
        "tests/regressions/manifest.py (BUG_CLASSES/get()) first for a "
        "matching class before restating the invariant from scratch — name "
        "its id here if it already has a home. "
        "'None — documented as a known gap' is an acceptable answer; "
        "silence is not.",
    ),
    # --- conditional ---------------------------------------------------
    Requirement(
        "real-dependency-test",
        "Real-dependency test",
        "This diff touches a third-party/format boundary, where a "
        "hand-constructed shortcut into the dependency's lower-level API can "
        "pass identically before and after the bug (ADR-059 §12).",
        triggers=("snapshot_io", "serialization", "adapters/", "dumper_", "_metadata"),
    ),
    Requirement(
        "malicious-fixture",
        "Malicious fixture + side-effect absence",
        "This diff touches the action/workflow trust boundary. Asserting the "
        "text of a YAML file does not prove the attack fails; #705 was "
        "text-asserted and #758 had to add the executing test.",
        # "action.yml" also matches `.github/workflows/test-action.yml`, which
        # is the Action's own test workflow — the same boundary, and already
        # matched by the `.github/workflows/` entry anyway.
        triggers=(
            "action/",
            # The plural tree is the same trust boundary: `actions/` does not
            # contain the substring `action/` (an `s` follows `action`), so
            # published composite-action scripts were recognised as shipped
            # code without ever being asked for hostile-input evidence
            # (Codex review).
            "actions/",
            "action.yml",
            # Every trust boundary is a *path prefix*, and this is the only
            # trigger list where that matters: a bare "security" substring
            # matched `abicheck/policies/security.yaml`, a packaged runtime
            # policy profile with no Action or workflow anywhere near it, and
            # then demanded hostile-input evidence for it — which `N/A` cannot
            # satisfy, since the answer parser rejects placeholders (Codex
            # review). The real security *workflow*
            # (`.github/workflows/security.yml`) is already covered by the
            # prefix above.
            *_TRUST_BOUNDARY_PREFIXES,
        ),
    ),
    Requirement(
        "known-unsupported-cases",
        "Known unsupported cases",
        "The general invariant answer says this fix does not close the whole "
        "class. AGENTS.md's rule for that case is to record the gap rather "
        "than ship a narrow patch as if it were complete, so name what is "
        "still unsupported — the template advertised this row while nothing "
        "could ever ask for it (Codex review).",
        triggers=(),
        required_when_gap_admitted_in="general-invariant",
    ),
    Requirement(
        "merge-pair",
        "Must-merge / must-not-merge pair",
        "This diff touches identity, dedup or grouping logic, where "
        "'these two collapse' and 'these two stay distinct' are independent "
        "claims and testing only the first is satisfiable by collapsing "
        "everything.",
        triggers=("finding_identity", "diff_filtering", "identity", "dedup"),
    ),
    Requirement(
        "fp-fn-pair",
        "False-positive removed / real break preserved",
        "This diff touches filtering or surface scoping, where suppressing the "
        "noise and hiding a real break look the same from the noisy side.",
        triggers=("diff_filtering", "surface", "suppression"),
    ),
    Requirement(
        "verdict-gate-exit",
        "Verdict, gate and exit code checked independently",
        "This diff touches policy/severity, where the semantic verdict, the CI "
        "gate decision and the process exit code are three separately "
        "configurable answers that a single assertion conflates.",
        triggers=("checker_policy", "severity", "contract_gating", "exit"),
    ),
)

_MARKER = "<!-- bugfix-test-contract -->"

#: `<!-- ... -->`, including multi-line regions.
#: An unterminated `<!--` hides the rest of the rendered description on
#: GitHub while a closing-marker-only pattern still read the answers behind
#: it — invisible to every reviewer, accepted by the parser, which is the
#: exact bypass this strip exists to close (CodeRabbit review). The
#: template ships two comment blocks, so a deleted `-->` is a realistic
#: edit.
#: `@@ -a,b +c,d @@` — only the new side matters here.
_HUNK_NEW_SIDE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)

#: A line in the body answering a requirement: `- Negative control: <text>`.
#: Tolerant of `*`/`-` bullets, bold, and a trailing checkbox.
_ANSWER = re.compile(
    r"^\s*[-*]?\s*(?:\[.\]\s*)?\*{0,2}(?P<label>[^:*]+?)\*{0,2}\s*:\s*(?P<value>.*)$"
)

#: Answers that are the template's own placeholder rather than a real answer.
_PLACEHOLDER = re.compile(
    r"^(|-|n/?a|tbd|todo|xxx|\.\.\.|<.*>|yes/no|_+)$", re.IGNORECASE
)


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def changed_paths(base: str, head: str) -> list[str]:
    return [path for _status, path in changed_files(base, head)]


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """``(status, path)`` pairs — the status matters for test evidence.

    A *deleted* test path still appears in the diff, and counting it as "you
    changed a test" let a fix that only removes a test satisfy the structural
    requirement (Codex review). Only an added or modified test is evidence
    that a regression test exists.

    ``T`` (type change) is included in the filter: replacing a shipped script
    with a symlink is a real behavioural change that was disappearing from the
    diff entirely, taking both shipped-code detection and the conditionals
    with it. It is deliberately *not* accepted as test evidence — retyping a
    test file is not writing one.
    """
    out = _git(
        [
            "diff",
            "--no-renames",
            "--name-status",
            "--diff-filter=ACDMT",
            f"{base}...{head}",
        ]
    )
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line or "\t" not in line:
            continue
        status, path = line.split("\t", 1)
        pairs.append((status.strip(), path.strip()))
    return pairs


def unified_diff(base: str, head: str) -> str:
    """The diff itself, for the content half of the test-evidence question.

    Rename detection is **on** here, unlike :func:`changed_files`. The two want
    opposite things from the same diff: the status listing wants a rename split
    into its `D` and `A` halves so a deleted test is still visible as a
    deletion, while the content check wants the halves paired, so a pure rename
    reduces to "no lines added" instead of looking like a brand-new test file
    (Codex review). `--unified=0` keeps context lines out of the added-line
    scan.
    """
    return _git(
        [
            "diff",
            "-M",
            # Copies too, and `--find-copies-harder` so a copy from a file the
            # branch did not otherwise touch is still found. `cp tests/test_a.py
            # tests/test_b.py` is a rename's twin: without this git reports the
            # copy as a brand-new file with every line added, and duplicating
            # an existing test counted as writing one (Codex review). With it,
            # a pure copy carries `similarity index 100%` and no `+++` header,
            # so it contributes no added lines — verified against real git.
            "-C",
            "--find-copies-harder",
            "--unified=0",
            f"{base}...{head}",
        ]
    )


def _show_at(rev: str, path: str) -> str | None:
    """The file's content at *rev*, or None when it cannot be read.

    The docstring rule needs the new-side file, not the diff: a docstring's
    middle lines are arbitrary prose and cannot be recognised from a hunk
    alone.
    """
    try:
        return _git(["show", f"{rev}:{path}"])
    except subprocess.CalledProcessError:
        return None


def commit_subjects(base: str, head: str) -> list[str]:
    out = _git(["log", "--format=%s", f"{base}..{head}"])
    return [line for line in out.splitlines() if line]


def is_bugfix(subjects: list[str], title: str | None) -> bool:
    """Classify the proposed change by its authoritative public subject.

    CI always supplies the pull-request title. That title is the subject users
    review and the squash-merge commit GitHub will create, so it is
    authoritative there. Looking through every intermediate commit as well
    made a feature/refactor PR suddenly require a bug-fix declaration after a
    routine ``fix: address review`` follow-up, even though neither the public
    PR classification nor the proposed squash commit changed.

    Local runs have no PR title and therefore fall back to commit subjects.
    This retains useful enforcement for a standalone ``fix:`` commit while
    making CI classification stable over a PR's review history.
    """
    if title is not None:
        return bool(_FIX_SUBJECT.match(title.strip()))
    return any(_FIX_SUBJECT.match(subject.strip()) for subject in subjects)


def touches_shipped_code(paths: list[str]) -> bool:
    if any(p in _SHIPPED_FILES for p in paths):
        return True
    return any(
        p.startswith(prefix) and p.endswith(suffixes)
        for p in paths
        for prefix, suffixes in _SHIPPED_SUFFIXES_BY_PREFIX.items()
    )


def is_test_path(path: str) -> bool:
    """Is *path* a test file?

    Deliberately structural — a `tests/` **directory component** — rather than
    a substring search for `test_` anywhere in the path. The substring form
    matched shipped sources whose names merely contain it, including
    `scripts/summarize_test_durations.py` and this checker itself
    (`check_bugfix_test_contract.py`), so a fix editing only those satisfied
    the structural requirement with no test at all — passing exactly the case
    it exists to reject (Codex review).

    A test **basename** outside any such directory used to count too, and no
    longer does. `testpaths = ["tests"]` and the canonical command is `pytest
    tests/`, so a `test_*.py`/`*_test.py` file elsewhere is never collected by
    any lane — accepting it credited a file nothing runs (Codex review). The
    one real file it matched proves the point:
    `agent-evals/tasks/*/hidden_tests/test_*.py`, an eval fixture that the
    test suite deliberately does not execute. Every directory named `tests`
    in this repository *is* run by some lane (the root suite, the clang
    plugin's, the layout tool's), so the directory rule needs no allowlist.
    """
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if _TEST_DIR not in parts[:-1]:
        return False
    # Inside a test tree, any file type can be test *data* (fixtures, golden
    # snapshots) — only prose is excluded, and not under golden/.
    name = parts[-1]
    is_prose = name.endswith(_DOC_SUFFIXES)
    return not is_prose or _TEST_DATA_DIR in parts[:-1]


def touches_tests(paths: list[str]) -> bool:
    """Path-only form: any test path, whatever happened to it."""
    return any(is_test_path(p) for p in paths)


def _is_collected_python_test_module(path: str) -> bool:
    """Does this `.py` path look like a file pytest actually *collects* as
    a test — `test_*.py` / `*_test.py` — rather than a shared support/data
    module a real test merely imports?

    `is_test_path()` credits any non-prose file under a `tests/` directory
    as evidence, including a shared support module like
    `canonical_identity_contract.py`, `_workflow_exec.py`, or this
    repository's own `tests/regressions/manifest.py` — none of which
    pytest's `testpaths = ["tests"]` collection ever runs on its own. A fix
    that only edits such a module (e.g. registering a new `BugClass` entry
    with no `seed_tests` path actually changed) satisfied the structural
    "you changed a test" gate with zero executable test evidence (Codex
    review). Scoped to `.py` files only — a non-`.py` file under `tests/`
    is still recognised as test *data* (fixtures, golden snapshots) by
    `is_test_path()`'s own docstring, unaffected by this narrower check.

    The naming convention alone is also too narrow in the other direction: a
    handful of real, CI-executed tests are invoked directly as `python
    <path> ...` by a checked-in workflow rather than collected by pytest, so
    they never needed `test_*.py` naming in the first place — see
    `_STANDALONE_TEST_RUNNERS`'s own docstring (Codex review, PR #885).

    A `conftest.py` under the root `tests/` tree is also accepted, despite
    being neither `test_*.py`-named nor CI-invoked directly — pytest
    auto-discovers and applies *every* `conftest.py` under its rootdir to
    every test in that file's own directory and below, unconditionally, by
    pytest's own collection contract (not by anything this PR-specific
    diff does). A fix that only widens an existing parametrized fixture
    there (adding a case to a `params=[...]` list an existing test already
    iterates) exercises genuinely new test runs without touching any
    `test_*.py` file at all (Codex review, PR #885, fifth round). This is
    deliberately narrower than the review's own broader framing ("count
    executable Python fixture changes as test evidence" generally, for any
    imported fixture/data module) — `conftest.py` is special because
    *pytest itself* guarantees it is wired into collection; an arbitrary
    imported module like `canonical_identity_contract.py`/
    `_workflow_exec.py`/this file's own `tests/regressions/manifest.py`
    carries no such guarantee (whether it actually affects a collected
    test is PR-specific and content-dependent), and crediting those
    unconditionally is the exact loophole `_is_collected_python_test_module`
    exists to close — widening it to "any file some test happens to
    import" would reopen it.

    Scoped to the root `tests/` tree specifically, not "any depth"
    (Codex review, PR #885, ninth round, fresh evidence): this
    repository's own `testpaths = ["tests"]` means pytest's collection
    root is exactly that one directory — a `conftest.py` under
    `contrib/abicheck-clang-plugin/tests/` (a real, sibling `tests/`
    directory in this repo, whose own tests are invoked directly with
    `python <path> ...`, never through pytest — see
    `_STANDALONE_TEST_RUNNERS`'s own docstring) is never loaded by pytest
    at all, so the earlier "any depth" version credited a fix confined to
    a plugin-tree `conftest.py` with test evidence the plugin's own CI
    lane never actually exercises.
    """
    if path in _STANDALONE_TEST_RUNNERS:
        return True
    parts = PurePosixPath(path).parts
    if parts and parts[0] == _TEST_DIR and parts[-1] == "conftest.py":
        return True
    return path.endswith(".py") and (
        PurePosixPath(path).name.startswith("test_") or path.endswith("_test.py")
    )


def added_lines_by_path(diff_text: str) -> dict[str, list[tuple[int, str]]]:
    """`path -> [(new-side line number, text), ...]` for every added line.

    The number comes from the hunk header (`--unified=0`, so every line in a
    hunk is an addition) and the text from the `+` line itself, because both
    are needed: the text answers "is this blank or a comment", the number
    answers "is this inside a docstring", and neither question can be
    answered from the other's evidence.

    A **pure rename or copy** produces no `+++` header at all under
    rename/copy detection (`similarity index 100%`), so it contributes
    nothing — which is the point: moving or duplicating a test is not writing
    one (Codex review).
    """
    out: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    next_line = 0
    is_symlink = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # Reset first: a rename/copy-only entry carries no `+++` header,
            # so without this the previous file's attribution leaks into it.
            current = None
            is_symlink = False
            continue
        if line.startswith("new file mode ") or line.startswith("new mode "):
            # `120000` is a symlink. Git renders its *target path* as the
            # added line, so `ln -s test_existing.py tests/test_regression.py`
            # looked like a test file with content — a duplicate of an
            # existing test accepted as new evidence (Codex review). Same
            # category as a rename or a copy: it writes no assertion.
            is_symlink = line.rstrip().endswith("120000")
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            continue
        if current is None or is_symlink:
            continue
        m = _HUNK_NEW_SIDE.match(line)
        if m:
            next_line = int(m.group(1))
            continue
        if line.startswith("+"):
            out.setdefault(current, []).append((next_line, line[1:]))
            next_line += 1
    return out


def removed_content_paths(diff_text: str) -> set[str]:
    """Paths this diff removes at least one non-blank line from.

    Used for *fixture* files only. Removing an expected line from a golden
    snapshot is exactly how a fix that stops emitting spurious output proves
    itself — it fails against the base behaviour — and an added-lines-only
    scan rejected that PR as having no test change at all (Codex review).

    Deliberately not applied to `.py` test modules: deleting lines there is
    how a test gets weakened, which is the thing the gate exists to catch.

    The same per-format comment rule as `added_content_paths` applies here,
    for the same reason and in both directions: deleting a YAML comment from
    a fixture changes no expected value, so accepting it as regression
    evidence let a shipped-code fix pass the structural gate with no assertion
    or fixture value touched at all (Codex review).
    """
    out: set[str] = set()
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = None
            continue
        if line.startswith("--- "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("a/") else None
            continue
        if current is None or not line.startswith("-") or line.startswith("---"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        comments = _comment_prefixes(current)
        if comments and body.startswith(comments):
            continue
        out.add(current)
    return out


def _docstring_lines(source: str) -> set[int]:
    """Every line occupied by a docstring in *source*.

    Docstrings are the one kind of added line that is real Python and still
    cannot fail: editing one changes no assertion, no fixture and no
    behaviour, so it is not regression evidence (Codex review). Other string
    literals are left alone deliberately — a fixture's expected output *is*
    test data.

    Determined by parsing rather than by pattern-matching the diff: a
    docstring's middle lines are arbitrary prose, indistinguishable from code
    without knowing the enclosing node.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _comment_prefixes(path: str) -> tuple[str, ...]:
    """The comment markers of *path*'s format, or `()` if it has none.

    Per format, not "`#` everywhere" and not "`.py` only". Both extremes are
    wrong in the same way — they answer a question about one language with a
    fact about another:

    * `#` everywhere discards a real Markdown heading and a real `#include`
      line in a C fixture, i.e. genuine test-data content.
    * `.py` only counts a comment-only edit to a YAML or shell fixture as
      substantive test evidence, which is exactly what this whole function
      exists to reject — a comment asserts nothing in any language (Codex
      review).

    `.json` is deliberately absent: JSON has no comments, so a line starting
    with `//` in one is content (or a syntax error), never a comment.
    """
    for suffixes, prefixes in _COMMENT_SYNTAX:
        if path.endswith(suffixes):
            return prefixes
    return ()


#: Ordered so the first match wins; the entries are disjoint today, but the
#: order makes that explicit rather than incidental.
_COMMENT_SYNTAX: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    ((".py", ".pyi"), ("#",)),
    ((".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh", ".bash", ".conf"), ("#",)),
    ((".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"), ("//",)),
)


def added_content_paths(
    diff_text: str, read_new: Callable[[str], str | None] | None = None
) -> set[str]:
    """Paths this diff adds at least one line of *substantive* content to.

    Three exclusions, all content-shaped rather than status-shaped: a rename
    or copy (no added lines at all), a blank or comment-only line, and — when
    *read_new* can supply the new file — a line inside a docstring.

    The comment rule is per format (`_comment_prefixes`); the docstring rule
    is `.py`-only because only Python has docstrings. Without *read_new* the
    docstring rule simply does not apply; the comment rule still does, since
    it needs only the diff.
    """
    out: set[str] = set()
    for path, added in added_lines_by_path(diff_text).items():
        is_python = path.endswith(".py")
        comments = _comment_prefixes(path)
        docstrings: set[int] = set()
        if is_python and read_new is not None:
            source = read_new(path)
            if source is not None:
                docstrings = _docstring_lines(source)
        for number, text in added:
            body = text.strip()
            if not body:
                continue
            if comments and body.startswith(comments):
                continue
            if number in docstrings:
                continue
            out.add(path)
            break
    return out


def adds_or_modifies_a_test(
    changed: list[tuple[str, str]],
    diff_text: str,
    read_new: Callable[[str], str | None] | None = None,
) -> bool:
    """Positive evidence that a regression test exists.

    Both halves are required, because each catches something the other cannot:
    the *status* rules out a deletion (the opposite of evidence) and a type
    change (retyping a test file is not writing one), while the *content* rules
    out a rename or a whitespace/comment-only edit, which carry an `A`/`M`
    status while asserting nothing new. A third check, `.py`-only, rules out a
    shared support/data module under `tests/` that is not itself collected —
    see `_is_collected_python_test_module()`'s own docstring (Codex review).
    """
    with_content = added_content_paths(diff_text, read_new)
    # A removal counts only in a *fixture*: dropping an expected line from a
    # golden snapshot is how a fix that stops emitting spurious output proves
    # itself, while dropping lines from a `.py` test module is how a test gets
    # weakened (Codex review).
    with_removals = {
        path for path in removed_content_paths(diff_text) if not path.endswith(".py")
    }
    return any(
        status in ("A", "M")
        and is_test_path(path)
        and (not path.endswith(".py") or _is_collected_python_test_module(path))
        and (path in with_content or (status == "M" and path in with_removals))
        for status, path in changed
    )


def strip_html_comments(text: str) -> str:
    """Remove `<!-- ... -->` regions.

    The declared half exists to put evidence *in front of a reviewer*. GitHub
    hides comment regions from the rendered description, so an answer written
    inside the template's own `<!-- Conditional ... -->` block satisfies a
    parser while being invisible to every human who opens the PR — which
    defeats the point entirely (Codex review). The conditional rows ship inside
    that comment as a menu to copy out, not as a place to answer.
    """
    return _HTML_COMMENT.sub("", text)


def parse_answers(body: str) -> dict[str, str]:
    """Map a normalized label -> answer text, for every visible `Label: value`."""
    answers: dict[str, str] = {}
    for line in strip_html_comments(body).splitlines():
        m = _ANSWER.match(line)
        if not m:
            continue
        label = _normalize(m.group("label"))
        if label:
            answers[label] = m.group("value").strip()
    return answers


def _normalize(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")


def missing_requirements(body: str, paths: list[str]) -> list[Requirement]:
    """Requirements that apply to this diff but have no substantive answer."""
    answers = parse_answers(body)
    missing = []
    for req in REQUIREMENTS:
        if not (req.applies_to(paths) or req.required_by(answers)):
            continue
        value = answers.get(_normalize(req.prompt))
        if value is None or _PLACEHOLDER.match(value.strip()):
            missing.append(req)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Optional so this can run with fixed argv as a `scripts/verify.py` step
    # (AGENTS.md "M0-3": one verification contract, not a CI-only check that a
    # local `--profile pr` cannot reproduce). CI supplies the real PR refs and
    # body through the environment; a local run falls back to origin/main..HEAD
    # with no body, which exercises the structural half.
    parser.add_argument("--base", default=os.environ.get("BUGFIX_CONTRACT_BASE"))
    parser.add_argument("--head", default=os.environ.get("BUGFIX_CONTRACT_HEAD"))
    parser.add_argument(
        "--title",
        default=os.environ.get("BUGFIX_CONTRACT_TITLE"),
        help="PR title. A PR titled `fix:` is in scope even when no commit subject is.",
    )
    parser.add_argument(
        "--body-file",
        default=os.environ.get("BUGFIX_CONTRACT_BODY_FILE"),
        help="File holding the PR description.",
    )
    parser.add_argument(
        "--skip-label",
        action="store_true",
        help="The skip-test-contract label is present; report and pass.",
    )
    args = parser.parse_args(argv)

    if args.skip_label:
        print("bugfix-test-contract: skipped via the skip-test-contract label")
        return 0

    explicit_refs = args.base is not None and args.head is not None
    base = args.base or _DEFAULT_BASE
    head = args.head or _DEFAULT_HEAD
    try:
        changed = changed_files(base, head)
        paths = [path for _status, path in changed]
        diff_text = unified_diff(base, head)
        subjects = commit_subjects(base, head)
    except subprocess.CalledProcessError as e:
        # An unresolvable ref is a real failure when CI named the refs, and an
        # ordinary local condition otherwise (no origin/main in a fresh clone).
        # Deliberately not one branch: swallowing it in CI would make this a
        # gate that passes because it could not run.
        if explicit_refs:
            print(f"ERROR: cannot diff {base}...{head}: {e}")
            return 1
        print(
            f"bugfix-test-contract: cannot diff {base}...{head} — nothing was "
            f"checked (exit {EXIT_NO_DIFF}). Fetch the base branch to run this "
            "locally."
        )
        # Partial, not a pass: without a diff even the structural half cannot
        # run, and returning 0 let `verify.py` record the step as passed and
        # the pr profile as complete — the same over-claim as a missing PR
        # body, from an input that is missing for a different reason (Codex
        # review).
        return EXIT_NO_DIFF

    if not is_bugfix(subjects, args.title):
        print("bugfix-test-contract: not a fix/perf/security change — not applicable")
        return 0

    failures: list[str] = []

    # --- structural ----------------------------------------------------
    if touches_shipped_code(paths) and not adds_or_modifies_a_test(
        changed, diff_text, lambda path: _show_at(head, path)
    ):
        failures.append(
            "This fix changes shipped code but no test. A fix with no test "
            "cannot fail if it is reverted — add the regression test, or use "
            "the skip-test-contract label if there is genuinely nothing to "
            "assert (and say why in the PR)."
        )

    # --- declared ------------------------------------------------------
    # "No --body-file at all" (a local run) and "--body-file given but empty"
    # are different situations and must not share a code path: CI always passes
    # the flag, so treating an empty file as "local, structural only" let a
    # contributor clear the PR description and bypass every declared
    # requirement (Codex review).
    if args.body_file is None:
        print(
            "bugfix-test-contract: no --body-file given — structural checks "
            "only. CI always passes one; this path is for local runs."
        )
    else:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"ERROR: cannot read --body-file: {e}")
            return 1
        if not body.strip():
            failures.append(
                "The PR description is empty. A fix PR has to answer the "
                "bug-fix test contract — fill in that section of the PR "
                "template (or use the skip-test-contract label)."
            )
        else:
            for req in missing_requirements(body, paths):
                failures.append(
                    f"{req.prompt} — unanswered.\n      Why it matters: {req.why}"
                )

    if failures:
        print("bugfix-test-contract: FAILED\n")
        for item in failures:
            print(f"  * {item}\n")
        print(
            "  Fill in the 'Bug-fix test contract' section of the PR template.\n"
            "  See scripts/check_bugfix_test_contract.py for what each answer "
            "is for."
        )
        return 1

    if args.body_file is None:
        # Everything that could run, ran and passed — but the declared half
        # needs a PR body this invocation was not given. Distinct from both
        # outcomes: a plain 0 let `verify.py --profile pr` claim CI parity
        # having checked half the gate, and skipping the step outright (the
        # first attempt at this) threw away the structural half's local
        # coverage instead (Codex review). A structural *failure* above still
        # returns 1: a real finding outranks a partial run.
        print(
            "bugfix-test-contract: structural checks passed; the declared "
            f"half did not run (exit {EXIT_STRUCTURAL_ONLY})."
        )
        return EXIT_STRUCTURAL_ONLY

    print("bugfix-test-contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
