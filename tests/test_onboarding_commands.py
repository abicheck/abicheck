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

"""A copy-pasteable tutorial command may only name artifacts the same
tutorial builds.

Bug class: a documented command referencing a build artifact its own
document never produces. Renaming `libv1.so`/`libv2.so` to
`libmathutils_v1.so`/`libmathutils_v2.so` in `docs/start/first-check.md`'s
build steps left the no-CastXML fallback further down still pointing at the
old names, so following the documented alternative path failed on a missing
file. Nothing caught it: `run_workflow_examples.py`'s README-drift check
covers `examples/workflows/*/README.md`, and this page is a *second copy* of
those commands living outside that guard -- which is precisely the shape the
drift check exists to prevent, one directory over.

The invariant below is the general one: within the runnable tutorial section
of each onboarding page, every local shared-library operand must be produced
by a `-o` in that same section. It catches any future rename, not just this
one. It deliberately stops at the "For your own library" boundary, where the
page switches from a runnable walkthrough to `libfoo.so.1`-style
placeholders a reader is meant to substitute.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))

import workflow_examples  # noqa: E402

#: (page, marker at which the runnable walkthrough ends).
ONBOARDING_PAGES = [
    (REPO_DIR / "docs" / "start" / "first-check.md", "For your own library:"),
    (
        REPO_DIR / "examples" / "workflows" / "compare-release" / "README.md",
        "## Next steps",
    ),
]

#: A local artifact: `libfoo.so`, `libfoo.so.1`, no directory component.
_LOCAL_LIB_RE = re.compile(r"^lib[\w.+-]*\.so(?:\.\d+)*$")


def _runnable_section(text: str, end_marker: str) -> str:
    index = text.find(end_marker)
    return text if index == -1 else text[:index]


def _commands(section: str) -> list[str]:
    """Delegates to `workflow_examples.documented_commands`.

    Deliberately not a second parser. This file originally carried its own
    copy, which is the same "second copy free to drift" smell these tests
    exist to catch -- and the two would have had to be fixed in lockstep
    when the shared one's fence regex was corrected.
    """
    return workflow_examples.documented_commands(section)


def _built_and_used(commands: list[str]) -> tuple[set[str], set[str]]:
    built: set[str] = set()
    used: set[str] = set()
    for command in commands:
        try:
            argv = shlex.split(command)
        except ValueError:  # pragma: no cover - a malformed doc command
            continue
        for index, token in enumerate(argv):
            if token == "-o" and index + 1 < len(argv):
                candidate = argv[index + 1]
                if _LOCAL_LIB_RE.match(candidate):
                    built.add(candidate)
            elif _LOCAL_LIB_RE.match(token):
                used.add(token)
    return built, used


PAGE_IDS = [page.name for page, _ in ONBOARDING_PAGES]


@pytest.mark.parametrize(("page", "end_marker"), ONBOARDING_PAGES, ids=PAGE_IDS)
def test_every_library_a_tutorial_command_names_is_one_it_builds(
    page: Path, end_marker: str
):
    section = _runnable_section(page.read_text(encoding="utf-8"), end_marker)
    commands = _commands(section)
    assert commands, f"{page}: found no shell commands to check"
    built, used = _built_and_used(commands)
    assert built, f"{page}: the walkthrough builds no library"
    dangling = sorted(used - built)
    assert not dangling, (
        f"{page}: command(s) reference {dangling}, which the page's own build "
        f"steps never produce (they build {sorted(built)}). A reader "
        "copy-pasting this hits a missing file."
    )


@pytest.mark.parametrize(("page", "end_marker"), ONBOARDING_PAGES, ids=PAGE_IDS)
def test_the_check_detects_a_renamed_build_output(page: Path, end_marker: str):
    """Adversarially falsify: rename each built artifact in turn and confirm
    the check reports the now-dangling references. Asserting only the clean
    state would pass just as happily against a check that parses nothing."""
    section = _runnable_section(page.read_text(encoding="utf-8"), end_marker)
    built, _ = _built_and_used(_commands(section))
    for artifact in sorted(built):
        renamed = section.replace(f"-o {artifact}", f"-o {artifact}.renamed")
        new_built, new_used = _built_and_used(_commands(renamed))
        assert artifact in new_used - new_built, (
            f"renaming {artifact}'s build output produced no dangling "
            "reference, so the check above proves nothing"
        )


def test_the_first_check_page_and_the_workflow_agree_on_the_build_commands():
    """The page is a second copy of the workflow's own commands. It may show
    fewer of them, but it must not show a *different* one -- that is the
    drift `workflow_examples.readme_drift` prevents inside the workflow
    directory and cannot see from here.

    Whole normalized command lines, never a substring of the page: a
    substring test passes when the page *extends* a command (the page shows
    `... --contract public`, the manifest still runs the shorter form), which
    is exactly the hole this PR closed in `readme_drift` and then
    reintroduced here at the second synchronization boundary.
    """
    workflow = workflow_examples.load(
        workflow_examples.WORKFLOWS_DIR / "compare-release"
    )
    page = REPO_DIR / "docs" / "start" / "first-check.md"
    documented = _commands(
        _runnable_section(page.read_text(encoding="utf-8"), "For your own library:")
    )
    for step in workflow.steps:
        assert workflow_examples.normalize_command(step.run) in documented, (
            f"first-check.md does not show the workflow's {step.name!r} command "
            f"verbatim; the two would drift silently. Page shows: {documented}"
        )
