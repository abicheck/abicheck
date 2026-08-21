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

"""Shared fixtures for the `test_skill_eval_graders*.py` files.

Not itself a test module (no `test_` prefix — pytest never collects it), the
same pattern `tests/CLAUDE.md` already documents for `_strict_process.py`/
`_workflow_exec.py`: a helper both `test_skill_eval_graders.py` and
`test_skill_eval_graders_consumer_scoping.py` import from, so the two files
share one definition of "what a run directory looks like" rather than two
copies drifting apart. Split out when `test_skill_eval_graders.py` crossed
the AI-readiness file-size hard cap (2000 lines) — `TestDimensionSix`, the
largest class and the one most of this session's consumer-scoping/contract
findings landed in, moved to its own file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
sys.path.insert(0, str(EVAL_DIR))

from graders import (  # noqa: E402
    claim as claim_mod,  # noqa: F401 -- re-exported for the two test files
    dimensions as dim,  # noqa: F401
    evidence as ev,  # noqa: F401
)

SCENARIO_BREAKING = {
    "skill": "review-native-library-change",
    "expected": {"verdict": "BREAKING"},
}
SCENARIO_COMPATIBLE = {
    "skill": "review-native-library-change",
    "expected": {"verdict": "COMPATIBLE"},
}


def build_run(
    tmp_path: Path,
    *,
    final: str,
    calls: list[dict] | None = None,
    artifacts: dict[str, str] | None = None,
    events: list[dict] | None = None,
) -> Path:
    """A run directory shaped exactly the way the shim and runner write one."""
    run = tmp_path / "run"
    (run / "captured").mkdir(parents=True)
    run.joinpath("final.md").write_text(final, encoding="utf-8")
    for rel, text in (artifacts or {}).items():
        target = run / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    lines = [json.dumps(c) for c in (calls or [])]
    run.joinpath("calls.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    if events is not None:
        run.joinpath("events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )
    return run


def a_breaking_call(seq: int = 0, argv: list[str] | None = None) -> dict:
    return {
        "seq": seq,
        "call_id": f"c{seq}",
        "argv": argv or ["compare", "old.so", "new.so", "--format", "json"],
        "exit_code": 4,
        "stdout_path": f"captured/{seq}.out",
        "outputs": [],
    }


def envelope(**fields) -> str:
    return "Here is the answer.\n\n```json\n" + json.dumps(fields) + "\n```\n"


def a_not_comparable_answer(evidence: list[int] | None = None) -> str:
    """The one uncertainty shape that carries a `null` verdict and no caveat."""
    return envelope(
        verdict=None,
        evidence=[0] if evidence is None else evidence,
        confident=False,
        uncertainty={
            "reason": "not_comparable",
            "unresolved": "the two sides were built for different architectures",
        },
    )
