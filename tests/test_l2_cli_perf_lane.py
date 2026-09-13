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

"""How the CI lane decides what to gate, and that the workflow asks the harness.

The third file in this family, split out once ``test_l2_cli_perf_contracts.py``
crossed the architecture gate's 1200-line test-file cap. The split is thematic,
not a trim to fit: its siblings are about *what the harness asserts* about a run
(`..._contracts.py`) and *how it turns receipts into a pass or a failure*
(`..._gate.py`), while these are about the step before either -- whether a base
receipt may be used as a baseline at all, and whether the workflow asks that
question of the gate's own rule instead of approximating it.

The rule both halves serve: a lane that gated nothing must not report a clean
pass, and an unmeasurable base must not fail the PR. Those pull in opposite
directions, which is why the decision is a count of usable baseline points
rather than a file size or an exit status.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = _load("check_l2_cli_perf")


_HARNESS_PATH = _SCRIPTS / "check_l2_cli_perf.py"
_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "performance.yml"
)


def _strip_comments(text: str) -> str:
    """*text* with YAML/shell comments removed, line by line.

    Load-bearing, and the repo says why (`AGENTS.md`: "when asserting against
    workflow *text*, strip comments first"). Without it, commenting OUT the real
    `--count-gateable-points` invocation or the positive-point conditional leaves
    the asserted substring present as a comment, so the wiring assertions below
    pass while the workflow no longer gates -- the same false positive an earlier
    `performance.yml` assertion already hit, which is why
    `test_classify_perf_paths.py` grew the identical helper. Same rule as that
    one (cut at the first `#`, drop blank remainders) rather than a second,
    subtly-different stripper.
    """
    out = []
    for raw in text.splitlines():
        body = raw.split("#", 1)[0]
        if body.strip():
            out.append(body)
    return "\n".join(out)


def _workflow_l2_body(text: str | None = None) -> str:
    """The `l2-cli-perf` job's own text, comments stripped.

    Raw slicing rather than a yaml round-trip: `safe_dump` re-quotes shell text,
    so a literal assertion about a `${var}` expansion fails against its own
    source. Sliced to this one job so a match cannot be satisfied by another
    job's body, and comment-stripped per `_strip_comments`.

    *text* exists so a test can run a deliberately-broken variant of the workflow
    through the real reader, which is the only way to show the stripping is doing
    anything.
    """
    text = _WORKFLOW.read_text() if text is None else text
    start = text.index("\n  l2-cli-perf:")
    rest = text[start + 1 :]
    out = [rest.split("\n")[0]]
    for line in rest.split("\n")[1:]:
        if line and not line.startswith("   ") and not line.lstrip().startswith("#"):
            break
        out.append(line)
    return _strip_comments("\n".join(out))


class TestWhetherToGateIsAskedOfTheGatesOwnRule:
    """A base receipt's usability is a question about its points, not its size.

    A run that failed every scenario still writes a full diagnostic receipt, so
    the workflow's `[ -s ... ]` test passed and the head run then failed for zero
    overlap -- an unmeasurable base became a failed PR. Requiring the base run's
    exit status to be 0 would be wrong in the other direction, discarding the
    baselines of six passing scenarios because a seventh failed (Codex review).
    """

    def test_a_receipt_with_no_passing_scenario_supplies_no_point(
        self, tmp_path, capsys
    ) -> None:
        receipt = tmp_path / "base.json"
        receipt.write_text(
            json.dumps(
                {
                    "scenarios": [
                        {
                            "id": "compare_live_live[x]",
                            "status": "failed",
                            "steps": [
                                {
                                    "name": "compare",
                                    "scope": "full_cli",
                                    "gated": True,
                                    "wall_seconds": 1.0,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        assert harness.main(["--count-gateable-points", str(receipt)]) == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_a_partially_failed_receipt_still_supplies_its_passing_points(
        self, tmp_path, capsys
    ) -> None:
        """Why `base_status == 0` would be too strict."""
        receipt = tmp_path / "base.json"
        receipt.write_text(
            json.dumps(
                {
                    "scenarios": [
                        {
                            "id": "compare_live_live[x]",
                            "status": "ok",
                            "steps": [
                                {
                                    "name": "compare",
                                    "scope": "full_cli",
                                    "gated": True,
                                    "wall_seconds": 1.0,
                                }
                            ],
                        },
                        {
                            "id": "dump_l2[x]",
                            "status": "failed",
                            "steps": [
                                {
                                    "name": "dump",
                                    "scope": "full_cli",
                                    "gated": True,
                                    "wall_seconds": 1.0,
                                }
                            ],
                        },
                    ]
                }
            )
        )
        assert harness.main(["--count-gateable-points", str(receipt)]) == 0
        assert capsys.readouterr().out.strip() == "1"

    def test_an_unreadable_receipt_answers_zero_rather_than_failing(
        self, tmp_path, capsys
    ) -> None:
        """It is a question, not a check: 'unusable as a baseline' either way."""
        broken = tmp_path / "broken.json"
        broken.write_text("{not json")
        assert harness.main(["--count-gateable-points", str(broken)]) == 0
        assert capsys.readouterr().out.strip() == "0"
        assert (
            harness.main(["--count-gateable-points", str(tmp_path / "gone.json")]) == 0
        )
        assert capsys.readouterr().out.strip() == "0"

    def test_the_query_measures_nothing(self) -> None:
        """It must not compile a fixture or run the CLI -- it is a CI preflight."""
        source = _HARNESS_PATH.read_text()
        body = source.split("def main(", 1)[1]
        preflight = body.split("unsuitable = host_unsuitable_reason()", 1)[0]
        assert "_report_gateable_point_count" in preflight


class TestTheWorkflowAsksTheHarnessWhetherToGate:
    def test_the_l2_job_no_longer_decides_on_file_size(self) -> None:
        body = _workflow_l2_body()
        assert "--count-gateable-points" in body
        assert "-s reports/perf/l2_cli_base.json" not in body

    def test_the_gate_is_conditional_on_a_positive_point_count(self) -> None:
        body = _workflow_l2_body()
        assert 'if [ "${base_points:-0}" -gt 0 ]' in body

    def test_the_body_is_really_just_this_job(self) -> None:
        """Vacuity guard: a reader returning the whole file passes the tests above."""
        body = _workflow_l2_body()
        assert "l2-cli-perf:" in body
        assert "l2-cli-extended:" not in body
        assert len(body) < len(_WORKFLOW.read_text())


class TestTheWorkflowReaderIgnoresComments:
    """A commented-out gate must fail the wiring assertions, not satisfy them.

    The invariant, executable rather than asserted in prose: take the REAL
    workflow, comment out the line each assertion depends on, push it through
    the real reader, and require the assertion to fail. Without comment
    stripping both mutations pass, which is exactly the false-positive mode
    `AGENTS.md` records from an earlier `performance.yml` assertion.
    """

    @staticmethod
    def _commented_out(fragment: str) -> str:
        """The real workflow with the line holding *fragment* commented out."""
        text = _WORKFLOW.read_text()
        lines = text.split("\n")
        hits = [i for i, line in enumerate(lines) if fragment in line]
        assert hits, f"{fragment!r} is not in the workflow at all"
        for index in hits:
            stripped = lines[index].lstrip()
            indent = lines[index][: len(lines[index]) - len(stripped)]
            lines[index] = f"{indent}# {stripped}"
        return "\n".join(lines)

    @pytest.mark.parametrize(
        "fragment",
        ["--count-gateable-points", 'if [ "${base_points:-0}" -gt 0 ]'],
    )
    def test_commenting_the_line_out_is_detected(self, fragment: str) -> None:
        body = _workflow_l2_body(self._commented_out(fragment))
        assert fragment not in body

    @pytest.mark.parametrize(
        "fragment",
        ["--count-gateable-points", 'if [ "${base_points:-0}" -gt 0 ]'],
    )
    def test_the_mutation_would_pass_without_stripping(self, fragment: str) -> None:
        """Vacuity guard on the test above: the mutation must really be invisible.

        If this ever fails, the test above proves nothing -- it would be passing
        because the mutation removed the text rather than because stripping
        caught it.
        """
        assert fragment in self._commented_out(fragment)

    def test_the_real_workflow_still_satisfies_the_assertions(self) -> None:
        """And stripping must not be so aggressive that the real wiring vanishes."""
        body = _workflow_l2_body()
        assert "--count-gateable-points" in body
        assert 'if [ "${base_points:-0}" -gt 0 ]' in body
