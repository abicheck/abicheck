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
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import _yaml_fast
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
    data = _yaml_fast.safe_load(match.group(1))
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


def test_the_published_portfolio_is_exactly_the_admitted_skills():
    """ADR-058: every public skill passes the five-criteria admission bar in
    an ADR amendment of its own. `check-abi-compatibility` survived the
    2026-08-20 portfolio reset; `explain-abi-change` and
    `set-up-abi-compatibility-ci` were admitted by 2026-09-30 amendments. A new directory here without that record fails."""
    assert [d.name for d in SKILL_DIRS] == [
        "check-abi-compatibility",
        "explain-abi-change",
        "set-up-abi-compatibility-ci",
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
#: `--diagnostic-comparison`, and `--contract` all postdate the
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
    `--diagnostic-comparison`, and `--contract`. The preflight then
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
    for path in gen.published_source_files(SRC):
        # Code examples are masked for the same reason the generator masks
        # them: link syntax shown inside a fence is content, not a link, and
        # demanding it resolve would reject a source for demonstrating one.
        masked, _ = gen.mask_code_regions(path.read_text(encoding="utf-8"))
        for target in _MD_LINK_RE.findall(masked):
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


# --------------------------------------------------------------------------
# Release pins, the declared range, `with:` inputs and baseline overwrites.
#
# The 0.7.0 drift got past every gate above: the range's upper bound was never
# checked, the release a skill pins was never compared with its range, `with:`
# keys were never read against the Action, and a template taught a `--clobber`
# upload. Each check below has a negative control that proves it fires.
# --------------------------------------------------------------------------

#: An abicheck release pin: `abicheck/abicheck@v0.7.0`, a sub-path Action or
#: reusable workflow, or a commit-SHA placeholder whose trailing `# v0.6.0`
#: comment names the release it resolves to.
_RELEASE_PIN_RE = re.compile(
    r"abicheck/abicheck(?:/(?P<path>[\w./-]+?))?@"
    r"(?:v(?P<tag>\d+\.\d+\.\d+)\b|(?P<ref>[^\s#]+))"
    r"(?:[ \t]*#[ \t]*v(?P<comment>\d+\.\d+\.\d+)\b)?"
)

_DATED_RELEASE_RE = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] (?:—|–|-) (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE
)

#: A release-asset write that replaces or removes a published baseline, matched
#: as a command: an overwrite flag on a `gh release upload` line, or a
#: `gh release delete-asset` that starts a command (not one inside an `echo`).
_OVERWRITE_RE = re.compile(
    r"gh release upload\b[^\n]*--clobber\b|^\s*(?:-\s*)?(?:run:\s*)?gh release delete-asset\b",
    re.MULTILINE,
)

#: Path (as `_display` renders it) -> reason, for an overwrite a skill or user
#: doc may teach. Empty by design: a release baseline is write-once everywhere
#: the skills and the `docs/use` pages show it.
OVERWRITE_ALLOWLIST: dict[str, str] = {}


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def _in_range(version: str, raw_range: str) -> bool:
    match = _VERSION_RANGE_RE.match(raw_range)
    assert match is not None, f"unparseable range {raw_range!r}"
    minimum, maximum = (_version_tuple(group) for group in match.groups())
    return minimum <= _version_tuple(version) < maximum


def _release_pins(text: str) -> list[tuple[int, str, str]]:
    """(line number, release the pin names, the pin as written)."""
    pins = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _RELEASE_PIN_RE.finditer(line):
            release = match.group("tag") or match.group("comment")
            if release:
                pins.append((lineno, release, match.group(0)))
    return pins


def _pins_outside_range(root: Path, raw_range: str) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*.md")):
        for lineno, release, pin in _release_pins(path.read_text(encoding="utf-8")):
            if not _in_range(release, raw_range):
                offenders.append(
                    f"{_display(path)}:{lineno}: {pin!r} names {release}, "
                    f"outside {raw_range}"
                )
    return offenders


def _newest_dated_release(changelog: str) -> str:
    """Highest-dated released version. Undated `not yet released` headings are
    not releases, so the answer is about released history, never the tree."""
    dated = _DATED_RELEASE_RE.findall(changelog)
    assert dated, "CHANGELOG.md has no dated release heading"
    return max(dated, key=lambda entry: entry[1])[0]


def _release_steps(node: object) -> Iterator[tuple[str, Mapping[str, object]]]:
    """(`uses:` value, `with:` mapping) for every step calling abicheck."""
    if isinstance(node, dict):
        uses = node.get("uses")
        if isinstance(uses, str) and "abicheck/abicheck" in uses:
            with_block = node.get("with")
            yield uses, with_block if isinstance(with_block, dict) else {}
        for value in node.values():
            yield from _release_steps(value)
    elif isinstance(node, list):
        for value in node:
            yield from _release_steps(value)


def _action_inputs(uses: str) -> set[str] | None:
    """Inputs the Action or reusable workflow named by `uses:` declares, read
    from this repository at HEAD. None when no such file exists."""
    match = re.match(r"abicheck/abicheck(?:/(?P<path>[^@]+))?@", uses)
    assert match is not None
    path = match.group("path") or ""
    if path.startswith(".github/workflows/"):
        workflow = _yaml_fast.safe_load((REPO / path).read_text(encoding="utf-8"))
        on_block = workflow.get("on") or workflow.get(True) or {}
        call = on_block.get("workflow_call") or {}
        return set(call.get("inputs") or {})
    action = REPO / path / "action.yml" if path else REPO / "action.yml"
    if not action.is_file():
        return None
    declared = _yaml_fast.safe_load(action.read_text(encoding="utf-8")) or {}
    return set(declared.get("inputs") or {})


def _unknown_with_keys(path: Path) -> list[str]:
    """`with:` keys a release-pinned step passes that the pinned Action does
    not declare, plus any fenced YAML block that does not parse (its keys
    could not be checked at all)."""
    offenders = []
    text = path.read_text(encoding="utf-8")
    for block in re.findall(r"^```yaml\n(.*?)^```", text, re.DOTALL | re.MULTILINE):
        try:
            document = _yaml_fast.safe_load(block)
        except yaml.YAMLError as error:
            offenders.append(f"{_display(path)}: unparseable yaml block ({error})")
            continue
        for uses, with_block in _release_steps(document):
            inputs = _action_inputs(uses)
            if inputs is None:
                offenders.append(f"{_display(path)}: {uses} names no action.yml")
                continue
            for key in with_block:
                if key not in inputs:
                    offenders.append(
                        f"{_display(path)}: {uses} passes with: {key!r}, "
                        "which is not an input of that Action"
                    )
    return offenders


def _baseline_write_surfaces() -> list[Path]:
    paths = [
        p
        for p in SRC.rglob("*")
        if p.is_file() and p.suffix in {".md", ".yml", ".yaml"}
    ]
    paths += sorted((REPO / "docs" / "use").glob("*.md"))
    return sorted(paths)


def _overwrite_offenders(
    paths: Iterable[Path], allowlist: Mapping[str, str]
) -> list[str]:
    offenders = []
    for path in paths:
        if _display(path) in allowlist:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _OVERWRITE_RE.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{_display(path)}:{lineno}: {match.group(0).strip()!r}")
    return offenders


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_every_pinned_abicheck_release_is_inside_the_skill_version_range(
    skill_dir: Path,
):
    """A pin names the release the workflow will run, so it must satisfy the
    range its skill declares. A pin outside the range sends a user to a release
    the skill's own preflight then refuses."""
    raw = _front_matter(skill_dir / "SKILL.md")["metadata"]["abicheck-version-range"]
    assert _pins_outside_range(skill_dir, raw) == []


def test_pin_check_fires_on_the_range_defect_it_was_written_for():
    """Negative control on the real tree: the set-up skill pins v0.7.0 in its
    own workflows, so the pre-fix `<0.7.0` range must be reported."""
    set_up = next(d for d in SKILL_DIRS if d.name == "set-up-abi-compatibility-ci")
    offenders = _pins_outside_range(set_up, ">=0.6.0,<0.7.0")
    assert offenders and all("v0.7.0" in line for line in offenders)


def test_pin_check_reads_both_tag_pins_and_sha_comment_pins(tmp_path: Path):
    (tmp_path / "SKILL.md").write_text(
        "- uses: abicheck/abicheck@v0.7.0\n"
        "- uses: abicheck/abicheck@<sha>  # v0.8.0\n"
        "- uses: abicheck/abicheck@v0.6.0\n",
        encoding="utf-8",
    )
    offenders = _pins_outside_range(tmp_path, ">=0.6.0,<0.7.0")
    assert len(offenders) == 2
    assert "names 0.7.0" in offenders[0] and "names 0.8.0" in offenders[1]


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_declared_range_contains_the_newest_dated_changelog_release(skill_dir: Path):
    """The range must admit the newest dated release, and its exclusive upper
    bound must sit above it. Derived from the changelog's dated headings, not
    the working tree, so it cannot be calibrated to an unreleased version."""
    newest = _newest_dated_release((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    raw = _front_matter(skill_dir / "SKILL.md")["metadata"]["abicheck-version-range"]
    assert _in_range(newest, raw), (
        f"{skill_dir.name} declares {raw}, which excludes the newest dated "
        f"release {newest}"
    )


def test_changelog_check_fires_when_a_release_is_dated_past_the_range():
    dated = "## [0.7.0] — 2026-10-13\n\n## [0.6.0] — 2026-09-13\n"
    newest = _newest_dated_release(dated)
    assert newest == "0.7.0"
    assert not _in_range(newest, ">=0.6.0,<0.7.0")
    assert _in_range(newest, ">=0.6.0,<0.8.0")


def test_changelog_check_ignores_undated_headings():
    undated = "## [0.7.0] — not yet released\n\n## [0.6.0] — 2026-09-13\n"
    assert _newest_dated_release(undated) == "0.6.0"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_every_with_key_on_an_abicheck_step_is_an_input_of_the_action_it_calls(
    skill_dir: Path,
):
    offenders: list[str] = []
    for path in sorted(skill_dir.rglob("*.md")):
        offenders += _unknown_with_keys(path)
    assert offenders == []


def test_with_key_check_fires_on_an_undeclared_input(tmp_path: Path):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "```yaml\n"
        "- uses: abicheck/abicheck@v0.7.0\n"
        "  with:\n"
        "    abi-baseline: latest-release\n"
        "    not-an-input: x\n"
        "```\n",
        encoding="utf-8",
    )
    offenders = _unknown_with_keys(path)
    assert len(offenders) == 1 and "'not-an-input'" in offenders[0]


def test_with_key_check_fires_on_a_yaml_block_it_cannot_read(tmp_path: Path):
    path = tmp_path / "SKILL.md"
    path.write_text("```yaml\n- uses: [unclosed\n```\n", encoding="utf-8")
    offenders = _unknown_with_keys(path)
    assert len(offenders) == 1 and "unparseable yaml block" in offenders[0]


def test_no_skill_or_user_doc_teaches_overwriting_a_release_baseline():
    assert _overwrite_offenders(_baseline_write_surfaces(), OVERWRITE_ALLOWLIST) == []


def test_overwrite_check_fires_on_clobber_and_delete_and_honours_the_allowlist(
    tmp_path: Path,
):
    clobber = tmp_path / "abi-baseline.yml"
    clobber.write_text(
        '      - run: gh release upload "$TAG" libfoo.abicheck.json --clobber\n',
        encoding="utf-8",
    )
    delete = tmp_path / "republish.md"
    delete.write_text(
        "```bash\ngh release delete-asset v1 libfoo.abicheck.json --yes\n```\n",
        encoding="utf-8",
    )
    surfaces = [clobber, delete]
    offenders = _overwrite_offenders(surfaces, {})
    assert len(offenders) == 2
    assert "--clobber" in offenders[0] and "delete-asset" in offenders[1]
    allowed = {"abi-baseline.yml": "reason", "republish.md": "reason"}
    assert _overwrite_offenders(surfaces, allowed) == []
