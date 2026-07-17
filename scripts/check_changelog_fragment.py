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
"""Fail a PR that changes abicheck's behavior without a changelog fragment.

Complements CHANGELOG.md's move to scriv-managed fragments (changelog.d/):
every PR that touches `abicheck/**/*.py` must add its own
`changelog.d/<name>.md` file (via `scriv create`) instead of hand-editing
the shared "Unreleased" section, which is what caused near-constant merge
conflicts there. See changelog.d/README.md for the fragment workflow.

Run locally:

    python scripts/check_changelog_fragment.py --base origin/main --head HEAD

In CI this is invoked with the PR's actual base/head SHAs. Pass
--skip-label when the PR carries the `skip-changelog` label (internal
refactors, test-only changes) to bypass the gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Real fragments are Markdown ([tool.scriv] format = "md" in pyproject.toml);
# scriv collect only reads *.md/*.rst in changelog.d/, so anything else
# (README.md, the .j2 template, or e.g. an accidental placeholder.txt) must
# not satisfy the gate.
_FRAGMENT_SUFFIX = ".md"
_NON_FRAGMENT_NAMES = {"README.md"}

# A (status, path) pair, as produced by `git diff --name-status`.
ChangedFile = tuple[str, str]


def changed_files(base: str, head: str) -> list[ChangedFile]:
    """Return (status, path) pairs changed between base and head, incl. deletions.

    A PR that only *removes* an `abicheck/**/*.py` module/API is exactly the
    kind of user-visible change that needs a fragment, so deletions (`D`)
    must count alongside additions/modifications. `--no-renames` makes git
    report a move as a separate delete-old + add-new pair instead of a
    single `R` entry — with rename detection on, `--name-only` prints only
    the destination path, so a file moved *out* of `abicheck/` (e.g.
    `abicheck/foo.py` -> `tools/foo.py`) would never surface its old,
    now-deleted `abicheck/` path. The status is kept (not just the path) so
    a *deleted* changelog.d/ fragment can't satisfy the gate — only an
    added/modified fragment actually gives `scriv collect` something to
    include in the next release.
    """
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-status",
            "--diff-filter=ACDM",
            f"{base}...{head}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    changes = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        status, path = line.split("\t", 1)
        changes.append((status, path))
    return changes


def needs_changelog_entry(changed: list[ChangedFile]) -> bool:
    """True if the diff touches abicheck's source (not just tests/docs/scripts)."""
    return any(
        path.startswith("abicheck/") and path.endswith(".py")
        for _status, path in changed
    )


def has_changelog_fragment(changed: list[ChangedFile]) -> bool:
    """True if the diff adds/modifies (not just deletes) a fragment in changelog.d/."""
    for status, f in changed:
        if status == "D" or not f.startswith("changelog.d/"):
            continue
        name = f.split("/", 1)[1]
        if name in _NON_FRAGMENT_NAMES or not name.endswith(_FRAGMENT_SUFFIX):
            continue
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base git ref")
    parser.add_argument("--head", default="HEAD", help="Head git ref")
    parser.add_argument(
        "--skip-label",
        action="store_true",
        help="Bypass the gate (PR carries the skip-changelog label)",
    )
    args = parser.parse_args()

    if args.skip_label:
        print("skip-changelog label present — skipping changelog-fragment check.")
        return 0

    changed = changed_files(args.base, args.head)
    if not needs_changelog_entry(changed):
        print("No abicheck/**/*.py changes — no changelog fragment required.")
        return 0

    if has_changelog_fragment(changed):
        print("Changelog fragment found in changelog.d/ — OK.")
        return 0

    print(
        "::error::This PR changes abicheck/**/*.py but adds no changelog "
        "fragment. Run `scriv create` and describe your change in the "
        "generated changelog.d/<name>.md file (see changelog.d/README.md), "
        "or add the `skip-changelog` label if this change has no "
        "user-facing effect.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
