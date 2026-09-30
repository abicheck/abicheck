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

"""Two-arm (skill vs. no skill) runner for `set-up-abi-compatibility-ci`.

For every scenario x arm x repetition it materializes the fixture into a fresh
git repository **outside this checkout** (so neither arm can see abicheck's
own sources, `CLAUDE.md`, or the published skill trees), installs the skill
into the workspace's `.claude/skills/` for the `skill` arm only, runs
`claude -p` headlessly, and grades the result with `grader.py`.

The arms are identical in every other respect: same prompt, same tools
(including web access, so the baseline can read abicheck's public docs the
way a real user's agent would), same model, same turn budget. A difference
is attributable to the skill.

    python run_eval.py --out /tmp/ci-setup-eval --repetitions 2 --model claude-sonnet-5-5
    python run_eval.py --out /tmp/ci-setup-eval --report-only   # re-grade + summarize
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))

import grader  # noqa: E402

SKILL = "set-up-abi-compatibility-ci"
PUBLISHED_SKILL = ROOT / "skills" / SKILL
FIXTURES = HERE / "fixtures"
ARMS = ("skill", "baseline")

#: Appended to every prompt in both arms. The runs are non-interactive, so an
#: agent that stops to ask a question would be graded on an empty workspace.
RUN_CONTRACT = (
    "\n\n(You are working non-interactively in the repository checked out in "
    "the current directory; nobody can answer questions. Make the changes "
    "directly in the files, make reasonable decisions where information is "
    "missing, and finish with a short summary of what you did and why. You "
    "cannot run GitHub Actions from here.)"
)

ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash", "WebFetch", "WebSearch", "Skill",
]

#: Variables binding a child `claude` to the session that launched it, plus
#: the ones that would inject this checkout's own CLAUDE.md into the child.
PARENT_SESSION_VARIABLES = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_ENV_FILE",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_REMOTE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_PID",
        "CLAUDE_AFTER_LAST_COMPACT",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD",
        "CLAUDE_ADDITIONAL_DIRECTORIES",
    }
)


def child_environment() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in PARENT_SESSION_VARIABLES}


#: Where the agent sees its repository inside the isolated mount namespace.
ISOLATED_WORKSPACE = "/opt/ci-setup-eval-ws"

#: Run as a mount-namespace-private shell script (root, `unshare --mount`).
#: The agent must see only its own repository: an earlier run found this
#: checkout via `find / -name action.yml` and read abicheck's sources, which
#: no real user's agent can do. So the checkout, every other run's output,
#: and the parent session's transcripts are covered with empty tmpfs mounts,
#: and abicheck comes from a non-editable venv (`--abicheck-venv`) rather
#: than the checkout's editable install.
ISOLATE_SCRIPT = r"""
set -e
mkdir -p "$EVAL_TARGET"
mount --bind "$EVAL_WORK" "$EVAL_TARGET"
for hidden in $EVAL_HIDE; do
  [ -d "$hidden" ] && mount -t tmpfs tmpfs "$hidden"
done
cd "$EVAL_TARGET"
exec "$@"
"""


def _git(work: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=work, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "Maintainer", "GIT_AUTHOR_EMAIL": "m@example.org",
             "GIT_COMMITTER_NAME": "Maintainer", "GIT_COMMITTER_EMAIL": "m@example.org"},
    )


def materialize(scenario: dict[str, Any], work: Path, arm: str) -> None:
    shutil.copytree(FIXTURES / scenario["fixture"], work)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", f"https://github.com/example-org/{scenario['fixture']}.git")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "Initial import")
    for tag in scenario.get("tags") or []:
        _git(work, "commit", "-q", "--allow-empty", "-m", f"Release {tag}")
        _git(work, "tag", tag)
    if arm == "skill":
        dest = work / ".claude" / "skills" / SKILL
        shutil.copytree(PUBLISHED_SKILL, dest)
        # Keep the installed skill out of the agent's diff of "the project".
        (work / ".git" / "info" / "exclude").write_text(".claude/\n", encoding="utf-8")


def _events(stdout: str) -> list[dict[str, Any]]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _final_text(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            return event["result"]
    return ""


def _skill_activated(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = json.dumps(block.get("input") or {})
            if block.get("name") == "Skill" and SKILL in inp:
                return True
            if block.get("name") == "Read" and f"skills/{SKILL}" in inp:
                return True
    return False


def run_one(scenario: dict[str, Any], arm: str, rep: int, out: Path, model: str | None,
            max_turns: int, timeout: int, venv: Path | None = None) -> dict[str, Any]:
    run_dir = out / scenario["id"] / arm / f"rep{rep}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    work = run_dir / "workspace"
    materialize(scenario, work, arm)
    prompt = scenario["prompt"].strip() + RUN_CONTRACT
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), "--allowedTools", *ALLOWED_TOOLS]
    if model:
        cmd += ["--model", model]
    env = child_environment()
    cwd: Path = work
    if venv is not None:
        target = f"{ISOLATED_WORKSPACE}-{scenario['id']}-{arm}-{rep}"
        hide = [str(ROOT), str(out), str(Path.home() / ".claude" / "projects"), "/tmp/claude-0"]
        env.update(
            EVAL_WORK=str(work), EVAL_TARGET=target, EVAL_HIDE=" ".join(hide),
            PATH=f"{venv / 'bin'}{os.pathsep}{env['PATH']}",
        )
        cmd = ["unshare", "--mount", "--propagation", "private", "sh", "-c", ISOLATE_SCRIPT, "isolate", *cmd]
        cwd = Path("/")
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = "TIMEOUT"
    elapsed = time.monotonic() - started
    (run_dir / "events.jsonl").write_text(stdout, encoding="utf-8")
    if stderr:
        (run_dir / "runner.err").write_text(stderr, encoding="utf-8")
    events = _events(stdout)
    final = _final_text(events)
    (run_dir / "final.md").write_text(final, encoding="utf-8")
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    meta = {
        "scenario": scenario["id"], "arm": arm, "rep": rep, "elapsed_s": round(elapsed, 1),
        "turns": result.get("num_turns"), "cost_usd": result.get("total_cost_usd"),
        "is_error": result.get("is_error", True), "skill_activated": _skill_activated(events),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def grade_all(out: Path) -> list[dict[str, Any]]:
    corpus = grader.load_scenarios()
    rows = []
    for meta_path in sorted(out.glob("*/*/rep*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        run_dir = meta_path.parent
        final = (run_dir / "final.md").read_text(encoding="utf-8")
        g = grader.grade(run_dir / "workspace", meta["scenario"], final, corpus)
        (run_dir / "grade.json").write_text(json.dumps(g, indent=2), encoding="utf-8")
        rows.append({**meta, **g})
    return rows


def summarize(rows: list[dict[str, Any]]) -> str:
    lines = ["| scenario | arm | runs | mean score | success (no critical failure) | skill activated | mean cost $ | mean turns |",
             "|---|---|---|---|---|---|---|---|"]
    scenarios = sorted({r["scenario"] for r in rows})
    for arm in ARMS:
        for sid in [*scenarios, "ALL"]:
            sel = [r for r in rows if r["arm"] == arm and (sid == "ALL" or r["scenario"] == sid)]
            if not sel:
                continue
            n = len(sel)
            cost = [r["cost_usd"] for r in sel if r.get("cost_usd") is not None]
            turns = [r["turns"] for r in sel if r.get("turns") is not None]
            lines.append(
                f"| {sid} | {arm} | {n} | {sum(r['score'] for r in sel) / n:.2f} | "
                f"{sum(r['success'] for r in sel)}/{n} | {sum(r['skill_activated'] for r in sel)}/{n} | "
                f"{(sum(cost) / len(cost)) if cost else 0:.2f} | {(sum(turns) / len(turns)) if turns else 0:.0f} |"
            )
    lines += ["", "Per-check pass rate (skill vs baseline):", "",
              "| check | skill | baseline |", "|---|---|---|"]
    names = sorted({c["check"] for r in rows for c in r["checks"]})
    for name in names:
        cells = []
        for arm in ARMS:
            hits = [c["passed"] for r in rows if r["arm"] == arm for c in r["checks"] if c["check"] == name]
            cells.append(f"{sum(hits)}/{len(hits)}" if hits else "—")
        lines.append(f"| {name} | {cells[0]} | {cells[1]} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--scenario", action="append", help="limit to these ids")
    parser.add_argument("--arm", action="append", choices=ARMS)
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--abicheck-venv", type=Path,
                        help="non-editable abicheck venv; enables mount-namespace isolation (Linux, root)")
    parser.add_argument("--json", type=Path, help="write graded rows here")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if out.is_relative_to(ROOT):
        parser.error("--out must be outside this checkout (the skill arm would leak into the baseline)")
    if not args.report_only:
        if not PUBLISHED_SKILL.is_dir():
            parser.error(f"{PUBLISHED_SKILL} missing: run scripts/gen_agent_skills.py")
        corpus = grader.load_scenarios()
        todo = [
            (s, arm, rep)
            for s in corpus["scenarios"]
            if not args.scenario or s["id"] in args.scenario
            for arm in (args.arm or ARMS)
            for rep in range(args.repetitions)
        ]
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_one, s, arm, rep, out, args.model, args.max_turns, args.timeout, args.abicheck_venv)
                       for s, arm, rep in todo]
            for fut in cf.as_completed(futures):
                m = fut.result()
                print(f"done {m['scenario']}/{m['arm']}/rep{m['rep']} {m['elapsed_s']}s", flush=True)
    rows = grade_all(out)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
