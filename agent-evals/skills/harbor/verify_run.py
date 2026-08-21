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

"""Harbor verifier bridge (ADR-058, Harbor migration amendment).

Each generated task's `tests/test.sh` runs this inside the trial container,
after the agent has finished. It bridges Harbor's filesystem-based verifier
contract onto the *same* deterministic graders `agent-evals/skills/graders/`
already implements and `tests/test_skill_eval_graders*.py` already covers --
this file adds no new grading logic, only the plumbing to feed it what a
Harbor trial produces instead of what `runners/claude_code.py`'s own harness
produces.

**Why not just port the graders wholesale.** They are unchanged and imported
directly (`sys.path` manipulation below, since `graders/` is a real package
with relative imports) -- rewriting them would mean re-verifying every
existing regression test against a second copy, exactly the duplication this
repo's own AGENTS.md warns against ("one fact defined in exactly one place").

**Why `arm=None`.** A Harbor task has no "skill" vs. "baseline" variant baked
in -- see the ADR amendment this file's sibling `../CLAUDE.md` note points
at: which arm a trial belongs to is an *agent-configuration* choice
(Harbor's own `--skill owner/repo:path[@ref]` flag on the CLI -- the
generated image deliberately never bakes the skill in, so a trial that
omits the flag is the baseline arm and one that supplies it is the skill
arm), not a task-directory choice, so this verifier cannot know which arm
produced the transcript it is grading and must not guess. `dimension_1`'s
own docstring already documents this degradation path precisely:
"omitted, activation stays optional."

**Reward shape.** `reward.txt` (Harbor's minimum, guaranteed-read contract)
is the single collapsed 0/1: correct verdict AND no zero-tolerance failure.
`reward.json` carries the full per-dimension breakdown for debugging --
Harbor documents both are valid; this writes both, txt as the authoritative
scalar, json as the diagnostic detail no single scalar can carry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: The generated Dockerfile clones abicheck to `/opt/abicheck-src`, so this
#: is where the graders live inside a real trial container -- inserted ahead
#: of anything already on `sys.path` so this always imports the graders this
#: exact task pack was generated against, never an unrelated `graders`
#: package that happened to resolve first. Overridable so this file can be
#: exercised directly against a checkout (`tests/test_gen_harbor_tasks.py`)
#: without a container at all.
_GRADERS_ROOT = Path(
    os.environ.get("HARBOR_GRADERS_ROOT", "/opt/abicheck-src/agent-evals/skills")
)
if str(_GRADERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_GRADERS_ROOT))

from graders import dimensions as dim  # noqa: E402


def _load_scenario(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def grade(workspace: Path, scenario: dict) -> dict:
    """`dimensions.grade_run`, unmodified, against a Harbor trial's own layout.

    `workspace` must carry `calls.jsonl` (the recording shim writes it there
    directly -- `SKILL_EVAL_CALLS=/workspace/calls.jsonl` in the generated
    Dockerfile) and, if the agent followed the instruction, `final.md` (the
    agent's own last action, not this script's -- see `instruction.md`'s
    file-write addendum). A run that never wrote `final.md` grades exactly
    like `runners/claude_code.py`'s own harness grades an empty transcript:
    `claim_mod.extract("")` reports `absent`, which fails every dimension
    that requires a claim -- not a Harbor-specific case to special-case here.
    """
    return dim.grade_run(workspace, scenario, arm=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--reward-txt", type=Path, required=True)
    parser.add_argument("--reward-json", type=Path, required=True)
    args = parser.parse_args(argv)

    scenario = _load_scenario(args.scenario)
    result = grade(args.workspace, scenario)

    passed = bool(result["correct"]) and not result["zero_tolerance_failed"]

    args.reward_txt.parent.mkdir(parents=True, exist_ok=True)
    args.reward_txt.write_text("1\n" if passed else "0\n", encoding="utf-8")
    args.reward_json.parent.mkdir(parents=True, exist_ok=True)
    args.reward_json.write_text(
        json.dumps(
            {
                "reward": 1.0 if passed else 0.0,
                "correct_verdict": 1.0 if result["correct"] else 0.0,
                "zero_tolerance_passed": 1.0
                if not result["zero_tolerance_failed"]
                else 0.0,
                "claim_status": result["claim_status"],
                "claimed_verdict": result["claimed_verdict"],
                "expected_verdict": result["expected_verdict"],
                "zero_tolerance_failed_dimensions": result["zero_tolerance_failed"],
                "dimensions": result["dimensions"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"verify_run: correct={result['correct']} "
        f"zero_tolerance_failed={result['zero_tolerance_failed']} "
        f"reward={1 if passed else 0}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
