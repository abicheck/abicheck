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

"""install_dev_skill.py — materialize the generated agent-skill trees locally.

`scripts/gen_agent_skills.py` renders `skills-src/` into three publication
trees (`.agents/skills/`, `.claude/skills/`, `.gemini/skills/`). Those trees
used to be committed; they no longer are (see the 2026-08-21 amendment to
ADR-058) — they are build output, reproducible on demand from `skills-src/`,
and committing them just meant a second copy to keep in sync. CI regenerates
them itself wherever a job needs one materialized; a contributor who wants to
exercise an installed skill locally (point Claude Code, Codex, or Gemini CLI
at this checkout, or run a Harbor task by hand) needs the identical trees on
their own disk, which is what this script is for.

Thin wrapper: all rendering, link-rewriting, and validation logic lives in
`gen_agent_skills.py` (`render_all`/`write_trees`) — this script does not
reimplement any of it, only selects which of the three output roots to
write.

Usage:
    python scripts/install_dev_skill.py                  # writes all three
    python scripts/install_dev_skill.py --target claude   # writes one
    python scripts/install_dev_skill.py --target codex,gemini
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_agent_skills as gen  # noqa: E402

#: CLI-facing target name -> the OUTPUT_ROOTS entry it selects. "codex" is an
#: alias for the portable `.agents/skills/` tree (Codex, Copilot, and Cursor
#: all read it directly per ADR-058 — there is no vendor-specific tree for
#: any of them to need); "all" is handled separately in `main`.
_TARGET_ROOTS: dict[str, Path] = {
    "codex": REPO_DIR / ".agents" / "skills",
    "claude": REPO_DIR / ".claude" / "skills",
    "gemini": REPO_DIR / ".gemini" / "skills",
}

assert set(_TARGET_ROOTS.values()) == set(gen.OUTPUT_ROOTS), (
    "install_dev_skill.py's target map has drifted from gen_agent_skills.py's "
    "own OUTPUT_ROOTS — every generated tree must be reachable by name here"
)


def _parse_targets(raw: str) -> tuple[Path, ...]:
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        raise argparse.ArgumentTypeError("--target must name at least one tree")
    if names == ["all"]:
        return gen.OUTPUT_ROOTS
    roots: list[Path] = []
    for name in names:
        if name == "all":
            raise argparse.ArgumentTypeError(
                "--target 'all' must not be combined with other names"
            )
        root = _TARGET_ROOTS.get(name)
        if root is None:
            choices = ", ".join([*sorted(_TARGET_ROOTS), "all"])
            raise argparse.ArgumentTypeError(
                f"unknown --target {name!r} (choices: {choices})"
            )
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default="all",
        type=_parse_targets,
        help=(
            "which generated tree(s) to write, comma-separated: "
            "codex (.agents/skills/, also read by Copilot and Cursor), "
            "claude (.claude/skills/), gemini (.gemini/skills/), "
            "or all (default)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        rendered = gen.render_all()
    except gen.SkillGenerationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    gen.write_trees(rendered, roots=args.target)
    print(
        f"wrote {len(rendered)} files to "
        + ", ".join(r.relative_to(REPO_DIR).as_posix() for r in args.target)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
