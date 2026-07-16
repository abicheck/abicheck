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

"""Restore each tracked file's mtime to its last-commit timestamp.

``abicheck.dumper``'s on-disk AST cache (``dumper_cache._cache_path``, keyed
via ``dumper._cache_key``) hashes each header's *mtime*, not its content — a
deliberate cheap-and-usually-right proxy for "has this file changed" on a
developer's machine, where editing a file naturally bumps its mtime.

A fresh CI checkout breaks that assumption: ``actions/checkout`` sets every
file's mtime to the moment of checkout, not the commit that last touched it,
so the AST cache key changes on every single run even when a header's content
is byte-identical to the last run — a persisted ``actions/cache`` for the AST
cache dir would then miss on every entry, every time.

This script restores each tracked file's mtime to its last-commit timestamp
(the same trick ``git-restore-mtime`` and similar reproducible-checkout tools
use): identical content at the same commit always gets the same mtime, so the
cache key is stable across runs and a persisted cache actually hits. A commit
that edits a file gives it a new, different timestamp, so real content changes
still correctly invalidate the cache entry.

Single ``git log`` walk over the requested paths (not one subprocess per
file): commits are visited newest-first, so the first commit timestamp seen
for a path is already its last-modified time.

Usage:
    python scripts/restore_git_mtimes.py [PATH ...]   # default: examples
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent


def _last_commit_mtimes(paths: list[str]) -> dict[str, int]:
    """Return {tracked file path: last-commit unix timestamp} under *paths*."""
    out = subprocess.run(
        [
            "git",
            "log",
            "--name-only",
            "--no-renames",
            "--pretty=format:@@@%ct",
            "--",
            *paths,
        ],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    mtimes: dict[str, int] = {}
    current_ts: int | None = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            current_ts = int(line[3:])
            continue
        if not line or current_ts is None:
            continue
        # First (newest) commit seen for a path is its last-modified time.
        mtimes.setdefault(line, current_ts)
    return mtimes


def restore_mtimes(paths: list[str]) -> int:
    """Set each tracked file under *paths* to its last-commit mtime. Returns count touched."""
    mtimes = _last_commit_mtimes(paths)
    touched = 0
    for rel_path, ts in mtimes.items():
        full_path = REPO_DIR / rel_path
        try:
            os.utime(full_path, (ts, ts))
        except OSError:
            continue  # deleted since that commit, or otherwise absent — nothing to touch
        touched += 1
    return touched


def main() -> int:
    paths = sys.argv[1:] or ["examples"]
    touched = restore_mtimes(paths)
    print(
        f"Restored git last-commit mtimes on {touched} file(s) under {', '.join(paths)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
