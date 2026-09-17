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

"""Every documented Action step must name inputs and outputs that exist.

**Bug class:** ``docs.example_resolves_against_nothing``. A documented YAML
step is not prose — it is the thing a reader copies into their own workflow
and then discovers, on a runner, that an input is ignored because it was
renamed, or that a step output is silently empty because the Action never
declared it. Both fail *quietly*: GitHub does not error on an unknown `with:`
key, and an unresolved `${{ steps.x.outputs.y }}` expands to the empty
string. The reader's workflow runs, and does the wrong thing.

The same sweep also catches the *other* direction of the same drift, which is
what motivated it here: the fork-PR guide documented a
sidecar-plus-second-verification sequence for reporting an analysed merge
commit, and kept documenting it after `provenance-from` made it unnecessary.
That one is prose and no scan can judge it — but an example that quietly stops
resolving is the mechanical half of the same problem, and it is worth
catching mechanically.

Deliberately a **sweep over every documented step**, not a check of the ones
this pass happened to add: a rule that only covers the newest examples is the
rule that lets the next rename through. Third-party steps (`actions/checkout`
and friends) are out of scope — this repository owns no contract for them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

#: `abicheck/abicheck/actions/<name>@<ref>` -> `actions/<name>/action.yml`,
#: plus the root composite Action, which is referenced as the bare repo.
_ACTION_REF = re.compile(r"^abicheck/abicheck(?:/(?P<path>actions/[a-z-]+))?@[^\s]+$")

#: A fenced YAML block. Documented steps appear inside these and nowhere else.
_FENCE = re.compile(r"```ya?ml\n(.*?)\n```", re.DOTALL)

#: Expressions of the form `steps.<id>.outputs.<name>`.
_STEP_OUTPUT = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")


def _action_documents() -> dict[str, dict[str, Any]]:
    """Every first-party Action, keyed by the path a doc would reference."""
    documents: dict[str, dict[str, Any]] = {}
    root = REPO_ROOT / "action.yml"
    if root.is_file():
        documents[""] = yaml.safe_load(root.read_text(encoding="utf-8"))
    for action_yml in sorted((REPO_ROOT / "actions").glob("*/action.yml")):
        key = f"actions/{action_yml.parent.name}"
        documents[key] = yaml.safe_load(action_yml.read_text(encoding="utf-8"))
    return documents


ACTIONS = _action_documents()


def _steps(node: Any) -> list[dict[str, Any]]:
    """Every mapping that looks like a step, anywhere in a parsed block.

    A documentation snippet is rarely a whole workflow: it may be a bare
    list of steps, a `jobs:` fragment, or a single step. Walking for the
    shape rather than for a fixed path is what lets one rule cover all of
    them without each page having to be written a particular way.
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "uses" in node and isinstance(node.get("uses"), str):
            found.append(node)
        for value in node.values():
            found.extend(_steps(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_steps(item))
    return found


#: Trees whose pages are historical records rather than copyable recipes: an
#: ADR quotes the surface as it stood when the decision was taken, and an
#: archived page is a snapshot by definition. Rewriting either to today's
#: input names would falsify the record. Same exemption
#: `scripts/check_docs_contract.py` already applies to these trees for its
#: own in-progress-phrase and retired-surface sweeps -- reusing that line
#: rather than inventing a second one.
_HISTORICAL_TREES = (
    "contribute/adr",
    "contribute/archive",
    "contribute/plans",
)


def _is_historical(page: Path) -> bool:
    relative = page.relative_to(DOCS).as_posix()
    return any(relative.startswith(f"{tree}/") for tree in _HISTORICAL_TREES)


def _documented_steps() -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    """(page, step, action document) for every first-party step in docs/."""
    collected: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for page in sorted(DOCS.rglob("*.md")):
        if _is_historical(page):
            continue
        text = page.read_text(encoding="utf-8")
        for block in _FENCE.findall(text):
            try:
                parsed = yaml.safe_load(block)
            except yaml.YAMLError:
                # A deliberately partial snippet. Not this test's business:
                # it checks the contract of steps that DO parse, and an
                # unparseable block names no inputs to check.
                continue
            for step in _steps(parsed):
                match = _ACTION_REF.match(str(step["uses"]).strip())
                if match is None:
                    continue
                document = ACTIONS.get(match.group("path") or "")
                if document is not None:
                    collected.append((page, step, document))
    return collected


DOCUMENTED_STEPS = _documented_steps()


def _identify(item: tuple[Path, dict[str, Any], dict[str, Any]]) -> str:
    page, step, _document = item
    return f"{page.relative_to(DOCS)}::{step['uses'].split('@')[0].split('/')[-1]}"


class TestEveryDocumentedInputExists:
    """An unknown `with:` key is accepted silently by GitHub and ignored."""

    def test_the_sweep_found_documented_steps(self) -> None:
        # Vacuity guard. A regex that stopped matching would make every
        # parametrized case below disappear and the file pass in silence,
        # which is the failure mode this whole module exists to prevent.
        assert len(DOCUMENTED_STEPS) >= 10, len(DOCUMENTED_STEPS)

    @pytest.mark.parametrize("item", DOCUMENTED_STEPS, ids=_identify)
    def test_every_with_key_is_declared(
        self, item: tuple[Path, dict[str, Any], dict[str, Any]]
    ) -> None:
        page, step, document = item
        declared = set(document.get("inputs", {}))
        given = {str(key) for key in (step.get("with") or {})}
        unknown = given - declared
        assert not unknown, (
            f"{page.relative_to(DOCS)} passes {sorted(unknown)} to "
            f"{step['uses']}, which declares no such input — a reader "
            "copying this gets no error, just an ignored value"
        )


class TestEveryReferencedOutputExists:
    """An unresolved output expression expands to the empty string."""

    @pytest.mark.parametrize(
        "page",
        sorted({page for page, _step, _doc in DOCUMENTED_STEPS}),
        ids=lambda p: str(p.relative_to(DOCS)),
    )
    def test_outputs_named_in_a_block_are_declared_by_the_step_that_has_that_id(
        self, page: Path
    ) -> None:
        text = page.read_text(encoding="utf-8")
        for block in _FENCE.findall(text):
            try:
                parsed = yaml.safe_load(block)
            except yaml.YAMLError:
                continue
            # Only steps in THIS block can be referenced with confidence;
            # a snippet may legitimately name an id defined in prose above
            # it, so an unknown id is skipped rather than failed.
            by_id: dict[str, dict[str, Any]] = {}
            for step in _steps(parsed):
                match = _ACTION_REF.match(str(step["uses"]).strip())
                document = ACTIONS.get(match.group("path") or "") if match else None
                if document is not None and step.get("id"):
                    by_id[str(step["id"])] = document
            for step_id, output in _STEP_OUTPUT.findall(block):
                document = by_id.get(step_id)
                if document is None:
                    continue
                assert output in document.get("outputs", {}), (
                    f"{page.relative_to(DOCS)} reads "
                    f"steps.{step_id}.outputs.{output}, which that Action "
                    "does not declare — it expands to the empty string, so "
                    "a reader copying this sees no error at all"
                )


class TestTheScanCanActuallyFire:
    """Negative controls for both halves, over synthetic content.

    Each assertion above is "no unknown key found", which passes trivially
    against a parser that finds nothing. These prove the machinery notices
    when there *is* something to notice.
    """

    BLOCK = """
- uses: abicheck/abicheck/actions/report@v1
  id: publish
  with:
    no-such-input: x
"""

    def test_an_unknown_input_is_visible_to_the_step_walker(self) -> None:
        parsed = yaml.safe_load(self.BLOCK)
        steps = _steps(parsed)
        assert len(steps) == 1
        declared = set(ACTIONS["actions/report"].get("inputs", {}))
        assert set(steps[0]["with"]) - declared == {"no-such-input"}

    def test_a_real_input_is_not_flagged(self) -> None:
        declared = set(ACTIONS["actions/report"].get("inputs", {}))
        assert "report" in declared

    def test_an_unknown_output_is_visible_to_the_expression_scan(self) -> None:
        found = _STEP_OUTPUT.findall("${{ steps.publish.outputs.no-such-output }}")
        assert found == [("publish", "no-such-output")]
        assert "no-such-output" not in ACTIONS["actions/report"].get("outputs", {})

    def test_the_action_reference_pattern_rejects_a_third_party_step(self) -> None:
        # Out of scope by design: this repository owns no contract for them.
        assert _ACTION_REF.match("actions/checkout@v4") is None
        assert _ACTION_REF.match("abicheck/abicheck/actions/report@v1") is not None

    def test_the_historical_exemption_is_narrow(self) -> None:
        # It must exempt the record trees and nothing a reader copies from.
        assert _is_historical(DOCS / "contribute/adr/002-x.md")
        assert _is_historical(DOCS / "contribute/archive/old.md")
        assert not _is_historical(DOCS / "use/github-action.md")
        assert not _is_historical(DOCS / "reference/report-action.md")
        assert not _is_historical(DOCS / "contribute/codebase-overview.md")
