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

"""build_npm_skill_package.py — stage `packages/abicheck-skills/` for npm.

ADR-058's publication step: the Agent Skill rendered from `skills-src/`
(by `gen_agent_skills.render_all`, the one renderer — nothing is
re-implemented here) is written into the npm package's `skills/` directory
together with a `manifest.json` the `npx abicheck-skills` installer reads.

The package version is abicheck's own (`pyproject.toml`), because a skill's
`abicheck-version-range` names the release whose CLI surface it drives: one
number for both keeps "which skill goes with which abicheck" answerable.

Usage:
    python scripts/build_npm_skill_package.py            # stage skills/, sync version
    python scripts/build_npm_skill_package.py --check    # fail if package.json's
                                                          # version drifted (CI)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_DIR / "packages" / "abicheck-skills"
PACKAGE_JSON = PACKAGE_DIR / "package.json"
STAGED_SKILLS = PACKAGE_DIR / "skills"

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_agent_skills as gen  # noqa: E402

_RANGE_RE = re.compile(r'^\s*abicheck-version-range:\s*"([^"]+)"\s*$', re.MULTILINE)


def abicheck_version() -> str:
    data = tomllib.loads((REPO_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def build_manifest(rendered: dict[str, str], package_version: str) -> dict:
    """The installer's view of `rendered`: skill names, files, version ranges."""
    skills: dict[str, list[str]] = {}
    for key in sorted(rendered):
        name, _, rel = key.partition("/")
        skills.setdefault(name, []).append(rel)
    entries = []
    for name, files in skills.items():
        skill_md = rendered.get(f"{name}/SKILL.md")
        if skill_md is None:
            raise gen.SkillGenerationError(f"{name}: rendered without a SKILL.md")
        match = _RANGE_RE.search(skill_md)
        if match is None:
            raise gen.SkillGenerationError(
                f"{name}: SKILL.md declares no metadata.abicheck-version-range"
            )
        entries.append(
            {"name": name, "abicheck_version_range": match.group(1), "files": files}
        )
    return {"package_version": package_version, "skills": entries}


def stage(rendered: dict[str, str], dest: Path, package_version: str) -> dict:
    manifest = build_manifest(rendered, package_version)
    if dest.exists():
        shutil.rmtree(dest)
    for key, content in rendered.items():
        path = dest / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def package_version_problem() -> str | None:
    declared = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
    expected = abicheck_version()
    if declared != expected:
        return (
            f"{PACKAGE_JSON.relative_to(REPO_DIR).as_posix()} version {declared} "
            f"!= pyproject.toml version {expected}; run "
            "scripts/build_npm_skill_package.py to sync it"
        )
    return None


def sync_package_version() -> None:
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    data["version"] = abicheck_version()
    PACKAGE_JSON.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        rendered = gen.render_all()
    except gen.SkillGenerationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.check:
        problem = package_version_problem()
        try:
            build_manifest(rendered, abicheck_version())
        except gen.SkillGenerationError as exc:
            problem = problem or str(exc)
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        print("npm skill package is consistent with skills-src/ and pyproject.toml")
        return 0

    sync_package_version()
    manifest = stage(rendered, STAGED_SKILLS, abicheck_version())
    shutil.copyfile(REPO_DIR / "LICENSE", PACKAGE_DIR / "LICENSE")
    print(
        f"staged {len(manifest['skills'])} skill(s) into "
        f"{STAGED_SKILLS.relative_to(REPO_DIR).as_posix()} "
        f"for abicheck-skills@{manifest['package_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
