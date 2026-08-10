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

"""Headless Claude Code runner — one recorded run per (scenario, arm, repetition).

The two arms differ in exactly one thing, which is the whole point of running
them: the `skill` arm gets the published skill trees installed into the
workspace's own `.claude/skills/`, the `baseline` arm gets none. Everything
else — prompt, fixture, tool access, model, turn budget — is identical, so a
difference in outcome is attributable to the skill rather than to the setup.

Each run happens in a disposable workspace holding a copy of the fixture, with
the recording shim first on PATH. The agent is never told abicheck exists; a
skill that has to be *found* is the thing being measured (ADR-058), and naming
the tool in the prompt would hand the baseline arm the same answer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = ROOT / "agent-evals" / "skills"
SHIM = EVAL_DIR / "shim" / "abicheck"
PUBLISHED_SKILLS = ROOT / ".claude" / "skills"
PACK = EVAL_DIR / "skill-eval-pack.json"

ARMS = ("skill", "baseline")

#: Identical for both arms — the treatment must be the skill, nothing else.
#: `Skill` is included so the skill arm can actually invoke what it finds;
#: the baseline arm has no skills installed, so offering it the tool changes
#: nothing and keeps the two invocations byte-identical apart from the
#: workspace contents.
ALLOWED_TOOLS = ("Bash", "Read", "Glob", "Grep", "Skill")


def _prepare_workspace(work: Path, scenario: dict, arm: str) -> None:
    """A disposable copy of the fixture, plus the arm's skill configuration."""
    fixture = ROOT / scenario["inputs"]
    shutil.copytree(fixture, work / "library")
    if arm == "skill":
        skills = work / ".claude" / "skills"
        skills.mkdir(parents=True)
        shutil.copytree(
            PUBLISHED_SKILLS / scenario["skill"], skills / scenario["skill"]
        )


def _run_once(
    scenario_id: str, scenario: dict, arm: str, rep: int, out_dir: Path, timeout: int
) -> dict:
    work = out_dir / "workspace"
    work.mkdir(parents=True)
    _prepare_workspace(work, scenario, arm)

    bin_dir = out_dir / "bin"
    bin_dir.mkdir()
    shutil.copy2(SHIM, bin_dir / "abicheck")
    (bin_dir / "abicheck").chmod(0o755)

    real = shutil.which("abicheck")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SKILL_EVAL_CALLS": str(out_dir / "calls.jsonl"),
        "SKILL_EVAL_REAL_ABICHECK": real or "",
    }

    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603
        [
            "claude",
            "-p",
            scenario["prompt"],
            "--max-turns",
            "12",
            # Named tools rather than `--permission-mode bypassPermissions`:
            # that maps to --dangerously-skip-permissions, which the CLI
            # refuses under root, so an evaluation running as root would
            # record 48 four-second failures and call them results. Both arms
            # get the identical list, so the treatment stays the skill alone.
            "--allowedTools",
            *ALLOWED_TOOLS,
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started

    (out_dir / "final.md").write_text(proc.stdout, encoding="utf-8")
    if proc.stderr:
        (out_dir / "runner.err").write_text(proc.stderr, encoding="utf-8")

    return {
        "scenario_id": scenario_id,
        "arm": arm,
        "repetition": rep,
        "skill": scenario["skill"],
        "exit_code": proc.returncode,
        "wall_clock_seconds": round(elapsed, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="Directory to write runs into")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument(
        "--scenarios", default="", help="Comma-separated ids; default: every ready one"
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    wanted = [s for s in args.scenarios.split(",") if s]
    scenarios = {
        sid: entry
        for sid, entry in sorted(pack["scenarios"].items())
        if entry["status"] == "ready" and (not wanted or sid in wanted)
    }
    if not scenarios:
        print("no ready scenarios selected", file=sys.stderr)
        return 1

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []

    for sid, scenario in scenarios.items():
        for arm in args.arms.split(","):
            for rep in range(args.repetitions):
                out_dir = out_root / sid / arm / str(rep)
                if (out_dir / "final.md").exists():
                    print(f"skip {sid}/{arm}/{rep} (already run)")
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                print(f"run  {sid}/{arm}/{rep}", flush=True)
                try:
                    record = _run_once(sid, scenario, arm, rep, out_dir, args.timeout)
                except subprocess.TimeoutExpired:
                    record = {
                        "scenario_id": sid,
                        "arm": arm,
                        "repetition": rep,
                        "skill": scenario["skill"],
                        "exit_code": None,
                        "timed_out": True,
                    }
                    (out_dir / "final.md").write_text("", encoding="utf-8")
                index.append(record)
                (out_root / "index.json").write_text(
                    json.dumps(index, indent=2), encoding="utf-8"
                )

    print(f"\n{len(index)} runs written to {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
