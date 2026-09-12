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

import re

import pytest
import yaml
from _workflow_files import REPO_ROOT, read_repo_text, workflow_paths

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


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate one GitHub path-filter glob to a regex.

    GitHub's `*` matches within a single path segment and `**` crosses `/`.
    `fnmatch` does not make that distinction -- its `*` matches `/` too --
    so `scripts/*/check.py` would "cover" `scripts/a/b/check.py`, which
    GitHub would not schedule. That error points the wrong way for a guard:
    it reports a genuinely uncovered dependency as covered, which is the
    finding this module exists to surface (CodeRabbit review, with a
    concrete counterexample).
    """
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _covers(globs: list[str], path: str) -> bool:
    """Whether *path* is selected by this ordered filter list.

    Follows GitHub's two documented rules: patterns are evaluated in order,
    and a `!`-prefixed pattern matching after a positive one excludes the
    path (a later positive re-includes it). The previous version ignored
    negation entirely and would have read an exclusion as an inclusion.
    """
    selected = False
    for glob in globs:
        negated = glob.startswith("!")
        pattern = glob[1:] if negated else glob
        if path == pattern or _glob_to_regex(pattern).match(path):
            selected = not negated
        elif pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"):
            selected = not negated
    return selected


def _required_paths(text: str) -> set[str]:
    """Files a workflow's own jobs execute, so its paths filter must name
    them: scripts it runs, `pyproject.toml` where it installs this
    package, and the `action.yml` of each local action it uses."""
    required = _executed_files(text)
    if "pip install -e" in text:
        # Every such job installs *this* package; its dependency bounds,
        # entry points and optional extras all live in pyproject.toml.
        required.add("pyproject.toml")
    # To a fixed point, because a local action may itself use another one:
    # scanning only the direct manifests would leave a nested action, and
    # every file *it* executes, outside the filter (CodeRabbit review). No
    # such nesting exists in this repository today -- which is exactly why
    # the traversal has to be written now rather than when the first one
    # appears and silently is not covered. The visited set makes a cycle
    # terminate instead of recursing forever.
    pending, visited = list(_LOCAL_ACTION.findall(text)), set()
    while pending:
        action = pending.pop()
        if action in visited:
            continue
        visited.add(action)
        required.add(f"{action}/action.yml")
        action_yml = REPO_ROOT / action / "action.yml"
        if not action_yml.is_file():
            continue
        action_text = read_repo_text(action_yml)
        required |= _executed_files(action_text)
        pending += _LOCAL_ACTION.findall(action_text)
    return required


def _filtered_workflows() -> list[tuple[str, str, list[str], set[str]]]:
    """One case per (workflow, event) that restricts itself with `paths`,
    carrying the declared globs and the paths it actually depends on."""
    cases = []
    for path in workflow_paths():
        text = read_repo_text(path)
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
    """A workflow that skips itself when its own machinery changes is a
    gate that silently stops gating."""
    missing = sorted(p for p in required if not _covers(globs, p))
    assert not missing, (
        f"{name} [{event}]: the jobs depend on these files but the paths "
        f"filter does not name them, so changing one merges without this "
        f"workflow running: {missing}"
    )


@pytest.mark.parametrize(
    "globs,path,expected",
    [
        # A single `*` stays inside one segment. `fnmatch` got this wrong
        # and reported the third case as covered (CodeRabbit review).
        ((["scripts/*/check.py"], "scripts/a/check.py", True)),
        ((["scripts/*/check.py"], "scripts/a/b/check.py", False)),
        ((["scripts/**/check.py"], "scripts/a/b/check.py", True)),
        ((["abicheck/**"], "abicheck/deep/nested/mod.py", True)),
        ((["pyproject.toml"], "pyproject.toml", True)),
        ((["pyproject.toml"], "sub/pyproject.toml", False)),
        ((["*.py"], "a/b.py", False)),
        ((["**/*.py"], "a/b.py", True)),
        # Ordered negation: a `!` after a positive excludes, a later
        # positive re-includes. Ignored entirely by the old helper.
        ((["sub/**", "!sub/docs/**"], "sub/src/x.py", True)),
        ((["sub/**", "!sub/docs/**"], "sub/docs/readme.md", False)),
        ((["sub/**", "!sub/docs/**", "sub/docs/keep.md"], "sub/docs/keep.md", True)),
        ((["!sub/docs/**", "sub/**"], "sub/docs/readme.md", True)),
    ],
)
def test_matcher_follows_github_path_filter_semantics(
    globs: list[str], path: str, expected: bool
) -> None:
    """The matcher's own contract, tested directly rather than only through
    the workflows that happen to exist today — none of which currently uses
    a `!` pattern or a single-`*` segment wildcard, so the repo-wide scan
    alone would never exercise either rule."""
    assert _covers(globs, path) is expected


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
