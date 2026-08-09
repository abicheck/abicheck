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

"""Structural gate for the published Agent Skills (ADR-058 / G36 P0.7).

Frontmatter validity, self-containment of the generated trees, and — the
check that is *not* implied by the syntactic drift tests — abicheck
version-range containment in **both** directions: a skill declaring a minimum
because it depends on a command or field introduced in that release would
otherwise pass silently against an older installation that predates it.

Scope: this closes syntactic and declarative drift only. A flag or field that
keeps its name while its behaviour changes is not caught here (nor anywhere
else in this plan); revisiting the affected skill on that command's normal
review cadence is the only mitigation.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "skills-src"
SHARED = SRC / "shared"

_spec = importlib.util.spec_from_file_location(
    "gen_agent_skills_structural", REPO / "scripts" / "gen_agent_skills.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("gen_agent_skills_structural", gen)
_spec.loader.exec_module(gen)

SKILL_DIRS = gen.discover_skills(SRC)

#: The Agent Skills format's own frontmatter constraints.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_NAME = 64
_MAX_DESCRIPTION = 1024

_VERSION_RANGE_RE = re.compile(
    r"^>=\s*([0-9][0-9A-Za-z.\-+]*)\s*,\s*<\s*([0-9][0-9A-Za-z.\-+]*)$"
)

_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert match is not None, f"{path}: missing YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{path}: frontmatter is not a mapping"
    return data


def _version_tuple(text: str) -> tuple[int, ...]:
    """Numeric release tuple, ignoring any pre/post/local suffix.

    Deliberately small rather than reaching for `packaging`: abicheck's own
    versions are plain `MAJOR.MINOR.PATCH`, and this test must not gain a
    dependency the fast lane does not already have.
    """
    core = re.match(r"^([0-9]+(?:\.[0-9]+)*)", text)
    assert core is not None, f"unparseable version {text!r}"
    return tuple(int(part) for part in core.group(1).split("."))


def test_four_p0_skills_are_present():
    """ADR-058's admission bar caps the portfolio: a fifth public skill needs
    all five criteria re-applied, not a drive-by directory."""
    assert [d.name for d in SKILL_DIRS] == [
        "native-api-evolution",
        "native-binary-compatibility-review",
        "native-consumer-compatibility",
        "native-release-compatibility",
    ]


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_frontmatter_is_valid(skill_dir: Path):
    fm = _front_matter(skill_dir / "SKILL.md")

    name = fm.get("name")
    assert isinstance(name, str) and name, "name is required"
    assert name == skill_dir.name, "name must match its directory"
    assert _NAME_RE.match(name), f"{name!r} must be lowercase-hyphen-separated"
    assert len(name) <= _MAX_NAME

    description = fm.get("description")
    assert isinstance(description, str) and description.strip(), (
        "description is required — it IS the discovery mechanism"
    )
    assert len(description) <= _MAX_DESCRIPTION


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_description_is_user_task_first_not_product_named(skill_dir: Path):
    """ADR-058's central product bet: a user who has never heard of abicheck
    must be able to trigger the skill. A description that leads with the
    product name has already failed that bar."""
    description = _front_matter(skill_dir / "SKILL.md")["description"]
    assert not description.lower().startswith("abicheck")
    assert not skill_dir.name.startswith("abicheck-")


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_declares_an_abicheck_version_range(skill_dir: Path):
    metadata = _front_matter(skill_dir / "SKILL.md").get("metadata")
    assert isinstance(metadata, dict), "metadata frontmatter is required"
    raw = metadata.get("abicheck-version-range")
    assert isinstance(raw, str) and _VERSION_RANGE_RE.match(raw), (
        f"metadata.abicheck-version-range must be spelled '>=MIN,<MAX'; got {raw!r}"
    )


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_installed_abicheck_is_inside_each_declared_range(skill_dir: Path):
    """Containment in both directions, failing loudly rather than degrading.

    This is a CI-time check over *this* repository's committed skills; it does
    not reach a user's own installation. Each `SKILL.md`'s own workflow
    performs the equivalent check at the start of a real session, which is
    what covers a standalone install.
    """
    raw = _front_matter(skill_dir / "SKILL.md")["metadata"]["abicheck-version-range"]
    match = _VERSION_RANGE_RE.match(raw)
    assert match is not None
    minimum, maximum = (_version_tuple(g) for g in match.groups())
    current = _version_tuple(installed_version("abicheck"))

    assert current >= minimum, (
        f"installed abicheck {installed_version('abicheck')} is BELOW "
        f"{skill_dir.name}'s declared minimum — the skill depends on surface "
        "this installation predates"
    )
    assert current < maximum, (
        f"installed abicheck {installed_version('abicheck')} has reached or "
        f"passed {skill_dir.name}'s declared maximum — re-validate the skill "
        "and widen the range deliberately"
    )


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_workflow_checks_its_own_version_range_at_runtime(skill_dir: Path):
    """The CI check above protects the repository; this asserts the skill also
    tells the agent to verify the range in a real session, which is the only
    thing that protects a standalone install."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "version range" in text and "--version" in text


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_links_the_shared_safety_invariants_rather_than_restating_them(
    skill_dir: Path,
):
    """One fact, one place: the operational safety invariants live in exactly
    one file and every skill cites it."""
    targets = _MD_LINK_RE.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert any(t.endswith("shared/safety-invariants.md") for t in targets)


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_skill_states_termination_criteria(skill_dir: Path):
    """ADR-058's skill content model: a workflow must say when the job is
    actually done, so a plausible-sounding narrative cannot end it."""
    assert "## Termination criteria" in (skill_dir / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_source_links_to_docs_pages_all_resolve():
    """Checked at source level, where the target is still a repo path — once
    generated, these are absolute published URLs no test can resolve offline.
    The generator itself also hard-fails on a missing target; this asserts the
    committed sources are clean rather than relying on generation order."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.md")):
        for target in _MD_LINK_RE.findall(path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            bare = target.split("#", 1)[0]
            if not bare:
                continue
            if not (path.parent / bare).resolve().is_file():
                offenders.append(f"{path.relative_to(REPO)}: {target}")
    assert offenders == []


def test_no_skill_source_file_is_a_symlink():
    assert not [p for p in SRC.rglob("*") if p.is_symlink()]


def test_shared_fragments_carry_the_docs_contract_front_matter():
    """A `shared/` fragment summarizing a canonical docs page must declare it,
    so `scripts/check_docs_contract.py` enforces the round-trip rather than
    the claim living only in prose."""
    missing: list[str] = []
    for fragment in sorted(SHARED.glob("*.md")):
        fm = _front_matter(fragment)
        if fm.get("doc_type") != "reference":
            missing.append(f"{fragment.name}: doc_type must be 'reference'")
        if fm.get("lifecycle") != "active":
            missing.append(f"{fragment.name}: lifecycle must be 'active'")
    assert missing == []
