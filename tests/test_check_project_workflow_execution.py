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

"""Execute check-project.yml's "Sanitize check-id for artifact name" step
against the same hostile corpus check-single.yml's identical step is
checked against (`test_reusable_workflow_execution.py`).

bug-class-regression-testing.md's Phase 8 names this class's own registered
gap explicitly: the execution harness #758 introduced was scoped to
`check-single.yml`'s shell steps only, "not every scalar input across the
repository's other shell scripts and composite-action steps." This is the
concrete, verified second target: `check-project.yml`'s own step comment
says it keeps "check-project.yml's own ... step verbatim" -- i.e. this is a
second, independently-maintained copy of the *identical* sanitizer logic
(read the two step bodies side by side to confirm), not merely a similarly-
named one. A regression introduced in only one of the two copies -- exactly
the kind of drift two independent copies of the same logic invite -- would
have gone uncaught by `test_reusable_workflow_execution.py` alone, since
that file only ever loads `check-single.yml`.

Only the sanitizer step is duplicated between the two workflows (confirmed
by inspecting both files); the staging-clear and workflow-identity steps
`test_reusable_workflow_execution.py` also exercises are check-single.yml-
specific, so this file does not repeat that coverage.
"""

from __future__ import annotations

import pytest
from _workflow_exec import (
    FORBIDDEN_ARTIFACT_NAME_CHARS,
    HOSTILE_SCALAR_CORPUS,
    find_run_step,
    have_bash,
    make_workspace,
    outside_is_intact,
    run_step,
)

CHECK_PROJECT = "check-project.yml"

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")

#: Shared by `TestCheckIdSanitizationIsInjective` and the cross-workflow
#: equality check below -- both need pairs that collide under the `tr`-style
#: sanitization but must stay distinct once the content hash is appended.
_COLLISION_PAIRS = [
    ("lib/a", "lib:a"),  # both sanitize to lib_a
    ("a b", "a-b"),  # space vs dash
    ("x!y", "x?y"),
    ("../a", "..%a"),
    ("lib\na", "lib\ta"),
]


def _sanitize(tmp_path, check_id: str):
    step = find_run_step(CHECK_PROJECT, "check", "Sanitize check-id for artifact name")
    workspace = make_workspace(tmp_path)
    return run_step(step, workspace=workspace, env={"CHECK_ID": check_id})


class TestCheckIdSanitizationUnderHostileInput:
    @pytest.mark.parametrize("check_id", HOSTILE_SCALAR_CORPUS)
    def test_result_is_always_a_safe_artifact_name(
        self, tmp_path, check_id: str
    ) -> None:
        result = _sanitize(tmp_path, check_id)
        assert result.returncode == 0, result.stderr
        value = result.outputs["id"]
        unsafe = sorted(set(value) & FORBIDDEN_ARTIFACT_NAME_CHARS)
        assert not unsafe, f"sanitized id {value!r} still contains {unsafe}"
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value), (
            f"sanitized id {value!r} still contains a control character"
        )

    @pytest.mark.parametrize("check_id", HOSTILE_SCALAR_CORPUS)
    def test_exactly_one_record_is_written(self, tmp_path, check_id: str) -> None:
        """The #706 bug class, on this (second, independent) copy of the step.

        Counting *records* -- not just checking the `id` value -- is what
        makes an injected extra $GITHUB_OUTPUT line visible.
        """
        result = _sanitize(tmp_path, check_id)
        assert len(result.output_lines) == 1, (
            f"expected one record, got {result.output_lines!r}"
        )
        assert result.output_lines[0].startswith("id=")

    @pytest.mark.parametrize("check_id", HOSTILE_SCALAR_CORPUS)
    def test_the_result_is_always_a_single_harmless_path_component(
        self, tmp_path, check_id: str
    ) -> None:
        result = _sanitize(tmp_path, check_id)
        value = result.outputs["id"]
        assert outside_is_intact(tmp_path)
        assert "/" not in value and "\\" not in value
        assert value not in (".", ".."), value


class TestCheckIdSanitizationIsInjective:
    """The must-merge / must-not-merge pair for this (second) copy of the
    sanitizer -- mirrors `test_reusable_workflow_execution.py`'s identical
    class for check-single.yml's copy, since safety alone is satisfiable by
    returning a constant, which would collapse every matrix cell onto one
    uploaded artifact name."""

    @pytest.mark.parametrize("left, right", _COLLISION_PAIRS)
    def test_distinct_ids_that_sanitize_alike_stay_distinct(
        self, tmp_path, left: str, right: str
    ) -> None:
        a = _sanitize(tmp_path / "a", left).outputs["id"]
        b = _sanitize(tmp_path / "b", right).outputs["id"]
        assert a != b, f"{left!r} and {right!r} both produced {a!r}"

    def test_the_same_id_is_stable_across_runs(self, tmp_path) -> None:
        a = _sanitize(tmp_path / "a", "my-lib").outputs["id"]
        b = _sanitize(tmp_path / "b", "my-lib").outputs["id"]
        assert a == b

    def test_a_benign_id_stays_readable(self, tmp_path) -> None:
        assert _sanitize(tmp_path, "libfoo").outputs["id"].startswith("libfoo-")


def _sanitized_id(tmp_path, workflow: str, check_id: str) -> str:
    step = find_run_step(workflow, "check", "Sanitize check-id for artifact name")
    return run_step(
        step, workspace=make_workspace(tmp_path), env={"CHECK_ID": check_id}
    ).outputs["id"]


class TestTheTwoCopiesOfTheSanitizerAgree:
    """A direct pin that the "verbatim" claim in check-project.yml's own
    comment holds today -- not just that each copy independently behaves
    safely, which the classes above already establish, but that they
    produce the *same* answer for the same input. Diverging outputs would
    mean an ADR-047 §7 check-id sanitizes to two different artifact names
    depending on which workflow ran it, which is exactly the drift risk two
    independently-maintained copies of one algorithm carry.

    Parametrized over the full hostile corpus and the collision pairs
    (Codex review, PR #919): a single fixed example does not pin this --
    e.g. a change made to only one copy that special-cases newlines or
    leading-dash ids could still pass every per-workflow safety/stability/
    injectivity assertion above while producing a different artifact name
    from the other copy, and a one-example equality check would stay green.
    """

    @pytest.mark.parametrize("check_id", HOSTILE_SCALAR_CORPUS)
    def test_agrees_on_every_hostile_corpus_value(
        self, tmp_path, check_id: str
    ) -> None:
        single_out = _sanitized_id(tmp_path / "a", "check-single.yml", check_id)
        project_out = _sanitized_id(tmp_path / "b", CHECK_PROJECT, check_id)
        assert single_out == project_out

    @pytest.mark.parametrize("left, right", _COLLISION_PAIRS)
    def test_agrees_on_every_collision_pair(
        self, tmp_path, left: str, right: str
    ) -> None:
        for check_id in (left, right):
            single_out = _sanitized_id(tmp_path / "a", "check-single.yml", check_id)
            project_out = _sanitized_id(tmp_path / "b", CHECK_PROJECT, check_id)
            assert single_out == project_out, check_id
