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

"""Flag docs pages whose declared `depends_on` paths a PR just touched.

docs/AGENTS.md's front-matter schema lets a page declare `depends_on`: repo
paths (code, CLI commands, config keys) whose change should prompt a look at
that page. Until now that field was purely informational — parsed and
schema-checked (`check_docs_contract.py`) but never actually compared against
a PR's changed files. This script closes that gap: it's a *review trigger*,
not a gate — it never fails the build, it only surfaces "this page might be
stale" as a PR-visible signal, the same spirit as a CODEOWNERS suggestion.

Deliberately advisory, not blocking:
  - `depends_on` paths are hand-maintained free text, not a generated index —
    a false positive (unrelated file under a broad prefix) or false negative
    (a page missing a `depends_on` entry it should have) is expected, so
    failing the build on a hit would just train people to ignore the gate.
  - The signal is most useful as an `::notice::` annotation (shows inline on
    the PR's Files tab) plus a step-summary table, not a hard failure.

Run locally:

    python scripts/check_docs_review_triggers.py --base origin/main --head HEAD

In CI this is invoked with the PR's actual base/head SHAs (see
.github/workflows/docs-review-triggers.yml).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_docs_contract import load_front_matter  # noqa: E402


def changed_files(base: str, head: str) -> list[str]:
    """Return the paths touched between `base` and `head` (added, modified,
    deleted, or either side of a rename — `--no-renames` reports a move as a
    delete-old + add-new pair so a page depending on the *old* path still
    triggers)."""
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", f"{base}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        # Outside ROOT — only reachable when a test monkeypatches DOCS to a
        # tmp_path fixture; real runs always resolve under ROOT.
        return str(path)


def _depends_on_for_page(path: Path) -> list[str] | None:
    try:
        fm = load_front_matter(path)
    except (yaml.YAMLError, ValueError):
        return None  # malformed front matter is reported by check_docs_contract.py
    if fm is None:
        return None
    depends_on = fm.get("depends_on")
    if not isinstance(depends_on, list):
        return None
    return [d for d in depends_on if isinstance(d, str)]


def _matches(dep: str, changed: str) -> bool:
    """A `depends_on` entry matches a changed path if it names that exact
    file, or names a directory the changed file lives under (trailing slash
    optional in the front-matter entry -- 'abicheck/buildsource' and
    'abicheck/buildsource/' both mean the whole subtree)."""
    dep = dep.rstrip("/")
    return changed == dep or changed.startswith(dep + "/")


def find_triggers(changed: list[str]) -> dict[str, list[str]]:
    """Map each docs page (repo-relative) with a `depends_on` hit to the
    sorted list of changed paths that triggered it."""
    triggers: dict[str, list[str]] = {}
    for path in sorted(DOCS.rglob("*.md")):
        depends_on = _depends_on_for_page(path)
        if not depends_on:
            continue
        hits = sorted(
            {c for c in changed for dep in depends_on if _matches(dep, c)}
        )
        if hits:
            triggers[_rel(path)] = hits
    return triggers


def render_summary(triggers: dict[str, list[str]]) -> str:
    if not triggers:
        return "No docs page's `depends_on` list overlaps this PR's changed files.\n"
    lines = [
        "| Page | Changed dependency |",
        "|------|---------------------|",
    ]
    for page, hits in sorted(triggers.items()):
        lines.append(f"| `{page}` | {', '.join(f'`{h}`' for h in hits)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base git ref")
    parser.add_argument("--head", default="HEAD", help="Head git ref")
    args = parser.parse_args()

    try:
        changed = changed_files(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(
            f"check_docs_review_triggers: could not diff {args.base}...{args.head} "
            f"({exc}) — skipping (nothing to compare locally is not an error).",
        )
        return 0

    triggers = find_triggers(changed)
    summary = render_summary(triggers)
    print(summary)

    for page, hits in sorted(triggers.items()):
        print(
            f"::notice file={page}::depends_on changed file(s) "
            f"({', '.join(hits)}) — consider reviewing this page."
        )

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write("\n## Docs review triggers\n\n")
            fh.write(summary)

    return 0  # advisory only — never fails the build


if __name__ == "__main__":
    raise SystemExit(main())
