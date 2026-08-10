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

"""`scripts/gen_agent_skills.py` (ADR-058 / G36 P0.3).

Two halves: the generator's own mechanics exercised against synthetic skill
trees (fragment resolution, transitivity, link rewriting, orphan detection),
and assertions over the three real committed publication trees — which must
reproduce from `skills-src/` and must each be self-contained.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "gen_agent_skills", REPO / "scripts" / "gen_agent_skills.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def _links(text: str) -> list[str]:
    return _LINK_RE.findall(text)


# ---------------------------------------------------------------------------
# Synthetic-tree mechanics
# ---------------------------------------------------------------------------


def _make_src(
    tmp_path: Path, skills: dict[str, dict[str, str]], shared: dict[str, str]
):
    """Build a synthetic `skills-src/` and point the module's globals at it."""
    src = tmp_path / "skills-src"
    shared_dir = src / "shared"
    shared_dir.mkdir(parents=True)
    for name, body in shared.items():
        (shared_dir / name).write_text(body, encoding="utf-8")
    for skill, files in skills.items():
        for rel, body in files.items():
            path = src / skill / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    return src, shared_dir


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    def build(skills, shared):
        src, shared_dir = _make_src(tmp_path, skills, shared)
        monkeypatch.setattr(gen, "SRC_DIR", src)
        monkeypatch.setattr(gen, "SHARED_DIR", shared_dir)
        monkeypatch.setattr(gen, "REPO_DIR", tmp_path)
        return src

    return build


def test_fragment_cited_only_from_a_reference_file_is_still_copied(synthetic):
    """The indirect-reference gap: a skill-specific `references/*.md` file —
    not `SKILL.md` — is the only citer of a fragment. Scanning `SKILL.md`
    alone would silently ship an incomplete fragment set."""
    synthetic(
        skills={
            "demo": {
                "SKILL.md": "---\nname: demo\n---\n\n# Demo\n\nNo shared links here.\n",
                "references/extra.md": "See [frag](../../shared/only-here.md).\n",
            }
        },
        shared={"only-here.md": "# Only here\n"},
    )
    rendered = gen.render_all(gen.SRC_DIR)
    assert "demo/references/shared/only-here.md" in rendered
    # ...and the citing file's link points at the copied location.
    assert _links(rendered["demo/references/extra.md"]) == ["shared/only-here.md"]


def test_transitive_fragment_reference_is_resolved_to_a_fixed_point(synthetic):
    synthetic(
        skills={
            "demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"},
        },
        shared={
            "a.md": "# A\n\n[b](b.md)\n",
            "b.md": "# B\n\n[c](c.md)\n",
            "c.md": "# C\n",
        },
    )
    rendered = gen.render_all(gen.SRC_DIR)
    for name in ("a", "b", "c"):
        assert f"demo/references/shared/{name}.md" in rendered
    # A fragment-to-fragment link is flat inside references/shared/.
    assert _links(rendered["demo/references/shared/a.md"]) == ["b.md"]


def test_missing_shared_fragment_is_a_hard_error(synthetic):
    synthetic(
        skills={
            "demo": {"SKILL.md": "---\nname: demo\n---\n\n[x](../shared/gone.md)\n"}
        },
        shared={"kept.md": "# Kept\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="gone.md"):
        gen.render_all(gen.SRC_DIR)


def test_reference_style_link_definition_is_a_hard_error(synthetic):
    """The link rewrite only sees inline `[text](target)`, so a reference-style
    definition would be published verbatim — leaving an installed skill
    pointing at a repo path that does not exist there. Generation must refuse
    it rather than pass it through."""
    synthetic(
        skills={
            "demo": {
                "SKILL.md": (
                    "---\nname: demo\n---\n\nSee [guide][docs] and "
                    "[a](../shared/a.md).\n\n[docs]: ../../docs/use/cli-usage.md\n"
                )
            }
        },
        shared={"a.md": "# A\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="reference-style"):
        gen.render_all(gen.SRC_DIR)


@pytest.mark.parametrize(
    "destination",
    ["<../../docs/use/a b.md>", "<../../docs/use/cli-usage.md>"],
    ids=["with-space", "without-space"],
)
def test_angle_bracket_link_destination_is_a_hard_error(synthetic, destination):
    """Third syntax `_MD_LINK_RE` cannot rewrite, and it fails two ways: a
    whitespace-bearing target is skipped by the `[^)\\s]+` destination class
    and published verbatim, while a whitespace-free one matches with the angle
    brackets stuck to the path. Both leave a repo-relative target behind."""
    synthetic(
        skills={
            "demo": {
                "SKILL.md": (
                    f"---\nname: demo\n---\n\n[guide]({destination})\n\n"
                    "[a](../shared/a.md)\n"
                )
            }
        },
        shared={"a.md": "# A\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="angle-bracket"):
        gen.render_all(gen.SRC_DIR)


def test_markdown_image_is_a_hard_error(synthetic):
    """Same class as the reference-definition case: `_MD_LINK_RE`'s negative
    lookbehind skips images, and the generator copies only Markdown — so an
    image would ship a repo-relative path to an asset the installed skill
    directory does not contain."""
    synthetic(
        skills={
            "demo": {
                "SKILL.md": (
                    "---\nname: demo\n---\n\n![diagram](../../docs/img/x.png)\n\n"
                    "[a](../shared/a.md)\n"
                )
            }
        },
        shared={"a.md": "# A\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="image"):
        gen.render_all(gen.SRC_DIR)


def test_footnote_definition_is_not_mistaken_for_a_reference_link(synthetic):
    """`[^1]:` is a footnote, not a link definition — the rewrite has nothing
    to do with it, so it must not trip the guard above."""
    synthetic(
        skills={
            "demo": {
                "SKILL.md": (
                    "---\nname: demo\n---\n\nText[^1] and [a](../shared/a.md).\n\n"
                    "[^1]: the note\n"
                )
            }
        },
        shared={"a.md": "# A\n"},
    )
    assert "demo/SKILL.md" in gen.render_all(gen.SRC_DIR)


def test_orphaned_shared_fragment_is_a_hard_error(synthetic):
    synthetic(
        skills={"demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"}},
        shared={"a.md": "# A\n", "nobody-cites-me.md": "# Orphan\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="nobody-cites-me.md"):
        gen.render_all(gen.SRC_DIR)


def test_link_escaping_the_skill_directory_is_a_hard_error(synthetic):
    synthetic(
        skills={
            "demo": {
                "SKILL.md": (
                    "---\nname: demo\n---\n\n[a](../shared/a.md)\n"
                    "[other](../other-skill/SKILL.md)\n"
                )
            },
            "other-skill": {
                "SKILL.md": "---\nname: other\n---\n\n[a](../shared/a.md)\n"
            },
        },
        shared={"a.md": "# A\n"},
    )
    with pytest.raises(gen.SkillGenerationError, match="escapes the skill"):
        gen.render_all(gen.SRC_DIR)


def test_generation_is_idempotent(synthetic, tmp_path):
    synthetic(
        skills={"demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"}},
        shared={"a.md": "# A\n"},
    )
    first = gen.render_all(gen.SRC_DIR)
    second = gen.render_all(gen.SRC_DIR)
    assert first == second

    roots = (tmp_path / "out1", tmp_path / "out2")
    gen.write_trees(first, roots=roots)
    assert gen.check_trees(first, roots=roots) == []
    gen.write_trees(gen.render_all(gen.SRC_DIR), roots=roots)
    assert gen.check_trees(first, roots=roots) == []


def test_write_trees_leaves_unrelated_skills_in_an_output_root_alone(
    synthetic, tmp_path
):
    """`.claude/skills/` also holds this repository's own hand-authored Claude
    Code skills. The generator owns only the directories it emits — wiping the
    whole root would silently delete them."""
    synthetic(
        skills={"demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"}},
        shared={"a.md": "# A\n"},
    )
    root = tmp_path / "out"
    foreign = root / "hand-authored-skill" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("---\nname: hand-authored-skill\n---\n", encoding="utf-8")

    rendered = gen.render_all(gen.SRC_DIR)
    gen.write_trees(rendered, roots=(root,))

    assert foreign.is_file(), "an unrelated skill under the output root was deleted"
    assert (root / "demo" / "SKILL.md").is_file()
    # ...and it is not reported as stale output either.
    assert gen.check_trees(rendered, roots=(root,)) == []


def test_check_trees_reports_missing_stale_and_outdated(synthetic, tmp_path):
    synthetic(
        skills={"demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"}},
        shared={"a.md": "# A\n"},
    )
    rendered = gen.render_all(gen.SRC_DIR)
    root = tmp_path / "out"
    gen.write_trees(rendered, roots=(root,))

    (root / "demo" / "SKILL.md").write_text("tampered\n", encoding="utf-8")
    (root / "demo" / "leftover.md").write_text("stale\n", encoding="utf-8")
    (root / "demo" / "references" / "shared" / "a.md").unlink()

    problems = " ".join(gen.check_trees(rendered, roots=(root,)))
    assert "out of date" in problems
    assert "stale" in problems
    assert "missing" in problems


# ---------------------------------------------------------------------------
# Published-docs URL mapping (derived from mkdocs.yml, never hardcoded)
# ---------------------------------------------------------------------------


def test_published_docs_url_uses_mkdocs_site_url_and_directory_urls():
    site_url = gen._read_mkdocs_scalar("site_url")
    assert site_url, "mkdocs.yml must declare site_url"
    raw = gen._read_mkdocs_scalar("use_directory_urls")
    directory_urls = True if raw is None else raw.lower() == "true"

    url = gen.published_docs_url("learn/evidence-and-detectability.md")
    assert url.startswith(site_url.rstrip("/") + "/")
    if directory_urls:
        assert url.endswith("/learn/evidence-and-detectability/")
    else:
        assert url.endswith("/learn/evidence-and-detectability.html")


def test_published_docs_url_passes_anchors_through(monkeypatch):
    """A multi-segment path plus an anchor: mkdocs drops the extension and
    adds a trailing slash under directory URLs, and never rewrites the
    anchor text."""
    monkeypatch.setattr(
        gen,
        "_read_mkdocs_scalar",
        lambda key: {
            "site_url": "https://example.test/proj",
            "use_directory_urls": None,
        }[key],
    )
    assert (
        gen.published_docs_url("reference/schemas/compare.md", "#a-heading")
        == "https://example.test/proj/reference/schemas/compare/#a-heading"
    )


def test_published_docs_url_honours_use_directory_urls_false(monkeypatch):
    monkeypatch.setattr(
        gen,
        "_read_mkdocs_scalar",
        lambda key: {
            "site_url": "https://example.test/proj",
            "use_directory_urls": "false",
        }[key],
    )
    assert (
        gen.published_docs_url("learn/foo.md", "#x")
        == "https://example.test/proj/learn/foo.html#x"
    )


# ---------------------------------------------------------------------------
# The real committed trees
# ---------------------------------------------------------------------------


def test_committed_trees_match_skills_src():
    assert gen.check_trees(gen.render_all()) == [], (
        "generated agent-skill trees are stale — run "
        "`python scripts/gen_agent_skills.py` and commit the result"
    )


#: The skill directories this generator owns — an output root may also hold
#: unrelated, hand-authored skills, which none of these assertions apply to.
OWNED = sorted(p.name for p in gen.discover_skills())


def _generated_files(root):
    return [
        p for name in OWNED for p in sorted((root / name).rglob("*")) if p.is_file()
    ]


def test_all_three_trees_are_byte_identical():
    """`.claude/` and `.gemini/` are copies, not a second emission path — so
    every assertion proven for `.agents/skills/` holds for them too."""
    trees = [
        {p.relative_to(root).as_posix(): p.read_bytes() for p in _generated_files(root)}
        for root in gen.OUTPUT_ROOTS
    ]
    assert all(t == trees[0] for t in trees[1:])
    assert trees[0], "the authoritative tree must not be empty"


@pytest.mark.parametrize("root", gen.OUTPUT_ROOTS, ids=lambda p: str(p))
def test_no_symlinks_in_generated_output(root):
    assert not [p for name in OWNED for p in (root / name).rglob("*") if p.is_symlink()]


@pytest.mark.parametrize("root", gen.OUTPUT_ROOTS, ids=lambda p: str(p))
def test_every_generated_link_resolves_inside_its_own_skill(root):
    """The self-containment invariant, checked mechanically over every
    generated file — `SKILL.md`, skill-specific references, and copied shared
    fragments alike — not only over each skill's top-level `SKILL.md`."""
    offenders: list[str] = []
    for path in [p for p in _generated_files(root) if p.suffix == ".md"]:
        skill_root = path
        while skill_root.parent != root:
            skill_root = skill_root.parent
        for target in _links(path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            bare = target.split("#", 1)[0]
            if not bare:
                continue
            resolved = (path.parent / bare).resolve()
            try:
                resolved.relative_to(skill_root.resolve())
            except ValueError:
                offenders.append(f"{path}: {target} escapes {skill_root.name}")
                continue
            if not resolved.is_file():
                offenders.append(f"{path}: {target} does not resolve")
    assert offenders == []


@pytest.mark.parametrize("root", gen.OUTPUT_ROOTS, ids=lambda p: str(p))
def test_every_generated_markdown_file_carries_the_ownership_marker(root):
    missing = [
        str(p)
        for p in _generated_files(root)
        if p.suffix == ".md"
        and gen.GENERATED_MARKER_SUBSTRING not in p.read_text(encoding="utf-8").lower()
    ]
    assert missing == []


def test_every_shared_fragment_is_cited_by_at_least_one_skill():
    """`render_all` raises on an orphan; this asserts the real tree has none,
    so the invariant is proven against committed content, not only against a
    synthetic fixture."""
    gen.render_all()  # raises SkillGenerationError on an orphaned fragment
    assert sorted(p.name for p in gen.SHARED_DIR.glob("*.md"))


# ---------------------------------------------------------------------------
# Review follow-ups (PR #686)
# ---------------------------------------------------------------------------


def test_a_removed_or_renamed_skill_is_detected_and_removed(synthetic, tmp_path):
    """A skill deleted or renamed in `skills-src/` must not stay published.

    Ownership is read off the emitted marker rather than the current render:
    a render-derived ownership set stops considering the stale directory
    owned the moment its name disappears from the source, which left it
    published in every tree while `--check` still reported "in sync".
    """
    synthetic(
        skills={
            "demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"},
            "going-away": {
                "SKILL.md": "---\nname: going-away\n---\n\n[a](../shared/a.md)\n"
            },
        },
        shared={"a.md": "# A\n"},
    )
    root = tmp_path / "out"
    gen.write_trees(gen.render_all(gen.SRC_DIR), roots=(root,))
    assert (root / "going-away" / "SKILL.md").is_file()

    # The skill is removed from the source tree.
    shutil.rmtree(gen.SRC_DIR / "going-away")
    rendered = gen.render_all(gen.SRC_DIR)

    problems = " ".join(gen.check_trees(rendered, roots=(root,)))
    assert "going-away" in problems and "stale" in problems, (
        "a removed skill left published was not reported as stale"
    )

    gen.write_trees(rendered, roots=(root,))
    assert not (root / "going-away").exists(), "the stale skill was not removed"
    assert (root / "demo" / "SKILL.md").is_file()
    assert gen.check_trees(rendered, roots=(root,)) == []


def test_stale_detection_still_leaves_hand_authored_skills_alone(synthetic, tmp_path):
    """The counterpart to the test above: a directory with no generated marker
    is not generator output, so stale-detection must not claim it."""
    synthetic(
        skills={"demo": {"SKILL.md": "---\nname: demo\n---\n\n[a](../shared/a.md)\n"}},
        shared={"a.md": "# A\n"},
    )
    root = tmp_path / "out"
    foreign = root / "hand-authored" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("---\nname: hand-authored\n---\n\n# Mine\n", encoding="utf-8")

    rendered = gen.render_all(gen.SRC_DIR)
    assert gen.check_trees(rendered, roots=(root,)) != []  # nothing written yet
    gen.write_trees(rendered, roots=(root,))

    assert foreign.is_file()
    assert gen.check_trees(rendered, roots=(root,)) == []


def test_render_all_resolves_fragments_against_its_own_src_dir(tmp_path, monkeypatch):
    """`render_all(src_dir)` must resolve shared fragments — and orphan-check —
    against that tree's own `shared/`, not whatever the module globals point
    at, or an alternate source tree silently mixes with the repository's own.
    """
    other = tmp_path / "other-src"
    (other / "shared").mkdir(parents=True)
    (other / "shared" / "own.md").write_text("# Own\n", encoding="utf-8")
    (other / "demo").mkdir()
    (other / "demo" / "SKILL.md").write_text(
        "---\nname: demo\n---\n\n[own](../shared/own.md)\n", encoding="utf-8"
    )
    # Module globals deliberately left pointing at the real repository tree.
    monkeypatch.setattr(gen, "REPO_DIR", tmp_path)

    rendered = gen.render_all(other)
    assert set(rendered) == {"demo/SKILL.md", "demo/references/shared/own.md"}
    # None of the repository's own fragments leaked in, and its own fragments
    # did not trip this render's orphan check either.
    assert not [rel for rel in rendered if "safety-invariants" in rel]


def test_missing_site_url_is_a_hard_error_not_a_root_relative_link(
    synthetic, tmp_path, monkeypatch
):
    """An empty `site_url` would silently yield `/learn/foo/` — a root-relative
    link that resolves to nothing from an installed skill, contradicting this
    generator's own no-dangling-references guarantee."""
    docs = tmp_path / "docs" / "learn"
    docs.mkdir(parents=True)
    (docs / "foo.md").write_text("# Foo\n", encoding="utf-8")
    synthetic(
        skills={
            "demo": {
                "SKILL.md": "---\nname: demo\n---\n\n[foo](../../docs/learn/foo.md)\n"
            }
        },
        shared={},
    )
    monkeypatch.setattr(gen, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(gen, "MKDOCS_YML", tmp_path / "no-such-mkdocs.yml")

    with pytest.raises(gen.SkillGenerationError, match="site_url"):
        gen.render_all(gen.SRC_DIR)
