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

# Fragment-directory files that don't count as a "changelog entry" themselves.
_NON_FRAGMENT_NAMES = {"README.md"}


def changed_files(base: str, head: str) -> list[str]:
    """Return paths changed between base and head, including deletions.

    A PR that only *removes* an `abicheck/**/*.py` module/API is exactly the
    kind of user-visible change that needs a fragment, so deletions (`D`)
    must count alongside additions/copies/modifications/renames.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMR", f"{base}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def needs_changelog_entry(changed: list[str]) -> bool:
    """True if the diff touches abicheck's source (not just tests/docs/scripts)."""
    return any(f.startswith("abicheck/") and f.endswith(".py") for f in changed)


def has_changelog_fragment(changed: list[str]) -> bool:
    """True if the diff adds/modifies a real fragment file under changelog.d/."""
    for f in changed:
        if not f.startswith("changelog.d/"):
            continue
        name = f.split("/", 1)[1]
        if name in _NON_FRAGMENT_NAMES or name.endswith(".j2"):
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
