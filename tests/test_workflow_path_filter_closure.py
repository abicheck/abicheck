# Copyright 2026 Nikolay Petrov
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

"""A `paths:` filter must cover the workflow's own build infrastructure.

Bug class: a path-filtered workflow names the *inputs* it validates and
omits the files that determine *how* it runs — the package metadata every
job pip-installs from, the composite actions it calls, and the scripts
those actions execute. A change to one of those then merges without a
single job that depends on it having run. The filter looks careful and is
silently incomplete, so nothing fails; the gap is only visible the next
time the skipped job would have caught something.

`ci.yml`'s `heavy-parity-gate` already names `pyproject.toml`,
`action/**`, `.github/actions/**` and `scripts/verify.py` for exactly this
reason, and its own comment states the sharpest case: "re-pinning CastXML
in action/install-castxml.sh ... would otherwise land without a single job
that uses CastXML having run." That fix was applied to one gate and not to
the three other path-filtered workflows with the same dependency, which is
what this test generalizes — the repository's own "fix the cause, not the
instance" rule applied to the filter itself.

The dependency set is *derived from each workflow*, never hard-coded: the
files its `run:` steps actually execute, the composite actions it calls,
the files *those* actions execute in turn, and `pyproject.toml` when it
installs the package. So a workflow that starts using a new script, or a
composite action that starts shelling out to a new one, is covered the
moment it does — not when someone remembers to extend a list here.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# A repo-relative script handed to an interpreter, or executed directly.
_EXECUTED_FILE = re.compile(
    r"(?:bash|sh|python3?|\./)\s+([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:sh|py))"
)
_LOCAL_ACTION = re.compile(r"uses:\s*\./(\.github/actions/[A-Za-z0-9_-]+)")


def _executed_files(text: str) -> set[str]:
    """Repo-relative files this YAML's `run:` steps execute. Filtered to
    paths that really exist, so a reference inside prose or a synthesized
    example command does not invent a dependency."""
    return {m for m in _EXECUTED_FILE.findall(text) if (REPO_ROOT / m).is_file()}


def _covers(globs: list[str], path: str) -> bool:
    """Whether any filter entry matches *path* under GitHub's path syntax.
    Deliberately permissive about `**` (treated as "anything"): this test
    should report a path that is genuinely uncovered, never argue about a
    glob that plausibly covers it."""
    for glob in globs:
        if path == glob or fnmatch.fnmatch(path, glob.replace("**", "*")):
            return True
        if glob.endswith("/**") and path.startswith(glob[:-3] + "/"):
            return True
    return False


def _required_paths(text: str) -> set[str]:
    required = _executed_files(text)
    if "pip install -e" in text:
        # Every such job installs *this* package; its dependency bounds,
        # entry points and optional extras all live in pyproject.toml.
        required.add("pyproject.toml")
    for action in _LOCAL_ACTION.findall(text):
        action_yml = REPO_ROOT / action / "action.yml"
        required.add(f"{action}/action.yml")
        if action_yml.is_file():
            required |= _executed_files(action_yml.read_text())
    return required


def _filtered_workflows() -> list[tuple[str, str, list[str], set[str]]]:
    cases = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text()
        doc = yaml.safe_load(text)
        # PyYAML parses the bare `on:` key as the boolean True.
        triggers = (doc or {}).get("on", (doc or {}).get(True)) or {}
        if not isinstance(triggers, dict):
            continue
        for event in ("push", "pull_request"):
            config = triggers.get(event)
            if isinstance(config, dict) and config.get("paths"):
                cases.append(
                    (path.name, event, list(config["paths"]), _required_paths(text))
                )
    return cases


_CASES = _filtered_workflows()


def test_the_survey_found_filtered_workflows() -> None:
    """Guards the test itself: a parsing change that matched nothing would
    make every assertion below vacuous."""
    assert len(_CASES) >= 4, [(c[0], c[1]) for c in _CASES]
    assert any(c[3] for c in _CASES), "no workflow resolved any dependency at all"


@pytest.mark.parametrize(
    "name,event,globs,required", _CASES, ids=[f"{c[0]}-{c[1]}" for c in _CASES]
)
def test_path_filter_covers_the_workflow_own_infrastructure(
    name: str, event: str, globs: list[str], required: set[str]
) -> None:
    missing = sorted(p for p in required if not _covers(globs, p))
    assert not missing, (
        f"{name} [{event}]: the jobs depend on these files but the paths "
        f"filter does not name them, so changing one merges without this "
        f"workflow running: {missing}"
    )


@pytest.mark.parametrize(
    "name,event,globs,required", _CASES, ids=[f"{c[0]}-{c[1]}" for c in _CASES]
)
def test_the_matcher_can_actually_fail(
    name: str, event: str, globs: list[str], required: set[str]
) -> None:
    """Negative control for `_covers`. Without it a matcher that returned
    True unconditionally — the easy way to "fix" a failure above — would
    make the whole module pass while checking nothing."""
    assert not _covers(globs, "definitely/not/covered/by/any/filter.zzz")
