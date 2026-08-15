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
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

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
_SHIPPED_FILES = frozenset({"action.yml"})

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
#: Basename shapes that mark a test module.
_TEST_BASENAME_PREFIX = "test_"
_TEST_BASENAME_SUFFIX = "_test.py"
_TEST_BASENAMES = frozenset({"conftest.py"})
#: Prose suffixes. A file under `tests/` with one of these is documentation,
#: not an executable test or a fixture — editing `tests/CLAUDE.md` alongside a
#: shipped-code fix must not satisfy "you changed a test" (Codex review).
_DOC_SUFFIXES = (".md", ".rst")
#: ...except under `golden/`, where `.md` files really are test data: the
#: golden report snapshots are compared byte-for-byte, so changing one is a
#: genuine test change. Verified against tests/golden/*.md.
_TEST_DATA_DIR = "golden"


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
        "one. 'None — documented as a known gap' is an acceptable answer; "
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
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

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
    return _git(["diff", "-M", "--unified=0", f"{base}...{head}"])


def commit_subjects(base: str, head: str) -> list[str]:
    out = _git(["log", "--format=%s", f"{base}..{head}"])
    return [line for line in out.splitlines() if line]


def is_bugfix(subjects: list[str], title: str | None) -> bool:
    candidates = [*subjects, title or ""]
    return any(_FIX_SUBJECT.match(s.strip()) for s in candidates if s)


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

    Deliberately structural — a `tests/` **directory component**, or a test
    **basename** — rather than a substring search for `test_` anywhere in the
    path. The substring form matched shipped sources whose names merely contain
    it, including `scripts/summarize_test_durations.py` and this checker itself
    (`check_bugfix_test_contract.py`), so a fix editing only those satisfied
    the structural requirement with no test at all — passing exactly the case
    it exists to reject (Codex review).
    """
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    name = parts[-1]
    if _TEST_DIR in parts[:-1]:
        # Inside a test tree, any file type can be test *data* (fixtures,
        # golden snapshots) — only prose is excluded, and not under golden/.
        is_prose = name.endswith(_DOC_SUFFIXES)
        return not is_prose or _TEST_DATA_DIR in parts[:-1]
    # Outside a test tree, the basename forms must be actual Python test
    # modules: `docs/test_plan.md` and `examples/test_notes.txt` start with
    # `test_` and are prose, and counting them satisfied the structural
    # requirement with no executable test changed (Codex review).
    if not name.endswith(".py"):
        return False
    return (
        name.startswith(_TEST_BASENAME_PREFIX)
        or name.endswith(_TEST_BASENAME_SUFFIX)
        or name in _TEST_BASENAMES
    )


def touches_tests(paths: list[str]) -> bool:
    """Path-only form: any test path, whatever happened to it."""
    return any(is_test_path(p) for p in paths)


def added_content_paths(diff_text: str) -> set[str]:
    """Paths this diff adds at least one line of *substantive* content to.

    Pure, so it can be tested against diff text directly. Two exclusions, both
    of which are content-shaped rather than status-shaped:

    * A **pure rename** produces no added lines at all under rename detection
      (`similarity index 100%`, no `+++` header), so it never appears here.
      This is the reason the function exists: `changed_files()` runs with
      `--no-renames`, which reports `git mv tests/test_a.py tests/test_b.py`
      as one `D` and one `A`, and a status-only predicate read that `A` as a
      regression test (Codex review). Renaming a test is not writing one.
    * Blank lines, and comment-only lines in Python, are not evidence either.
      They are the cheapest possible way to produce an `M` on a test file.

    The comment rule is `.py`-only on purpose: `#` starts a heading in a
    golden Markdown snapshot and is ordinary content in most fixture formats,
    so applying it everywhere would discard real test-data changes.
    """
    out: set[str] = set()
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # Reset first: a rename-only entry carries no `+++` header at all,
            # so without this the previous file's attribution would leak into
            # it and any following `+` line would be credited to the rename.
            current = None
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            continue
        if current is None or not line.startswith("+"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        if current.endswith(".py") and body.startswith("#"):
            continue
        out.add(current)
    return out


def adds_or_modifies_a_test(changed: list[tuple[str, str]], diff_text: str) -> bool:
    """Positive evidence that a regression test exists.

    Both halves are required, because each catches something the other cannot:
    the *status* rules out a deletion (the opposite of evidence) and a type
    change (retyping a test file is not writing one), while the *content* rules
    out a rename or a whitespace/comment-only edit, which carry an `A`/`M`
    status while asserting nothing new.
    """
    with_content = added_content_paths(diff_text)
    return any(
        status in ("A", "M") and is_test_path(path) and path in with_content
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
        if not req.applies_to(paths):
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
            f"bugfix-test-contract: cannot diff {base}...{head} — skipping. "
            "Fetch the base branch to run this locally."
        )
        return 0

    if not is_bugfix(subjects, args.title):
        print("bugfix-test-contract: not a fix/perf/security change — not applicable")
        return 0

    failures: list[str] = []

    # --- structural ----------------------------------------------------
    if touches_shipped_code(paths) and not adds_or_modifies_a_test(changed, diff_text):
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

    print("bugfix-test-contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
