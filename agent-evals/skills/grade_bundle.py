#!/usr/bin/env python3
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

"""Grade one recorded run over the deterministic dimensions (G37 D4).

    python agent-evals/skills/grade_bundle.py --run <run-dir> --scenario <id>

Reads the run's own artifacts and the scenario's expected outcome out of
`skill-eval-pack.json`, and calls no model — so the same command re-derives the
same grade for an auditor with no credentials, which is the property that lets
these dimensions gate `pr` at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graders.dimensions import grade_run  # noqa: E402

PACK = Path(__file__).resolve().parent / "skill-eval-pack.json"


def load_scenario(scenario_id: str) -> dict:
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    try:
        return pack["scenarios"][scenario_id]
    except KeyError:
        raise SystemExit(
            f"unknown scenario {scenario_id!r}; the pack has: "
            f"{', '.join(sorted(pack['scenarios']))}"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", required=True, help="One run directory")
    parser.add_argument("--scenario", required=True, help="Its scenarios.yaml id")
    parser.add_argument(
        "--arm",
        choices=("skill", "baseline"),
        help="Which arm produced it. The skill arm must have invoked its skill; "
        "omitted, that half of dimension 1 is not asked.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run)
    if not run_dir.is_dir():
        print(f"{run_dir} is not a directory", file=sys.stderr)
        return 1

    grade = grade_run(run_dir, load_scenario(args.scenario), args.arm)
    print(json.dumps(grade, indent=2))
    return 0 if not grade["zero_tolerance_failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
