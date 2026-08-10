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
them: the `skill` arm gets the one published skill the scenario names installed
into the workspace's own `.claude/skills/`, the `baseline` arm gets none.
Everything else — prompt, fixture, tool access, model, turn budget — is
identical, so a difference in outcome is attributable to the skill rather than
to the setup.

Each run happens in a disposable workspace holding a copy of the fixture, with
the recording shim first on PATH. The agent is never told abicheck exists; a
skill that has to be *found* is the thing being measured (ADR-058), and naming
the tool in the prompt would hand the baseline arm the same answer.

**The workspace must live outside this repository, and that is enforced rather
than documented.** Claude Code discovers skills from the project the working
directory belongs to, and this repository's own root carries all four published
trees in `.claude/skills/`. A workspace anywhere beneath it therefore hands the
*baseline* arm every skill it is defined by not having — verified against the
real CLI, which reported all four visible from an in-repo directory and exactly
the one installed from a workspace outside it. That is not a degraded
measurement, it is the absence of one: both arms would be skill arms and the
comparison would read as "the skill changes nothing".

Because that confound is silent in the output, each run also *records* what the
CLI said it could see (`system/init`'s skill list) and refuses to continue when
the arms are not what they claim. Evidence that the treatment was applied
belongs in the transcript, next to the outcome it explains.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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

#: Appended verbatim to every scenario prompt, in both arms.
#:
#: G37 D3: the zero-tolerance dimensions grade the *claim*, and a regex over
#: prose cannot tell "ABI-compatible but source-breaking" — which is exactly
#: `API_BREAK` — from hedging. So the answer carries its own typed envelope
#: instead of being parsed for one.
#:
#: The cost is stated in the plan and worth repeating here: naming the ordinal
#: vocabulary tells *both* arms which distinctions exist, which makes the
#: interaction less natural than an unprompted one and, if anything, helps the
#: baseline. It buys a safety gate that does not rest on reading free text, and
#: since both arms get the identical text it cannot manufacture a difference
#: between them.
ANSWER_CONTRACT = """

When you have finished, end your reply with a fenced ```json block — nothing
after it — in exactly this shape:

{"verdict": "<one of NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK, API_BREAK, BREAKING, or null if the two sides cannot be compared at all>",
 "evidence": [<which compatibility-tool runs this rests on: number *only* your invocations of the compatibility-checking tool itself, from 0, in the order you ran them — not shell commands, file reads, or compiles. Each run also prints its own number on stderr; use that if you have it>],
 "confident": true or false}

If `confident` is false, add an `"uncertainty"` object with `"reason"` (one of
`not_comparable`, `evidence_too_shallow`, `matrix_target_unrun`,
`contract_coverage_incomplete`) and `"unresolved"` naming what specifically is
unresolved. Give exactly one such block.
"""

#: `platform.system()` -> the spelling `examples/ground_truth.json` uses.
_HOST_PLATFORM = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}


def host_platform() -> str:
    return _HOST_PLATFORM.get(platform.system(), platform.system().lower())


def supported_here(scenario: dict) -> bool:
    """Whether this host can produce the evidence the scenario expects.

    Several catalog cases are Linux-only. Running one elsewhere grades correct
    platform-specific behaviour against a Linux expectation, which is a wrong
    number rather than a missing one. An empty list is "no declared
    restriction" — Category B fixtures, built for this evaluation.
    """
    platforms = scenario.get("platforms") or []
    return not platforms or host_platform() in platforms


def is_inside_repo(path: Path) -> bool:
    """Whether `path` would place a workspace inside this checkout."""
    resolved = path.resolve()
    return resolved == ROOT or ROOT in resolved.parents


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


def visible_native_skills(events: list[dict]) -> list[str] | None:
    """The published skills the CLI reported seeing, or None if it never said.

    `None` and `[]` are different answers and must not collapse: an absent
    `init` event means the treatment is unverified, while an empty list is
    positive evidence that the baseline arm saw nothing.
    """
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return sorted(s for s in event.get("skills", []) if s.startswith("native-"))
    return None


def check_treatment(arm: str, scenario: dict, visible: list[str] | None) -> str | None:
    """The reason this run is not evidence about its arm, if it is not."""
    if visible is None:
        return "the CLI never reported which skills it could see"
    if arm == "baseline" and visible:
        return f"baseline arm could see published skill(s): {', '.join(visible)}"
    if arm == "skill" and visible != [scenario["skill"]]:
        return (
            f"skill arm should see exactly ['{scenario['skill']}'], saw: "
            f"{visible or '[]'}"
        )
    return None


def _final_text(events: list[dict]) -> str:
    for event in reversed(events):
        if event.get("type") == "result":
            return str(event.get("result") or "")
    return ""


def _usage(events: list[dict], elapsed: float) -> dict[str, Any]:
    usage: dict[str, Any] = {"wall_clock_seconds": round(elapsed, 1)}
    for event in reversed(events):
        if event.get("type") != "result":
            continue
        counts = event.get("usage") or {}
        usage["turns"] = event.get("num_turns")
        usage["tokens_in"] = counts.get("input_tokens")
        usage["tokens_out"] = counts.get("output_tokens")
        usage["cost_usd"] = event.get("total_cost_usd")
        break
    usage["tool_calls"] = sum(
        1
        for event in events
        if event.get("type") == "assistant"
        for block in (event.get("message") or {}).get("content") or []
        if isinstance(block, dict) and block.get("type") == "tool_use"
    )
    return usage


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
    if real is None:  # checked again in main() before any model call
        raise RuntimeError("abicheck is not on PATH")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SKILL_EVAL_CALLS": str(out_dir / "calls.jsonl"),
        "SKILL_EVAL_REAL_ABICHECK": real,
    }

    prompt = scenario["prompt"] + ANSWER_CONTRACT
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603
        [
            "claude",
            "-p",
            prompt,
            "--max-turns",
            "12",
            # stream-json, not text: the event stream is what carries which
            # skill activated and which tools ran, so dimension 1 grades an
            # observation rather than an inference from the final prose.
            "--output-format",
            "stream-json",
            "--verbose",
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

    (out_dir / "events.jsonl").write_text(proc.stdout, encoding="utf-8")
    if proc.stderr:
        (out_dir / "runner.err").write_text(proc.stderr, encoding="utf-8")

    events: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    (out_dir / "final.md").write_text(_final_text(events), encoding="utf-8")
    usage = _usage(events, elapsed)
    (out_dir / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")

    visible = visible_native_skills(events)
    problem = check_treatment(arm, scenario, visible)
    if problem is not None:
        raise RuntimeError(
            f"{scenario_id}/{arm}/{rep}: {problem}. The arms are not what they "
            f"claim, so no run in this batch is evidence about the skill."
        )

    return {
        "scenario_id": scenario_id,
        "arm": arm,
        "repetition": rep,
        "skill": scenario["skill"],
        "exit_code": proc.returncode,
        "visible_skills": visible,
        "wall_clock_seconds": usage["wall_clock_seconds"],
    }


def _recovered_record(
    out_dir: Path, sid: str, arm: str, rep: int, scenario: dict
) -> dict:
    """An index row for a run whose directory exists but whose row does not.

    A crash between writing `final.md` and rewriting `index.json` used to make
    that repetition permanently invisible: every later resume skipped the
    directory as done and never added the row, so the aggregate silently
    counted one run fewer than was actually paid for.

    The treatment check runs again here, and that is not belt-and-braces. A run
    rejected by `_run_once` has *already written* `final.md` — the check happens
    after — so recovering on the strength of that file alone would launder a
    contaminated baseline into accepted evidence on the next resume, which is
    the one failure the check exists to prevent.
    """
    record = {
        "scenario_id": sid,
        "arm": arm,
        "repetition": rep,
        "skill": scenario["skill"],
        "recovered": True,
    }
    usage_path = out_dir / "usage.json"
    if usage_path.is_file():
        try:
            record["wall_clock_seconds"] = json.loads(
                usage_path.read_text(encoding="utf-8")
            ).get("wall_clock_seconds")
        except json.JSONDecodeError:
            pass
    events_path = out_dir / "events.jsonl"
    events: list[dict] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    visible = visible_native_skills(events)
    record["visible_skills"] = visible
    problem = check_treatment(arm, scenario, visible)
    if problem is not None:
        raise RuntimeError(
            f"{sid}/{arm}/{rep}: {problem}. This run was left on disk without an "
            f"index row, which is what a rejected run looks like — delete its "
            f"directory to re-run it rather than recovering it as evidence."
        )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="Directory to write runs into")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"Comma-separated subset of {','.join(ARMS)}",
    )
    parser.add_argument(
        "--scenarios", default="", help="Comma-separated ids; default: every ready one"
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)

    # These checks run before the first model call: a run that discovers any of
    # them afterwards has spent real money producing output that *looks* like a
    # completed evaluation. An absent abicheck makes the shim answer 70 to every
    # call — indistinguishable at grading time from a tool failure; a mistyped
    # arm silently produces a baseline workspace under its name; and an in-repo
    # output root gives the baseline arm the skills that define it.
    if shutil.which("abicheck") is None:
        print(
            "abicheck is not on PATH; every recorded call would be a shim "
            "misconfiguration. Install it with `pip install -e .`.",
            file=sys.stderr,
        )
        return 1
    arms = [a for a in args.arms.split(",") if a]
    unknown = sorted(set(arms) - set(ARMS))
    if unknown:
        print(f"unknown arm(s): {', '.join(unknown)}", file=sys.stderr)
        return 1

    out_root = Path(args.out)
    if is_inside_repo(out_root):
        print(
            f"--out {out_root} is inside {ROOT}, so every workspace would belong "
            f"to this project and the baseline arm would discover the published "
            f"skills in .claude/skills/. Choose a path outside the checkout.",
            file=sys.stderr,
        )
        return 1

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

    out_root.mkdir(parents=True, exist_ok=True)

    # Resuming must not lose what earlier invocations recorded: starting from
    # an empty list and rewriting on the first new run drops every completed
    # repetition from the index while its directory still sits on disk.
    index_path = out_root / "index.json"
    index: list[dict] = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else []
    )
    done = {(r["scenario_id"], r["arm"], r["repetition"]) for r in index}

    def flush() -> None:
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    for sid, scenario in scenarios.items():
        if not supported_here(scenario):
            print(
                f"skip {sid}: not supported on {host_platform()} ({scenario['platforms']})"
            )
            continue
        for arm in arms:
            for rep in range(args.repetitions):
                out_dir = out_root / sid / arm / str(rep)
                if (sid, arm, rep) in done:
                    print(f"skip {sid}/{arm}/{rep} (already run)")
                    continue
                if (out_dir / "final.md").exists():
                    print(f"recover {sid}/{arm}/{rep} (ran, was not indexed)")
                    index.append(_recovered_record(out_dir, sid, arm, rep, scenario))
                    flush()
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
                flush()

    print(f"\n{len(index)} runs written to {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
