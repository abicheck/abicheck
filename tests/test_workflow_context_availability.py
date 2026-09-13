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

"""No workflow may name a context where GitHub does not provide one.

A context used in a position that does not offer it is not a runtime error
in one job -- it is a *validation* error for the whole file, so GitHub
rejects the workflow before any job starts. The run then completes in about
a second with `failure`, zero jobs, and a title taken from the file's path
rather than its `name:`, because the file never parsed. Every gate in that
workflow is silently offline, which for `ci.yml` is the entire required set.

Observed here rather than theorised: PR #1294 set `TMPDIR: ${{ runner.temp }}`
in `jobs.unit-tests.env`, and run 34779572374 did exactly the above. `runner`
is available to a *step*'s `env`/`if`/`run`, and not at `jobs.<id>.env` --
which reads as an arbitrary distinction and is easy to get wrong, since the
same expression one indentation level down is correct. No local check catches
it: the file is valid YAML, `ruff` does not read it, and the repository's own
workflow tests parse it rather than validate it.

Scoped deliberately narrowly. This asserts the *one* rule that has actually
bitten, over the positions a plain YAML parse can identify with certainty --
job-level `env`, `if`, `name`, `timeout-minutes`, `continue-on-error` and
`strategy` -- rather than attempting a general re-implementation of GitHub's
context-availability table, which would be a second, unverifiable copy of a
specification this repository does not own. `actionlint` would subsume it; it
is not available in this environment, and a test that only runs where a tool
happens to be installed is not a gate.
"""

from __future__ import annotations

import re

import pytest
import yaml
from _workflow_files import read_repo_text, workflow_paths

#: Contexts GitHub does not resolve at the *job* level. `runner` describes the
#: machine a step runs on, which does not exist until a job is dispatched, so
#: no `jobs.<id>.*` key outside a step may name it. `steps`, `job` and `env`
#: are unavailable there for the same reason: they describe state that only
#: exists once the job is running.
_JOB_LEVEL_FORBIDDEN = ("runner", "steps", "job", "env")

#: The `jobs.<id>.*` keys whose values GitHub evaluates as expressions and
#: that are not themselves step definitions.
#:
#: `runs-on` is the one most worth having: the runner is *being selected* when
#: it is evaluated, so `runs-on: ${{ runner.os }}` is rejected for exactly the
#: reason the rest of this list is, and leaving it out let the same
#: whole-workflow rejection through an adjacent field (Codex review, PR #1294).
_JOB_LEVEL_EXPRESSION_KEYS = (
    "env",
    "if",
    "name",
    "runs-on",
    "timeout-minutes",
    "continue-on-error",
    "strategy",
    "container",
    "services",
    "concurrency",
    "with",
    "secrets",
)

#: Deliberately NOT swept, because the forbidden set is not uniform across
#: job-level keys and a blanket rule is wrong here: `jobs.<id>.outputs` may use
#: `steps` (`${{ steps.x.outputs.y }}` is the standard idiom -- ten real
#: workflows in this repository do it) and `job`/`runner`/`env` besides, and
#: `jobs.<id>.defaults.run` may use `env`. `environment` splits *within itself*:
#: its `url` may use `steps` -- that is GitHub's own Pages idiom, and
#: `pages.yml` here uses it -- while its `name` may not.
#:
#: Each of these was found by the sweep reporting a real, valid workflow as
#: rejected, which is the false-positive failure mode this module already had
#: to fix once. That is the boundary: keys are listed above only where all four
#: names are genuinely unavailable, and widening past it means re-implementing
#: GitHub's context-availability table sub-key by sub-key -- a second,
#: unverifiable copy of a specification this repository does not own, whose
#: errors present as blocked valid changes.
_JOB_LEVEL_KEYS_WITH_WIDER_ACCESS = ("outputs", "defaults", "environment")

#: Keys GitHub evaluates as an expression even without `${{ }}` delimiters.
#: `if: runner.os == 'Linux'` is valid shorthand, and reading only delimited
#: expressions accepted exactly the reference this module exists to reject
#: (Codex review, PR #1294).
_IMPLICIT_EXPRESSION_KEYS = ("if",)


def _without_string_literals(expression: str) -> str:
    """*expression* with every single-quoted literal blanked out.

    A context name inside a literal is text, not a reference:
    `contains(github.event.pull_request.body, 'runner.os')` names no context
    at all. GitHub escapes a quote by doubling it, and the pattern consumes
    `''` inside a literal for that reason (Codex review, PR #1294).
    """
    return re.sub(r"'(?:[^']|'')*'", "''", expression)


def _root_contexts(expression: str) -> set[str]:
    """The context names *expression* references at the ROOT of a path.

    Only the first segment selects a context: `github.event.inputs.runner.os`
    is rooted in `github` -- which is permitted at job level -- and `runner`
    there is an input's property name that GitHub never resolves as a context.
    Matching any dotted occurrence would fail that workflow even though GitHub
    accepts it, so a preceding `.` (or an identifier character, for a name like
    `my_runner`) disqualifies the match.

    A root is recognised by what precedes and follows it, not by a trailing
    `.`: GitHub also dereferences by index and passes a whole context to a
    function, so `runner['temp']` and `toJSON(runner)` reference `runner` just
    as `runner.temp` does, and a rule keyed on the dot accepted both (Codex
    review, PR #1294). Every identifier in the expression is returned; the
    caller intersects with the names it forbids, so ordinary function names
    (`success`, `contains`) and permitted contexts are simply not asked about.
    """
    stripped = _without_string_literals(expression)
    return set(
        re.findall(
            r"(?<![A-Za-z0-9_.$-])([A-Za-z_][A-Za-z0-9_-]*)(?![A-Za-z0-9_-])", stripped
        )
    )


def _expressions(value: object, *, implicit: bool = False) -> list[str]:
    """Every expression body inside *value*, recursively.

    With *implicit*, a string carrying no `${{ }}` at all is itself the body --
    the shorthand GitHub accepts for `if:`.
    """
    if isinstance(value, str):
        delimited = re.findall(r"\$\{\{(.*?)\}\}", value, flags=re.DOTALL)
        if delimited or not implicit:
            return delimited
        return [value]
    if isinstance(value, dict):
        return [e for v in value.values() for e in _expressions(v, implicit=implicit)]
    if isinstance(value, list):
        return [e for v in value for e in _expressions(v, implicit=implicit)]
    return []


def _job_level_violations(document: dict) -> list[str]:
    found: list[str] = []
    for job_name, job in (document.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for key in _JOB_LEVEL_EXPRESSION_KEYS:
            if key not in job:
                continue
            implicit = key in _IMPLICIT_EXPRESSION_KEYS
            for expression in _expressions(job[key], implicit=implicit):
                for context in sorted(
                    _root_contexts(expression) & set(_JOB_LEVEL_FORBIDDEN)
                ):
                    found.append(
                        f"jobs.{job_name}.{key} uses the '{context}' "
                        f"context: ${{{{{expression}}}}}"
                    )
    return found


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_no_job_level_key_uses_a_step_only_context(path) -> None:
    document = yaml.safe_load(read_repo_text(path))
    if not isinstance(document, dict):  # pragma: no cover - a non-mapping workflow
        pytest.skip(f"{path.name} is not a mapping")
    violations = _job_level_violations(document)
    assert violations == [], (
        f"{path.name} would be REJECTED by GitHub before any job starts, "
        "taking every gate in it offline. Move the value into a step (an "
        '`env:` on the step, or `echo "NAME=$VALUE" >> "$GITHUB_ENV"` in '
        f"a `run:`), where the context is available: {violations}"
    )


def test_the_check_catches_the_shape_that_broke_ci() -> None:
    """Vacuity guard, against the exact document that was rejected.

    Without this, the sweep above passes on a clean tree whether or not the
    rule is implemented at all -- which is the state the repository was in
    when the real one shipped.
    """

    rejected = yaml.safe_load(
        "jobs:\n"
        "  unit-tests:\n"
        "    env:\n"
        "      TMPDIR: ${{ runner.temp }}\n"
        "    steps:\n"
        "      - run: true\n"
    )
    assert _job_level_violations(rejected) == [
        "jobs.unit-tests.env uses the 'runner' context: ${{ runner.temp }}"
    ]


def test_the_same_expression_inside_a_step_is_accepted() -> None:
    """And the rule must not reject the correct spelling one level down."""

    accepted = yaml.safe_load(
        "jobs:\n"
        "  unit-tests:\n"
        "    steps:\n"
        "      - run: echo hi\n"
        "        env:\n"
        "          TMPDIR: ${{ runner.temp }}\n"
        "      - if: runner.os == 'Linux'\n"
        "        run: echo linux\n"
    )
    assert _job_level_violations(accepted) == []


class TestOnlyARootContextReferenceCounts:
    """A dotted name is not automatically a context reference.

    Only the FIRST segment of a path selects a context, and a name inside a
    string literal selects nothing at all. A checker that searches for the
    substring `runner.` fails workflows GitHub accepts -- which is worse than
    not having the checker, since the false positive blocks a valid change and
    the obvious way to silence it is to delete the gate. Both cases below were
    raised by Codex on PR #1294 against exactly that first implementation.
    """

    def _job(self, expression: str) -> dict:
        return {"jobs": {"j": {"if": expression, "steps": [{"run": "true"}]}}}

    def test_a_property_named_like_a_context_is_not_one(self) -> None:
        """`github.event.inputs.runner.os` is rooted in the permitted `github`."""

        document = self._job("${{ github.event.inputs.runner.os == 'linux' }}")
        assert _job_level_violations(document) == []

    def test_a_context_name_inside_a_string_literal_is_text(self) -> None:
        document = self._job(
            "${{ contains(github.event.pull_request.body, 'runner.os') }}"
        )
        assert _job_level_violations(document) == []

    def test_an_escaped_quote_inside_a_literal_does_not_end_it(self) -> None:
        """`''` is GitHub's escape, so the literal continues past it."""

        document = self._job("${{ contains(github.head_ref, 'it''s runner.os') }}")
        assert _job_level_violations(document) == []

    def test_a_name_merely_ending_in_a_context_name_is_not_one(self) -> None:
        document = self._job("${{ vars.my_runner.os == 'linux' }}")
        assert _job_level_violations(document) == []

    @pytest.mark.parametrize(
        "expression",
        (
            "${{ runner.temp }}",
            "${{ runner.os == 'Linux' }}",
            "${{ !cancelled() && runner.os != 'Windows' }}",
            "${{ format('{0}', runner.arch) }}",
        ),
        ids=("bare", "comparison", "after-operator", "as-argument"),
    )
    def test_a_real_root_reference_is_still_caught(self, expression: str) -> None:
        """The narrowing must not cost the detection it exists for."""

        assert _job_level_violations(self._job(expression)) != []

    def test_the_naive_substring_rule_would_fail_these(self) -> None:
        """Vacuity guard: the cases above must actually distinguish the two rules.

        Without this, the class could pass against a checker that never looked
        for contexts at all -- and it pins that the false positives are real
        rather than hypothetical, by reproducing the rejected implementation.
        """

        naive = [
            "${{ github.event.inputs.runner.os == 'linux' }}",
            "${{ contains(github.event.pull_request.body, 'runner.os') }}",
        ]
        for expression in naive:
            assert re.search(r"\brunner\s*\.", expression), expression
            assert _job_level_violations(self._job(expression)) == [], expression


class TestEveryFormAReferenceCanTake:
    """A root reference is not always `name.` -- three shapes slipped past.

    All three were false *negatives*: the gate accepted a document GitHub
    rejects, which is the quieter failure, since the gate then looks healthy
    while protecting nothing. Raised by Codex on PR #1294 after the
    false-positive narrowing, which is the direction a narrowing tends to
    overshoot in.
    """

    def _job(self, key: str, value: object) -> dict:
        return {"jobs": {"j": {key: value, "steps": [{"run": "true"}]}}}

    def test_an_if_without_delimiters_is_still_an_expression(self) -> None:
        """`if: runner.os == 'Linux'` is valid shorthand, and still rejected."""

        assert _job_level_violations(self._job("if", "runner.os == 'Linux'")) != []

    def test_an_implicit_if_naming_a_permitted_context_is_accepted(self) -> None:
        """The shorthand must not become "any bare `if` is a violation"."""

        assert (
            _job_level_violations(self._job("if", "github.event_name == 'push'")) == []
        )

    @pytest.mark.parametrize(
        "expression",
        ("${{ runner['temp'] }}", "${{ toJSON(runner) }}", "${{ runner }}"),
        ids=("index", "whole-context-argument", "bare"),
    )
    def test_a_reference_without_a_trailing_dot_is_still_one(
        self, expression: str
    ) -> None:
        assert _job_level_violations(self._job("name", expression)) != []

    def test_runs_on_is_swept(self) -> None:
        """The runner is being *selected* when `runs-on` is evaluated."""

        assert _job_level_violations(self._job("runs-on", "${{ runner.os }}")) != []

    @pytest.mark.parametrize("key", ("container", "services", "concurrency", "with"))
    def test_the_other_narrow_keys_are_swept(self, key: str) -> None:
        assert _job_level_violations(self._job(key, "${{ runner.os }}")) != []


class TestKeysWhoseAccessIsWiderAreLeftAlone:
    """Not every job-level key forbids these names, and guessing wrong blocks work.

    Each case here was found by the sweep reporting a real, valid workflow in
    this repository as rejected while the list was being widened. They are
    pinned as tests rather than only as a comment, because the tempting next
    edit is to add the key back for symmetry.
    """

    def test_outputs_may_use_steps(self) -> None:
        """`${{ steps.x.outputs.y }}` in `jobs.<id>.outputs` is the idiom."""

        document = {
            "jobs": {
                "j": {
                    "outputs": {"matrix": "${{ steps.discover.outputs.matrix }}"},
                    "steps": [{"run": "true"}],
                }
            }
        }
        assert _job_level_violations(document) == []

    def test_an_environment_url_may_use_steps(self) -> None:
        """GitHub's own Pages idiom; `pages.yml` in this repository uses it."""

        document = {
            "jobs": {
                "j": {
                    "environment": {
                        "name": "github-pages",
                        "url": "${{ steps.deployment.outputs.page_url }}",
                    },
                    "steps": [{"run": "true"}],
                }
            }
        }
        assert _job_level_violations(document) == []

    def test_defaults_run_may_use_env(self) -> None:
        document = {
            "jobs": {
                "j": {
                    "defaults": {"run": {"working-directory": "${{ env.DIR }}"}},
                    "steps": [{"run": "true"}],
                }
            }
        }
        assert _job_level_violations(document) == []

    def test_the_excluded_keys_are_disjoint_from_the_swept_ones(self) -> None:
        """Vacuity guard: an excluded key must not also be in the sweep."""

        assert not (
            set(_JOB_LEVEL_KEYS_WITH_WIDER_ACCESS) & set(_JOB_LEVEL_EXPRESSION_KEYS)
        )
