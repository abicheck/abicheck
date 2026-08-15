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
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

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
    "abicheck/": (".py",),
    "scripts/": (".py",),
    "action/": (".py", ".sh"),
}
_SHIPPED_PREFIXES = tuple(_SHIPPED_SUFFIXES_BY_PREFIX)

#: Individually-named shipped files that no prefix rule covers. The root
#: `action.yml` *is* the published composite Action — it declares its inputs
#: and its executable steps — and it has dedicated coverage in
#: `tests/test_action_reference.py` / `test_action_run_contract.py`, so a fix
#: to it can and should carry a test (Codex review).
_SHIPPED_FILES = frozenset({"action.yml"})

#: Directory component that marks a test tree.
_TEST_DIR = "tests"
#: Basename shapes that mark a test module.
_TEST_BASENAME_PREFIX = "test_"
_TEST_BASENAME_SUFFIX = "_test.py"
_TEST_BASENAMES = frozenset({"conftest.py"})


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
        # Only *shipped* paths trigger a conditional. A test file named after
        # the area it covers (tests/test_finding_identity.py) would otherwise
        # ask its question on every test-only change, and a conditional that
        # fires on everything is boilerplate rather than a signal.
        subject = [p for p in paths if not is_test_path(p)]
        return any(t in p for p in subject for t in self.triggers)


#: The four always-required answers, then the conditional ones. Each
#: conditional requirement exists because a real escape in this repository went
#: through that exact surface.
REQUIREMENTS: tuple[Requirement, ...] = (
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
        triggers=("action/", ".github/workflows/", "security"),
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
    out = _git(
        [
            "diff",
            "--no-renames",
            "--name-only",
            "--diff-filter=ACDM",
            f"{base}...{head}",
        ]
    )
    return [line for line in out.splitlines() if line]


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
    if _TEST_DIR in parts[:-1]:
        return True
    name = parts[-1]
    return (
        name.startswith(_TEST_BASENAME_PREFIX)
        or name.endswith(_TEST_BASENAME_SUFFIX)
        or name in _TEST_BASENAMES
    )


def touches_tests(paths: list[str]) -> bool:
    return any(is_test_path(p) for p in paths)


def parse_answers(body: str) -> dict[str, str]:
    """Map a normalized label -> answer text, for every `Label: value` line."""
    answers: dict[str, str] = {}
    for line in body.splitlines():
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
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--title", default=None, help="PR title.")
    parser.add_argument("--body-file", help="File holding the PR description.")
    parser.add_argument(
        "--skip-label",
        action="store_true",
        help="The skip-test-contract label is present; report and pass.",
    )
    args = parser.parse_args(argv)

    if args.skip_label:
        print("bugfix-test-contract: skipped via the skip-test-contract label")
        return 0

    paths = changed_paths(args.base, args.head)
    subjects = commit_subjects(args.base, args.head)

    if not is_bugfix(subjects, args.title):
        print("bugfix-test-contract: not a fix/perf/security change — not applicable")
        return 0

    failures: list[str] = []

    # --- structural ----------------------------------------------------
    if touches_shipped_code(paths) and not touches_tests(paths):
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
