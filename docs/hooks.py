"""mkdocs build hooks for abicheck's published documentation.

`on_config` stamps `config.extra.latest_published_version` from
`repo_facts.json`'s `latest_release` field — the field this repo already
maintains specifically to distinguish "the version tag actually cut and
published" from `project_version` (`pyproject.toml`'s own `version`, which
CLAUDE.md's own "Two different Python version numbers matter here" pattern
already warns can be bumped ahead of a release). Reading `project_version`
here instead would be wrong the moment a routine pre-release version bump
lands before the release itself is cut — the banner would claim a version
that hasn't shipped yet is "the latest published release" (Codex review,
fresh evidence). `repo_facts.json` is the fact owner for exactly this
distinction (CLAUDE.md "M1-4"; `scripts/gen_repo_facts.py`'s own
`"latest_release": existing.get("latest_release", _project_version())`
line is what keeps it decoupled from `project_version` across regenerations).

This closes the P0.1 finding from a documentation review: GitHub Pages
(`.github/workflows/pages.yml`) deploys on every push to `main`, with no
separate build for a tagged release, so the public site can otherwise
describe unreleased `main`-only features (Agent Skills, `--contract`
domains, ...) with nothing telling the reader they aren't looking at the
latest published release. A full stable/dev channel split (separate URLs,
per-channel Action-pin policy) is a larger infra project left for a
follow-up; this banner is the minimal mitigation, reading the one field
this repo already keeps accurate for release cuts rather than a second,
hand-maintained copy of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _latest_published_version(repo_root: Path) -> str:
    """``repo_facts.json``'s ``latest_release`` -- the maintained fact owner
    for "the version actually published", not ``project_version`` (which
    tracks the in-tree ``pyproject.toml`` version and can be bumped ahead
    of the release that makes it real).
    """
    facts_path = repo_root / "repo_facts.json"
    if not facts_path.is_file():
        return "unknown"
    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    value = facts.get("latest_release")
    return str(value) if value else "unknown"


#: Cached by `on_config`, read by `on_page_markdown` -- so a page can embed
#: the same fact the announcement bar uses (e.g. `docs/use/agent-skills.md`'s
#: "latest published release" callout) via `{{ latest_published_version }}`
#: instead of a second, hand-typed copy that goes stale independently.
_LATEST_PUBLISHED_VERSION = "unknown"


def on_config(config: Any, **kwargs: Any) -> Any:
    global _LATEST_PUBLISHED_VERSION
    config_file_path = config.get("config_file_path")
    repo_root = Path(config_file_path).resolve().parent if config_file_path else Path(".")
    _LATEST_PUBLISHED_VERSION = _latest_published_version(repo_root)
    config["extra"]["latest_published_version"] = _LATEST_PUBLISHED_VERSION
    return config


def on_page_markdown(markdown: str, **kwargs: Any) -> str:
    """Substitute `{{ latest_published_version }}` in page Markdown.

    Deliberately a plain string replace, not full Jinja templating over
    every page's content -- narrow and predictable, and the only
    placeholder this repo's pages currently need.
    """
    return markdown.replace("{{ latest_published_version }}", _LATEST_PUBLISHED_VERSION)
