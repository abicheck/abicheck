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

"""The abicheck surface a skill consumes, and its digest (G37 D6).

A leaf module, pure stdlib, imported by **both** `gen_skill_eval_pack.py` and
`check_skill_eval_freshness.py` — the checker cannot import the generator (that
one needs PyYAML and must stay a post-install step), and two copies of this
definition would be two things to drift.

**This digest is deliberately not recorded in the committed pack.** It is
derived from files other pull requests change: `docs/reference/cli-reference.md`
moves whenever any PR adds a CLI option. Committing it would mean every such PR
has to regenerate the eval pack, and — worse — a PR that changes the CLI while
another PR is open merges cleanly into a `main` whose committed pack is now
stale, because the two touch different files and git sees no conflict. That is
not hypothetical: two CLI PRs (`scan` severity options, then `dump
--compression`) landed during this feature's own review.

Computing it at check time removes the tax without weakening anything.
Invalidation is preserved exactly — a bundle records the digest it ran against,
and the checker compares that to what the surface is *now*, so a CLI change
still makes every bundle stale. What disappears is only the committed copy
nobody needed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: The committed, drift-gated projections of the live objects a skill can
#: reach: the CLI command/option reference (every command, option, default,
#: choice and help string), the detector spec (every `ChangeKind`'s verdict,
#: severity and minimum evidence), and the report schema a skill parses.
#:
#: Files, not live introspection: the pack is checked on three operating
#: systems and four Pythons, and a digest that varies with the host makes the
#: gate unsatisfiable somewhere — which is exactly what a live Click/registry
#: walk did before this (macOS and Ubuntu 3.14 both failed).
SURFACE_SOURCES: tuple[Path, ...] = (
    Path("docs/reference/cli-reference.md"),
    Path("docs/reference/detector-spec.json"),
    Path("abicheck/schemas/compare_report.schema.json"),
)

#: Routing declarations: the artifacts above plus the sources whose change is
#: what regenerates them, so an observed read of `cli.py` resolves here.
SURFACE_ROOTS: tuple[Path, ...] = (
    *SURFACE_SOURCES,
    Path("abicheck/cli.py"),
    Path("abicheck/cli_options.py"),
    Path("abicheck/frontends/cli/options/inventory.py"),
    Path("abicheck/schemas"),
    Path("abicheck/change_registry.py"),
    Path("abicheck/checker_policy.py"),
)


def normalize_newlines(data: bytes) -> bytes:
    """Content, independent of how a checkout wrote the line endings.

    Git converts LF to CRLF on Windows by default, so the same commit yields
    different bytes on different hosts — and a committed digest over those
    bytes then fails its own `--check` on one platform, which is what the
    Windows unit lane reported. Normalizing here keeps the digest a property
    of the repository rather than of the checkout. Two files differing only in
    line endings hash alike, which is exactly the intent.
    """
    return data.replace(b"\r\n", b"\n")


def surface_digest(root: Path) -> str:
    """Digest the consumed surface as it stands in `root` right now."""
    missing = [rel.as_posix() for rel in SURFACE_SOURCES if not (root / rel).is_file()]
    if missing:
        # Substituting b"" would make a *renamed* source freeze the digest: it
        # would keep returning a stable value that no longer tracks the artifact,
        # so every bundle would read as fresh across CLI changes — the exact
        # invalidation this module exists to preserve. These are committed,
        # drift-gated files; absence is a repository error, not a state to hash.
        raise FileNotFoundError(
            "consumed-surface source(s) absent, so the digest would stop tracking "
            f"them: {', '.join(sorted(missing))}"
        )
    h = hashlib.sha256()
    for rel in sorted(SURFACE_SOURCES, key=lambda p: p.as_posix()):
        path = root / rel
        h.update(rel.as_posix().encode())
        h.update(b"\0")
        h.update(normalize_newlines(path.read_bytes()))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def surface_roots() -> list[str]:
    return sorted(p.as_posix() for p in SURFACE_ROOTS)
