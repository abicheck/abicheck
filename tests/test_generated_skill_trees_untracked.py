"""Every generated agent-skill directory must be git-ignored and untracked.

`tests/conftest.py` regenerates `.agents/skills/`, `.claude/skills/` and
`.gemini/skills/` from `skills-src/` at the start of every pytest session. A
generated skill directory that is tracked (or not ignored) therefore dirties
the working tree on every test run -- which is what happened when
`set-up-abi-compatibility-ci` was added without a `.gitignore` entry and its
generated `.claude/skills/` copy was committed, then went stale. The oracle
here is git itself (`check-ignore` / `ls-files`), not the generator's own
list, and it covers every skill `discover_skills()` finds, so a newly added
skill is checked without editing this file.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="needs a git checkout",
)


def _gen():
    spec = importlib.util.spec_from_file_location(
        "gen_agent_skills_untracked", REPO / "scripts" / "gen_agent_skills.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def _generated_dirs() -> list[str]:
    gen = _gen()
    skills = [p.name for p in gen.discover_skills()]
    assert skills, "discover_skills() found nothing -- the check would pass vacuously"
    return [
        f"{root.relative_to(REPO).as_posix()}/{name}/"
        for root in gen.OUTPUT_ROOTS
        for name in skills
    ]


def test_every_generated_skill_dir_is_git_ignored():
    dirs = _generated_dirs()
    # --no-index: answer from the ignore rules alone, even for a tracked path.
    result = _git("check-ignore", "--no-index", "-v", "--non-matching", *dirs)
    unignored = [
        line.split("\t", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("::")
    ]
    assert not unignored, f"generated skill dirs not covered by .gitignore: {unignored}"


def test_no_generated_skill_file_is_tracked():
    tracked = _git("ls-files", "--", *_generated_dirs()).stdout.split()
    assert not tracked, (
        f"generated skill files are committed (run `git rm -r --cached`): {tracked}"
    )
