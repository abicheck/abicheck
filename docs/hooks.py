"""mkdocs build hooks for abicheck's published documentation.

`on_config` stamps `config.extra.latest_published_version` from
`pyproject.toml`'s own `version` field — via the same regex approach
`scripts/gen_repo_facts.py` already uses (`tomllib` is stdlib only from
Python 3.11, and this repo's floor is 3.10, so the CI docs-build lane
can't rely on it) — so the site-wide "unreleased docs" announcement bar
(`docs/overrides/main.html`'s `announce` block) never drifts from what's
actually checked out.

This closes the P0.1 finding from a documentation review: GitHub Pages
(`.github/workflows/pages.yml`) deploys on every push to `main`, with no
separate build for a tagged release, so the public site can otherwise
describe unreleased `main`-only features (Agent Skills, `--contract`
domains, ...) with nothing telling the reader they aren't looking at the
latest published release. A full stable/dev channel split (separate URLs,
per-channel Action-pin policy) is a larger infra project left for a
follow-up; this banner is the minimal, always-accurate mitigation: it
never needs hand-editing, since it reads the version straight from the
tree being built, the same tree `pip install .`/`pyproject.toml` itself
would report.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Scoped to the `[project]` table specifically, mirroring
#: `scripts/gen_repo_facts.py`'s own `_PROJECT_VERSION_RE`/table-scoping —
#: a stray `version = "..."` in some other table (e.g. a build-backend
#: config) must never false-match.
_PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_PROJECT_TABLE_RE = re.compile(r"^\[project\]\s*$(.*?)^\[", re.MULTILINE | re.DOTALL)


def _project_version(repo_root: Path) -> str:
    """`pyproject.toml`'s `project.version`, read directly from the tree
    being built -- never from a periodically-regenerated cache like
    `repo_facts.json`, so this can't itself go stale between regenerations.
    """
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return "unknown"
    text = pyproject.read_text(encoding="utf-8")
    # Append a synthetic trailing "[" so a `[project]` table that happens to
    # be the last table in the file (no following `[...]` header) still
    # matches the non-greedy `(.*?)^\[` body capture.
    table_match = _PROJECT_TABLE_RE.search(text + "\n[")
    searched = table_match.group(1) if table_match else text
    version_match = _PROJECT_VERSION_RE.search(searched)
    return version_match.group(1) if version_match else "unknown"


def on_config(config: Any, **kwargs: Any) -> Any:
    config_file_path = config.get("config_file_path")
    repo_root = Path(config_file_path).resolve().parent if config_file_path else Path(".")
    config["extra"]["latest_published_version"] = _project_version(repo_root)
    return config
