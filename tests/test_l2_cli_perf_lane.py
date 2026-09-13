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


def _workflow_l2_body() -> str:
    """The `l2-cli-perf` job's own RAW text, so a match cannot come from another job.

    Raw, not a yaml round-trip: `safe_dump` re-quotes shell text, so a literal
    assertion about a `${var}` expansion fails against its own source.
    """
    text = _WORKFLOW.read_text()
    start = text.index("\n  l2-cli-perf:")
    rest = text[start + 1 :]
    out = [rest.split("\n")[0]]
    for line in rest.split("\n")[1:]:
        if line and not line.startswith("   ") and not line.lstrip().startswith("#"):
            break
        out.append(line)
    return "\n".join(out)


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
