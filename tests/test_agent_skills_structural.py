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
check that is *not* implied by the syntactic drift tests — that each skill's
declared abicheck version range names a release actually providing the CLI
surface its workflow drives. A skill whose minimum predates that surface
would approve an installation it then fails on, which is exactly the failure
the range exists to prevent.

Scope: this closes syntactic and declarative drift only. A flag or field that
keeps its name while its behaviour changes is not caught here (nor anywhere
else in this plan); revisiting the affected skill on that command's normal
review cadence is the only mitigation.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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


#: The first release that provides the CLI surface these workflows drive.
#:
#: Stated as a constant rather than derived, because it is a fact about
#: *which release contains which commands* — something no signal in the
#: working tree can answer. Two derivations were tried and both were wrong:
#: the installed version (`importlib.metadata` reports the last *published*
#: version in an unreleased tree, so it forced the minimum down to a release
#: predating the surface), and "is `changelog.d/` non-empty" (any unrelated
#: post-0.6.0 fragment would then demand a minimum above 0.6.0, blocking
#: every later PR over surface that had in fact already shipped).
#:
#: 0.6.0 because `aggregate`, `project plan`, `--report-mode root-cause`,
#: `--diagnostic-comparison`, and `--contract-evaluation` all postdate the
#: 0.5.0 release. Raise this only when a skill starts depending on surface
#: that first ships in a later release — not when the tree's version moves.
SURFACE_FLOOR = (0, 6, 0)


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_declared_minimum_is_not_a_release_that_lacks_the_surface(skill_dir: Path):
    """The declared minimum must name a release that actually provides the CLI
    surface these workflows drive.

    This replaces an earlier "installed version is inside the range" check,
    which was actively harmful: in an unreleased tree `importlib.metadata`
    reports the *last published* version (there is no separate dev marker), so
    satisfying that assertion forced the minimum down to a release that
    predates `aggregate`, `project plan`, `--report-mode root-cause`,
    `--diagnostic-comparison`, and `--contract-evaluation`. The preflight then
    approved exactly the installation the workflow cannot run on — the failure
    the version range exists to prevent.

    The bar is `SURFACE_FLOOR` — the release that first contains that
    surface — and deliberately not anything derived from the working tree.
    See that constant for the two derivations that were tried and why each
    produced a wrong answer in one direction or the other.
    """
    raw = _front_matter(skill_dir / "SKILL.md")["metadata"]["abicheck-version-range"]
    match = _VERSION_RANGE_RE.match(raw)
    assert match is not None
    minimum, maximum = (_version_tuple(g) for g in match.groups())

    assert minimum >= SURFACE_FLOOR, (
        f"{skill_dir.name} declares a minimum of {match.group(1)}, which "
        f"predates {'.'.join(map(str, SURFACE_FLOOR))} — the release that "
        "first provides the commands and options this workflow drives. A "
        "user on that older version would pass the preflight and then fail "
        "on unknown commands, which is what the range exists to prevent"
    )
    assert maximum > minimum, "the range must be non-empty"


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
